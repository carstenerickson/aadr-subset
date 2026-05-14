"""select subcommand orchestrator.

Handles single-version and cross-version (resolve_to_version) selectors.
ids / tsv / json output, selector signature, glob expansion, sampling.
Multi-anno support (v0.4+) lives in the multi-anno branch of run_select.
"""

from __future__ import annotations

import sys
import time
from dataclasses import replace
from pathlib import Path

import aadr_resolve

from .._cmd_helpers import (
    normalize_coverage_flags as _normalize_coverage_flags,
    parse_schema_override as _parse_schema_override,
    resolve_cross_version_inputs as _resolve_cross_version_inputs,
)
from ..engine import merge_multi_anno_results, select_samples
from ..errors import (
    EXIT_SUCCESS,
    IOFailure,
    SoftValidationFailure,
    UsageError,
    ValidationError,
)
from ..formats import write_multi_anno_select_output, write_select_output
from ..reporting import format_run_summary
from ..selector import compute_signature, load_selector
from ..types import OutputFormat, Selector


# Known AADR release version ordering (ascending). Used to sort AnnoFrames
# passed to multi-anno select so newer-version rows win dedup.
# Unknown versions (not in this list) sort to the end (treated as newest).
_AADR_VERSION_ORDER = ["v44.3", "v50.0", "v52.2", "v54.1", "v62.0", "v66.0"]


def _version_sort_key(af: aadr_resolve.AnnoFrame) -> int:
    """Ascending sort key for AnnoFrames by AADR version."""
    try:
        return _AADR_VERSION_ORDER.index(af.version)
    except ValueError:
        return len(_AADR_VERSION_ORDER)  # unknown versions sort last


def run_select(
    *,
    selector_path: str,
    anno_paths: tuple[str, ...],
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
    max_per_population: int | None = None,
    max_per_individual: int | None = None,
    quiet: bool = False,
) -> int:
    """Orchestrate `aadr-subset select`. Returns exit code.

    Single-anno path (len(anno_paths)==1): existing pipeline — load selector,
    load anno, optional cross-version lift, engine evaluation, write output.

    Multi-anno path (len(anno_paths)>1, v0.4+): run select_samples per anno,
    merge via merge_multi_anno_results, write via write_multi_anno_select_output.
    Incompatible with resolve_to_version / --source-anno (hard UsageError).
    """
    # Dispatch to multi-anno path when more than one .anno path is given.
    if len(anno_paths) > 1:
        if source_anno is not None:
            raise UsageError(
                errors=[
                    ValidationError(
                        file="<cli>",
                        line=1,
                        col=1,
                        pointer="/--source-anno",
                        message=(
                            "--source-anno is not supported with multi-anno select; "
                            "pass a single .anno for cross-version IID lift."
                        ),
                    )
                ],
            )
        return _run_select_multi(
            selector_path=selector_path,
            anno_paths=anno_paths,
            out=out,
            fmt=fmt,
            schema_override=schema_override,
            allow_empty=allow_empty,
            allow_empty_source=allow_empty_source,
            include_matched_criteria=include_matched_criteria,
            mid_bridge=mid_bridge,
            coverage_column=coverage_column,
            coverage_derive=coverage_derive,
            max_per_population=max_per_population,
            max_per_individual=max_per_individual,
            quiet=quiet,
        )

    # --- Single-anno path ---
    anno_path = anno_paths[0]

    # 0. Normalize coverage flags. --coverage-column and --coverage-derive
    # are aliases; both-set → UsageError.
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
        max_per_population=max_per_population,
        max_per_individual=max_per_individual,
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

    # 7. Compute selector signature. CLI coverage_column / sampling
    # caps inject into the signature ONLY when the selector itself
    # doesn't pin them (selector-wins-per-field merge).
    sig = compute_signature(
        selector,
        cli_coverage_column=cli_coverage_column,
        cli_max_per_population=max_per_population,
        cli_max_per_individual=max_per_individual,
    )

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

    # 9. Write output (TSV / JSON / IDs via formats.py dispatcher).
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

    # 10. Stdout summary (to stderr; output goes to stdout when out is None).
    if not quiet:
        sys.stderr.write(
            format_run_summary(
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


def _run_select_multi(
    *,
    selector_path: str,
    anno_paths: tuple[str, ...],
    out: str | None,
    fmt: str,
    schema_override: str | None,
    allow_empty: bool,
    allow_empty_source: bool,
    include_matched_criteria: bool,
    mid_bridge: str | None,
    coverage_column: str | None,
    coverage_derive: str | None,
    max_per_population: int | None,
    max_per_individual: int | None,
    quiet: bool,
) -> int:
    """Multi-anno select path (v0.4+). Runs select_samples per anno, merges
    results via merge_multi_anno_results, writes via write_multi_anno_select_output.

    Incompatible flags that would need design work beyond v0.4 scope:
    --source-anno / resolve_to_version + multi-anno (hard UsageError).
    """
    cli_coverage_column = _normalize_coverage_flags(coverage_column, coverage_derive)

    t_parse_start = time.monotonic()
    _metadata, selector = load_selector(selector_path, allow_empty_source=allow_empty_source)

    # Guard: resolve_to_version + multi-anno is unsupported in v0.4.
    if selector.resolve_to_version is not None:
        raise UsageError(
            errors=[
                ValidationError(
                    file=selector_path,
                    line=1,
                    col=1,
                    pointer="/resolve_to_version",
                    message=(
                        "multi-anno (multiple .anno paths) and resolve_to_version: "
                        "are incompatible in v0.4. Use a single --anno for "
                        "cross-version IID lift."
                    ),
                )
            ],
        )

    schema_override_enum = _parse_schema_override(schema_override)

    # Load and sort AnnoFrames by version (ascending; newer-version rows win
    # during dedup in merge_multi_anno_results).
    anno_frames: list[aadr_resolve.AnnoFrame] = []
    for ap in anno_paths:
        try:
            af = aadr_resolve.AnnoFrame.from_path(ap, schema_override=schema_override_enum)
        except aadr_resolve.SchemaDetectionError as e:
            raise IOFailure(f"AADR .anno schema unrecognized at {ap}: {e}") from e
        except (OSError, aadr_resolve.IOFailure) as e:
            raise IOFailure(f"cannot load .anno at {ap}: {e}") from e
        anno_frames.append(af)

    anno_frames.sort(key=_version_sort_key)
    # Re-order anno_paths to match sorted anno_frames for accurate anno_files.
    sorted_anno_paths = [str(af.path) if af.path else "<in-memory>" for af in anno_frames]

    t_parse_end = time.monotonic()
    parse_time = t_parse_end - t_parse_start

    # v62 class-D coverage warning for each anno that qualifies.
    if selector.coverage_column is None and cli_coverage_column is None:
        for af in anno_frames:
            _emit_v62_coverage_warning_if_needed(af, selector)

    # Engine evaluation: one pass per anno.
    t_eval_start = time.monotonic()
    pairs: list[tuple[aadr_resolve.AnnoFrame, object]] = []
    all_empty_globs: list[str] = []
    for af in anno_frames:
        per_result = select_samples(
            af,
            selector,
            coverage_column=cli_coverage_column,
            max_per_population=max_per_population,
            max_per_individual=max_per_individual,
            include_matched_criteria=include_matched_criteria,
        )
        pairs.append((af, per_result))
        all_empty_globs.extend(per_result.warnings.empty_glob_patterns)

    # Glob-expansion warning (collected across all annos, deduped).
    unique_empty_globs = list(dict.fromkeys(all_empty_globs))
    if unique_empty_globs:
        sys.stderr.write(
            f"WARNING: {len(unique_empty_globs)} Group_ID glob pattern(s) matched "
            f"zero labels across all target annos: {unique_empty_globs}. "
            f"Check for typos or AADR version drift.\n"
        )

    # Merge results.
    merged = merge_multi_anno_results(
        pairs,  # type: ignore[arg-type]
        mid_bridge=Path(mid_bridge) if mid_bridge else None,
    )
    eval_time = time.monotonic() - t_eval_start

    # allow_empty gate.
    if merged.n_matched == 0 and not allow_empty:
        raise SoftValidationFailure(
            "selector matched 0 samples across all annos — output not written. "
            "Pass --allow-empty for a sentinel-file write."
        )

    # Selector signature: includes sorted anno_versions for multi-anno.
    sig = compute_signature(
        selector,
        cli_coverage_column=cli_coverage_column,
        cli_max_per_population=max_per_population,
        cli_max_per_individual=max_per_individual,
        anno_versions=[af.version for af in anno_frames],
    )

    effective_cov_col = selector.coverage_column or cli_coverage_column
    merged = replace(
        merged,
        anno_versions=sorted({af.version for af in anno_frames}),
        anno_files=sorted_anno_paths,
        selector_file=selector_path,
        selector_signature=sig,
        coverage_column_used=effective_cov_col,
        schema_class=anno_frames[-1].schema_class.value,
    )

    # Write output.
    fmt_enum = OutputFormat(fmt)
    t_write_start = time.monotonic()
    write_multi_anno_select_output(
        merged,
        pairs,  # type: ignore[arg-type]
        fmt=fmt_enum,
        out_path=Path(out) if out else None,
        include_matched_criteria=include_matched_criteria,
    )
    write_time = time.monotonic() - t_write_start

    # Stdout summary.
    if not quiet:
        versions_str = ", ".join(merged.anno_versions)
        sys.stderr.write(
            format_run_summary(
                merged,
                anno=anno_frames[-1],
                parse_time=parse_time,
                eval_time=eval_time,
                write_time=write_time,
                out_path_str=out,
                selector_file=selector_path,
                multi_anno_versions=versions_str,
            )
            + "\n"
        )

    return EXIT_SUCCESS


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


