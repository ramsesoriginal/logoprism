# Contributing

Thanks for helping. The short version:

```bash
uv sync
uv run pytest
uv run ruff format . && uv run ruff check . --fix
uv run ty check src
```

All four must pass; CI runs them on Linux, macOS and Windows.

## Layout

| Path | What lives there |
|---|---|
| `src/logoprism/core/` | Pure building blocks, no terminal I/O: `svg` (parsing, normalising, recolouring), `color`, `geometry` (ink measurement, layouts), `sizes`, `recipes` (variants), `plan`, `engine` (parallel, incremental), `render`, `compose`, `png` |
| `src/logoprism/config/` | The `Settings` model (`models.py` drives config files, env vars, CLI and the JSON Schema), layered loading with provenance, validation, templates |
| `src/logoprism/ui/` | Rich views, console setup, the interactive wizard |
| `src/logoprism/workflow.py`, `cli.py` | Orchestration and command line |
| `scripts/record_demo.py` | Records the README GIFs from real runs |

## Guidelines

- **A new setting** goes into `config/models.py` first (with an attribute docstring, which becomes its
  schema description), then validation if needed, then a CLI flag in `cli.py`. Regenerate the schema:
  `uv run logoprism config schema > docs/logoprism.schema.json`. A test fails if it is stale.
- **Keep `core/` free of terminal I/O**, and keep imports of heavy modules (Pillow, resvg) lazy so
  `logoprism --help` stays fast.
- **Test fixtures are synthetic SVGs** written inline (see `tests/conftest.py`) plus the project's
  own `docs/assets/logoprism.svg`. Do not commit third-party or private logos.
- Output must stay deterministic: no timestamps, no unordered iteration into files.
- Update `CHANGELOG.md` under *Unreleased*.

## Releasing

1. Move the *Unreleased* notes under a new version heading in `CHANGELOG.md`; bump `version` in
   `pyproject.toml` and `src/logoprism/__init__.py`.
2. Tag `vX.Y.Z` and push the tag. The `release` workflow builds the distributions and publishes
   them to PyPI through trusted publishing (no API token involved).
