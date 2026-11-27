"""Colour parsing and colour maths.

Parsing is delegated to :mod:`tinycss2.color4`, so everything CSS Color 4 understands
(named colours, ``#rgb[a]``, ``rgb()``, ``hsl()``, ``oklch()``, ``color()``, ...) works.
All colours are normalised to sRGB. Perceptual decisions (is this colour "colourful"?)
use OKLCH chroma, contrast uses the WCAG 2.x relative-luminance ratio.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

from tinycss2 import color4

# Rec. 709 luma weights, the same ones CSS ``grayscale()`` uses.
GREY_WEIGHTS = (0.2126, 0.7152, 0.0722)
# OKLCH chroma above which a colour counts as "colourful" (``#08131F`` is ~0.03, gold ~0.1).
COLORFUL_CHROMA = 0.04


class ColorError(ValueError):
    """Raised when a colour string cannot be understood."""


@dataclass(frozen=True, slots=True)
class Color:
    """An sRGB colour with 8-bit channels and a float alpha in ``[0, 1]``."""

    r: int
    g: int
    b: int
    a: float = 1.0

    # -- construction ---------------------------------------------------------------
    @classmethod
    def parse(cls, text: str) -> Color:
        """Parse any CSS colour string. Raises :class:`ColorError` on failure."""
        parsed = _parse_cached(text.strip())
        if parsed is None:
            raise ColorError(f"not a valid colour: {text!r}")
        return parsed

    @classmethod
    def try_parse(cls, text: str) -> Color | None:
        return _parse_cached(text.strip())

    # -- conversions ----------------------------------------------------------------
    @property
    def rgb(self) -> tuple[int, int, int]:
        return (self.r, self.g, self.b)

    @property
    def hex(self) -> str:
        """``#rrggbb`` (or ``#rrggbbaa`` when not fully opaque)."""
        base = f"#{self.r:02x}{self.g:02x}{self.b:02x}"
        if self.a < 1.0:
            base += f"{round(self.a * 255):02x}"
        return base

    @property
    def opaque(self) -> Color:
        return Color(self.r, self.g, self.b, 1.0)

    # -- perception -----------------------------------------------------------------
    @property
    def luminance(self) -> float:
        """WCAG relative luminance in ``[0, 1]``."""
        r, g, b = (_srgb_to_linear(c / 255) for c in self.rgb)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    @property
    def oklch(self) -> tuple[float, float, float]:
        """``(lightness, chroma, hue_degrees)`` in OKLCH."""
        r, g, b = (_srgb_to_linear(c / 255) for c in self.rgb)
        lms = (
            0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b,
            0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b,
            0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b,
        )
        l_, m_, s_ = (math.cbrt(v) for v in lms)
        lab_l = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
        lab_a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
        lab_b = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
        chroma = math.hypot(lab_a, lab_b)
        hue = math.degrees(math.atan2(lab_b, lab_a)) % 360
        return (lab_l, chroma, hue)

    @property
    def chroma(self) -> float:
        return self.oklch[1]

    def is_colorful(self, threshold: float = COLORFUL_CHROMA) -> bool:
        """Whether the colour is perceptibly chromatic (not black/white/grey)."""
        return self.chroma > threshold

    def grey(self) -> Color:
        """Greyscale using Rec. 709 weights in gamma space (matches CSS ``grayscale()``)."""
        y = round(sum(w * c for w, c in zip(GREY_WEIGHTS, self.rgb, strict=True)))
        return Color(y, y, y, self.a)

    def __str__(self) -> str:
        return self.hex


BLACK = Color(0, 0, 0)
WHITE = Color(255, 255, 255)
TRANSPARENT = Color(0, 0, 0, 0.0)


def contrast_ratio(a: Color, b: Color) -> float:
    """WCAG 2.x contrast ratio between two colours, in ``[1, 21]``."""
    hi, lo = sorted((a.luminance, b.luminance), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


@lru_cache(maxsize=1024)
def _parse_cached(text: str) -> Color | None:
    if not text:
        return None
    try:
        parsed = color4.parse_color(text)
    except ValueError, TypeError, IndexError:  # pragma: no cover - defensive
        return None
    if parsed is None or isinstance(parsed, str):  # None, or the ``currentColor`` keyword
        return None
    rgb = _to_srgb(parsed)
    if rgb is None:
        return None
    r, g, b = (min(255, max(0, round(c * 255))) for c in rgb)
    alpha = 1.0 if parsed.alpha is None else min(1.0, max(0.0, float(parsed.alpha)))
    return Color(r, g, b, alpha)


# -- colour space conversion -----------------------------------------------------------
# tinycss2 parses CSS Color 4 but converts only some spaces, so the rest is done here.
_Matrix = tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]
_Vec = tuple[float, float, float]
_XYZ_D65_TO_LINEAR_SRGB: _Matrix = (
    (3.2409699419, -1.5373831776, -0.4986107603),
    (-0.9692436363, 1.8759675015, 0.0415550574),
    (0.0556300797, -0.2039769589, 1.0569715142),
)
_XYZ_D50_TO_D65: _Matrix = (
    (0.9555766, -0.0230393, 0.0631636),
    (-0.0282895, 1.0099416, 0.0210077),
    (0.0122982, -0.0204830, 1.3299098),
)
_LINEAR_P3_TO_XYZ_D65: _Matrix = (
    (0.4865709486, 0.2656676932, 0.1982172852),
    (0.2289745641, 0.6917385218, 0.0792869141),
    (0.0, 0.0451133819, 1.0439443689),
)
_D50_WHITE = (0.96422, 1.0, 0.82521)


def _mul(m: _Matrix, v: _Vec) -> _Vec:
    return (
        m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
        m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
        m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
    )


def _linear_to_srgb(c: float) -> float:
    c = min(1.0, max(0.0, c))  # simple gamut clip
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def _oklab_to_linear_srgb(lightness: float, a: float, b: float) -> _Vec:
    l_ = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (
        4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
        -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
        -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_,
    )


def _lab_d50_to_xyz_d65(lightness: float, a: float, b: float) -> _Vec:
    eps, kappa = 216 / 24389, 24389 / 27
    fy = (lightness + 16) / 116
    fx, fz = a / 500 + fy, fy - b / 200
    xr = fx**3 if fx**3 > eps else (116 * fx - 16) / kappa
    yr = fy**3 if lightness > kappa * eps else lightness / kappa
    zr = fz**3 if fz**3 > eps else (116 * fz - 16) / kappa
    d50 = (xr * _D50_WHITE[0], yr * _D50_WHITE[1], zr * _D50_WHITE[2])
    return _mul(_XYZ_D50_TO_D65, d50)


def _polar(chroma: float, hue_degrees: float) -> tuple[float, float]:
    return chroma * math.cos(math.radians(hue_degrees)), chroma * math.sin(
        math.radians(hue_degrees)
    )


def _to_srgb(parsed: color4.Color) -> _Vec | None:
    """Convert a parsed CSS colour to gamma-encoded sRGB floats, or None if unsupported."""
    try:
        r, g, b = parsed.to("srgb").coordinates
    except NotImplementedError:
        pass
    except ValueError, KeyError, TypeError:  # pragma: no cover - defensive
        return None
    else:
        return (r or 0.0, g or 0.0, b or 0.0)

    c0, c1, c2 = (0.0 if v is None else float(v) for v in parsed.coordinates)
    space = parsed.space
    if space == "oklab":
        linear = _oklab_to_linear_srgb(c0, c1, c2)
    elif space == "oklch":
        linear = _oklab_to_linear_srgb(c0, *_polar(c1, c2))
    elif space == "lab":
        linear = _mul(_XYZ_D65_TO_LINEAR_SRGB, _lab_d50_to_xyz_d65(c0, c1, c2))
    elif space == "lch":
        linear = _mul(_XYZ_D65_TO_LINEAR_SRGB, _lab_d50_to_xyz_d65(c0, *_polar(c1, c2)))
    elif space == "display-p3":
        p3 = (_srgb_to_linear(c0), _srgb_to_linear(c1), _srgb_to_linear(c2))
        linear = _mul(_XYZ_D65_TO_LINEAR_SRGB, _mul(_LINEAR_P3_TO_XYZ_D65, p3))
    elif space == "srgb-linear":
        linear = (c0, c1, c2)
    elif space in ("xyz", "xyz-d65"):
        linear = _mul(_XYZ_D65_TO_LINEAR_SRGB, (c0, c1, c2))
    elif space == "xyz-d50":
        linear = _mul(_XYZ_D65_TO_LINEAR_SRGB, _mul(_XYZ_D50_TO_D65, (c0, c1, c2)))
    else:
        return None
    return (_linear_to_srgb(linear[0]), _linear_to_srgb(linear[1]), _linear_to_srgb(linear[2]))


def is_current_color(text: str) -> bool:
    """Whether *text* is the CSS ``currentColor`` keyword."""
    return text.strip().lower() == "currentcolor"
