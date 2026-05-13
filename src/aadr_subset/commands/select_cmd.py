"""select subcommand orchestrator.

Day 6: full HLD select surface end-to-end —
- Single-version selectors (Day 2 surface)
- ids / tsv / json output (Day 4)
- Selector signature (Day 5)
- Cross-version IID lift via --source-anno + selector.resolve_to_version
  + optional --mid-bridge + --strict-resolve

Per LLD §3.9 / §4.1.
"""

from __future__ import annotations

import sys
import time
from dataclasses import replace
from pathlib import Path

import aadr_resolve

from ..engine import select_samples
from ..errors import (
    EXIT_SUCCESS,
    IOFailure,
    SoftValidationFailure,
    UsageError,
    ValidationError,
)
from ..formats import write_select_output
from ..reporting import format_stdout_summary
from ..selector import compute_signature, load_selector
from ..types import OutputFormat, Selector


def run_select(
    *,
    selector_path: str,
    anno_path: str,
    out: str | None,
    fmt: str,
    schema_override: str | None,
    allow_empty: bool,
    allow_empty_source: bool,
    include_matched_criteria: bool,
    source_anno: str | None = None,
    mid_bridge: str | None = None,
    strict_resolve: bool = False,
    coverage_column: str | None = None,
    coverage_derive: str | None = None,
    quiet: bool = False,
) -> int:
    """Orchestrate `aadr-subset select`. Returns exit code per HLD §Exit codes.

     Day-6 sequence (§4.1):
     1. Load + validate selector.
     2. Load target AnnoFrame from anno_path.
     3. Cross-version flag check + (optional) source AnnoFrame load.
     4. v62 class-D coverage warning if applicable.
     5. engine.select_samples (timed).
     6. Exit-1 gate: n_matched == 0 and not allow_empty → SoftValidationFailure.
     7. Compute selector signature.
     8. Populate run-env metadata.
     9. Write output via write_select_output (timed).
    10. Stdout summary unless quiet.
    11. Return EXIT_SUCCESS.
    """
    # 0. Normalize coverage flags. --coverage-column and --coverage-derive
    # are aliases (HLD §Coverage handling); both-set → UsageError.
    cli_coverage_column = _normalize_coverage_flags(coverage_column, coverage_derive)

    # 1. Load + validate selector.
    t_parse_start = time.monotonic()
    _metadata, selector = load_selector(
        selector_path,
        allow_empty_source=allow_empty_source,
    )

    # 2. Load target AnnoFrame.
    schema_override_enum = _parse_schema_override(schema_override)
    try:
        anno = aadr_resolve.AnnoFrame.from_path(
            anno_path,
            schema_override=schema_override_enum,
        )
    except aadr_resolve.SchemaDetectionError as e:
        raise IOFailure(f"AADR .anno schema unrecognized: {e}") from e
    except (OSError, aadr_resolve.IOFailure) as e:
        raise IOFailure(f"cannot load .anno at {anno_path}: {e}") from e

    # 3. Cross-version branch (LLD §4.1 step 4).
    source_anno_frame = _resolve_cross_version_inputs(
        selector,
        source_anno=source_anno,
        target_anno=anno,
        schema_override_enum=schema_override_enum,
    )

    t_parse_end = time.monotonic()
    parse_time = t_parse_end - t_parse_start

    # 4. v62 class-D coverage warning. Only fires when no override is
    # supplied (selector-level OR CLI-level); --coverage-column /
    # --coverage-derive routes the filter to a real Series so the
    # silently-empty trap no longer applies.
    if selector.coverage_column is None and cli_coverage_column is None:
        _emit_v62_coverage_warning_if_needed(anno, selector)

    # 5. Engine evaluation (timed).
    t_eval_start = time.monotonic()
    result = select_samples(
        anno,
        selector,
        source_anno=source_anno_frame,
        mid_bridge=Path(mid_bridge) if mid_bridge else None,
        strict_resolve=strict_resolve,
        coverage_column=cli_coverage_column,
        include_matched_criteria=include_matched_criteria,
    )
    eval_time = time.monotonic() - t_eval_start

    # 5a. Glob-expansion warning. Patterns that matched zero Group_IDs
    # in the target .anno are surfaced — almost certainly a typo
    # (`Egnland_*` instead of `England_*`) or a version mismatch.
    if result.warnings.empty_glob_patterns:
        patterns = result.warnings.empty_glob_patterns
        sys.stderr.write(
            f"WARNING: {len(patterns)} Group_ID glob pattern(s) matched zero "
            f"labels in {anno.version}: {patterns}. Check for typos or AADR "
            f"version drift.\n"
        )

    # 5b. Cross-version missing-IID stderr warning (non-strict path).
    # strict_resolve already raised SoftValidationFailure inside engine
    # if there were missing IIDs; if we got here with missing entries,
    # surface them as a warning to stderr.
    if result.warnings.missing_after_resolve and not strict_resolve:
        missing = result.warnings.missing_after_resolve
        shown = missing[:10]
        more = "" if len(missing) <= 10 else f" (+{len(missing) - 10} more)"
        sys.stderr.write(
            f"WARNING: {len(missing)} Individual_ID(s) failed to resolve from "
            f"{selector.source_version} to {selector.resolve_to_version}: "
            f"{shown}{more}. Pass --strict-resolve to fail on this.\n"
        )

    # 6. Exit-1 gates.
    if result.n_matched == 0 and not allow_empty:
        raise SoftValidationFailure(
            "selector matched 0 samples — output not written. "
            "Pass --allow-empty for a sentinel-file write."
        )

    # 7. Compute selector signature. CLI coverage_column injects into
    # the signature ONLY when the selector itself doesn't pin one
    # (selector wins per HLD §Coverage handling).
    sig = compute_signature(selector, cli_coverage_column=cli_coverage_column)

    # 8. Populate run-env metadata on the result. coverage_column_used
    # records the effective post-merge value (selector wins over CLI).
    effective_cov_col = selector.coverage_column or cli_coverage_column
    result = replace(
        result,
        anno_file=str(anno_path),
        anno_version=anno.version,
        schema_class=anno.schema_class.value,
        selector_file=selector_path,
        selector_signature=sig,
        coverage_column_used=effective_cov_col,
    )

    # 6. Write output (TSV / JSON / IDs via formats.py dispatcher).
    fmt_enum = OutputFormat(fmt)
    t_write_start = time.monotonic()
    write_select_output(
        result,
        anno,
        fmt=fmt_enum,
        out_path=Path(out) if out else None,
        include_matched_criteria=include_matched_criteria,
    )
    write_time = time.monotonic() - t_write_start

    # 7. Stdout summary (to stderr; output goes to stdout when out is None).
    if not quiet:
        sys.stderr.write(
            format_stdout_summary(
                result,
                anno=anno,
                parse_time=parse_time,
                eval_time=eval_time,
                write_time=write_time,
                out_path_str=out,
                selector_file=selector_path,
            )
            + "\n"
        )

    return EXIT_SUCCESS


def _normalize_coverage_flags(
    coverage_column: str | None, coverage_derive: str | None
) -> str | None:
    """Merge --coverage-column / --coverage-derive into a single value.

    Per HLD §Coverage handling the two flags are aliases; passing both is
    a usage error so the conflict is surfaced rather than silently
    favoring one.
    """
    if coverage_column is not None and coverage_derive is not None:
        raise UsageError(
            errors=[
                ValidationError(
                    file="<cli>",
                    line=1,
                    col=1,
                    pointer="/--coverage-column",
                    message=("--coverage-column and --coverage-derive are aliases; pass only one."),
                )
            ],
        )
    return coverage_column or coverage_derive


def _resolve_cross_version_inputs(
    selector: Selector,
    *,
    source_anno: str | None,
    target_anno: aadr_resolve.AnnoFrame,
    schema_override_enum: aadr_resolve.types.SchemaClass | None,
) -> aadr_resolve.AnnoFrame | None:
    """Validate cross-version flag/selector combinations + load source .anno
    when both are present. Returns the source AnnoFrame or None.

    Rules (LLD §4.1 step 4):
    - resolve_to_version is None + source_anno is None: single-version path.
    - resolve_to_version is None + source_anno is set: UsageError.
    - resolve_to_version is set + source_anno is None: UsageError.
    - Both set: load source AnnoFrame; verify version match against
      selector.source_version (UsageError on mismatch); verify target
      anno.version matches selector.resolve_to_version (UsageError on
      mismatch).
    """
    if selector.resolve_to_version is None:
        if source_anno is not None:
            raise UsageError(
                errors=[
                    ValidationError(
                        file="<cli>",
                        line=1,
                        col=1,
                        pointer="/--source-anno",
                        message=(
                            "--source-anno is meaningful only with cross-version "
                            "resolution; selector does not set resolve_to_version"
                        ),
                    )
                ],
            )
        return None

    # resolve_to_version is set.
    if source_anno is None:
        raise UsageError(
            errors=[
                ValidationError(
                    file=str(selector.resolve_to_version),
                    line=1,
                    col=1,
                    pointer="/resolve_to_version",
                    message=(
                        f"selector sets resolve_to_version: "
                        f"{selector.resolve_to_version} but --source-anno was "
                        f"not provided"
                    ),
                )
            ],
        )

    # Load source AnnoFrame.
    try:
        source_af = aadr_resolve.AnnoFrame.from_path(
            source_anno,
            schema_override=schema_override_enum,
        )
    except aadr_resolve.SchemaDetectionError as e:
        raise IOFailure(f"source .anno schema unrecognized: {e}") from e
    except (OSError, aadr_resolve.IOFailure) as e:
        raise IOFailure(f"cannot load source .anno at {source_anno}: {e}") from e

    # Verify source version matches selector.source_version (if set).
    if selector.source_version is not None and source_af.version != selector.source_version:
        raise UsageError(
            errors=[
                ValidationError(
                    file=str(source_anno),
                    line=1,
                    col=1,
                    pointer="/--source-anno",
                    message=(
                        f"--source-anno version is {source_af.version!r} but "
                        f"selector source_version is {selector.source_version!r}"
                    ),
                )
            ],
        )

    # Verify target version matches selector.resolve_to_version.
    if target_anno.version != selector.resolve_to_version:
        raise UsageError(
            errors=[
                ValidationError(
                    file=str(target_anno.path) if target_anno.path else "<target-anno>",
                    line=1,
                    col=1,
                    pointer="/resolve_to_version",
                    message=(
                        f"target .anno version is {target_anno.version!r} but "
                        f"selector resolve_to_version is "
                        f"{selector.resolve_to_version!r}"
                    ),
                )
            ],
        )

    return source_af


def _emit_v62_coverage_warning_if_needed(anno: aadr_resolve.AnnoFrame, selector: Selector) -> None:
    """Class-D inputs (v62.0; no native coverage column) cause min_coverage
    filters to silently produce empty results unless a derived proxy is
    opted in. Emit a stderr WARNING when this combination is detected.

    Per HLD §Coverage handling. Check uses schema_class (canonical) rather
    than af.version (which depends on aadr-resolve's filename inference and
    is fragile in tests). The --coverage-derive / --coverage-column
    opt-in lands later; this warning is informational until then.
    """
    if anno.schema_class.value != "D":
        return
    selector_has_min_coverage = selector.min_coverage is not None or any(
        b.min_coverage is not None for b in selector.any_branches
    )
    if not selector_has_min_coverage:
        return
    sys.stderr.write(
        "WARNING: v62.0 input has no native coverage column; min_coverage "
        "filter selects nothing. Use `--coverage-derive snps_hit_1240k` "
        "(pending CLI flag) for a derived proxy.\n"
    )


def _parse_schema_override(value: str | None):  # type: ignore[no-untyped-def]
    """Map a CLI --schema-override CLASS letter to aadr_resolve.SchemaClass.
    None passes through (no override)."""
    if value is None:
        return None
    from aadr_resolve.types import SchemaClass

    try:
        return SchemaClass[value]
    except KeyError as e:
        raise UsageError(
            errors=[
                ValidationError(
                    file="<cli>",
                    line=1,
                    col=1,
                    pointer="/--schema-override",
                    message=(
                        f"unknown schema class '{value}'; expected one of "
                        f"{[c.name for c in SchemaClass]}"
                    ),
                )
            ],
        ) from e
