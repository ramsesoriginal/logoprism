from __future__ import annotations

import json
from pathlib import Path

import msgspec
import pytest

from logoprism.config.layers import ConfigStack, find_project_config
from logoprism.config.models import FIELD_NAMES, Settings
from logoprism.config.schema import build_schema
from logoprism.config.template import INIT_TEMPLATE, render_choices
from logoprism.config.validate import ConfigError

from .conftest import REPO


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults():
    s = ConfigStack(cwd=Path.cwd()).base().settings
    assert s == msgspec.convert({}, Settings)
    assert s.padding == 0.04
    assert s.sizes == ("standard",)


def test_precedence_project_env_cli(tmp_path):
    write(tmp_path / "logoprism.toml", 'padding = 0.1\nsizes = ["favicon"]\nlayout = "flat"\n')
    env = {"LOGOPRISM_PADDING": "0.2", "LOGOPRISM_SIZES": "32,64"}
    stack = ConfigStack(cli={"padding": 0.3}, env=env, cwd=tmp_path)
    resolved = stack.base()
    assert resolved.settings.padding == 0.3  # CLI beats env beats file
    assert resolved.settings.sizes == ("32", "64")  # env beats file
    assert resolved.settings.layout == "flat"  # file beats default
    assert resolved.sources["padding"] == "command line"
    assert resolved.sources["sizes"] == "environment"
    assert resolved.sources["layout"].startswith("project")


def test_relative_paths_are_relative_to_the_config_file(tmp_path):
    write(tmp_path / "cfg" / "logoprism.toml", 'output = "build"\ninputs = ["logos/*.svg"]\n')
    s = ConfigStack(cwd=tmp_path / "cfg").base().settings
    assert Path(s.output) == tmp_path / "cfg" / "build"
    assert Path(s.inputs[0]) == tmp_path / "cfg" / "logos" / "*.svg"


def test_pyproject_tool_table(tmp_path):
    write(tmp_path / "pyproject.toml", '[project]\nname="x"\n[tool.logoprism]\npadding = 0.07\n')
    assert find_project_config(tmp_path) == tmp_path / "pyproject.toml"
    assert ConfigStack(cwd=tmp_path).base().settings.padding == 0.07


def test_pyproject_without_table_is_ignored(tmp_path):
    write(tmp_path / "pyproject.toml", '[project]\nname="x"\n')
    assert find_project_config(tmp_path) is None


def test_per_file_rules_and_sidecar(tmp_path):
    write(
        tmp_path / "logoprism.toml",
        'padding = 0.05\n[files."icons/*.svg"]\nshapes = ["square"]\n[files."other.svg"]\npadding = 0.2\n',
    )
    icon = write(tmp_path / "icons" / "a.svg", "<svg/>")
    plain = write(tmp_path / "b.svg", "<svg/>")
    stack = ConfigStack(cwd=tmp_path)
    assert stack.for_file(icon).settings.shapes == ("square",)
    assert stack.for_file(plain).settings.shapes == ("native", "square", "circle")
    assert stack.for_file(plain).settings.padding == 0.05

    write(tmp_path / "b.logoprism.toml", "padding = 0.33\n")
    resolved = stack.for_file(plain)
    assert resolved.settings.padding == 0.33
    assert resolved.sources["padding"].startswith("sidecar")


def test_themes_merge_by_name(tmp_path):
    write(tmp_path / "logoprism.toml", '[themes.dark]\nmap = { currentColor = "#fff" }\n')
    cli = {"themes": {"custom": {"map": {"#000": "#fff"}}}}
    s = ConfigStack(cli=cli, cwd=tmp_path).base().settings
    assert set(s.themes) == {"dark", "custom"}


def test_explicit_config_and_no_config(tmp_path):
    cfg = write(tmp_path / "elsewhere.toml", "padding = 0.12\n")
    write(tmp_path / "logoprism.toml", "padding = 0.01\n")
    assert ConfigStack(config_path=cfg, cwd=tmp_path).base().settings.padding == 0.12
    assert ConfigStack(use_config_files=False, cwd=tmp_path).base().settings.padding == 0.04
    with pytest.raises(ConfigError, match="not found"):
        ConfigStack(config_path=tmp_path / "missing.toml", cwd=tmp_path).base()


def test_unknown_key_suggests_fix(tmp_path):
    write(tmp_path / "logoprism.toml", "paddng = 0.1\n")
    with pytest.raises(ConfigError, match="did you mean 'padding'"):
        ConfigStack(cwd=tmp_path).base()


def test_invalid_toml_and_values(tmp_path):
    write(tmp_path / "logoprism.toml", "padding = \n")
    with pytest.raises(ConfigError, match="invalid TOML"):
        ConfigStack(cwd=tmp_path).base()
    write(tmp_path / "logoprism.toml", 'padding = "wide"\n')
    with pytest.raises(ConfigError, match=r"padding.*project"):
        ConfigStack(cwd=tmp_path).base()


@pytest.mark.parametrize(
    ("cli", "message"),
    [
        ({"padding": 0.9}, "padding"),
        ({"sizes": ["huge"]}, "unknown size"),
        ({"brand-bg": "nonsense"}, "valid CSS colour"),
        ({"min-contrast": 40}, "min-contrast"),
        ({"layout": "flat", "name-pattern": "{stem}"}, "flat layout"),
        ({"name-pattern": "{stem}-{bogus}-{size}"}, "unknown field"),
        ({"themes": {"x": {"map": {"nope": "#fff"}}}}, "valid CSS colour"),
    ],
)
def test_semantic_validation(cli, message, tmp_path):
    with pytest.raises(ConfigError, match=message):
        ConfigStack(cli=cli, cwd=tmp_path).base()


def test_env_bool_and_errors(tmp_path):
    assert ConfigStack(env={"LOGOPRISM_TRIM": "no"}, cwd=tmp_path).base().settings.trim is False
    with pytest.raises(ConfigError, match="boolean"):
        ConfigStack(env={"LOGOPRISM_TRIM": "maybe"}, cwd=tmp_path).base()
    with pytest.raises(ConfigError, match="number"):
        ConfigStack(env={"LOGOPRISM_PADDING": "lots"}, cwd=tmp_path).base()


def test_init_template_is_valid_toml_and_applies_cleanly(tmp_path):
    import tomllib

    tomllib.loads(INIT_TEMPLATE)  # parses, even with everything commented out
    write(tmp_path / "logoprism.toml", INIT_TEMPLATE)
    assert ConfigStack(cwd=tmp_path).base().settings == msgspec.convert({}, Settings)


def test_template_with_everything_uncommented_is_accepted(tmp_path):
    lines = [
        line[2:] if (line.startswith("# ") and "=" in line) or line.startswith("# [") else line
        for line in INIT_TEMPLATE.splitlines()
    ]
    write(tmp_path / "logoprism.toml", "\n".join(lines))
    s = ConfigStack(cwd=tmp_path).base().settings
    assert s.brand_bg == "#0B3D91" and "dark" in s.themes


def test_render_choices_roundtrip(tmp_path):
    choices = {"shapes": ["native"], "sizes": ["32", "web"], "brand-bg": "#123456", "padding": 0.1}
    write(tmp_path / "logoprism.toml", render_choices(choices))
    s = ConfigStack(cwd=tmp_path).base().settings
    assert (s.shapes, s.sizes, s.brand_bg, s.padding) == (
        ("native",),
        ("32", "web"),
        "#123456",
        0.1,
    )


def test_schema_matches_models_and_committed_file():
    schema = build_schema()
    props = schema["$defs"]["Settings"]["properties"]
    assert set(props) == {*FIELD_NAMES, "files"}
    assert props["padding"]["description"].startswith("Free margin")
    committed = REPO / "docs" / "logoprism.schema.json"
    assert json.loads(committed.read_text(encoding="utf-8")) == schema, (
        "docs/logoprism.schema.json is stale: run `logoprism config schema > docs/logoprism.schema.json`"
    )
