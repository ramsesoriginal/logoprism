"""Writing configuration files: the documented ``init`` template and wizard choices."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

SCHEMA_URL = (
    "https://raw.githubusercontent.com/ramsesoriginal/logoprism/main/docs/logoprism.schema.json"
)

INIT_TEMPLATE = f"""\
#:schema {SCHEMA_URL}
# LogoPrism configuration. Everything is optional; uncomment what you need.
# Relative paths are relative to this file. Run `logoprism config show` to see the result.

# inputs = ["logos/*.svg"]          # files, folders or globs to convert
# output = "build/brand"            # where the PNGs go
# layout = "nested"                 # "nested": <stem>/<variant>/<shape>/file.png, or "flat"
# name-pattern = "{{stem}}-{{variant}}-{{shape}}-{{size}}"

# sizes = ["standard"]              # 512, favicon, icon, web, large, print, all, a4@150 ...
# shapes = ["native", "square", "circle"]
# padding = 0.04                    # free margin around the logo (fraction of the canvas)
# trim = true                       # crop to the drawn content instead of the SVG viewBox
# circle-fit = "tight"              # "tight" or "bbox"

# Brand colours add more variants (see the README for exactly which).
# brand-bg = "#0B3D91"
# brand-fg = "#FFCC00"
# brand-light = "#FFFFFF"           # used on dark backgrounds
# brand-dark = "#0B1F3A"            # used on light backgrounds

# variants = ["*"]                  # globs, e.g. ["black-*", "*-on-transparent"]
# exclude = ["grey-*"]
# min-contrast = 3.0                # skip solid colour pairs below this WCAG ratio

# A custom colour dictionary: here, restyle a dark logo for dark backgrounds.
# [themes.dark]
# map = {{ currentColor = "#E6EDF3", "#08131f" = "#E6EDF3" }}
# backgrounds = ["transparent", "#0D1117"]

# Per-file overrides: a table per glob, same keys as above.
# [files."logos/icon-*.svg"]
# shapes = ["square", "circle"]
# sizes = ["favicon", "icon"]
"""


def toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list | tuple):
        return "[" + ", ".join(toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return (
            "{ " + ", ".join(f"{json.dumps(k)} = {toml_value(v)}" for k, v in value.items()) + " }"
        )
    raise TypeError(f"cannot write {type(value).__name__} to TOML")


def render_choices(
    choices: Mapping[str, Any], *, header: str = "Written by `logoprism --interactive`."
) -> str:
    """A minimal config file holding exactly *choices* (kebab-case keys)."""
    lines = [f"#:schema {SCHEMA_URL}", f"# {header}", ""]
    themes = choices.get("themes")
    for key, value in choices.items():
        if key == "themes":
            continue
        lines.append(f"{key} = {toml_value(value)}")
    if themes:
        for name, theme in themes.items():
            lines += ["", f"[themes.{name}]"] + [f"{k} = {toml_value(v)}" for k, v in theme.items()]
    return "\n".join(lines) + "\n"
