"""Stdout summary formatter + inspect mode formatter.

Distinct from formats.py because the consumers differ: stdout summary
goes to stderr after `select` completes (visible to humans, invisible
to pipes); inspect summary is the entire output of `aadr-subset
inspect` and goes to stdout.

Per LLD §3.6 + HLD §Stdout summary + HLD §Inspect mode.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .types import SubsetResult

if TYPE_CHECKING:
    from aadr_resolve import AnnoFrame

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


__all__ = ["format_inspect_summary", "format_stdout_summary"]
