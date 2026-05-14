"""Output writers.

Day 2 shipped `write_ids` + atomic_write; Day 4 adds `write_tsv`,
`write_json`, and the `write_select_output` dispatcher.

JSON output key order is fixed (LLD §3.5 pin) for diff-friendliness:
1. genetic_ids
2. n_matched
3. per_population_counts
4. per_branch_counts
5. excluded_counts
6. sampling_drops  ← v0.3+; empty list when sampling inactive (additive,
                     non-breaking; no JSON_SCHEMA_VERSION bump)
7. matched_criteria  ← OMITTED ENTIRELY when empty
8. warnings
9. selector_signature
10. selector_file
11. anno_file
12. anno_version
13. schema_class
14. coverage_column
15. aadr_subset_version
16. aadr_resolve_version
17. schema_version
"""

from __future__ import annotations

import csv
import fcntl
import io
import json
import os
import sys
import tempfile
from dataclasses import asdict

import pandas as pd
from pathlib import Path
from typing import TYPE_CHECKING

from . import __version__
from .errors import IOFailure
from .types import OutputFormat, SubsetResult

if TYPE_CHECKING:
    from aadr_resolve import AnnoFrame

# JSON output schema_version; HLD §Output JSON. Increment only on
# breaking changes to the JSON shape (additive new keys are non-breaking).
JSON_SCHEMA_VERSION = 1


def write_ids(genetic_ids: list[str], out_path: Path | None) -> None:
    """One GeneticID per line, UTF-8, LF terminator (including the last
    line). No header.

    When out_path is None, writes to stdout (no atomicity contract).
    When set, uses atomic_write: tempfile + fsync + os.rename, gated
    by an advisory fcntl.flock on `{out_path}.lock`.
    """
    body = "\n".join(genetic_ids)
    if body:
        body += "\n"
    if out_path is None:
        sys.stdout.write(body)
        sys.stdout.flush()
        return
    atomic_write(out_path, body)


def atomic_write(path: Path, content: bytes | str) -> None:
    """Atomic write via tempfile + os.rename, gated by advisory
    fcntl.flock on `{path}.lock` (LOCK_EX | LOCK_NB).

    Sequence (per LLD §3.5):
    1. Acquire lock on `{path}.lock` (non-blocking; raises IOFailure
       if held by another process).
    2. Write content to a tempfile in path.parent.
    3. fsync the tempfile.
    4. os.rename the tempfile to path (POSIX-atomic).
    5. Release lock.

    Bytes-vs-string mode is auto-detected from content type — no `mode`
    kwarg.
    """
    is_bytes = isinstance(content, bytes)
    mode = "wb" if is_bytes else "w"

    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)

    lock_path = path.with_suffix(path.suffix + ".lock")
    try:
        # Use a context-manager pattern around the lock file. Persisting
        # the .lock file after run is intentional (avoids the create-vs-
        # acquire race; LLD §3.5 pin).
        with open(lock_path, "w") as lock_fp:
            try:
                fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as e:
                raise IOFailure(f"output lock held by another process: {lock_path}") from e
            try:
                tmp_kwargs: dict[str, object] = {
                    "dir": parent,
                    "prefix": path.name + ".tmp.",
                    "delete": False,
                    "mode": mode,
                }
                if not is_bytes:
                    tmp_kwargs["encoding"] = "utf-8"
                tmp = tempfile.NamedTemporaryFile(**tmp_kwargs)  # type: ignore[call-overload]
                tmp_path = Path(tmp.name)
                try:
                    tmp.write(content)
                    tmp.flush()
                    os.fsync(tmp.fileno())
                    tmp.close()
                    os.rename(tmp_path, path)
                except Exception:
                    # Clean up the tempfile on any failure.
                    tmp.close()
                    if tmp_path.exists():
                        tmp_path.unlink()
                    raise
            finally:
                fcntl.flock(lock_fp.fileno(), fcntl.LOCK_UN)
    except OSError as e:
        # Any other I/O error (open lock file, parent dir not writable,
        # rename failure) → IOFailure.
        raise IOFailure(f"failed to write {path}: {e}") from e


def write_select_output(
    result: SubsetResult,
    anno: AnnoFrame,
    *,
    fmt: OutputFormat,
    out_path: Path | None,
    include_matched_criteria: bool,
) -> None:
    """Dispatch to write_ids / write_tsv / write_json based on fmt.

    out_path=None writes to stdout (no atomicity contract).
    out_path set: atomic_write per LLD §3.5.
    """
    if fmt == OutputFormat.IDS:
        write_ids(result.genetic_ids, out_path)
    elif fmt == OutputFormat.TSV:
        write_tsv(result, anno, out_path)
    elif fmt == OutputFormat.JSON:
        write_json(
            result,
            anno,
            include_matched_criteria=include_matched_criteria,
            out_path=out_path,
        )
    else:
        # OutputFormat is a closed enum; this branch is unreachable.
        from .errors import InvariantViolation
        raise InvariantViolation(f"unknown output format: {fmt!r}")


def write_tsv(result: SubsetResult, anno: AnnoFrame, out_path: Path | None) -> None:
    """Tab-separated with header row.

    Columns (HLD §TSV format):
    genetic_id, individual_id, group_id, date_calbp, coverage, matched_criteria

    Cell formatting:
    - date_calbp: integer when present; empty cell for <NA>.
    - coverage: plain float (e.g., 1.2) when present; empty for NaN.
      No 'x' suffix — downstream parsers consume the column as numeric.
    - matched_criteria: semicolon-joined; empty cell when
      result.matched_criteria doesn't carry an entry for this GID (the
      common case when include_matched_criteria=False).

    Rows iterate result.genetic_ids in order (preserves .anno row order
    per HLD §Selector overlap and deduplication).
    """
    # Build a row-index lookup once: GeneticID → row position in af.
    # Using pandas Index.get_loc per GID is fine for the typical
    # selection size (~100-5000); not worth optimizing to a merge.
    gid_to_row: dict[str, int] = {gid: idx for idx, gid in enumerate(anno.genetic_id.tolist())}

    iid_col = anno.individual_id.tolist()
    grp_col = anno.group_id.tolist()
    date_col = anno.date_calbp
    cov_col = anno.coverage

    buf = io.StringIO()
    # excel-tab dialect provides tab delimiter; we override quoting to NONE
    # since AADR Group_IDs / IIDs don't contain tab characters in practice.
    # No escapechar needed: GeneticIDs and IIDs are token-like (no whitespace).
    writer = csv.writer(
        buf,
        delimiter="\t",
        quoting=csv.QUOTE_NONE,
        escapechar="\\",
        lineterminator="\n",
    )
    writer.writerow(
        [
            "genetic_id",
            "individual_id",
            "group_id",
            "date_calbp",
            "coverage",
            "matched_criteria",
        ]
    )
    for gid in result.genetic_ids:
        i = gid_to_row[gid]
        date_val = date_col.iloc[i]
        date_cell = "" if _is_na(date_val) else str(int(date_val))
        cov_val = cov_col.iloc[i]
        cov_cell = "" if _is_na(cov_val) else f"{float(cov_val):g}"
        criteria = result.matched_criteria.get(gid, [])
        criteria_cell = ";".join(criteria)
        writer.writerow([gid, iid_col[i], grp_col[i], date_cell, cov_cell, criteria_cell])

    content = buf.getvalue()
    if out_path is None:
        sys.stdout.write(content)
        sys.stdout.flush()
        return
    atomic_write(out_path, content)


def write_json(
    result: SubsetResult,
    anno: AnnoFrame,
    *,
    include_matched_criteria: bool,
    out_path: Path | None,
) -> None:
    """Full SubsetResult-shape JSON per HLD §Output JSON.

    Key order pinned at 16 entries (insertion-order via
    json.dumps(sort_keys=False)); see module docstring.

    matched_criteria omission rule: when the dict is empty (the
    --include-matched-criteria=False default case), the key is OMITTED
    from JSON output entirely. When non-empty, it's serialized normally.
    """
    try:
        import aadr_resolve

        aadr_resolve_version = getattr(aadr_resolve, "__version__", "unknown")
    except ImportError:
        aadr_resolve_version = "not-installed"

    out: dict[str, object] = {}
    out["genetic_ids"] = list(result.genetic_ids)
    out["n_matched"] = result.n_matched
    out["per_population_counts"] = dict(result.per_population_counts)
    out["per_branch_counts"] = dict(result.per_branch_counts)
    out["excluded_counts"] = [asdict(ec) for ec in result.excluded_counts]
    # v0.3: additive field — no JSON_SCHEMA_VERSION bump because old
    # consumers ignoring the new key continue to work. Empty list when
    # sampling wasn't active OR was active but matched no candidates to drop.
    out["sampling_drops"] = [asdict(sd) for sd in result.sampling_drops]

    if include_matched_criteria and result.matched_criteria:
        out["matched_criteria"] = {gid: list(crit) for gid, crit in result.matched_criteria.items()}
    # else: key omitted entirely per HLD H6 / LLD §3.5 pin.

    out["warnings"] = asdict(result.warnings)
    out["selector_signature"] = result.selector_signature
    out["selector_file"] = result.selector_file
    out["anno_file"] = result.anno_file
    out["anno_version"] = result.anno_version
    out["schema_class"] = result.schema_class
    out["coverage_column"] = result.coverage_column_used
    out["aadr_subset_version"] = __version__
    out["aadr_resolve_version"] = aadr_resolve_version
    out["schema_version"] = JSON_SCHEMA_VERSION

    body = json.dumps(out, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    if out_path is None:
        sys.stdout.write(body)
        sys.stdout.flush()
        return
    atomic_write(out_path, body)


def write_multi_anno_select_output(
    result: SubsetResult,
    pairs: list[tuple[AnnoFrame, SubsetResult]],
    *,
    fmt: OutputFormat,
    out_path: Path | None,
    include_matched_criteria: bool,
) -> None:
    """Dispatch multi-anno output to the appropriate writer.

    IDS: identical to single-anno (write result.genetic_ids).
    TSV: same fixed columns as write_tsv plus source_version before
         matched_criteria; rows grouped by anno (oldest first).
    JSON: same shape as write_json plus anno_versions / anno_files /
         per_anno_n_matched additive keys.

    out_path=None writes to stdout; set uses atomic_write.
    """
    if fmt == OutputFormat.IDS:
        write_ids(result.genetic_ids, out_path)
    elif fmt == OutputFormat.TSV:
        _write_multi_anno_tsv(result, pairs, out_path)
    elif fmt == OutputFormat.JSON:
        _write_multi_anno_json(result, pairs, include_matched_criteria=include_matched_criteria, out_path=out_path)
    else:
        from .errors import InvariantViolation
        raise InvariantViolation(f"unknown output format: {fmt!r}")


def _write_multi_anno_tsv(
    result: SubsetResult,
    pairs: list[tuple[AnnoFrame, SubsetResult]],
    out_path: Path | None,
) -> None:
    """TSV with source_version column.

    Columns: genetic_id, individual_id, group_id, date_calbp, coverage,
             source_version, matched_criteria.

    Row ordering: per anno in pairs order (oldest first); within each anno
    rows follow the order in result.per_anno_genetic_ids[af.version].
    """
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
            "genetic_id",
            "individual_id",
            "group_id",
            "date_calbp",
            "coverage",
            "source_version",
            "matched_criteria",
        ]
    )

    for af, _per_result in pairs:
        surviving_gids = result.per_anno_genetic_ids.get(af.version, [])
        if not surviving_gids:
            continue

        # Build a row-index lookup for this anno's surviving gids.
        gid_to_row: dict[str, int] = {
            gid: idx for idx, gid in enumerate(af.genetic_id.tolist())
        }
        iid_col = af.individual_id.tolist()
        grp_col = af.group_id.tolist()
        date_col = af.date_calbp
        cov_col = af.coverage

        for gid in surviving_gids:
            i = gid_to_row[gid]
            date_val = date_col.iloc[i]
            date_cell = "" if _is_na(date_val) else str(int(date_val))
            cov_val = cov_col.iloc[i]
            cov_cell = "" if _is_na(cov_val) else f"{float(cov_val):g}"
            criteria = result.matched_criteria.get(gid, [])
            criteria_cell = ";".join(criteria)
            writer.writerow(
                [gid, iid_col[i], grp_col[i], date_cell, cov_cell, af.version, criteria_cell]
            )

    content = buf.getvalue()
    if out_path is None:
        sys.stdout.write(content)
        sys.stdout.flush()
        return
    atomic_write(out_path, content)


def _write_multi_anno_json(
    result: SubsetResult,
    pairs: list[tuple[AnnoFrame, SubsetResult]],
    *,
    include_matched_criteria: bool,
    out_path: Path | None,
) -> None:
    """JSON output for multi-anno results.

    Additive keys beyond the single-anno shape (no JSON_SCHEMA_VERSION bump):
      anno_versions, anno_files, per_anno_n_matched.
    anno_version / anno_file retain the newest-version values for backwards
    compat with consumers that read only those fields.
    """
    try:
        import aadr_resolve

        aadr_resolve_version = getattr(aadr_resolve, "__version__", "unknown")
    except ImportError:
        aadr_resolve_version = "not-installed"

    out: dict[str, object] = {}
    out["genetic_ids"] = list(result.genetic_ids)
    out["n_matched"] = result.n_matched
    out["per_population_counts"] = dict(result.per_population_counts)
    out["per_branch_counts"] = dict(result.per_branch_counts)
    out["excluded_counts"] = [asdict(ec) for ec in result.excluded_counts]
    out["sampling_drops"] = [asdict(sd) for sd in result.sampling_drops]

    if include_matched_criteria and result.matched_criteria:
        out["matched_criteria"] = {gid: list(crit) for gid, crit in result.matched_criteria.items()}

    out["warnings"] = asdict(result.warnings)
    out["selector_signature"] = result.selector_signature
    out["selector_file"] = result.selector_file
    out["anno_file"] = result.anno_file        # newest version (backwards compat)
    out["anno_version"] = result.anno_version  # newest version (backwards compat)
    out["anno_versions"] = list(result.anno_versions)
    out["anno_files"] = list(result.anno_files)
    out["per_anno_n_matched"] = {
        v: len(gids) for v, gids in result.per_anno_genetic_ids.items()
    }
    out["schema_class"] = result.schema_class
    out["coverage_column"] = result.coverage_column_used
    out["aadr_subset_version"] = __version__
    out["aadr_resolve_version"] = aadr_resolve_version
    out["schema_version"] = JSON_SCHEMA_VERSION

    body = json.dumps(out, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    if out_path is None:
        sys.stdout.write(body)
        sys.stdout.flush()
        return
    atomic_write(out_path, body)


def _is_na(value: object) -> bool:
    """True for pandas NA / NaN / None. Handles both Int64-nullable and
    Float64 dtypes plus plain None."""
    if value is None:
        return True
    try:
        return bool(pd.isna(value))  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return False


__all__ = [
    "atomic_write",
    "write_ids",
    "write_json",
    "write_multi_anno_select_output",
    "write_select_output",
    "write_tsv",
]
