"""The interactive wizard: pick a logo, look at what was detected, choose, confirm."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from logoprism.config.validate import ConfigError

if TYPE_CHECKING:
    from rich.console import Console

    from logoprism.config.layers import ConfigStack
    from logoprism.workflow import RunOptions


class WizardCancelled(Exception):
    """The user aborted (Ctrl-C / Esc)."""


@dataclass(slots=True)
class WizardAnswers:
    inputs: list[Path]
    overrides: dict[str, Any] = field(default_factory=dict)


SIZE_CHOICES = (
    ("standard: favicon to A4 (16 … 2048, A4 @300 dpi)", ["standard"]),
    ("favicons and app icons (16 … 256)", ["favicon", "icon"]),
    ("web: icons plus social-card sizes (16 … 1024)", ["favicon", "icon", "web"]),
    ("print: A6, A5 and A4 at 300 dpi", ["print"]),
    ("everything, including 4096", ["all"]),
)


def _ask(question: Any) -> Any:
    answer = question.ask()
    if answer is None:  # Ctrl-C / Esc
        raise WizardCancelled
    return answer


def run_wizard(out: Console, opts: RunOptions, stack: ConfigStack) -> WizardAnswers:
    """Ask for everything the defaults cannot know. Raises :class:`WizardCancelled`."""
    import questionary
    from questionary import Choice, Style

    from logoprism.config.validate import validate
    from logoprism.core.analysis import analyse
    from logoprism.core.color import Color, ColorError
    from logoprism.core.plan import plan_logo
    from logoprism.core.render import ResvgRenderer
    from logoprism.core.sizes import SizeError, parse_sizes
    from logoprism.ui import console as ui
    from logoprism.ui import views
    from logoprism.workflow import expand_inputs

    if not sys.stdin.isatty():
        raise ConfigError("the interactive wizard needs a terminal (stdin is not a TTY)")

    style = Style(
        [
            ("qmark", "fg:#3899DB bold"),
            ("question", "bold"),
            ("answer", "fg:#42B883 bold"),
            ("pointer", "fg:#E85D75 bold"),
            ("highlighted", "fg:#E85D75 bold"),
            ("selected", "fg:#42B883"),
            ("instruction", "fg:#888888"),
        ]
    )
    ui.header(out, subtitle="interactive mode · Ctrl-C to cancel")

    # 1 ── which logo(s)
    specs = list(opts.inputs) or list(stack.base().settings.inputs)
    while not specs:
        raw = _ask(
            questionary.path("Which SVG file or folder?", style=style, only_directories=False)
        ).strip()
        specs = [raw] if raw else []
    files = expand_inputs(specs, recursive=opts.recursive)

    # 2 ── look at the first logo
    base = stack.for_file(files[0]).settings
    renderer = ResvgRenderer()
    analysis, _ = analyse(files[0], renderer, trim=base.trim)
    out.print(views.analysis_panel(analysis))
    if len(files) > 1:
        out.print(f"[dim]… and {len(files) - 1} more logo(s) that get the same choices.[/]\n")

    overrides: dict[str, Any] = {}

    # 3 ── shapes
    shape_choices = [
        Choice("native      the logo's own aspect ratio", "native", checked=True),
        Choice(
            "square      centred on a square canvas"
            + ("  (already square)" if analysis.ink.is_square else ""),
            "square",
            checked=not analysis.ink.is_square,
        ),
        Choice(
            "circle      fits an inscribed circle (avatars, round icons)", "circle", checked=True
        ),
    ]
    shapes = _ask(questionary.checkbox("Shapes", choices=shape_choices, style=style))
    if not shapes:
        raise ConfigError("select at least one shape")
    overrides["shapes"] = list(shapes)

    # 4 ── sizes
    labels = [label for label, _ in SIZE_CHOICES] + ["custom…"]
    chosen = _ask(questionary.select("Sizes", choices=labels, style=style))
    if chosen == "custom…":

        def valid_sizes(text: str) -> bool | str:
            try:
                parse_sizes([text])
            except SizeError as exc:
                return str(exc)
            return True

        text = _ask(
            questionary.text("Sizes (e.g. 32, 256, web, a4@150)", validate=valid_sizes, style=style)
        )
        overrides["sizes"] = [text]
    else:
        overrides["sizes"] = dict(SIZE_CHOICES)[chosen]

    # 5 ── brand colours
    if _ask(questionary.confirm("Add brand colours?", default=False, style=style)):

        def valid_color(text: str) -> bool | str:
            if not text.strip():
                return True
            try:
                Color.parse(text)
            except ColorError:
                return "not a CSS colour (try #0B3D91, rebeccapurple, rgb(11 61 145) …)"
            return True

        for key, prompt in (
            ("brand-bg", "Brand background colour (empty to skip)"),
            ("brand-fg", "Brand foreground colour (empty to skip)"),
            ("brand-light", "Light brand colour, for dark backgrounds (empty to skip)"),
            ("brand-dark", "Dark brand colour, for light backgrounds (empty to skip)"),
        ):
            value = _ask(questionary.text(prompt, validate=valid_color, style=style)).strip()
            if value:
                overrides[key] = value
                out.print(views.swatch(Color.parse(value)))

    # 6 ── variants (computed from what we know now)
    merged = {**opts.cli, **overrides}
    draft = _settings_from(stack, files[0], merged)
    draft_plan = plan_logo(analysis, draft)
    recipes = draft_plan.recipes.recipes
    picked = _ask(
        questionary.checkbox(
            "Variants",
            choices=[Choice(r.id, r.id, checked=True) for r in recipes],
            style=style,
        )
    )
    if not picked:
        raise ConfigError("select at least one variant")
    if len(picked) != len(recipes):
        overrides["variants"] = list(picked)

    # 7 ── output
    output = _ask(questionary.text("Output folder", default=draft.output, style=style)).strip()
    if output and output != draft.output:
        overrides["output"] = output

    # 8 ── confirm
    final = _settings_from(stack, files[0], {**opts.cli, **overrides})
    validate(final)
    final_plan = plan_logo(analysis, final)
    out.print()
    out.print(views.plan_summary(final_plan))
    out.print()
    if not _ask(
        questionary.confirm(
            f"Generate {len(final_plan.outputs) * len(files)} files?", default=True, style=style
        )
    ):
        raise WizardCancelled

    if _ask(
        questionary.confirm("Save these choices to logoprism.toml?", default=False, style=style)
    ):
        from logoprism.config.template import render_choices

        target = Path("logoprism.toml")
        if target.exists() and not _ask(
            questionary.confirm("logoprism.toml exists. Overwrite?", default=False, style=style)
        ):
            out.print("[dim]kept the existing logoprism.toml[/]")
        else:
            target.write_text(render_choices(overrides), encoding="utf-8")
            out.print(f"[ok]✓[/] wrote {target}")
    out.print()
    return WizardAnswers(files, overrides)


def _settings_from(stack: ConfigStack, svg: Path, overrides: dict[str, Any]):
    return stack.derive(overrides).for_file(svg).settings
