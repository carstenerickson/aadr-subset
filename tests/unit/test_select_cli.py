"""Integration tests for the `aadr-subset select` CLI.

Day 2 surface: populations + individual_ids predicates on a single
.anno; output to file or stdout via --format=ids (default). The full
inspect / report / cross-version flows land later per HLD project plan.

These tests synthesize a class-E (.v66.0) .anno via the synthesizer in
tests/fixtures/synthesize.py and run aadr-subset as a subprocess, then
inspect the output file content and the exit code.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.fixtures.synthesize import (
    make_loschbour_v66_fixture,
    make_v62_class_d_fixture,
)


def _run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "aadr_subset", *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
    )


@pytest.fixture
def v66_anno(tmp_path: Path) -> Path:
    """A class-E synthetic .anno at a filename aadr-resolve infers as v66.0."""
    p = tmp_path / "synth_v66.0.anno"
    make_loschbour_v66_fixture(p)
    return p


def test_select_populations_writes_id_list(tmp_path: Path, v66_anno: Path) -> None:
    """select with populations: [Western_HG] emits the 3 Western_HG samples."""
    selector = tmp_path / "western.yaml"
    selector.write_text("populations: [Western_HG]\n", encoding="utf-8")
    out = tmp_path / "out.ids"

    result = _run_cli("select", str(selector), str(v66_anno), "-o", str(out))
    assert result.returncode == 0, result.stderr

    ids = out.read_text(encoding="utf-8").strip().splitlines()
    assert ids == ["Loschbour.AG", "Loschbour.DG", "Bichon"]


def test_select_individual_ids_captures_multi_row_iid(tmp_path: Path, v66_anno: Path) -> None:
    """individual_ids: [Loschbour] returns BOTH .AG and .DG rows (multi-
    library individual; HLD §within-version multi-row IIDs are normal)."""
    selector = tmp_path / "loschbour.yaml"
    selector.write_text("individual_ids: [Loschbour]\n", encoding="utf-8")
    out = tmp_path / "loschbour.ids"

    result = _run_cli("select", str(selector), str(v66_anno), "-o", str(out))
    assert result.returncode == 0, result.stderr

    ids = out.read_text(encoding="utf-8").strip().splitlines()
    assert ids == ["Loschbour.AG", "Loschbour.DG"]


def test_select_to_stdout_when_no_out(tmp_path: Path, v66_anno: Path) -> None:
    """Without -o, the ID list goes to stdout; the summary goes to stderr."""
    selector = tmp_path / "kotias.yaml"
    selector.write_text("individual_ids: [KO1]\n", encoding="utf-8")

    result = _run_cli("select", str(selector), str(v66_anno))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "KO1"
    # Summary on stderr.
    assert "Matched 1 samples" in result.stderr
    assert "Western_HG" not in result.stderr  # KO1 is Eastern_HG
    assert "Eastern_HG=1" in result.stderr


def test_select_zero_match_exit_1(tmp_path: Path, v66_anno: Path) -> None:
    """No matches → exit 1 (SoftValidationFailure); no output file written."""
    selector = tmp_path / "noop.yaml"
    selector.write_text("populations: [DoesNotExist]\n", encoding="utf-8")
    out = tmp_path / "should_not_exist.ids"

    result = _run_cli("select", str(selector), str(v66_anno), "-o", str(out))
    assert result.returncode == 1
    assert not out.exists()
    assert "matched 0 samples" in result.stderr.lower()


def test_select_zero_match_with_allow_empty(tmp_path: Path, v66_anno: Path) -> None:
    """--allow-empty downgrades zero-match exit 1 to exit 0; writes empty file."""
    selector = tmp_path / "noop.yaml"
    selector.write_text("populations: [DoesNotExist]\n", encoding="utf-8")
    out = tmp_path / "empty.ids"

    result = _run_cli("select", str(selector), str(v66_anno), "-o", str(out), "--allow-empty")
    assert result.returncode == 0, result.stderr
    assert out.exists()
    assert out.read_text(encoding="utf-8") == ""


def test_select_unsupported_feature_exits_4(tmp_path: Path, v66_anno: Path) -> None:
    """Features still in the feature gate (coverage_column as of Day 3,
    cross-version Day-6-pending) → exit 4 with constraint=feature_not_implemented."""
    selector = tmp_path / "covcol.yaml"
    selector.write_text(
        "populations: [Western_HG]\ncoverage_column: snps_hit_1240k\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.ids"

    result = _run_cli("select", str(selector), str(v66_anno), "-o", str(out))
    assert result.returncode == 4
    assert "not yet implemented" in result.stderr
    assert "feature_not_implemented" in result.stderr


def test_select_quiet_no_summary(tmp_path: Path, v66_anno: Path) -> None:
    """--quiet suppresses the stderr summary block."""
    selector = tmp_path / "ko1.yaml"
    selector.write_text("individual_ids: [KO1]\n", encoding="utf-8")

    result = _run_cli("--quiet", "select", str(selector), str(v66_anno))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "KO1"
    # stderr should be empty (or just contain non-summary warnings).
    assert "Matched" not in result.stderr
    assert "Done in" not in result.stderr


def test_select_unknown_schema_anno_exits_2(tmp_path: Path) -> None:
    """A non-AADR .anno (no matching schema signature) → exit 2 IOFailure."""
    bogus = tmp_path / "synth_v66.0.anno"
    bogus.write_text("not\ta\tvalid\theader\nbogus\trow\n", encoding="utf-8")
    selector = tmp_path / "any.yaml"
    selector.write_text("populations: [Western_HG]\n", encoding="utf-8")

    result = _run_cli("select", str(selector), str(bogus))
    assert result.returncode == 2
    # Error message should reference the schema-detection problem.
    assert "schema" in result.stderr.lower() or "unrecognized" in result.stderr.lower()


def test_select_help_lists_day2_flags() -> None:
    """`aadr-subset select --help` documents the Day-2 surface flags."""
    result = _run_cli("select", "--help")
    assert result.returncode == 0
    for flag in ("--out", "--schema-override", "--allow-empty", "--allow-empty-source"):
        assert flag in result.stdout


# --- Day 3 features via the CLI ---


def test_select_modern_only(tmp_path: Path, v66_anno: Path) -> None:
    """modern_only: true matches only the date<=70 samples (English.1 and .2)."""
    selector = tmp_path / "modern.yaml"
    selector.write_text("modern_only: true\n", encoding="utf-8")
    out = tmp_path / "modern.ids"
    result = _run_cli("select", str(selector), str(v66_anno), "-o", str(out))
    assert result.returncode == 0, result.stderr
    ids = out.read_text(encoding="utf-8").strip().splitlines()
    assert ids == ["English.1", "English.2"]


def test_select_date_range(tmp_path: Path, v66_anno: Path) -> None:
    """date.min_calbp + date.max_calbp filters to an interval."""
    selector = tmp_path / "iron_age.yaml"
    selector.write_text("date:\n  min_calbp: 7000\n  max_calbp: 9000\n", encoding="utf-8")
    out = tmp_path / "ia.ids"
    result = _run_cli("select", str(selector), str(v66_anno), "-o", str(out))
    assert result.returncode == 0, result.stderr
    ids = out.read_text(encoding="utf-8").strip().splitlines()
    # Loschbour x2 (8000) + KO1 (7700) match; Bichon (13700) and English (70) excluded.
    assert ids == ["Loschbour.AG", "Loschbour.DG", "KO1"]


def test_select_min_coverage(tmp_path: Path, v66_anno: Path) -> None:
    """min_coverage filters out NaN coverage and below-threshold samples."""
    selector = tmp_path / "covered.yaml"
    selector.write_text("min_coverage: 1.0\n", encoding="utf-8")
    out = tmp_path / "covered.ids"
    result = _run_cli("select", str(selector), str(v66_anno), "-o", str(out))
    assert result.returncode == 0, result.stderr
    ids = out.read_text(encoding="utf-8").strip().splitlines()
    # coverage: Losch.AG=1.21, Losch.DG=0.78, Bichon=0.82, KO1=2.40, English=NaN.
    # Threshold 1.0 → Losch.AG + KO1.
    assert ids == ["Loschbour.AG", "KO1"]


def test_select_any_block_three_branches(tmp_path: Path, v66_anno: Path) -> None:
    """HLD test 4: any: with 3 branches produces the union, deduped."""
    selector = tmp_path / "anyblk.yaml"
    selector.write_text(
        "any:\n  - populations: [Western_HG]\n  - individual_ids: [KO1]\n  - modern_only: true\n",
        encoding="utf-8",
    )
    out = tmp_path / "any.ids"
    result = _run_cli("select", str(selector), str(v66_anno), "-o", str(out))
    assert result.returncode == 0, result.stderr
    ids = out.read_text(encoding="utf-8").strip().splitlines()
    # Western_HG (3) ∪ KO1 (1) ∪ modern (2) = 6 (everyone).
    assert ids == [
        "Loschbour.AG",
        "Loschbour.DG",
        "Bichon",
        "KO1",
        "English.1",
        "English.2",
    ]


def test_select_exclude_drops_named_groups(tmp_path: Path, v66_anno: Path) -> None:
    """HLD test 5: exclude.group_ids drops the named populations."""
    selector = tmp_path / "ex.yaml"
    selector.write_text(
        "populations: [Western_HG, Eastern_HG]\nexclude:\n  group_ids: [Eastern_HG]\n",
        encoding="utf-8",
    )
    out = tmp_path / "ex.ids"
    result = _run_cli("select", str(selector), str(v66_anno), "-o", str(out))
    assert result.returncode == 0, result.stderr
    ids = out.read_text(encoding="utf-8").strip().splitlines()
    assert ids == ["Loschbour.AG", "Loschbour.DG", "Bichon"]


def test_select_complex_and_or_not(tmp_path: Path, v66_anno: Path) -> None:
    """Compound: populations + date AND-block, OR any:, NOT exclude."""
    selector = tmp_path / "complex.yaml"
    selector.write_text(
        "populations: [Western_HG, Eastern_HG]\n"
        "date:\n  min_calbp: 5000\n"
        "any:\n"
        "  - min_coverage: 0.5\n"
        "exclude:\n"
        "  individual_ids: [Bichon]\n",
        encoding="utf-8",
    )
    out = tmp_path / "complex.ids"
    result = _run_cli("select", str(selector), str(v66_anno), "-o", str(out))
    assert result.returncode == 0, result.stderr
    ids = out.read_text(encoding="utf-8").strip().splitlines()
    # Western+Eastern AND date>=5000 AND (any: cov>=0.5) AND NOT Bichon
    # Eligible after top-AND: Losch.AG, Losch.DG, Bichon-row, KO1, Eng* not eligible.
    # any: cov>=0.5: Losch.AG (1.21), Losch.DG (0.78), Bichon-row (0.82), KO1 (2.40).
    # exclude Bichon individual: drops Bichon-row.
    # Final: Losch.AG, Losch.DG, KO1.
    assert ids == ["Loschbour.AG", "Loschbour.DG", "KO1"]


def test_select_unsupported_coverage_column_still_blocked(tmp_path: Path, v66_anno: Path) -> None:
    """coverage_column: still in Day-3 feature gate pending CLI flag."""
    selector = tmp_path / "cc.yaml"
    selector.write_text(
        "populations: [Western_HG]\ncoverage_column: snps_hit_1240k\n",
        encoding="utf-8",
    )
    result = _run_cli("select", str(selector), str(v66_anno))
    assert result.returncode == 4
    assert "feature_not_implemented" in result.stderr
    assert "coverage_column" in result.stderr


# --- v62 class-D coverage warning (HLD §Coverage handling) ---


@pytest.fixture
def v62_anno(tmp_path: Path) -> Path:
    """Class-D synthetic .anno at a v62-recognizable filename."""
    p = tmp_path / "synth_v62.0_HO.anno"
    make_v62_class_d_fixture(p)
    return p


def test_select_v62_min_coverage_emits_warning(tmp_path: Path, v62_anno: Path) -> None:
    """v62 input + min_coverage in selector → stderr WARNING about no native
    coverage column. Engine still runs; min_coverage just selects zero rows."""
    selector = tmp_path / "v62.yaml"
    selector.write_text("min_coverage: 0.5\n", encoding="utf-8")
    out = tmp_path / "v62.ids"
    result = _run_cli("select", str(selector), str(v62_anno), "-o", str(out), "--allow-empty")
    assert result.returncode == 0, result.stderr
    assert "v62.0 input has no native coverage column" in result.stderr
    # min_coverage filtered out everything (no native coverage).
    assert out.read_text(encoding="utf-8") == ""


def test_select_v62_without_coverage_no_warning(tmp_path: Path, v62_anno: Path) -> None:
    """v62 input WITHOUT min_coverage → no warning."""
    selector = tmp_path / "noco.yaml"
    selector.write_text("populations: [Western_HG]\n", encoding="utf-8")
    out = tmp_path / "noco.ids"
    result = _run_cli("select", str(selector), str(v62_anno), "-o", str(out))
    assert result.returncode == 0, result.stderr
    assert "no native coverage" not in result.stderr
