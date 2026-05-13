"""Unit tests for selector.compute_signature (RFC 8785 JCS over selector intent)."""

from __future__ import annotations

from pathlib import Path

from aadr_subset.selector import compute_signature, load_selector


def _write(p: Path, body: str) -> Path:
    p.write_text(body, encoding="utf-8")
    return p


def test_signature_is_sha256_prefixed_hex(tmp_path: Path) -> None:
    sel = _write(tmp_path / "s.yaml", "populations:\n  - Western_HG\n")
    _, selector = load_selector(sel)
    sig = compute_signature(selector, cli_coverage_column=None)
    assert sig.startswith("sha256:")
    assert len(sig) == len("sha256:") + 64
    # All hex chars.
    int(sig[len("sha256:") :], 16)


def test_signature_invariant_to_key_order(tmp_path: Path) -> None:
    """YAML key reordering must not change the signature."""
    a = _write(
        tmp_path / "a.yaml",
        "populations: [Western_HG, Eastern_HG]\nmin_coverage: 1.0\n",
    )
    b = _write(
        tmp_path / "b.yaml",
        "min_coverage: 1.0\npopulations: [Western_HG, Eastern_HG]\n",
    )
    _, sa = load_selector(a)
    _, sb = load_selector(b)
    assert compute_signature(sa, cli_coverage_column=None) == compute_signature(
        sb, cli_coverage_column=None
    )


def test_signature_invariant_to_population_order(tmp_path: Path) -> None:
    """populations is set-like: reordering must not change the signature."""
    a = _write(tmp_path / "a.yaml", "populations: [Western_HG, Eastern_HG]\n")
    b = _write(tmp_path / "b.yaml", "populations: [Eastern_HG, Western_HG]\n")
    _, sa = load_selector(a)
    _, sb = load_selector(b)
    assert compute_signature(sa, cli_coverage_column=None) == compute_signature(
        sb, cli_coverage_column=None
    )


def test_signature_invariant_to_duplicate_populations(tmp_path: Path) -> None:
    """populations dedup: [A, B, A] equals [A, B] for signature purposes."""
    a = _write(tmp_path / "a.yaml", "populations: [Western_HG, Eastern_HG]\n")
    b = _write(tmp_path / "b.yaml", "populations: [Western_HG, Eastern_HG, Western_HG]\n")
    _, sa = load_selector(a)
    _, sb = load_selector(b)
    assert compute_signature(sa, cli_coverage_column=None) == compute_signature(
        sb, cli_coverage_column=None
    )


def test_signature_changes_with_filter_change(tmp_path: Path) -> None:
    a = _write(tmp_path / "a.yaml", "populations: [Western_HG]\nmin_coverage: 1.0\n")
    b = _write(tmp_path / "b.yaml", "populations: [Western_HG]\nmin_coverage: 2.0\n")
    _, sa = load_selector(a)
    _, sb = load_selector(b)
    assert compute_signature(sa, cli_coverage_column=None) != compute_signature(
        sb, cli_coverage_column=None
    )


def test_signature_individual_ids_union_from_source(tmp_path: Path) -> None:
    """individual_ids = union of YAML-inlined and individual_ids_from_source.
    The union forms the signature regardless of where the IDs came from."""
    src = _write(tmp_path / "ids.txt", "I1\nI2\n")
    a = _write(
        tmp_path / "a.yaml",
        "individual_ids: [I1, I2]\n",
    )
    b = _write(
        tmp_path / "b.yaml",
        f"individual_ids_source: {src.name}\n",
    )
    _, sa = load_selector(a)
    _, sb = load_selector(b)
    assert compute_signature(sa, cli_coverage_column=None) == compute_signature(
        sb, cli_coverage_column=None
    )


def test_signature_branch_individual_ids_union_from_source(tmp_path: Path) -> None:
    """v0.2: branch individual_ids_source contributes the same way as
    top-level — file content enters the canonical form, path does not."""
    src = _write(tmp_path / "branch_ids.txt", "I1\nI2\n")
    a = _write(
        tmp_path / "a.yaml",
        "any:\n  - individual_ids: [I1, I2]\n",
    )
    b = _write(
        tmp_path / "b.yaml",
        f"any:\n  - individual_ids_source: {src.name}\n",
    )
    _, sa = load_selector(a)
    _, sb = load_selector(b)
    assert compute_signature(sa, cli_coverage_column=None) == compute_signature(
        sb, cli_coverage_column=None
    )


def test_signature_individual_ids_source_path_not_significant(tmp_path: Path) -> None:
    """Renaming the source file (same content) doesn't change the signature."""
    src1 = _write(tmp_path / "ids1.txt", "I1\nI2\n")
    src2 = _write(tmp_path / "ids2.txt", "I1\nI2\n")
    a = _write(tmp_path / "a.yaml", f"individual_ids_source: {src1.name}\n")
    b = _write(tmp_path / "b.yaml", f"individual_ids_source: {src2.name}\n")
    _, sa = load_selector(a)
    _, sb = load_selector(b)
    assert compute_signature(sa, cli_coverage_column=None) == compute_signature(
        sb, cli_coverage_column=None
    )


def test_signature_cli_coverage_column_injects_when_selector_silent(
    tmp_path: Path,
) -> None:
    """selector.coverage_column unset + CLI value set → CLI value enters signature."""
    sel = _write(tmp_path / "s.yaml", "populations: [Western_HG]\n")
    _, selector = load_selector(sel)
    s1 = compute_signature(selector, cli_coverage_column=None)
    s2 = compute_signature(selector, cli_coverage_column="snps_hit_1240k")
    assert s1 != s2


def test_signature_selector_coverage_column_wins(tmp_path: Path) -> None:
    """selector.coverage_column set → CLI value ignored (selector wins per
    HLD §Coverage handling)."""
    sel = _write(
        tmp_path / "s.yaml",
        "populations: [Western_HG]\ncoverage_column: snps_hit_1240k\n",
    )
    _, selector = load_selector(sel)
    s1 = compute_signature(selector, cli_coverage_column=None)
    s2 = compute_signature(selector, cli_coverage_column="something_else")
    assert s1 == s2


def test_signature_metadata_not_significant(tmp_path: Path) -> None:
    """Metadata block (first YAML doc) is cohort-irrelevant; signature is
    invariant to changes there."""
    a = _write(
        tmp_path / "a.yaml",
        "tested_against: [v66.0]\n---\npopulations: [Western_HG]\n",
    )
    b = _write(
        tmp_path / "b.yaml",
        "tested_against: [v62.0]\nmaintainer: cre\n---\npopulations: [Western_HG]\n",
    )
    _, sa = load_selector(a)
    _, sb = load_selector(b)
    assert compute_signature(sa, cli_coverage_column=None) == compute_signature(
        sb, cli_coverage_column=None
    )


def test_signature_any_branch_order_significant(tmp_path: Path) -> None:
    """any_branches are indexed (any[0], any[1]); branch order is preserved
    in the signature."""
    a = _write(
        tmp_path / "a.yaml",
        "any:\n  - populations: [Western_HG]\n  - populations: [Eastern_HG]\n",
    )
    b = _write(
        tmp_path / "b.yaml",
        "any:\n  - populations: [Eastern_HG]\n  - populations: [Western_HG]\n",
    )
    _, sa = load_selector(a)
    _, sb = load_selector(b)
    assert compute_signature(sa, cli_coverage_column=None) != compute_signature(
        sb, cli_coverage_column=None
    )


def test_signature_exclude_block_canonicalized(tmp_path: Path) -> None:
    """exclude.group_ids is set-like: order/duplicates don't change signature."""
    a = _write(
        tmp_path / "a.yaml",
        "populations: [Western_HG]\nexclude:\n  group_ids: [Eastern_HG, English.SG]\n",
    )
    b = _write(
        tmp_path / "b.yaml",
        "populations: [Western_HG]\nexclude:\n  group_ids: [English.SG, Eastern_HG, Eastern_HG]\n",
    )
    _, sa = load_selector(a)
    _, sb = load_selector(b)
    assert compute_signature(sa, cli_coverage_column=None) == compute_signature(
        sb, cli_coverage_column=None
    )


def test_signature_date_block_in_signature(tmp_path: Path) -> None:
    a = _write(
        tmp_path / "a.yaml",
        "populations: [Western_HG]\ndate: {min_calbp: 1000, max_calbp: 9000}\n",
    )
    b = _write(
        tmp_path / "b.yaml",
        "populations: [Western_HG]\ndate: {min_calbp: 2000, max_calbp: 9000}\n",
    )
    _, sa = load_selector(a)
    _, sb = load_selector(b)
    assert compute_signature(sa, cli_coverage_column=None) != compute_signature(
        sb, cli_coverage_column=None
    )
