"""select subcommand orchestrator.

Day 2: single-version path only — selector with populations and/or
individual_ids matched against a target .anno. Cross-version
(--source-anno + resolve_to_version:) lands on Day 6. Output is
sample-ID list only (--format=ids); TSV + JSON land on Day 4.

Per LLD §3.9 / §4.1, simplified to the Day-2 surface.
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
from ..formats import write_ids
from ..selector import load_selector
from ..types import Selector, SubsetResult


def run_select(
    *,
    selector_path: str,
    anno_path: str,
    out: str | None,
    schema_override: str | None,
    allow_empty: bool,
    allow_empty_source: bool,
    include_matched_criteria: bool,
    quiet: bool,
) -> int:
    """Orchestrate `aadr-subset select`. Returns exit code per HLD §Exit codes.

    Day-2 sequence (§4.1 reduced):
    1. Load + validate selector (load_selector with collect_all_errors=False;
       fail-fast on first batch).
    2. Reject Day-3+ features early via UsageError (engine also enforces,
       but doing it pre-AnnoFrame-load saves the .anno parse cost on
       guaranteed-fail selectors). NB: actually engine does this; this
       comment documents the design.
    3. Load target AnnoFrame from anno_path (catches SchemaDetectionError
       → IOFailure).
    4. engine.select_samples (timed).
    5. Exit-1 gate: n_matched == 0 and not allow_empty → SoftValidationFailure.
    6. Write output via formats.write_ids (timed).
    7. Stdout summary unless quiet.
    8. Return EXIT_SUCCESS.
    """
    # 1. Load + validate selector.
    t_parse_start = time.monotonic()
    try:
        _metadata, selector = load_selector(
            selector_path,
            allow_empty_source=allow_empty_source,
        )
    except UsageError:
        # Re-raise; cli.py top-level handler will format errors to stderr.
        raise

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

    t_parse_end = time.monotonic()
    parse_time = t_parse_end - t_parse_start

    # 3. v62 class-D coverage warning (HLD §Coverage handling). Fires when
    # the selector touches min_coverage AND target .anno is class D
    # (v62.0; no native coverage column) AND no override flag is set.
    # The --coverage-column / --coverage-derive opt-in lands later; until
    # then this warning is informational only.
    _emit_v62_coverage_warning_if_needed(anno, selector)

    # 4. Engine evaluation (timed).
    t_eval_start = time.monotonic()
    result = select_samples(
        anno,
        selector,
        include_matched_criteria=include_matched_criteria,
    )
    eval_time = time.monotonic() - t_eval_start

    # 4. Exit-1 gates.
    if result.n_matched == 0 and not allow_empty:
        raise SoftValidationFailure(
            "selector matched 0 samples — output not written. "
            "Pass --allow-empty for a sentinel-file write."
        )

    # 5. Populate run-env metadata on the result.
    result = replace(
        result,
        anno_file=str(anno_path),
        anno_version=anno.version,
        schema_class=anno.schema_class.value,
        selector_file=selector_path,
        # selector_signature lands Day 7.
        # coverage_column_used lands Day 3 with the coverage filter.
    )

    # 6. Write output.
    t_write_start = time.monotonic()
    write_ids(result.genetic_ids, Path(out) if out else None)
    write_time = time.monotonic() - t_write_start

    # 7. Stdout summary (to stderr; ID list goes to stdout when out is None).
    if not quiet:
        sys.stderr.write(
            _format_stdout_summary(
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


def _format_stdout_summary(
    result: SubsetResult,
    *,
    anno: aadr_resolve.AnnoFrame,
    parse_time: float,
    eval_time: float,
    write_time: float,
    out_path_str: str | None,
    selector_file: str,
) -> str:
    """Day-2 minimal stdout summary. Full inline-vs-columnar formatting
    lands Day 4 (per HLD §Stdout summary)."""
    pop_count = len(result.per_population_counts)
    lines = [
        f"Selector: {selector_file}",
        f".anno:    {anno.path} ({anno.version}, class {anno.schema_class.value})",
        "",
        f"Matched {result.n_matched} samples across {pop_count} populations.",
    ]
    if pop_count > 0:
        # Compact-inline form regardless of population count for Day 2;
        # columnar-vs-inline distinction lands Day 4.
        pop_str = ", ".join(
            f"{name}={cnt}" for name, cnt in list(result.per_population_counts.items())[:10]
        )
        if pop_count > 10:
            pop_str += f", ... (+{pop_count - 10} more)"
        lines.append(f"Per-population: {pop_str}")
    lines.append("")
    out_label = out_path_str if out_path_str else "stdout"
    lines.append(f"Wrote {out_label} ({result.n_matched} lines)")
    total = parse_time + eval_time + write_time
    lines.append(
        f"Done in {total:.2f}s "
        f"(parse {parse_time:.2f}s, eval {eval_time:.2f}s, write {write_time:.2f}s)."
    )
    return "\n".join(lines)
