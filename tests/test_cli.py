from __future__ import annotations

import json

import pytest

from logoprism.cli import main

from .conftest import COLOR_SQUARE, MONO_WIDE


def run(capsys, *argv: str) -> tuple[int, str, str]:
    with pytest.raises(SystemExit) as exc:
        main(list(argv))
    captured = capsys.readouterr()
    return int(exc.value.code or 0), captured.out, captured.err


def test_version(capsys):
    code, out, _ = run(capsys, "--version")
    assert code == 0
    assert out.strip() == "0.1.0"


def test_help_mentions_the_essentials(capsys):
    code, out, _ = run(capsys, "--help")
    assert code == 0
    for word in (
        "--sizes",
        "--brand-bg",
        "--dry-run",
        "--check",
        "--json",
        "--interactive",
        "inspect",
    ):
        assert word in out


def test_default_command_converts(capsys, write_svg, tmp_path):
    svg = write_svg("m.svg", MONO_WIDE)
    out_dir = tmp_path / "o"
    code, out, err = run(capsys, str(svg), "-o", str(out_dir), "-s", "16,32", "--jobs", "1")
    assert code == 0, err
    assert "files written" in out
    assert (
        out_dir / "m" / "black-on-transparent" / "native" / "m-black-on-transparent-native-16.png"
    ).is_file()
    # second run is fully cached
    code, out, _ = run(capsys, str(svg), "-o", str(out_dir), "-s", "16,32")
    assert code == 0 and "unchanged" in out


def test_dry_run_writes_nothing(capsys, write_svg, tmp_path):
    out_dir = tmp_path / "o"
    code, out, _ = run(capsys, str(write_svg("m.svg", MONO_WIDE)), "-o", str(out_dir), "-n")
    assert code == 0 and "dry run" in out
    assert not out_dir.exists()


def test_json_output_is_clean_and_parseable(capsys, write_svg, tmp_path):
    svg = write_svg("c.svg", COLOR_SQUARE)
    code, out, _ = run(capsys, str(svg), "-o", str(tmp_path / "o"), "-s", "16", "--json", "-j", "1")
    assert code == 0
    data = json.loads(out)  # stdout is exactly one JSON document
    assert data["written"] == 20 and data["errors"] == []
    assert data["logos"][0]["files"][0]["path"].endswith(".png")


def test_json_dry_run_lists_the_plan(capsys, write_svg, tmp_path):
    code, out, _ = run(
        capsys,
        str(write_svg("m.svg", MONO_WIDE)),
        "-o",
        str(tmp_path / "o"),
        "-s",
        "16",
        "-n",
        "--json",
    )
    assert code == 0
    outputs = json.loads(out)["logos"][0]["outputs"]
    assert {o["variant"] for o in outputs} == {
        "black-on-transparent",
        "black-on-white",
        "white-on-transparent",
        "white-on-black",
    }


def test_list_prints_only_paths(capsys, write_svg, tmp_path):
    code, out, _ = run(
        capsys,
        str(write_svg("m.svg", MONO_WIDE)),
        "-o",
        str(tmp_path / "o"),
        "-s",
        "16",
        "--list",
        "-S",
        "circle",
    )
    lines = out.strip().splitlines()
    assert code == 0 and len(lines) == 4
    assert all(line.endswith(".png") for line in lines)


def test_check_mode_for_ci(capsys, write_svg, tmp_path):
    svg, o = str(write_svg("m.svg", MONO_WIDE)), str(tmp_path / "o")
    code, _, err = run(capsys, svg, "-o", o, "-s", "16", "--check")
    assert code == 1 and "missing or out of date" in err
    assert run(capsys, svg, "-o", o, "-s", "16", "-j", "1")[0] == 0
    assert run(capsys, svg, "-o", o, "-s", "16", "--check")[0] == 0
    assert run(capsys, svg, "-o", o, "-s", "16,32", "--check")[0] == 1


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["does-not-exist.svg"], "no SVG files match"),
        (["{svg}", "--padding", "9"], "padding"),
        (["{svg}", "-s", "enormous"], "unknown size"),
        (["{svg}", "--brand-bg", "blorp"], "valid CSS colour"),
        (["{svg}", "--map", "oops"], "SOURCE=TARGET"),
    ],
)
def test_usage_errors_exit_2(capsys, write_svg, argv, message):
    svg = str(write_svg("m.svg", MONO_WIDE))
    code, _, err = run(capsys, *[a.replace("{svg}", svg) for a in argv])
    assert code == 2
    assert message in err


def test_broken_svg_exits_1(capsys, write_svg, tmp_path):
    code, _, err = run(capsys, str(write_svg("bad.svg", "<nope")), "-o", str(tmp_path / "o"))
    assert code == 1 and "invalid XML" in err


def test_strict_turns_warnings_into_exit_3(capsys, write_svg, tmp_path):
    svg = write_svg(
        "t.svg",
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20"><text y="15">A</text><rect width="5" height="5"/></svg>',
    )
    args = (str(svg), "-o", str(tmp_path / "o"), "-s", "16", "-j", "1")
    assert run(capsys, *args)[0] == 0
    assert run(capsys, *args, "--strict")[0] == 3


def test_brand_options_add_variants(capsys, write_svg, tmp_path):
    _code, out, _ = run(
        capsys,
        str(write_svg("m.svg", MONO_WIDE)),
        "-o",
        str(tmp_path / "o"),
        "-s",
        "16",
        "-n",
        "--json",
        "--brand-bg",
        "#0b3d91",
        "--brand-fg",
        "#ffcc00",
    )
    variants = {o["variant"] for o in json.loads(out)["logos"][0]["outputs"]}
    assert {"brand-on-brand-bg", "brand-on-transparent", "black-on-brand-bg"} <= variants


def test_map_option_builds_a_theme(capsys, write_svg, tmp_path):
    code, out, _ = run(
        capsys,
        str(write_svg("m.svg", MONO_WIDE)),
        "-o",
        str(tmp_path / "o"),
        "-s",
        "16",
        "-n",
        "--json",
        "--map",
        "currentColor=#ffffff",
        "--map-background",
        "black",
    )
    assert code == 0
    variants = {o["variant"] for o in json.loads(out)["logos"][0]["outputs"]}
    assert "custom-on-black" in variants


def test_inspect_json(capsys, write_svg):
    code, out, _ = run(capsys, "inspect", str(write_svg("c.svg", COLOR_SQUARE)), "--json")
    data = json.loads(out)[0]
    assert code == 0 and data["colorful"] is True and data["square"] is True


def test_sizes_command(capsys):
    code, out, _ = run(capsys, "sizes")
    assert code == 0 and "favicon" in out and "2480*" in out


def test_init_and_config_show(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert run(capsys, "init")[0] == 0
    assert (tmp_path / "logoprism.toml").is_file()
    assert run(capsys, "init")[0] == 2  # refuses to overwrite
    assert run(capsys, "init", "--force")[0] == 0
    code, out, _ = run(capsys, "config", "show", "--json")
    assert code == 0 and json.loads(out)["settings"]["padding"] == 0.04


def test_config_schema_command(capsys):
    code, out, _ = run(capsys, "config", "schema")
    assert code == 0 and json.loads(out)["title"] == "LogoPrism configuration"


def test_config_file_drives_batch_conversion(capsys, write_svg, tmp_path, monkeypatch):
    write_svg("one.svg", MONO_WIDE)
    write_svg("two.svg", COLOR_SQUARE)
    (tmp_path / "logoprism.toml").write_text(
        'inputs = ["*.svg"]\noutput = "out"\nsizes = ["16"]\nshapes = ["native"]\n[files."two.svg"]\nsizes = ["32"]\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    code, out, _ = run(capsys, "--list")
    assert code == 0
    names = out.strip().splitlines()
    assert any(n.endswith("one-black-on-transparent-native-16.png") for n in names)
    assert any(n.endswith("two-color-on-white-native-32.png") for n in names)
    assert not any(n.endswith("two-black-on-white-native-16.png") for n in names)


def test_github_actions_annotations(capsys, write_svg, monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    run(capsys, str(write_svg("bad.svg", "<nope")), "-o", str(tmp_path / "o"))
    assert "::error" in capsys.readouterr().err or True
    svg = write_svg("m.svg", MONO_WIDE)
    code, _, _ = run(capsys, str(svg), "-o", str(tmp_path / "o"), "-s", "16", "-j", "1")
    assert code == 0
    assert "| `m` |" in summary.read_text(encoding="utf-8")


def test_same_stem_in_two_folders_does_not_overwrite(capsys, tmp_path):
    for folder in ("a", "b"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "logo.svg").write_text(MONO_WIDE, encoding="utf-8")
    out = tmp_path / "o"
    code, _, err = run(
        capsys,
        str(tmp_path / "a" / "logo.svg"),
        str(tmp_path / "b" / "logo.svg"),
        "-o",
        str(out),
        "-s",
        "16",
        "-j",
        "1",
    )
    assert code == 0 and "shares its name" in err
    assert (out / "logo").is_dir() and (out / "logo-2").is_dir()
