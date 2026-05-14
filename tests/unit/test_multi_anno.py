"""Tests for the multi-anno select path (v0.4+).

Covers merge_multi_anno_results(), write_multi_anno_select_output(), and
the CLI multi-anno dispatch via run_select(anno_paths=...).

Seven original test cases from the v0.4 plan:
  1. Two-anno happy path — non-overlapping IDs → merged result has both sets.
  2. Exact-string dedup — same genetic_id in both → count=1, newer wins.
  3. Per-anno counts — per_anno_genetic_ids keys are correct.
  4. source_version TSV column — rows tagged with correct version.
  5. Row ordering — oldest anno rows first; .anno order within each.
  6. Multi-anno JSON — anno_versions / per_anno_n_matched keys present.
  7. Single-anno regression — existing run_select path unchanged.

Three regression tests for bugs fixed in review pass:
  8. Single-pair merge must populate anno_versions/anno_files/per_anno_genetic_ids.
  9. sum(per_population_counts.values()) must equal n_matched after dedup.
  10. source_anno passed with multiple anno_paths must raise UsageError.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

import aadr_resolve
from aadr_subset.engine import merge_multi_anno_results, select_samples
from aadr_subset.formats import write_multi_anno_select_output
from aadr_subset.types import OutputFormat, Selector

from tests.fixtures.synthesize import SynthRow, write_class_e_anno


# ---------------------------------------------------------------------------
# Helpers: build minimal synthetic AnnoFrames without touching the filesystem
# ---------------------------------------------------------------------------


def _make_anno(tmp_path: Path, name: str, rows: list[SynthRow]) -> aadr_resolve.AnnoFrame:
    """Write a class-E .anno under tmp_path and return its AnnoFrame."""
    p = tmp_path / name
    write_class_e_anno(p, rows)
    return aadr_resolve.AnnoFrame.from_path(p)


def _select_all(af: aadr_resolve.AnnoFrame) -> object:
    """Run select_samples with an empty (match-all) selector."""
    return select_samples(af, Selector())


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def anno_a(tmp_path: Path) -> aadr_resolve.AnnoFrame:
    """Three samples, unique to 'anno A' (version inferred from filename)."""
    return _make_anno(
        tmp_path,
        "anno_a_v44.3.anno",
        [
            SynthRow(genetic_id="IND_A1", individual_id="A1", group_id="PopA", coverage=1.0),
            SynthRow(genetic_id="IND_A2", individual_id="A2", group_id="PopA", coverage=0.8),
            SynthRow(genetic_id="IND_B1", individual_id="B1", group_id="PopB", coverage=0.5),
        ],
    )


@pytest.fixture
def anno_b(tmp_path: Path) -> aadr_resolve.AnnoFrame:
    """Three samples, unique to 'anno B'."""
    return _make_anno(
        tmp_path,
        "anno_b_v66.0.anno",
        [
            SynthRow(genetic_id="IND_C1", individual_id="C1", group_id="PopC", coverage=2.0),
            SynthRow(genetic_id="IND_C2", individual_id="C2", group_id="PopC", coverage=1.5),
            SynthRow(genetic_id="IND_D1", individual_id="D1", group_id="PopD", coverage=0.3),
        ],
    )


@pytest.fixture
def anno_with_overlap_old(tmp_path: Path) -> aadr_resolve.AnnoFrame:
    """Two samples; IND_SHARED appears here AND in anno_with_overlap_new."""
    return _make_anno(
        tmp_path,
        "overlap_v44.3.anno",
        [
            SynthRow(genetic_id="IND_SHARED", individual_id="SHARED", group_id="Pop1", coverage=0.5),
            SynthRow(genetic_id="IND_UNIQUE_OLD", individual_id="UNIQUE_OLD", group_id="Pop1", coverage=0.4),
        ],
    )


@pytest.fixture
def anno_with_overlap_new(tmp_path: Path) -> aadr_resolve.AnnoFrame:
    """Two samples; IND_SHARED appears here AND in anno_with_overlap_old."""
    return _make_anno(
        tmp_path,
        "overlap_v66.0.anno",
        [
            SynthRow(genetic_id="IND_SHARED", individual_id="SHARED", group_id="Pop1", coverage=1.0),
            SynthRow(genetic_id="IND_UNIQUE_NEW", individual_id="UNIQUE_NEW", group_id="Pop1", coverage=0.9),
        ],
    )


# ---------------------------------------------------------------------------
# Test 1: two-anno happy path — non-overlapping IDs
# ---------------------------------------------------------------------------


def test_merge_non_overlapping_annos(
    anno_a: aadr_resolve.AnnoFrame, anno_b: aadr_resolve.AnnoFrame
) -> None:
    result_a = _select_all(anno_a)
    result_b = _select_all(anno_b)
    pairs = [(anno_a, result_a), (anno_b, result_b)]

    merged = merge_multi_anno_results(pairs)  # type: ignore[arg-type]

    assert merged.n_matched == 6
    all_gids = set(merged.genetic_ids)
    assert "IND_A1" in all_gids
    assert "IND_C1" in all_gids
    # Both annos present in anno_versions.
    assert len(merged.anno_versions) == 2


# ---------------------------------------------------------------------------
# Test 2: exact-string dedup — same genetic_id in both annos
# ---------------------------------------------------------------------------


def test_merge_exact_string_dedup(
    anno_with_overlap_old: aadr_resolve.AnnoFrame,
    anno_with_overlap_new: aadr_resolve.AnnoFrame,
) -> None:
    result_old = _select_all(anno_with_overlap_old)
    result_new = _select_all(anno_with_overlap_new)
    # pairs in ascending version order (older first).
    pairs = [(anno_with_overlap_old, result_old), (anno_with_overlap_new, result_new)]

    merged = merge_multi_anno_results(pairs)  # type: ignore[arg-type]

    # IND_SHARED appears once; UNIQUE_OLD and UNIQUE_NEW both present.
    assert merged.n_matched == 3
    assert "IND_SHARED" in merged.genetic_ids
    assert "IND_UNIQUE_OLD" in merged.genetic_ids
    assert "IND_UNIQUE_NEW" in merged.genetic_ids
    gids_list = merged.genetic_ids
    assert gids_list.count("IND_SHARED") == 1, "IND_SHARED must appear exactly once"


# ---------------------------------------------------------------------------
# Test 3: per_anno_genetic_ids — correct per-version breakdown
# ---------------------------------------------------------------------------


def test_merge_per_anno_genetic_ids(
    anno_with_overlap_old: aadr_resolve.AnnoFrame,
    anno_with_overlap_new: aadr_resolve.AnnoFrame,
) -> None:
    result_old = _select_all(anno_with_overlap_old)
    result_new = _select_all(anno_with_overlap_new)
    pairs = [(anno_with_overlap_old, result_old), (anno_with_overlap_new, result_new)]

    merged = merge_multi_anno_results(pairs)  # type: ignore[arg-type]

    old_v = anno_with_overlap_old.version
    new_v = anno_with_overlap_new.version

    assert old_v in merged.per_anno_genetic_ids
    assert new_v in merged.per_anno_genetic_ids

    # IND_SHARED was superseded → should NOT be in old's surviving set.
    assert "IND_SHARED" not in merged.per_anno_genetic_ids[old_v]
    assert "IND_UNIQUE_OLD" in merged.per_anno_genetic_ids[old_v]

    # IND_SHARED IS in new's surviving set (it claimed it).
    assert "IND_SHARED" in merged.per_anno_genetic_ids[new_v]
    assert "IND_UNIQUE_NEW" in merged.per_anno_genetic_ids[new_v]


# ---------------------------------------------------------------------------
# Test 4: source_version column in TSV output
# ---------------------------------------------------------------------------


def test_multi_anno_tsv_source_version_column(
    anno_a: aadr_resolve.AnnoFrame, anno_b: aadr_resolve.AnnoFrame
) -> None:
    result_a = _select_all(anno_a)
    result_b = _select_all(anno_b)
    pairs = [(anno_a, result_a), (anno_b, result_b)]
    merged = merge_multi_anno_results(pairs)  # type: ignore[arg-type]

    buf = io.StringIO()
    import sys
    import unittest.mock as mock

    with mock.patch("sys.stdout", buf):
        write_multi_anno_select_output(
            merged,
            pairs,  # type: ignore[arg-type]
            fmt=OutputFormat.TSV,
            out_path=None,
            include_matched_criteria=False,
        )

    content = buf.getvalue()
    lines = content.strip().splitlines()
    header = lines[0].split("\t")
    assert "source_version" in header, f"source_version missing from header: {header}"

    sv_idx = header.index("source_version")
    data_lines = lines[1:]
    assert len(data_lines) == 6

    # All rows must have a non-empty source_version.
    for line in data_lines:
        cells = line.split("\t")
        assert cells[sv_idx] != "", f"Empty source_version in row: {line}"

    # anno_a rows should have anno_a.version; anno_b rows should have anno_b.version.
    anno_a_gids = set(merged.per_anno_genetic_ids.get(anno_a.version, []))
    anno_b_gids = set(merged.per_anno_genetic_ids.get(anno_b.version, []))

    gid_idx = header.index("genetic_id")
    for line in data_lines:
        cells = line.split("\t")
        gid = cells[gid_idx]
        sv = cells[sv_idx]
        if gid in anno_a_gids:
            assert sv == anno_a.version, f"{gid} should have source_version={anno_a.version!r}"
        elif gid in anno_b_gids:
            assert sv == anno_b.version, f"{gid} should have source_version={anno_b.version!r}"


# ---------------------------------------------------------------------------
# Test 5: row ordering — oldest anno rows first, .anno order within each
# ---------------------------------------------------------------------------


def test_multi_anno_row_ordering(
    anno_a: aadr_resolve.AnnoFrame, anno_b: aadr_resolve.AnnoFrame
) -> None:
    result_a = _select_all(anno_a)
    result_b = _select_all(anno_b)
    # anno_a is "older" (sorted first by version in pairs).
    pairs = [(anno_a, result_a), (anno_b, result_b)]
    merged = merge_multi_anno_results(pairs)  # type: ignore[arg-type]

    anno_a_gids = merged.per_anno_genetic_ids.get(anno_a.version, [])
    anno_b_gids = merged.per_anno_genetic_ids.get(anno_b.version, [])

    # All anno_a gids should appear before anno_b gids in genetic_ids.
    a_indices = [merged.genetic_ids.index(g) for g in anno_a_gids]
    b_indices = [merged.genetic_ids.index(g) for g in anno_b_gids]

    if a_indices and b_indices:
        assert max(a_indices) < min(b_indices), (
            "anno_a rows should all precede anno_b rows in merged genetic_ids"
        )


# ---------------------------------------------------------------------------
# Test 6: multi-anno JSON output includes additive keys
# ---------------------------------------------------------------------------


def test_multi_anno_json_additive_keys(
    anno_a: aadr_resolve.AnnoFrame, anno_b: aadr_resolve.AnnoFrame
) -> None:
    result_a = _select_all(anno_a)
    result_b = _select_all(anno_b)
    pairs = [(anno_a, result_a), (anno_b, result_b)]
    merged = merge_multi_anno_results(pairs)  # type: ignore[arg-type]

    buf = io.StringIO()
    import unittest.mock as mock

    with mock.patch("sys.stdout", buf):
        write_multi_anno_select_output(
            merged,
            pairs,  # type: ignore[arg-type]
            fmt=OutputFormat.JSON,
            out_path=None,
            include_matched_criteria=False,
        )

    data = json.loads(buf.getvalue())

    # Additive keys present.
    assert "anno_versions" in data, "anno_versions key missing from multi-anno JSON"
    assert "anno_files" in data, "anno_files key missing"
    assert "per_anno_n_matched" in data, "per_anno_n_matched key missing"

    # Backwards-compat single-anno keys still present.
    assert "anno_version" in data
    assert "anno_file" in data

    # per_anno_n_matched values should sum to n_matched.
    total = sum(data["per_anno_n_matched"].values())
    assert total == data["n_matched"]


# ---------------------------------------------------------------------------
# Test 7: single-anno regression — run_select with one anno_path unchanged
# ---------------------------------------------------------------------------


def test_single_anno_select_unchanged(tmp_path: Path, anno_a: aadr_resolve.AnnoFrame) -> None:
    """run_select with a single anno_path behaves identically to pre-v0.4."""
    from aadr_subset.commands.select_cmd import run_select

    selector = tmp_path / "all.yaml"
    selector.write_text("populations: [PopA]\n", encoding="utf-8")
    out = tmp_path / "out.ids"

    exit_code = run_select(
        selector_path=str(selector),
        anno_paths=(str(anno_a.path),),
        out=str(out),
        fmt="ids",
        schema_override=None,
        allow_empty=False,
        allow_empty_source=False,
        include_matched_criteria=False,
        source_anno=None,
        mid_bridge=None,
        strict_resolve=False,
        coverage_column=None,
        coverage_derive=None,
        max_per_population=None,
        max_per_individual=None,
        quiet=True,
    )
    assert exit_code == 0
    ids = out.read_text(encoding="utf-8").strip().splitlines()
    assert set(ids) == {"IND_A1", "IND_A2"}


# ---------------------------------------------------------------------------
# Test 8: single-pair merge must populate multi-anno fields  [bug regression]
# ---------------------------------------------------------------------------


def test_merge_single_pair_populates_multi_anno_fields(
    anno_a: aadr_resolve.AnnoFrame,
) -> None:
    """Single-pair merge must populate anno_versions/anno_files/per_anno_genetic_ids.

    Before the fix, the early-return path in merge_multi_anno_results returned
    the bare engine SubsetResult without filling in the three multi-anno fields,
    leaving them at their empty-default values.
    """
    result_a = _select_all(anno_a)
    merged = merge_multi_anno_results([(anno_a, result_a)])  # type: ignore[arg-type]

    assert merged.anno_versions == [anno_a.version]
    assert len(merged.anno_files) == 1
    assert anno_a.version in merged.per_anno_genetic_ids
    assert set(merged.per_anno_genetic_ids[anno_a.version]) == set(merged.genetic_ids)


# ---------------------------------------------------------------------------
# Test 9: per_population_counts sum == n_matched after dedup  [bug regression]
# ---------------------------------------------------------------------------


def test_merge_pop_counts_accurate_after_dedup(
    anno_with_overlap_old: aadr_resolve.AnnoFrame,
    anno_with_overlap_new: aadr_resolve.AnnoFrame,
) -> None:
    """sum(per_population_counts.values()) must equal n_matched after dedup.

    Before the fix, per_population_counts was built by summing the raw
    per-anno counts, which double-counted samples that appeared in both
    annos and were deduplicated out.  With overlap fixtures the sum was 4
    but n_matched was 3 (IND_SHARED deduplicated to the newer version).
    """
    result_old = _select_all(anno_with_overlap_old)
    result_new = _select_all(anno_with_overlap_new)
    pairs = [(anno_with_overlap_old, result_old), (anno_with_overlap_new, result_new)]

    merged = merge_multi_anno_results(pairs)  # type: ignore[arg-type]

    # IND_SHARED appears in both annos but is deduplicated to exactly one row.
    assert merged.n_matched == 3, (
        f"expected 3 merged samples (IND_SHARED deduped), got {merged.n_matched}"
    )
    total_from_counts = sum(merged.per_population_counts.values())
    assert total_from_counts == merged.n_matched, (
        f"per_population_counts sum {total_from_counts} != n_matched {merged.n_matched}; "
        f"counts={merged.per_population_counts}"
    )


# ---------------------------------------------------------------------------
# Test 10: source_anno + multi-anno paths must raise UsageError  [bug regression]
# ---------------------------------------------------------------------------


def test_run_select_multi_rejects_source_anno(
    tmp_path: Path, anno_a: aadr_resolve.AnnoFrame
) -> None:
    """source_anno passed alongside multiple anno_paths must raise UsageError.

    Before the fix, the multi-anno dispatch branch was reached before the
    source_anno guard, so source_anno was silently dropped rather than
    surfacing a hard error to the caller.
    """
    from aadr_subset.commands.select_cmd import run_select
    from aadr_subset.errors import UsageError

    second_anno = _make_anno(
        tmp_path,
        "anno_second_v66.0.anno",
        [SynthRow(genetic_id="IND_X1", individual_id="X1", group_id="PopX", coverage=1.0)],
    )
    selector = tmp_path / "sel.yaml"
    selector.write_text("populations: [PopA]\n", encoding="utf-8")

    with pytest.raises(UsageError) as exc_info:
        run_select(
            selector_path=str(selector),
            anno_paths=(str(anno_a.path), str(second_anno.path)),
            out=None,
            fmt="ids",
            schema_override=None,
            allow_empty=True,
            allow_empty_source=False,
            include_matched_criteria=False,
            source_anno=str(tmp_path / "fake_source.anno"),
            mid_bridge=None,
            strict_resolve=False,
            coverage_column=None,
            coverage_derive=None,
            max_per_population=None,
            max_per_individual=None,
            quiet=True,
        )
    # UsageError stores the message in the ValidationError payload, not in the
    # base exception string.  Verify the flag name appears in at least one error.
    assert any(
        "--source-anno" in e.message for e in exc_info.value.errors
    ), f"expected '--source-anno' in error messages; got {exc_info.value.errors}"
