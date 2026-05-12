"""Stdout summary formatter + inspect mode formatter + report writers.

Distinct from formats.py because the consumers differ: stdout summary
goes to stderr after `select` completes (visible to humans, invisible
to pipes); inspect summary is the entire output of `aadr-subset
inspect` and goes to stdout; report writers emit per-population TSV /
JSON aggregates for `aadr-subset report`.

Per LLD §3.6 + HLD §Stdout summary + HLD §Inspect mode + HLD §Reports.
"""

from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import __version__
from .formats import atomic_write
from .types import SubsetResult

if TYPE_CHECKING:
    from aadr_resolve import AnnoFrame

# Report JSON schema version (HLD §Reports JSON). Bumped only on breaking
# changes to the JSON shape (additive keys are non-breaking).
REPORT_SCHEMA_VERSION = 1

# Threshold for switching from compact-inline to columnar stdout
# summary form (HLD §Stdout summary). <10 pops → inline; ≥10 → columnar.
COLUMNAR_POPULATION_THRESHOLD = 10


def format_stdout_summary(
    result: SubsetResult,
    *,
    parse_time: float,
    eval_time: float,
    write_time: float,
    out_path_str: str | None,
    selector_file: str,
    anno: AnnoFrame,
) -> str:
    """Multi-line summary per HLD §Stdout summary.

    Two layouts based on population count:
    - <10 populations: compact-inline form
        Per-population: A=187, B=34, ...
    - ≥10 populations: columnar form (one Group_ID per line, right-padded counts)

    Timing breakdown: parse / eval / write with total. Per LLD §3.6 pin,
    parse_time covers everything before engine.select_samples (selector
    load + AnnoFrame parse + signature compute); eval_time is the engine
    call; write_time is the output write.

    out_path_str=None → "stdout" in the "Wrote …" line.
    """
    pop_count = len(result.per_population_counts)
    n_excluded = sum(ec.count for ec in result.excluded_counts)

    lines: list[str] = []
    sig_tail = (
        f" ({_short_signature(result.selector_signature)})" if result.selector_signature else ""
    )
    lines.append(f"Selector: {selector_file}{sig_tail}")
    lines.append(f".anno:    {anno.path} ({anno.version}, class {anno.schema_class.value})")
    lines.append("")
    lines.append(f"Matched {result.n_matched} samples across {pop_count} populations.")
    if n_excluded > 0:
        n_conditions = len(result.excluded_counts)
        cond_word = "condition" if n_conditions == 1 else "conditions"
        lines.append(f"Excluded {n_excluded} samples via {n_conditions} exclusion {cond_word}.")
    lines.append("")

    if pop_count > 0:
        if pop_count < COLUMNAR_POPULATION_THRESHOLD:
            pop_str = ", ".join(
                f"{name}={cnt}" for name, cnt in result.per_population_counts.items()
            )
            lines.append(f"Per-population: {pop_str}")
        else:
            lines.append("Per-population breakdown:")
            # Right-align counts; pad group_id names to a uniform left col.
            name_width = max(len(n) for n in result.per_population_counts)
            count_width = max(len(str(c)) for c in result.per_population_counts.values())
            for name, cnt in result.per_population_counts.items():
                lines.append(f"  {name:<{name_width}}  {cnt:>{count_width}}")
        lines.append("")

    out_label = out_path_str if out_path_str else "stdout"
    n_lines = result.n_matched
    lines.append(f"Wrote {out_label} ({n_lines} lines)")
    total = parse_time + eval_time + write_time
    lines.append(
        f"Done in {total:.2f}s "
        f"(parse {parse_time:.2f}s, eval {eval_time:.2f}s, write {write_time:.2f}s)."
    )
    return "\n".join(lines)


def format_inspect_summary(result: SubsetResult, anno: AnnoFrame) -> str:
    """Full inspect-mode output per HLD §Inspect mode.

    Always columnar; inspect's consumer is debugging the selector, not
    parsing the output. Includes: per-population breakdown, branch
    contributions, exclusions, date range + coverage range of matched
    samples, selector signature (when populated).
    """
    selector_file = result.selector_file or "<stdin>"

    lines: list[str] = []
    lines.append(f"Selector: {selector_file}")
    lines.append(
        f".anno:    {anno.path} ({anno.version}, class {anno.schema_class.value}, "
        f"{anno.n_rows:,} samples)"
    )
    lines.append("")
    pop_count = len(result.per_population_counts)
    pop_word = "population" if pop_count == 1 else "populations"
    lines.append(f"Matched: {result.n_matched} samples across {pop_count} {pop_word}")
    lines.append("")

    if result.per_population_counts:
        lines.append("Per-population breakdown:")
        name_width = max(len(n) for n in result.per_population_counts)
        count_width = max(len(str(c)) for c in result.per_population_counts.values())
        for name, cnt in result.per_population_counts.items():
            lines.append(f"  {name:<{name_width}}  {cnt:>{count_width}}")
        lines.append("")

    if result.per_branch_counts:
        lines.append("Branch contributions:")
        key_width = max(len(k) for k in result.per_branch_counts)
        val_width = max(len(str(v)) for v in result.per_branch_counts.values())
        for key, cnt in result.per_branch_counts.items():
            lines.append(f"  {key:<{key_width}}  {cnt:>{val_width}}")
        lines.append("")

    if result.excluded_counts:
        lines.append("Excluded:")
        for ec in result.excluded_counts:
            sample_word = "sample" if ec.count == 1 else "samples"
            lines.append(f"  {ec.key}: {ec.value}    {ec.count} {sample_word} dropped")
        lines.append("")

    # Date + coverage range over MATCHED rows. Compute from anno columns.
    if result.n_matched > 0:
        import numpy as np

        gid_to_row = {gid: idx for idx, gid in enumerate(anno.genetic_id.tolist())}
        matched_rows = np.array(
            [gid_to_row[g] for g in result.genetic_ids if g in gid_to_row],
            dtype=np.int64,
        )
        date_series = anno.date_calbp.iloc[matched_rows]
        cov_series = anno.coverage.iloc[matched_rows]
        if date_series.notna().any():
            d_min = int(date_series.min())
            d_max = int(date_series.max())
            d_med = int(date_series.median())
            lines.append(f"Date range of matched: {d_min} - {d_max} calBP (median {d_med})")
        if cov_series.notna().any():
            c_min = float(cov_series.min())
            c_max = float(cov_series.max())
            c_med = float(cov_series.median())
            lines.append(f"Coverage range:        {c_min:.2f} - {c_max:.2f}x (median {c_med:.2f})")
        lines.append("")

    if result.selector_signature:
        lines.append(f"Selector signature: {result.selector_signature}")

    return "\n".join(lines).rstrip()


def _short_signature(sig: str) -> str:
    """Compact form for the selector signature header line.

    Full form: sha256:abcdef0123...0123456789abcdef
    Short form: sha256:abcdef0...456789abcdef
    """
    if not sig.startswith("sha256:") or len(sig) < 20:
        return sig
    body = sig[7:]
    return f"sha256:{body[:7]}...{body[-7:]}"


# --- Report writers (per-population aggregates) ---


def write_report_tsv(
    result: SubsetResult,
    anno: AnnoFrame,
    *,
    include_empty_groups: bool,
    out_path: Path | None,
) -> None:
    """Per-population TSV per HLD §Reports. Columns:
    group_id, n_matched, n_in_anno, pct_matched, date_min_calbp,
    date_max_calbp, coverage_median.

    Row inclusion:
    - Default: rows where n_matched > 0 (the ones the selector pulled).
    - include_empty_groups=True: also include rows where n_matched == 0
      for groups present in the .anno. Useful for population-survey
      workflows where "did this group survive the filter?" is the
      question.

    pct_matched rendered to 1 decimal place; round-half-to-even via
    Python's default formatter. No "%" suffix.
    Empty cells for NaN coverage_median / <NA> date_min/max.

    Row order: per_population_counts insertion order for matched groups
    (first-appearance order from engine groupby), then any empty groups
    appended in .anno encounter order. Atomic write per formats.atomic_write.
    """
    rows = _build_report_rows(result, anno, include_empty_groups=include_empty_groups)

    buf = io.StringIO()
    writer = csv.writer(
        buf,
        delimiter="\t",
        quoting=csv.QUOTE_NONE,
        escapechar="\\",
        lineterminator="\n",
    )
    writer.writerow(
        [
            "group_id",
            "n_matched",
            "n_in_anno",
            "pct_matched",
            "date_min_calbp",
            "date_max_calbp",
            "coverage_median",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row["group_id"],
                row["n_matched"],
                row["n_in_anno"],
                _fmt_pct(row["pct_matched"]),
                _fmt_int_or_blank(row["date_min_calbp"]),
                _fmt_int_or_blank(row["date_max_calbp"]),
                _fmt_float_or_blank(row["coverage_median"]),
            ]
        )

    content = buf.getvalue()
    if out_path is None:
        sys.stdout.write(content)
        sys.stdout.flush()
        return
    atomic_write(out_path, content)


def write_report_json(
    result: SubsetResult,
    anno: AnnoFrame,
    *,
    include_empty_groups: bool,
    out_path: Path | None,
) -> None:
    """JSON shape per HLD §Reports JSON. Top-level keys:
    selector_signature, anno_version, schema_version, aadr_subset_version,
    populations[].

    Each population entry: group_id, n_matched, n_in_anno, pct_matched,
    date_min_calbp, date_max_calbp, coverage_median, coverage_min,
    coverage_max. pct_matched serialized as a float (e.g., 0.882), NOT
    rendered as the TSV's "88.2" — JSON consumers do their own rendering.

    Atomic write per formats.atomic_write.
    """
    rows = _build_report_rows(result, anno, include_empty_groups=include_empty_groups)

    populations: list[dict[str, Any]] = []
    for row in rows:
        populations.append(
            {
                "group_id": row["group_id"],
                "n_matched": row["n_matched"],
                "n_in_anno": row["n_in_anno"],
                "pct_matched": row["pct_matched"],
                "date_min_calbp": row["date_min_calbp"],
                "date_max_calbp": row["date_max_calbp"],
                "coverage_median": row["coverage_median"],
                "coverage_min": row["coverage_min"],
                "coverage_max": row["coverage_max"],
            }
        )

    out: dict[str, Any] = {
        "selector_signature": result.selector_signature,
        "anno_version": result.anno_version,
        "schema_version": REPORT_SCHEMA_VERSION,
        "aadr_subset_version": __version__,
        "populations": populations,
    }

    body = json.dumps(out, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    if out_path is None:
        sys.stdout.write(body)
        sys.stdout.flush()
        return
    atomic_write(out_path, body)


def _build_report_rows(
    result: SubsetResult,
    anno: AnnoFrame,
    *,
    include_empty_groups: bool,
) -> list[dict[str, Any]]:
    """Per-group aggregate rows shared by TSV + JSON writers.

    Order: matched groups in `per_population_counts` insertion order, then
    (when include_empty_groups=True) every other group present in .anno in
    first-appearance order.
    """
    import numpy as np

    group_col = anno.group_id.tolist()
    gid_col = anno.genetic_id.tolist()
    date_col = anno.date_calbp
    cov_col = anno.coverage

    # Map matched genetic_ids → row positions for the matched-row date/cov
    # aggregates.
    matched_set = set(result.genetic_ids)
    gid_to_row = {g: i for i, g in enumerate(gid_col)}

    # Group → list of row indices in .anno; preserves first-appearance order.
    group_to_rows: dict[str, list[int]] = {}
    for i, g in enumerate(group_col):
        group_to_rows.setdefault(g, []).append(i)

    ordered: list[str] = []
    seen: set[str] = set()
    for g in result.per_population_counts:
        if g not in seen:
            ordered.append(g)
            seen.add(g)
    if include_empty_groups:
        for g in group_to_rows:
            if g not in seen:
                ordered.append(g)
                seen.add(g)

    rows: list[dict[str, Any]] = []
    for group in ordered:
        anno_rows = group_to_rows.get(group, [])
        n_in_anno = len(anno_rows)
        n_matched = result.per_population_counts.get(group, 0)
        pct = (n_matched / n_in_anno) if n_in_anno > 0 else 0.0

        # Date / coverage stats over MATCHED rows of this group only.
        matched_indices = [
            gid_to_row[g]
            for g in result.genetic_ids
            if g in gid_to_row and group_col[gid_to_row[g]] == group
        ]
        date_min: int | None = None
        date_max: int | None = None
        cov_median: float | None = None
        cov_min: float | None = None
        cov_max: float | None = None
        if matched_indices:
            idx = np.array(matched_indices, dtype=np.int64)
            d_sub = date_col.iloc[idx]
            if d_sub.notna().any():
                date_min = int(d_sub.min())
                date_max = int(d_sub.max())
            c_sub = cov_col.iloc[idx]
            if c_sub.notna().any():
                cov_median = float(c_sub.median())
                cov_min = float(c_sub.min())
                cov_max = float(c_sub.max())

        # Mark matched_set as used to avoid unused-var lint (it's
        # intentionally available for future invariants).
        _ = matched_set

        rows.append(
            {
                "group_id": group,
                "n_matched": n_matched,
                "n_in_anno": n_in_anno,
                "pct_matched": pct,
                "date_min_calbp": date_min,
                "date_max_calbp": date_max,
                "coverage_median": cov_median,
                "coverage_min": cov_min,
                "coverage_max": cov_max,
            }
        )
    return rows


def _fmt_pct(pct: float) -> str:
    """Render fractional 0.0-1.0 as a percentage with 1 decimal place."""
    return f"{pct * 100:.1f}"


def _fmt_int_or_blank(value: int | None) -> str:
    return "" if value is None else str(value)


def _fmt_float_or_blank(value: float | None) -> str:
    return "" if value is None else f"{value:g}"


__all__ = [
    "REPORT_SCHEMA_VERSION",
    "format_inspect_summary",
    "format_stdout_summary",
    "write_report_json",
    "write_report_tsv",
]
