"""The convert workflow: resolve config, analyse, plan, (check | dry-run | execute), report."""

from __future__ import annotations

import glob
import json
import os
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from logoprism import __version__
from logoprism.config.layers import ConfigStack
from logoprism.config.validate import ConfigError
from logoprism.core.svg import SvgError

if TYPE_CHECKING:
    from rich.console import Console

    from logoprism.core.analysis import Analysis
    from logoprism.core.engine import RunReport
    from logoprism.core.plan import LogoPlan

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_WARNINGS = 3
EXIT_INTERRUPTED = 130

SVG_SUFFIXES = (".svg", ".svgz")


@dataclass(slots=True)
class RunOptions:
    inputs: list[str] = field(default_factory=list)
    cli: dict[str, Any] = field(default_factory=dict)  # explicit setting overrides (kebab keys)
    config: Path | None = None
    use_config_files: bool = True
    recursive: bool = False
    dry_run: bool = False
    check: bool = False
    force: bool = False
    list_files: bool = False
    json_out: bool = False
    quiet: bool = False
    strict: bool = False
    interactive: bool = False
    details: bool | None = None  # None = automatic (few logos -> details)


def expand_inputs(specs: list[str], *, recursive: bool = False) -> list[Path]:
    """Files, directories and globs -> a sorted, de-duplicated list of SVG paths."""
    found: dict[Path, None] = {}
    for spec in specs:
        path = Path(spec)
        if path.is_dir():
            pattern = "**/*" if recursive else "*"
            hits = [p for p in sorted(path.glob(pattern)) if p.suffix.lower() in SVG_SUFFIXES]
        elif path.is_file():
            hits = [path]
        else:
            hits = [
                Path(p)
                for p in sorted(glob.glob(spec, recursive=True))  # noqa: PTH207
                if Path(p).is_file() and Path(p).suffix.lower() in SVG_SUFFIXES
            ]
        if not hits:
            raise ConfigError(f"no SVG files match {spec!r}")
        for hit in hits:
            found.setdefault(hit.resolve(), None)
    return list(found)


def github_annotation(level: str, message: str, file: str | None = None) -> None:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        message = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        where = f" file={file}" if file else ""
        print(f"::{level}{where}::{message}", file=sys.stderr, flush=True)  # noqa: T201


def run(opts: RunOptions, out: Console, err: Console) -> int:
    """Execute the whole workflow; returns the process exit code."""
    from logoprism.ui import console as ui
    from logoprism.ui import views

    machine = opts.json_out
    chatty = not (machine or opts.quiet or opts.list_files)
    try:
        stack = ConfigStack(
            cli=opts.cli, config_path=opts.config, use_config_files=opts.use_config_files
        )
        if (
            not opts.interactive
            and not opts.inputs
            and not stack.base().settings.inputs
            and sys.stdin.isatty()
            and sys.stdout.isatty()
            and not (machine or opts.quiet or opts.check or opts.dry_run or opts.list_files)
        ):
            opts.interactive = True  # bare `logoprism` in a terminal: guide the user
        if opts.interactive:
            from logoprism.ui.wizard import WizardCancelled, run_wizard

            try:
                answers = run_wizard(out, opts, stack)
            except WizardCancelled:
                err.print("[warn]Cancelled.[/]")
                return EXIT_INTERRUPTED
            opts.inputs = [str(p) for p in answers.inputs]
            opts.cli = {**opts.cli, **answers.overrides}
            stack = ConfigStack(
                cli=opts.cli, config_path=opts.config, use_config_files=opts.use_config_files
            )
        specs = opts.inputs or list(stack.base().settings.inputs)
        if not specs:
            err.print(
                "[err]error:[/] no input SVG given. Pass files or folders, set [key]inputs[/] "
                "in logoprism.toml, or run [key]logoprism --interactive[/]."
            )
            return EXIT_USAGE
        files = expand_inputs(specs, recursive=opts.recursive)
    except ConfigError as exc:
        err.print(f"[err]error:[/] {exc}")
        github_annotation("error", str(exc))
        return EXIT_USAGE

    if chatty:
        ui.header(out)

    plans, failures = _analyse_and_plan(files, stack, out, err, chatty=chatty)
    if failures and not plans:
        return EXIT_FAILED if not _only_config_errors(failures) else EXIT_USAGE

    show_details = opts.details if opts.details is not None else len(plans) <= 3
    if chatty:
        for plan in plans:
            out.print(views.analysis_panel(plan.analysis))
            if show_details:
                out.print(views.plan_panel(plan))
            out.print(views.plan_summary(plan))
            out.print()

    warnings = sum(len(p.warnings) + sum(r.low_contrast for r in p.recipes.recipes) for p in plans)
    for plan in plans:
        for w in plan.warnings:
            github_annotation("warning", w, str(plan.analysis.path))

    if opts.list_files or (opts.dry_run and machine):
        return _emit_listing(plans, out, opts, failures)
    if opts.check:
        return _check(plans, out, err, opts, failures)
    if opts.dry_run:
        if chatty:
            out.print("[dim]dry run: nothing written.[/]")
        return EXIT_FAILED if failures else (EXIT_WARNINGS if opts.strict and warnings else EXIT_OK)

    total = sum(len(p.outputs) for p in plans)
    try:
        report = _execute_with_progress(plans, out, opts, total)
    except KeyboardInterrupt:
        err.print("\n[warn]Interrupted.[/] Finished files are kept; run again to resume.")
        return EXIT_INTERRUPTED

    errors = [*failures, *report.errors]
    if machine:
        out.print_json(_report_json(report, plans, errors, warnings), indent=2)
    elif chatty:
        _print_report(report, out)
    for e in report.errors:
        err.print(f"[err]error:[/] {e}")
        github_annotation("error", e)
    _step_summary(report, plans)
    if errors:
        return EXIT_FAILED
    if opts.strict and warnings:
        err.print(f"[warn]{warnings} warning(s) and --strict is set.[/]")
        return EXIT_WARNINGS
    return EXIT_OK


# =====================================================================================
def _only_config_errors(failures: list[str]) -> bool:
    return bool(failures) and all(f.startswith("config:") for f in failures)


def _analyse_and_plan(
    files: list[Path],
    stack: ConfigStack,
    out: Console,
    err: Console,
    *,
    chatty: bool,
) -> tuple[list[LogoPlan], list[str]]:
    from logoprism.core.analysis import analyse
    from logoprism.core.plan import plan_logo
    from logoprism.core.render import FontOptions, ResvgRenderer

    plans: list[LogoPlan] = []
    failures: list[str] = []
    used: set[tuple[str, str]] = set()
    status = (
        out.status("[accent]Analysing…[/]", spinner="dots") if chatty and out.is_terminal else None
    )
    if status:
        status.start()
    try:
        for svg in files:
            if status:
                status.update(f"[accent]Analysing[/] {svg.name}")
            try:
                resolved = stack.for_file(svg)
                s = resolved.settings
                renderer = ResvgRenderer(
                    FontOptions(
                        files=tuple(f for f in s.fonts if Path(f).is_file()),
                        dirs=tuple(f for f in s.fonts if Path(f).is_dir()),
                        system_fonts=s.system_fonts,
                    )
                )
                analysis, _doc = analyse(svg, renderer, trim=s.trim)
                stem = analysis.stem
                if (s.output, stem) in used:
                    n = 2
                    while (s.output, f"{stem}-{n}") in used:
                        n += 1
                    analysis = replace(analysis, stem=f"{stem}-{n}")
                    err.print(
                        f"[warn]warning:[/] {svg.name} shares its name with another logo; "
                        f"writing to '{analysis.stem}/'"
                    )
                used.add((s.output, analysis.stem))
                plans.append(plan_logo(analysis, s))
            except SvgError as exc:
                failures.append(f"{svg.name}: {exc}")
                err.print(f"[err]error:[/] {svg.name}: {exc}")
                github_annotation("error", str(exc), str(svg))
            except ConfigError as exc:
                failures.append(f"config: {exc}")
                err.print(f"[err]error:[/] {exc}")
                github_annotation("error", str(exc), str(svg))
    finally:
        if status:
            status.stop()
    return plans, failures


def _execute_with_progress(
    plans: list[LogoPlan], out: Console, opts: RunOptions, total: int
) -> RunReport:
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
        TimeRemainingColumn,
    )

    from logoprism.core.engine import execute

    interactive_ui = out.is_terminal and not (opts.json_out or opts.quiet)
    if not interactive_ui:
        return execute(plans, jobs=_jobs(plans), force=opts.force)
    with Progress(
        SpinnerColumn(style="accent"),
        TextColumn("[bold]Rendering[/]"),
        BarColumn(bar_width=None, complete_style="#3899DB", finished_style="#42B883"),
        MofNCompleteColumn(),
        TextColumn("[dim]files[/]"),
        TimeElapsedColumn(),
        TextColumn("[dim]eta[/]"),
        TimeRemainingColumn(),
        console=out,
        transient=False,
    ) as progress:
        task = progress.add_task("render", total=total)
        return execute(
            plans,
            jobs=_jobs(plans),
            force=opts.force,
            on_progress=lambda n, _label: progress.advance(task, n),
        )


def _jobs(plans: list[LogoPlan]) -> int | None:
    return max((p.settings.jobs or 0 for p in plans), default=0) or None


def _print_report(report: RunReport, out: Console) -> None:
    from logoprism.ui import views

    out.print()
    out.print(views.report_table(report))
    tree = views.output_tree(report)
    if tree is not None:
        out.print(tree)
    cached = f", {report.unchanged} unchanged" if report.unchanged else ""
    mark = "[ok]✓[/]" if not report.errors else "[err]✗[/]"
    out.print(
        f"\n{mark} [bold]{report.written} files written[/]{cached} · "
        f"{views.human_bytes(report.bytes)} · {report.seconds:.1f}s · {report.workers} worker(s)"
    )


def _emit_listing(
    plans: list[LogoPlan], out: Console, opts: RunOptions, failures: list[str]
) -> int:
    if opts.json_out:
        data = {
            "version": __version__,
            "logos": [
                {
                    "source": str(p.analysis.path),
                    "outputs": [_output_json(o) for o in p.outputs],
                    "skipped-variants": [{"id": i, "reason": r} for i, r in p.recipes.skipped],
                    "warnings": list(p.warnings),
                }
                for p in plans
            ],
            "errors": failures,
        }
        out.print_json(json.dumps(data), indent=2)
    else:
        for plan in plans:
            for o in plan.outputs:
                out.print(str(o.path), markup=False, highlight=False, soft_wrap=True)
    return EXIT_FAILED if failures else EXIT_OK


def _output_json(o: Any) -> dict[str, Any]:
    return {
        "path": str(o.path),
        "variant": o.recipe.id,
        "shape": o.shape,
        "size": o.size.px,
        "width": o.layout.width,
        "height": o.layout.height,
    }


def _check(
    plans: list[LogoPlan], out: Console, err: Console, opts: RunOptions, failures: list[str]
) -> int:
    from logoprism.core.engine import stale_outputs

    stale = {p.analysis.stem: stale_outputs(p) for p in plans}
    count = sum(len(v) for v in stale.values())
    total = sum(len(p.outputs) for p in plans)
    if opts.json_out:
        out.print_json(
            json.dumps(
                {
                    "version": __version__,
                    "up-to-date": count == 0 and not failures,
                    "stale": [str(o.path) for v in stale.values() for o in v],
                    "errors": failures,
                }
            ),
            indent=2,
        )
    elif count == 0:
        if not opts.quiet:
            out.print(f"[ok]✓[/] all {total} outputs are up to date")
    else:
        err.print(f"[err]✗[/] {count} of {total} outputs are missing or out of date:")
        for v in stale.values():
            for o in v[:10]:
                err.print(f"  [dim]{o.path}[/]", highlight=False)
        if count > 10:
            err.print("  [dim]…[/]")
        err.print("Run [key]logoprism[/] again to regenerate them.")
        github_annotation("error", f"{count} generated logo outputs are out of date")
    return EXIT_FAILED if (count or failures) else EXIT_OK


def _report_json(report: RunReport, plans: list[LogoPlan], errors: list[str], warnings: int) -> str:
    data = {
        "version": __version__,
        "seconds": round(report.seconds, 3),
        "workers": report.workers,
        "written": report.written,
        "unchanged": report.unchanged,
        "bytes": report.bytes,
        "warnings": warnings,
        "errors": errors,
        "logos": [
            {
                "source": r.source,
                "out-dir": r.out_dir,
                "written": r.written,
                "unchanged": r.unchanged,
                "bytes": r.bytes,
                "files": [
                    {
                        "path": f.path,
                        "variant": f.recipe,
                        "shape": f.shape,
                        "size": f.size,
                        "width": f.width,
                        "height": f.height,
                        "bytes": f.bytes,
                        "sha256": f.sha256,
                        "status": f.status,
                    }
                    for f in sorted(r.files, key=lambda x: x.path)
                ],
                "palette": [e.key for e in p.analysis.palette],
            }
            for r, p in zip(report.logos, plans, strict=True)
        ],
    }
    return json.dumps(data)


def _step_summary(report: RunReport, plans: list[LogoPlan]) -> None:
    """Append a Markdown table to the GitHub Actions job summary when running there."""
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if not target:
        return
    lines = [
        "### LogoPrism",
        "",
        "| logo | written | unchanged | output |",
        "|---|---:|---:|---|",
    ]
    for r, _ in zip(report.logos, plans, strict=True):
        lines.append(f"| `{r.stem}` | {r.written} | {r.unchanged} | `{r.out_dir}` |")
    try:
        with Path(target).open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError:  # pragma: no cover - best effort only
        pass


def analysis_for_inspect(path: Path, stack: ConfigStack) -> Analysis:
    from logoprism.core.analysis import analyse
    from logoprism.core.render import FontOptions, ResvgRenderer

    s = stack.for_file(path).settings
    renderer = ResvgRenderer(FontOptions(system_fonts=s.system_fonts))
    analysis, _ = analyse(path, renderer, trim=s.trim)
    return analysis
