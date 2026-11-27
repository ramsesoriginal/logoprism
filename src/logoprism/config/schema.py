"""JSON Schema for ``logoprism.toml`` (editor completion and validation)."""

from __future__ import annotations

import ast
import inspect
import itertools
import json
from typing import Any

import msgspec

from logoprism.config import models
from logoprism.config.models import Settings

SCHEMA_ID = (
    "https://raw.githubusercontent.com/ramsesoriginal/logoprism/main/docs/logoprism.schema.json"
)


def build_schema() -> dict[str, Any]:
    raw = msgspec.json.schema(Settings)
    defs: dict[str, Any] = raw["$defs"]
    settings_def = defs["Settings"]
    settings_def["properties"]["files"] = {
        "description": "Per-file overrides: a table per glob pattern, with the same keys as above.",
        "type": "object",
        "additionalProperties": {"$ref": "#/$defs/Settings"},
    }
    _describe(defs)
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": "LogoPrism configuration",
        "$ref": raw["$ref"],
        "$defs": defs,
    }
    return json.loads(json.dumps(schema))  # plain JSON types only (defaults are tuples)


def _describe(defs: dict[str, Any]) -> None:
    """Copy attribute docstrings (``name: type = x`` followed by a string) into the schema."""
    try:
        tree = ast.parse(inspect.getsource(models))
    except OSError, TypeError:  # pragma: no cover - source not available
        return
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name not in defs:
            continue
        properties = defs[node.name].get("properties", {})
        body = node.body
        for current, following in itertools.pairwise(body):
            if (
                isinstance(current, ast.AnnAssign)
                and isinstance(current.target, ast.Name)
                and isinstance(following, ast.Expr)
                and isinstance(following.value, ast.Constant)
                and isinstance(following.value.value, str)
            ):
                prop = current.target.id.replace("_", "-")
                if prop in properties:
                    properties[prop]["description"] = " ".join(following.value.value.split())
