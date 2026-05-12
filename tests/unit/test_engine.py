"""Unit tests for engine.select_samples.

Day 2 surface: populations + individual_ids predicates only. Other
selector features raise UsageError with constraint="feature_not_implemented".

These tests use a Mock AnnoFrame that exposes just the accessors engine
touches (genetic_id, individual_id, group_id, n_rows). Day-2+ tests
that need a real AnnoFrame run via the integration suite where a
synthetic .anno file is parsed by aadr-resolve.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from aadr_subset.engine import select_samples
from aadr_subset.types import (
    AnyBranch,
    DateRange,
    ExcludeBlock,
    Selector,
)


@dataclass
class FakeAnnoFrame:
    """Minimal duck-type stand-in for aadr_resolve.AnnoFrame. Provides
    the accessors engine.select_samples touches: genetic_id / individual_id /
    group_id / n_rows (Day 2), plus date_calbp / coverage (Day 3)."""

    _genetic_ids: list[str]
    _individual_ids: list[str]
    _group_ids: list[str]
    # Day-3 additions. None entries become <NA> for date or NaN for coverage.
    _date_calbp: list[int | None] | None = None
    _coverage: list[float | None] | None = None

    @property
    def genetic_id(self) -> pd.Series:
        return pd.Series(self._genetic_ids, dtype="string")

    @property
    def individual_id(self) -> pd.Series:
        return pd.Series(self._individual_ids, dtype="string")

    @property
    def group_id(self) -> pd.Series:
        return pd.Series(self._group_ids, dtype="string")

    @property
    def date_calbp(self) -> pd.Series:
        if self._date_calbp is None:
            # Default: 0 (modern) for every row. Most engine tests touching
            # date supply their own values.
            return pd.Series([0] * len(self._genetic_ids), dtype="Int64")
        return pd.Series(self._date_calbp, dtype="Int64")

    @property
    def coverage(self) -> pd.Series:
        if self._coverage is None:
            return pd.Series([pd.NA] * len(self._genetic_ids), dtype="Float64")
        return pd.Series(self._coverage, dtype="Float64")

    @property
    def n_rows(self) -> int:
        return len(self._genetic_ids)


def make_fake_af() -> FakeAnnoFrame:
    """Six-sample synthetic AnnoFrame for engine testing.

    Loschbour has two GIDs (multi-library individual; HLD §within-version
    multi-row IIDs are normal). Three Group_IDs (Western_HG, Eastern_HG,
    Modern).
    """
    return FakeAnnoFrame(
        _genetic_ids=[
            "I0001",  # row 0: Eastern_HG / Bichon
            "Loschbour.AG",  # row 1: Western_HG / Loschbour
            "Loschbour.DG",  # row 2: Western_HG / Loschbour
            "Bichon",  # row 3: Western_HG / Bichon
            "KO1",  # row 4: Western_HG / KO1
            "English.1",  # row 5: Modern / Eng1
        ],
        _individual_ids=["Bichon", "Loschbour", "Loschbour", "Bichon", "KO1", "Eng1"],
        _group_ids=[
            "Eastern_HG",
            "Western_HG",
            "Western_HG",
            "Western_HG",
            "Western_HG",
            "Modern",
        ],
    )


# --- Day-2 supported predicates ---


def test_empty_selector_matches_all() -> None:
    """HLD test 1: empty selector matches every sample in .anno row order."""
    af = make_fake_af()
    result = select_samples(af, Selector())  # type: ignore[arg-type]
    assert result.n_matched == 6
    assert result.genetic_ids == [
        "I0001",
        "Loschbour.AG",
        "Loschbour.DG",
        "Bichon",
        "KO1",
        "English.1",
    ]
    # per_population_counts in first-appearance order:
    assert list(result.per_population_counts.keys()) == [
        "Eastern_HG",
        "Western_HG",
        "Modern",
    ]
    assert result.per_population_counts["Western_HG"] == 4


def test_populations_single_match() -> None:
    """HLD test 2: populations matches exactly the rows where group_id is set."""
    af = make_fake_af()
    sel = Selector(populations=["Western_HG"])
    result = select_samples(af, sel)  # type: ignore[arg-type]
    assert result.n_matched == 4
    assert result.genetic_ids == ["Loschbour.AG", "Loschbour.DG", "Bichon", "KO1"]
    assert result.per_population_counts == {"Western_HG": 4}


def test_populations_multi() -> None:
    """populations: [A, B] is OR within the populations key (matches both)."""
    af = make_fake_af()
    sel = Selector(populations=["Western_HG", "Eastern_HG"])
    result = select_samples(af, sel)  # type: ignore[arg-type]
    assert result.n_matched == 5
    # First-appearance order:
    assert list(result.per_population_counts.keys()) == ["Eastern_HG", "Western_HG"]


def test_individual_ids_match() -> None:
    """individual_ids matches against af.individual_id; captures all rows
    for each matched individual (multi-library/data-type IIDs naturally
    produce multiple GeneticIDs)."""
    af = make_fake_af()
    sel = Selector(individual_ids=["Loschbour"])
    result = select_samples(af, sel)  # type: ignore[arg-type]
    assert result.n_matched == 2
    assert result.genetic_ids == ["Loschbour.AG", "Loschbour.DG"]
    assert result.per_population_counts == {"Western_HG": 2}


def test_individual_ids_multiple() -> None:
    """Multiple individual_ids OR within the same key."""
    af = make_fake_af()
    sel = Selector(individual_ids=["Loschbour", "KO1"])
    result = select_samples(af, sel)  # type: ignore[arg-type]
    assert result.n_matched == 3
    assert result.genetic_ids == ["Loschbour.AG", "Loschbour.DG", "KO1"]


def test_populations_and_individual_ids_combined() -> None:
    """populations AND individual_ids — sample must match BOTH."""
    af = make_fake_af()
    sel = Selector(
        populations=["Western_HG"],
        individual_ids=["Loschbour", "Bichon"],
    )
    result = select_samples(af, sel)  # type: ignore[arg-type]
    # Bichon and Loschbour both Western_HG → 3 rows (Loschbour x2 + Bichon x1).
    assert result.n_matched == 3
    assert result.genetic_ids == ["Loschbour.AG", "Loschbour.DG", "Bichon"]


def test_individual_ids_from_source_unioned() -> None:
    """individual_ids + individual_ids_from_source are union-merged."""
    af = make_fake_af()
    sel = Selector(
        individual_ids=["Loschbour"],
        individual_ids_from_source=["KO1"],
    )
    result = select_samples(af, sel)  # type: ignore[arg-type]
    assert result.n_matched == 3
    assert set(result.genetic_ids) == {"Loschbour.AG", "Loschbour.DG", "KO1"}


def test_no_match_returns_empty_result() -> None:
    """populations: [nonexistent] → empty result; no error from engine."""
    af = make_fake_af()
    sel = Selector(populations=["DoesNotExist"])
    result = select_samples(af, sel)  # type: ignore[arg-type]
    assert result.n_matched == 0
    assert result.genetic_ids == []
    assert result.per_population_counts == {}


def test_include_matched_criteria_opt_in() -> None:
    """matched_criteria empty by default; populated when opt-in."""
    af = make_fake_af()
    sel = Selector(populations=["Western_HG"])
    result_default = select_samples(af, sel)  # type: ignore[arg-type]
    assert result_default.matched_criteria == {}

    result_with = select_samples(af, sel, include_matched_criteria=True)  # type: ignore[arg-type]
    assert len(result_with.matched_criteria) == 4
    assert all("populations:Western_HG" in v for v in result_with.matched_criteria.values())


# --- Day-3 features: modern_only / date / min_coverage / any: / exclude: ---


def make_dated_af() -> FakeAnnoFrame:
    """Six-sample AnnoFrame with explicit dates + coverage for Day-3 tests.

    Row 0: I0001     / Bichon    / Eastern_HG / date=8000  / coverage=NaN
    Row 1: Losch.AG  / Loschbour / Western_HG / date=8000  / coverage=1.21
    Row 2: Losch.DG  / Loschbour / Western_HG / date=8000  / coverage=0.78
    Row 3: Bichon    / Bichon    / Western_HG / date=13700 / coverage=0.82
    Row 4: KO1       / KO1       / Eastern_HG / date=7700  / coverage=2.40
    Row 5: English.1 / Eng1      / Modern     / date=70    / coverage=NaN
    """
    return FakeAnnoFrame(
        _genetic_ids=[
            "I0001",
            "Loschbour.AG",
            "Loschbour.DG",
            "Bichon",
            "KO1",
            "English.1",
        ],
        _individual_ids=["Bichon", "Loschbour", "Loschbour", "Bichon", "KO1", "Eng1"],
        _group_ids=[
            "Eastern_HG",
            "Western_HG",
            "Western_HG",
            "Western_HG",
            "Eastern_HG",
            "Modern",
        ],
        _date_calbp=[8000, 8000, 8000, 13700, 7700, 70],
        _coverage=[None, 1.21, 0.78, 0.82, 2.40, None],
    )


def test_modern_only_true_includes_only_modern() -> None:
    """modern_only: true matches samples with date_calbp <= 70."""
    af = make_dated_af()
    sel = Selector(modern_only=True)
    result = select_samples(af, sel)  # type: ignore[arg-type]
    assert result.genetic_ids == ["English.1"]


def test_modern_only_boundary() -> None:
    """Boundary inclusive at 70; exclusive at 71."""
    af = FakeAnnoFrame(
        _genetic_ids=["A", "B", "C"],
        _individual_ids=["a", "b", "c"],
        _group_ids=["X", "X", "X"],
        _date_calbp=[70, 71, 0],
    )
    sel = Selector(modern_only=True)
    result = select_samples(af, sel)  # type: ignore[arg-type]
    assert result.genetic_ids == ["A", "C"]


def test_date_min_calbp() -> None:
    """date.min_calbp filters to date >= N."""
    af = make_dated_af()
    sel = Selector(date=DateRange(min_calbp=10000))
    result = select_samples(af, sel)  # type: ignore[arg-type]
    assert result.genetic_ids == ["Bichon"]


def test_date_max_calbp() -> None:
    """date.max_calbp filters to date <= N."""
    af = make_dated_af()
    sel = Selector(date=DateRange(max_calbp=200))
    result = select_samples(af, sel)  # type: ignore[arg-type]
    assert result.genetic_ids == ["English.1"]


def test_date_range_both_bounds() -> None:
    """date.min + date.max → AND-combined."""
    af = make_dated_af()
    sel = Selector(date=DateRange(min_calbp=7000, max_calbp=9000))
    result = select_samples(af, sel)  # type: ignore[arg-type]
    # 8000 × 3 + 7700 = 4 samples
    assert set(result.genetic_ids) == {"I0001", "Loschbour.AG", "Loschbour.DG", "KO1"}


def test_min_coverage_filters_nan_too() -> None:
    """min_coverage filters out NaN coverage samples (HLD §Coverage handling)."""
    af = make_dated_af()
    sel = Selector(min_coverage=0.5)
    result = select_samples(af, sel)  # type: ignore[arg-type]
    # NaN-coverage samples (I0001, English.1) excluded; threshold drops
    # nothing else (all others >= 0.5).
    assert result.genetic_ids == ["Loschbour.AG", "Loschbour.DG", "Bichon", "KO1"]


def test_min_coverage_strict_threshold() -> None:
    """Threshold of 1.0 keeps only those >= 1.0."""
    af = make_dated_af()
    sel = Selector(min_coverage=1.0)
    result = select_samples(af, sel)  # type: ignore[arg-type]
    assert result.genetic_ids == ["Loschbour.AG", "KO1"]


def test_flat_and_combined() -> None:
    """HLD test 3: flat AND across populations + date + coverage."""
    af = make_dated_af()
    sel = Selector(
        populations=["Western_HG"],
        date=DateRange(min_calbp=5000),
        min_coverage=0.5,
    )
    result = select_samples(af, sel)  # type: ignore[arg-type]
    # Western_HG AND date>=5000 AND cov>=0.5 → 3 samples
    assert result.genetic_ids == ["Loschbour.AG", "Loschbour.DG", "Bichon"]


# --- any: OR-block tests (HLD test 4) ---


def test_any_or_three_branches() -> None:
    """HLD test 4: 3-branch any: block; output is the union, deduped,
    in .anno row order."""
    af = make_dated_af()
    sel = Selector(
        any_branches=[
            AnyBranch(populations=["Western_HG"]),
            AnyBranch(individual_ids=["KO1"]),
            AnyBranch(date=DateRange(max_calbp=200)),
        ]
    )
    result = select_samples(af, sel)  # type: ignore[arg-type]
    # Western_HG (4 samples) ∪ KO1 (1) ∪ modern (1) = 6 samples (every row)
    # Actually I0001 is Eastern_HG and date 8000 (not modern) and individual
    # is Bichon (not KO1) → excluded. So union excludes I0001.
    assert result.genetic_ids == [
        "Loschbour.AG",
        "Loschbour.DG",
        "Bichon",
        "KO1",
        "English.1",
    ]


def test_any_block_dedup() -> None:
    """Same sample matching multiple branches appears once."""
    af = make_dated_af()
    sel = Selector(
        any_branches=[
            AnyBranch(populations=["Western_HG"]),
            AnyBranch(individual_ids=["Loschbour"]),  # also Western_HG → overlap
        ]
    )
    result = select_samples(af, sel)  # type: ignore[arg-type]
    assert len(result.genetic_ids) == 3  # 3 Western_HG, no double-counting
    assert result.per_population_counts == {"Western_HG": 3}


def test_any_block_per_branch_counts() -> None:
    """per_branch_counts reports contribution to final result per branch."""
    af = make_dated_af()
    sel = Selector(
        any_branches=[
            AnyBranch(populations=["Western_HG"]),
            AnyBranch(individual_ids=["KO1"]),
        ]
    )
    result = select_samples(af, sel)  # type: ignore[arg-type]
    # top_level matches every row (no top-level predicate); branch[0] = 3 Western_HG;
    # branch[1] = 1 KO1.
    assert result.per_branch_counts["any[0]"] == 3
    assert result.per_branch_counts["any[1]"] == 1


# --- exclude: NOT-of-OR tests (HLD test 5) ---


def test_exclude_group_ids() -> None:
    """HLD test 5: exclude.group_ids drops samples matching ANY listed Group_ID."""
    af = make_dated_af()
    sel = Selector(
        populations=["Western_HG", "Eastern_HG"],
        exclude=ExcludeBlock(group_ids=["Eastern_HG"]),
    )
    result = select_samples(af, sel)  # type: ignore[arg-type]
    # Western_HG ∪ Eastern_HG minus Eastern_HG = 3 Western_HG.
    assert result.genetic_ids == ["Loschbour.AG", "Loschbour.DG", "Bichon"]


def test_exclude_individual_ids() -> None:
    """exclude.individual_ids drops samples by IID."""
    af = make_dated_af()
    sel = Selector(
        populations=["Western_HG"],
        exclude=ExcludeBlock(individual_ids=["Loschbour"]),
    )
    result = select_samples(af, sel)  # type: ignore[arg-type]
    # Western_HG minus the 2 Loschbour rows = Bichon only.
    assert result.genetic_ids == ["Bichon"]


def test_excluded_counts_per_literal() -> None:
    """excluded_counts has one entry per excluded literal."""
    af = make_dated_af()
    sel = Selector(
        populations=["Western_HG", "Eastern_HG"],
        exclude=ExcludeBlock(group_ids=["Eastern_HG", "Western_HG"]),
    )
    result = select_samples(af, sel)  # type: ignore[arg-type]
    by_value = {(e.key, e.value): e.count for e in result.excluded_counts}
    # Eastern_HG: 2 rows in .anno (I0001 + KO1); Western_HG: 3 (Loschbour x2 + Bichon).
    assert by_value == {
        ("group_ids", "Eastern_HG"): 2,
        ("group_ids", "Western_HG"): 3,
    }


def test_top_level_and_any_combined() -> None:
    """top-level AND-block ANDed with any: OR-block."""
    af = make_dated_af()
    sel = Selector(
        date=DateRange(min_calbp=5000),  # top-level: date >= 5000
        any_branches=[
            AnyBranch(populations=["Western_HG"]),
            AnyBranch(individual_ids=["KO1"]),
        ],
    )
    result = select_samples(af, sel)  # type: ignore[arg-type]
    # date>=5000: I0001, Losch.AG, Losch.DG, Bichon, KO1 (5 samples)
    # any: Western_HG OR KO1 → 4 samples (Losch.AG, Losch.DG, Bichon, KO1)
    # Final = 4 samples (I0001 not in any: branch)
    assert result.genetic_ids == ["Loschbour.AG", "Loschbour.DG", "Bichon", "KO1"]


def test_complex_selector_and_or_not() -> None:
    """populations AND date AND (any: OR) AND NOT exclude all combined."""
    af = make_dated_af()
    sel = Selector(
        populations=["Western_HG", "Eastern_HG"],
        date=DateRange(min_calbp=5000),
        any_branches=[
            AnyBranch(min_coverage=0.5),
        ],
        exclude=ExcludeBlock(individual_ids=["Bichon"]),
    )
    result = select_samples(af, sel)  # type: ignore[arg-type]
    # populations: 5 (everyone but English.1)
    # date >= 5000: 5 (everyone but English.1)
    # any branch min_coverage>=0.5: 4 (Losch.AG, Losch.DG, Bichon-row, KO1) — note I0001
    #   row 0 has NaN coverage so fails
    # exclude individual_ids=Bichon: drops Bichon individual (rows 0 and 3)
    # Final: Losch.AG, Losch.DG, KO1
    assert result.genetic_ids == ["Loschbour.AG", "Loschbour.DG", "KO1"]


# --- Feature gate (empty as of Day 7; full HLD v0.1 surface wired) ---
