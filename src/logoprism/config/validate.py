"""Semantic validation that the type system alone cannot express."""

from __future__ import annotations

import string

from logoprism.config.models import Settings
from logoprism.core.color import Color, ColorError, is_current_color
from logoprism.core.sizes import SizeError, parse_sizes

NAMED_BACKGROUNDS = frozenset(
    {"transparent", "white", "black", "brand-bg", "brand-light", "brand-dark"}
)
PATTERN_FIELDS = frozenset({"stem", "variant", "shape", "size"})


class ConfigError(ValueError):
    """The effective configuration is invalid."""


def validate(settings: Settings) -> None:
    """Raise :class:`ConfigError` describing the first problem found."""
    s = settings
    if not 0 <= s.padding <= 0.4:
        raise ConfigError(f"padding must be between 0 and 0.4, got {s.padding}")
    if not 0 <= s.compress_level <= 9:
        raise ConfigError(f"compress-level must be 0-9, got {s.compress_level}")
    if s.jobs is not None and s.jobs < 1:
        raise ConfigError(f"jobs must be at least 1, got {s.jobs}")
    if s.min_contrast is not None and not 1 <= s.min_contrast <= 21:
        raise ConfigError(f"min-contrast must be between 1 and 21, got {s.min_contrast}")
    if not s.shapes:
        raise ConfigError("shapes must not be empty")
    try:
        parse_sizes(s.sizes)
    except SizeError as exc:
        raise ConfigError(str(exc)) from exc

    for key in ("brand_bg", "brand_fg", "brand_light", "brand_dark"):
        if (value := getattr(s, key)) is not None:
            _color(value, f"--{key.replace('_', '-')}")
    for name, theme in s.themes.items():
        if not name.replace("-", "").replace("_", "").isalnum():
            raise ConfigError(f"theme name {name!r} may only contain letters, digits, - and _")
        if not theme.map:
            raise ConfigError(f"theme {name!r} has an empty colour map")
        for src, dst in theme.map.items():
            if not is_current_color(src):
                _color(src, f"theme {name!r} key")
            _color(dst, f"theme {name!r} value for {src!r}")
        for bg in theme.backgrounds:
            if bg not in NAMED_BACKGROUNDS:
                _color(bg, f"theme {name!r} background")

    fields = {f for _, f, _, _ in string.Formatter().parse(s.name_pattern) if f}
    unknown = fields - PATTERN_FIELDS
    if unknown:
        raise ConfigError(
            f"name-pattern uses unknown field(s) {sorted(unknown)}; allowed: {sorted(PATTERN_FIELDS)}"
        )
    if s.layout == "flat" and fields != PATTERN_FIELDS:
        missing = sorted(PATTERN_FIELDS - fields)
        raise ConfigError(f"flat layout needs every field in name-pattern; missing {missing}")
    if "size" not in fields:
        raise ConfigError("name-pattern must contain {size}, otherwise sizes overwrite each other")


def _color(value: str, what: str) -> Color:
    try:
        return Color.parse(value)
    except ColorError:
        raise ConfigError(f"{what}: {value!r} is not a valid CSS colour") from None
