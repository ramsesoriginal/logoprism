"""Renderables: analysis panel, plan table, run summary, config table."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from rich import box
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

if TYPE_CHECKING:
    from rich.console import Console

    from logoprism.core.analysis import Analysis
    from logoprism.core.color import Color
    from logoprism.core.engine import RunReport
    from logoprism.core.plan import LogoPlan
    from logoprism.core.recipes import Recipe


def display_path(path: Path | str) -> str:
    """Shortest sensible spelling: relative to the cwd when that is shorter."""
    p = Path(path)
    try:
        rel = p.resolve().relative_to(Path.cwd().resolve())
    except ValueError:
        return str(p)
    return str(rel) if str(rel) != "." else "."


def swatch(color: Color, *, label: bool = True) -> Text:
    """A 4-cell colour chip; near-black and near-white get a thin edge so they stay visible."""
    c = color.opaque
    edge = c.luminance < 0.05 or c.luminance > 0.85
    text = Text("▕" if edge else " ", style="#8b949e")
    text.append("██", style=c.hex)
    text.append("▏" if edge else " ", style="#8b949e")
    if label:
        text.append(f" {color.opaque.hex}", style="dim")
    return text


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"  # pragma: no cover


def _shape_words(aspect: float) -> str:
    if abs(aspect - 1) <= 0.01:
        return "square"
    return f"{'landscape' if aspect > 1 else 'portrait'} {max(aspect, 1 / aspect):.2f}:1"


def analysis_panel(a: Analysis) -> Panel:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", justify="right")
    grid.add_column()
    grid.add_row("source", Text(display_path(a.path), style="path"))
    if a.title:
        grid.add_row("title", a.title)
    grid.add_row("canvas", f"{a.viewbox.w:g} × {a.viewbox.h:g}")
    ink = a.ink
    trimmed = "trimmed to content" if ink.trimmed else "full viewBox"
    grid.add_row(
        "content", f"{ink.box.w:.0f} × {ink.box.h:.0f}  ·  {_shape_words(ink.aspect)}  ·  {trimmed}"
    )
    colours = Text("colourful" if a.colorful else "monochrome", style="bold")
    colours.append("  ")
    for color, share in a.rendered_colors[:4]:
        colours.append_text(swatch(color))
        colours.append(f" {share:.0%}  ", style="dim")
    grid.add_row("colours", colours)
    declared = [e for e in a.palette if e.color is not None]
    if declared:
        palette = Text()
        for entry in declared[:6]:
            assert entry.color is not None
            palette.append_text(swatch(entry.color, label=False))
            palette.append(f" {entry.key} ", style="dim")
        grid.add_row("palette", palette)
    for warning in a.warnings:
        grid.add_row("[warn]⚠[/]", Text(warning, style="yellow"))
    return Panel(
        grid, title=f"[accent]{a.path.name}[/]", title_align="left", box=box.ROUNDED, padding=(0, 1)
    )


def _fg_cell(recipe: Recipe) -> Text:
    if recipe.fg_kind == "solid" and recipe.fg_color is not None:
        return swatch(recipe.fg_color)
    if recipe.fg_kind == "grey":
        return Text("greyscale", style="dim")
    if recipe.source != "original":
        return Text(f"theme {recipe.source}", style="magenta")
    return Text("original colours")


def plan_panel(plan: LogoPlan) -> Table:
    table = Table(box=box.SIMPLE_HEAD, show_edge=False, pad_edge=False, title_justify="left")
    table.add_column("variant", style="key")
    table.add_column("foreground")
    table.add_column("background")
    table.add_column("", style="yellow")
    for recipe in plan.recipes.recipes:
        bg = swatch(recipe.bg) if recipe.bg is not None else Text("transparent", style="dim")
        note = (
            f"⚠ contrast {recipe.contrast:.1f}:1" if recipe.low_contrast and recipe.contrast else ""
        )
        table.add_row(recipe.id, _fg_cell(recipe), bg, note)
    for rid, reason in plan.recipes.skipped:
        table.add_row(
            Text(rid, style="dim strike"), "", "", Text(f"skipped: {reason}", style="dim")
        )
    return table


def plan_summary(plan: LogoPlan) -> Text:
    v, s, z = len(plan.recipes.recipes), len(plan.shapes), len(plan.sizes)
    text = Text()
    text.append(f"{len(plan.outputs)} files", style="bold")
    text.append(f" = {v} variants × {s} shapes ({', '.join(plan.shapes)}) × {z} sizes\n", style="")
    sizes = ", ".join(f"{sz.px}{'*' if sz.dpi else ''}" for sz in plan.sizes)
    text.append(f"sizes: {sizes}", style="dim")
    dpis = sorted({sz.dpi for sz in plan.sizes if sz.dpi})
    if dpis:
        text.append(f"   (* print size at {'/'.join(map(str, dpis))} dpi)", style="dim")
    for shape, reason in plan.skipped_shapes:
        text.append(f"\nskipped {shape}: {reason}", style="dim")
    text.append(f"\noutput: {display_path(plan.out_dir)}", style="dim")
    return text


def report_table(report: RunReport) -> Table:
    table = Table(box=box.SIMPLE_HEAD, show_edge=False, pad_edge=False)
    table.add_column("logo", style="key")
    table.add_column("written", justify="right", style="green")
    table.add_column("cached", justify="right", style="dim")
    table.add_column("size", justify="right")
    table.add_column("output", style="path")
    for r in report.logos:
        table.add_row(
            r.stem, str(r.written), str(r.unchanged), human_bytes(r.bytes), display_path(r.out_dir)
        )
    return table


def output_tree(report: RunReport, *, max_logos: int = 3) -> Tree | None:
    if not report.logos or len(report.logos) > max_logos:
        return None
    tree = Tree(Text(display_path(Path(report.logos[0].out_dir).parent), style="path"))
    for r in report.logos:
        branch = tree.add(Text(f"{r.stem}/", style="path"))
        variants: dict[str, set[str]] = {}
        counts: dict[str, int] = {}
        for f in r.files:
            variants.setdefault(f.recipe, set()).add(f.shape)
            counts[f.recipe] = counts.get(f.recipe, 0) + 1
        for variant in sorted(variants):
            branch.add(
                Text.assemble(
                    (variant, "key"),
                    (f"  {counts[variant]} files · {', '.join(sorted(variants[variant]))}", "dim"),
                )
            )
    return tree


def settings_table(console: Console, rows: list[tuple[str, str, str]]) -> None:
    table = Table(box=box.SIMPLE_HEAD, show_edge=False, pad_edge=False)
    table.add_column("setting", style="key")
    table.add_column("value")
    table.add_column("from", style="dim")
    for key, value, source in rows:
        table.add_row(key, value, source)
    console.print(table)
