from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from logoprism.core.color import BLACK, WHITE, Color, ColorError, contrast_ratio, is_current_color


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("#08131F", (8, 19, 31, 1.0)),
        ("#fff", (255, 255, 255, 1.0)),
        ("rebeccapurple", (102, 51, 153, 1.0)),
        ("rgb(10 20 30)", (10, 20, 30, 1.0)),
        ("rgba(10, 20, 30, 0.5)", (10, 20, 30, 0.5)),
        ("hsl(0 100% 50%)", (255, 0, 0, 1.0)),
        ("#ff000080", (255, 0, 0, pytest.approx(0.502, abs=0.01))),
    ],
)
def test_parse_css_colors(text, expected):
    c = Color.parse(text)
    assert (c.r, c.g, c.b, c.a) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("oklch(1 0 0)", (255, 255, 255)),
        ("oklch(0 0 0)", (0, 0, 0)),
        ("oklab(0.628 0.2249 0.1258)", (255, 0, 0)),  # sRGB red in OKLab
        ("lab(54.29 80.8 69.89)", (255, 0, 0)),  # sRGB red in CIELAB (D50)
        ("lch(54.29 106.84 40.85)", (255, 0, 0)),
        ("color(display-p3 1 1 1)", (255, 255, 255)),
        ("color(srgb-linear 1 1 1)", (255, 255, 255)),
    ],
)
def test_wide_gamut_and_perceptual_spaces(text, expected):
    got = Color.parse(text).rgb
    assert all(abs(a - b) <= 2 for a, b in zip(got, expected, strict=True)), got


def test_oklch_chroma_roundtrip():
    c = Color.parse("oklch(0.7 0.1 200)")
    assert c.chroma == pytest.approx(0.1, abs=0.01)
    assert c.oklch[2] == pytest.approx(200, abs=3)


@pytest.mark.parametrize("bad", ["", "nope", "#12", "currentColor", "url(#x)"])
def test_parse_rejects(bad):
    assert Color.try_parse(bad) is None
    with pytest.raises(ColorError):
        Color.parse(bad)


def test_current_color_keyword():
    assert is_current_color(" currentColor ")
    assert not is_current_color("red")


def test_contrast_extremes():
    assert contrast_ratio(BLACK, WHITE) == pytest.approx(21.0)
    assert contrast_ratio(WHITE, WHITE) == pytest.approx(1.0)
    assert contrast_ratio(BLACK, WHITE) == contrast_ratio(WHITE, BLACK)


def test_colourfulness():
    assert not Color.parse("#08131F").is_colorful()  # ink, not colour
    assert not Color.parse("#808080").is_colorful()
    assert Color.parse("#B88A3B").is_colorful()
    assert Color.parse("#e85d75").is_colorful()


def test_grey_matches_css_weights():
    assert Color.parse("#ff0000").grey().r == round(0.2126 * 255)
    grey = Color.parse("#336699").grey()
    assert grey.r == grey.g == grey.b


@given(st.integers(0, 255), st.integers(0, 255), st.integers(0, 255))
def test_hex_roundtrip(r, g, b):
    c = Color(r, g, b)
    assert Color.parse(c.hex) == c


@given(st.integers(0, 255), st.integers(0, 255), st.integers(0, 255))
def test_luminance_and_chroma_bounds(r, g, b):
    c = Color(r, g, b)
    assert 0 <= c.luminance <= 1
    assert c.chroma >= 0
