"""Unit tests for reporting.write_report_tsv + write_report_json (Day 5)."""

from __future__ import annotations

import json
from pathlib import Path

import aadr_resolve

from aadr_subset.reporting import (
    REPORT_SCHEMA_VERSION,
    write_report_json,
    write_report_tsv,
)
from aadr_subset.types import (
    SelectorWarnings,
    SubsetResult,
)
from tests.fixtures.synthesize import make_loschbour_v66_fixture


def _make_western_result(anno: aadr_resolve.AnnoFrame) -> SubsetResult:
    """3 of 3 Western_HG samples match — the Loschbour pair + Bichon."""
    return SubsetResult(
        genetic_ids=["Loschbour.AG", "Loschbour.DG", "Bichon"],
        n_matched=3,
        per_population_counts={"Western_HG": 3},
        per_branch_counts={"top_level": 3},
        excluded_counts=[],
        matched_criteria={},
        warnings=SelectorWarnings(),
        selector_signature="sha256:" + "a" * 64,
        anno_file="x.anno",
        anno_version="v66.0",
        schema_class="E",
        selector_file="x.yaml",
    )


# --- write_report_tsv ---


def test_report_tsv_header_and_one_row(tmp_path: Path) -> None:
    anno_path = tmp_path / "synth_v66.0.anno"
    make_loschbour_v66_fixture(anno_path)
    anno = aadr_resolve.AnnoFrame.from_path(anno_path, version_label="v66.0")
    result = _make_western_result(anno)
    out = tmp_path / "report.tsv"
    write_report_tsv(result, anno, include_empty_groups=False, out_path=out)
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0].split("\t") == [
        "group_id",
        "n_matched",
        "n_in_anno",
        "pct_matched",
        "date_min_calbp",
        "date_max_calbp",
        "coverage_median",
    ]
    cells = lines[1].split("\t")
    assert cells[0] == "Western_HG"
    assert cells[1] == "3"
    assert cells[2] == "3"
    assert cells[3] == "100.0"
    assert cells[4] == "8000"
    assert cells[5] == "13700"
    # median of [1.21, 0.78, 0.82] = 0.82
    assert cells[6] == "0.82"


def test_report_tsv_pct_one_decimal(tmp_path: Path) -> None:
    """pct_matched rendered to 1 decimal: 1/3 → 33.3."""
    anno_path = tmp_path / "synth_v66.0.anno"
    make_loschbour_v66_fixture(anno_path)
    anno = aadr_resolve.AnnoFrame.from_path(anno_path, version_label="v66.0")
    # Only Bichon matches; 1 of 3 Western_HG → 33.3%.
    result = SubsetResult(
        genetic_ids=["Bichon"],
        n_matched=1,
        per_population_counts={"Western_HG": 1},
        per_branch_counts={"top_level": 1},
        excluded_counts=[],
        matched_criteria={},
        warnings=SelectorWarnings(),
    )
    out = tmp_path / "r.tsv"
    write_report_tsv(result, anno, include_empty_groups=False, out_path=out)
    cells = out.read_text(encoding="utf-8").splitlines()[1].split("\t")
    assert cells[3] == "33.3"


def test_report_tsv_default_excludes_empty_groups(tmp_path: Path) -> None:
    """Default include_empty_groups=False emits only matched groups."""
    anno_path = tmp_path / "synth_v66.0.anno"
    make_loschbour_v66_fixture(anno_path)
    anno = aadr_resolve.AnnoFrame.from_path(anno_path, version_label="v66.0")
    result = _make_western_result(anno)
    out = tmp_path / "r.tsv"
    write_report_tsv(result, anno, include_empty_groups=False, out_path=out)
    lines = out.read_text(encoding="utf-8").splitlines()
    # 1 header + 1 data row (Western_HG only).
    assert len(lines) == 2


def test_report_tsv_include_empty_groups_adds_rows(tmp_path: Path) -> None:
    """include_empty_groups=True adds the other .anno groups (Eastern_HG,
    English.SG) with n_matched=0."""
    anno_path = tmp_path / "synth_v66.0.anno"
    make_loschbour_v66_fixture(anno_path)
    anno = aadr_resolve.AnnoFrame.from_path(anno_path, version_label="v66.0")
    result = _make_western_result(anno)
    out = tmp_path / "r.tsv"
    write_report_tsv(result, anno, include_empty_groups=True, out_path=out)
    lines = out.read_text(encoding="utf-8").splitlines()
    # Header + Western_HG + Eastern_HG + English.SG = 4 lines.
    assert len(lines) == 4
    # Empty-group rows have n_matched=0 + 0.0%, n_in_anno populated.
    empty_rows = [line.split("\t") for line in lines[2:]]
    for row in empty_rows:
        assert row[1] == "0"
        assert int(row[2]) > 0  # n_in_anno > 0
        assert row[3] == "0.0"


def test_report_tsv_stdout_no_atomic(tmp_path: Path) -> None:
    import io
    import sys

    anno_path = tmp_path / "synth_v66.0.anno"
    make_loschbour_v66_fixture(anno_path)
    anno = aadr_resolve.AnnoFrame.from_path(anno_path, version_label="v66.0")
    result = _make_western_result(anno)
    captured = io.StringIO()
    orig = sys.stdout
    try:
        sys.stdout = captured
        write_report_tsv(result, anno, include_empty_groups=False, out_path=None)
    finally:
        sys.stdout = orig
    text = captured.getvalue()
    assert text.startswith("group_id\t")


# --- write_report_json ---


def test_report_json_top_level_keys(tmp_path: Path) -> None:
    anno_path = tmp_path / "synth_v66.0.anno"
    make_loschbour_v66_fixture(anno_path)
    anno = aadr_resolve.AnnoFrame.from_path(anno_path, version_label="v66.0")
    result = _make_western_result(anno)
    out = tmp_path / "r.json"
    write_report_json(result, anno, include_empty_groups=False, out_path=out)
    parsed = json.loads(out.read_text(encoding="utf-8"))
    assert set(parsed.keys()) == {
        "selector_signature",
        "anno_version",
        "schema_version",
        "aadr_subset_version",
        "populations",
    }
    assert parsed["schema_version"] == REPORT_SCHEMA_VERSION
    assert parsed["selector_signature"].startswith("sha256:")
    assert parsed["anno_version"] == "v66.0"


def test_report_json_population_entry_shape(tmp_path: Path) -> None:
    anno_path = tmp_path / "synth_v66.0.anno"
    make_loschbour_v66_fixture(anno_path)
    anno = aadr_resolve.AnnoFrame.from_path(anno_path, version_label="v66.0")
    result = _make_western_result(anno)
    out = tmp_path / "r.json"
    write_report_json(result, anno, include_empty_groups=False, out_path=out)
    parsed = json.loads(out.read_text(encoding="utf-8"))
    pop = parsed["populations"][0]
    assert set(pop.keys()) == {
        "group_id",
        "n_matched",
        "n_in_anno",
        "pct_matched",
        "date_min_calbp",
        "date_max_calbp",
        "coverage_median",
        "coverage_min",
        "coverage_max",
    }
    assert pop["group_id"] == "Western_HG"
    assert pop["n_matched"] == 3
    assert pop["n_in_anno"] == 3
    # pct_matched is a fraction, NOT a percentage (HLD §Reports JSON).
    assert pop["pct_matched"] == 1.0
    assert pop["date_min_calbp"] == 8000
    assert pop["date_max_calbp"] == 13700
    # Coverage stats over matched rows only.
    assert pop["coverage_min"] == 0.78
    assert pop["coverage_max"] == 1.21
    assert pop["coverage_median"] == 0.82


def test_report_json_empty_groups_have_null_stats(tmp_path: Path) -> None:
    anno_path = tmp_path / "synth_v66.0.anno"
    make_loschbour_v66_fixture(anno_path)
    anno = aadr_resolve.AnnoFrame.from_path(anno_path, version_label="v66.0")
    result = _make_western_result(anno)
    out = tmp_path / "r.json"
    write_report_json(result, anno, include_empty_groups=True, out_path=out)
    parsed = json.loads(out.read_text(encoding="utf-8"))
    # Empty groups (n_matched=0) have null date/coverage aggregates.
    empties = [p for p in parsed["populations"] if p["n_matched"] == 0]
    assert empties, "expected non-Western groups in empty rows"
    for p in empties:
        assert p["date_min_calbp"] is None
        assert p["date_max_calbp"] is None
        assert p["coverage_median"] is None


def test_report_json_excludes_empty_when_flag_off(tmp_path: Path) -> None:
    anno_path = tmp_path / "synth_v66.0.anno"
    make_loschbour_v66_fixture(anno_path)
    anno = aadr_resolve.AnnoFrame.from_path(anno_path, version_label="v66.0")
    result = _make_western_result(anno)
    out = tmp_path / "r.json"
    write_report_json(result, anno, include_empty_groups=False, out_path=out)
    parsed = json.loads(out.read_text(encoding="utf-8"))
    assert len(parsed["populations"]) == 1
    assert parsed["populations"][0]["group_id"] == "Western_HG"
