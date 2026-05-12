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

from tests.fixtures.synthesize import make_loschbour_v66_fixture


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
    """Day-3+ features (date:, any:, etc.) → exit 4 with constraint=
    feature_not_implemented."""
    selector = tmp_path / "date.yaml"
    selector.write_text("populations: [Western_HG]\ndate:\n  min_calbp: 5000\n", encoding="utf-8")
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
