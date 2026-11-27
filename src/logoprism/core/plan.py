"""Turn analysed logos plus settings into a concrete list of files to write."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from logoprism import __version__
from logoprism.config.validate import ConfigError
from logoprism.core.geometry import Layout, Shape, make_layout
from logoprism.core.recipes import Recipe, RecipeSet, build_recipes
from logoprism.core.sizes import SizeSpec, parse_sizes

if TYPE_CHECKING:
    from logoprism.config.models import Settings
    from logoprism.core.analysis import Analysis

MANIFEST_NAME = "logoprism-manifest.json"
MANIFEST_VERSION = 1


@dataclass(frozen=True, slots=True)
class OutputSpec:
    recipe: Recipe
    shape: Shape
    size: SizeSpec
    path: Path
    layout: Layout
    key: str  # changes whenever the produced bytes could change


@dataclass(frozen=True, slots=True)
class LogoPlan:
    analysis: Analysis
    settings: Settings
    recipes: RecipeSet
    shapes: tuple[Shape, ...]
    skipped_shapes: tuple[tuple[str, str], ...]
    sizes: tuple[SizeSpec, ...]
    outputs: tuple[OutputSpec, ...]
    out_dir: Path
    warnings: tuple[str, ...]

    @property
    def manifest_path(self) -> Path:
        return self.out_dir / MANIFEST_NAME

    @property
    def rel_dir(self) -> Path:
        return self.out_dir


def plan_logo(analysis: Analysis, settings: Settings) -> LogoPlan:
    """Decide every output file for one logo."""
    recipes = build_recipes(settings, colorful=analysis.colorful)
    sizes = tuple(parse_sizes(settings.sizes))
    shapes: list[Shape] = []
    skipped_shapes: list[tuple[str, str]] = []
    for shape in dict.fromkeys(settings.shapes):
        if shape == "square" and analysis.native_is_square and not settings.force_square:
            skipped_shapes.append(("square", "the logo is already square (identical to native)"))
            continue
        shapes.append(shape)

    out_dir = Path(settings.output) / analysis.stem
    options = "|".join(
        str(x)
        for x in (
            __version__,
            analysis.fingerprint,
            settings.round_bg,
            settings.optimize,
            settings.compress_level if not settings.optimize else "opt",
            settings.system_fonts,
            sorted(settings.fonts),
        )
    )
    themes = {
        name: json.dumps(sorted(theme.map.items())) for name, theme in settings.themes.items()
    }

    outputs: list[OutputSpec] = []
    seen: dict[Path, str] = {}
    for shape in shapes:
        for size in sizes:
            layout = make_layout(
                analysis.ink,
                shape,
                size.px,
                padding=settings.padding,
                circle_fit=settings.circle_fit,
            )
            geometry = f"{layout.view.attr}|{layout.width}x{layout.height}|{size.dpi}|{shape}"
            for recipe in recipes.recipes:
                path = _output_path(out_dir, settings, analysis.stem, recipe, shape, size)
                if path in seen:
                    raise ConfigError(
                        f"name-pattern {settings.name_pattern!r} makes {recipe.id}/{shape}/{size.px} "
                        f"collide with {seen[path]} at {path}"
                    )
                seen[path] = f"{recipe.id}/{shape}/{size.px}"
                digest = hashlib.sha256(
                    "\x1f".join(
                        (options, geometry, recipe.fingerprint(), themes.get(recipe.source, ""))
                    ).encode()
                ).hexdigest()[:24]
                outputs.append(OutputSpec(recipe, shape, size, path, layout, digest))

    warnings = [*analysis.warnings, *recipes.warnings]
    if not recipes.recipes:
        warnings.append("no variants selected: check --variants / --exclude")
    return LogoPlan(
        analysis=analysis,
        settings=settings,
        recipes=recipes,
        shapes=tuple(shapes),
        skipped_shapes=tuple(skipped_shapes),
        sizes=sizes,
        outputs=tuple(outputs),
        out_dir=out_dir,
        warnings=tuple(warnings),
    )


def _output_path(
    out_dir: Path, settings: Settings, stem: str, recipe: Recipe, shape: str, size: SizeSpec
) -> Path:
    name = settings.name_pattern.format(stem=stem, variant=recipe.id, shape=shape, size=size.label)
    if settings.layout == "flat":
        return out_dir / f"{name}.png"
    return out_dir / recipe.id / shape / f"{name}.png"
