from __future__ import annotations

import json
from pathlib import Path

import msgspec
import pytest
from PIL import Image

from logoprism.config.models import Settings
from logoprism.core.analysis import analyse
from logoprism.core.engine import execute, stale_outputs
from logoprism.core.plan import MANIFEST_NAME, plan_logo

from .conftest import COLOR_SQUARE, LOGOPRISM_SVG, MONO_WIDE


def open_png(path: Path) -> Image.Image:
    with Image.open(path) as im:
        im.load()
        return im.copy() if not im.info else _with_info(im)


def _with_info(im: Image.Image) -> Image.Image:
    copy = im.copy()
    copy.info = dict(im.info)
    return copy


def make_plan(svg: Path, renderer, out: Path, **kw):
    settings = msgspec.convert({"output": str(out), **kw}, Settings)
    analysis, _ = analyse(svg, renderer, trim=settings.trim)
    return plan_logo(analysis, settings)


def test_analysis_classifies_colours(renderer, write_svg):
    assert not analyse(write_svg("m.svg", MONO_WIDE), renderer)[0].colorful
    assert analyse(write_svg("c.svg", COLOR_SQUARE), renderer)[0].colorful
    assert analyse(LOGOPRISM_SVG, renderer)[0].colorful


def test_plan_counts_and_skips_square_for_square_logos(renderer, write_svg, tmp_path):
    plan = make_plan(write_svg("c.svg", COLOR_SQUARE), renderer, tmp_path / "o", sizes=["16", "32"])
    assert plan.shapes == ("native", "circle")
    assert plan.skipped_shapes
    assert len(plan.outputs) == 10 * 2 * 2  # variants x shapes x sizes
    forced = make_plan(
        write_svg("c.svg", COLOR_SQUARE),
        renderer,
        tmp_path / "o",
        sizes=["16"],
        **{"force-square": True},
    )
    assert "square" in forced.shapes


def test_plan_paths_nested_and_flat(renderer, write_svg, tmp_path):
    svg = write_svg("My Logo!.svg", MONO_WIDE)
    nested = make_plan(svg, renderer, tmp_path / "o", sizes=["32"])
    first = nested.outputs[0].path
    assert first.relative_to(tmp_path / "o").as_posix() == (
        "My-Logo/black-on-transparent/native/My-Logo-black-on-transparent-native-32.png"
    )
    flat = make_plan(svg, renderer, tmp_path / "o", sizes=["32"], layout="flat")
    assert flat.outputs[0].path.parent == tmp_path / "o" / "My-Logo"


def test_custom_name_pattern(renderer, write_svg, tmp_path):
    plan = make_plan(
        write_svg("m.svg", MONO_WIDE),
        renderer,
        tmp_path / "o",
        sizes=["32"],
        **{"name-pattern": "{size}"},
    )
    assert plan.outputs[0].path.name == "32.png"
    assert len({o.path for o in plan.outputs}) == len(plan.outputs)


def test_colliding_paths_are_detected(renderer, write_svg, tmp_path):
    # two sizes can only collide if the pattern drops {size}, which validation forbids,
    # so exercise the guard directly with an impossible-to-validate-away situation.
    from logoprism.core.plan import plan_logo

    svg = write_svg("m.svg", MONO_WIDE)
    analysis, _ = analyse(svg, renderer)
    settings = msgspec.convert({"output": str(tmp_path / "o"), "sizes": ["32", "32px"]}, Settings)
    assert len(plan_logo(analysis, settings).outputs) == len(
        plan_logo(analysis, msgspec.structs.replace(settings, sizes=("32",))).outputs
    )


@pytest.mark.slow
def test_execute_writes_correct_pngs(renderer, write_svg, tmp_path):
    out = tmp_path / "out"
    plan = make_plan(write_svg("m.svg", MONO_WIDE), renderer, out, sizes=["64", "a4@72"])
    report = execute([plan], jobs=1)
    assert not report.errors
    assert report.written == len(plan.outputs) == 4 * 3 * 2

    def img(variant: str, shape: str, size: int) -> Image.Image:
        return open_png(out / "m" / variant / shape / f"m-{variant}-{shape}-{size}.png")

    native = img("black-on-transparent", "native", 64)
    assert native.mode == "RGBA"
    assert max(native.size) == 64
    assert native.size[0] > native.size[1]  # wide logo stays wide
    assert native.getpixel((0, 0))[3] == 0  # transparent corner
    assert native.getpixel((native.size[0] // 2, native.size[1] // 2)) == (0, 0, 0, 255)

    on_black = img("white-on-black", "square", 64)
    assert on_black.mode == "RGB"
    assert on_black.size == (64, 64)
    assert on_black.getpixel((0, 0)) == (0, 0, 0)
    assert on_black.getpixel((32, 32)) == (255, 255, 255)

    circle = img("black-on-white", "circle", 64)
    assert circle.size == (64, 64)

    a4 = img("black-on-white", "native", 595)  # a4 @ 72 dpi
    assert a4.info["dpi"] == pytest.approx((72, 72), abs=0.01)
    assert "srgb" in a4.info


@pytest.mark.slow
def test_incremental_runs_and_manifest(renderer, write_svg, tmp_path):
    svg = write_svg("c.svg", COLOR_SQUARE)
    out = tmp_path / "out"
    plan = make_plan(svg, renderer, out, sizes=["32"])
    assert len(stale_outputs(plan)) == len(plan.outputs)

    first = execute([plan], jobs=1)
    assert first.written == len(plan.outputs) and first.unchanged == 0
    assert stale_outputs(plan) == []

    second = execute([plan], jobs=1)
    assert second.written == 0 and second.unchanged == len(plan.outputs)

    manifest = json.loads((out / "c" / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["manifest-version"] == 1
    assert len(manifest["files"]) == len(plan.outputs)
    assert all(k.endswith(".png") and "\\" not in k for k in manifest["files"])

    forced = execute([plan], jobs=1, force=True)
    assert forced.written == len(plan.outputs)

    # a different setting invalidates exactly the affected outputs
    changed = make_plan(svg, renderer, out, sizes=["32"], padding=0.1)
    assert len(stale_outputs(changed)) == len(changed.outputs)

    # deleting one file is noticed and repaired
    victim = plan.outputs[0].path
    victim.unlink()
    assert [o.path for o in stale_outputs(plan)] == [victim]
    assert execute([plan], jobs=1).written == 1
    assert victim.is_file()


@pytest.mark.slow
def test_interrupted_run_resumes_where_it_stopped(renderer, write_svg, tmp_path, monkeypatch):
    import logoprism.core.engine as engine

    plan = make_plan(
        write_svg("c.svg", COLOR_SQUARE), renderer, tmp_path / "out", sizes=["16", "32", "48"]
    )
    real_run_job = engine.run_job
    calls = []

    def flaky(job):
        calls.append(job.size_px)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return real_run_job(job)

    monkeypatch.setattr(engine, "run_job", flaky)
    with pytest.raises(KeyboardInterrupt):
        execute([plan], jobs=1)
    monkeypatch.setattr(engine, "run_job", real_run_job)

    manifest = json.loads((plan.out_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["files"], "progress made before the interrupt is remembered"
    resumed = execute([plan], jobs=1)
    assert 0 < resumed.unchanged < len(plan.outputs)
    assert resumed.written + resumed.unchanged == len(plan.outputs)
    assert stale_outputs(plan) == []


@pytest.mark.slow
def test_output_is_byte_deterministic(renderer, write_svg, tmp_path):
    svg = write_svg("c.svg", COLOR_SQUARE)
    a = make_plan(svg, renderer, tmp_path / "a", sizes=["48"])
    b = make_plan(svg, renderer, tmp_path / "b", sizes=["48"])
    execute([a], jobs=1)
    execute([b], jobs=1)
    for oa, ob in zip(a.outputs, b.outputs, strict=True):
        assert oa.path.read_bytes() == ob.path.read_bytes()


@pytest.mark.slow
def test_themes_recolour_the_source(renderer, write_svg, tmp_path):
    svg = write_svg("c.svg", COLOR_SQUARE)
    plan = make_plan(
        svg,
        renderer,
        tmp_path / "out",
        sizes=["32"],
        shapes=["native"],
        variants=["dark-*"],
        themes={"dark": {"map": {"#cc0000": "#ffffff"}, "backgrounds": ["black"]}},
    )
    assert [o.recipe.id for o in plan.outputs] == ["dark-on-black"]
    execute([plan], jobs=1)
    image = open_png(plan.outputs[0].path).convert("RGB")
    # the red bar (left part of the logo) became white, on a black background
    assert image.getpixel((image.width // 5, image.height // 2)) == (255, 255, 255)


@pytest.mark.slow
def test_parallel_pool_matches_inline(renderer, write_svg, tmp_path):
    svg = write_svg("c.svg", COLOR_SQUARE)
    inline = make_plan(svg, renderer, tmp_path / "inline", sizes=["16", "32", "48", "64"])
    pooled = make_plan(svg, renderer, tmp_path / "pooled", sizes=["16", "32", "48", "64"])
    execute([inline], jobs=1)
    report = execute([pooled], jobs=2)
    assert report.workers == 2 and not report.errors
    for a, b in zip(inline.outputs, pooled.outputs, strict=True):
        assert a.path.read_bytes() == b.path.read_bytes()
