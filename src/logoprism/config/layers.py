"""Layered configuration with provenance.

Precedence, lowest to highest::

    built-in defaults
    < global config        (<user config dir>/logoprism/config.toml)
    < project config       (logoprism.toml, .logoprism.toml or [tool.logoprism] in pyproject.toml)
    < project [files."glob"] overrides
    < sidecar              (<logo>.logoprism.toml next to the SVG)
    < environment          (LOGOPRISM_*)
    < command line

Relative paths inside a config file are relative to that file. Every setting remembers which
layer provided it, which is what ``logoprism config show`` prints.
"""

from __future__ import annotations

import difflib
import fnmatch
import os
import tomllib
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import msgspec

from logoprism.config.models import FIELD_NAMES, Settings
from logoprism.config.validate import ConfigError, validate

if TYPE_CHECKING:
    from collections.abc import Mapping

PROJECT_FILES = ("logoprism.toml", ".logoprism.toml")
PATH_KEYS = ("output", "inputs", "fonts")
ENV_PREFIX = "LOGOPRISM_"


@dataclass(frozen=True, slots=True)
class Layer:
    label: str
    data: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Resolved:
    settings: Settings
    sources: dict[str, str]  # setting name -> layer label (only explicitly set ones)
    files: tuple[Path, ...] = ()  # config files that contributed


@dataclass(slots=True)
class _Loaded:
    layer: Layer
    file_rules: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    path: Path | None = None


def global_config_path() -> Path:
    import platformdirs

    return platformdirs.user_config_path("logoprism", appauthor=False) / "config.toml"


def find_project_config(start: Path) -> Path | None:
    """Walk upwards from *start* for a project configuration file."""
    for directory in (start.resolve(), *start.resolve().parents):
        for name in PROJECT_FILES:
            if (candidate := directory / name).is_file():
                return candidate
        pyproject = directory / "pyproject.toml"
        if pyproject.is_file():
            try:
                data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            except OSError, ValueError:
                continue
            if "logoprism" in data.get("tool", {}):
                return pyproject
    return None


class ConfigStack:
    """Resolves effective settings for a project and, optionally, for one input file."""

    def __init__(
        self,
        *,
        cli: Mapping[str, Any] | None = None,
        config_path: Path | None = None,
        use_config_files: bool = True,
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
    ) -> None:
        self._cli = {k: v for k, v in (cli or {}).items() if v is not None}
        self._explicit = config_path
        self._use_files = use_config_files
        self._env = os.environ if env is None else env
        self._cwd = cwd or Path.cwd()
        self._cache: dict[Path, _Loaded] = {}

    # -- public ---------------------------------------------------------------------
    def derive(self, cli: Mapping[str, Any]) -> ConfigStack:
        """A stack identical to this one but with different command-line overrides."""
        return ConfigStack(
            cli=cli,
            config_path=self._explicit,
            use_config_files=self._use_files,
            env=self._env,
            cwd=self._cwd,
        )

    def base(self) -> Resolved:
        """Settings without any per-file layers (used to discover inputs)."""
        return self._resolve(self._project_for(self._cwd), None)

    def for_file(self, svg: Path) -> Resolved:
        return self._resolve(self._project_for(svg.parent), svg)

    # -- loading --------------------------------------------------------------------
    def _project_for(self, start: Path) -> Path | None:
        if not self._use_files:
            return None
        if self._explicit is not None:
            if not self._explicit.is_file():
                raise ConfigError(f"config file not found: {self._explicit}")
            return self._explicit
        return find_project_config(start) or find_project_config(self._cwd)

    def _load(self, path: Path, label: str) -> _Loaded:
        path = path.resolve()
        if path in self._cache:
            return self._cache[path]
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ConfigError(f"cannot read {path}: {exc.strerror or exc}") from exc
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path}: invalid TOML: {exc}") from exc
        if path.name == "pyproject.toml":
            raw = raw.get("tool", {}).get("logoprism", {})
        rules: list[tuple[str, str, dict[str, Any]]] = []
        for pattern, override in (raw.pop("files", None) or {}).items():
            if not isinstance(override, dict):
                raise ConfigError(f"{path}: files.{pattern!r} must be a table")
            _check_keys(override, path, f"files.{pattern}")
            rules.append((f"{label}:files[{pattern}]", pattern, _absolutise(override, path.parent)))
        _check_keys(raw, path, None)
        loaded = _Loaded(Layer(label, _absolutise(raw, path.parent)), rules, path)
        self._cache[path] = loaded
        return loaded

    def _resolve(self, project: Path | None, svg: Path | None) -> Resolved:
        layers: list[Layer] = []
        files: list[Path] = []
        if self._use_files:
            g = global_config_path()
            if g.is_file():
                loaded = self._load(g, "global")
                layers.append(loaded.layer)
                files.append(g)
        if project is not None:
            loaded = self._load(project, f"project ({project.name})")
            layers.append(loaded.layer)
            files.append(project)
            if svg is not None:
                rel = _relative_posix(svg, project.parent)
                for label, pattern, data in loaded.file_rules:
                    if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(svg.name, pattern):
                        layers.append(Layer(label, data))
        if svg is not None and self._use_files:
            sidecar = svg.with_name(f"{svg.stem}.logoprism.toml")
            if sidecar.is_file():
                layers.append(self._load(sidecar, f"sidecar ({sidecar.name})").layer)
                files.append(sidecar)
        if env := self._env_layer():
            layers.append(env)
        if self._cli:
            layers.append(Layer("command line", {_kebab(k): v for k, v in self._cli.items()}))
        return _merge(layers, tuple(files))

    def _env_layer(self) -> Layer | None:
        data: dict[str, Any] = {}
        types = {f.encode_name: f.type for f in msgspec.structs.fields(Settings)}
        for key in FIELD_NAMES:
            raw = self._env.get(ENV_PREFIX + key.upper().replace("-", "_"))
            if raw is None or key == "themes":
                continue
            data[key] = _coerce_env(key, raw, types[key])
        return Layer("environment", data) if data else None


# =====================================================================================
# helpers
# =====================================================================================
def _kebab(key: str) -> str:
    return key.replace("_", "-")


def _relative_posix(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _check_keys(data: Mapping[str, Any], path: Path, scope: str | None) -> None:
    for key in data:
        if key not in FIELD_NAMES:
            hint = difflib.get_close_matches(key, FIELD_NAMES, n=1)
            extra = f" - did you mean {hint[0]!r}?" if hint else ""
            where = f" in [{scope}]" if scope else ""
            raise ConfigError(f"{path}: unknown setting {key!r}{where}{extra}")


def _absolutise(data: Mapping[str, Any], base: Path) -> dict[str, Any]:
    out = dict(data)
    for key in PATH_KEYS:
        if key not in out:
            continue
        value = out[key]
        if isinstance(value, str):
            out[key] = _abs(value, base)
        elif isinstance(value, list):
            out[key] = [_abs(v, base) if isinstance(v, str) else v for v in value]
    return out


def _abs(value: str, base: Path) -> str:
    return value if Path(value).is_absolute() else str(base / value)


def _coerce_env(key: str, raw: str, annotation: Any) -> Any:
    args = typing.get_args(annotation)
    origin = typing.get_origin(annotation)
    if annotation is bool:
        lowered = raw.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        raise ConfigError(f"{ENV_PREFIX}{key.upper().replace('-', '_')}: {raw!r} is not a boolean")
    if origin is tuple:
        return [part.strip() for part in raw.split(",") if part.strip()]
    candidates = args if args else (annotation,)
    try:
        if int in candidates:
            return int(raw)
        if float in candidates:
            return float(raw)
    except ValueError:
        raise ConfigError(
            f"{ENV_PREFIX}{key.upper().replace('-', '_')}: {raw!r} is not a number"
        ) from None
    return raw


def _merge(layers: list[Layer], files: tuple[Path, ...]) -> Resolved:
    merged: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for layer in layers:
        for key, value in layer.data.items():
            if key == "themes":
                themes = dict(merged.get("themes", {}))
                for name, theme in value.items():
                    themes[name] = theme
                    sources[f"themes.{name}"] = layer.label
                merged["themes"] = themes
            else:
                merged[key] = value
                sources[key] = layer.label
    try:
        settings = msgspec.convert(merged, Settings)
    except msgspec.ValidationError as exc:
        raise ConfigError(_explain(str(exc), sources)) from exc
    validate(settings)
    return Resolved(settings, sources, files)


def _explain(message: str, sources: Mapping[str, str]) -> str:
    """Add the originating layer to a msgspec validation message (``... - at `$.padding```)."""
    if "`$." in message:
        path = message.rsplit("`$.", 1)[1].rstrip("`")
        key = path.split(".")[0].split("[")[0]
        if key in sources:
            return f"invalid value for {key!r} (from {sources[key]}): {message}"
    return f"invalid configuration: {message}"
