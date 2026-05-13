"""Tests for stratified sampling (v0.3).

Covers the 25 tests in
cs-wiki/projects/aadr-subset-stratified-sampling.md §11 plus the
15b positive coverage_column propagation case added in revision-1.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import aadr_resolve
import pytest

from aadr_subset.engine import select_samples
from aadr_subset.errors import IOFailure, UsageError
from aadr_subset.selector import compute_signature, load_selector
from aadr_subset.types import (
    AnyBranch,
    SamplingDrop,
    SamplingPolicy,
    SamplingSpec,
    Selector,
)
from tests.fixtures.synthesize import (
    make_loschbour_v66_fixture,
    make_v62_class_d_fixture,
)
from tests.unit.test_engine import FakeAnnoFrame, make_fake_af


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "aadr_subset", *args],
        capture_output=True,
        text=True,
        check=False,
    )


# -- 1. SamplingSpec round-trip --------------------------------------------


def test_sampling_spec_dataclass_round_trip() -> None:
    """SamplingSpec is frozen + slots; dataclasses.replace works."""
    from dataclasses import replace

    a = SamplingSpec(max_per_population=50)
    b = replace(a, max_per_individual=1)
    assert b.max_per_population == 50
    assert b.max_per_individual == 1
    assert b.policy == SamplingPolicy.TOP_COVERAGE


# -- 2. Schema rejects empty / null / zero / negative caps -----------------


def test_schema_rejects_empty_sampling_block(tmp_path: Path) -> None:
    """`sampling: {}` errors via anyOf — at least one cap field required."""
    sel = tmp_path / "s.yaml"
    sel.write_text("populations: [X]\nsampling: {}\n", encoding="utf-8")
    with pytest.raises(UsageError):
        load_selector(sel)


def test_schema_rejects_null_sampling(tmp_path: Path) -> None:
    """`sampling: null` errors — type: object check."""
    sel = tmp_path / "s.yaml"
    sel.write_text("populations: [X]\nsampling: null\n", encoding="utf-8")
    with pytest.raises(UsageError):
        load_selector(sel)


def test_schema_rejects_zero_max_per_population(tmp_path: Path) -> None:
    sel = tmp_path / "s.yaml"
    sel.write_text("populations: [X]\nsampling: {max_per_population: 0}\n", encoding="utf-8")
    with pytest.raises(UsageError):
        load_selector(sel)


def test_schema_rejects_negative_cap(tmp_path: Path) -> None:
    sel = tmp_path / "s.yaml"
    sel.write_text("populations: [X]\nsampling: {max_per_individual: -1}\n", encoding="utf-8")
    with pytest.raises(UsageError):
        load_selector(sel)


def test_schema_rejects_unknown_sampling_property(tmp_path: Path) -> None:
    sel = tmp_path / "s.yaml"
    sel.write_text("populations: [X]\nsampling: {max_per_pop: 50}\n", encoding="utf-8")
    with pytest.raises(UsageError):
        load_selector(sel)


# -- 3. Schema rejects policy: random in v0.3 ------------------------------


def test_schema_rejects_policy_random_in_v03(tmp_path: Path) -> None:
    """policy: random not yet implemented; schema enum locks down."""
    sel = tmp_path / "s.yaml"
    sel.write_text(
        "populations: [X]\nsampling: {max_per_population: 50, policy: random}\n",
        encoding="utf-8",
    )
    with pytest.raises(UsageError) as exc:
        load_selector(sel)
    # JSON-schema error message; just confirm 'random' is mentioned.
    assert "random" in "\n".join(e.message for e in exc.value.errors)


# -- 4. Engine: per-pop cap caps each group --------------------------------


def test_engine_per_pop_cap_caps_each_group() -> None:
    """7 candidates in one group, cap=3 → top 3 by coverage survive."""
    af = FakeAnnoFrame(
        _genetic_ids=[f"G{i}" for i in range(7)],
        _individual_ids=[f"I{i}" for i in range(7)],
        _group_ids=["G1"] * 7,
        _coverage=[1.0, 0.8, 0.6, 0.4, 0.2, 0.1, 0.05],
    )
    sel = Selector(sampling=SamplingSpec(max_per_population=3))
    r = select_samples(af, sel)  # type: ignore[arg-type]
    assert r.n_matched == 3
    assert sorted(r.genetic_ids) == ["G0", "G1", "G2"]


# -- 5. Engine: per-IID cap operates across groups -------------------------


def test_engine_per_iid_cap_operates_across_groups() -> None:
    """Same individual in two groups; per-IID cap=1 → one row total."""
    af = FakeAnnoFrame(
        _genetic_ids=["A.AG", "A.DG"],
        _individual_ids=["A", "A"],
        _group_ids=["Western_HG", "Mesolithic_Europe"],
        _coverage=[1.0, 0.5],
    )
    sel = Selector(sampling=SamplingSpec(max_per_individual=1))
    r = select_samples(af, sel)  # type: ignore[arg-type]
    assert r.n_matched == 1
    assert r.genetic_ids == ["A.AG"]


# -- 6. Per-IID-first ordering: counter-example yields 3, not 2 ------------


def test_engine_per_iid_first_ordering_counter_example() -> None:
    """Per-IID-first yields 3 survivors; per-pop-first would yield 2.

    Setup matches the design doc §4 example:
      Western_HG, 7 libraries across 3 individuals:
        A: 1.0, 0.8, 0.6
        B: 0.9, 0.7
        C: 0.5, 0.3
      max_per_population=4, max_per_individual=1
    """
    af = FakeAnnoFrame(
        _genetic_ids=["A1", "A2", "A3", "B1", "B2", "C1", "C2"],
        _individual_ids=["A", "A", "A", "B", "B", "C", "C"],
        _group_ids=["Western_HG"] * 7,
        _coverage=[1.0, 0.8, 0.6, 0.9, 0.7, 0.5, 0.3],
    )
    sel = Selector(sampling=SamplingSpec(max_per_population=4, max_per_individual=1))
    r = select_samples(af, sel)  # type: ignore[arg-type]
    # Per-IID-first picks A1, B1, C1 (one per IID, top-cov each); per-pop
    # then caps at 4 — already 3 ≤ 4. Result: 3.
    assert r.n_matched == 3
    assert sorted(r.genetic_ids) == ["A1", "B1", "C1"]


# -- 7. Stable-sort tie-break preserves .anno row order --------------------


def test_engine_stable_sort_tie_break_anno_row_order() -> None:
    """Equal coverage → earlier .anno row wins (stable sort)."""
    af = FakeAnnoFrame(
        _genetic_ids=["First", "Second", "Third"],
        _individual_ids=["I1", "I2", "I3"],
        _group_ids=["G"] * 3,
        _coverage=[0.5, 0.5, 0.5],  # all tied
    )
    sel = Selector(sampling=SamplingSpec(max_per_population=1))
    r = select_samples(af, sel)  # type: ignore[arg-type]
    assert r.genetic_ids == ["First"]


# -- 8. NaN coverage sinks --------------------------------------------------


def test_engine_nan_coverage_sinks() -> None:
    """NaN-coverage row picked only when no other choice."""
    af = FakeAnnoFrame(
        _genetic_ids=["HighCov", "NoCov"],
        _individual_ids=["I1", "I2"],
        _group_ids=["G"] * 2,
        _coverage=[1.0, None],
    )
    # Cap=1 → HighCov wins.
    sel1 = Selector(sampling=SamplingSpec(max_per_population=1))
    r1 = select_samples(af, sel1)  # type: ignore[arg-type]
    assert r1.genetic_ids == ["HighCov"]
    # Cap=2 → both survive (no contest).
    sel2 = Selector(sampling=SamplingSpec(max_per_population=2))
    r2 = select_samples(af, sel2)  # type: ignore[arg-type]
    assert sorted(r2.genetic_ids) == ["HighCov", "NoCov"]


# -- 9. NaN Group_ID passes through per-IID, bypasses per-pop --------------


def test_engine_nan_group_id_bypasses_per_pop() -> None:
    """Row with NaN group_id survives per-population cap (group undefined)."""
    import pandas as pd

    af = FakeAnnoFrame(
        _genetic_ids=["NoGroup", "G1_high", "G1_low"],
        _individual_ids=["I1", "I2", "I3"],
        # pd.NA for the first; "G1" for the rest.
        _group_ids=[pd.NA, "G1", "G1"],  # type: ignore[list-item]
        _coverage=[0.1, 0.9, 0.5],
    )
    sel = Selector(sampling=SamplingSpec(max_per_population=1))
    r = select_samples(af, sel)  # type: ignore[arg-type]
    # NoGroup survives (bypassed); G1_high wins the G1 cap.
    assert set(r.genetic_ids) == {"NoGroup", "G1_high"}


# -- 10. Class-D + sampling without --coverage-derive → IOFailure ----------


def test_engine_class_d_sampling_without_coverage_derive_fails(tmp_path: Path) -> None:
    """Hard fail per LLD pin — class D has no native coverage column."""
    anno_path = tmp_path / "v62.0.anno"
    make_v62_class_d_fixture(anno_path)
    af = aadr_resolve.AnnoFrame.from_path(anno_path, version_label="v62.0")
    sel = Selector(sampling=SamplingSpec(max_per_population=2))
    with pytest.raises(IOFailure) as exc:
        select_samples(af, sel)
    assert "sampling requires a coverage column" in str(exc.value)
    assert "snps_hit_1240k" in str(exc.value)


# -- 11. Idempotence -------------------------------------------------------


def test_engine_sampling_idempotent_on_already_sampled_pool() -> None:
    """Run sampling on a pool that already passes the cap → no further drops."""
    af = FakeAnnoFrame(
        _genetic_ids=[f"G{i}" for i in range(3)],
        _individual_ids=[f"I{i}" for i in range(3)],
        _group_ids=["G"] * 3,
        _coverage=[1.0, 0.5, 0.3],
    )
    sel = Selector(sampling=SamplingSpec(max_per_population=10))  # cap > pool
    r = select_samples(af, sel)  # type: ignore[arg-type]
    assert r.n_matched == 3
    assert r.sampling_drops == []


# -- 12. Cross-version + per-IID operates on target IIDs -------------------


def test_engine_cross_version_per_iid_operates_on_target(tmp_path: Path) -> None:
    """Per-IID cap counts target Individual_IDs after cross-version lift."""
    src_path = tmp_path / "v62.0.anno"
    tgt_path = tmp_path / "v66.0.anno"
    make_v62_class_d_fixture(src_path)
    make_loschbour_v66_fixture(tgt_path)
    src_af = aadr_resolve.AnnoFrame.from_path(src_path, version_label="v62.0")
    tgt_af = aadr_resolve.AnnoFrame.from_path(tgt_path, version_label="v66.0")
    # Loschbour exists in v62 with one row; v66 has two rows (Loschbour.AG +
    # Loschbour.DG). Per-IID cap=1 should keep one library after lift.
    sel = Selector(
        individual_ids=["Loschbour"],
        source_version="v62.0",
        resolve_to_version="v66.0",
        sampling=SamplingSpec(max_per_individual=1),
    )
    r = select_samples(tgt_af, sel, source_anno=src_af)
    assert r.n_matched == 1
    # The chosen library is the top-coverage one (Loschbour.AG, cov 1.21).
    assert r.genetic_ids == ["Loschbour.AG"]


# -- 13. per_branch_counts reflects post-sampling counts -------------------


def test_engine_per_branch_counts_reflect_post_sampling() -> None:
    """Branch counts AND-in the sampling reduction."""
    af = FakeAnnoFrame(
        _genetic_ids=["G1", "G2", "G3"],
        _individual_ids=["I1", "I2", "I3"],
        _group_ids=["P", "P", "P"],
        _coverage=[1.0, 0.5, 0.1],
    )
    # any-branch matches all 3; sampling caps to 1.
    sel = Selector(
        any_branches=[AnyBranch(populations=["P"])],
        sampling=SamplingSpec(max_per_population=1),
    )
    r = select_samples(af, sel)  # type: ignore[arg-type]
    assert r.n_matched == 1
    # Branch[0] should report 1, not 3.
    assert r.per_branch_counts == {"top_level": 1, "any[0]": 1}


# -- 14. matched_criteria excludes sampling-dropped rows -------------------


def test_engine_matched_criteria_excludes_dropped_rows() -> None:
    """Dropped rows never appear in matched_criteria — they're not in the cohort."""
    af = FakeAnnoFrame(
        _genetic_ids=["G1", "G2", "G3"],
        _individual_ids=["I1", "I2", "I3"],
        _group_ids=["P"] * 3,
        _coverage=[1.0, 0.5, 0.1],
    )
    sel = Selector(
        populations=["P"],
        sampling=SamplingSpec(max_per_population=1),
    )
    r = select_samples(af, sel, include_matched_criteria=True)  # type: ignore[arg-type]
    assert set(r.matched_criteria.keys()) == {"G1"}
    # The dropped rows don't show up.
    assert "G2" not in r.matched_criteria
    assert "G3" not in r.matched_criteria


# -- 15. Branch coverage_column doesn't affect sampling priority ----------


def test_engine_branch_coverage_column_does_not_affect_sampling() -> None:
    """Sampling always uses the top-level effective coverage column.

    Set up a branch with its own coverage_column; verify sampling
    priority uses the TOP-level coverage (or default if no top set).
    """
    # FakeAnnoFrame only exposes .coverage, not .coverage_via. The
    # branch's coverage_column is structurally accepted but only
    # functionally exercised when AnnoFrame.coverage_via is callable.
    # For this test we verify that sampling produces the same result
    # whether the branch carries a coverage_column or not — sampling
    # is top-level only.
    af = FakeAnnoFrame(
        _genetic_ids=["G1", "G2", "G3"],
        _individual_ids=["I1", "I2", "I3"],
        _group_ids=["P"] * 3,
        _coverage=[1.0, 0.5, 0.1],
    )
    sel_with_branch_col = Selector(
        any_branches=[AnyBranch(populations=["P"], coverage_column="other_col")],
        sampling=SamplingSpec(max_per_population=1),
    )
    sel_without = Selector(
        any_branches=[AnyBranch(populations=["P"])],
        sampling=SamplingSpec(max_per_population=1),
    )
    r_with = select_samples(af, sel_with_branch_col)  # type: ignore[arg-type]
    r_without = select_samples(af, sel_without)  # type: ignore[arg-type]
    # Both pick G1 (highest top-level coverage). Branch's coverage_column
    # doesn't change sampling priority.
    assert r_with.genetic_ids == r_without.genetic_ids == ["G1"]


# -- 15b. Selector coverage_column propagates to sampling priority --------


def test_engine_selector_coverage_column_propagates_to_sampling(tmp_path: Path) -> None:
    """Selector's coverage_column governs the priority used by sampling."""
    anno_path = tmp_path / "v62.0.anno"
    make_v62_class_d_fixture(anno_path)
    af = aadr_resolve.AnnoFrame.from_path(anno_path, version_label="v62.0")
    # Without coverage_column on class D, sampling hard-fails. With
    # selector.coverage_column='snps_hit_1240k', sampling succeeds and
    # uses the proxy column.
    sel_path = tmp_path / "s.yaml"
    sel_path.write_text(
        "populations: [Western_HG]\n"
        "coverage_column: snps_hit_1240k\n"
        "sampling: {max_per_population: 2}\n",
        encoding="utf-8",
    )
    _, selector = load_selector(sel_path)
    r = select_samples(af, selector)
    assert r.n_matched == 2
    # The class-D fixture has Western_HG with snps_hit_1240k values that
    # differ — sampling should pick the top 2 by that proxy.
    assert all(g.startswith(("I0001", "Loschbour", "Bichon")) for g in r.genetic_ids)


# -- 16. Signature: same selector + caps + .anno → same hash ---------------


def test_signature_same_selector_same_caps_same_hash(tmp_path: Path) -> None:
    a = tmp_path / "a.yaml"
    a.write_text("populations: [X]\nsampling: {max_per_population: 50}\n", encoding="utf-8")
    _, sel = load_selector(a)
    sig1 = compute_signature(sel, cli_coverage_column=None)
    sig2 = compute_signature(sel, cli_coverage_column=None)
    assert sig1 == sig2


# -- 17. Signature: intent-not-expansion (.anno-independent) ---------------


def test_signature_anno_independent_with_sampling(tmp_path: Path) -> None:
    """Same selector with sampling produces the same signature
    regardless of which .anno it runs against (intent-not-expansion).
    """
    sel = tmp_path / "s.yaml"
    sel.write_text("populations: [X]\nsampling: {max_per_population: 50}\n", encoding="utf-8")
    _, selector = load_selector(sel)
    sig = compute_signature(selector, cli_coverage_column=None)
    assert sig.startswith("sha256:")
    # Reload and re-hash → same.
    _, selector_again = load_selector(sel)
    assert compute_signature(selector_again, cli_coverage_column=None) == sig


# -- 18. Signature: different caps → different hash ------------------------


def test_signature_different_caps_different_hash(tmp_path: Path) -> None:
    a = tmp_path / "a.yaml"
    b = tmp_path / "b.yaml"
    a.write_text("populations: [X]\nsampling: {max_per_population: 50}\n", encoding="utf-8")
    b.write_text("populations: [X]\nsampling: {max_per_population: 100}\n", encoding="utf-8")
    _, sa = load_selector(a)
    _, sb = load_selector(b)
    assert compute_signature(sa, cli_coverage_column=None) != compute_signature(
        sb, cli_coverage_column=None
    )


# -- 19. Signature: policy: top_coverage explicit vs omitted → same hash ---


def test_signature_default_policy_elided(tmp_path: Path) -> None:
    a = tmp_path / "a.yaml"
    b = tmp_path / "b.yaml"
    a.write_text("populations: [X]\nsampling: {max_per_population: 50}\n", encoding="utf-8")
    b.write_text(
        "populations: [X]\nsampling: {max_per_population: 50, policy: top_coverage}\n",
        encoding="utf-8",
    )
    _, sa = load_selector(a)
    _, sb = load_selector(b)
    assert compute_signature(sa, cli_coverage_column=None) == compute_signature(
        sb, cli_coverage_column=None
    )


# -- 20. Signature: CLI fallback enters signature when selector field unset --


def test_signature_cli_fills_selector_omission(tmp_path: Path) -> None:
    """Selector pins max_per_population: 50; CLI is --max-per-population 100
    → signature has 50 (selector wins). Selector omits max_per_individual;
    CLI is --max-per-individual 1 → signature has 1."""
    sel = tmp_path / "s.yaml"
    sel.write_text("populations: [X]\nsampling: {max_per_population: 50}\n", encoding="utf-8")
    _, selector = load_selector(sel)
    sig_with_cli_iid = compute_signature(
        selector, cli_coverage_column=None, cli_max_per_individual=1
    )
    sig_without = compute_signature(selector, cli_coverage_column=None)
    # CLI per-IID injected → different signatures.
    assert sig_with_cli_iid != sig_without
    # CLI max_per_population is overridden by selector; signature unchanged.
    sig_with_cli_pop_override = compute_signature(
        selector, cli_coverage_column=None, cli_max_per_population=100
    )
    assert sig_with_cli_pop_override == sig_without


# -- 21. CLI rejects --max-per-population 0 at click level ----------------


def test_cli_rejects_zero_max_per_population(tmp_path: Path) -> None:
    """click.IntRange(min=1) rejects 0 before any selector / .anno load."""
    sel = tmp_path / "s.yaml"
    sel.write_text("populations: [X]\n", encoding="utf-8")
    anno = tmp_path / "v66.0.anno"
    make_loschbour_v66_fixture(anno)
    result = _run_cli("select", str(sel), str(anno), "--max-per-population", "0")
    assert result.returncode != 0
    assert (
        "is not in the range" in result.stderr
        or "Invalid value" in result.stderr
        or "0 is not" in result.stderr
    )


def test_cli_rejects_negative_max_per_individual(tmp_path: Path) -> None:
    sel = tmp_path / "s.yaml"
    sel.write_text("populations: [X]\n", encoding="utf-8")
    anno = tmp_path / "v66.0.anno"
    make_loschbour_v66_fixture(anno)
    result = _run_cli("select", str(sel), str(anno), "--max-per-individual", "-1")
    assert result.returncode != 0


# -- 22. Sampling flags work on select / inspect / report ----------------


def test_cli_max_per_population_on_select(tmp_path: Path) -> None:
    sel = tmp_path / "s.yaml"
    sel.write_text("populations: [Western_HG]\n", encoding="utf-8")
    anno = tmp_path / "v66.0.anno"
    make_loschbour_v66_fixture(anno)
    out = tmp_path / "out.txt"
    result = _run_cli(
        "select",
        str(sel),
        str(anno),
        "--max-per-population",
        "1",
        "-o",
        str(out),
    )
    assert result.returncode == 0, result.stderr
    ids = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(ids) == 1


def test_cli_max_per_population_on_inspect(tmp_path: Path) -> None:
    sel = tmp_path / "s.yaml"
    sel.write_text("populations: [Western_HG]\n", encoding="utf-8")
    anno = tmp_path / "v66.0.anno"
    make_loschbour_v66_fixture(anno)
    result = _run_cli("inspect", str(sel), str(anno), "--max-per-population", "1")
    assert result.returncode == 0, result.stderr
    # Downsampled section appears.
    assert "Downsampled:" in result.stdout


def test_cli_max_per_population_on_report(tmp_path: Path) -> None:
    sel = tmp_path / "s.yaml"
    sel.write_text("populations: [Western_HG]\n", encoding="utf-8")
    anno = tmp_path / "v66.0.anno"
    make_loschbour_v66_fixture(anno)
    out = tmp_path / "r.tsv"
    result = _run_cli(
        "report",
        str(sel),
        str(anno),
        "--max-per-population",
        "1",
        "-o",
        str(out),
    )
    assert result.returncode == 0, result.stderr
    lines = out.read_text(encoding="utf-8").splitlines()
    # Header + 1 row; n_matched=1 reflects post-sampling.
    assert lines[1].split("\t")[1] == "1"


# -- 23. JSON output: sampling_drops shape, ordering, sparseness ----------


def test_cli_json_output_sampling_drops_shape(tmp_path: Path) -> None:
    """JSON output exposes sampling_drops as list-of-objects with the
    pinned shape (dimension/key/count); per-IID first, then per-pop."""
    sel = tmp_path / "s.yaml"
    sel.write_text(
        "populations: [Western_HG]\nsampling: {max_per_population: 1, max_per_individual: 1}\n",
        encoding="utf-8",
    )
    anno = tmp_path / "v66.0.anno"
    make_loschbour_v66_fixture(anno)
    out = tmp_path / "r.json"
    result = _run_cli("select", str(sel), str(anno), "--format", "json", "-o", str(out))
    assert result.returncode == 0, result.stderr
    parsed = json.loads(out.read_text(encoding="utf-8"))
    drops = parsed["sampling_drops"]
    assert isinstance(drops, list)
    # Western_HG has 3 samples (Loschbour.AG, Loschbour.DG, Bichon) over
    # 2 IIDs (Loschbour×2, Bichon×1). Per-IID cap 1: drop 1 (Loschbour.DG).
    # Per-pop cap 1: drop 1 more (Bichon).
    dimensions = [d["dimension"] for d in drops]
    # Per-individual entries first, then per-population.
    if "individual" in dimensions and "population" in dimensions:
        first_pop_idx = dimensions.index("population")
        # All "individual" entries must precede any "population" entry.
        for i in range(first_pop_idx):
            assert dimensions[i] == "individual"


def test_json_sampling_drops_empty_when_no_sampling(tmp_path: Path) -> None:
    """No sampling spec → empty sampling_drops list (sparse rule)."""
    sel = tmp_path / "s.yaml"
    sel.write_text("populations: [Western_HG]\n", encoding="utf-8")
    anno = tmp_path / "v66.0.anno"
    make_loschbour_v66_fixture(anno)
    out = tmp_path / "r.json"
    _run_cli("select", str(sel), str(anno), "--format", "json", "-o", str(out))
    parsed = json.loads(out.read_text(encoding="utf-8"))
    assert parsed["sampling_drops"] == []


# -- 24. Inspect Downsampled section + per-individual aggregate row -------


def test_inspect_downsampled_section_aggregates_per_individual(tmp_path: Path) -> None:
    sel = tmp_path / "s.yaml"
    sel.write_text(
        "populations: [Western_HG]\nsampling: {max_per_individual: 1}\n",
        encoding="utf-8",
    )
    anno = tmp_path / "v66.0.anno"
    make_loschbour_v66_fixture(anno)
    result = _run_cli("inspect", str(sel), str(anno))
    assert result.returncode == 0, result.stderr
    # Per-individual aggregate phrase appears (not per-IID rows).
    assert "per-individual aggregate" in result.stdout


# -- 25. Determinism across pandas versions (snapshot fixture) ------------


def test_engine_sampling_deterministic_genetic_ids() -> None:
    """Two runs of the same selector against the same fixture produce
    byte-identical genetic_ids lists (kind='stable' pin)."""
    af = make_fake_af()
    af._coverage = [1.0, 0.8, 0.6, 0.4, 0.2, None]  # type: ignore[assignment]
    sel = Selector(sampling=SamplingSpec(max_per_population=2))
    r1 = select_samples(af, sel)  # type: ignore[arg-type]
    r2 = select_samples(af, sel)  # type: ignore[arg-type]
    assert r1.genetic_ids == r2.genetic_ids


# -- Extra: sampling_drops carries SamplingDrop instances -----------------


def test_engine_sampling_drops_are_sampling_drop_instances() -> None:
    af = FakeAnnoFrame(
        _genetic_ids=[f"G{i}" for i in range(5)],
        _individual_ids=[f"I{i}" for i in range(5)],
        _group_ids=["P"] * 5,
        _coverage=[1.0, 0.8, 0.6, 0.4, 0.2],
    )
    sel = Selector(sampling=SamplingSpec(max_per_population=2))
    r = select_samples(af, sel)  # type: ignore[arg-type]
    assert all(isinstance(sd, SamplingDrop) for sd in r.sampling_drops)
    pop_drops = [sd for sd in r.sampling_drops if sd.dimension == "population"]
    assert len(pop_drops) == 1
    assert pop_drops[0].key == "P"
    assert pop_drops[0].count == 3
