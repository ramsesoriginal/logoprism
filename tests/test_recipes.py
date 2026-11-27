from __future__ import annotations

import msgspec
import pytest
from PIL import Image

from logoprism.config.models import Settings
from logoprism.core.color import Color
from logoprism.core.recipes import apply_recipe, build_recipes


def settings(**kw) -> Settings:
    return msgspec.convert(kw, Settings)


def ids(rs) -> set[str]:
    return {r.id for r in rs.recipes}


MONO = {
    "black-on-transparent",
    "black-on-white",
    "white-on-transparent",
    "white-on-black",
}
COLOUR = MONO | {
    "color-on-transparent",
    "color-on-white",
    "color-on-black",
    "grey-on-transparent",
    "grey-on-white",
    "grey-on-black",
}


def test_monochrome_logo_gets_black_and_white_only():
    assert ids(build_recipes(settings(), colorful=False)) == MONO


def test_colourful_logo_adds_original_and_grey():
    assert ids(build_recipes(settings(), colorful=True)) == COLOUR


def test_brand_background():
    rs = build_recipes(settings(**{"brand-bg": "#0b3d91"}), colorful=True)
    assert ids(rs) >= COLOUR
    assert {
        "color-on-brand-bg",
        "black-on-brand-bg",
        "white-on-brand-bg",
    } <= ids(rs)
    assert "brand-on-brand-bg" not in ids(rs)  # no brand foreground given


def test_brand_foreground_and_background():
    rs = build_recipes(settings(**{"brand-bg": "#0b3d91", "brand-fg": "#ffcc00"}), colorful=False)
    assert {"brand-on-transparent", "brand-on-white", "brand-on-black", "brand-on-brand-bg"} <= ids(
        rs
    )
    assert "color-on-brand-bg" not in ids(rs)  # monochrome logo: no original colours


def test_light_dark_pair():
    rs = build_recipes(
        settings(**{"brand-light": "#ffffff", "brand-dark": "#0b1f3a"}), colorful=False
    )
    assert {
        "brand-dark-on-transparent",
        "brand-dark-on-white",
        "brand-dark-on-brand-light",
        "brand-light-on-transparent",
        "brand-light-on-black",
        "brand-light-on-brand-dark",
    } <= ids(rs)


def test_identical_colours_are_skipped_with_reason():
    rs = build_recipes(settings(**{"brand-bg": "#ffffff"}), colorful=False)
    assert "white-on-brand-bg" not in ids(rs)
    assert any(rid == "white-on-brand-bg" and "same colour" in why for rid, why in rs.skipped)


def test_min_contrast_skips_and_low_contrast_is_flagged():
    flagged = build_recipes(settings(**{"brand-bg": "#0b3d91"}), colorful=False)
    black_on_brand = next(r for r in flagged.recipes if r.id == "black-on-brand-bg")
    assert black_on_brand.low_contrast
    strict = build_recipes(settings(**{"brand-bg": "#0b3d91", "min-contrast": 3.0}), colorful=False)
    assert "black-on-brand-bg" not in ids(strict)


def test_variant_globs_and_excludes():
    rs = build_recipes(
        settings(variants=["black-*", "*-on-black"], exclude=["*-white"]), colorful=True
    )
    assert ids(rs) == {
        "black-on-transparent",
        "black-on-white",
        "white-on-black",
        "color-on-black",
        "grey-on-black",
    } - {"black-on-white"}


def test_unmatched_pattern_warns():
    rs = build_recipes(settings(variants=["nothing-*"]), colorful=True)
    assert not rs.recipes
    assert any("nothing-*" in w for w in rs.warnings)


def test_theme_adds_variants_per_background():
    s = settings(
        themes={
            "dark": {"map": {"currentColor": "#fff"}, "backgrounds": ["transparent", "#0d1117"]}
        }
    )
    rs = build_recipes(s, colorful=False)
    assert {"dark-on-transparent", "dark-on-bg-0d1117"} <= ids(rs)
    assert all(r.source == "dark" for r in rs.recipes if r.id.startswith("dark-"))


def logo_image() -> Image.Image:
    im = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    im.putpixel((1, 1), (200, 30, 30, 255))
    im.putpixel((2, 2), (200, 30, 30, 128))
    return im


@pytest.mark.parametrize(
    "name", ["black-on-transparent", "white-on-black", "grey-on-white", "color-on-transparent"]
)
def test_apply_recipe_pixels(name):
    recipe = next(r for r in build_recipes(settings(), colorful=True).recipes if r.id == name)
    out = apply_recipe(logo_image(), recipe)
    assert out.size == (4, 4)
    if name == "black-on-transparent":
        assert out.mode == "RGBA"
        assert out.getpixel((1, 1)) == (0, 0, 0, 255)
        assert out.getpixel((2, 2)) == (0, 0, 0, 128)
        assert out.getpixel((0, 0))[3] == 0
    elif name == "white-on-black":
        assert out.mode == "RGB"
        assert out.getpixel((1, 1)) == (255, 255, 255)
        assert out.getpixel((0, 0)) == (0, 0, 0)
    elif name == "grey-on-white":
        r, g, b = out.getpixel((1, 1))
        assert r == g == b == Color(200, 30, 30).grey().r
        assert out.getpixel((0, 0)) == (255, 255, 255)
    else:
        assert out.getpixel((1, 1)) == (200, 30, 30, 255)


def test_round_background_leaves_corners_transparent():
    recipe = next(
        r for r in build_recipes(settings(), colorful=False).recipes if r.id == "white-on-black"
    )
    out = apply_recipe(Image.new("RGBA", (64, 64), (0, 0, 0, 0)), recipe, round_bg=True)
    assert out.mode == "RGBA"
    assert out.getpixel((0, 0))[3] == 0
    assert out.getpixel((32, 32)) == (0, 0, 0, 255)
