"""Rasterisation backends.

The rest of LogoPrism only talks to the :class:`Renderer` protocol, so a different engine
can be swapped in without touching planning, colour handling or the CLI.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from logoprism.core.svg import SvgError

if TYPE_CHECKING:
    from PIL import Image

MAX_DIMENSION = 16384


class Renderer(Protocol):
    def render(self, svg: bytes, width: int, height: int) -> Image.Image:
        """Rasterise *svg* to an RGBA image of exactly ``width`` x ``height``."""
        ...


@dataclass(frozen=True, slots=True)
class FontOptions:
    """Font handling. Reproducible builds should disable system fonts and pass their own."""

    files: tuple[str, ...] = ()
    dirs: tuple[str, ...] = ()
    system_fonts: bool = True


@dataclass(frozen=True, slots=True)
class ResvgRenderer:
    """Renderer backed by `resvg <https://github.com/linebender/resvg>`_ (via ``resvg-py``)."""

    fonts: FontOptions = field(default_factory=FontOptions)

    def render(self, svg: bytes, width: int, height: int) -> Image.Image:
        import resvg_py  # lazy: keeps ``--help`` fast
        from PIL import Image

        if not (0 < width <= MAX_DIMENSION and 0 < height <= MAX_DIMENSION):
            raise SvgError(f"refusing to render {width}x{height}px (limit {MAX_DIMENSION}px)")
        try:
            data = resvg_py.svg_to_bytes(
                svg_string=svg.decode("utf-8"),
                width=width,
                height=height,
                skip_system_fonts=not self.fonts.system_fonts,
                font_files=list(self.fonts.files) or None,
                font_dirs=list(self.fonts.dirs) or None,
            )
        except ValueError as exc:
            raise SvgError(f"render failed: {exc}") from exc
        image = Image.open(io.BytesIO(bytes(data)))
        image.load()
        return image.convert("RGBA")
