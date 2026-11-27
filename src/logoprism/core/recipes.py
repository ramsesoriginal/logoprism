"""Variants ("recipes"): which foreground treatment goes on which background.

A recipe is ``<foreground> on <background>`` where the foreground is the original colours,
a greyscale rendering, a single solid colour, or a themed (recoloured) rendering. Which
recipes exist follows from the logo (does it have colours?) and the brand colours supplied.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from logoprism.core.color import BLACK, WHITE, Color, contrast_ratio

if TYPE_CHECKING:
    from collections.abc import Iterable

    from logoprism.config.models import Settings

FgKind = Literal["original", "grey", "solid"]

LOW_CONTRAST = 3.0  # WCAG 2.x non-text contrast
UNREADABLE_CONTRAST = 1.1  # essentially the same colour: never useful


@dataclass(frozen=True, slots=True)
class Recipe:
    id: str
    fg_name: str
    bg_name: str
    fg_kind: FgKind
    fg_color: Color | None
    bg: Color | None  # None = transparent
    source: str = "original"  # "original" or the name of a theme
    contrast: float | None = None

    @property
    def low_contrast(self) -> bool:
        return self.contrast is not None and self.contrast < LOW_CONTRAST

    def fingerprint(self) -> str:
        return "|".join(
            str(x) for x in (self.id, self.fg_kind, self.fg_color, self.bg, self.source)
        )


@dataclass(frozen=True, slots=True)
class RecipeSet:
    recipes: tuple[Recipe, ...]
    skipped: tuple[tuple[str, str], ...]  # (id, reason)
    warnings: tuple[str, ...]


def build_recipes(settings: Settings, *, colorful: bool) -> RecipeSet:
    """Expand the settings and the logo's nature into the list of variants to produce."""
    s = settings
    backgrounds: dict[str, Color | None] = {
        "transparent": None,
        "white": WHITE,
        "black": BLACK,
    }
    if s.brand_bg:
        backgrounds["brand-bg"] = Color.parse(s.brand_bg)
    if s.brand_light:
        backgrounds["brand-light"] = Color.parse(s.brand_light)
    if s.brand_dark:
        backgrounds["brand-dark"] = Color.parse(s.brand_dark)

    found: dict[str, Recipe] = {}
    skipped: list[tuple[str, str]] = []
    warnings: list[str] = []

    def add(
        fg_name: str,
        kind: FgKind,
        fg_color: Color | None,
        bg_name: str,
        bg: Color | None,
        source: str = "original",
    ) -> None:
        rid = f"{fg_name}-on-{bg_name}"
        if rid in found or any(rid == sid for sid, _ in skipped):
            return
        ratio = None
        if kind == "solid" and fg_color is not None and bg is not None:
            ratio = contrast_ratio(fg_color, bg)
            if ratio < UNREADABLE_CONTRAST:
                skipped.append(
                    (rid, f"foreground and background are the same colour ({ratio:.2f}:1)")
                )
                return
            if s.min_contrast is not None and ratio < s.min_contrast:
                skipped.append(
                    (rid, f"contrast {ratio:.2f}:1 is below --min-contrast {s.min_contrast}")
                )
                return
        found[rid] = Recipe(rid, fg_name, bg_name, kind, fg_color, bg, source, ratio)

    def add_many(
        fg_name: str, kind: FgKind, fg_color: Color | None, bg_names: Iterable[str]
    ) -> None:
        for name in bg_names:
            if name in backgrounds:
                add(fg_name, kind, fg_color, name, backgrounds[name])

    plain = ("transparent", "white", "black")
    add_many("black", "solid", BLACK, ("transparent", "white"))
    add_many("white", "solid", WHITE, ("transparent", "black"))
    if colorful:
        add_many("color", "original", None, plain)
        add_many("grey", "grey", None, plain)

    if s.brand_bg:
        if colorful:
            add_many("color", "original", None, ("brand-bg",))
        if s.brand_fg:
            add_many("brand", "solid", Color.parse(s.brand_fg), ("brand-bg",))
        add_many("black", "solid", BLACK, ("brand-bg",))
        add_many("white", "solid", WHITE, ("brand-bg",))
    if s.brand_fg:
        add_many("brand", "solid", Color.parse(s.brand_fg), (*plain, "brand-bg"))
    if s.brand_dark:
        add_many(
            "brand-dark",
            "solid",
            Color.parse(s.brand_dark),
            ("transparent", "white", "brand-light"),
        )
    if s.brand_light:
        add_many(
            "brand-light",
            "solid",
            Color.parse(s.brand_light),
            ("transparent", "black", "brand-dark"),
        )

    for name, theme in s.themes.items():
        for bg_spec in theme.backgrounds:
            if bg_spec in backgrounds:
                bg_name, bg = bg_spec, backgrounds[bg_spec]
            elif bg_spec in ("brand-bg", "brand-light", "brand-dark"):
                warnings.append(
                    f"theme {name!r}: background {bg_spec!r} needs its --{bg_spec} colour"
                )
                continue
            else:
                bg = Color.parse(bg_spec)
                bg_name = f"bg-{bg.opaque.hex[1:]}"
            add(name, "original", None, bg_name, bg, source=name)

    selected: list[Recipe] = []
    matched_patterns: set[str] = set()
    for recipe in found.values():
        hit = [p for p in s.variants if fnmatch.fnmatchcase(recipe.id, p)]
        drop = [p for p in s.exclude if fnmatch.fnmatchcase(recipe.id, p)]
        matched_patterns.update(hit)
        if hit and not drop:
            selected.append(recipe)
    for pattern in s.variants:
        if pattern not in matched_patterns and pattern != "*":
            warnings.append(f"variant pattern {pattern!r} matches nothing for this logo")
    return RecipeSet(tuple(selected), tuple(skipped), tuple(warnings))


def apply_recipe(base, recipe: Recipe, *, round_bg: bool = False):
    """Turn the rendered original (RGBA) into the final image for *recipe*."""
    from logoprism.core.compose import colorize, greyscale, on_background

    if recipe.fg_kind == "solid":
        assert recipe.fg_color is not None
        image = colorize(base, recipe.fg_color)
    elif recipe.fg_kind == "grey":
        image = greyscale(base)
    else:
        image = base
    if recipe.bg is not None:
        image = on_background(image, recipe.bg, round_bg=round_bg)
    return image
