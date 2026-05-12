"""inspect subcommand orchestrator.

Diagnostic dry-run: shows what a selector matches against a target .anno
without writing any file. Always exits 0 — inspect is informational; a
non-zero exit on zero-match would defeat the purpose.

Per LLD §3.10 / §4.3. Day 4 ships the single-version path; cross-version
diagnostics land Day 6 alongside select's cross-version flow.
"""

from __future__ import annotations

import sys
from dataclasses import replace

import aadr_resolve

from ..engine import select_samples
from ..errors import EXIT_SUCCESS, IOFailure, UsageError, ValidationError
from ..reporting import format_inspect_summary
from ..selector import load_selector


def run_inspect(
    *,
    selector_path: str,
    anno_path: str,
    schema_override: str | None,
    allow_empty_source: bool,
    strict_resolve: bool,
    quiet: bool,
) -> int:
    """Orchestrate `aadr-subset inspect`. Always returns EXIT_SUCCESS.

    Day-4 sequence (§4.3 reduced for single-version):
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

    # 3. Engine evaluation. include_matched_criteria=True so the inspect
    # summary can show per-branch attribution.
    result = select_samples(
        anno,
        selector,
        include_matched_criteria=True,
    )

    # 4. Populate run-env metadata.
    result = replace(
        result,
        anno_file=str(anno_path),
        anno_version=anno.version,
        schema_class=anno.schema_class.value,
        selector_file=selector_path,
    )

    # 5. Print inspect summary to STDOUT.
    summary = format_inspect_summary(result, anno)

    # strict_resolve diagnostic: HLD pins it as informational-only on
    # inspect. Day 4 has no cross-version yet, so missing_after_resolve
    # is always empty; reserved for Day 6.
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


def _parse_schema_override(value: str | None):  # type: ignore[no-untyped-def]
    """Map a CLI --schema-override CLASS letter to aadr_resolve.SchemaClass."""
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
