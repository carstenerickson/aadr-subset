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
import pytest

from aadr_subset.engine import select_samples
from aadr_subset.errors import UsageError
from aadr_subset.types import (
    AnyBranch,
    DateRange,
    ExcludeBlock,
    Selector,
)


@dataclass
class FakeAnnoFrame:
    """Minimal duck-type stand-in for aadr_resolve.AnnoFrame. Provides
    exactly the accessors engine.select_samples touches in the Day-2 path."""

    _genetic_ids: list[str]
    _individual_ids: list[str]
    _group_ids: list[str]

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


# --- Day-3+ feature gate (engine rejects with constraint=feature_not_implemented) ---


@pytest.mark.parametrize(
    "selector_kwargs",
    [
        {"any_branches": [AnyBranch(populations=["Western_HG"])]},
        {"exclude": ExcludeBlock(group_ids=["Eastern_HG"])},
        {"date": DateRange(min_calbp=2000)},
        {"modern_only": True},
        {"min_coverage": 0.5},
        {"coverage_column": "snps_hit_1240k"},
        {"source_version": "v44.3", "resolve_to_version": "v66.0"},
    ],
)
def test_unsupported_features_rejected(selector_kwargs: dict[str, object]) -> None:
    """Day-2 engine rejects features that haven't landed yet."""
    af = make_fake_af()
    sel = Selector(**selector_kwargs)  # type: ignore[arg-type]
    with pytest.raises(UsageError) as excinfo:
        select_samples(af, sel)  # type: ignore[arg-type]
    assert any(e.constraint == "feature_not_implemented" for e in excinfo.value.errors)
    assert any("not yet implemented" in e.message for e in excinfo.value.errors)
