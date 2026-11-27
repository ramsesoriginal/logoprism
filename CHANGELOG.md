# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0]

First release.

### Added

- `logoprism FILE.svg`: converts SVG logos into PNG sets, favicon (16 px) to A4 (2480 px at 300 dpi).
- Variants: black, white, original colours and greyscale on transparent, white, black and brand
  backgrounds; brand foreground / background, light / dark brand pair, custom colour maps (themes).
- Shapes: native aspect ratio, square, and circle-safe (fits the inscribed circle).
- SVG normalisation: hardened parsing, CSS `var()` resolution, removal of scripts and external references.
- Layered configuration (global, project, per-file, sidecar, environment, CLI) with provenance,
  `logoprism.toml` / `[tool.logoprism]`, and a published JSON Schema.
- Rich terminal UI, an interactive wizard, `inspect`, `sizes`, `init`, `config show|schema|path`.
- Automation: `--json`, `--check`, `--dry-run`, `--list`, `--strict`, exit codes, GitHub Actions
  annotations and job summary, incremental builds with a manifest, pre-commit hook.
- Parallel rendering, atomic and deterministic PNG output with `sRGB` and `pHYs` chunks.

[Unreleased]: https://github.com/ramsesoriginal/logoprism/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/ramsesoriginal/logoprism/releases/tag/v0.1.0
