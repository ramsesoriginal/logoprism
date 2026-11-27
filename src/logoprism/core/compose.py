"""Raster-level colour treatments and background compositing (Pillow only)."""

from __future__ import annotations

from functools import lru_cache

from PIL import Image, ImageDraw

from logoprism.core.color import GREY_WEIGHTS, Color


def colorize(image: Image.Image, color: Color) -> Image.Image:
    """Paint the logo's silhouette (its alpha channel) in a single colour."""
    alpha = image.getchannel("A")
    if color.a < 1.0:
        alpha = alpha.point([round(v * color.a) for v in range(256)])
    out = Image.new("RGBA", image.size, (*color.rgb, 255))
    out.putalpha(alpha)
    return out


def greyscale(image: Image.Image) -> Image.Image:
    """Greyscale with Rec. 709 weights (as CSS ``grayscale()``), alpha untouched."""
    luma = image.convert("RGB").convert("L", (*GREY_WEIGHTS, 0.0))
    return Image.merge("RGBA", (luma, luma, luma, image.getchannel("A")))


def on_background(image: Image.Image, background: Color, *, round_bg: bool = False) -> Image.Image:
    """Composite *image* over a solid background (optionally a circular one)."""
    fill = (*background.rgb, round(background.a * 255))
    if round_bg:
        base = Image.new("RGBA", image.size, (0, 0, 0, 0))
        base.paste(Image.new("RGBA", image.size, fill), mask=_circle_mask(image.size[0]))
    else:
        base = Image.new("RGBA", image.size, fill)
    out = Image.alpha_composite(base, image)
    return out.convert("RGB") if out.getchannel("A").getextrema()[0] == 255 else out


@lru_cache(maxsize=32)
def _circle_mask(size: int) -> Image.Image:
    """Anti-aliased filled circle spanning the full square (4x supersampled)."""
    big = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(big).ellipse((0, 0, size * 4 - 1, size * 4 - 1), fill=255)
    return big.resize((size, size), Image.Resampling.LANCZOS)
