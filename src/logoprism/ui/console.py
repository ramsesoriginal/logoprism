"""Console setup, the brand header and shared styling."""

from __future__ import annotations

import contextlib
import os
import sys
from typing import TYPE_CHECKING

from rich.console import Console
from rich.text import Text
from rich.theme import Theme

from logoprism import __version__

if TYPE_CHECKING:
    from collections.abc import Sequence

TAGLINE = "One logo, a spectrum of possibilities"
GLYPH = "─⟁⫷"
GLYPH_ASCII = "-<|"

#: The six spectral ray colours of the LogoPrism mark.
SPECTRUM = ("#E85D75", "#F49B48", "#E5B844", "#42B883", "#3899DB", "#9873D8")

THEME = Theme(
    {
        "ok": "bold green",
        "warn": "bold yellow",
        "err": "bold red",
        "dim": "dim",
        "accent": "bold #3899DB",
        "path": "cyan",
        "key": "bold",
    }
)


def configure_streams() -> None:
    """Make stdout/stderr UTF-8 even on legacy Windows code pages."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(OSError, ValueError):  # exotic streams may refuse
                reconfigure(encoding="utf-8", errors="replace")


def use_ascii() -> bool:
    return bool(os.environ.get("LOGOPRISM_ASCII")) or os.environ.get("TERM") == "dumb"


def make_consoles(*, no_color: bool = False, quiet: bool = False) -> tuple[Console, Console]:
    """Return ``(out, err)`` consoles. Rich honours NO_COLOR / FORCE_COLOR / non-TTY itself."""
    colour = True if no_color else None
    out = Console(theme=THEME, no_color=colour, highlight=False, quiet=quiet)
    err = Console(theme=THEME, no_color=colour, highlight=False, stderr=True)
    return out, err


def gradient(text: str, colors: Sequence[str] = SPECTRUM) -> Text:
    """Colour *text* character by character along *colors*."""
    result = Text()
    visible = [c for c in text if not c.isspace()]
    n = max(1, len(visible) - 1)
    index = 0
    for char in text:
        if char.isspace():
            result.append(char)
            continue
        position = index / n * (len(colors) - 1)
        result.append(char, style=f"bold {colors[min(round(position), len(colors) - 1)]}")
        index += 1
    return result


def header(console: Console, *, subtitle: str | None = None) -> None:
    glyph = GLYPH_ASCII if use_ascii() else GLYPH
    line = Text()
    line.append_text(gradient(glyph))
    line.append(" ")
    line.append_text(gradient("LogoPrism"))
    line.append(f"  {__version__}", style="dim")
    console.print(line)
    console.print(Text(subtitle or TAGLINE, style="dim italic"))
    console.print()
