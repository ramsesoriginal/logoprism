"""Record the README demo GIFs from *real* LogoPrism runs.

It spawns the CLI in a pseudo-terminal, scripts the keystrokes, replays the captured
output through a terminal emulator (pyte) and draws every frame with Pillow, so no screen
recorder, ffmpeg or browser is needed. POSIX only (needs ``pty``); run it from Linux/macOS/WSL:

    uv run --group docs python scripts/record_demo.py all --out docs/assets

Scenes: ``quickstart`` (the default command) and ``interactive`` (the wizard).
"""

from __future__ import annotations

import argparse
import codecs
import fcntl
import os
import pty
import re
import select
import shutil
import struct
import sys
import tempfile
import termios
import time
from dataclasses import dataclass
from pathlib import Path

import pyte
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
LOGO = ROOT / "docs" / "assets" / "logoprism.svg"

COLS, ROWS = 100, 38
FONT_SIZE = 15
FPS = 12
BG, FG = "#0d1117", "#e6edf3"
CHROME_H, PAD = 38, 14

ANSI16 = {
    "black": "#484f58",
    "red": "#ff7b72",
    "green": "#3fb950",
    "brown": "#d29922",
    "blue": "#58a6ff",
    "magenta": "#bc8cff",
    "cyan": "#39c5cf",
    "white": "#b1bac4",
    "brightblack": "#6e7681",
    "brightred": "#ffa198",
    "brightgreen": "#56d364",
    "brightbrown": "#e3b341",
    "brightblue": "#79c0ff",
    "brightmagenta": "#d2a8ff",
    "brightcyan": "#56d4dd",
    "brightwhite": "#f0f6fc",
}

FONT_CANDIDATES = [
    (
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    ),
    ("/System/Library/Fonts/Menlo.ttc", "/System/Library/Fonts/Menlo.ttc"),
]
# (path, collection index): fonts tried in order when the terminal font lacks a glyph.
# Cambria Math (Windows) carries the brand glyphs U+27C1 and U+2AF7.
FALLBACK_CANDIDATES = [
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
    ("/mnt/c/Windows/Fonts/cambria.ttc", 1),
    ("/mnt/c/Windows/Fonts/cambria.ttc", 0),
    ("C:/Windows/Fonts/cambria.ttc", 1),
    ("/System/Library/Fonts/Supplemental/STIXTwoMath.otf", 0),
]


@dataclass
class Step:
    kind: str  # expect | keys | type | pause
    arg: str = ""
    delay: float = 0.0


def expect(text: str) -> Step:
    return Step("expect", text)


def keys(seq: str, then: float = 0.6) -> Step:
    return Step("keys", seq, then)


def typed(text: str, then: float = 0.5) -> Step:
    return Step("type", text, then)


def pause(seconds: float) -> Step:
    return Step("pause", delay=seconds)


DOWN, ENTER, SPACE = "\x1b[B", "\r", " "


def fake_typing(command: str) -> list[tuple[float, bytes]]:
    """The shell prompt and the command being typed, before the real program starts."""
    events: list[tuple[float, bytes]] = [(0.0, b"\x1b[1;32m$\x1b[0m ")]
    t = 0.5
    for ch in command:
        events.append((t, ch.encode()))
        t += 0.045
    events.append((t + 0.35, b"\r\n"))
    return events


def run_session(
    argv: list[str], cwd: Path, steps: list[Step], command_label: str
) -> list[tuple[float, bytes]]:
    events = fake_typing(command_label)
    t0 = events[-1][0] + 0.15
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(cwd)
        os.environ.update(TERM="xterm-256color", COLORTERM="truecolor", PYTHONUTF8="1")
        os.environ.pop("NO_COLOR", None)
        os.execvp(argv[0], argv)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
    start = time.monotonic()
    pending = b""
    # A live emulator so the program's cursor-position queries (prompt_toolkit sends them)
    # get answered the way a real terminal would.
    live = pyte.Screen(COLS, ROWS)
    live_stream = pyte.Stream(live)
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def pump(duration: float) -> bool:
        nonlocal pending
        end = time.monotonic() + duration
        while time.monotonic() < end:
            ready, _, _ = select.select([fd], [], [], 0.02)
            if ready:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    return False
                if not data:
                    return False
                events.append((t0 + time.monotonic() - start, data))
                pending += data
                live_stream.feed(decoder.decode(data))
                if b"\x1b[6n" in data:
                    os.write(fd, f"\x1b[{live.cursor.y + 1};{live.cursor.x + 1}R".encode())
        return True

    alive = True
    for step in steps:
        if step.kind == "expect":
            deadline = time.monotonic() + 90
            while step.arg.encode() not in pending and time.monotonic() < deadline:
                if not pump(0.1):
                    alive = False
                    break
            if step.arg.encode() not in pending:
                raise RuntimeError(f"timed out waiting for {step.arg!r}")
            pending = b""
        elif step.kind == "pause":
            alive = pump(step.delay)
        elif step.kind == "keys":
            os.write(fd, step.arg.encode())
            alive = pump(step.delay)
        elif step.kind == "type":
            for ch in step.arg:
                os.write(fd, ch.encode())
                pump(0.07)
            alive = pump(step.delay)
        if not alive:
            break
    while pump(0.3):
        pass
    os.waitpid(pid, 0)
    return events


# ------------------------------------------------------------------------------ rendering
class Painter:
    def __init__(self) -> None:
        reg, bold = next((r, b) for r, b in FONT_CANDIDATES if Path(r).exists())
        self.regular = ImageFont.truetype(reg, FONT_SIZE)
        self.bold = ImageFont.truetype(bold, FONT_SIZE)
        self.fallback = [
            ImageFont.truetype(p, FONT_SIZE, index=i)
            for p, i in FALLBACK_CANDIDATES
            if Path(p).exists()
        ]
        ascent, descent = self.regular.getmetrics()
        self.cw = round(self.regular.getlength("M"))
        self.ch = ascent + descent
        self.width = COLS * self.cw + 2 * PAD
        self.height = ROWS * self.ch + 2 * PAD + CHROME_H
        self._missing = self._glyph(self.regular, "\uffff")
        self._cache: dict[tuple[str, bool], ImageFont.FreeTypeFont] = {}

    def _glyph(self, font: ImageFont.FreeTypeFont, char: str) -> bytes:
        """Rendered pixels of one glyph (used to detect fonts that lack it)."""
        probe = Image.new("L", (self.cw * 3, self.ch * 2), 0)
        ImageDraw.Draw(probe).text((2, 2), char, fill=255, font=font)
        return probe.tobytes()

    def font_for(self, char: str, bold: bool) -> ImageFont.FreeTypeFont:
        key = (char, bold)
        if key not in self._cache:
            primary = self.bold if bold else self.regular
            choice = primary
            if ord(char) > 0x7F and self._glyph(primary, char) == self._missing:
                choice = next(
                    (f for f in self.fallback if self._glyph(f, char) != self._glyph(f, "\uffff")),
                    primary,
                )
            self._cache[key] = choice
        return self._cache[key]

    @staticmethod
    def colour(value: str, default: str) -> str:
        if value == "default":
            return default
        if value in ANSI16:
            return ANSI16[value]
        if re.fullmatch(r"[0-9a-fA-F]{6}", value):
            return f"#{value}"
        return default

    def frame(self, screen: pyte.Screen) -> Image.Image:
        img = Image.new("RGB", (self.width, self.height), BG)
        draw = ImageDraw.Draw(img)
        draw.rectangle((0, 0, self.width, CHROME_H), fill="#161b22")
        for i, c in enumerate(("#ff5f56", "#ffbd2e", "#27c93f")):
            draw.ellipse((16 + i * 22, 12, 28 + i * 22, 24), fill=c)
        title = "logoprism"
        draw.text(
            (self.width / 2 - self.regular.getlength(title) / 2, 10),
            title,
            fill="#8b949e",
            font=self.regular,
        )
        for y in range(ROWS):
            row = screen.buffer[y]
            for x in range(COLS):
                cell = row[x]
                fg = self.colour(cell.fg, FG)
                bg = self.colour(cell.bg, BG)
                if cell.reverse:
                    fg, bg = bg, fg
                px, py = PAD + x * self.cw, CHROME_H + PAD + y * self.ch
                if bg != BG:
                    draw.rectangle((px, py, px + self.cw, py + self.ch), fill=bg)
                if cell.data.strip():
                    draw.text(
                        (px, py), cell.data, fill=fg, font=self.font_for(cell.data, cell.bold)
                    )
        return img


def render(events: list[tuple[float, bytes]], out: Path, hold: float = 3.0) -> None:
    painter = Painter()
    screen = pyte.Screen(COLS, ROWS)
    stream = pyte.Stream(screen)
    decoder_buf = b""
    frames: list[tuple[Image.Image, float]] = []
    last_key = None
    end = events[-1][0]
    idx, t = 0, 0.0
    step = 1 / FPS
    while t <= end + step:
        while idx < len(events) and events[idx][0] <= t:
            decoder_buf += events[idx][1]
            try:
                text, decoder_buf = decoder_buf.decode("utf-8"), b""
            except UnicodeDecodeError as exc:
                text, decoder_buf = (
                    decoder_buf[: exc.start].decode("utf-8"),
                    decoder_buf[exc.start :],
                )
            stream.feed(text)
            idx += 1
        key = tuple(
            (c.data, c.fg, c.bg, c.bold, c.reverse)
            for y in range(ROWS)
            for c in screen.buffer[y].values()
        )
        if key != last_key:
            frames.append((painter.frame(screen), step))
            last_key = key
        elif frames:
            frames[-1] = (frames[-1][0], frames[-1][1] + step)
        t += step
    frames[-1] = (frames[-1][0], hold)
    paletted = [
        f.convert("P", palette=Image.Palette.ADAPTIVE, colors=128, dither=Image.Dither.NONE)
        for f, _ in frames
    ]
    durations = [max(40, min(round(d * 1000), 2500)) for _, d in frames]
    paletted[0].save(
        out,
        save_all=True,
        append_images=paletted[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=1,
    )
    sys.stderr.write(f"{out}  {len(frames)} frames, {out.stat().st_size / 1024:.0f} KiB\n")


# --------------------------------------------------------------------------------- scenes
def logoprism_exe() -> str:
    found = shutil.which("logoprism")
    if found:
        return found
    return str(Path(sys.executable).with_name("logoprism"))


def scene_quickstart(out: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        shutil.copy(LOGO, work / "logo.svg")
        events = run_session(
            [logoprism_exe(), "logo.svg", "--no-details"],
            work,
            [expect("files written"), pause(0.5)],
            "logoprism logo.svg --no-details",
        )
    render(events, out)


def scene_interactive(out: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        shutil.copy(LOGO, work / "logo.svg")
        steps = [
            expect("Shapes"),
            pause(1.2),
            keys(DOWN, 0.5),
            keys(SPACE, 0.9),
            keys(ENTER, 0.4),
            expect("Sizes"),
            pause(0.8),
            keys(DOWN, 0.5),
            keys(DOWN, 1.0),
            keys(ENTER, 0.4),
            expect("Add brand colours?"),
            pause(0.7),
            keys("y", 0.5),
            expect("Brand background"),
            typed("#0B3D91", 0.4),
            keys(ENTER, 0.4),
            expect("Brand foreground"),
            typed("#FFCC00", 0.4),
            keys(ENTER, 0.4),
            expect("Light brand"),
            keys(ENTER, 0.3),
            expect("Dark brand"),
            keys(ENTER, 0.4),
            expect("Variants"),
            pause(1.6),
            keys(ENTER, 0.4),
            expect("Output folder"),
            pause(0.6),
            keys(ENTER, 0.4),
            expect("Generate"),
            pause(1.4),
            keys(ENTER, 0.4),
            expect("Save these choices"),
            pause(0.5),
            keys("n", 0.4),
            expect("files written"),
            pause(0.5),
        ]
        events = run_session(
            [logoprism_exe(), "--interactive", "logo.svg", "-j", "4"],
            work,
            steps,
            "logoprism --interactive logo.svg",
        )
    render(events, out)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("scene", choices=["quickstart", "interactive", "all"])
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "assets")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.scene in ("quickstart", "all"):
        scene_quickstart(args.out / "demo-quickstart.gif")
    if args.scene in ("interactive", "all"):
        scene_interactive(args.out / "demo-interactive.gif")


if __name__ == "__main__":
    main()
