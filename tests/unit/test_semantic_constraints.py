"""Unit tests for semantic-constraint check.

Validations that the JSON schema can't express:
- date.min_calbp <= date.max_calbp
- source_version != resolve_to_version
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aadr_subset.errors import UsageError
from aadr_subset.selector import load_selector


def write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_date_range_inverted_rejected(selector_dir: Path) -> None:
    """date.min_calbp > date.max_calbp → semantic-constraint violation."""
    content = "date:\n  min_calbp: 2800\n  max_calbp: 2200\n"
    path = write(selector_dir / "inverted.yaml", content)
    with pytest.raises(UsageError) as excinfo:
        load_selector(path)
    errs = excinfo.value.errors
    assert any(e.constraint == "date_range_inverted" for e in errs)
    # Message includes both values for debuggability.
    assert any("2800" in e.message and "2200" in e.message for e in errs)


def test_source_equals_resolve_to_rejected(selector_dir: Path) -> None:
    """source_version == resolve_to_version → cross_version_self_reference."""
    content = "individual_ids: [Loschbour]\nsource_version: v66.0\nresolve_to_version: v66.0\n"
    path = write(selector_dir / "self_ref.yaml", content)
    with pytest.raises(UsageError) as excinfo:
        load_selector(path)
    errs = excinfo.value.errors
    assert any(e.constraint == "cross_version_self_reference" for e in errs)


def test_semantic_check_skipped_when_schema_caught(selector_dir: Path) -> None:
    """If schema already flagged /date/max_calbp (e.g., wrong type),
    semantic check for date_range_inverted does NOT fire (would be
    a double-error)."""
    content = "date:\n  min_calbp: 2800\n  max_calbp: 'not_a_number'\n"
    path = write(selector_dir / "wrong_type.yaml", content)
    with pytest.raises(UsageError) as excinfo:
        load_selector(path)
    errs = excinfo.value.errors
    # Schema should flag the type mismatch.
    assert any("/date/max_calbp" in e.pointer for e in errs)
    # date_range_inverted should NOT fire (precondition failed at schema).
    assert not any(e.constraint == "date_range_inverted" for e in errs)


def test_validate_collects_all_errors(selector_dir: Path) -> None:
    """In collect_all_errors=True mode, multiple violations surface together."""
    content = """populations: [42]
date:
  min_calbp: 2800
  max_calbp: 2200
min_coverage: -0.5
"""
    path = write(selector_dir / "multi.yaml", content)
    with pytest.raises(UsageError) as excinfo:
        load_selector(path, collect_all_errors=True)
    errs = excinfo.value.errors
    # Should see at least the populations[0] type error, min_coverage min,
    # and the date_range_inverted semantic constraint.
    assert len(errs) >= 3
    pointers = {e.pointer for e in errs}
    assert any("/populations" in p for p in pointers)
    assert any("/min_coverage" in p for p in pointers)
    # date_range_inverted fires since both date fields were valid integers.
    assert any(e.constraint == "date_range_inverted" for e in errs)
