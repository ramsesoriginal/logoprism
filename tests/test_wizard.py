"""The interactive wizard, driven through a real pseudo-terminal (POSIX only)."""

from __future__ import annotations

import os
import re
import select
import sys
import time
from pathlib import Path

import pytest

from logoprism.cli import main

from .conftest import COLOR_SQUARE, MONO_WIDE

DOWN, ENTER = "\x1b[B", "\r"


def drive(
    argv: list[str], cwd: Path, steps: list[tuple[str, str]], timeout: float = 90
) -> tuple[int, str]:
    """Spawn *argv* on a pty; for each ``(expected_text, keys_to_send)`` wait, then type."""
    import pty

    pid, fd = pty.fork()
    if pid == 0:  # child
        os.chdir(cwd)
        os.environ.update(TERM="xterm-256color", COLUMNS="120", PYTHONUTF8="1")
        os.execvp(argv[0], argv)
    seen = b""
    window = b""

    def pump(seconds: float) -> bool:
        nonlocal seen, window
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            ready, _, _ = select.select([fd], [], [], 0.05)
            if ready:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    return False
                if not data:
                    return False
                seen += data
                window += data
                if b"\x1b[6n" in data:  # cursor position request: answer like a terminal
                    os.write(fd, b"\x1b[1;1R")
        return True

    try:
        for expected, keys in steps:
            deadline = time.monotonic() + timeout
            while expected.encode() not in window and time.monotonic() < deadline:
                if not pump(0.1):
                    break
            assert expected.encode() in window, (
                f"never saw {expected!r}; got: {_plain(seen)[-600:]}"
            )
            window = b""
            time.sleep(0.1)
            os.write(fd, keys.encode())
        while pump(0.3):
            pass
    finally:
        _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status), _plain(seen)


def _plain(raw: bytes) -> str:
    return re.sub(
        r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07", "", raw.decode("utf-8", "replace")
    )


@pytest.mark.slow
@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX pty")
def test_wizard_end_to_end(tmp_path):
    (tmp_path / "logo.svg").write_text(MONO_WIDE, encoding="utf-8")
    steps = [
        ("Shapes", ENTER),
        ("Sizes", DOWN + ENTER),  # favicons and app icons
        ("Add brand colours?", "n"),
        ("Variants", ENTER),
        ("Output folder", ENTER),
        ("Generate", ENTER),
        ("Save these choices", "n"),
    ]
    code, transcript = drive(
        [sys.executable, "-m", "logoprism", "--interactive", "logo.svg", "-o", "out", "-j", "1"],
        tmp_path,
        steps,
    )
    assert code == 0, transcript[-800:]
    assert "files written" in transcript
    assert (
        tmp_path
        / "out"
        / "logo"
        / "black-on-transparent"
        / "native"
        / "logo-black-on-transparent-native-16.png"
    ).is_file()
    assert (
        tmp_path
        / "out"
        / "logo"
        / "white-on-black"
        / "circle"
        / "logo-white-on-black-circle-256.png"
    ).is_file()
    assert not (tmp_path / "logoprism.toml").exists()


@pytest.mark.slow
@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX pty")
def test_wizard_can_save_its_choices_and_brand_colours_flow(tmp_path):
    (tmp_path / "logo.svg").write_text(COLOR_SQUARE, encoding="utf-8")
    steps = [
        ("Shapes", ENTER),
        ("Sizes", ENTER),
        ("Add brand colours?", "y"),
        ("Brand background", "#0B3D91\r"),
        ("Brand foreground", "\r"),
        ("Light brand", "\r"),
        ("Dark brand", "\r"),
        ("Variants", ENTER),
        ("Output folder", ENTER),
        ("Generate", ENTER),
        ("Save these choices", "y"),
    ]
    code, transcript = drive(
        [
            sys.executable,
            "-m",
            "logoprism",
            "--interactive",
            "logo.svg",
            "-o",
            "out",
            "-j",
            "1",
            "-s",
            "16",
        ],
        tmp_path,
        steps,
    )
    assert code == 0, transcript[-800:]
    saved = (tmp_path / "logoprism.toml").read_text(encoding="utf-8")
    assert 'brand-bg = "#0B3D91"' in saved
    assert "shapes" in saved
    assert (tmp_path / "out" / "logo" / "white-on-brand-bg").is_dir()


def test_wizard_requires_a_terminal(capsys, write_svg):
    with pytest.raises(SystemExit) as exc:
        main(["--interactive", str(write_svg("m.svg", MONO_WIDE))])
    assert exc.value.code == 2
    assert "needs a terminal" in capsys.readouterr().err
