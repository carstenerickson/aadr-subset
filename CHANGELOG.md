# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added (Day 2 — aadr-resolve library integration + basic select)

- `aadr-subset select SELECTOR ANNO [-o OUT]` subcommand end-to-end.
  Day-2 surface supports the simplest predicate path: `populations`
  and/or `individual_ids` matched against a single target `.anno`.
  Output via `--format=ids` (default) writes a newline-delimited
  GeneticID list to stdout or `-o PATH`. Atomic write via tempfile +
  `os.rename` + advisory `fcntl.flock` on `{PATH}.lock` (LOCK_NB; one
  writer wins fast).
- `--schema-override CLASS` flag (A|B|C|D|E) forwarded to
  `AnnoFrame.from_path(schema_override=)` for renamed `.anno` files
  matching an existing class signature.
- `--allow-empty` flag — downgrades zero-match exit 1 to exit 0
  (writes an empty output file; CI sentinel).
- `--allow-empty-source` flag — allows `individual_ids_source:`
  to be empty without raising `SoftValidationFailure`.
- `--include-matched-criteria` flag — opt-in for JSON output's
  matched_criteria field (declared; JSON format lands Day 4).
- `engine.select_samples`: vectorized pandas filter pipeline over
  `populations` and `individual_ids` predicates. Multi-row IIDs
  (multi-library individuals like Loschbour with `.AG` + `.DG`)
  naturally produce multiple GeneticID rows in the output, matching
  HLD §within-version multi-row IIDs are normal.
- Feature gate in engine: Day-3+ selector features (`any:`, `exclude:`,
  `date:`, `modern_only:`, `min_coverage:`, `coverage_column:`,
  `resolve_to_version:`) produce `UsageError` with
  `constraint="feature_not_implemented"` until their day lands. Note:
  validate accepts these at the grammar level since the spec is
  well-formed; engine refuses to execute them.
- `formats.atomic_write` + `formats.write_ids`: ships now since the
  output-atomicity contract should hold from Day 2 forward; the same
  code path will carry TSV/JSON writers on Day 4.
- aadr-resolve library wired in: top-level handler maps
  `aadr_resolve.SchemaDetectionError` to `IOFailure` (exit 2);
  `aadr_resolve.IOFailure` also caught.
- Test fixture synthesizer: `tests/fixtures/synthesize.py` builds
  class-E (.v66.0) `.anno` files on the fly from aadr-resolve's
  shipped `class_E.yaml`. Used by select-CLI integration tests to
  exercise the real `AnnoFrame.from_path` parse path with a known
  6-sample fixture (Loschbour x2 + Bichon + KO1 + English x2).

### Tests (Day 2)

- 31 new tests (74 total): engine unit tests with `FakeAnnoFrame`
  mock, atomic-write concurrency test, select-CLI integration tests
  via subprocess against the synthetic v66 fixture.
- HLD test 1 (empty selector matches all) — covered.
- HLD test 2 (single-population selector) — covered.
- HLD test 3 (flat AND) — implicitly covered via populations + IIDs.
- HLD test 16 (multi-GID per IID handling) — single-version variant
  covered; cross-version variant Day 6.

### Deferred to later days

- Day 3: `any:` and `exclude:` combinators; `modern_only:`, `date:`,
  `min_coverage:` selectors.
- Day 4: inspect mode; TSV and JSON output formats.
- Day 6: cross-version (`resolve_to_version:` + `--source-anno`).
- Day 7: `selector_signature` (RFC 8785 JCS).
- Day 8: templates + template subcommand.

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
