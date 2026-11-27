from __future__ import annotations

import pytest

from logoprism.core.color import Color
from logoprism.core.svg import SvgDoc, SvgError, ViewBox

from .conftest import COLOR_SQUARE, EVIL_ENTITY, LOGOPRISM_SVG, MONO_WIDE, NS


def doc(text: str) -> SvgDoc:
    return SvgDoc.from_bytes(text.encode())


def test_viewbox_and_title():
    d = SvgDoc.from_path(LOGOPRISM_SVG)
    assert d.viewbox == ViewBox(0, 0, 512, 512)
    assert d.title == "LogoPrism brand mark"


def test_var_with_fallback_is_resolved():
    # The renderer cannot do this itself: the whole reason SvgDoc exists.
    d = SvgDoc.from_path(LOGOPRISM_SVG)
    assert "var(" not in d.to_bytes().decode()
    keys = {e.key for e in d.palette()}
    assert {"#e85d75", "#f49b48", "#e5b844", "#42b883", "#3899db", "#9873d8"} <= keys


def test_global_custom_property_beats_fallback_and_missing_uses_fallback():
    keys = {e.key for e in doc(COLOR_SQUARE).palette()}
    assert {"#cc0000", "#00aa00", "#0000cc"} <= keys


def test_unresolvable_var_is_reported():
    d = doc(f'<svg {NS} viewBox="0 0 1 1"><rect width="1" height="1" fill="var(--nope)"/></svg>')
    assert any("--nope" in w for w in d.warnings)


def test_ink_color_and_current_color():
    d = doc(MONO_WIDE)
    assert d.ink_color() == Color.parse("#102030")
    assert {e.key for e in d.palette()} == {"currentColor", "#102030"}


def test_entities_are_refused():
    with pytest.raises(SvgError, match="entity"):
        doc(EVIL_ENTITY)


@pytest.mark.parametrize(
    "bad", ["not xml", "<html/>", "<svg viewBox='0 0 1 1'/>", f'<svg {NS} viewBox="0 0 0 5"/>']
)
def test_invalid_documents(bad):
    with pytest.raises(SvgError):
        doc(bad)


def test_unsafe_content_is_stripped():
    d = doc(
        f'<svg {NS} xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 10 10" onload="x()">'
        "<script>alert(1)</script><foreignObject/>"
        '<image href="https://example.com/a.png" width="1" height="1"/>'
        '<image href="data:image/png;base64,AAAA" width="1" height="1"/></svg>'
    )
    xml = d.to_bytes().decode()
    assert "script" not in xml
    assert "foreignObject" not in xml
    assert "onload" not in xml
    assert "example.com" not in xml
    assert "data:image/png" in xml
    assert len(d.warnings) == 2


def test_text_is_flagged():
    d = doc(f'<svg {NS} viewBox="0 0 10 10"><text>hi</text></svg>')
    assert d.has_text
    assert any("<text>" in w for w in d.warnings)


def test_size_fallback_without_viewbox():
    d = doc(f'<svg {NS} width="2in" height="1in"/>')
    assert d.viewbox == ViewBox(0, 0, 192, 96)


def test_color_map_remaps_declared_colours_and_current_color():
    d = doc(COLOR_SQUARE)
    out = d.render_source(
        d.viewbox, 10, 10, {"#cc0000": Color.parse("#ffffff"), "#00aa00": Color.parse("#000000")}
    ).decode()
    assert "#ffffff" in out
    assert "#cc0000" not in out
    assert "#0000cc" in out  # untouched

    mono = doc(MONO_WIDE)
    out = mono.render_source(
        mono.viewbox, 10, 10, {"currentColor": Color.parse("#abcdef")}
    ).decode()
    assert "color:#abcdef" in out
    assert "#102030" not in out


def test_render_source_sets_geometry():
    d = doc(MONO_WIDE)
    out = d.render_source(ViewBox(1, 2, 30, 40), 300, 400).decode()
    assert 'viewBox="1 2 30 40"' in out
    assert 'width="300"' in out
    assert 'height="400"' in out


def test_fingerprint_is_stable_and_content_sensitive():
    assert doc(MONO_WIDE).fingerprint == doc(MONO_WIDE).fingerprint
    assert doc(MONO_WIDE).fingerprint != doc(COLOR_SQUARE).fingerprint
