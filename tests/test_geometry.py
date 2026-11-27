from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from logoprism.core.geometry import (
    Circle,
    Ink,
    make_layout,
    measure_ink,
    min_enclosing_circle,
)
from logoprism.core.svg import SvgDoc, ViewBox

from .conftest import LOGOPRISM_SVG, PADDED

points = st.lists(
    st.tuples(st.floats(-1e3, 1e3, allow_nan=False), st.floats(-1e3, 1e3, allow_nan=False)),
    min_size=1,
    max_size=40,
)


@given(points)
def test_enclosing_circle_contains_every_point(pts):
    cx, cy, r = min_enclosing_circle(pts)
    assert all(math.hypot(x - cx, y - cy) <= r * (1 + 1e-9) + 1e-6 for x, y in pts)


@given(points)
def test_enclosing_circle_is_no_bigger_than_the_bbox_circle(pts):
    _, _, r = min_enclosing_circle(pts)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    assert r <= math.hypot(max(xs) - min(xs), max(ys) - min(ys)) / 2 * (1 + 1e-9) + 1e-6


def test_enclosing_circle_known_cases():
    assert min_enclosing_circle([(0, 0), (2, 0)]) == pytest.approx((1, 0, 1))
    cx, cy, r = min_enclosing_circle([(0, 0), (4, 0), (0, 3)])
    assert (cx, cy, r) == pytest.approx((2, 1.5, 2.5))


def ink(w: float, h: float) -> Ink:
    box = ViewBox(10, 20, w, h)
    cx, cy = 10 + w / 2, 20 + h / 2
    return Ink(box, Circle(cx, cy, math.hypot(w, h) / 2), trimmed=True)


@given(
    st.floats(1, 500),
    st.floats(1, 500),
    st.integers(16, 4096),
    st.floats(0, 0.3),
)
def test_layout_invariants(w, h, size, padding):
    i = ink(w, h)
    native = make_layout(i, "native", size, padding=padding)
    assert max(native.width, native.height) == size
    assert native.view.x <= i.box.x + 1e-6
    assert native.view.x + native.view.w >= i.box.x + i.box.w - 1e-6

    square = make_layout(i, "square", size, padding=padding)
    assert (square.width, square.height) == (size, size)
    assert square.view.w == pytest.approx(square.view.h)
    assert square.view.w >= max(w, h) - 1e-6

    circle = make_layout(i, "circle", size, padding=padding)
    assert circle.view.w == pytest.approx(i.bbox_circle.r * 2 / (1 - 2 * padding))


def test_native_keeps_aspect_ratio():
    layout = make_layout(ink(200, 100), "native", 1000, padding=0)
    assert (layout.width, layout.height) == (1000, 500)


def test_padding_is_clamped():
    assert make_layout(ink(10, 10), "square", 100, padding=5).view.w == pytest.approx(10 / 0.2)


def test_measure_ink_trims_to_content(renderer):
    m = measure_ink(SvgDoc.from_bytes(PADDED.encode()), renderer)
    box = m.ink.box
    assert (box.x, box.y, box.w, box.h) == pytest.approx((180, 80, 40, 40), abs=2.0)
    assert m.ink.is_square
    # a square drawn at the centre of the canvas: circle radius is half the diagonal
    assert m.ink.circle.r == pytest.approx(math.hypot(40, 40) / 2, rel=0.03)


def test_measure_ink_without_trim_keeps_viewbox(renderer):
    m = measure_ink(SvgDoc.from_bytes(PADDED.encode()), renderer, trim=False)
    assert m.ink.box == ViewBox(0, 0, 400, 200)
    assert not m.ink.trimmed


def test_measure_ink_on_logoprism(renderer):
    m = measure_ink(SvgDoc.from_path(LOGOPRISM_SVG), renderer)
    assert not m.ink.is_square
    assert m.ink.box.w > m.ink.box.h
    assert m.ink.circle.r < m.ink.bbox_circle.r  # tight really is tighter


def test_blank_svg_is_rejected(renderer):
    from logoprism.core.svg import SvgError

    with pytest.raises(SvgError, match="transparent"):
        measure_ink(
            SvgDoc.from_bytes(b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 5 5"/>'),
            renderer,
        )
