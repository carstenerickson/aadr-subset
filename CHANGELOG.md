# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added (Day 1 — project skeleton + validate subcommand)

- Project skeleton: `pyproject.toml`, README, LICENSE (MIT), `.gitignore`, `.python-version`.
- Package layout under `src/aadr_subset/`: `__init__.py`, `__main__.py`, `py.typed`, `cli.py`, `types.py`, `errors.py`, `selector.py`, `schemas/selector.schema.json`, `commands/validate_cmd.py`.
- `aadr-subset validate SELECTOR.yaml` subcommand end-to-end: loads selector via `ruamel.yaml` (line/col preserved), validates against the in-package Draft 2020-12 JSON Schema, runs semantic-constraint checks, accumulates all errors in one pass, exits 4 on any violation with `{file}:{line}:{col}: at {pointer}: {message}` per HLD §JSON-schema error message format.
- `aadr-subset --version` reports `aadr-subset {VERSION}` (and `aadr-resolve {VERSION}` once aadr-resolve is wired in on Day 2).
- Selector grammar surface (per HLD §Selector grammar + LLD §2.3): `populations`, `individual_ids`, `individual_ids_source`, `source_version`, `resolve_to_version`, `modern_only`, `min_coverage`, `coverage_column`, `date`, `any`, `exclude`. Deprecated aliases `master_ids` / `master_ids_source` accepted with stderr WARNING + ValidationError captured for JSON capture (used by `select` Day 3+).
- Exception hierarchy in `errors.py`: `AadrSubsetError` base + `SoftValidationFailure` / `IOFailure` / `InvariantViolation` / `UsageError` mapping to exits 1/2/3/4. `ValidationError` value-type for accumulated error reporting.
- GitHub Actions CI workflow: matrix Python 3.11/3.12/3.13 × Ubuntu/macOS. ruff lint + format-check + mypy strict + pytest with coverage gate (90% on src/aadr_subset/ excluding cli.py and __main__.py).

### Tests (Day 1)

- Unit tests for `load_selector`, `validate_schema`, `check_semantic_constraints`, deprecated-alias handling, file format edge cases.
- `aadr-subset validate` integration smoke (subprocess + exit code).

### Deferred to later days

- `aadr-resolve` library imports (Day 2): `AnnoFrame.from_path`, `resolve_master_ids`, `CollisionDetected`.
- `select` / `inspect` / `report` / `template` subcommands (Days 3-8).
- Cross-version resolution flow + `--source-anno` / `--mid-bridge` (Day 6).
- Templates (Day 8).
- PyPI publish workflow (Day 12).
