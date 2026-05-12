"""Integration tests for `aadr-subset inspect`.

Day 4 surface: diagnostic dry-run; always exits 0; prints summary to
STDOUT (not stderr). No file output.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.fixtures.synthesize import make_loschbour_v66_fixture


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "aadr_subset", *args],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def v66_anno(tmp_path: Path) -> Path:
    p = tmp_path / "synth_v66.0.anno"
    make_loschbour_v66_fixture(p)
    return p


def test_inspect_basic(tmp_path: Path, v66_anno: Path) -> None:
    """inspect prints the summary to stdout; exit 0."""
    selector = tmp_path / "western.yaml"
    selector.write_text("populations: [Western_HG]\n", encoding="utf-8")
    result = _run_cli("inspect", str(selector), str(v66_anno))
    assert result.returncode == 0, result.stderr
    assert "Matched: 3 samples across 1 population" in result.stdout
    assert "Per-population breakdown:" in result.stdout
    assert "Western_HG" in result.stdout


def test_inspect_zero_matches_exit_0(tmp_path: Path, v66_anno: Path) -> None:
    """inspect always exits 0, even on zero matches."""
    selector = tmp_path / "noop.yaml"
    selector.write_text("populations: [DoesNotExist]\n", encoding="utf-8")
    result = _run_cli("inspect", str(selector), str(v66_anno))
    assert result.returncode == 0
    assert "Matched: 0 samples" in result.stdout


def test_inspect_complex_selector(tmp_path: Path, v66_anno: Path) -> None:
    """Compound selector with any:/exclude:/date — full summary populated."""
    selector = tmp_path / "compound.yaml"
    selector.write_text(
        "populations: [Western_HG, Eastern_HG]\n"
        "date:\n  min_calbp: 5000\n"
        "exclude:\n"
        "  individual_ids: [Bichon]\n",
        encoding="utf-8",
    )
    result = _run_cli("inspect", str(selector), str(v66_anno))
    assert result.returncode == 0, result.stderr
    # Loschbour.AG, Loschbour.DG (Western_HG, 8000), KO1 (Eastern_HG, 7700).
    # Bichon excluded.
    assert "Matched: 3 samples across 2 populations" in result.stdout
    assert "Excluded:" in result.stdout
    assert "individual_ids: Bichon" in result.stdout
    assert "Date range of matched:" in result.stdout
    assert "Coverage range:" in result.stdout


def test_inspect_quiet_silences_summary(tmp_path: Path, v66_anno: Path) -> None:
    """--quiet suppresses the inspect summary."""
    selector = tmp_path / "western.yaml"
    selector.write_text("populations: [Western_HG]\n", encoding="utf-8")
    result = _run_cli("--quiet", "inspect", str(selector), str(v66_anno))
    assert result.returncode == 0
    assert result.stdout == ""


def test_inspect_help_documents_flags() -> None:
    """`aadr-subset inspect --help` documents the Day-4 surface flags."""
    result = _run_cli("inspect", "--help")
    assert result.returncode == 0
    for flag in ("--schema-override", "--allow-empty-source", "--strict-resolve"):
        assert flag in result.stdout


def test_inspect_schema_unknown_exits_2(tmp_path: Path) -> None:
    """A non-AADR .anno → exit 2 IOFailure (same as select)."""
    bogus = tmp_path / "synth_v66.0.anno"
    bogus.write_text("not\ta\tvalid\theader\nbogus\trow\n", encoding="utf-8")
    selector = tmp_path / "any.yaml"
    selector.write_text("populations: [Western_HG]\n", encoding="utf-8")
    result = _run_cli("inspect", str(selector), str(bogus))
    assert result.returncode == 2
