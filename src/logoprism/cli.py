"""Command-line interface (cyclopts). ``logoprism FILE.svg`` converts with sensible defaults."""

from __future__ import annotations

import contextlib
import errno
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from cyclopts import App, Group, Parameter

from logoprism import __version__
from logoprism.ui.console import (
    GLYPH,
    GLYPH_ASCII,
    TAGLINE,
    configure_streams,
    make_consoles,
    use_ascii,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

_glyph = GLYPH_ASCII if use_ascii() else GLYPH

app = App(
    name="logoprism",
    version=__version__,
    version_flags=["--version"],
    help=(
        f"{_glyph} LogoPrism: {TAGLINE}.\n\n"
        "Turns SVG logos into a complete, systematic set of PNGs: favicon to A4, black / white / "
        "colour / greyscale / brand variants, on transparent or solid backgrounds, in native, "
        "square and circle-safe shapes.\n\n"
        "[bold]Quick start:[/bold]  logoprism logo.svg\n"
        "[bold]Guided:[/bold]       logoprism --interactive\n"
        "[bold]Preview:[/bold]      logoprism logo.svg --dry-run"
    ),
    help_format="rich",
    default_parameter=Parameter(negative_iterable=()),
)

G_OUTPUT = Group("Output", sort_key=1)
G_SHAPES = Group("Shapes and sizes", sort_key=2)
G_COLOURS = Group("Colours and variants", sort_key=3)
G_AUTOMATION = Group("Automation", sort_key=4)
G_ENCODING = Group("Rendering and encoding", sort_key=5)
G_CONFIG = Group("Configuration", sort_key=6)


def _split(values: Iterable[str] | None) -> list[str] | None:
    """Accept both ``-s 16 -s 32`` and ``-s 16,32``."""
    if values is None:
        return None
    parts = [p.strip() for v in values for p in str(v).split(",") if p.strip()]
    return parts or None


def _themes_from_map(
    pairs: Sequence[str] | None, backgrounds: Sequence[str] | None
) -> dict[str, Any] | None:
    from logoprism.config.validate import ConfigError

    if not pairs:
        return None
    mapping: dict[str, str] = {}
    for pair in pairs:
        src, sep, dst = pair.partition("=")
        if not sep or not src.strip() or not dst.strip():
            raise ConfigError(f"--map expects SOURCE=TARGET (e.g. '#08131f=#ffffff'), got {pair!r}")
        mapping[src.strip()] = dst.strip()
    theme: dict[str, Any] = {"map": mapping}
    if backgrounds := _split(backgrounds):
        theme["backgrounds"] = backgrounds
    return {"custom": theme}


@app.default
def convert(
    *inputs: Path,
    # ---- output -----------------------------------------------------------------------
    output: Annotated[
        Path | None,
        Parameter(
            name=["--output", "-o"],
            group=G_OUTPUT,
            help="Output directory [default: logoprism-out]",
        ),
    ] = None,
    layout: Annotated[
        str | None,
        Parameter(group=G_OUTPUT, help="'nested' (<stem>/<variant>/<shape>/…) or 'flat'"),
    ] = None,
    name_pattern: Annotated[
        str | None,
        Parameter(group=G_OUTPUT, help="File name template: {stem} {variant} {shape} {size}"),
    ] = None,
    force: Annotated[
        bool,
        Parameter(
            name=["--force", "-f"], group=G_OUTPUT, help="Re-render everything, ignoring the cache"
        ),
    ] = False,
    # ---- shapes and sizes -------------------------------------------------------------
    sizes: Annotated[
        list[str] | None,
        Parameter(
            name=["--sizes", "-s"],
            group=G_SHAPES,
            help="Sizes: 512, favicon, icon, web, large, print, standard, all, a4@300. Repeatable or comma-separated",
        ),
    ] = None,
    shapes: Annotated[
        list[str] | None,
        Parameter(
            name=["--shapes", "-S"],
            group=G_SHAPES,
            help="native, square, circle [default: all three]",
        ),
    ] = None,
    padding: Annotated[
        float | None,
        Parameter(
            group=G_SHAPES,
            help="Margin around the logo as a fraction of the canvas (0-0.4) [default: 0.04]",
        ),
    ] = None,
    trim: Annotated[
        bool | None,
        Parameter(
            group=G_SHAPES,
            help="Crop to the drawn content instead of the SVG viewBox [default: on]",
        ),
    ] = None,
    circle_fit: Annotated[
        str | None,
        Parameter(
            group=G_SHAPES,
            help="'tight' (smallest circle around the ink) or 'bbox' (around the bounding box)",
        ),
    ] = None,
    round_bg: Annotated[
        bool | None,
        Parameter(group=G_SHAPES, help="Make solid backgrounds of circle outputs round"),
    ] = None,
    force_square: Annotated[
        bool | None,
        Parameter(group=G_SHAPES, help="Write square outputs even if the logo is already square"),
    ] = None,
    # ---- colours ----------------------------------------------------------------------
    brand_bg: Annotated[
        str | None, Parameter(group=G_COLOURS, help="Brand background colour")
    ] = None,
    brand_fg: Annotated[
        str | None, Parameter(group=G_COLOURS, help="Brand foreground colour")
    ] = None,
    brand_light: Annotated[
        str | None, Parameter(group=G_COLOURS, help="Light brand colour (for dark backgrounds)")
    ] = None,
    brand_dark: Annotated[
        str | None, Parameter(group=G_COLOURS, help="Dark brand colour (for light backgrounds)")
    ] = None,
    variants: Annotated[
        list[str] | None,
        Parameter(
            name=["--variants", "-V"],
            group=G_COLOURS,
            help="Only these variants (globs): 'black-*', '*-on-transparent'",
        ),
    ] = None,
    exclude: Annotated[
        list[str] | None,
        Parameter(name=["--exclude", "-x"], group=G_COLOURS, help="Drop these variants (globs)"),
    ] = None,
    min_contrast: Annotated[
        float | None,
        Parameter(group=G_COLOURS, help="Skip solid colour pairs below this WCAG contrast ratio"),
    ] = None,
    map: Annotated[
        list[str] | None,
        Parameter(
            name="--map",
            group=G_COLOURS,
            help="Custom colour map SOURCE=TARGET (repeatable); 'currentColor' works as a source",
        ),
    ] = None,
    map_background: Annotated[
        list[str] | None,
        Parameter(
            name="--map-background",
            group=G_COLOURS,
            help="Backgrounds for the --map variant [default: transparent]",
        ),
    ] = None,
    # ---- automation -------------------------------------------------------------------
    dry_run: Annotated[
        bool,
        Parameter(
            name=["--dry-run", "-n"],
            group=G_AUTOMATION,
            help="Analyse and show the plan, write nothing",
        ),
    ] = False,
    check: Annotated[
        bool,
        Parameter(
            group=G_AUTOMATION,
            help="Exit 1 if any output is missing or stale (for CI), write nothing",
        ),
    ] = False,
    list_files: Annotated[
        bool,
        Parameter(
            name="--list",
            group=G_AUTOMATION,
            help="Print the paths that would be written, one per line",
        ),
    ] = False,
    json_out: Annotated[
        bool,
        Parameter(
            name="--json",
            group=G_AUTOMATION,
            help="Machine-readable JSON on stdout, no other output",
        ),
    ] = False,
    quiet: Annotated[
        bool, Parameter(name=["--quiet", "-q"], group=G_AUTOMATION, help="Only errors")
    ] = False,
    strict: Annotated[
        bool, Parameter(group=G_AUTOMATION, help="Exit 3 when there were warnings")
    ] = False,
    no_color: Annotated[
        bool, Parameter(group=G_AUTOMATION, help="Disable colours (NO_COLOR is honoured too)")
    ] = False,
    jobs: Annotated[
        int | None,
        Parameter(
            name=["--jobs", "-j"], group=G_AUTOMATION, help="Worker processes [default: all cores]"
        ),
    ] = None,
    details: Annotated[
        bool | None,
        Parameter(group=G_AUTOMATION, help="Show the variant table [default: when ≤3 logos]"),
    ] = None,
    # ---- rendering --------------------------------------------------------------------
    optimize: Annotated[
        bool | None, Parameter(group=G_ENCODING, help="Smaller PNGs, slower")
    ] = None,
    compress_level: Annotated[
        int | None, Parameter(group=G_ENCODING, help="zlib level 0-9 [default: 6]")
    ] = None,
    fonts: Annotated[
        list[str] | None,
        Parameter(group=G_ENCODING, help="Font files/folders for <text> in logos (repeatable)"),
    ] = None,
    system_fonts: Annotated[
        bool | None,
        Parameter(
            group=G_ENCODING,
            help="Use installed fonts [default: on]; --no-system-fonts for reproducible CI",
        ),
    ] = None,
    # ---- configuration ----------------------------------------------------------------
    interactive: Annotated[
        bool, Parameter(name=["--interactive", "-i"], group=G_CONFIG, help="Guided wizard")
    ] = False,
    recursive: Annotated[
        bool,
        Parameter(name=["--recursive", "-r"], group=G_CONFIG, help="Search folders recursively"),
    ] = False,
    config: Annotated[
        Path | None,
        Parameter(
            name=["--config", "-c"],
            group=G_CONFIG,
            help="Use this config file instead of discovering one",
        ),
    ] = None,
    no_config: Annotated[
        bool, Parameter(group=G_CONFIG, help="Ignore all config files (env vars still apply)")
    ] = False,
) -> int:
    """Convert SVG logos into PNG assets."""
    from logoprism.config.validate import ConfigError
    from logoprism.workflow import EXIT_USAGE, RunOptions, run

    out, err = make_consoles(no_color=no_color, quiet=quiet and not json_out)
    try:
        theme_map = _themes_from_map(map, map_background)
    except ConfigError as exc:
        err.print(f"[err]error:[/] {exc}")
        return EXIT_USAGE
    overrides: dict[str, Any] = {
        "output": None if output is None else str(output),
        "layout": layout,
        "name-pattern": name_pattern,
        "sizes": _split(sizes),
        "shapes": _split(shapes),
        "padding": padding,
        "trim": trim,
        "circle-fit": circle_fit,
        "round-bg": round_bg,
        "force-square": force_square,
        "brand-bg": brand_bg,
        "brand-fg": brand_fg,
        "brand-light": brand_light,
        "brand-dark": brand_dark,
        "variants": _split(variants),
        "exclude": _split(exclude),
        "min-contrast": min_contrast,
        "themes": theme_map,
        "optimize": optimize,
        "compress-level": compress_level,
        "fonts": _split(fonts),
        "system-fonts": system_fonts,
        "jobs": jobs,
    }
    opts = RunOptions(
        inputs=[str(p) for p in inputs],
        cli={k: v for k, v in overrides.items() if v is not None},
        config=config,
        use_config_files=not no_config,
        recursive=recursive,
        dry_run=dry_run,
        check=check,
        force=force,
        list_files=list_files,
        json_out=json_out,
        quiet=quiet,
        strict=strict,
        interactive=interactive,
        details=details,
    )
    try:
        return run(opts, out, err)
    except ConfigError as exc:
        err.print(f"[err]error:[/] {exc}")
        return EXIT_USAGE


@app.command
def wizard() -> int:
    """Guided, interactive conversion (same as ``logoprism --interactive``)."""
    return convert(interactive=True)


@app.command
def inspect(
    *inputs: Path,
    json_out: Annotated[bool, Parameter(name="--json", help="JSON on stdout")] = False,
    no_color: bool = False,
    config: Annotated[Path | None, Parameter(name=["--config", "-c"])] = None,
) -> int:
    """Analyse SVGs: size, content bounds, palette, colour detection, warnings."""
    from logoprism.config.layers import ConfigStack
    from logoprism.config.validate import ConfigError
    from logoprism.core.svg import SvgError
    from logoprism.ui import views
    from logoprism.workflow import EXIT_FAILED, EXIT_USAGE, analysis_for_inspect, expand_inputs

    out, err = make_consoles(no_color=no_color, quiet=json_out)
    try:
        files = expand_inputs([str(p) for p in inputs] or ["."])
        stack = ConfigStack(config_path=config)
    except ConfigError as exc:
        err.print(f"[err]error:[/] {exc}")
        return EXIT_USAGE
    code, results = 0, []
    for svg in files:
        try:
            analysis = analysis_for_inspect(svg, stack)
        except (SvgError, ConfigError) as exc:
            err.print(f"[err]error:[/] {svg.name}: {exc}")
            code = EXIT_FAILED
            continue
        results.append(analysis)
        if not json_out:
            out.print(views.analysis_panel(analysis))
    if json_out:
        print(json.dumps([_analysis_json(a) for a in results], indent=2))
    return code


def _analysis_json(a: Any) -> dict[str, Any]:
    return {
        "source": str(a.path),
        "title": a.title,
        "viewbox": [a.viewbox.x, a.viewbox.y, a.viewbox.w, a.viewbox.h],
        "content": [round(v, 3) for v in (a.ink.box.x, a.ink.box.y, a.ink.box.w, a.ink.box.h)],
        "aspect": round(a.ink.aspect, 4),
        "square": a.ink.is_square,
        "colorful": a.colorful,
        "colorful-share": round(a.colorful_share, 4),
        "rendered-colors": [{"color": c.hex, "share": round(s, 4)} for c, s in a.rendered_colors],
        "palette": [{"color": e.key, "uses": e.uses} for e in a.palette],
        "warnings": list(a.warnings),
        "sha256": a.fingerprint,
    }


@app.command
def sizes() -> int:
    """List the size presets."""
    from rich import box
    from rich.table import Table

    from logoprism.core.sizes import PRESETS, parse_sizes

    out, _ = make_consoles()
    table = Table(box=box.SIMPLE_HEAD, show_edge=False, pad_edge=False)
    table.add_column("preset", style="key")
    table.add_column("pixels")
    table.add_column("for", style="dim")
    for name, (_, purpose) in PRESETS.items():
        px = ", ".join(f"{s.px}{'*' if s.dpi else ''}" for s in parse_sizes([name]))
        table.add_row(name, px, purpose)
    out.print(table)
    out.print(
        "[dim]* print size: physical width at 300 dpi; the PNG records the DPI. "
        "Also accepted: any integer (512), a3-a7 with a DPI (a4@150).[/]"
    )
    return 0


@app.command
def init(
    path: Path = Path("logoprism.toml"),
    *,
    force: Annotated[bool, Parameter(name=["--force", "-f"])] = False,
) -> int:
    """Write a documented ``logoprism.toml`` to start from."""
    from logoprism.config.template import INIT_TEMPLATE

    out, err = make_consoles()
    if path.exists() and not force:
        err.print(f"[err]error:[/] {path} already exists (use --force to overwrite)")
        return 2
    path.write_text(INIT_TEMPLATE, encoding="utf-8")
    out.print(
        f"[ok]✓[/] wrote [path]{path}[/]. Run [key]logoprism config show[/] to check the result."
    )
    return 0


config_app = App(name="config", help="Inspect configuration.", help_format="rich")
app.command(config_app)


@config_app.command
def show(
    file: Path | None = None,
    *,
    config: Annotated[Path | None, Parameter(name=["--config", "-c"])] = None,
    no_config: bool = False,
    json_out: Annotated[bool, Parameter(name="--json")] = False,
) -> int:
    """Show the effective settings and which layer each one came from.

    Parameters
    ----------
    file
        Resolve for this logo (applies [files] overrides and sidecar configs).
    """
    import msgspec

    from logoprism.config.layers import ConfigStack, global_config_path
    from logoprism.config.models import Settings
    from logoprism.config.validate import ConfigError
    from logoprism.ui import views

    out, err = make_consoles()
    try:
        stack = ConfigStack(config_path=config, use_config_files=not no_config)
        resolved = stack.for_file(file) if file else stack.base()
    except ConfigError as exc:
        err.print(f"[err]error:[/] {exc}")
        return 2
    values = msgspec.to_builtins(resolved.settings)
    if json_out:
        print(json.dumps({"settings": values, "sources": resolved.sources}, indent=2))
        return 0
    defaults = msgspec.to_builtins(msgspec.convert({}, Settings))
    rows = []
    for key, value in values.items():
        source = resolved.sources.get(key) or ("default" if value == defaults[key] else "")
        if key == "themes" and value:
            source = ", ".join(
                sorted({v for k, v in resolved.sources.items() if k.startswith("themes.")})
            )
        rows.append(
            (key, json.dumps(value) if not isinstance(value, str) else value, source or "default")
        )
    out.print(f"[dim]global config: {global_config_path()}[/]")
    for f in resolved.files:
        out.print(f"[dim]loaded: {f}[/]")
    views.settings_table(out, rows)
    return 0


@config_app.command
def schema() -> int:
    """Print the JSON Schema of logoprism.toml."""
    from logoprism.config.schema import build_schema

    print(json.dumps(build_schema(), indent=2))
    return 0


@config_app.command
def path() -> int:
    """Print where the global config file lives."""
    from logoprism.config.layers import global_config_path

    print(global_config_path())
    return 0


app.register_install_completion_command()


def main(argv: Sequence[str] | None = None) -> None:
    """Console-script entry point."""
    configure_streams()
    try:
        result = app(list(argv) if argv is not None else None, exit_on_error=True)
    except KeyboardInterrupt:
        sys.exit(130)
    except OSError as exc:
        if exc.errno not in (errno.EPIPE, errno.EINVAL):
            raise
        # The reader (e.g. `| head`) went away: not an error worth a traceback.
        with contextlib.suppress(OSError, ValueError):
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)
    sys.exit(result if isinstance(result, int) else 0)


if __name__ == "__main__":
    main()
