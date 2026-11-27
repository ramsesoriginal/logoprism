"""Ink measurement and output layouts (native / square / circle)."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from logoprism.core.svg import SvgDoc, SvgError, ViewBox

if TYPE_CHECKING:
    from PIL import Image

    from logoprism.core.render import Renderer

Shape = Literal["native", "square", "circle"]
CircleFit = Literal["tight", "bbox"]
SHAPES: tuple[Shape, ...] = ("native", "square", "circle")

PROBE_SIZE = 1024
ALPHA_THRESHOLD = 3
MAX_PADDING = 0.4
SQUARE_TOLERANCE = 0.01


@dataclass(frozen=True, slots=True)
class Circle:
    cx: float
    cy: float
    r: float


@dataclass(frozen=True, slots=True)
class Ink:
    """The area a logo actually occupies, in SVG user units."""

    box: ViewBox
    circle: Circle  # tight enclosing circle of the drawn pixels
    trimmed: bool

    @property
    def aspect(self) -> float:
        return self.box.w / self.box.h

    @property
    def is_square(self) -> bool:
        return abs(self.aspect - 1.0) <= SQUARE_TOLERANCE

    @property
    def bbox_circle(self) -> Circle:
        b = self.box
        return Circle(b.x + b.w / 2, b.y + b.h / 2, math.hypot(b.w, b.h) / 2)


@dataclass(frozen=True, slots=True)
class Layout:
    """A concrete view rectangle plus pixel dimensions for one output."""

    view: ViewBox
    width: int
    height: int
    shape: Shape


# =====================================================================================
# measuring
# =====================================================================================
@dataclass(frozen=True, slots=True)
class Measurement:
    ink: Ink
    probe: Image.Image  # the small RGBA render the measurement was taken from


def measure_ink(doc: SvgDoc, renderer: Renderer, *, trim: bool = True) -> Measurement:
    """Render the document once, small, and find where the ink is."""
    vb = doc.viewbox
    scale = PROBE_SIZE / max(vb.w, vb.h)
    px_w, px_h = max(1, round(vb.w * scale)), max(1, round(vb.h * scale))
    image = renderer.render(doc.render_source(vb, px_w, px_h), px_w, px_h)
    mask = image.getchannel("A").point([0] * ALPHA_THRESHOLD + [255] * (256 - ALPHA_THRESHOLD))
    bbox = mask.getbbox()
    if bbox is None:
        raise SvgError("the SVG renders as completely transparent (nothing to export)")
    sx, sy = vb.w / px_w, vb.h / px_h
    points = _edge_points(mask)
    hull = _convex_hull(points)
    cx, cy, r = min_enclosing_circle(hull)
    circle = Circle(vb.x + cx * sx, vb.y + cy * sy, r * (sx + sy) / 2 + (sx + sy) / 2)
    if not trim:
        return Measurement(Ink(vb, circle, trimmed=False), image)
    x0, y0, x1, y1 = bbox
    x0, y0 = max(0, x0 - 1), max(0, y0 - 1)  # one probe pixel of anti-aliasing slack
    x1, y1 = min(px_w, x1 + 1), min(px_h, y1 + 1)
    box = ViewBox(vb.x + x0 * sx, vb.y + y0 * sy, (x1 - x0) * sx, (y1 - y0) * sy)
    return Measurement(Ink(box, circle, trimmed=True), image)


def _edge_points(mask: Image.Image) -> list[tuple[float, float]]:
    width, height = mask.size
    data = mask.tobytes()
    points: list[tuple[float, float]] = []
    for y in range(height):
        row = data[y * width : (y + 1) * width]
        left = row.find(255)
        if left < 0:
            continue
        right = row.rfind(255) + 1
        points += [(left, y), (left, y + 1), (right, y), (right, y + 1)]
    return points


# =====================================================================================
# computational geometry
# =====================================================================================
def _convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def min_enclosing_circle(points: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Smallest circle containing all *points* (expected linear time, deterministic)."""
    if not points:
        raise ValueError("no points")
    pts = list(points)
    random.Random(0).shuffle(pts)

    def contains(c: tuple[float, float, float], p: tuple[float, float]) -> bool:
        return math.hypot(p[0] - c[0], p[1] - c[1]) <= c[2] * (1 + 1e-12) + 1e-9

    circle = (pts[0][0], pts[0][1], 0.0)
    for i, p in enumerate(pts):
        if contains(circle, p):
            continue
        circle = (p[0], p[1], 0.0)
        for j, q in enumerate(pts[:i]):
            if contains(circle, q):
                continue
            circle = _circle2(p, q)
            for r in pts[:j]:
                if not contains(circle, r):
                    circle = _circle3(p, q, r)
    return circle


def _circle2(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float, float]:
    return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, math.hypot(a[0] - b[0], a[1] - b[1]) / 2)


def _circle3(
    a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]
) -> tuple[float, float, float]:
    d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
    if abs(d) < 1e-12:  # collinear: the widest pair decides
        return max((_circle2(a, b), _circle2(a, c), _circle2(b, c)), key=lambda t: t[2])
    a2, b2, c2 = (p[0] ** 2 + p[1] ** 2 for p in (a, b, c))
    ux = (a2 * (b[1] - c[1]) + b2 * (c[1] - a[1]) + c2 * (a[1] - b[1])) / d
    uy = (a2 * (c[0] - b[0]) + b2 * (a[0] - c[0]) + c2 * (b[0] - a[0])) / d
    return (ux, uy, math.hypot(a[0] - ux, a[1] - uy))


# =====================================================================================
# layouts
# =====================================================================================
def make_layout(
    ink: Ink, shape: Shape, size: int, *, padding: float = 0.04, circle_fit: CircleFit = "tight"
) -> Layout:
    """Compute the view rectangle and pixel dimensions for *shape* at *size* px.

    ``size`` is the longest edge for ``native`` and the edge length for ``square``/``circle``.
    *padding* is the free margin as a fraction of the canvas edge on every side.
    """
    padding = min(max(padding, 0.0), MAX_PADDING)
    box = ink.box
    if shape == "native":
        longest = max(box.w, box.h)
        pad = longest * padding / (1 - 2 * padding)
        cw, ch = box.w + 2 * pad, box.h + 2 * pad
        cx, cy = box.x + box.w / 2, box.y + box.h / 2
        scale = size / max(cw, ch)
        width, height = max(1, round(cw * scale)), max(1, round(ch * scale))
    elif shape == "square":
        cw = ch = max(box.w, box.h) / (1 - 2 * padding)
        cx, cy = box.x + box.w / 2, box.y + box.h / 2
        width = height = size
    elif shape == "circle":
        circle = ink.circle if circle_fit == "tight" else ink.bbox_circle
        cw = ch = 2 * circle.r / (1 - 2 * padding)
        cx, cy = circle.cx, circle.cy
        width = height = size
    else:  # pragma: no cover - guarded by the type
        raise ValueError(f"unknown shape {shape!r}")
    return Layout(ViewBox(cx - cw / 2, cy - ch / 2, cw, ch), width, height, shape)
