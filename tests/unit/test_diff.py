"""Tests for `aadr-subset diff` (v0.2).

Exercises the set-difference engine output, the build_diff_result
helper, the human and JSON output formats, and the CLI integration.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from aadr_subset.reporting import build_diff_result, format_diff_summary, write_diff_json
from aadr_subset.types import SelectorWarnings, SubsetResult
from tests.fixtures.synthesize import make_loschbour_v66_fixture


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "aadr_subset", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _result(
    genetic_ids: list[str],
    per_pop: dict[str, int],
    *,
    selector_file: str = "a.yaml",
    signature: str = "sha256:" + "a" * 64,
    anno_version: str = "v66.0",
) -> SubsetResult:
    return SubsetResult(
        genetic_ids=genetic_ids,
        n_matched=len(genetic_ids),
        per_population_counts=per_pop,
        per_branch_counts={},
        excluded_counts=[],
        matched_criteria={},
        warnings=SelectorWarnings(),
        selector_signature=signature,
        anno_file="x.anno",
        anno_version=anno_version,
        schema_class="E",
        selector_file=selector_file,
    )


# --- build_diff_result ---


def test_diff_set_partition_disjoint() -> None:
    a = _result(["X", "Y", "Z"], {"G1": 3}, selector_file="a.yaml")
    b = _result(["P", "Q"], {"G2": 2}, selector_file="b.yaml")
    d = build_diff_result(a, b)
    assert d.a_only == ["X", "Y", "Z"]
    assert d.b_only == ["P", "Q"]
    assert d.both == []


def test_diff_set_partition_overlap() -> None:
    a = _result(["X", "Y", "Z"], {"G1": 3}, selector_file="a.yaml")
    b = _result(["Y", "Z", "W"], {"G1": 3}, selector_file="b.yaml")
    d = build_diff_result(a, b)
    assert d.a_only == ["X"]
    assert d.b_only == ["W"]
    # `both` preserves A's order.
    assert d.both == ["Y", "Z"]


def test_diff_identical_selectors_have_empty_only_sets() -> None:
    a = _result(["X", "Y"], {"G1": 2})
    b = _result(["X", "Y"], {"G1": 2}, selector_file="b.yaml")
    d = build_diff_result(a, b)
    assert d.a_only == []
    assert d.b_only == []
    assert d.both == ["X", "Y"]


def test_diff_preserves_anno_row_order() -> None:
    """The `a_only` and `b_only` lists preserve each result's row order."""
    a = _result(["Z", "Y", "X"], {})
    b = _result(["Y", "W"], {}, selector_file="b.yaml")
    d = build_diff_result(a, b)
    # X and Z are A-only; their order matches A's order (Z first, then X).
    assert d.a_only == ["Z", "X"]
    assert d.b_only == ["W"]


def test_diff_per_population_delta() -> None:
    a = _result(["X", "Y"], {"G1": 2})
    b = _result(["Y", "Z", "W"], {"G1": 1, "G2": 2}, selector_file="b.yaml")
    d = build_diff_result(a, b)
    assert d.per_population_delta == {"G1": (2, 1), "G2": (0, 2)}


def test_diff_carries_signatures_and_filenames() -> None:
    a = _result(["X"], {}, selector_file="a.yaml", signature="sha256:" + "a" * 64)
    b = _result(["Y"], {}, selector_file="b.yaml", signature="sha256:" + "b" * 64)
    d = build_diff_result(a, b)
    assert d.selector_a_file == "a.yaml"
    assert d.selector_b_file == "b.yaml"
    assert d.a_signature.endswith("a" * 4)
    assert d.b_signature.endswith("b" * 4)


# --- format_diff_summary ---


def test_format_diff_summary_basic() -> None:
    a = _result(["X", "Y"], {"G1": 2}, selector_file="a.yaml")
    b = _result(["Y", "Z"], {"G1": 2}, selector_file="b.yaml")
    d = build_diff_result(a, b)
    text = format_diff_summary(d)
    assert "Selector A: a.yaml" in text
    assert "Selector B: b.yaml" in text
    assert "A only: 1 sample" in text
    assert "B only: 1 sample" in text
    assert "Both:   1 sample" in text
    assert "Per-population delta:" in text


def test_format_diff_summary_short_signature() -> None:
    """Header lines show a short sha256:abcdefg...hijklmn form."""
    a = _result(["X"], {"G1": 1}, signature="sha256:" + "a" * 64)
    b = _result(["Y"], {"G1": 1}, selector_file="b.yaml", signature="sha256:" + "b" * 64)
    d = build_diff_result(a, b)
    text = format_diff_summary(d)
    assert "(sha256:aaaaaaa...aaaaaaa)" in text
    assert "(sha256:bbbbbbb...bbbbbbb)" in text


def test_format_diff_summary_handles_empty_signature() -> None:
    a = _result(["X"], {"G1": 1}, signature="")
    b = _result(["Y"], {"G1": 1}, selector_file="b.yaml", signature="")
    d = build_diff_result(a, b)
    text = format_diff_summary(d)
    # No "(sha256:" parenthesized blob; just the bare filename.
    assert "Selector A: a.yaml\n" in text


def test_format_diff_summary_long_sample_list_preview() -> None:
    """Sample preview truncates at sample_preview=10 with a tail count."""
    a = _result([f"S{i}" for i in range(15)], {"G": 15})
    b = _result(["S0"], {"G": 1}, selector_file="b.yaml")
    d = build_diff_result(a, b)
    text = format_diff_summary(d)
    assert "+4 more" in text  # 14 a_only - 10 preview


# --- write_diff_json ---


def test_write_diff_json_top_level_keys(tmp_path: Path) -> None:
    a = _result(["X", "Y"], {"G1": 2})
    b = _result(["Y", "Z"], {"G1": 2}, selector_file="b.yaml")
    d = build_diff_result(a, b)
    out = tmp_path / "diff.json"
    write_diff_json(d, out_path=out)
    parsed = json.loads(out.read_text(encoding="utf-8"))
    assert set(parsed) == {
        "anno_file",
        "anno_version",
        "schema_class",
        "selector_a",
        "selector_b",
        "n_a_only",
        "n_b_only",
        "n_both",
        "a_only",
        "b_only",
        "both",
        "per_population_delta",
        "schema_version",
        "aadr_subset_version",
    }
    assert parsed["schema_version"] == 1
    assert parsed["n_a_only"] == 1
    assert parsed["n_b_only"] == 1
    assert parsed["n_both"] == 1


def test_write_diff_json_per_population_delta_shape(tmp_path: Path) -> None:
    a = _result(["X"], {"G1": 1})
    b = _result(["Y", "Z"], {"G1": 1, "G2": 1}, selector_file="b.yaml")
    d = build_diff_result(a, b)
    out = tmp_path / "diff.json"
    write_diff_json(d, out_path=out)
    parsed = json.loads(out.read_text(encoding="utf-8"))
    assert parsed["per_population_delta"] == [
        {"group_id": "G1", "n_a": 1, "n_b": 1, "delta": 0},
        {"group_id": "G2", "n_a": 0, "n_b": 1, "delta": 1},
    ]


def test_write_diff_json_to_stdout() -> None:
    import io as _io
    import sys as _sys

    a = _result(["X"], {"G1": 1})
    b = _result(["Y"], {"G1": 1}, selector_file="b.yaml")
    d = build_diff_result(a, b)
    captured = _io.StringIO()
    orig = _sys.stdout
    try:
        _sys.stdout = captured
        write_diff_json(d, out_path=None)
    finally:
        _sys.stdout = orig
    parsed = json.loads(captured.getvalue())
    assert parsed["n_a_only"] == 1


# --- CLI integration ---


@pytest.fixture
def v66_anno(tmp_path: Path) -> Path:
    p = tmp_path / "v66.0.anno"
    make_loschbour_v66_fixture(p)
    return p


def test_cli_diff_human_format(tmp_path: Path, v66_anno: Path) -> None:
    """Two overlapping selectors; default `human` format to stdout."""
    a = tmp_path / "a.yaml"
    a.write_text("populations: [Western_HG]\n", encoding="utf-8")
    b = tmp_path / "b.yaml"
    b.write_text("populations: [Western_HG, Eastern_HG]\n", encoding="utf-8")
    result = _run_cli("diff", str(a), str(b), str(v66_anno))
    assert result.returncode == 0, result.stderr
    # A: 3 Western_HG; B: 3 Western_HG + 1 Eastern_HG.
    assert "A only: 0 samples" in result.stdout
    assert "B only: 1 sample" in result.stdout
    assert "Both:   3 samples" in result.stdout
    assert "Per-population delta:" in result.stdout
    assert "Eastern_HG" in result.stdout


def test_cli_diff_json_to_file(tmp_path: Path, v66_anno: Path) -> None:
    a = tmp_path / "a.yaml"
    a.write_text("populations: [Western_HG]\n", encoding="utf-8")
    b = tmp_path / "b.yaml"
    b.write_text("populations: [Eastern_HG]\n", encoding="utf-8")
    out = tmp_path / "diff.json"
    result = _run_cli("diff", str(a), str(b), str(v66_anno), "--format", "json", "-o", str(out))
    assert result.returncode == 0, result.stderr
    parsed = json.loads(out.read_text(encoding="utf-8"))
    assert parsed["n_a_only"] == 3
    assert parsed["n_b_only"] == 1
    assert parsed["n_both"] == 0
    assert parsed["selector_a"]["signature"].startswith("sha256:")
    assert parsed["selector_b"]["signature"].startswith("sha256:")


def test_cli_diff_identical_selectors(tmp_path: Path, v66_anno: Path) -> None:
    """Two literally-identical selectors → empty diff, still exits 0."""
    sel_yaml = "populations: [Western_HG]\n"
    a = tmp_path / "a.yaml"
    a.write_text(sel_yaml, encoding="utf-8")
    b = tmp_path / "b.yaml"
    b.write_text(sel_yaml, encoding="utf-8")
    result = _run_cli("diff", str(a), str(b), str(v66_anno))
    assert result.returncode == 0, result.stderr
    assert "A only: 0 samples" in result.stdout
    assert "B only: 0 samples" in result.stdout
    assert "Both:   3 samples" in result.stdout


def test_cli_diff_rejects_cross_version(tmp_path: Path, v66_anno: Path) -> None:
    """v0.2 limitation: cross-version diff (any selector with
    resolve_to_version) is a UsageError."""
    a = tmp_path / "a.yaml"
    a.write_text(
        "individual_ids: [I0001]\nsource_version: v44.3\nresolve_to_version: v66.0\n",
        encoding="utf-8",
    )
    b = tmp_path / "b.yaml"
    b.write_text("populations: [Western_HG]\n", encoding="utf-8")
    result = _run_cli("diff", str(a), str(b), str(v66_anno))
    assert result.returncode == 4
    assert "cross-version diff is not supported" in result.stderr


def test_cli_diff_help_documents_flags() -> None:
    result = _run_cli("diff", "--help")
    assert result.returncode == 0
    assert "--format" in result.stdout
    assert "human" in result.stdout
    assert "json" in result.stdout
    assert "SELECTOR_A_PATH" in result.stdout or "SELECTOR_A" in result.stdout
