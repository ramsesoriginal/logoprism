"""Output size ladder and presets.

A size token is an integer (``512``, ``512px``), a preset name (``favicon``, ``web``, ...),
or an ISO paper width (``a4``, ``a4@150``) which resolves to pixels at the given DPI
(default 300) and records that DPI in the PNG so printers get the right physical size.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable
from dataclasses import dataclass

MAX_SIZE = 16384
PRINT_DPI = 300

#: Paper widths in millimetres (portrait).
PAPER_WIDTH_MM = {"a7": 74, "a6": 105, "a5": 148, "a4": 210, "a3": 297}

#: What each preset is for, shown by ``logoprism sizes``.
PRESETS: dict[str, tuple[tuple[str, ...], str]] = {
    "favicon": (("16", "32", "48"), "browser tabs and bookmarks"),
    "icon": (("64", "96", "128", "180", "192", "256"), "app icons, touch icons, PWA"),
    "web": (("384", "512", "768", "1024"), "social cards, store listings, hero images"),
    "large": (("2048", "4096"), "high-DPI and archive masters"),
    "print": (("a6", "a5", "a4"), "A6, A5 and A4 width at 300 dpi"),
    "standard": (
        ("16", "32", "48", "64", "128", "256", "512", "1024", "2048", "a4"),
        "the default ladder: favicon to A4",
    ),
}
PRESETS["all"] = (
    tuple(
        dict.fromkeys(
            t for k in ("favicon", "icon", "web", "large", "print") for t in PRESETS[k][0]
        )
    ),
    "every size of every preset",
)
DEFAULT_SIZES: tuple[str, ...] = ("standard",)

_TOKEN = re.compile(r"^(?:(?P<int>\d+)(?:px)?|(?P<paper>a\d)(?:@(?P<dpi>\d+))?)$")


class SizeError(ValueError):
    """A size token could not be understood."""


@dataclass(frozen=True, slots=True, order=True)
class SizeSpec:
    px: int
    dpi: int | None = None

    @property
    def label(self) -> str:
        return str(self.px)


def parse_sizes(tokens: Iterable[str]) -> list[SizeSpec]:
    """Expand tokens (presets included) into a sorted list of unique sizes."""
    found: dict[int, SizeSpec] = {}

    def add(spec: SizeSpec) -> None:
        if not 1 <= spec.px <= MAX_SIZE:
            raise SizeError(f"size {spec.px}px is outside 1..{MAX_SIZE}")
        if spec.px not in found or (spec.dpi and not found[spec.px].dpi):
            found[spec.px] = spec

    def expand(token: str) -> None:
        token = token.strip().lower()
        if not token:
            return
        if token in PRESETS:
            for inner in PRESETS[token][0]:
                expand(inner)
            return
        m = _TOKEN.match(token)
        if not m:
            hint = difflib.get_close_matches(token, [*PRESETS, *PAPER_WIDTH_MM], n=1)
            extra = f" (did you mean {hint[0]!r}?)" if hint else ""
            raise SizeError(f"unknown size {token!r}{extra}")
        if m["int"]:
            add(SizeSpec(int(m["int"])))
            return
        paper = m["paper"]
        if paper not in PAPER_WIDTH_MM:
            raise SizeError(f"unknown paper size {paper!r} (known: {', '.join(PAPER_WIDTH_MM)})")
        dpi = int(m["dpi"] or PRINT_DPI)
        add(SizeSpec(round(PAPER_WIDTH_MM[paper] / 25.4 * dpi), dpi))

    for raw in tokens:
        for part in re.split(r"[,\s]+", raw):
            expand(part)
    if not found:
        raise SizeError("no sizes selected")
    return sorted(found.values())
