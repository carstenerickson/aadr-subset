"""Selector evaluation engine.

Day 2 scope: `select_samples` supports the simplest path — populations
and individual_ids predicates against a single AnnoFrame. Features
deferred to later days (any:, exclude:, date:, modern_only:, min_coverage:,
coverage_column:, resolve_to_version:) are detected and rejected with
a clear UsageError at engine entry; validate accepts them at the grammar
level but engine refuses to run them until they land.

Day 3 adds any:/exclude:/date/coverage/modern_only.
Day 6 adds cross-version (resolve_to_version + --source-anno).
"""

from __future__ import annotations

import pandas as pd
from aadr_resolve import AnnoFrame

from .errors import UsageError, ValidationError
from .types import Selector, SelectorWarnings, SubsetResult


def select_samples(
    anno: AnnoFrame,
    selector: Selector,
    *,
    include_matched_criteria: bool = False,
) -> SubsetResult:
    """Evaluate selector against AnnoFrame; return SubsetResult.

    Day-2 surface only — accepts selectors using `populations` and/or
    `individual_ids` (incl. `individual_ids_source`-derived contents).
    Other selector features raise UsageError (exit 4) until their day
    lands per HLD project plan.

    Args:
        anno: target AnnoFrame (pre-loaded by run_select).
        selector: validated, fully-loaded Selector.
        include_matched_criteria: when True, populate
            SubsetResult.matched_criteria; otherwise leave empty (default).

    Returns:
        SubsetResult with genetic_ids in .anno row order, n_matched,
        per_population_counts, and optionally matched_criteria populated.
        Other fields default to empty / placeholder values; the CLI
        orchestrator fills run-env metadata before returning.

    Raises:
        UsageError: a Day-3+ feature was used (any:, exclude:, date:,
            modern_only:, min_coverage:, coverage_column:,
            resolve_to_version:).
    """
    _reject_unsupported_features(selector)

    # Build the predicate mask. Day 2: only populations + individual_ids.
    masks: list[pd.Series] = []

    if selector.populations:
        masks.append(anno.group_id.isin(selector.populations))

    # Single-version individual_ids match. Cross-version (Day 6) replaces
    # the source IID set with a resolved target_iid set; Day 2 path is the
    # union of YAML-inline + file-loaded IIDs matched against
    # af.individual_id directly.
    iid_pool = set(selector.individual_ids) | set(selector.individual_ids_from_source)
    if iid_pool:
        masks.append(anno.individual_id.isin(iid_pool))

    # AND-combine masks.
    if masks:
        final_mask = masks[0]
        for m in masks[1:]:
            final_mask = final_mask & m
    else:
        # Empty selector matches every sample per HLD §Selector grammar
        # semantics.
        final_mask = pd.Series([True] * anno.n_rows, index=anno.genetic_id.index)

    # Materialize matched rows.
    matched_gids: list[str] = anno.genetic_id[final_mask].tolist()
    matched_group_ids: list[str] = anno.group_id[final_mask].tolist()

    # Defensive dedup (aadr-resolve's loader enforces unique GeneticIDs per
    # .anno; left in for the multi-branch path Day 3 will introduce).
    unique_gids, duplicates = _dedup_preserve_order(matched_gids)
    # group_id list shrinks parallel to gids dedup — matched_group_ids[i]
    # corresponds to matched_gids[i]; we drop the i-th group_id whenever we
    # drop the i-th gid as a duplicate.
    unique_group_ids = _filter_parallel(matched_gids, matched_group_ids)

    # per_population_counts: insertion order = .anno first-appearance order.
    per_pop: dict[str, int] = {}
    for gpid in unique_group_ids:
        per_pop[gpid] = per_pop.get(gpid, 0) + 1

    # matched_criteria: opt-in only.
    matched_criteria: dict[str, list[str]] = {}
    if include_matched_criteria:
        criteria_keys: list[str] = []
        if selector.populations:
            criteria_keys.append(f"populations:{','.join(selector.populations)}")
        if iid_pool:
            criteria_keys.append("individual_ids")
        matched_criteria = {gid: list(criteria_keys) for gid in unique_gids}

    n_matched = len(unique_gids)
    warnings = SelectorWarnings(duplicate_genetic_ids=duplicates)
    return SubsetResult(
        genetic_ids=unique_gids,
        n_matched=n_matched,
        per_population_counts=per_pop,
        per_branch_counts={"top_level": n_matched},
        excluded_counts=[],
        matched_criteria=matched_criteria,
        warnings=warnings,
    )


# --- Internal helpers ---


def _reject_unsupported_features(selector: Selector) -> None:
    """Day-2 feature gate. Raises UsageError naming each unsupported
    feature the selector touched + the HLD project-plan day it lands."""
    unsupported: list[str] = []
    if selector.any_branches:
        unsupported.append("any: (Day 3)")
    if selector.exclude is not None:
        unsupported.append("exclude: (Day 3)")
    if selector.date is not None:
        unsupported.append("date: (Day 3)")
    if selector.modern_only is not None:
        unsupported.append("modern_only: (Day 3)")
    if selector.min_coverage is not None:
        unsupported.append("min_coverage: (Day 3)")
    if selector.coverage_column is not None:
        unsupported.append("coverage_column: (Day 3)")
    if selector.resolve_to_version is not None or selector.source_version is not None:
        unsupported.append("cross-version (Day 6)")

    if unsupported:
        raise UsageError(
            errors=[
                ValidationError(
                    file="<selector>",
                    line=1,
                    col=1,
                    pointer="/",
                    message=(
                        f"selector uses feature(s) not yet implemented in this "
                        f"build: {', '.join(unsupported)}. See HLD project plan "
                        f"for the day each feature lands."
                    ),
                    constraint="feature_not_implemented",
                )
            ],
        )


def _dedup_preserve_order(items: list[str]) -> tuple[list[str], list[str]]:
    """Drop duplicates while preserving first-occurrence order. Returns
    (unique-in-order, duplicates-seen-after-first)."""
    seen: dict[str, None] = {}
    duplicates: list[str] = []
    for item in items:
        if item in seen:
            duplicates.append(item)
        else:
            seen[item] = None
    return list(seen.keys()), duplicates


def _filter_parallel(keys: list[str], values: list[str]) -> list[str]:
    """Given parallel lists, return values aligned with the first
    occurrence of each key (dropping values at duplicate-key positions)."""
    seen: set[str] = set()
    result: list[str] = []
    for k, v in zip(keys, values, strict=True):
        if k not in seen:
            seen.add(k)
            result.append(v)
    return result


__all__ = ["select_samples"]
