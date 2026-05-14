"""report subcommand orchestrator.

Per-population aggregate output. Same selector + AnnoFrame loading as
`select`, then `reporting.write_report_tsv` / `write_report_json` instead
of formats.py writers.
"""

from __future__ import annotations

import sys
import time
from dataclasses import replace
from pathlib import Path

import aadr_resolve

from ..engine import select_samples
from .._cmd_helpers import (
    normalize_coverage_flags as _normalize_coverage_flags,
    parse_schema_override as _parse_schema_override,
)
from ..errors import (
    EXIT_SUCCESS,
    IOFailure,
    SoftValidationFailure,
)
from ..reporting import write_report_json, write_report_tsv
from ..selector import compute_signature, load_selector
from ..types import ReportFormat


def run_report(
    *,
    selector_path: str,
    anno_path: str,
    out: str | None,
    fmt: str,
    schema_override: str | None,
    allow_empty: bool,
    allow_empty_source: bool,
    include_empty_groups: bool,
    coverage_column: str | None = None,
    coverage_derive: str | None = None,
    max_per_population: int | None = None,
    max_per_individual: int | None = None,
    quiet: bool,
) -> int:
    """Orchestrate `aadr-subset report`. Returns exit code.

    Sequence:
    1. Load + validate selector.
    2. Load target AnnoFrame.
    3. Compute selector_signature.
    4. Engine evaluation (include_matched_criteria=False — report doesn't
       need per-row criteria, only group aggregates).
    5. Exit-1 gate: n_matched == 0 and not allow_empty → SoftValidationFailure.
    6. Populate run-env metadata.
    7. Write report (TSV or JSON) via reporting.write_report_*.
    8. One-line stdout summary unless quiet (HLD §Reports: report's stdout
       summary is intentionally a one-liner — no parse/eval/write breakdown).
    9. Return EXIT_SUCCESS.
    """
    t_parse_start = time.monotonic()

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

    # 3. Normalize coverage flags + compute selector signature.
    cli_coverage_column = _normalize_coverage_flags(coverage_column, coverage_derive)
    sig = compute_signature(
        selector,
        cli_coverage_column=cli_coverage_column,
        cli_max_per_population=max_per_population,
        cli_max_per_individual=max_per_individual,
    )

    parse_time = time.monotonic() - t_parse_start

    # 4. Engine evaluation.
    t_eval_start = time.monotonic()
    result = select_samples(
        anno,
        selector,
        coverage_column=cli_coverage_column,
        max_per_population=max_per_population,
        max_per_individual=max_per_individual,
        include_matched_criteria=False,
    )
    eval_time = time.monotonic() - t_eval_start

    # 5. Exit-1 gate.
    if result.n_matched == 0 and not allow_empty:
        raise SoftValidationFailure(
            "selector matched 0 samples — report not written. "
            "Pass --allow-empty for a header-only report."
        )

    # 6. Populate run-env metadata.
    result = replace(
        result,
        anno_file=str(anno_path),
        anno_version=anno.version,
        schema_class=anno.schema_class.value,
        selector_file=selector_path,
        selector_signature=sig,
    )

    # 7. Write report.
    fmt_enum = ReportFormat(fmt)
    t_write_start = time.monotonic()
    out_path = Path(out) if out else None
    if fmt_enum == ReportFormat.TSV:
        write_report_tsv(
            result,
            anno,
            include_empty_groups=include_empty_groups,
            out_path=out_path,
        )
    else:
        write_report_json(
            result,
            anno,
            include_empty_groups=include_empty_groups,
            out_path=out_path,
        )
    write_time = time.monotonic() - t_write_start

    # 8. One-line stdout summary (HLD §Reports). Intentionally not the
    # multi-segment parse/eval/write breakdown that select uses.
    if not quiet:
        n_pops = len(result.per_population_counts)
        pop_word = "population" if n_pops == 1 else "populations"
        out_label = str(out_path) if out_path else "stdout"
        total = parse_time + eval_time + write_time
        sys.stderr.write(
            f"Wrote {out_label} ({n_pops} {pop_word}, {result.n_matched} samples) "
            f"in {total:.2f}s.\n"
        )

    return EXIT_SUCCESS


