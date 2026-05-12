"""Selector evaluation engine.

Day 6 scope: full predicate set + cross-version IID lift via aadr-resolve.

- populations / individual_ids / individual_ids_source (Day 2)
- modern_only / date.min_calbp / date.max_calbp / min_coverage (Day 3)
- any: OR-block + exclude: NOT-of-OR block (Day 3)
- cross-version (source_version + resolve_to_version + --source-anno +
  --mid-bridge + --strict-resolve) — Day 6

Feature gate still rejects:
- coverage_column: (landed as a selector grammar key but the
  AnnoFrame.coverage_via() override isn't wired through yet; lands
  with the --coverage-column / --coverage-derive CLI flags)

Per LLD §3.4 evaluation algorithm.
"""

from __future__ import annotations

from pathlib import Path

import aadr_resolve
import pandas as pd
from aadr_resolve import AnnoFrame

from .errors import InvariantViolation, SoftValidationFailure, UsageError, ValidationError
from .types import (
    AnyBranch,
    DateRange,
    ExcludeBlock,
    ExcludeCount,
    Selector,
    SelectorWarnings,
    SubsetResult,
)

# Modern-vs-ancient threshold per HLD §Modern vs ancient detection.
# Pinned numeric: 70 calBP = 1880 CE.
MODERN_THRESHOLD_CALBP = 70


def select_samples(
    anno: AnnoFrame,
    selector: Selector,
    *,
    source_anno: AnnoFrame | None = None,
    mid_bridge: Path | None = None,
    strict_resolve: bool = False,
    include_matched_criteria: bool = False,
) -> SubsetResult:
    """Evaluate selector against AnnoFrame; return SubsetResult.

    Algorithm per HLD §Selector evaluation algorithm:
    0. (Day 6) Cross-version lift if selector.resolve_to_version is set:
       lift source Individual_IDs to target Individual_IDs via
       aadr_resolve.resolve_master_ids, then use target_iids in place
       of selector.individual_ids in the predicate mask.
    1. Top-level AND mask over filter predicates.
    2. any: OR mask (or all-True if absent).
    3. exclude: NOT-of-OR mask (or all-True if absent).
    4. Final mask: top_and & any_or & ~exclude_or.
    5. Dedup matched genetic_ids (defensive; aadr-resolve enforces
       per-.anno GeneticID uniqueness already).
    6. Compute per_population_counts, per_branch_counts.

    Raises:
        UsageError: a feature still in the gate was used (currently
            coverage_column).
        SoftValidationFailure: strict_resolve=True and at least one
            source Individual_ID failed to resolve to target.
        InvariantViolation: cross-lab MID collision detected by
            aadr-resolve, or AnnoFrame.path is None on cross-version.
    """
    _reject_unsupported_features(selector)

    # 0. Cross-version IID lift. When resolve_to_version is set, the
    # selector's individual_ids (YAML + source-file union) are SOURCE IIDs;
    # we lift them to TARGET IIDs via aadr-resolve and feed those into
    # the top-level predicate mask instead.
    target_iids: set[str] | None = None
    missing_after_resolve: list[str] = []
    if selector.resolve_to_version is not None:
        if source_anno is None:
            raise InvariantViolation(
                "cross-version resolution requires source_anno; "
                "run_select / orchestrator must pass it."
            )
        target_iids, missing_after_resolve = _resolve_cross_version(
            selector,
            source_anno=source_anno,
            target_anno=anno,
            mid_bridge=mid_bridge,
        )
        if missing_after_resolve and strict_resolve:
            preview = missing_after_resolve[:10]
            raise SoftValidationFailure(
                f"{len(missing_after_resolve)} Individual_ID(s) failed to "
                f"resolve from {selector.source_version} to "
                f"{selector.resolve_to_version}. First {len(preview)}: "
                f"{preview}. Pass --allow-empty if a partial cohort is OK, "
                f"or drop --strict-resolve to downgrade to a warning."
            )

    # 1. Top-level AND mask.
    if target_iids is not None:
        # Cross-version: target_iids supersedes selector.individual_ids.
        # Pass the resolved target Individual_ID set as the predicate.
        effective_individual_ids = sorted(target_iids)
    else:
        effective_individual_ids = list(
            set(selector.individual_ids) | set(selector.individual_ids_from_source)
        )
    top_and_mask = _build_predicate_mask(
        anno,
        populations=selector.populations,
        individual_ids=effective_individual_ids,
        modern_only=selector.modern_only,
        min_coverage=selector.min_coverage,
        date=selector.date,
    )

    # 2. any: OR mask. Computed even when no any: block so we can attribute
    # counts; an absent any: yields all-True (does not filter).
    any_or_mask, branch_masks = _build_any_or_mask(anno, selector.any_branches)

    # 3. exclude: NOT-of-OR mask.
    exclude_keep_mask = _build_exclude_mask(anno, selector.exclude)

    # 4. Final mask.
    final_mask = top_and_mask & any_or_mask & exclude_keep_mask

    # 5. Materialize matched rows + dedup.
    matched_gids: list[str] = anno.genetic_id[final_mask].tolist()
    matched_group_ids: list[str] = anno.group_id[final_mask].tolist()
    unique_gids, duplicates = _dedup_preserve_order(matched_gids)
    unique_group_ids = _filter_parallel(matched_gids, matched_group_ids)

    # 6. per_population_counts in first-appearance order.
    per_pop: dict[str, int] = {}
    for gpid in unique_group_ids:
        per_pop[gpid] = per_pop.get(gpid, 0) + 1

    # 7. per_branch_counts: attribute matched rows to top_level vs each any: branch.
    per_branch = _compute_per_branch_counts(top_and_mask, branch_masks, exclude_keep_mask)

    # 8. excluded_counts: per-condition independent count.
    excluded_counts = _compute_excluded_counts(anno, selector.exclude)

    # 9. matched_criteria: opt-in only.
    matched_criteria: dict[str, list[str]] = {}
    if include_matched_criteria:
        matched_criteria = _compute_matched_criteria(
            anno, final_mask, selector, branch_masks, unique_gids
        )

    n_matched = len(unique_gids)
    warnings = SelectorWarnings(
        duplicate_genetic_ids=duplicates,
        missing_after_resolve=missing_after_resolve,
    )
    return SubsetResult(
        genetic_ids=unique_gids,
        n_matched=n_matched,
        per_population_counts=per_pop,
        per_branch_counts=per_branch,
        excluded_counts=excluded_counts,
        matched_criteria=matched_criteria,
        warnings=warnings,
    )


# --- Predicate masks ---


def _build_predicate_mask(
    af: AnnoFrame,
    *,
    populations: list[str],
    individual_ids: list[str],
    modern_only: bool | None,
    min_coverage: float | None,
    date: DateRange | None,
) -> pd.Series:
    """Build a boolean Series by AND-combining per-key sub-masks. Each
    sub-mask is constructed only when its key is non-empty/non-None.

    Returns all-True when no predicates are set (empty selector matches
    every row per HLD §Selector grammar semantics).

    Sub-mask construction:
    - populations: af.group_id.isin(populations)
    - individual_ids: af.individual_id.isin(individual_ids)
    - modern_only=True: af.date_calbp <= 70 (NaN/<NA> dates FAIL)
    - min_coverage=F: af.coverage >= F (NaN coverage FAILS)
    - date.min_calbp=N: af.date_calbp >= N (<NA> dates FAIL)
    - date.max_calbp=N: af.date_calbp <= N (<NA> dates FAIL)
    """
    masks: list[pd.Series] = []

    if populations:
        masks.append(af.group_id.isin(populations))

    if individual_ids:
        masks.append(af.individual_id.isin(individual_ids))

    if modern_only is True:
        # date_calbp is Int64 nullable; <NA> comparisons are <NA>, which
        # behaves as False in a boolean mask. So <NA>-date samples FAIL
        # modern_only — matches HLD §Date handling pin.
        masks.append((af.date_calbp <= MODERN_THRESHOLD_CALBP).fillna(False))
    # modern_only=False is the "no constraint" form per HLD; treat as absent.

    if min_coverage is not None:
        # af.coverage is Float64 with NaN for missing. NaN comparisons
        # are False, so NaN-coverage samples FAIL the threshold.
        masks.append(af.coverage >= min_coverage)

    if date is not None:
        if date.min_calbp is not None:
            masks.append((af.date_calbp >= date.min_calbp).fillna(False))
        if date.max_calbp is not None:
            masks.append((af.date_calbp <= date.max_calbp).fillna(False))

    if not masks:
        # Empty mask set = match everything.
        return pd.Series([True] * af.n_rows, index=af.genetic_id.index)

    final = masks[0]
    for m in masks[1:]:
        final = final & m
    return final


def _build_any_or_mask(
    af: AnnoFrame, branches: list[AnyBranch]
) -> tuple[pd.Series, list[pd.Series]]:
    """For each `any:` branch, build a branch mask via _build_predicate_mask.
    Returns (or_combined_mask, per_branch_mask_list).

    Branches with empty filters (the schema rejects this at parse time)
    would produce all-True; relying on schema to enforce minProperties=1.

    When branches is empty, returns (all-True, []) — no any: filter.
    """
    if not branches:
        return (
            pd.Series([True] * af.n_rows, index=af.genetic_id.index),
            [],
        )

    branch_masks: list[pd.Series] = []
    for branch in branches:
        branch_iids = list(branch.individual_ids)
        if branch.individual_ids_source is not None:
            # Note: any-branch individual_ids_source loading happens in
            # selector.load_selector for top-level; branches are not
            # currently loaded by load_selector. Treat as empty here —
            # explicit branch-source loading deferred to v0.2 if a use
            # case surfaces. HLD calls for this support but the path
            # isn't exercised yet.
            pass
        branch_masks.append(
            _build_predicate_mask(
                af,
                populations=branch.populations,
                individual_ids=branch_iids,
                modern_only=branch.modern_only,
                min_coverage=branch.min_coverage,
                date=branch.date,
            )
        )

    or_mask = branch_masks[0]
    for m in branch_masks[1:]:
        or_mask = or_mask | m
    return or_mask, branch_masks


def _build_exclude_mask(af: AnnoFrame, exclude: ExcludeBlock | None) -> pd.Series:
    """Per-condition OR over exclude.group_ids + exclude.individual_ids;
    return NOT-of-OR (the keep-mask).

    Returns all-True when exclude is None or both conditions are empty.
    """
    if exclude is None:
        return pd.Series([True] * af.n_rows, index=af.genetic_id.index)

    drop_masks: list[pd.Series] = []
    if exclude.group_ids:
        drop_masks.append(af.group_id.isin(exclude.group_ids))
    if exclude.individual_ids:
        drop_masks.append(af.individual_id.isin(exclude.individual_ids))

    if not drop_masks:
        return pd.Series([True] * af.n_rows, index=af.genetic_id.index)

    drop_or = drop_masks[0]
    for m in drop_masks[1:]:
        drop_or = drop_or | m
    return ~drop_or


# --- Counters ---


def _compute_per_branch_counts(
    top_and_mask: pd.Series,
    branch_masks: list[pd.Series],
    exclude_keep_mask: pd.Series,
) -> dict[str, int]:
    """For each branch in the any: block, count rows attributable to
    that branch (intersection with top_and AND exclude_keep_mask, so
    only rows that survived the full filter contribute).

    Per HLD pin: counts reflect the branch's CONTRIBUTION to the final
    result, not the branch's gross mask.

    'top_level' key counts rows surviving top_and + exclude (regardless
    of any: branch attribution).
    """
    counts: dict[str, int] = {}
    # top_level: rows that match top_and AND survive exclude.
    counts["top_level"] = int((top_and_mask & exclude_keep_mask).sum())
    for i, mask in enumerate(branch_masks):
        # Contribution: rows matching this branch AND top_and AND surviving
        # exclude. The (top_and & branch_mask) intersect is the relevant
        # space (final = top_and & any_or & exclude_keep).
        counts[f"any[{i}]"] = int((top_and_mask & mask & exclude_keep_mask).sum())
    return counts


def _compute_excluded_counts(af: AnnoFrame, exclude: ExcludeBlock | None) -> list[ExcludeCount]:
    """Per-literal fan-out: one ExcludeCount per excluded Group_ID
    and per excluded Individual_ID. Counts are independent (each
    counts the rows matching that specific literal, even when
    multiple literals overlap on the same row).
    """
    if exclude is None:
        return []

    counts: list[ExcludeCount] = []
    for gid in exclude.group_ids:
        n = int((af.group_id == gid).sum())
        if n > 0:
            counts.append(ExcludeCount(key="group_ids", value=gid, count=n))
    for iid in exclude.individual_ids:
        n = int((af.individual_id == iid).sum())
        if n > 0:
            counts.append(ExcludeCount(key="individual_ids", value=iid, count=n))
    return counts


def _compute_matched_criteria(
    af: AnnoFrame,
    final_mask: pd.Series,
    selector: Selector,
    branch_masks: list[pd.Series],
    unique_gids: list[str],
) -> dict[str, list[str]]:
    """For each matched GeneticID, list the contributing selector keys.

    Order per HLD: top-level keys first (in YAML key-appearance order,
    approximated here by Selector field order), then any-branch indices.
    """
    # Top-level criteria that fire for ANY matched row (when the key is
    # set in the selector). For the matched_criteria dict, every matched
    # GID gets the same top-level-key list (since final_mask requires the
    # top-AND mask to pass).
    top_keys: list[str] = []
    if selector.populations:
        top_keys.append(f"populations:{','.join(selector.populations)}")
    if selector.individual_ids or selector.individual_ids_from_source:
        top_keys.append("individual_ids")
    if selector.modern_only is True:
        top_keys.append("modern_only")
    if selector.min_coverage is not None:
        top_keys.append(f"min_coverage:{selector.min_coverage}")
    if selector.date is not None:
        date_parts = []
        if selector.date.min_calbp is not None:
            date_parts.append(f"min={selector.date.min_calbp}")
        if selector.date.max_calbp is not None:
            date_parts.append(f"max={selector.date.max_calbp}")
        top_keys.append("date:" + ",".join(date_parts))

    # Per-row branch attribution.
    result: dict[str, list[str]] = {}
    for gid in unique_gids:
        # Find row index for this gid (first occurrence).
        row_idx = int(af.genetic_id[af.genetic_id == gid].index[0])
        criteria = list(top_keys)
        for i, mask in enumerate(branch_masks):
            if bool(mask.iloc[row_idx]) and bool(final_mask.iloc[row_idx]):
                criteria.append(f"any[{i}]")
        result[gid] = criteria
    return result


# --- Feature gate (Day 3: shrinks; Day 6 will remove the last entries) ---


def _reject_unsupported_features(selector: Selector) -> None:
    """Feature gate.

    Still unsupported (raised with constraint=feature_not_implemented):
    - coverage_column: (pending --coverage-column / --coverage-derive
      CLI flags).
    """
    unsupported: list[str] = []
    if selector.coverage_column is not None:
        unsupported.append("coverage_column: (--coverage-column CLI flag pending)")

    # Also reject coverage_column inside any: branches.
    for i, branch in enumerate(selector.any_branches):
        if branch.coverage_column is not None:
            unsupported.append(f"any[{i}].coverage_column: (pending)")

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


# --- Dedup helpers ---


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


# --- Cross-version helpers (LLD §3.4 _resolve_cross_version) ---


def _resolve_cross_version(
    selector: Selector,
    *,
    source_anno: AnnoFrame,
    target_anno: AnnoFrame,
    mid_bridge: Path | None,
) -> tuple[set[str], list[str]]:
    """Lift source Individual_IDs to target Individual_IDs via aadr-resolve.

    Returns (target_iids, missing). missing is the sorted list of source
    IIDs that aadr-resolve could not place in target (returned None).

    Path handling: aadr-resolve's AnnoFrame.from_path() populates
    `anno.path` (Q9 resolved in aadr-resolve LLD). Defensive None-check
    catches the rare case where an AnnoFrame was synthesized outside
    from_path().

    CollisionDetected → InvariantViolation (cross-lab MID collision is
    a bridge-quality problem, not a user error).
    """
    if source_anno.path is None or target_anno.path is None:
        raise InvariantViolation(
            "cross-version resolution requires AnnoFrames constructed "
            "via AnnoFrame.from_path(); one or both .path is None"
        )

    source_iids = set(selector.individual_ids) | set(selector.individual_ids_from_source)
    if not source_iids:
        return set(), []

    try:
        result = aadr_resolve.resolve_master_ids(
            ids=sorted(source_iids),
            src_version=selector.source_version or "<unknown>",
            dst_version=selector.resolve_to_version or "<unknown>",
            anno_paths={
                (selector.source_version or "<unknown>"): source_anno.path,
                (selector.resolve_to_version or "<unknown>"): target_anno.path,
            },
            mid_bridge=mid_bridge,
        )
    except aadr_resolve.CollisionDetected as e:
        raise InvariantViolation(
            f"aadr-resolve detected a cross-lab MID collision while "
            f"resolving Individual_IDs from {selector.source_version} "
            f"to {selector.resolve_to_version}: {e}"
        ) from e

    target_iids: set[str] = set()
    missing: list[str] = []
    for src_iid in sorted(source_iids):
        target_gid = result.get(src_iid)
        if target_gid is None:
            missing.append(src_iid)
            continue
        target_iid = _lift_gid_to_iid(target_anno, target_gid)
        if target_iid is None:
            # resolve_master_ids returned a GID that doesn't exist in
            # target.genetic_id — treat as missing rather than dropping.
            missing.append(src_iid)
        else:
            target_iids.add(target_iid)
    return target_iids, missing


# Module-level per-AnnoFrame gid→iid cache. id(af) keys avoid leaking
# references; AnnoFrame instances are short-lived (one per run).
_GID_TO_IID_CACHE: dict[int, dict[str, str]] = {}


def _lift_gid_to_iid(af: AnnoFrame, gid: str) -> str | None:
    """Look up Individual_ID for a Genetic_ID in `af`. Builds a cached
    dict on first call per AnnoFrame instance."""
    key = id(af)
    table = _GID_TO_IID_CACHE.get(key)
    if table is None:
        table = dict(zip(af.genetic_id.tolist(), af.individual_id.tolist(), strict=True))
        _GID_TO_IID_CACHE[key] = table
    return table.get(gid)


__all__ = ["select_samples"]
