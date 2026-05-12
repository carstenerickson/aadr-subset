"""Integration tests for `aadr-subset report`.

Day 5 surface: per-population aggregates; TSV or JSON; --include-empty-groups
flag; same zero-match exit-1 gate as select unless --allow-empty.
"""

from __future__ import annotations

import json
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


def test_report_tsv_basic(tmp_path: Path, v66_anno: Path) -> None:
    """report --format tsv writes the 7-column TSV; exit 0; one-liner stderr."""
    selector = tmp_path / "western.yaml"
    selector.write_text("populations: [Western_HG]\n", encoding="utf-8")
    out = tmp_path / "report.tsv"
    result = _run_cli("report", str(selector), str(v66_anno), "-o", str(out))
    assert result.returncode == 0, result.stderr
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("group_id\t")
    # Header + 1 data row.
    assert len(lines) == 2
    assert lines[1].split("\t")[0] == "Western_HG"
    assert lines[1].split("\t")[1] == "3"
    # Stderr one-liner.
    assert "Wrote" in result.stderr
    assert "1 population" in result.stderr


def test_report_json_basic(tmp_path: Path, v66_anno: Path) -> None:
    selector = tmp_path / "western.yaml"
    selector.write_text("populations: [Western_HG]\n", encoding="utf-8")
    out = tmp_path / "report.json"
    result = _run_cli("report", str(selector), str(v66_anno), "--format", "json", "-o", str(out))
    assert result.returncode == 0, result.stderr
    parsed = json.loads(out.read_text(encoding="utf-8"))
    # Filename-inferred version is "synth_v66.0" when no --version-label.
    assert "v66.0" in parsed["anno_version"]
    assert parsed["selector_signature"].startswith("sha256:")
    assert len(parsed["populations"]) == 1
    assert parsed["populations"][0]["group_id"] == "Western_HG"
    assert parsed["populations"][0]["pct_matched"] == 1.0


def test_report_tsv_to_stdout(tmp_path: Path, v66_anno: Path) -> None:
    selector = tmp_path / "western.yaml"
    selector.write_text("populations: [Western_HG]\n", encoding="utf-8")
    result = _run_cli("report", str(selector), str(v66_anno))
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("group_id\t")
    assert "Western_HG\t3" in result.stdout


def test_report_zero_matches_exits_1(tmp_path: Path, v66_anno: Path) -> None:
    selector = tmp_path / "noop.yaml"
    selector.write_text("populations: [DoesNotExist]\n", encoding="utf-8")
    result = _run_cli("report", str(selector), str(v66_anno))
    assert result.returncode == 1
    assert "0 samples" in result.stderr or "matched 0" in result.stderr


def test_report_allow_empty_exits_0(tmp_path: Path, v66_anno: Path) -> None:
    selector = tmp_path / "noop.yaml"
    selector.write_text("populations: [DoesNotExist]\n", encoding="utf-8")
    out = tmp_path / "empty.tsv"
    result = _run_cli("report", str(selector), str(v66_anno), "--allow-empty", "-o", str(out))
    assert result.returncode == 0, result.stderr
    # Header only.
    assert out.read_text(encoding="utf-8").strip().split("\n")[0].startswith("group_id\t")


def test_report_include_empty_groups(tmp_path: Path, v66_anno: Path) -> None:
    selector = tmp_path / "western.yaml"
    selector.write_text("populations: [Western_HG]\n", encoding="utf-8")
    out = tmp_path / "r.tsv"
    result = _run_cli(
        "report",
        str(selector),
        str(v66_anno),
        "--include-empty-groups",
        "-o",
        str(out),
    )
    assert result.returncode == 0, result.stderr
    lines = out.read_text(encoding="utf-8").splitlines()
    # Header + Western_HG + Eastern_HG + English.SG.
    assert len(lines) == 4


def test_report_help_documents_flags() -> None:
    result = _run_cli("report", "--help")
    assert result.returncode == 0
    assert "--format" in result.stdout
    assert "--include-empty-groups" in result.stdout
    assert "--allow-empty" in result.stdout
