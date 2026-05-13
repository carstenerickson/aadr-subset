"""Unit tests for reporting.format_stdout_summary + format_inspect_summary."""

from __future__ import annotations

from pathlib import Path

import aadr_resolve

from aadr_subset.reporting import format_inspect_summary, format_stdout_summary
from aadr_subset.types import (
    ExcludeCount,
    SubsetResult,
)
from tests.fixtures.synthesize import make_loschbour_v66_fixture


def _v66_anno(tmp_path: Path) -> aadr_resolve.AnnoFrame:
    p = tmp_path / "synth_v66.0.anno"
    make_loschbour_v66_fixture(p)
    return aadr_resolve.AnnoFrame.from_path(p, version_label="v66.0")


# --- format_stdout_summary ---


def test_summary_inline_form_under_threshold(tmp_path: Path) -> None:
    """<10 populations → inline `Per-population: A=N, B=M, ...` form."""
    anno = _v66_anno(tmp_path)
    result = SubsetResult(
        genetic_ids=["Loschbour.AG", "Loschbour.DG", "Bichon"],
        n_matched=3,
        per_population_counts={"Western_HG": 3},
        per_branch_counts={"top_level": 3},
    )
    summary = format_stdout_summary(
        result,
        parse_time=0.5,
        eval_time=0.1,
        write_time=0.01,
        out_path_str="out.ids",
        selector_file="sel.yaml",
        anno=anno,
    )
    assert "Per-population: Western_HG=3" in summary
    assert "Matched 3 samples across 1 population." in summary
    assert "Done in 0.61s" in summary
    assert "Wrote out.ids (3 lines)" in summary


def test_summary_columnar_form_at_threshold(tmp_path: Path) -> None:
    """≥10 populations → columnar `Per-population breakdown:` block."""
    anno = _v66_anno(tmp_path)
    per_pop = {f"Pop_{i}": 1 for i in range(15)}
    result = SubsetResult(
        genetic_ids=[f"g{i}" for i in range(15)],
        n_matched=15,
        per_population_counts=per_pop,
        per_branch_counts={"top_level": 15},
    )
    summary = format_stdout_summary(
        result,
        parse_time=0.1,
        eval_time=0.01,
        write_time=0.01,
        out_path_str=None,
        selector_file="sel.yaml",
        anno=anno,
    )
    assert "Per-population breakdown:" in summary
    assert "Per-population:" not in summary  # NOT the inline form
    # Each population on its own line with right-aligned count.
    for i in range(15):
        assert f"Pop_{i}" in summary


def test_summary_excluded_line(tmp_path: Path) -> None:
    """Excluded samples produce the 'Excluded …' line."""
    anno = _v66_anno(tmp_path)
    result = SubsetResult(
        genetic_ids=["Loschbour.AG"],
        n_matched=1,
        per_population_counts={"Western_HG": 1},
        per_branch_counts={"top_level": 1},
        excluded_counts=[ExcludeCount(key="group_ids", value="Eastern_HG", count=2)],
    )
    summary = format_stdout_summary(
        result,
        parse_time=0.1,
        eval_time=0.01,
        write_time=0.01,
        out_path_str=None,
        selector_file="sel.yaml",
        anno=anno,
    )
    assert "Excluded 2 samples via 1 exclusion condition." in summary


def test_summary_signature_short_form(tmp_path: Path) -> None:
    """Selector signature in the header line uses the short `sha256:abc...def` form."""
    anno = _v66_anno(tmp_path)
    sig = "sha256:" + "a" * 64
    result = SubsetResult(
        genetic_ids=["g"],
        n_matched=1,
        selector_signature=sig,
    )
    summary = format_stdout_summary(
        result,
        parse_time=0.1,
        eval_time=0.01,
        write_time=0.01,
        out_path_str=None,
        selector_file="sel.yaml",
        anno=anno,
    )
    # Header includes (sha256:aaaaaaa...aaaaaaa) short form.
    assert "sha256:aaaaaaa...aaaaaaa" in summary


def test_summary_no_signature_line_when_empty(tmp_path: Path) -> None:
    """selector_signature empty (Day-4 pre-signature) → no signature tail."""
    anno = _v66_anno(tmp_path)
    result = SubsetResult(genetic_ids=["g"], n_matched=1)
    summary = format_stdout_summary(
        result,
        parse_time=0.1,
        eval_time=0.01,
        write_time=0.01,
        out_path_str=None,
        selector_file="sel.yaml",
        anno=anno,
    )
    assert "sha256:" not in summary


# --- format_inspect_summary ---


def test_inspect_summary_has_per_population(tmp_path: Path) -> None:
    """Inspect summary includes the per-population breakdown."""
    anno = _v66_anno(tmp_path)
    result = SubsetResult(
        genetic_ids=["Loschbour.AG", "Loschbour.DG", "Bichon"],
        n_matched=3,
        per_population_counts={"Western_HG": 3},
        per_branch_counts={"top_level": 3},
        selector_file="iron_age.yaml",
    )
    summary = format_inspect_summary(result, anno)
    assert "Per-population breakdown:" in summary
    assert "Western_HG" in summary
    assert "Branch contributions:" in summary
    assert "top_level" in summary


def test_inspect_summary_excluded_block(tmp_path: Path) -> None:
    """Excluded conditions get their own block."""
    anno = _v66_anno(tmp_path)
    result = SubsetResult(
        genetic_ids=["Loschbour.AG"],
        n_matched=1,
        per_population_counts={"Western_HG": 1},
        per_branch_counts={"top_level": 1},
        excluded_counts=[
            ExcludeCount(key="group_ids", value="Eastern_HG", count=2),
        ],
    )
    summary = format_inspect_summary(result, anno)
    assert "Excluded:" in summary
    assert "group_ids: Eastern_HG    2 samples dropped" in summary


def test_inspect_summary_date_and_coverage_ranges(tmp_path: Path) -> None:
    """Date range + coverage range computed from matched rows."""
    anno = _v66_anno(tmp_path)
    # 4 Western_HG + Eastern_HG samples (date 7700-13700, coverage 0.78-2.40).
    result = SubsetResult(
        genetic_ids=["Loschbour.AG", "Loschbour.DG", "Bichon", "KO1"],
        n_matched=4,
        per_population_counts={"Western_HG": 3, "Eastern_HG": 1},
        per_branch_counts={"top_level": 4},
    )
    summary = format_inspect_summary(result, anno)
    assert "Date range of matched: 7700 - 13700 calBP" in summary
    assert "Coverage range:" in summary
    assert "0.78" in summary
    assert "2.40" in summary


def test_inspect_summary_zero_matches(tmp_path: Path) -> None:
    """Zero matches → no per-population block, no date/coverage lines."""
    anno = _v66_anno(tmp_path)
    result = SubsetResult(genetic_ids=[], n_matched=0)
    summary = format_inspect_summary(result, anno)
    assert "Matched: 0 samples across 0 populations" in summary
    assert "Per-population breakdown:" not in summary
    assert "Date range" not in summary


def test_inspect_summary_signature_when_populated(tmp_path: Path) -> None:
    """Selector signature line present only when populated (Day 7+)."""
    anno = _v66_anno(tmp_path)
    sig = "sha256:abc123"
    result = SubsetResult(
        genetic_ids=["g"],
        n_matched=1,
        selector_signature=sig,
    )
    summary = format_inspect_summary(result, anno)
    assert "Selector signature: sha256:abc123" in summary
