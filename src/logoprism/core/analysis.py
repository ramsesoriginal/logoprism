"""Load a logo, measure it, and describe it."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from logoprism.core.color import Color
from logoprism.core.geometry import Ink, measure_ink
from logoprism.core.svg import PaletteEntry, SvgDoc, ViewBox

if TYPE_CHECKING:
    from PIL import Image

    from logoprism.core.render import Renderer

STATS_GRID = 192  # nearest-neighbour sample grid for colour statistics
OPAQUE_ALPHA = 200
COLORFUL_SHARE = 0.003  # at least this share of opaque samples must be chromatic


@dataclass(frozen=True, slots=True)
class Analysis:
    """Everything the planner needs to know about one logo."""

    path: Path
    stem: str
    title: str | None
    viewbox: ViewBox
    ink: Ink
    colorful: bool
    colorful_share: float
    rendered_colors: tuple[tuple[Color, float], ...]  # most frequent opaque colours + share
    palette: tuple[PaletteEntry, ...]
    warnings: tuple[str, ...]
    fingerprint: str
    svg: bytes  # normalised document, ready for rendering

    @property
    def native_is_square(self) -> bool:
        return self.ink.is_square


def safe_stem(path: Path) -> str:
    stem = re.sub(r"[^\w.\-]+", "-", path.stem, flags=re.UNICODE).strip("-.")
    return stem or "logo"


def analyse(path: Path, renderer: Renderer, *, trim: bool = True) -> tuple[Analysis, SvgDoc]:
    """Parse *path*, measure its ink and classify its colours."""
    doc = SvgDoc.from_path(path)
    measurement = measure_ink(doc, renderer, trim=trim)
    share, colors = _colour_stats(measurement.probe)
    svg = doc.to_bytes()
    analysis = Analysis(
        path=path,
        stem=safe_stem(path),
        title=doc.title,
        viewbox=doc.viewbox,
        ink=measurement.ink,
        colorful=share >= COLORFUL_SHARE,
        colorful_share=share,
        rendered_colors=colors,
        palette=tuple(doc.palette()),
        warnings=tuple(doc.warnings),
        fingerprint=doc.fingerprint,
        svg=svg,
    )
    return analysis, doc


def _colour_stats(probe: Image.Image) -> tuple[float, tuple[tuple[Color, float], ...]]:
    from PIL import Image  # local: keep module import light

    sample = probe.resize((STATS_GRID, STATS_GRID), Image.Resampling.NEAREST)
    counts: Counter[tuple[int, int, int]] = Counter()
    raw = sample.tobytes()
    for i in range(0, len(raw), 4):
        if raw[i + 3] >= OPAQUE_ALPHA:
            counts[(raw[i], raw[i + 1], raw[i + 2])] += 1
    total = sum(counts.values())
    if not total:
        return 0.0, ()
    colourful = sum(n for rgb, n in counts.items() if Color(*rgb).is_colorful())
    top = tuple((Color(*rgb), n / total) for rgb, n in counts.most_common(6))
    return colourful / total, top
