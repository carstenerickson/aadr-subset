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
from .types import DiffResult, SubsetResult

if TYPE_CHECKING:
    from aadr_resolve import AnnoFrame

# Report JSON schema version (HLD §Reports JSON). Bumped only on breaking
# changes to the JSON shape (additive keys are non-breaking).
REPORT_SCHEMA_VERSION = 1

# Diff JSON schema version (HLD v0.2). Same versioning discipline as
# REPORT_SCHEMA_VERSION — bump on breaking shape changes, additive keys
# are non-breaking.
DIFF_SCHEMA_VERSION = 1

# Threshold for switching from compact-inline to columnar stdout
# summary form (HLD §Stdout summary). <10 pops → inline; ≥10 → columnar.
COLUMNAR_POPULATION_THRESHOLD = 10


def format_run_summary(
    result: SubsetResult,
    *,
    parse_time: float,
    eval_time: float,
    write_time: float,
    out_path_str: str | None,
    selector_file: str,
    anno: AnnoFrame,
    multi_anno_versions: str | None = None,
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
    if multi_anno_versions:
        lines.append(f".anno:    {anno.path} (versions: {multi_anno_versions})")
    else:
        lines.append(f".anno:    {anno.path} ({anno.version}, class {anno.schema_class.value})")
    lines.append("")
    pop_word = "population" if pop_count == 1 else "populations"
    lines.append(f"Matched {result.n_matched} samples across {pop_count} {pop_word}.")
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

    # v0.3: Downsampled section. Per-population entries are listed
    # explicitly; per-individual entries are aggregated to one row
    # (per-IID drops can be in the thousands and would dominate the
    # inspect summary). JSON output (`select --format json`) preserves
    # per-IID detail for callers that need it.
    if result.sampling_drops:
        lines.append("Downsampled:")
        pop_drops = [sd for sd in result.sampling_drops if sd.dimension == "population"]
        iid_drops = [sd for sd in result.sampling_drops if sd.dimension == "individual"]
        if pop_drops:
            key_w = max(len("group_id"), max(len(sd.key) for sd in pop_drops))
            cnt_w = max(len(str(sd.count)) for sd in pop_drops)
            for sd in pop_drops:
                sample_word = "sample" if sd.count == 1 else "samples"
                lines.append(f"  {sd.key:<{key_w}}  {sd.count:>{cnt_w}} {sample_word} dropped")
        if iid_drops:
            total = sum(sd.count for sd in iid_drops)
            sample_word = "sample" if total == 1 else "samples"
            lines.append(
                f"  per-individual aggregate: {total} {sample_word} "
                f"dropped across {len(iid_drops)} individual(s)"
            )
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


def _compact_sha256(sig: str) -> str:
    """Return the 7+7 abbreviated form of a sha256: signature body."""
    body = sig[len("sha256:"):]
    return f"sha256:{body[:7]}...{body[-7:]}"


def _short_signature(sig: str) -> str:
    """Compact `sha256:XXXXXXX...XXXXXXX` form for the selector header line."""
    if not sig.startswith("sha256:") or len(sig) < 20:
        return sig
    return _compact_sha256(sig)


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


# --- Diff (v0.2: aadr-subset diff selA.yaml selB.yaml ANNO.anno) ---


def build_diff_result(
    result_a: SubsetResult,
    result_b: SubsetResult,
) -> DiffResult:
    """Set-difference two SubsetResults that share the same target .anno.

    Compute a_only / b_only / both as ordered lists (each list preserves
    the .anno row order of its parent SubsetResult), plus a
    per_population_delta dict mapping every Group_ID that either side
    matched to its (n_a, n_b) tuple.

    The two SubsetResults must have been computed against the same
    AnnoFrame — caller's responsibility. Mismatched anno metadata is not
    validated here; run_diff handles that gate.
    """
    set_a = set(result_a.genetic_ids)
    set_b = set(result_b.genetic_ids)

    a_only = [g for g in result_a.genetic_ids if g not in set_b]
    b_only = [g for g in result_b.genetic_ids if g not in set_a]
    # `both` preserves A's order (arbitrary tie-break; either side is fine).
    both = [g for g in result_a.genetic_ids if g in set_b]

    # per_population_delta: union of group_ids across both results.
    all_groups: list[str] = []
    seen: set[str] = set()
    for g in list(result_a.per_population_counts) + list(result_b.per_population_counts):
        if g not in seen:
            all_groups.append(g)
            seen.add(g)
    per_pop_delta: dict[str, tuple[int, int]] = {
        g: (
            result_a.per_population_counts.get(g, 0),
            result_b.per_population_counts.get(g, 0),
        )
        for g in all_groups
    }

    return DiffResult(
        a_only=a_only,
        b_only=b_only,
        both=both,
        per_population_delta=per_pop_delta,
        a_signature=result_a.selector_signature,
        b_signature=result_b.selector_signature,
        selector_a_file=result_a.selector_file,
        selector_b_file=result_b.selector_file,
        anno_file=result_a.anno_file,
        anno_version=result_a.anno_version,
        schema_class=result_a.schema_class,
    )


def format_diff_summary(diff: DiffResult, *, sample_preview: int = 10) -> str:
    """Multi-line human-readable diff summary.

    Set sizes + per-population delta table + a small per-side preview of
    the diverging sample IDs. Output goes to stdout (diff has no
    machine-readable companion when format=human, so stdout is the
    consumer surface).
    """
    lines: list[str] = []
    lines.append(f"Selector A: {diff.selector_a_file}{_signature_tail(diff.a_signature)}")
    lines.append(f"Selector B: {diff.selector_b_file}{_signature_tail(diff.b_signature)}")
    lines.append(f".anno:      {diff.anno_file} ({diff.anno_version}, class {diff.schema_class})")
    lines.append("")

    n_a_only = len(diff.a_only)
    n_b_only = len(diff.b_only)
    n_both = len(diff.both)
    lines.append(f"A only: {n_a_only} sample{'s' if n_a_only != 1 else ''}")
    lines.append(f"B only: {n_b_only} sample{'s' if n_b_only != 1 else ''}")
    lines.append(f"Both:   {n_both} sample{'s' if n_both != 1 else ''}")
    lines.append("")

    if diff.per_population_delta:
        lines.append("Per-population delta:")
        # Right-pad columns for stable alignment.
        groups = list(diff.per_population_delta)
        name_w = max(len("group_id"), max(len(g) for g in groups))
        a_w = max(len("A"), max(len(str(v[0])) for v in diff.per_population_delta.values()))
        b_w = max(len("B"), max(len(str(v[1])) for v in diff.per_population_delta.values()))
        delta_w = max(
            len("delta"),
            max(len(_fmt_delta(b - a)) for a, b in diff.per_population_delta.values()),
        )
        header = f"  {'group_id':<{name_w}}  {'A':>{a_w}}  {'B':>{b_w}}  {'delta':>{delta_w}}"
        lines.append(header)
        for g, (a, b) in diff.per_population_delta.items():
            delta = _fmt_delta(b - a)
            lines.append(f"  {g:<{name_w}}  {a:>{a_w}}  {b:>{b_w}}  {delta:>{delta_w}}")
        lines.append("")

    if n_a_only > 0:
        preview = diff.a_only[:sample_preview]
        suffix = "" if n_a_only <= sample_preview else f" (+{n_a_only - sample_preview} more)"
        lines.append(f"A only sample preview: {preview}{suffix}")
    if n_b_only > 0:
        preview = diff.b_only[:sample_preview]
        suffix = "" if n_b_only <= sample_preview else f" (+{n_b_only - sample_preview} more)"
        lines.append(f"B only sample preview: {preview}{suffix}")

    return "\n".join(lines).rstrip()


def write_diff_json(diff: DiffResult, *, out_path: Path | None) -> None:
    """Serialize a DiffResult to JSON.

    Top-level keys: anno_file, anno_version, schema_class, selector_a,
    selector_b, n_a_only, n_b_only, n_both, a_only[], b_only[], both[],
    per_population_delta[] (list of {group_id, n_a, n_b, delta}),
    schema_version, aadr_subset_version. List ordering preserves the
    .anno row order of each parent SubsetResult.

    Atomic write to PATH or write to stdout when None.
    """
    pop_delta_rows = [
        {
            "group_id": g,
            "n_a": n_a,
            "n_b": n_b,
            "delta": n_b - n_a,
        }
        for g, (n_a, n_b) in diff.per_population_delta.items()
    ]
    payload: dict[str, Any] = {
        "anno_file": diff.anno_file,
        "anno_version": diff.anno_version,
        "schema_class": diff.schema_class,
        "selector_a": {
            "file": diff.selector_a_file,
            "signature": diff.a_signature,
        },
        "selector_b": {
            "file": diff.selector_b_file,
            "signature": diff.b_signature,
        },
        "n_a_only": len(diff.a_only),
        "n_b_only": len(diff.b_only),
        "n_both": len(diff.both),
        "a_only": list(diff.a_only),
        "b_only": list(diff.b_only),
        "both": list(diff.both),
        "per_population_delta": pop_delta_rows,
        "schema_version": DIFF_SCHEMA_VERSION,
        "aadr_subset_version": __version__,
    }

    body = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    if out_path is None:
        sys.stdout.write(body)
        sys.stdout.flush()
        return
    atomic_write(out_path, body)


def _fmt_delta(d: int) -> str:
    """Render a per-population delta with a leading sign for non-zero."""
    if d > 0:
        return f"+{d}"
    return str(d)


def _signature_tail(sig: str) -> str:
    """Parenthesized short signature for diff header lines. Empty when sig is empty."""
    if not sig:
        return ""
    if sig.startswith("sha256:") and len(sig) >= 20:
        return f" ({_compact_sha256(sig)})"
    return f" ({sig})"


__all__ = [
    "DIFF_SCHEMA_VERSION",
    "REPORT_SCHEMA_VERSION",
    "build_diff_result",
    "format_diff_summary",
    "format_inspect_summary",
    "format_run_summary",
    "write_diff_json",
    "write_report_json",
    "write_report_tsv",
]
