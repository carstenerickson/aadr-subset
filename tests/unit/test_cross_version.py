"""Cross-version IID lift tests (Day 6).

Exercises the engine + run_select path that goes through
aadr_resolve.resolve_master_ids and lifts source Individual_IDs to
target Individual_IDs.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import aadr_resolve
import pytest

from aadr_subset.engine import select_samples
from aadr_subset.errors import InvariantViolation, SoftValidationFailure
from aadr_subset.selector import load_selector
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
def v62_v66_pair(tmp_path: Path) -> tuple[Path, Path]:
    """Synthetic v62.0 (class D) + v66.0 (class E) anno pair sharing
    Loschbour / KO1 / Bichon individuals. Filenames chosen so
    aadr-resolve's Path.stem inference yields clean `v62.0` / `v66.0`
    labels without needing --version-label."""
    src = tmp_path / "v62.0.anno"
    dst = tmp_path / "v66.0.anno"
    make_v62_class_d_fixture(src)
    make_loschbour_v66_fixture(dst)
    return src, dst


# --- engine.select_samples direct ---


def test_engine_cross_version_lifts_iids(tmp_path: Path, v62_v66_pair: tuple[Path, Path]) -> None:
    """Selector with v62.0→v66.0 lift maps Loschbour to v66 Loschbour rows."""
    src, dst = v62_v66_pair
    src_af = aadr_resolve.AnnoFrame.from_path(src, version_label="v62.0")
    dst_af = aadr_resolve.AnnoFrame.from_path(dst, version_label="v66.0")
    sel = tmp_path / "s.yaml"
    sel.write_text(
        "individual_ids: [Loschbour]\nsource_version: v62.0\nresolve_to_version: v66.0\n",
        encoding="utf-8",
    )
    _, selector = load_selector(sel)
    result = select_samples(dst_af, selector, source_anno=src_af)
    # Both Loschbour rows (Loschbour.AG + Loschbour.DG) match in v66.
    assert sorted(result.genetic_ids) == ["Loschbour.AG", "Loschbour.DG"]
    assert result.n_matched == 2
    assert result.warnings.missing_after_resolve == []


def test_engine_cross_version_missing_iid_reported(
    tmp_path: Path, v62_v66_pair: tuple[Path, Path]
) -> None:
    """An IID not in source .anno surfaces in warnings.missing_after_resolve."""
    src, dst = v62_v66_pair
    src_af = aadr_resolve.AnnoFrame.from_path(src, version_label="v62.0")
    dst_af = aadr_resolve.AnnoFrame.from_path(dst, version_label="v66.0")
    sel = tmp_path / "s.yaml"
    sel.write_text(
        "individual_ids: [Loschbour, NotPresent]\nsource_version: v62.0\n"
        "resolve_to_version: v66.0\n",
        encoding="utf-8",
    )
    _, selector = load_selector(sel)
    result = select_samples(dst_af, selector, source_anno=src_af)
    assert result.warnings.missing_after_resolve == ["NotPresent"]
    # Loschbour still resolves, so n_matched > 0.
    assert result.n_matched == 2


def test_engine_strict_resolve_raises_soft_validation(
    tmp_path: Path, v62_v66_pair: tuple[Path, Path]
) -> None:
    """strict_resolve=True + missing IID → SoftValidationFailure (exit 1)."""
    src, dst = v62_v66_pair
    src_af = aadr_resolve.AnnoFrame.from_path(src, version_label="v62.0")
    dst_af = aadr_resolve.AnnoFrame.from_path(dst, version_label="v66.0")
    sel = tmp_path / "s.yaml"
    sel.write_text(
        "individual_ids: [Loschbour, NotPresent]\nsource_version: v62.0\n"
        "resolve_to_version: v66.0\n",
        encoding="utf-8",
    )
    _, selector = load_selector(sel)
    with pytest.raises(SoftValidationFailure):
        select_samples(dst_af, selector, source_anno=src_af, strict_resolve=True)


def test_engine_cross_version_requires_source_anno(
    tmp_path: Path, v62_v66_pair: tuple[Path, Path]
) -> None:
    """resolve_to_version: set + source_anno=None → InvariantViolation.
    (run_select catches this earlier as a UsageError; engine guard is
    defensive for the rare case where engine is called directly.)"""
    _, dst = v62_v66_pair
    dst_af = aadr_resolve.AnnoFrame.from_path(dst, version_label="v66.0")
    sel = tmp_path / "s.yaml"
    sel.write_text(
        "individual_ids: [Loschbour]\nsource_version: v62.0\nresolve_to_version: v66.0\n",
        encoding="utf-8",
    )
    _, selector = load_selector(sel)
    with pytest.raises(InvariantViolation):
        select_samples(dst_af, selector, source_anno=None)


# --- CLI run_select ---


def test_cli_cross_version_basic(tmp_path: Path, v62_v66_pair: tuple[Path, Path]) -> None:
    src, dst = v62_v66_pair
    sel = tmp_path / "s.yaml"
    sel.write_text(
        "individual_ids: [Loschbour, KO1]\nsource_version: v62.0\nresolve_to_version: v66.0\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.txt"
    result = _run_cli(
        "select",
        str(sel),
        str(dst),
        "--source-anno",
        str(src),
        "-o",
        str(out),
    )
    assert result.returncode == 0, result.stderr
    ids = sorted(out.read_text(encoding="utf-8").strip().splitlines())
    assert ids == ["KO1", "Loschbour.AG", "Loschbour.DG"]


def test_cli_cross_version_json_records_signature(
    tmp_path: Path, v62_v66_pair: tuple[Path, Path]
) -> None:
    """JSON output carries the selector_signature + anno versions."""
    src, dst = v62_v66_pair
    sel = tmp_path / "s.yaml"
    sel.write_text(
        "individual_ids: [Loschbour]\nsource_version: v62.0\nresolve_to_version: v66.0\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.json"
    result = _run_cli(
        "select",
        str(sel),
        str(dst),
        "--source-anno",
        str(src),
        "--format",
        "json",
        "-o",
        str(out),
    )
    assert result.returncode == 0, result.stderr
    parsed = json.loads(out.read_text(encoding="utf-8"))
    assert parsed["selector_signature"].startswith("sha256:")
    assert "v66.0" in parsed["anno_version"]
    assert parsed["n_matched"] == 2


def test_cli_source_anno_without_resolve_to_version_errors(
    tmp_path: Path, v62_v66_pair: tuple[Path, Path]
) -> None:
    """--source-anno + selector without resolve_to_version: → UsageError."""
    src, dst = v62_v66_pair
    sel = tmp_path / "s.yaml"
    sel.write_text("populations: [Western_HG]\n", encoding="utf-8")
    result = _run_cli("select", str(sel), str(dst), "--source-anno", str(src))
    # UsageError → exit 4 (HLD §Exit codes).
    assert result.returncode == 4
    assert "meaningful only with cross-version" in result.stderr


def test_cli_resolve_to_version_without_source_anno_errors(
    tmp_path: Path, v62_v66_pair: tuple[Path, Path]
) -> None:
    """selector.resolve_to_version + no --source-anno → UsageError."""
    _, dst = v62_v66_pair
    sel = tmp_path / "s.yaml"
    sel.write_text(
        "individual_ids: [Loschbour]\nsource_version: v62.0\nresolve_to_version: v66.0\n",
        encoding="utf-8",
    )
    result = _run_cli("select", str(sel), str(dst))
    assert result.returncode == 4
    assert "--source-anno was not provided" in result.stderr


def test_cli_target_version_mismatch_errors(
    tmp_path: Path, v62_v66_pair: tuple[Path, Path]
) -> None:
    """target .anno version != selector.resolve_to_version → UsageError."""
    src, dst = v62_v66_pair
    sel = tmp_path / "s.yaml"
    sel.write_text(
        "individual_ids: [Loschbour]\nsource_version: v62.0\nresolve_to_version: v54.1\n",
        encoding="utf-8",
    )
    result = _run_cli("select", str(sel), str(dst), "--source-anno", str(src))
    assert result.returncode == 4
    assert "resolve_to_version" in result.stderr


def test_cli_missing_iid_warns_non_strict(tmp_path: Path, v62_v66_pair: tuple[Path, Path]) -> None:
    """Default (non-strict): missing IIDs surface as a stderr WARNING; exit 0."""
    src, dst = v62_v66_pair
    sel = tmp_path / "s.yaml"
    sel.write_text(
        "individual_ids: [Loschbour, NotPresent]\nsource_version: v62.0\n"
        "resolve_to_version: v66.0\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.txt"
    result = _run_cli(
        "select",
        str(sel),
        str(dst),
        "--source-anno",
        str(src),
        "-o",
        str(out),
    )
    assert result.returncode == 0
    assert "WARNING" in result.stderr
    assert "NotPresent" in result.stderr


def test_cli_missing_iid_strict_exits_1(tmp_path: Path, v62_v66_pair: tuple[Path, Path]) -> None:
    """--strict-resolve + missing IID → exit 1 (SoftValidationFailure)."""
    src, dst = v62_v66_pair
    sel = tmp_path / "s.yaml"
    sel.write_text(
        "individual_ids: [Loschbour, NotPresent]\nsource_version: v62.0\n"
        "resolve_to_version: v66.0\n",
        encoding="utf-8",
    )
    result = _run_cli(
        "select",
        str(sel),
        str(dst),
        "--source-anno",
        str(src),
        "--strict-resolve",
    )
    assert result.returncode == 1
    assert "NotPresent" in result.stderr


def test_cli_help_documents_cross_version_flags() -> None:
    result = _run_cli("select", "--help")
    assert result.returncode == 0
    assert "--source-anno" in result.stdout
    assert "--mid-bridge" in result.stdout
    assert "--strict-resolve" in result.stdout
