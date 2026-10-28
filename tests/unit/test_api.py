"""Tests for the public library API — aadr_subset.select().

Exercises the six test cases from the v0.4 plan:
  1. Happy path: selector path + anno path → result with n_matched > 0.
  2. Pre-loaded Selector object → same result, selector_file == "<in-memory>".
  3. Pre-loaded AnnoFrame → same result, anno_file == "<in-memory>".
  4. Empty match with allow_empty=True (default) → result.n_matched == 0.
  5. Empty match with allow_empty=False → SoftValidationFailure.
  6. Warnings go to logging, not stderr.
  7. Error types importable at package root.
  8. Unreadable / unrecognised target .anno → IOFailure.
  9. Cross-version lift with a pre-loaded source AnnoFrame + its version checks.
 10. Unresolved-IID and class-D coverage warnings go to logging.
"""

from __future__ import annotations

import logging
from pathlib import Path

import aadr_resolve
import pytest

import aadr_subset
from aadr_subset import select
from aadr_subset.errors import IOFailure, SoftValidationFailure, UsageError
from aadr_subset.selector import load_selector
from tests.fixtures.synthesize import make_loschbour_v66_fixture, make_v62_class_d_fixture

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


def test_select_returns_subset_result_from_paths(selector_path: Path, anno_path: Path) -> None:
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


def test_select_accepts_preloaded_selector(selector_path: Path, anno_path: Path) -> None:
    _meta, loaded_selector = load_selector(selector_path)
    result = select(loaded_selector, anno_path)

    assert result.n_matched == 3
    assert result.selector_file == "<in-memory>"
    # Signature is computed on the selector intent, not the path.
    assert result.selector_signature != ""


# ---------------------------------------------------------------------------
# Test 3: pre-loaded AnnoFrame
# ---------------------------------------------------------------------------


def test_select_accepts_preloaded_anno_frame(selector_path: Path, anno_path: Path) -> None:
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
    from aadr_subset import SamplingSpec, Selector, SelectorMetadata, SubsetResult  # noqa: F401

    assert aadr_subset.select is select


# ---------------------------------------------------------------------------
# Test 8: unreadable / unrecognised target .anno → IOFailure
# ---------------------------------------------------------------------------


def test_select_missing_anno_raises_io_failure(selector_path: Path, tmp_path: Path) -> None:
    with pytest.raises(IOFailure, match="cannot load"):
        select(selector_path, tmp_path / "missing.anno")


def test_select_unrecognised_anno_schema_raises_io_failure(
    selector_path: Path, tmp_path: Path
) -> None:
    bad = tmp_path / "bad.anno"
    bad.write_text("foo\tbar\n1\t2\n", encoding="utf-8")
    with pytest.raises(IOFailure, match="schema unrecognised"):
        select(selector_path, bad)


# ---------------------------------------------------------------------------
# Test 9: cross-version lift with a pre-loaded source AnnoFrame
# ---------------------------------------------------------------------------

_LIFT = "individual_ids: [Loschbour]\nsource_version: v62.0\nresolve_to_version: v66.0\n"


@pytest.fixture
def v62_v66_frames(tmp_path: Path) -> tuple[aadr_resolve.AnnoFrame, aadr_resolve.AnnoFrame]:
    """Pre-loaded v62.0 (class D) source + v66.0 (class E) target AnnoFrames."""
    src = make_v62_class_d_fixture(tmp_path / "v62.0.anno")
    dst = make_loschbour_v66_fixture(tmp_path / "v66.0.anno")
    return (
        aadr_resolve.AnnoFrame.from_path(src, version_label="v62.0"),
        aadr_resolve.AnnoFrame.from_path(dst, version_label="v66.0"),
    )


def _write_selector(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "lift.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_select_lifts_iids_with_preloaded_source_anno(
    tmp_path: Path, v62_v66_frames: tuple[aadr_resolve.AnnoFrame, aadr_resolve.AnnoFrame]
) -> None:
    source, target = v62_v66_frames
    result = select(_write_selector(tmp_path, _LIFT), target, source_anno=source)

    assert sorted(result.genetic_ids) == ["Loschbour.AG", "Loschbour.DG"]
    assert result.warnings.missing_after_resolve == []


def test_select_preloaded_source_requires_resolve_to_version(
    selector_path: Path,
    v62_v66_frames: tuple[aadr_resolve.AnnoFrame, aadr_resolve.AnnoFrame],
) -> None:
    source, target = v62_v66_frames
    with pytest.raises(UsageError) as excinfo:
        select(selector_path, target, source_anno=source)
    assert [e.pointer for e in excinfo.value.errors] == ["/source_anno"]


def test_select_preloaded_source_version_mismatch(
    tmp_path: Path, v62_v66_frames: tuple[aadr_resolve.AnnoFrame, aadr_resolve.AnnoFrame]
) -> None:
    """Selector says the IDs come from v54.1, but the source frame is v62.0."""
    source, target = v62_v66_frames
    selector = _write_selector(
        tmp_path,
        "individual_ids: [Loschbour]\nsource_version: v54.1\nresolve_to_version: v66.0\n",
    )
    with pytest.raises(UsageError) as excinfo:
        select(selector, target, source_anno=source)
    err = excinfo.value.errors[0]
    assert err.pointer == "/source_anno"
    assert "'v62.0'" in err.message


def test_select_preloaded_target_version_mismatch(
    tmp_path: Path, v62_v66_frames: tuple[aadr_resolve.AnnoFrame, aadr_resolve.AnnoFrame]
) -> None:
    """Passing the v62.0 frame as target contradicts resolve_to_version: v66.0."""
    source, _target = v62_v66_frames
    with pytest.raises(UsageError) as excinfo:
        select(_write_selector(tmp_path, _LIFT), source, source_anno=source)
    assert [e.pointer for e in excinfo.value.errors] == ["/resolve_to_version"]


# ---------------------------------------------------------------------------
# Test 10: unresolved-IID and class-D coverage warnings go to logging
# ---------------------------------------------------------------------------


def test_select_unresolved_iid_warning_goes_to_logging(
    tmp_path: Path,
    v62_v66_frames: tuple[aadr_resolve.AnnoFrame, aadr_resolve.AnnoFrame],
    caplog: pytest.LogCaptureFixture,
) -> None:
    source, target = v62_v66_frames
    selector = _write_selector(
        tmp_path,
        "individual_ids: [Loschbour, NotPresent]\nsource_version: v62.0\n"
        "resolve_to_version: v66.0\n",
    )

    with caplog.at_level(logging.WARNING, logger="aadr_subset"):
        result = select(selector, target, source_anno=source)

    assert result.warnings.missing_after_resolve == ["NotPresent"]
    assert any(
        "failed to resolve" in rec.message and "NotPresent" in rec.message for rec in caplog.records
    )


@pytest.mark.parametrize(
    ("selector_body", "expect_warning"),
    [
        ("populations: [Western_HG]\nmin_coverage: 0.5\n", True),
        ("populations: [Western_HG]\n", False),
    ],
)
def test_select_class_d_min_coverage_warning(
    tmp_path: Path,
    v62_v66_frames: tuple[aadr_resolve.AnnoFrame, aadr_resolve.AnnoFrame],
    caplog: pytest.LogCaptureFixture,
    selector_body: str,
    expect_warning: bool,
) -> None:
    """Class D (v62.0) has no native coverage column, so min_coverage selects
    nothing; select() logs why instead of returning an unexplained empty result."""
    class_d, _target = v62_v66_frames
    selector = tmp_path / "s.yaml"
    selector.write_text(selector_body, encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="aadr_subset"):
        result = select(selector, class_d)

    warned = any("no native coverage column" in rec.message for rec in caplog.records)
    assert warned is expect_warning
    if expect_warning:
        assert result.n_matched == 0
