"""inspect subcommand orchestrator.

Diagnostic dry-run: shows what a selector matches against a target .anno
without writing any file. Always exits 0 — inspect is informational; a
non-zero exit on zero-match would defeat the purpose.
"""

from __future__ import annotations

import sys
from dataclasses import replace

import aadr_resolve

from .._cmd_helpers import (
    normalize_coverage_flags as _normalize_coverage_flags,
    parse_schema_override as _parse_schema_override,
)
from ..engine import select_samples
from ..errors import EXIT_SUCCESS, IOFailure
from ..reporting import format_inspect_summary
from ..selector import compute_signature, load_selector


def run_inspect(
    *,
    selector_path: str,
    anno_path: str,
    schema_override: str | None,
    allow_empty_source: bool,
    strict_resolve: bool,
    coverage_column: str | None = None,
    coverage_derive: str | None = None,
    max_per_population: int | None = None,
    max_per_individual: int | None = None,
    quiet: bool,
) -> int:
    """Orchestrate `aadr-subset inspect`. Always returns EXIT_SUCCESS.

    Sequence:
    1. Load + validate selector.
    2. Load target AnnoFrame.
    3. Engine evaluation with include_matched_criteria=True (inspect's
       output uses matched_criteria via the branch breakdown).
    4. Populate run-env metadata.
    5. Print format_inspect_summary to stdout (NOT stderr — inspect has
       no machine-readable output to protect).
    6. Return EXIT_SUCCESS regardless of n_matched. SoftValidationFailure
       from zero matches becomes a stdout "0 samples matched" message.
       strict_resolve diagnostics surface in the summary block but don't
       change exit code (HLD §Inspect mode pin).
    """
    # 1. Load + validate selector.
    _metadata, selector = load_selector(
        selector_path,
        allow_empty_source=allow_empty_source,
    )

    # 2. Load AnnoFrame.
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

    # 3. Normalize coverage flags + engine evaluation.
    cli_coverage_column = _normalize_coverage_flags(coverage_column, coverage_derive)
    result = select_samples(
        anno,
        selector,
        coverage_column=cli_coverage_column,
        max_per_population=max_per_population,
        max_per_individual=max_per_individual,
        include_matched_criteria=True,
    )

    # 4. Compute signature + populate run-env metadata.
    sig = compute_signature(
        selector,
        cli_coverage_column=cli_coverage_column,
        cli_max_per_population=max_per_population,
        cli_max_per_individual=max_per_individual,
    )
    result = replace(
        result,
        anno_file=str(anno_path),
        anno_version=anno.version,
        schema_class=anno.schema_class.value,
        selector_file=selector_path,
        selector_signature=sig,
    )

    # 5. Print inspect summary to STDOUT.
    summary = format_inspect_summary(result, anno)

    # strict_resolve diagnostic: HLD pins it as informational-only on
    # inspect; missing_after_resolve is only populated on cross-version.
    if strict_resolve and result.warnings.missing_after_resolve:
        missing = result.warnings.missing_after_resolve
        shown = missing[:10]
        summary += (
            f"\n\n[STRICT-RESOLVE would fail: {len(missing)} Individual_ID(s) "
            f"failed to resolve. First 10: {shown}]"
        )

    if not quiet:
        sys.stdout.write(summary + "\n")

    return EXIT_SUCCESS


