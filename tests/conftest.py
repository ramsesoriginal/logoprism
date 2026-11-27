from __future__ import annotations

from pathlib import Path

import pytest

from logoprism.core.render import ResvgRenderer

REPO = Path(__file__).resolve().parents[1]
LOGOPRISM_SVG = REPO / "docs" / "assets" / "logoprism.svg"

NS = 'xmlns="http://www.w3.org/2000/svg"'

#: Wide, single colour, drawn with currentColor and a root ``color``.
MONO_WIDE = (
    f'<svg {NS} viewBox="0 0 200 100" style="color:#102030">'
    '<rect x="20" y="20" width="160" height="60" fill="currentColor"/></svg>'
)
#: Square, two colours via CSS classes, one of them through var() with a fallback.
COLOR_SQUARE = (
    f'<svg {NS} viewBox="0 0 100 100">'
    "<style>:root{--accent:#00aa00}"
    ".a{fill:#cc0000}.b{fill:var(--accent)}.c{fill:var(--missing, #0000cc)}</style>"
    '<rect class="a" x="10" y="10" width="35" height="80"/>'
    '<rect class="b" x="55" y="10" width="35" height="35"/>'
    '<rect class="c" x="55" y="55" width="35" height="35"/></svg>'
)
#: Wide canvas with lots of empty space around a small square.
PADDED = f'<svg {NS} viewBox="0 0 400 200"><rect x="180" y="80" width="40" height="40" fill="#ff8800"/></svg>'
EVIL_ENTITY = (
    '<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
    f'<svg {NS} viewBox="0 0 10 10"><text>&xxe;</text></svg>'
)


@pytest.fixture(autouse=True)
def _isolate_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No test may see the developer's real global config or LOGOPRISM_* variables."""
    import os

    for key in list(os.environ):
        if key.startswith("LOGOPRISM_"):
            monkeypatch.delenv(key)
    monkeypatch.setattr(
        "logoprism.config.layers.global_config_path", lambda: tmp_path / "no-global" / "config.toml"
    )
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)


@pytest.fixture(scope="session")
def renderer() -> ResvgRenderer:
    return ResvgRenderer()


@pytest.fixture
def write_svg(tmp_path: Path):
    def _write(name: str, text: str) -> Path:
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return path

    return _write
