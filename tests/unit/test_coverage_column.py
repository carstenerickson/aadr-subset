"""Coverage column override tests (Day 7).

Exercises:
- engine: af.coverage_via routing when coverage_column kwarg is set
- engine: per-branch coverage_column wins over top-level
- engine: MissingNativeFieldError → IOFailure
- run_select: --coverage-column / --coverage-derive mutex
- run_select: selector.coverage_column wins over CLI; signature reflects
  the effective value
- v62 class-D warning suppressed when override is supplied
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import aadr_resolve
import pytest

from aadr_subset.engine import select_samples
from aadr_subset.errors import IOFailure
from aadr_subset.selector import compute_signature, load_selector
from tests.fixtures.synthesize import (
    make_loschbour_v66_fixture,
    make_v62_class_d_fixture,
)


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "aadr_subset", *args],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def v62_anno(tmp_path: Path) -> Path:
    p = tmp_path / "v62.0.anno"
    make_v62_class_d_fixture(p)
    return p


@pytest.fixture
def v66_anno(tmp_path: Path) -> Path:
    p = tmp_path / "v66.0.anno"
    make_loschbour_v66_fixture(p)
    return p


# --- Engine direct ---


def test_engine_coverage_via_class_d_proxy(tmp_path: Path, v62_anno: Path) -> None:
    """Class-D (v62.0) + coverage_column='snps_hit_1240k' routes through
    af.coverage_via, producing a proxy mask."""
    af = aadr_resolve.AnnoFrame.from_path(v62_anno)
    sel_path = tmp_path / "s.yaml"
    sel_path.write_text("populations: [Western_HG]\nmin_coverage: 0.85\n", encoding="utf-8")
    _, selector = load_selector(sel_path)
    # Without override: class D has no native coverage → 0 matches.
    r_no = select_samples(af, selector)
    assert r_no.n_matched == 0
    # With override: snps_hit_1240k proxy. From fixture (1.148M total):
    #   I0001.AG: 1,120,000 / 1,148,000 ≈ 0.976 (pass)
    #   Loschbour.DG: 987,000 / 1,148,000 ≈ 0.860 (pass)
    #   Bichon: 965,000 / 1,148,000 ≈ 0.841 (FAIL @ 0.85)
    r_yes = select_samples(af, selector, coverage_column="snps_hit_1240k")
    assert sorted(r_yes.genetic_ids) == ["I0001.AG", "Loschbour.DG"]


def test_engine_missing_native_field_maps_to_io_failure(v62_anno: Path) -> None:
    """Class-D + asking for the native coverage column → IOFailure."""
    af = aadr_resolve.AnnoFrame.from_path(v62_anno)
    from aadr_subset.types import Selector

    selector = Selector(populations=["Western_HG"], min_coverage=0.5)
    with pytest.raises(IOFailure) as exc:
        select_samples(af, selector, coverage_column="coverage_1240k_native")
    assert "coverage_1240k_native" in str(exc.value)


def test_engine_branch_coverage_column_wins_over_top(tmp_path: Path, v62_anno: Path) -> None:
    """A branch with its own coverage_column overrides the top-level effective value."""
    af = aadr_resolve.AnnoFrame.from_path(v62_anno)
    sel_path = tmp_path / "s.yaml"
    sel_path.write_text(
        # Top-level filter empty; any branch matches with snps_hit_1240k proxy.
        "any:\n"
        "  - populations: [Western_HG]\n"
        "    min_coverage: 0.95\n"
        "    coverage_column: snps_hit_1240k\n",
        encoding="utf-8",
    )
    _, selector = load_selector(sel_path)
    r = select_samples(af, selector)
    # Only I0001.AG (0.976) passes 0.95.
    assert sorted(r.genetic_ids) == ["I0001.AG"]


# --- compute_signature ---


def test_signature_selector_coverage_column_wins_over_cli(tmp_path: Path) -> None:
    """Selector.coverage_column set → CLI value ignored in signature."""
    sel_path = tmp_path / "s.yaml"
    sel_path.write_text(
        "populations: [Western_HG]\ncoverage_column: snps_hit_1240k\n",
        encoding="utf-8",
    )
    _, sel = load_selector(sel_path)
    a = compute_signature(sel, cli_coverage_column=None)
    b = compute_signature(sel, cli_coverage_column="different_value")
    assert a == b


# --- CLI integration ---


def test_cli_coverage_column_class_d(tmp_path: Path, v62_anno: Path) -> None:
    """--coverage-column snps_hit_1240k on class-D yields matched samples."""
    sel = tmp_path / "s.yaml"
    sel.write_text("populations: [Western_HG]\nmin_coverage: 0.85\n", encoding="utf-8")
    out = tmp_path / "out.txt"
    result = _run_cli(
        "select",
        str(sel),
        str(v62_anno),
        "--coverage-column",
        "snps_hit_1240k",
        "-o",
        str(out),
    )
    assert result.returncode == 0, result.stderr
    ids = sorted(out.read_text(encoding="utf-8").strip().splitlines())
    assert ids == ["I0001.AG", "Loschbour.DG"]


def test_cli_coverage_derive_is_alias(tmp_path: Path, v62_anno: Path) -> None:
    """--coverage-derive behaves identically to --coverage-column."""
    sel = tmp_path / "s.yaml"
    sel.write_text("populations: [Western_HG]\nmin_coverage: 0.85\n", encoding="utf-8")
    out = tmp_path / "out.txt"
    result = _run_cli(
        "select",
        str(sel),
        str(v62_anno),
        "--coverage-derive",
        "snps_hit_1240k",
        "-o",
        str(out),
    )
    assert result.returncode == 0, result.stderr
    ids = sorted(out.read_text(encoding="utf-8").strip().splitlines())
    assert ids == ["I0001.AG", "Loschbour.DG"]


def test_cli_both_coverage_flags_set_errors(tmp_path: Path, v62_anno: Path) -> None:
    """--coverage-column + --coverage-derive both set → UsageError exit 4."""
    sel = tmp_path / "s.yaml"
    sel.write_text("populations: [Western_HG]\nmin_coverage: 0.5\n", encoding="utf-8")
    result = _run_cli(
        "select",
        str(sel),
        str(v62_anno),
        "--coverage-column",
        "snps_hit_1240k",
        "--coverage-derive",
        "snps_hit_1240k",
    )
    assert result.returncode == 4
    assert "aliases" in result.stderr


def test_cli_v62_warning_suppressed_when_override(tmp_path: Path, v62_anno: Path) -> None:
    """The class-D coverage warning fires WITHOUT an override and is
    silenced once --coverage-column is supplied."""
    sel = tmp_path / "s.yaml"
    sel.write_text("populations: [Western_HG]\nmin_coverage: 0.5\n", encoding="utf-8")

    # No override → warning.
    result_no = _run_cli("select", str(sel), str(v62_anno), "--allow-empty")
    assert "v62.0 input has no native coverage column" in result_no.stderr

    # With override → no warning.
    result_yes = _run_cli(
        "select",
        str(sel),
        str(v62_anno),
        "--coverage-column",
        "snps_hit_1240k",
    )
    assert "v62.0 input has no native coverage column" not in result_yes.stderr


def test_cli_coverage_column_in_json_output(tmp_path: Path, v62_anno: Path) -> None:
    """coverage_column JSON top-level key reflects the effective value."""
    sel = tmp_path / "s.yaml"
    sel.write_text("populations: [Western_HG]\nmin_coverage: 0.85\n", encoding="utf-8")
    out = tmp_path / "out.json"
    result = _run_cli(
        "select",
        str(sel),
        str(v62_anno),
        "--coverage-column",
        "snps_hit_1240k",
        "--format",
        "json",
        "-o",
        str(out),
    )
    assert result.returncode == 0, result.stderr
    parsed = json.loads(out.read_text(encoding="utf-8"))
    assert parsed["coverage_column"] == "snps_hit_1240k"


def test_cli_selector_coverage_column_wins_over_cli(tmp_path: Path, v66_anno: Path) -> None:
    """selector.coverage_column: in YAML overrides --coverage-column from CLI."""
    sel = tmp_path / "s.yaml"
    sel.write_text(
        "populations: [Western_HG]\nmin_coverage: 0.5\ncoverage_column: coverage_1240k\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.json"
    # CLI passes a different valid column but selector should win.
    result = _run_cli(
        "select",
        str(sel),
        str(v66_anno),
        "--coverage-column",
        "snps_hit_1240k",
        "--format",
        "json",
        "-o",
        str(out),
    )
    assert result.returncode == 0, result.stderr
    parsed = json.loads(out.read_text(encoding="utf-8"))
    assert parsed["coverage_column"] == "coverage_1240k"


def test_cli_help_documents_coverage_flags() -> None:
    result = _run_cli("select", "--help")
    assert result.returncode == 0
    assert "--coverage-column" in result.stdout
    assert "--coverage-derive" in result.stdout
