"""Deterministic PNG encoding with atomic writes."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PIL.PngImagePlugin import PngInfo

if TYPE_CHECKING:
    from PIL import Image


@dataclass(frozen=True, slots=True)
class Written:
    path: Path
    bytes: int
    sha256: str


def encode_png(
    image: Image.Image, *, dpi: int | None = None, optimize: bool = False, compress_level: int = 6
) -> bytes:
    """Encode to PNG: no timestamps, an ``sRGB`` chunk, and ``pHYs`` when *dpi* is given."""
    info = PngInfo()
    info.add(b"sRGB", b"\x00")  # rendering intent: perceptual
    params: dict[str, object] = {
        "pnginfo": info,
        "optimize": optimize,
        "compress_level": 9 if optimize else compress_level,
    }
    if dpi:
        params["dpi"] = (dpi, dpi)
    buffer = io.BytesIO()
    image.save(buffer, "PNG", **params)
    return buffer.getvalue()


def write_atomic(path: Path, data: bytes) -> Written:
    """Write *data* to *path* via a temporary file so readers never see partial output."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_bytes(data)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    return Written(path, len(data), hashlib.sha256(data).hexdigest())
