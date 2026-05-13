"""Unit tests for write_tsv + write_json (Day 4)."""

from __future__ import annotations

import json
from pathlib import Path

import aadr_resolve

from aadr_subset.formats import write_json, write_tsv
from aadr_subset.types import (
    ExcludeCount,
    SelectorWarnings,
    SubsetResult,
)
from tests.fixtures.synthesize import make_loschbour_v66_fixture


def _make_result_for_western(anno: aadr_resolve.AnnoFrame) -> SubsetResult:
    """Build a SubsetResult matching the Western_HG samples in the v66 fixture."""
    return SubsetResult(
        genetic_ids=["Loschbour.AG", "Loschbour.DG", "Bichon"],
        n_matched=3,
        per_population_counts={"Western_HG": 3},
        per_branch_counts={"top_level": 3},
        excluded_counts=[],
        matched_criteria={},
        warnings=SelectorWarnings(),
        selector_signature="",
        anno_file="x.anno",
        anno_version="v66.0",
        schema_class="E",
        selector_file="x.yaml",
        coverage_column_used=None,
    )


# --- write_tsv ---


def test_write_tsv_header_and_rows(tmp_path: Path) -> None:
    """TSV header lists the six pinned columns; each matched row gets one line."""
    anno_path = tmp_path / "synth_v66.0.anno"
    make_loschbour_v66_fixture(anno_path)
    anno = aadr_resolve.AnnoFrame.from_path(anno_path, version_label="v66.0")
    result = _make_result_for_western(anno)
    out = tmp_path / "out.tsv"
    write_tsv(result, anno, out)
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0].split("\t") == [
        "genetic_id",
        "individual_id",
        "group_id",
        "date_calbp",
        "coverage",
        "matched_criteria",
    ]
    # Three Western_HG rows, in .anno row order.
    assert lines[1].split("\t")[0] == "Loschbour.AG"
    assert lines[1].split("\t")[1] == "Loschbour"
    assert lines[1].split("\t")[2] == "Western_HG"
    assert lines[1].split("\t")[3] == "8000"
    # Coverage formatted as plain float (1.21 — no x suffix).
    assert lines[1].split("\t")[4] == "1.21"
    assert lines[3].split("\t")[0] == "Bichon"


def test_write_tsv_empty_cells_for_missing(tmp_path: Path) -> None:
    """date_calbp / coverage missing → empty cells (English.SG modern samples
    have NaN coverage by design in the fixture)."""
    anno_path = tmp_path / "synth_v66.0.anno"
    make_loschbour_v66_fixture(anno_path)
    anno = aadr_resolve.AnnoFrame.from_path(anno_path, version_label="v66.0")
    result = SubsetResult(
        genetic_ids=["English.1"],
        n_matched=1,
        per_population_counts={"English.SG": 1},
        per_branch_counts={"top_level": 1},
        excluded_counts=[],
        matched_criteria={},
        warnings=SelectorWarnings(),
    )
    out = tmp_path / "modern.tsv"
    write_tsv(result, anno, out)
    lines = out.read_text(encoding="utf-8").splitlines()
    cells = lines[1].split("\t")
    assert cells[0] == "English.1"
    assert cells[3] == "70"  # date_calbp populated
    assert cells[4] == ""  # coverage NaN → empty cell


def test_write_tsv_matched_criteria_semicolon_joined(tmp_path: Path) -> None:
    """matched_criteria emits semicolon-joined keys."""
    anno_path = tmp_path / "synth_v66.0.anno"
    make_loschbour_v66_fixture(anno_path)
    anno = aadr_resolve.AnnoFrame.from_path(anno_path, version_label="v66.0")
    result = SubsetResult(
        genetic_ids=["KO1"],
        n_matched=1,
        per_population_counts={"Eastern_HG": 1},
        per_branch_counts={"top_level": 1, "any[0]": 1},
        excluded_counts=[],
        matched_criteria={"KO1": ["populations:Eastern_HG", "any[0]"]},
        warnings=SelectorWarnings(),
    )
    out = tmp_path / "ko.tsv"
    write_tsv(result, anno, out)
    cells = out.read_text(encoding="utf-8").splitlines()[1].split("\t")
    assert cells[5] == "populations:Eastern_HG;any[0]"


def test_write_tsv_stdout_no_atomic(tmp_path: Path, capsys: object) -> None:
    """out_path=None writes to stdout (no atomicity contract)."""
    import sys

    anno_path = tmp_path / "synth_v66.0.anno"
    make_loschbour_v66_fixture(anno_path)
    anno = aadr_resolve.AnnoFrame.from_path(anno_path, version_label="v66.0")
    result = _make_result_for_western(anno)
    # Run write_tsv and capture stdout.
    import io

    captured = io.StringIO()
    orig = sys.stdout
    try:
        sys.stdout = captured
        write_tsv(result, anno, None)
    finally:
        sys.stdout = orig
    text = captured.getvalue()
    assert text.startswith("genetic_id\t")
    assert "Loschbour.AG\tLoschbour\tWestern_HG" in text


# --- write_json ---


def test_write_json_key_order_pinned(tmp_path: Path) -> None:
    """JSON output emits the 16-key insertion order from LLD §3.5."""
    anno_path = tmp_path / "synth_v66.0.anno"
    make_loschbour_v66_fixture(anno_path)
    anno = aadr_resolve.AnnoFrame.from_path(anno_path, version_label="v66.0")
    result = _make_result_for_western(anno)
    out = tmp_path / "out.json"
    write_json(result, anno, include_matched_criteria=False, out_path=out)
    # Parse + check the key order (json.dumps preserves insertion order).
    raw = out.read_text(encoding="utf-8")
    keys_in_order = [line.split('"')[1] for line in raw.splitlines() if line.startswith('  "')]
    # v0.3: sampling_drops inserted after excluded_counts (additive).
    # matched_criteria still omitted when empty.
    expected = [
        "genetic_ids",
        "n_matched",
        "per_population_counts",
        "per_branch_counts",
        "excluded_counts",
        "sampling_drops",
        "warnings",
        "selector_signature",
        "selector_file",
        "anno_file",
        "anno_version",
        "schema_class",
        "coverage_column",
        "aadr_subset_version",
        "aadr_resolve_version",
        "schema_version",
    ]
    assert keys_in_order == expected


def test_write_json_matched_criteria_omitted_when_empty(tmp_path: Path) -> None:
    """matched_criteria empty → key OMITTED from JSON (not emitted as {})."""
    anno_path = tmp_path / "synth_v66.0.anno"
    make_loschbour_v66_fixture(anno_path)
    anno = aadr_resolve.AnnoFrame.from_path(anno_path, version_label="v66.0")
    result = _make_result_for_western(anno)
    out = tmp_path / "default.json"
    write_json(result, anno, include_matched_criteria=False, out_path=out)
    parsed = json.loads(out.read_text(encoding="utf-8"))
    assert "matched_criteria" not in parsed


def test_write_json_matched_criteria_present_when_opted_in(tmp_path: Path) -> None:
    """include_matched_criteria=True AND non-empty matched_criteria → key present."""
    anno_path = tmp_path / "synth_v66.0.anno"
    make_loschbour_v66_fixture(anno_path)
    anno = aadr_resolve.AnnoFrame.from_path(anno_path, version_label="v66.0")
    result = SubsetResult(
        genetic_ids=["Loschbour.AG"],
        n_matched=1,
        per_population_counts={"Western_HG": 1},
        per_branch_counts={"top_level": 1},
        excluded_counts=[],
        matched_criteria={"Loschbour.AG": ["populations:Western_HG"]},
        warnings=SelectorWarnings(),
    )
    out = tmp_path / "with_mc.json"
    write_json(result, anno, include_matched_criteria=True, out_path=out)
    parsed = json.loads(out.read_text(encoding="utf-8"))
    assert "matched_criteria" in parsed
    assert parsed["matched_criteria"] == {"Loschbour.AG": ["populations:Western_HG"]}


def test_write_json_excluded_counts_list_of_objects(tmp_path: Path) -> None:
    """excluded_counts serializes as a list-of-objects (HLD v4b shape),
    NOT a dict with synthetic colon-keys."""
    anno_path = tmp_path / "synth_v66.0.anno"
    make_loschbour_v66_fixture(anno_path)
    anno = aadr_resolve.AnnoFrame.from_path(anno_path, version_label="v66.0")
    result = SubsetResult(
        genetic_ids=["Loschbour.AG"],
        n_matched=1,
        per_population_counts={"Western_HG": 1},
        per_branch_counts={"top_level": 1},
        excluded_counts=[
            ExcludeCount(key="group_ids", value="Eastern_HG", count=2),
            ExcludeCount(key="group_ids", value="English.SG", count=2),
        ],
        matched_criteria={},
        warnings=SelectorWarnings(),
    )
    out = tmp_path / "ex.json"
    write_json(result, anno, include_matched_criteria=False, out_path=out)
    parsed = json.loads(out.read_text(encoding="utf-8"))
    assert isinstance(parsed["excluded_counts"], list)
    assert parsed["excluded_counts"] == [
        {"key": "group_ids", "value": "Eastern_HG", "count": 2},
        {"key": "group_ids", "value": "English.SG", "count": 2},
    ]


def test_write_json_schema_version_field(tmp_path: Path) -> None:
    """schema_version: 1 always present."""
    anno_path = tmp_path / "synth_v66.0.anno"
    make_loschbour_v66_fixture(anno_path)
    anno = aadr_resolve.AnnoFrame.from_path(anno_path, version_label="v66.0")
    result = _make_result_for_western(anno)
    out = tmp_path / "sv.json"
    write_json(result, anno, include_matched_criteria=False, out_path=out)
    parsed = json.loads(out.read_text(encoding="utf-8"))
    assert parsed["schema_version"] == 1


def test_write_json_version_fields_populated(tmp_path: Path) -> None:
    """aadr_subset_version + aadr_resolve_version both populated."""
    from aadr_subset import __version__ as aadr_subset_v

    anno_path = tmp_path / "synth_v66.0.anno"
    make_loschbour_v66_fixture(anno_path)
    anno = aadr_resolve.AnnoFrame.from_path(anno_path, version_label="v66.0")
    result = _make_result_for_western(anno)
    out = tmp_path / "ver.json"
    write_json(result, anno, include_matched_criteria=False, out_path=out)
    parsed = json.loads(out.read_text(encoding="utf-8"))
    assert parsed["aadr_subset_version"] == aadr_subset_v
    # aadr_resolve is installed in the venv; should not be "not-installed".
    assert parsed["aadr_resolve_version"] not in ("not-installed", "unknown")
