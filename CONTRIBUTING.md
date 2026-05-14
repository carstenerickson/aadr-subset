# Contributing to aadr-subset

New to the codebase? Read [DEVELOPMENT.md](DEVELOPMENT.md) first — it
covers the mental model, module map, execution pipeline, and "where to
add things" recipes. This file covers process: setup, lint, tests, PR
rules, release.

## Development setup

```bash
git clone https://github.com/carstenerickson/aadr-subset.git
cd aadr-subset
pip install -e ".[dev]"
aadr-subset --version    # sanity-check the editable install
```

## Running the test suite

```bash
pytest                        # all tests
pytest --cov=aadr_subset      # with coverage report
pytest tests/unit/            # unit tests only
pytest tests/integration/     # integration tests only
```

## Linting and type checking

```bash
ruff check src tests          # linter (E, F, W, I, UP, B, RUF)
ruff format --check src tests # formatter check
mypy --strict src             # type checker
```

All three must pass cleanly before opening a PR. CI runs them on
Python 3.11, 3.12, and 3.13 across Ubuntu and macOS.

## Source layout

```
src/aadr_subset/
  __main__.py     # `python -m aadr_subset` entry-point
  cli.py          # click entry-point; wires flags → command modules
  commands/       # one module per subcommand (select, inspect, report, diff, …)
  api.py          # library API entry point — select() for programmatic use (v0.4+)
  _cmd_helpers.py # shared validation helpers used by commands/ and api.py (v0.4+)
  engine.py       # core selection + sampling algorithm
  selector.py     # YAML → Selector dataclass + signature computation
  types.py        # shared dataclasses (Selector, SubsetResult, SamplingSpec, …)
  formats.py      # output writers (ids / tsv / json)
  reporting.py    # inspect / report / diff human-readable formatting
  schemas/        # selector.schema.json (JSON Schema for selector YAML)
  templates/      # shipped starter selectors (*.yaml)
  templates.py    # loader / lister for the templates/ directory
  errors.py       # typed exception hierarchy + EXIT_* constants
tests/
  unit/           # fast, no filesystem; FakeAnnoFrame fixtures
  integration/    # real .anno fixture files; slower
  fixtures/       # shared .anno and .yaml test files
```

## Pull request guidelines

- One logical change per PR; keep diffs reviewable.
- New engine behaviour needs unit tests in `tests/unit/` and, where
  applicable, an integration test.
- Selector grammar additions require a JSON Schema update in
  `schemas/selector.schema.json` and a `tested_against:` bump in any
  affected shipped templates.
- Update `CHANGELOG.md` under the `[Unreleased]` section for every
  user-visible change. (If that section doesn't exist — e.g. the
  previous version was just released — add it back at the top.)
- The selector signature is a public contract — changes that alter the
  canonical form for existing selectors are breaking and need a major
  version bump discussion.

## Release process

Releases are cut from `main` by the maintainer:

1. Bump `version` in `pyproject.toml` and `src/aadr_subset/__init__.py`
   (drop `.devN` suffix).
2. Replace `[Unreleased]` with `[X.Y.Z] — YYYY-MM-DD` in `CHANGELOG.md`.
3. Commit, push, tag: `git tag -a vX.Y.Z -m "aadr-subset vX.Y.Z"`.
4. Push the tag — the release workflow builds the sdist + wheel,
   smoke-tests across 6 jobs (Python 3.11 / 3.12 / 3.13 × Ubuntu /
   macOS), and publishes to PyPI via trusted-publisher OIDC.
