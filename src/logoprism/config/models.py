"""The settings model. One definition drives config files, env vars, CLI flags and the JSON Schema."""

from __future__ import annotations

from typing import Literal

import msgspec

Layout = Literal["nested", "flat"]
ShapeName = Literal["native", "square", "circle"]
CircleFitName = Literal["tight", "bbox"]

DEFAULT_NAME_PATTERN = "{stem}-{variant}-{shape}-{size}"


class Theme(msgspec.Struct, kw_only=True, frozen=True, forbid_unknown_fields=True, rename="kebab"):
    """A custom colour dictionary, e.g. to restyle a logo for dark backgrounds."""

    map: dict[str, str] = {}
    """Source colour -> target colour. Keys are any CSS colour or ``currentColor``."""
    backgrounds: tuple[str, ...] = ("transparent",)
    """Backgrounds to render this theme on: transparent, white, black, brand-bg, brand-light,
    brand-dark or any CSS colour."""


class Settings(
    msgspec.Struct, kw_only=True, frozen=True, forbid_unknown_fields=True, rename="kebab"
):
    """Everything that shapes an export. All fields have sensible defaults."""

    inputs: tuple[str, ...] = ()
    """SVG files, directories or glob patterns to convert."""
    output: str = "logoprism-out"
    """Output directory."""
    layout: Layout = "nested"
    """``nested``: <output>/<stem>/<variant>/<shape>/file.png, ``flat``: <output>/<stem>/file.png."""
    name_pattern: str = DEFAULT_NAME_PATTERN
    """File name template (without extension). Fields: stem, variant, shape, size."""

    sizes: tuple[str, ...] = ("standard",)
    """Sizes: integers, presets (favicon, icon, web, large, print, standard, all), a4[@dpi]."""
    shapes: tuple[ShapeName, ...] = ("native", "square", "circle")
    """native = the logo's own aspect ratio, square, circle = fits an inscribed circle."""
    variants: tuple[str, ...] = ("*",)
    """Glob patterns selecting variants, e.g. ``black-*`` or ``*-on-transparent``."""
    exclude: tuple[str, ...] = ()
    """Glob patterns removing variants again."""

    padding: float = 0.04
    """Free margin on every side, as a fraction of the canvas (0 to 0.4)."""
    trim: bool = True
    """Crop to the drawn content instead of using the SVG's viewBox."""
    circle_fit: CircleFitName = "tight"
    """``tight``: smallest circle around the drawn pixels, ``bbox``: around the bounding box."""
    round_bg: bool = False
    """Make solid backgrounds of ``circle`` outputs round instead of filling the square."""
    force_square: bool = False
    """Also write ``square`` outputs when the logo is already (nearly) square."""

    brand_bg: str | None = None
    """Brand background colour."""
    brand_fg: str | None = None
    """Brand foreground colour."""
    brand_light: str | None = None
    """Light brand colour: used on dark backgrounds."""
    brand_dark: str | None = None
    """Dark brand colour: used on light backgrounds."""
    min_contrast: float | None = None
    """Skip solid foreground/background pairs below this WCAG contrast ratio."""
    themes: dict[str, Theme] = {}
    """Custom colour dictionaries, each producing extra variants."""

    optimize: bool = False
    """Spend more CPU for smaller PNGs."""
    compress_level: int = 6
    """zlib level 0-9 when not optimising."""
    jobs: int | None = None
    """Worker processes (default: all cores)."""
    fonts: tuple[str, ...] = ()
    """Font files or directories for <text> elements."""
    system_fonts: bool = True
    """Use fonts installed on the system."""


FIELD_NAMES: tuple[str, ...] = tuple(f.encode_name for f in msgspec.structs.fields(Settings))
"""Config-file (kebab-case) key for every setting."""
