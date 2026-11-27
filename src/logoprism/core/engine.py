"""Execution: incremental, parallel, atomic.

Work is split into jobs, one per ``(logo, shape, size)``. A job renders the logo once per
colour source (the original plus each theme) and derives every variant from that raster,
so the expensive step scales with sizes x shapes, not with variants.
"""

from __future__ import annotations

import json
import os
import signal
import time
from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from logoprism import __version__
from logoprism.core.color import Color
from logoprism.core.compose import on_background  # noqa: F401  (import warms the worker)
from logoprism.core.geometry import Layout, Shape
from logoprism.core.plan import MANIFEST_NAME, MANIFEST_VERSION, LogoPlan, OutputSpec
from logoprism.core.png import encode_png, write_atomic
from logoprism.core.recipes import Recipe, apply_recipe
from logoprism.core.render import FontOptions, ResvgRenderer
from logoprism.core.svg import SvgDoc, SvgError

if TYPE_CHECKING:
    from collections.abc import Callable

Status = Literal["written", "unchanged"]


@dataclass(frozen=True, slots=True)
class JobOutput:
    path: str
    recipe: Recipe
    key: str


@dataclass(frozen=True, slots=True)
class Job:
    logo: int
    svg: bytes
    shape: Shape
    size_px: int
    dpi: int | None
    layout: Layout
    outputs: tuple[JobOutput, ...]
    themes: tuple[tuple[str, tuple[tuple[str, str], ...]], ...]
    round_bg: bool
    optimize: bool
    compress_level: int
    fonts: FontOptions


@dataclass(frozen=True, slots=True)
class FileResult:
    path: str
    key: str
    sha256: str
    bytes: int
    width: int
    height: int
    recipe: str
    shape: str
    size: int
    status: Status


@dataclass(slots=True)
class JobResult:
    logo: int
    files: list[FileResult] = field(default_factory=list)
    error: str | None = None
    seconds: float = 0.0


@dataclass(slots=True)
class LogoReport:
    stem: str
    source: str
    written: int = 0
    unchanged: int = 0
    bytes: int = 0
    errors: list[str] = field(default_factory=list)
    files: list[FileResult] = field(default_factory=list)
    out_dir: str = ""

    @property
    def total(self) -> int:
        return self.written + self.unchanged


@dataclass(slots=True)
class RunReport:
    logos: list[LogoReport]
    seconds: float
    workers: int

    @property
    def written(self) -> int:
        return sum(r.written for r in self.logos)

    @property
    def unchanged(self) -> int:
        return sum(r.unchanged for r in self.logos)

    @property
    def bytes(self) -> int:
        return sum(r.bytes for r in self.logos)

    @property
    def errors(self) -> list[str]:
        return [e for r in self.logos for e in r.errors]


# =====================================================================================
# manifest / staleness
# =====================================================================================
def load_manifest(path: Path) -> dict[str, dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {}
    if not isinstance(data, dict) or data.get("manifest-version") != MANIFEST_VERSION:
        return {}
    files = data.get("files")
    return files if isinstance(files, dict) else {}


def _rel(plan: LogoPlan, path: Path) -> str:
    return path.relative_to(plan.out_dir).as_posix()


def stale_outputs(plan: LogoPlan) -> list[OutputSpec]:
    """Outputs that are missing or were produced from different inputs/settings."""
    manifest = load_manifest(plan.manifest_path)
    return [
        o
        for o in plan.outputs
        if not o.path.is_file() or manifest.get(_rel(plan, o.path), {}).get("key") != o.key
    ]


# =====================================================================================
# worker
# =====================================================================================
def _worker_init() -> None:
    """Ctrl-C reaches the whole process group; only the parent should react to it."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)


@lru_cache(maxsize=4)
def _load(svg: bytes) -> SvgDoc:
    return SvgDoc.from_bytes(svg)


def run_job(job: Job) -> JobResult:
    """Render one ``(shape, size)`` and write all of its variants. Runs in a worker."""
    started = time.perf_counter()
    result = JobResult(job.logo)
    try:
        doc = _load(job.svg)
        renderer = ResvgRenderer(job.fonts)
        theme_maps = {name: {k: Color.parse(v) for k, v in items} for name, items in job.themes}
        lay = job.layout
        bases = {}
        for source in {o.recipe.source for o in job.outputs}:
            color_map = theme_maps.get(source)
            bases[source] = renderer.render(
                doc.render_source(lay.view, lay.width, lay.height, color_map),
                lay.width,
                lay.height,
            )
        for out in job.outputs:
            image = apply_recipe(
                bases[out.recipe.source],
                out.recipe,
                round_bg=job.round_bg and job.shape == "circle",
            )
            data = encode_png(
                image, dpi=job.dpi, optimize=job.optimize, compress_level=job.compress_level
            )
            written = write_atomic(Path(out.path), data)
            result.files.append(
                FileResult(
                    out.path,
                    out.key,
                    written.sha256,
                    written.bytes,
                    lay.width,
                    lay.height,
                    out.recipe.id,
                    job.shape,
                    job.size_px,
                    "written",
                )
            )
    except (SvgError, OSError, ValueError) as exc:
        result.error = f"{type(exc).__name__}: {exc}"
    result.seconds = time.perf_counter() - started
    return result


# =====================================================================================
# orchestration
# =====================================================================================
def execute(
    plans: list[LogoPlan],
    *,
    jobs: int | None = None,
    force: bool = False,
    on_progress: Callable[[int, str], None] | None = None,
) -> RunReport:
    """Write every output of *plans*. *on_progress* receives ``(files_done, label)``."""
    started = time.perf_counter()
    reports = [
        LogoReport(p.analysis.stem, str(p.analysis.path), out_dir=str(p.out_dir)) for p in plans
    ]
    pending: list[Job] = []

    for index, plan in enumerate(plans):
        report = reports[index]
        manifest = {} if force else load_manifest(plan.manifest_path)
        grouped: dict[tuple[str, int], list[OutputSpec]] = {}
        for o in plan.outputs:
            fresh = (
                not force
                and o.path.is_file()
                and manifest.get(_rel(plan, o.path), {}).get("key") == o.key
            )
            if fresh:
                entry = manifest[_rel(plan, o.path)]
                report.unchanged += 1
                report.bytes += int(entry.get("bytes", 0))
                report.files.append(_entry_to_result(o, entry))
                if on_progress:
                    on_progress(
                        1, f"{plan.analysis.stem}: {o.recipe.id}/{o.shape}/{o.size.px} (cached)"
                    )
            else:
                grouped.setdefault((o.shape, o.size.px), []).append(o)
        s = plan.settings
        themes = tuple((name, tuple(sorted(theme.map.items()))) for name, theme in s.themes.items())
        fonts = FontOptions(
            files=tuple(f for f in s.fonts if Path(f).is_file()),
            dirs=tuple(f for f in s.fonts if Path(f).is_dir()),
            system_fonts=s.system_fonts,
        )
        for (_shape, size_px), outs in grouped.items():
            first = outs[0]
            pending.append(
                Job(
                    logo=index,
                    svg=plan.analysis.svg,
                    shape=first.shape,
                    size_px=size_px,
                    dpi=first.size.dpi,
                    layout=first.layout,
                    outputs=tuple(JobOutput(str(o.path), o.recipe, o.key) for o in outs),
                    themes=themes,
                    round_bg=s.round_bg,
                    optimize=s.optimize,
                    compress_level=s.compress_level,
                    fonts=fonts,
                )
            )

    pending.sort(key=lambda j: j.size_px, reverse=True)  # big jobs first: better balance
    workers = max(1, min(jobs or os.process_cpu_count() or 1, len(pending)))
    if len(pending) <= 2:
        workers = 1

    def collect(result: JobResult) -> None:
        report = reports[result.logo]
        if result.error:
            report.errors.append(result.error)
        for f in result.files:
            report.written += 1
            report.bytes += f.bytes
            report.files.append(f)
        if on_progress:
            on_progress(len(result.files), f"{report.stem}")

    try:
        if workers == 1:
            for job in pending:
                collect(run_job(job))
        else:
            pool = ProcessPoolExecutor(max_workers=workers, initializer=_worker_init)
            futures = [pool.submit(run_job, job) for job in pending]
            collected: set[Future[JobResult]] = set()
            try:
                for future in as_completed(futures):
                    collect(future.result())
                    collected.add(future)
            except BaseException:
                # Stop queueing, let jobs that are already running finish (they are short),
                # and keep their results so the next run resumes instead of starting over.
                pool.shutdown(wait=True, cancel_futures=True)
                for future in futures:
                    finished = future.done() and not future.cancelled()
                    if future not in collected and finished and future.exception() is None:
                        collect(future.result())
                raise
            else:
                pool.shutdown()
    finally:
        # Even when interrupted: remember what was finished, so the next run resumes from there.
        for index, plan in enumerate(plans):
            if reports[index].files or not reports[index].errors:
                _write_manifest(plan, reports[index])
    return RunReport(reports, time.perf_counter() - started, workers)


def _entry_to_result(o: OutputSpec, entry: dict) -> FileResult:
    return FileResult(
        str(o.path),
        o.key,
        str(entry.get("sha256", "")),
        int(entry.get("bytes", 0)),
        int(entry.get("width", o.layout.width)),
        int(entry.get("height", o.layout.height)),
        o.recipe.id,
        o.shape,
        o.size.px,
        "unchanged",
    )


def _write_manifest(plan: LogoPlan, report: LogoReport) -> None:
    files = {
        _rel(plan, Path(f.path)): {
            "key": f.key,
            "sha256": f.sha256,
            "bytes": f.bytes,
            "width": f.width,
            "height": f.height,
            "variant": f.recipe,
            "shape": f.shape,
            "size": f.size,
        }
        for f in sorted(report.files, key=lambda r: r.path)
    }
    data = {
        "manifest-version": MANIFEST_VERSION,
        "tool": f"logoprism {__version__}",
        "source": {"path": plan.analysis.path.name, "sha256": plan.analysis.fingerprint},
        "files": files,
    }
    plan.out_dir.mkdir(parents=True, exist_ok=True)
    (plan.out_dir / MANIFEST_NAME).write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
