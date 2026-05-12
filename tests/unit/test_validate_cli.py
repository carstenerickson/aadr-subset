"""Integration test for the `aadr-subset validate` CLI surface.

Subprocess-based: invokes the real CLI to catch packaging / wiring issues
that direct run_validate calls would miss (e.g., entry-point registration).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "aadr_subset", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_validate_exits_0_on_valid_selector(selector_dir: Path) -> None:
    """validate exits 0 on a well-formed selector."""
    sel = selector_dir / "ok.yaml"
    sel.write_text("populations: [English.SG]\n", encoding="utf-8")
    result = _run_cli("validate", str(sel))
    assert result.returncode == 0, result.stderr
    assert "OK:" in result.stdout


def test_validate_exits_4_on_schema_violation(selector_dir: Path) -> None:
    """validate exits 4 on a schema violation, with formatted error to stderr."""
    sel = selector_dir / "bad.yaml"
    sel.write_text("populations: [1, 2, 3]\n", encoding="utf-8")
    result = _run_cli("validate", str(sel))
    assert result.returncode == 4
    assert "at /populations" in result.stderr
    # Error format includes file:line:col
    assert str(sel) in result.stderr


def test_validate_exits_4_on_semantic_violation(selector_dir: Path) -> None:
    """validate exits 4 on date_range_inverted (semantic constraint)."""
    sel = selector_dir / "semantic.yaml"
    sel.write_text("date:\n  min_calbp: 2800\n  max_calbp: 2200\n", encoding="utf-8")
    result = _run_cli("validate", str(sel))
    assert result.returncode == 4
    assert "date_range_inverted" in result.stderr


def test_validate_collects_all_errors_in_one_run(selector_dir: Path) -> None:
    """validate accumulates errors across schema + semantic phases."""
    sel = selector_dir / "multi.yaml"
    sel.write_text(
        "populations: [42]\nmin_coverage: -1\ndate:\n  min_calbp: 2800\n  max_calbp: 2200\n",
        encoding="utf-8",
    )
    result = _run_cli("validate", str(sel))
    assert result.returncode == 4
    # At least 3 errors on separate lines in stderr.
    error_lines = [line for line in result.stderr.strip().splitlines() if "at /" in line]
    assert len(error_lines) >= 3


def test_validate_quiet_suppresses_ok_message(selector_dir: Path) -> None:
    """--quiet suppresses the 'OK:' line on success."""
    sel = selector_dir / "ok.yaml"
    sel.write_text("populations: [English.SG]\n", encoding="utf-8")
    result = _run_cli("--quiet", "validate", str(sel))
    assert result.returncode == 0
    assert result.stdout == ""


def test_version_flag_reports_aadr_subset() -> None:
    """--version prints aadr-subset version (and aadr-resolve placeholder)."""
    result = _run_cli("--version")
    assert result.returncode == 0
    assert "aadr-subset" in result.stdout
    # aadr-resolve line present (either with version or "not installed")
    assert "aadr-resolve" in result.stdout
