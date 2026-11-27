from __future__ import annotations

import pytest

from logoprism.core.sizes import PRESETS, SizeError, SizeSpec, parse_sizes


def px(tokens):
    return [s.px for s in parse_sizes(tokens)]


def test_integers_and_presets_merge_sorted_and_unique():
    assert px(["512", "favicon", "32px"]) == [16, 32, 48, 512]
    assert px(["64,16 32"]) == [16, 32, 64]


def test_paper_sizes_carry_dpi():
    assert parse_sizes(["a4"]) == [SizeSpec(2480, 300)]
    assert parse_sizes(["a4@150"]) == [SizeSpec(1240, 150)]
    assert px(["print"]) == [1240, 1748, 2480]


def test_dpi_wins_when_same_pixels_are_requested_twice():
    (spec,) = parse_sizes(["2480", "a4"])
    assert spec.dpi == 300


def test_default_ladder_goes_from_favicon_to_a4():
    ladder = px(["standard"])
    assert ladder[0] == 16
    assert ladder[-1] == 2480
    assert ladder == sorted(ladder)


@pytest.mark.parametrize("bad", ["", "0", "99999", "big", "a9", "fav"])
def test_bad_sizes(bad):
    with pytest.raises(SizeError):
        parse_sizes([bad])


def test_typo_suggests_a_preset():
    with pytest.raises(SizeError, match="did you mean 'favicon'"):
        parse_sizes(["favicn"])


def test_every_preset_parses():
    for name in PRESETS:
        assert parse_sizes([name])
