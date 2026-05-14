"""Public library API — aadr_subset.select().

Callable from Python pipelines without shelling out to the CLI.  Returns
a SubsetResult directly; the caller owns any output writing.

Key differences from the CLI:
  - allow_empty defaults True (zero matches returns an empty SubsetResult;
    check result.n_matched; raise SoftValidationFailure yourself if needed).
  - Warnings go to logging.warning("aadr_subset") instead of sys.stderr.
  - selector and anno may be pre-loaded objects (Selector / AnnoFrame) so
    callers that batch many selects can amortise AnnoFrame load cost.
  - No output writing, no timing, no terminal summary.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

import aadr_resolve
from aadr_resolve import AnnoFrame

from ._cmd_helpers import (
    normalize_coverage_flags,
    parse_schema_override,
    resolve_cross_version_inputs,
)
from .engine import select_samples
from .errors import IOFailure, SoftValidationFailure, UsageError, ValidationError
from .selector import compute_signature, load_selector
from .types import Selector, SubsetResult

logger = logging.getLogger("aadr_subset")


def select(
    selector: str | Path | Selector,
    anno: str | Path | AnnoFrame,
    *,
    source_anno: str | Path | AnnoFrame | None = None,
    coverage_column: str | None = None,
    coverage_derive: str | None = None,
    max_per_population: int | None = None,
    max_per_individual: int | None = None,
    include_matched_criteria: bool = False,
    allow_empty: bool = True,
    strict_resolve: bool = False,
    mid_bridge: str | Path | None = None,
    allow_empty_source: bool = False,
    schema_override: str | None = None,
) -> SubsetResult:
    """Evaluate a selector against an AADR .anno file and return the result.

    Parameters
    ----------
    selector:
        YAML selector — a filesystem path (str or Path) or a pre-loaded
        Selector object.  When a pre-loaded Selector is passed,
        result.selector_file is set to "<in-memory>".
    anno:
        Target AADR .anno file — a filesystem path or a pre-loaded AnnoFrame.
        Pre-loaded AnnoFrames skip the disk-load step, useful when batching
        multiple selects against the same file.
    source_anno:
        Source .anno for cross-version IID resolution (when the selector sets
        resolve_to_version:).  Accepts path or pre-loaded AnnoFrame.
    coverage_column:
        Override the coverage column name (e.g. "snps_hit_1240k").  Alias for
        coverage_derive; passing both raises UsageError.
    coverage_derive:
        Alias for coverage_column; the two are interchangeable.
    max_per_population:
        CLI-side per-population sampling cap.  Selector-side value wins when
        both are set.
    max_per_individual:
        CLI-side per-individual sampling cap.  Selector-side value wins.
    include_matched_criteria:
        Populate result.matched_criteria (per-GID contributing keys).
    allow_empty:
        When False, raise SoftValidationFailure if result.n_matched == 0.
        Defaults True (library callers typically check n_matched themselves).
    strict_resolve:
        Raise SoftValidationFailure if any cross-version IID fails to resolve.
    mid_bridge:
        Path to aadr-resolve MID-bridge file for cross-version resolution.
    allow_empty_source:
        Suppress errors from an empty individual_ids_source file.
    schema_override:
        Force .anno schema class ("A"–"E"); passed to AnnoFrame.from_path.

    Returns
    -------
    SubsetResult
        Populated result including selector_signature, anno_version, and all
        per-population / per-branch counts.  Output writing is the caller's
        responsibility (use formats.write_select_output if needed).

    Raises
    ------
    UsageError
        Bad selector YAML, schema violation, conflicting flags.
    IOFailure
        .anno file unreadable, unrecognised schema, or missing coverage column.
    SoftValidationFailure
        allow_empty=False and zero matches; or strict_resolve=True and one or
        more IIDs failed to resolve.
    """
    # 0. Normalise coverage flags (aliases; both-set → UsageError).
    cli_coverage_column = normalize_coverage_flags(coverage_column, coverage_derive)

    # 1. Load selector (accept path or pre-loaded object).
    if isinstance(selector, Selector):
        loaded_selector = selector
        selector_file_str = "<in-memory>"
    else:
        selector_path = Path(selector)
        _meta, loaded_selector = load_selector(
            selector_path,
            allow_empty_source=allow_empty_source,
        )
        selector_file_str = str(selector_path)

    # 2. Load target AnnoFrame (accept path or pre-loaded object).
    schema_override_enum = parse_schema_override(schema_override)
    if isinstance(anno, AnnoFrame):
        target_anno = anno
        anno_file_str = "<in-memory>"
    else:
        anno_path = Path(anno)
        try:
            target_anno = aadr_resolve.AnnoFrame.from_path(
                anno_path,
                schema_override=schema_override_enum,
            )
        except aadr_resolve.SchemaDetectionError as e:
            raise IOFailure(f"AADR .anno schema unrecognised: {e}") from e
        except (OSError, aadr_resolve.IOFailure) as e:
            raise IOFailure(f"cannot load .anno at {anno_path}: {e}") from e
        anno_file_str = str(anno_path)

    # 3. Cross-version flag check + optional source AnnoFrame load.
    source_anno_obj: AnnoFrame | None
    if isinstance(source_anno, AnnoFrame):
        source_anno_obj = source_anno
        # Still validate selector resolve_to_version consistency.
        _validate_cross_version_preloaded(loaded_selector, source_anno_obj, target_anno)
    else:
        source_anno_obj = resolve_cross_version_inputs(
            loaded_selector,
            source_anno=str(source_anno) if source_anno is not None else None,
            target_anno=target_anno,
            schema_override_enum=schema_override_enum,
        )

    # 4. v62 class-D + min_coverage warning (library path emits via logging).
    if loaded_selector.coverage_column is None and cli_coverage_column is None:
        _maybe_log_v62_coverage_warning(target_anno, loaded_selector)

    # 5. Engine evaluation.
    result = select_samples(
        target_anno,
        loaded_selector,
        source_anno=source_anno_obj,
        mid_bridge=Path(mid_bridge) if mid_bridge else None,
        strict_resolve=strict_resolve,
        coverage_column=cli_coverage_column,
        max_per_population=max_per_population,
        max_per_individual=max_per_individual,
        include_matched_criteria=include_matched_criteria,
    )

    # 5a. Glob-expansion warning.
    if result.warnings.empty_glob_patterns:
        patterns = result.warnings.empty_glob_patterns
        logger.warning(
            "%d Group_ID glob pattern(s) matched zero labels in %s: %s. "
            "Check for typos or AADR version drift.",
            len(patterns),
            target_anno.version,
            patterns,
        )

    # 5b. Cross-version missing-IID warning (non-strict path).
    if result.warnings.missing_after_resolve and not strict_resolve:
        missing = result.warnings.missing_after_resolve
        shown = missing[:10]
        more = "" if len(missing) <= 10 else f" (+{len(missing) - 10} more)"
        logger.warning(
            "%d Individual_ID(s) failed to resolve from %s to %s: %s%s. "
            "Pass strict_resolve=True to raise instead.",
            len(missing),
            loaded_selector.source_version,
            loaded_selector.resolve_to_version,
            shown,
            more,
        )

    # 6. allow_empty gate.
    if result.n_matched == 0 and not allow_empty:
        raise SoftValidationFailure(
            "selector matched 0 samples. Pass allow_empty=True to suppress."
        )

    # 7. Compute selector signature.
    sig = compute_signature(
        loaded_selector,
        cli_coverage_column=cli_coverage_column,
        cli_max_per_population=max_per_population,
        cli_max_per_individual=max_per_individual,
    )

    # 8. Populate run-env metadata.
    effective_cov_col = loaded_selector.coverage_column or cli_coverage_column
    result = replace(
        result,
        anno_file=anno_file_str,
        anno_version=target_anno.version,
        schema_class=target_anno.schema_class.value,
        selector_file=selector_file_str,
        selector_signature=sig,
        coverage_column_used=effective_cov_col,
    )

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _validate_cross_version_preloaded(
    selector: Selector,
    source_af: AnnoFrame,
    target_af: AnnoFrame,
) -> None:
    """Version-consistency checks when a pre-loaded source AnnoFrame is passed.

    Mirrors the checks inside resolve_cross_version_inputs but skips the
    loading step (already done by the caller).
    """
    if selector.resolve_to_version is None:
        # source_anno supplied but selector has no resolve_to_version.
        raise UsageError(
            errors=[
                ValidationError(
                    file="<api>",
                    line=1,
                    col=1,
                    pointer="/source_anno",
                    message=(
                        "source_anno is meaningful only with cross-version "
                        "resolution; selector does not set resolve_to_version"
                    ),
                )
            ],
        )
    if selector.source_version is not None and source_af.version != selector.source_version:
        raise UsageError(
            errors=[
                ValidationError(
                    file="<api>",
                    line=1,
                    col=1,
                    pointer="/source_anno",
                    message=(
                        f"source_anno version is {source_af.version!r} but "
                        f"selector source_version is {selector.source_version!r}"
                    ),
                )
            ],
        )
    if target_af.version != selector.resolve_to_version:
        raise UsageError(
            errors=[
                ValidationError(
                    file="<api>",
                    line=1,
                    col=1,
                    pointer="/resolve_to_version",
                    message=(
                        f"target anno version is {target_af.version!r} but "
                        f"selector resolve_to_version is "
                        f"{selector.resolve_to_version!r}"
                    ),
                )
            ],
        )


def _maybe_log_v62_coverage_warning(anno: AnnoFrame, selector: Selector) -> None:
    """Log a warning when a class-D anno is used with min_coverage filters.

    Class-D inputs (v62.0) have no native coverage column; min_coverage
    filters silently produce empty results unless a derived proxy is opted
    in via coverage_column / coverage_derive.
    """
    if anno.schema_class.value != "D":
        return
    selector_has_min_coverage = selector.min_coverage is not None or any(
        b.min_coverage is not None for b in selector.any_branches
    )
    if not selector_has_min_coverage:
        return
    logger.warning(
        "v62.0 input has no native coverage column; min_coverage filter "
        "selects nothing. Pass coverage_column='snps_hit_1240k' for a "
        "derived proxy."
    )


__all__ = ["select"]
