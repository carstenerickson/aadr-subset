"""diff subcommand orchestrator.

Set-difference of two selectors against the same target .anno. Useful
for PR review and selector iteration: "what does the new selector
match that the old one doesn't, and vice versa?"

v0.2 lands the single-version path. Cross-version diff (each selector
carrying its own `source_version:` + `resolve_to_version:`) is rejected
with a UsageError until a future release decides on the
two-source-anno surface.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import aadr_resolve

from ..engine import select_samples
from ..errors import (
    EXIT_SUCCESS,
    IOFailure,
    UsageError,
    ValidationError,
)
from .._cmd_helpers import parse_schema_override as _parse_schema_override
from ..reporting import build_diff_result, format_diff_summary, write_diff_json
from ..selector import compute_signature, load_selector
from ..types import DiffFormat, Selector, SubsetResult


def run_diff(
    *,
    selector_a_path: str,
    selector_b_path: str,
    anno_path: str,
    out: str | None,
    fmt: str,
    schema_override: str | None,
    allow_empty_source: bool,
    quiet: bool,
) -> int:
    """Orchestrate `aadr-subset diff`. Returns EXIT_SUCCESS unconditionally
    (diff is diagnostic; empty diff is itself meaningful, not a failure).

    Sequence:
    1. Load + validate both selectors.
    2. Reject cross-version selectors (v0.2 limitation).
    3. Load target AnnoFrame.
    4. Run engine on each selector.
    5. Compute signatures + run-env metadata.
    6. Build DiffResult via reporting.build_diff_result.
    7. Emit format=human to stdout, or format=json to stdout/--out PATH.
    """
    _ = quiet  # diff's stdout IS the output; quiet has no current effect.

    # 1. Load both selectors.
    _meta_a, selector_a = load_selector(selector_a_path, allow_empty_source=allow_empty_source)
    _meta_b, selector_b = load_selector(selector_b_path, allow_empty_source=allow_empty_source)

    # 2. Cross-version diff is out of scope for v0.2.
    for label, sel in (("A", selector_a), ("B", selector_b)):
        if sel.resolve_to_version is not None:
            raise UsageError(
                errors=[
                    ValidationError(
                        file=(selector_a_path if label == "A" else selector_b_path),
                        line=1,
                        col=1,
                        pointer="/resolve_to_version",
                        message=(
                            f"selector {label} sets resolve_to_version; cross-"
                            f"version diff is not supported in v0.2. Materialize "
                            f"each selector separately with `aadr-subset select` "
                            f"and diff the resulting ID lists, or drop "
                            f"resolve_to_version from both selectors."
                        ),
                    )
                ],
            )

    # 3. Load target AnnoFrame.
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

    # 4-5. Evaluate each selector and populate run-env metadata so the
    # DiffResult carries selector_file / anno_version / signature.
    def _run(selector: Selector, sel_path: str) -> SubsetResult:
        r = select_samples(anno, selector, include_matched_criteria=False)
        sig = compute_signature(selector, cli_coverage_column=None)
        return replace(
            r,
            anno_file=str(anno_path),
            anno_version=anno.version,
            schema_class=anno.schema_class.value,
            selector_file=sel_path,
            selector_signature=sig,
        )

    result_a = _run(selector_a, selector_a_path)
    result_b = _run(selector_b, selector_b_path)

    # 6. Build the DiffResult.
    diff = build_diff_result(result_a, result_b)

    # 7. Emit.
    fmt_enum = DiffFormat(fmt)
    out_path = Path(out) if out else None
    if fmt_enum == DiffFormat.JSON:
        write_diff_json(diff, out_path=out_path)
    else:
        body = format_diff_summary(diff)
        if out_path is None:
            sys.stdout.write(body + "\n")
            sys.stdout.flush()
        else:
            # Atomic write keeps the contract uniform across formats even
            # though human output is rarely written to a file.
            from ..formats import atomic_write

            atomic_write(out_path, body + "\n")

    return EXIT_SUCCESS


