"""Selector evaluation engine.

Day 7 scope: full HLD v0.1 engine surface.

- populations / individual_ids / individual_ids_source (Day 2)
- modern_only / date.min_calbp / date.max_calbp / min_coverage (Day 3)
- any: OR-block + exclude: NOT-of-OR block (Day 3)
- cross-version (source_version + resolve_to_version + --source-anno +
  --mid-bridge + --strict-resolve) — Day 6
- coverage_column override via selector + CLI --coverage-column /
  --coverage-derive (Day 7). Per-branch coverage_column wins inside
  the branch.

Feature gate is now empty.

Per LLD §3.4 evaluation algorithm.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Callable
from pathlib import Path

import aadr_resolve
import pandas as pd
from aadr_resolve import AnnoFrame

from .errors import (
    InvariantViolation,
    IOFailure,
    SoftValidationFailure,
)
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
    coverage_column: str | None = None,
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
        SoftValidationFailure: strict_resolve=True and at least one
            source Individual_ID failed to resolve to target.
        InvariantViolation: cross-lab MID collision detected by
            aadr-resolve, or AnnoFrame.path is None on cross-version.
    """
    _reject_unsupported_features(selector)

    # Effective top-level coverage_column: selector wins over CLI.
    top_effective_cov_col = selector.coverage_column or coverage_column

    # Expand Group_ID glob patterns (v0.2). Patterns are detected by
    # presence of `*`, `?`, or `[`; otherwise the literal passes through.
    # Patterns that expand to zero matches in this .anno are collected
    # for the warnings field — they're a near-certain bug signal
    # (typo'd pattern, wrong AADR version, etc.).
    #
    # Empty-after-expansion semantics: when the user *had* a populations
    # constraint that resolved to zero groups, the result is "match
    # nothing" (all-False), NOT "no constraint" (all-True). Use None to
    # mean "no constraint", `[]` to mean "constraint set, matches none".
    available_groups = set(anno.group_id.unique().tolist())
    empty_glob_patterns: list[str] = []

    def _expand_if_set(literals: list[str]) -> list[str] | None:
        if not literals:
            return None
        return _expand_group_id_patterns(literals, available_groups, empty_glob_patterns)

    expanded_top_populations = _expand_if_set(selector.populations)
    if selector.exclude is not None and selector.exclude.group_ids:
        expanded_exclude_groups = _expand_group_id_patterns(
            selector.exclude.group_ids, available_groups, empty_glob_patterns
        )
    else:
        expanded_exclude_groups = []

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
        populations=expanded_top_populations,
        individual_ids=effective_individual_ids,
        modern_only=selector.modern_only,
        min_coverage=selector.min_coverage,
        date=selector.date,
        coverage_column=top_effective_cov_col,
    )

    # 2. any: OR mask. Computed even when no any: block so we can attribute
    # counts; an absent any: yields all-True (does not filter). Each branch
    # gets the top-level effective coverage_column as its fallback; branch-
    # level coverage_column overrides for that branch only. Branch
    # populations get the same glob expansion.
    def _expand_branch_populations(literals: list[str]) -> list[str]:
        # Branch populations expansion uses the same `available_groups`
        # set + `empty_glob_patterns` accumulator as the top-level
        # expansion so a single warnings list captures every empty
        # pattern across the selector.
        return _expand_group_id_patterns(literals, available_groups, empty_glob_patterns)

    any_or_mask, branch_masks = _build_any_or_mask(
        anno,
        selector.any_branches,
        top_coverage_column=top_effective_cov_col,
        expand_populations=_expand_branch_populations,
    )

    # 3. exclude: NOT-of-OR mask with expanded group_ids.
    exclude_keep_mask = _build_exclude_mask(
        anno, selector.exclude, expanded_group_ids=expanded_exclude_groups
    )

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

    # 8. excluded_counts: per-condition independent count. Uses the
    # expanded group_ids so a glob like `England_*` reports one row per
    # concrete England_IA / England_Viking / ... that contributed.
    excluded_counts = _compute_excluded_counts(
        anno, selector.exclude, expanded_group_ids=expanded_exclude_groups
    )

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
        empty_glob_patterns=empty_glob_patterns,
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
    populations: list[str] | None,
    individual_ids: list[str],
    modern_only: bool | None,
    min_coverage: float | None,
    date: DateRange | None,
    coverage_column: str | None = None,
) -> pd.Series:
    """Build a boolean Series by AND-combining per-key sub-masks. Each
    sub-mask is constructed only when its key is set / non-None.

    Returns all-True when no predicates are set (empty selector matches
    every row per HLD §Selector grammar semantics).

    Sub-mask construction:
    - populations: tri-state.
      - None: no constraint (skip).
      - []: constraint was set but resolved empty (e.g. a glob with zero
        matches in this .anno) → all-False contribution (match nothing).
      - non-empty: af.group_id.isin(populations).
    - individual_ids: af.individual_id.isin(individual_ids)
    - modern_only=True: af.date_calbp <= 70 (NaN/<NA> dates FAIL)
    - min_coverage=F: af.coverage >= F (NaN coverage FAILS).
      When coverage_column is set, af.coverage_via(coverage_column) is
      consulted instead. MissingNativeFieldError → IOFailure.
    - date.min_calbp=N: af.date_calbp >= N (<NA> dates FAIL)
    - date.max_calbp=N: af.date_calbp <= N (<NA> dates FAIL)
    """
    masks: list[pd.Series] = []

    if populations is not None:
        if populations:
            masks.append(af.group_id.isin(populations))
        else:
            # Constraint set, resolved empty → match nothing.
            masks.append(pd.Series([False] * af.n_rows, index=af.genetic_id.index))

    if individual_ids:
        masks.append(af.individual_id.isin(individual_ids))

    if modern_only is True:
        # date_calbp is Int64 nullable; <NA> comparisons are <NA>, which
        # behaves as False in a boolean mask. So <NA>-date samples FAIL
        # modern_only — matches HLD §Date handling pin.
        masks.append((af.date_calbp <= MODERN_THRESHOLD_CALBP).fillna(False))
    # modern_only=False is the "no constraint" form per HLD; treat as absent.

    if min_coverage is not None:
        cov_series = _coverage_series(af, coverage_column)
        # NaN comparisons are False, so NaN-coverage samples FAIL the threshold.
        masks.append(cov_series >= min_coverage)

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
    af: AnnoFrame,
    branches: list[AnyBranch],
    *,
    top_coverage_column: str | None = None,
    expand_populations: Callable[[list[str]], list[str]] | None = None,
) -> tuple[pd.Series, list[pd.Series]]:
    """For each `any:` branch, build a branch mask via _build_predicate_mask.
    Returns (or_combined_mask, per_branch_mask_list).

    Per HLD §Coverage handling, the effective coverage_column for a
    branch is `branch.coverage_column or top_coverage_column` (branch
    wins over top-level fallback).

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
        # Branch individual_ids = union of YAML-inline + file-loaded
        # (selector.load_selector populates `individual_ids_from_source`
        # for branches the same way it does for the top-level Selector).
        branch_iids = sorted(set(branch.individual_ids) | set(branch.individual_ids_from_source))
        branch_cov_col = branch.coverage_column or top_coverage_column
        # Tri-state populations: None = no constraint, [] = match nothing,
        # non-empty = isin. Apply the same expansion + None-vs-empty rule
        # the top-level uses.
        branch_pops: list[str] | None
        if not branch.populations:
            branch_pops = None
        elif expand_populations is not None:
            branch_pops = expand_populations(branch.populations)
        else:
            branch_pops = list(branch.populations)
        branch_masks.append(
            _build_predicate_mask(
                af,
                populations=branch_pops,
                individual_ids=branch_iids,
                modern_only=branch.modern_only,
                min_coverage=branch.min_coverage,
                date=branch.date,
                coverage_column=branch_cov_col,
            )
        )

    or_mask = branch_masks[0]
    for m in branch_masks[1:]:
        or_mask = or_mask | m
    return or_mask, branch_masks


def _build_exclude_mask(
    af: AnnoFrame,
    exclude: ExcludeBlock | None,
    *,
    expanded_group_ids: list[str] | None = None,
) -> pd.Series:
    """Per-condition OR over exclude.group_ids + exclude.individual_ids;
    return NOT-of-OR (the keep-mask).

    `expanded_group_ids` carries the post-glob-expansion concrete labels
    (caller-side); when supplied it takes precedence over
    `exclude.group_ids` (which may contain unexpanded patterns).

    Returns all-True when exclude is None or both conditions are empty.
    """
    if exclude is None:
        return pd.Series([True] * af.n_rows, index=af.genetic_id.index)

    drop_masks: list[pd.Series] = []
    group_ids_for_mask = expanded_group_ids if expanded_group_ids is not None else exclude.group_ids
    if group_ids_for_mask:
        drop_masks.append(af.group_id.isin(group_ids_for_mask))
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


def _compute_excluded_counts(
    af: AnnoFrame,
    exclude: ExcludeBlock | None,
    *,
    expanded_group_ids: list[str] | None = None,
) -> list[ExcludeCount]:
    """Per-literal fan-out: one ExcludeCount per excluded Group_ID
    and per excluded Individual_ID. Counts are independent (each
    counts the rows matching that specific literal, even when
    multiple literals overlap on the same row).

    `expanded_group_ids` overrides exclude.group_ids when supplied —
    used by select_samples to report concrete labels after glob
    expansion (`England_*` reports per-real-group-id rows, not one
    aggregate row for the pattern).
    """
    if exclude is None:
        return []

    counts: list[ExcludeCount] = []
    group_ids_for_counts = (
        expanded_group_ids if expanded_group_ids is not None else exclude.group_ids
    )
    for gid in group_ids_for_counts:
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
    """Feature gate. Empty as of Day 7 — full HLD v0.1 surface is wired.
    Retained as a no-op insertion point for v0.2 grammar extensions.
    """
    # Reserved for v0.2+ feature gates. Intentionally a no-op.
    _ = selector
    return


# --- Coverage column helper ---


def _coverage_series(af: AnnoFrame, coverage_column: str | None) -> pd.Series:
    """Pick the right coverage Series for a min_coverage check.

    None → af.coverage (native canonical column). Set → af.coverage_via(
    coverage_column); MissingNativeFieldError mapped to IOFailure so the
    user sees a clean exit-2 message instead of an internal traceback.
    """
    if coverage_column is None:
        return af.coverage
    try:
        return af.coverage_via(coverage_column)
    except aadr_resolve.MissingNativeFieldError as e:
        raise IOFailure(
            f"coverage column {coverage_column!r} is not available in "
            f"{af.version} (schema class {af.schema_class.value}): {e}"
        ) from e


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


# --- Group_ID glob expansion (v0.2) ---

# Characters that mark a literal as an fnmatch glob pattern. Anything
# else is a regular literal and passes through expansion verbatim.
_GLOB_CHARS = frozenset("*?[")


def _is_glob(literal: str) -> bool:
    return any(c in _GLOB_CHARS for c in literal)


def _expand_group_id_patterns(
    literals: list[str],
    available_groups: set[str],
    empty_patterns_out: list[str],
) -> list[str]:
    """Expand fnmatch-style glob patterns in a Group_ID literal list.

    Literals without glob characters pass through. Literals with `*`,
    `?`, or `[` are matched against `available_groups` (the unique
    Group_ID set from the target .anno) via fnmatch. Patterns that
    match zero groups are recorded in `empty_patterns_out` for the
    caller to surface as a warning.

    Returns a deduped list of concrete Group_ID labels preserving first-
    appearance order (literals first as they appear, then expanded
    matches in lexicographic order — fnmatch.filter returns the
    iteration order of `available_groups`, so we sort there for
    determinism across pandas versions).

    Patterns are NOT included in the signature canonicalization step —
    selector.compute_signature already hashes the patterns verbatim as
    they appear in selector.populations / exclude.group_ids. The
    EXPANSION is .anno-dependent and would couple the signature to the
    AADR release, defeating the reproducibility contract.
    """
    seen: dict[str, None] = {}
    for literal in literals:
        if _is_glob(literal):
            # Stable order: lexicographic over matched groups.
            matches = sorted(fnmatch.filter(available_groups, literal))
            if not matches:
                empty_patterns_out.append(literal)
                continue
            for m in matches:
                seen.setdefault(m, None)
        else:
            seen.setdefault(literal, None)
    return list(seen)


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
