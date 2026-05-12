"""Unit tests for JSON-schema validation path.

Covers HLD test 6 (nested any: rejected) and several Day-1 grammar
invariants. Uses functional tests that walk the load_selector entry
to exercise the full flow including error formatting.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aadr_subset.errors import UsageError
from aadr_subset.selector import load_selector


def write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def _err_messages(exc: UsageError) -> list[str]:
    return [e.message for e in exc.errors]


# --- Structural errors ---


def test_unknown_top_level_key_rejected(selector_dir: Path) -> None:
    """A top-level key not in the schema's properties list → ValidationError."""
    path = write(selector_dir / "unknown.yaml", "unknown_thing: 42\n")
    with pytest.raises(UsageError) as excinfo:
        load_selector(path)
    msgs = _err_messages(excinfo.value)
    assert any("Additional properties are not allowed" in m or "unknown_thing" in m for m in msgs)


def test_populations_must_be_array_of_strings(selector_dir: Path) -> None:
    """populations as array-of-int → ValidationError."""
    path = write(selector_dir / "wrong_type.yaml", "populations: [1, 2, 3]\n")
    with pytest.raises(UsageError) as excinfo:
        load_selector(path)
    msgs = _err_messages(excinfo.value)
    assert any("not of type 'string'" in m or "is not of type" in m for m in msgs)


def test_min_coverage_negative_rejected(selector_dir: Path) -> None:
    """min_coverage: -0.5 → ValidationError (minimum: 0)."""
    path = write(
        selector_dir / "neg_cov.yaml",
        "populations: [English.SG]\nmin_coverage: -0.5\n",
    )
    with pytest.raises(UsageError) as excinfo:
        load_selector(path)
    msgs = _err_messages(excinfo.value)
    assert any("less than the minimum" in m or "minimum" in m for m in msgs)


# --- HLD test 6: nested any: rejected ---


def test_nested_any_rejected(selector_dir: Path) -> None:
    """An any: branch containing another any: must fail schema validation
    (branch schema has additionalProperties: false; only top-level allows
    any/exclude)."""
    content = """any:
  - any:
      - populations: [A]
"""
    path = write(selector_dir / "nested.yaml", content)
    with pytest.raises(UsageError) as excinfo:
        load_selector(path)
    msgs = _err_messages(excinfo.value)
    # The schema produces "Additional properties are not allowed ('any' was unexpected)"
    assert any("any" in m for m in msgs)


# --- AADR version enum ---


def test_unknown_resolve_to_version_rejected(selector_dir: Path) -> None:
    """resolve_to_version: v99.0 → ValidationError (enum)."""
    content = "individual_ids: [Loschbour]\nsource_version: v44.3\nresolve_to_version: v99.0\n"
    path = write(selector_dir / "bad_version.yaml", content)
    with pytest.raises(UsageError) as excinfo:
        load_selector(path)
    msgs = _err_messages(excinfo.value)
    assert any("'v99.0'" in m for m in msgs)


# --- Cross-key constraint: resolve_to_version requires source_version ---


def test_resolve_without_source_version_rejected(selector_dir: Path) -> None:
    """resolve_to_version: set without source_version: → schema cross-key check fires."""
    content = "resolve_to_version: v66.0\nindividual_ids: [Loschbour]\n"
    path = write(selector_dir / "no_src_ver.yaml", content)
    with pytest.raises(UsageError) as excinfo:
        load_selector(path)
    # Schema enforces this via if/then.
    msgs = _err_messages(excinfo.value)
    assert any("source_version" in m.lower() for m in msgs)


# --- Date as empty object rejected ---


def test_date_empty_object_rejected(selector_dir: Path) -> None:
    """date: {} → ValidationError (date_range has minProperties: 1).
    jsonschema's message for this constraint is `'{} should be non-empty'`."""
    path = write(selector_dir / "empty_date.yaml", "date: {}\n")
    with pytest.raises(UsageError) as excinfo:
        load_selector(path)
    errs = excinfo.value.errors
    assert any("/date" in e.pointer for e in errs)
    assert any("non-empty" in e.message or "minProperties" in e.message for e in errs)


# --- Compound valid selector loads ---


def test_compound_selector_valid(selector_dir: Path) -> None:
    """populations + date range + exclude + min_coverage all together → OK."""
    content = """populations:
  - England_IA
  - England_IA.SG
date:
  min_calbp: 2200
  max_calbp: 2800
exclude:
  group_ids:
    - England_Saxon.SG
min_coverage: 0.2
"""
    path = write(selector_dir / "compound.yaml", content)
    _metadata, selector = load_selector(path)
    assert selector.populations == ["England_IA", "England_IA.SG"]
    assert selector.date is not None
    assert selector.date.min_calbp == 2200
    assert selector.date.max_calbp == 2800
    assert selector.min_coverage == 0.2
    assert selector.exclude is not None
    assert selector.exclude.group_ids == ["England_Saxon.SG"]


def test_any_block_valid(selector_dir: Path) -> None:
    """any: with three branches loads correctly."""
    content = """any:
  - populations: [Western_HG]
  - populations: [WHG]
  - individual_ids: [Loschbour, Bichon, KO1]
"""
    path = write(selector_dir / "any.yaml", content)
    _metadata, selector = load_selector(path)
    assert len(selector.any_branches) == 3
    assert selector.any_branches[0].populations == ["Western_HG"]
    assert selector.any_branches[2].individual_ids == ["Loschbour", "Bichon", "KO1"]
