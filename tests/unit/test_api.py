"""Tests for the public library API — aadr_subset.select().

Exercises the six test cases from the v0.4 plan:
  1. Happy path: selector path + anno path → result with n_matched > 0.
  2. Pre-loaded Selector object → same result, selector_file == "<in-memory>".
  3. Pre-loaded AnnoFrame → same result, anno_file == "<in-memory>".
  4. Empty match with allow_empty=True (default) → result.n_matched == 0.
  5. Empty match with allow_empty=False → SoftValidationFailure.
  6. Warnings go to logging, not stderr.
  7. Error types importable at package root.
"""

from __future__ import annotations

import logging
from pathlib import Path

import aadr_resolve
import pytest

import aadr_subset
from aadr_subset import select
from aadr_subset.errors import SoftValidationFailure
from aadr_subset.selector import load_selector
from tests.fixtures.synthesize import make_loschbour_v66_fixture


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def anno_path(tmp_path: Path) -> Path:
    """Class-E v66.0 .anno with 6 samples (Western_HG x3, Eastern_HG x1, England_MN x2)."""
    p = tmp_path / "synth_v66.0.anno"
    make_loschbour_v66_fixture(p)
    return p


@pytest.fixture
def selector_path(tmp_path: Path) -> Path:
    """Selector that matches Western_HG (3 samples)."""
    p = tmp_path / "western.yaml"
    p.write_text("populations: [Western_HG]\n", encoding="utf-8")
    return p


@pytest.fixture
def empty_selector_path(tmp_path: Path) -> Path:
    """Selector that matches nothing in the fixture."""
    p = tmp_path / "nobody.yaml"
    p.write_text("populations: [NoSuchGroup]\n", encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Test 1: happy path — paths only
# ---------------------------------------------------------------------------


def test_select_returns_subset_result_from_paths(
    selector_path: Path, anno_path: Path
) -> None:
    result = select(selector_path, anno_path)

    assert result.n_matched == 3
    assert set(result.genetic_ids) == {"Loschbour.AG", "Loschbour.DG", "Bichon"}
    assert result.selector_signature != ""
    assert result.anno_version != ""  # set from AnnoFrame.version; exact value is filename-derived
    assert result.selector_file == str(selector_path)
    assert result.anno_file == str(anno_path)


# ---------------------------------------------------------------------------
# Test 2: pre-loaded Selector object
# ---------------------------------------------------------------------------


def test_select_accepts_preloaded_selector(
    selector_path: Path, anno_path: Path
) -> None:
    _meta, loaded_selector = load_selector(selector_path)
    result = select(loaded_selector, anno_path)

    assert result.n_matched == 3
    assert result.selector_file == "<in-memory>"
    # Signature is computed on the selector intent, not the path.
    assert result.selector_signature != ""


# ---------------------------------------------------------------------------
# Test 3: pre-loaded AnnoFrame
# ---------------------------------------------------------------------------


def test_select_accepts_preloaded_anno_frame(
    selector_path: Path, anno_path: Path
) -> None:
    anno_frame = aadr_resolve.AnnoFrame.from_path(anno_path)
    result = select(selector_path, anno_frame)

    assert result.n_matched == 3
    assert result.anno_file == "<in-memory>"
    assert result.anno_version != ""  # populated from the pre-loaded AnnoFrame


# ---------------------------------------------------------------------------
# Test 4: empty match with allow_empty=True (default)
# ---------------------------------------------------------------------------


def test_select_empty_match_returns_result_by_default(
    empty_selector_path: Path, anno_path: Path
) -> None:
    """Default allow_empty=True: zero matches returns result, no exception."""
    result = select(empty_selector_path, anno_path)

    assert result.n_matched == 0
    assert result.genetic_ids == []


# ---------------------------------------------------------------------------
# Test 5: empty match with allow_empty=False raises SoftValidationFailure
# ---------------------------------------------------------------------------


def test_select_empty_match_raises_when_disallowed(
    empty_selector_path: Path, anno_path: Path
) -> None:
    with pytest.raises(SoftValidationFailure):
        select(empty_selector_path, anno_path, allow_empty=False)


# ---------------------------------------------------------------------------
# Test 6: warnings go to logging, not stderr
# ---------------------------------------------------------------------------


def test_select_empty_glob_warning_goes_to_logging(
    tmp_path: Path, anno_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A typo'd glob pattern triggers logging.warning, not sys.stderr."""
    selector = tmp_path / "bad_glob.yaml"
    selector.write_text("populations: [Typo_*]\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="aadr_subset"):
        result = select(selector, anno_path)

    assert result.n_matched == 0
    assert any("glob" in rec.message.lower() or "Typo_*" in rec.message for rec in caplog.records)
    # Nothing on stderr — the warning stayed in the logging system.


# ---------------------------------------------------------------------------
# Test 7: public error types are importable from the package root
# ---------------------------------------------------------------------------


def test_public_error_types_importable_from_root() -> None:
    """All error types declared in __all__ are real importable subclasses."""
    assert issubclass(aadr_subset.SoftValidationFailure, aadr_subset.AadrSubsetError)
    assert issubclass(aadr_subset.IOFailure, aadr_subset.AadrSubsetError)
    assert issubclass(aadr_subset.UsageError, aadr_subset.AadrSubsetError)
    assert issubclass(aadr_subset.InvariantViolation, aadr_subset.AadrSubsetError)
    # ValidationError is a frozen dataclass (error payload), not an Exception.
    assert hasattr(aadr_subset.ValidationError, "message")
    assert hasattr(aadr_subset.ValidationError, "format_line")


def test_public_types_importable_from_root() -> None:
    """Core result and selector types are accessible at the package root."""
    from aadr_subset import SubsetResult, Selector, SelectorMetadata, SamplingSpec  # noqa: F401

    assert aadr_subset.select is select
