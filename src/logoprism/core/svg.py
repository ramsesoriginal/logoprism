"""Hardened SVG loading, normalisation and recolouring.

The renderer (resvg) understands almost all of SVG, but it silently drops paints written as
CSS custom properties (``stroke: var(--brand, #f00)``). :class:`SvgDoc` therefore *normalises*
documents when they are loaded:

* untrusted input is parsed with a hardened XML parser (no entities, no DTD, no network),
* scripts, ``foreignObject``, event handlers and external references are removed,
* CSS ``var()`` references are resolved (global custom properties and fallbacks),

and offers :meth:`SvgDoc.palette` and :meth:`SvgDoc.render_source` (viewBox/size rewriting and
colour remapping) on top of the normalised tree. The CSS cascade itself is left to the renderer.
"""

from __future__ import annotations

import copy
import gzip
import hashlib
import io
import re
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import tinycss2
from lxml import etree

from logoprism.core.color import Color, is_current_color

if TYPE_CHECKING:
    from lxml.etree import _Element as Element

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
MAX_BYTES = 25 * 1024 * 1024

#: Properties that carry a single paint colour and can be remapped.
COLOR_PROPS: tuple[str, ...] = (
    "fill",
    "stroke",
    "color",
    "stop-color",
    "flood-color",
    "lighting-color",
)
_REMOVED_ELEMENTS = frozenset({"script", "foreignObject"})
_GLOBAL_SELECTORS = frozenset({":root", "svg", "*", "html"})
_VAR_RE = re.compile(r"var\(\s*(--[\w-]+)\s*(?:,((?:[^()]|\([^()]*\))*))?\)")
_UNIT_TO_PX = {
    "": 1.0,
    "px": 1.0,
    "pt": 4 / 3,
    "pc": 16.0,
    "mm": 96 / 25.4,
    "cm": 96 / 2.54,
    "in": 96.0,
}

Rewrite = Callable[[str, str], str | None]


class SvgError(ValueError):
    """The input is not a usable SVG document."""


@dataclass(frozen=True, slots=True)
class ViewBox:
    x: float
    y: float
    w: float
    h: float

    @property
    def attr(self) -> str:
        return f"{_num(self.x)} {_num(self.y)} {_num(self.w)} {_num(self.h)}"


@dataclass(frozen=True, slots=True)
class PaletteEntry:
    """A colour *declared* in the document and how often it is referenced."""

    key: str  # "#rrggbb" or "currentColor"
    color: Color | None
    uses: int


@dataclass(slots=True)
class SvgDoc:
    """A normalised SVG document."""

    root: Element
    viewbox: ViewBox
    warnings: list[str] = field(default_factory=list)
    has_text: bool = False
    has_raster_images: bool = False

    # -- loading --------------------------------------------------------------------
    @classmethod
    def from_path(cls, path: str | Path) -> SvgDoc:
        p = Path(path)
        try:
            size = p.stat().st_size
        except OSError as exc:
            raise SvgError(f"cannot read {p}: {exc.strerror or exc}") from exc
        if size > MAX_BYTES:
            raise SvgError(f"{p.name} is larger than {MAX_BYTES // 2**20} MiB")
        return cls.from_bytes(p.read_bytes())

    @classmethod
    def from_bytes(cls, data: bytes) -> SvgDoc:
        if data[:2] == b"\x1f\x8b":  # .svgz
            try:
                with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
                    data = gz.read(MAX_BYTES + 1)
            except OSError as exc:
                raise SvgError(f"invalid gzip data: {exc}") from exc
            if len(data) > MAX_BYTES:
                raise SvgError("decompressed SVG is too large")
        if re.search(rb"<!ENTITY", data):
            raise SvgError("XML entity declarations are not allowed (refusing to parse)")
        parser = etree.XMLParser(
            resolve_entities=False,
            no_network=True,
            load_dtd=False,
            dtd_validation=False,
            huge_tree=False,
            remove_comments=True,
            remove_pis=True,
        )
        try:
            root = etree.fromstring(data.strip(), parser)
        except etree.XMLSyntaxError as exc:
            raise SvgError(f"invalid XML: {exc}") from exc
        if root.tag != f"{{{SVG_NS}}}svg":
            if root.tag == "svg":
                raise SvgError('missing xmlns="http://www.w3.org/2000/svg" on the <svg> element')
            raise SvgError(f"root element is <{_local(root.tag)}>, expected <svg>")

        warnings: list[str] = []
        _sanitise(root, warnings)
        _resolve_vars(root, warnings)
        viewbox = _viewbox(root, warnings)
        names = {_local(el.tag) for el in root.iter() if isinstance(el.tag, str)}
        doc = cls(root=root, viewbox=viewbox, warnings=warnings, has_text="text" in names)
        doc.has_raster_images = "image" in names
        if doc.has_text:
            warnings.append(
                "contains <text>: rendering depends on installed fonts; "
                "convert text to outlines or pass --fonts for reproducible results"
            )
        return doc

    # -- inspection -----------------------------------------------------------------
    @property
    def title(self) -> str | None:
        for el in self.root.iter(f"{{{SVG_NS}}}title"):
            if el.text and el.text.strip():
                return " ".join(el.text.split())
        return None

    @property
    def fingerprint(self) -> str:
        """Stable hash of the normalised document."""
        return hashlib.sha256(self.to_bytes()).hexdigest()

    def to_bytes(self) -> bytes:
        return etree.tostring(self.root, encoding="utf-8", xml_declaration=True)

    def palette(self) -> list[PaletteEntry]:
        """Colours declared in attributes, ``style`` attributes and ``<style>`` rules."""
        counts: Counter[str] = Counter()
        colors: dict[str, Color | None] = {}

        def record(_prop: str, value: str) -> None:
            if is_current_color(value):
                counts["currentColor"] += 1
                colors["currentColor"] = self.ink_color()
                return
            parsed = Color.try_parse(value)
            if parsed is not None and parsed.a > 0:
                counts[parsed.opaque.hex] += 1
                colors[parsed.opaque.hex] = parsed.opaque

        _walk_paints(self.root, record, write=False)
        return [PaletteEntry(k, colors[k], n) for k, n in counts.most_common()]

    def ink_color(self) -> Color:
        """The colour ``currentColor`` resolves to at the root (default: black)."""
        found = [None]

        def peek(prop: str, value: str) -> None:
            if prop == "color" and (c := Color.try_parse(value)):
                found[0] = c.opaque

        # attributes/stylesheet first, root style attribute wins (highest precedence)
        _walk_paints(self.root, peek, write=False)
        style = self.root.get("style")
        if style:
            _rewrite_declarations(style, peek)
        return found[0] or Color(0, 0, 0)

    # -- rendering source -----------------------------------------------------------
    def render_source(
        self,
        viewbox: ViewBox,
        width: int,
        height: int,
        color_map: Mapping[str, Color] | None = None,
    ) -> bytes:
        """Serialise the document for a specific view rectangle and pixel size.

        *color_map* maps source colours (``#rrggbb`` keys, or ``currentColor``) to target
        colours. Colours that are not mentioned stay untouched.
        """
        root = copy.deepcopy(self.root)
        if color_map:
            _apply_color_map(root, color_map)
        root.set("viewBox", viewbox.attr)
        root.set("width", str(width))
        root.set("height", str(height))
        root.set("preserveAspectRatio", "xMidYMid meet")
        for attr in ("x", "y"):
            root.attrib.pop(attr, None)
        return etree.tostring(root, encoding="utf-8", xml_declaration=True)


# =====================================================================================
# helpers
# =====================================================================================
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _num(v: float) -> str:
    return f"{v:.6f}".rstrip("0").rstrip(".") or "0"


def _sanitise(root: Element, warnings: list[str]) -> None:
    doomed = [
        el for el in root.iter() if isinstance(el.tag, str) and _local(el.tag) in _REMOVED_ELEMENTS
    ]
    for el in doomed:
        if (parent := el.getparent()) is not None:
            parent.remove(el)
    if doomed:
        warnings.append(f"removed {len(doomed)} unsafe element(s) (<script>/<foreignObject>)")
    external = 0
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        for attr in list(el.attrib):
            name = _local(attr)
            if name.lower().startswith("on"):
                del el.attrib[attr]
            elif name == "href":
                value = el.attrib[attr].strip()
                if not value.startswith(("#", "data:")):
                    del el.attrib[attr]
                    external += 1
    if external:
        warnings.append(f"removed {external} external reference(s) (only #id and data: allowed)")


def _resolve_vars(root: Element, warnings: list[str]) -> None:
    """Resolve CSS ``var()`` in stylesheets, ``style`` attributes and presentation attributes."""
    defs: dict[str, str] = {}
    style_elements = [
        el for el in root.iter() if isinstance(el.tag, str) and _local(el.tag) == "style"
    ]
    for el in style_elements:
        for node in tinycss2.parse_stylesheet(
            el.text or "", skip_comments=True, skip_whitespace=True
        ):
            if node.type != "qualified-rule":
                continue
            selectors = {s.strip() for s in tinycss2.serialize(node.prelude).split(",")}
            if selectors & _GLOBAL_SELECTORS:
                defs.update(_custom_properties(node.content))
    if style := root.get("style"):
        defs.update(_custom_properties(style))
    unresolved: set[str] = set()

    def sub(text: str) -> str:
        for _ in range(8):

            def repl(m: re.Match[str]) -> str:
                name, fallback = m.group(1), m.group(2)
                if name in defs:
                    return defs[name]
                if fallback is not None:
                    return fallback.strip()
                unresolved.add(name)
                return m.group(0)

            new = _VAR_RE.sub(repl, text)
            if new == text:
                break
            text = new
        return text

    defs = {k: sub(v) for k, v in defs.items()}
    for el in style_elements:
        if el.text and "var(" in el.text:
            el.text = sub(el.text)
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        for attr, value in list(el.attrib.items()):
            if "var(" in value:
                el.set(attr, sub(value))
    if unresolved:
        warnings.append(
            "unresolved CSS variable(s) without fallback: " + ", ".join(sorted(unresolved))
        )


def _custom_properties(source) -> dict[str, str]:
    out: dict[str, str] = {}
    for decl in tinycss2.parse_blocks_contents(source, skip_comments=True, skip_whitespace=True):
        if decl.type == "declaration" and decl.name.startswith("--"):
            out[decl.name] = tinycss2.serialize(decl.value).strip()
    return out


def _viewbox(root: Element, warnings: list[str]) -> ViewBox:
    raw = root.get("viewBox")
    if raw:
        try:
            x, y, w, h = (float(p) for p in re.split(r"[\s,]+", raw.strip()))
        except ValueError:
            raise SvgError(f"invalid viewBox {raw!r}") from None
        if w <= 0 or h <= 0:
            raise SvgError(f"viewBox has no area: {raw!r}")
        return ViewBox(x, y, w, h)
    w, h = _length(root.get("width")), _length(root.get("height"))
    if w and h:
        return ViewBox(0, 0, w, h)
    warnings.append("no viewBox and no usable width/height; assuming 100x100")
    return ViewBox(0, 0, 100, 100)


def _length(value: str | None) -> float | None:
    if not value:
        return None
    m = re.fullmatch(r"\s*([+-]?\d*\.?\d+(?:[eE][+-]?\d+)?)\s*([a-z]*)\s*", value)
    if not m or m.group(2) not in _UNIT_TO_PX:
        return None
    n = float(m.group(1)) * _UNIT_TO_PX[m.group(2)]
    return n if n > 0 else None


# -- paint walking -------------------------------------------------------------------
def _walk_paints(root: Element, fn: Rewrite, *, write: bool = True) -> None:
    """Call ``fn(property, value)`` for every colour property; apply replacements if *write*."""
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        if _local(el.tag) == "style":
            if el.text:
                new = _rewrite_stylesheet(el.text, fn)
                if write:
                    el.text = new
            continue
        for prop in COLOR_PROPS:
            value = el.get(prop)
            if value is not None:
                replacement = fn(prop, value.strip())
                if write and replacement is not None:
                    el.set(prop, replacement)
        style = el.get("style")
        if style:
            new = _rewrite_declarations(style, fn)
            if write:
                el.set("style", new)


def _rewrite_declarations(source, fn: Rewrite) -> str:
    parts: list[str] = []
    for node in tinycss2.parse_blocks_contents(source, skip_comments=True, skip_whitespace=True):
        if node.type == "declaration":
            value = tinycss2.serialize(node.value).strip()
            if node.lower_name in COLOR_PROPS:
                value = fn(node.lower_name, value) or value
            bang = " !important" if node.important else ""
            parts.append(f"{node.name}:{value}{bang}")
        elif node.type != "error":
            parts.append(node.serialize())
    return ";".join(parts)


def _rewrite_stylesheet(css: str, fn: Rewrite) -> str:
    out: list[str] = []
    for node in tinycss2.parse_stylesheet(css, skip_comments=True, skip_whitespace=True):
        if node.type == "qualified-rule":
            selector = tinycss2.serialize(node.prelude).strip()
            out.append(f"{selector}{{{_rewrite_declarations(node.content, fn)}}}")
        elif node.type == "at-rule" and node.lower_at_keyword == "import":
            continue  # never fetch external stylesheets
        elif node.type != "error":
            out.append(node.serialize())
    return "\n".join(out)


# -- recolouring ---------------------------------------------------------------------
def _css_color(target: Color, source_alpha: float = 1.0) -> str:
    alpha = target.a * source_alpha
    if alpha >= 0.999:
        return f"#{target.r:02x}{target.g:02x}{target.b:02x}"
    return f"rgba({target.r},{target.g},{target.b},{alpha:.3f})"


def _apply_color_map(root: Element, color_map: Mapping[str, Color]) -> None:
    normalised = {_normalise_key(k): v for k, v in color_map.items()}

    def mapper(_prop: str, value: str) -> str | None:
        if is_current_color(value):
            return None  # handled through the root ``color`` below
        parsed = Color.try_parse(value)
        if parsed is None or parsed.a == 0:
            return None
        target = normalised.get(parsed.opaque.hex)
        return _css_color(target, parsed.a) if target else None

    _walk_paints(root, mapper)
    target = normalised.get("currentcolor")
    if target is not None:
        existing = root.get("style") or ""
        kept = [
            part
            for part in existing.split(";")
            if part.strip() and not part.strip().lower().startswith("color")
        ]
        kept.append(f"color:{_css_color(target)}")
        root.set("style", ";".join(kept))


def _normalise_key(key: str) -> str:
    if is_current_color(key):
        return "currentcolor"
    return Color.parse(key).opaque.hex
