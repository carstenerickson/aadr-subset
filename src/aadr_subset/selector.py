"""Selector loader + JSON-schema validator + semantic-constraint checker.

Day 1 surface per LLD §3.3:
- load_selector: top-level entry that takes a path/stream and returns
  (SelectorMetadata, Selector), raising UsageError / IOFailure /
  SoftValidationFailure on the appropriate failure modes.
- validate_schema: jsonschema.Draft202012Validator.iter_errors() mapped to
  ValidationError with file/line/col via ruamel.yaml AST walk.
- check_semantic_constraints: validations the JSON schema can't express.
- load_individual_ids_source: newline-delimited ID file per HLD format spec.
- format_validation_errors: render list[ValidationError] for stderr.

compute_signature (RFC 8785 JCS) lands on Day 7 alongside the select
subcommand; not needed by validate.
"""

from __future__ import annotations

import importlib.resources
import io
import json
import sys
from pathlib import Path
from typing import Any, TextIO

import jsonschema
import yaml
from ruamel.yaml import YAML

from .errors import (
    IOFailure,
    SoftValidationFailure,
    UsageError,
    ValidationError,
)
from .types import (
    AnyBranch,
    DateRange,
    ExcludeBlock,
    Selector,
    SelectorMetadata,
)

# Resolved once at import time (LLD pin: importlib.resources, not __file__).
SCHEMA_PATH: Path = Path(
    str(importlib.resources.files("aadr_subset") / "schemas" / "selector.schema.json")
)

# Deprecated alias map. master_ids → individual_ids, master_ids_source →
# individual_ids_source. Applied recursively into any: branches.
_DEPRECATED_ALIASES = {
    "master_ids": "individual_ids",
    "master_ids_source": "individual_ids_source",
}


# --- Top-level entry ---


def load_selector(
    source: Path | str | TextIO,
    *,
    base_dir: Path | None = None,
    allow_empty_source: bool = False,
    schema_path: Path | None = None,
    collect_all_errors: bool = False,
) -> tuple[SelectorMetadata, Selector]:
    """Parse a selector YAML (single- or two-document form). See LLD §3.3.

    `collect_all_errors=True` is the validate-mode flag: accumulate every
    ValidationError before raising a single UsageError. Default (False) is
    fail-fast for select/inspect/report which abort anyway on first error.
    """
    schema_path = schema_path or SCHEMA_PATH

    source_label, stream, source_dir = _open_source(source)
    if base_dir is None:
        base_dir = source_dir

    try:
        raw_text = stream.read()
    finally:
        if hasattr(stream, "close") and source is not sys.stdin:
            stream.close()

    docs = _load_yaml_documents(raw_text, source_label=source_label)

    metadata_dict: dict[str, Any]
    selector_dict: dict[str, Any]
    if len(docs) == 0:
        # Empty file or only-comments. The empty-selector contract (HLD
        # §Selector grammar semantics) requires an explicit `{}` mapping;
        # a literally-empty file is almost always a user error.
        raise UsageError(
            errors=[
                ValidationError(
                    file=source_label,
                    line=1,
                    col=1,
                    pointer="/",
                    message=(
                        "selector file is empty or contains no YAML documents. "
                        "Use '{}' for an explicit empty selector that matches "
                        "every sample."
                    ),
                )
            ],
        )
    elif len(docs) == 1:
        metadata_dict = {}
        selector_dict = docs[0]
    elif len(docs) == 2:
        metadata_dict = docs[0] if docs[0] is not None else {}
        selector_dict = docs[1] if docs[1] is not None else {}
    else:
        raise UsageError(
            errors=[
                ValidationError(
                    file=source_label,
                    line=1,
                    col=1,
                    pointer="/",
                    message=(f"expected 1 or 2 YAML documents in {source_label}; got {len(docs)}"),
                )
            ],
        )

    if selector_dict is None:
        selector_dict = {}
    if not isinstance(selector_dict, dict):
        raise UsageError(
            f"{source_label}: selector must be a YAML mapping at the top level "
            f"(got {type(selector_dict).__name__})",
        )

    # Resolve deprecated aliases (rewrites the dict in place; collects warnings).
    selector_dict, alias_warnings = _resolve_deprecated_aliases(
        selector_dict,
        source_label=source_label,
        raw_text=raw_text,
    )

    # Surface deprecation warnings to stderr immediately (per HLD).
    for warning in alias_warnings:
        sys.stderr.write(warning.format_line() + "\n")

    # Schema validation.
    schema_errors = _validate_schema(
        selector_dict,
        schema_path=schema_path,
        source_label=source_label,
        raw_text=raw_text,
    )

    # Semantic-constraint check (skips constraints whose preconditions failed
    # the schema check, per H3 fix in LLD v2).
    semantic_errors = _check_semantic_constraints(
        selector_dict,
        source_label=source_label,
        raw_text=raw_text,
        schema_errors=schema_errors,
    )

    all_errors = schema_errors + semantic_errors

    if all_errors and not collect_all_errors:
        # Fail fast: raise on first batch of errors.
        raise UsageError(errors=all_errors)
    if all_errors and collect_all_errors:
        raise UsageError(errors=all_errors)

    # Parse metadata + selector dataclasses.
    metadata = _parse_metadata(metadata_dict, source_label=source_label)
    selector = _build_selector(selector_dict, metadata=metadata)

    # Load individual_ids_source file content if present.
    if selector.individual_ids_source is not None:
        from_source = _load_individual_ids_source(
            selector.individual_ids_source,
            base_dir=base_dir,
            allow_empty=allow_empty_source,
            source_label=source_label,
        )
        # Replace via dataclass replace (Selector is frozen).
        from dataclasses import replace

        selector = replace(selector, individual_ids_from_source=from_source)

    return metadata, selector


# --- Stream/path handling ---


def _open_source(source: Path | str | TextIO) -> tuple[str, TextIO, Path]:
    """Return (label_for_messages, readable_stream, base_dir_for_relative_paths).

    `source` is one of:
    - Path/str path: opens the file
    - '-': reads from stdin
    - TextIO: used directly (caller provides path context separately)
    """
    if hasattr(source, "read"):
        # TextIO-like
        return ("<stream>", source, Path.cwd())  # type: ignore[return-value]

    src_str = str(source)
    if src_str == "-":
        return ("<stdin>", sys.stdin, Path.cwd())

    src_path = Path(src_str)
    if not src_path.exists():
        raise IOFailure(f"selector file not found: {src_path}")
    if not src_path.is_file():
        raise IOFailure(f"selector path is not a regular file: {src_path}")
    try:
        stream = src_path.open("r", encoding="utf-8")
    except OSError as e:
        raise IOFailure(f"cannot read selector file {src_path}: {e}") from e
    return (str(src_path), stream, src_path.parent)


def _load_yaml_documents(raw_text: str, *, source_label: str) -> list[Any]:
    """Stream documents via yaml.safe_load_all; consume up to 3 to detect overrun.

    1 doc:  [selector_dict]
    2 docs: [metadata_dict, selector_dict]
    3+ docs: UsageError
    """
    try:
        docs = list(yaml.safe_load_all(raw_text))
    except yaml.YAMLError as e:
        # PyYAML's mark gives 0-indexed line/col; convert to 1-indexed.
        mark = getattr(e, "problem_mark", None)
        line = (mark.line + 1) if mark else 1
        col = (mark.column + 1) if mark else 1
        raise UsageError(
            errors=[
                ValidationError(
                    file=source_label,
                    line=line,
                    col=col,
                    pointer="/",
                    message=f"YAML parse error: {e}",
                )
            ],
        ) from e

    if len(docs) > 2:
        raise UsageError(
            errors=[
                ValidationError(
                    file=source_label,
                    line=1,
                    col=1,
                    pointer="/",
                    message=f"expected 1 or 2 YAML documents; got {len(docs)}",
                )
            ],
        )
    return docs


# --- Deprecated alias resolution ---


def _resolve_deprecated_aliases(
    d: dict[str, Any],
    *,
    source_label: str,
    raw_text: str,
) -> tuple[dict[str, Any], list[ValidationError]]:
    """Rewrite master_ids / master_ids_source → individual_ids /
    individual_ids_source at top level AND inside any: branches. Collect
    WARNING-severity ValidationError per occurrence.

    Raises UsageError immediately if BOTH canonical and deprecated alias
    are present for the same concept (defense-in-depth; schema also
    enforces via cross-key constraint).
    """
    warnings: list[ValidationError] = []
    rewritten = dict(d)  # shallow copy; mutates in place below

    # Top-level conflict check.
    for old_key, new_key in _DEPRECATED_ALIASES.items():
        if old_key in rewritten and new_key in rewritten:
            raise UsageError(
                errors=[
                    ValidationError(
                        file=source_label,
                        line=1,
                        col=1,
                        pointer="/",
                        message=(
                            f"cannot specify both '{new_key}' and '{old_key}' "
                            f"(deprecated alias for the same concept)"
                        ),
                        constraint="canonical_and_deprecated_alias_both_set",
                    )
                ],
            )

    # Top-level rewrite.
    for old_key, new_key in _DEPRECATED_ALIASES.items():
        if old_key in rewritten:
            line, col = _locate_node(raw_text, [old_key])
            warnings.append(
                ValidationError(
                    file=source_label,
                    line=line,
                    col=col,
                    pointer=f"/{old_key}",
                    message=(f"'{old_key}' is deprecated; use '{new_key}' (removed in v0.2)"),
                    severity="WARNING",
                )
            )
            rewritten[new_key] = rewritten.pop(old_key)

    # Branch rewrites (any: list of branch dicts).
    branches = rewritten.get("any")
    if isinstance(branches, list):
        new_branches: list[Any] = []
        for i, branch in enumerate(branches):
            if not isinstance(branch, dict):
                new_branches.append(branch)
                continue
            branch_copy = dict(branch)
            for old_key, new_key in _DEPRECATED_ALIASES.items():
                if old_key in branch_copy and new_key in branch_copy:
                    raise UsageError(
                        errors=[
                            ValidationError(
                                file=source_label,
                                line=1,
                                col=1,
                                pointer=f"/any/{i}",
                                message=(
                                    f"branch {i}: cannot specify both '{new_key}' and '{old_key}'"
                                ),
                                constraint="canonical_and_deprecated_alias_both_set",
                            )
                        ],
                    )
                if old_key in branch_copy:
                    line, col = _locate_node(raw_text, ["any", i, old_key])
                    warnings.append(
                        ValidationError(
                            file=source_label,
                            line=line,
                            col=col,
                            pointer=f"/any/{i}/{old_key}",
                            message=(
                                f"'{old_key}' is deprecated; use '{new_key}' (removed in v0.2)"
                            ),
                            severity="WARNING",
                        )
                    )
                    branch_copy[new_key] = branch_copy.pop(old_key)
            new_branches.append(branch_copy)
        rewritten["any"] = new_branches

    return rewritten, warnings


# --- Schema validation (uses ruamel.yaml AST for line/col) ---


def _validate_schema(
    d: dict[str, Any],
    *,
    schema_path: Path,
    source_label: str,
    raw_text: str,
) -> list[ValidationError]:
    """Run Draft202012Validator.iter_errors(d) and map each error to a
    ValidationError with file/line/col."""
    try:
        with schema_path.open("r", encoding="utf-8") as f:
            schema = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise IOFailure(f"cannot load JSON schema at {schema_path}: {e}") from e

    validator = jsonschema.Draft202012Validator(schema)
    errors: list[ValidationError] = []
    for err in validator.iter_errors(d):
        pointer = _absolute_path_to_pointer(list(err.absolute_path))
        line, col = _locate_node(raw_text, list(err.absolute_path))
        errors.append(
            ValidationError(
                file=source_label,
                line=line,
                col=col,
                pointer=pointer,
                message=err.message,
            )
        )
    return errors


# --- Semantic-constraint check ---


def _check_semantic_constraints(
    d: dict[str, Any],
    *,
    source_label: str,
    raw_text: str,
    schema_errors: list[ValidationError],
) -> list[ValidationError]:
    """Validations the JSON schema can't express.

    Skips constraints whose preconditions already failed the schema check
    (avoids double-reporting in validate mode).
    """
    errors: list[ValidationError] = []
    failed_pointers = {e.pointer for e in schema_errors}

    # 1. date.min_calbp <= date.max_calbp
    date = d.get("date")
    if isinstance(date, dict):
        min_calbp = date.get("min_calbp")
        max_calbp = date.get("max_calbp")
        if (
            isinstance(min_calbp, int)
            and isinstance(max_calbp, int)
            and min_calbp > max_calbp
            and "/date/min_calbp" not in failed_pointers
            and "/date/max_calbp" not in failed_pointers
        ):
            line, col = _locate_node(raw_text, ["date", "min_calbp"])
            errors.append(
                ValidationError(
                    file=source_label,
                    line=line,
                    col=col,
                    pointer="/date/min_calbp",
                    message=(
                        f"{min_calbp} must be less than or equal to "
                        f"/date/max_calbp (got {max_calbp})"
                    ),
                    constraint="date_range_inverted",
                )
            )

    # 2. source_version != resolve_to_version when both present
    sv = d.get("source_version")
    rtv = d.get("resolve_to_version")
    if (
        isinstance(sv, str)
        and isinstance(rtv, str)
        and sv == rtv
        and "/source_version" not in failed_pointers
        and "/resolve_to_version" not in failed_pointers
    ):
        line, col = _locate_node(raw_text, ["resolve_to_version"])
        errors.append(
            ValidationError(
                file=source_label,
                line=line,
                col=col,
                pointer="/resolve_to_version",
                message=(
                    f"resolve_to_version '{rtv}' equals source_version; "
                    f"cross-version resolution requires distinct versions"
                ),
                constraint="cross_version_self_reference",
            )
        )

    return errors


# --- individual_ids_source file loader ---


def _load_individual_ids_source(
    path: Path,
    *,
    base_dir: Path,
    allow_empty: bool,
    source_label: str,
) -> list[str]:
    """Read newline-delimited ID list per HLD §individual_ids_source file format."""
    resolved = path if path.is_absolute() else (base_dir / path).resolve()
    if not resolved.exists():
        raise IOFailure(f"individual_ids_source file not found: {resolved}")
    try:
        raw = resolved.read_bytes()
    except OSError as e:
        raise IOFailure(f"cannot read {resolved}: {e}") from e

    # BOM strip + UTF-8 decode.
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise IOFailure(f"{resolved}: not valid UTF-8 ({e})") from e

    ids: list[str] = []
    errors: list[ValidationError] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if any(ch.isspace() for ch in stripped):
            errors.append(
                ValidationError(
                    file=str(resolved),
                    line=lineno,
                    col=1,
                    pointer="/",
                    message=(f"ID contains internal whitespace: '{stripped}'"),
                    constraint="id_contains_internal_whitespace",
                )
            )
            continue
        ids.append(stripped)

    if errors:
        raise UsageError(errors=errors)

    if not ids and not allow_empty:
        raise SoftValidationFailure(
            f"individual_ids_source '{resolved}' has zero IDs after blank/comment "
            f"filtering — likely user error. Use --allow-empty-source to allow."
        )
    return ids


# --- Metadata parsing ---


_METADATA_KEYS = {"tested_against", "last_verified", "maintainer", "notes"}


def _parse_metadata(d: dict[str, Any], *, source_label: str) -> SelectorMetadata:
    """Construct SelectorMetadata. Unknown keys → stderr WARNING (non-fatal)."""
    if not d:
        return SelectorMetadata()

    for key in d.keys():
        if key not in _METADATA_KEYS:
            sys.stderr.write(
                f"WARNING: {source_label}: unknown metadata key '{key}' "
                f"(recognized: {sorted(_METADATA_KEYS)})\n"
            )

    tested_against = d.get("tested_against", []) or []
    if not isinstance(tested_against, list):
        raise UsageError(
            errors=[
                ValidationError(
                    file=source_label,
                    line=1,
                    col=1,
                    pointer="/tested_against",
                    message="tested_against must be a list of AADR version strings",
                )
            ],
        )

    return SelectorMetadata(
        tested_against=[str(v) for v in tested_against],
        last_verified=str(d["last_verified"]) if d.get("last_verified") else None,
        maintainer=str(d["maintainer"]) if d.get("maintainer") else None,
        notes=str(d.get("notes", "") or ""),
    )


# --- Selector dataclass construction (post schema-validation) ---


def _build_selector(d: dict[str, Any], *, metadata: SelectorMetadata) -> Selector:
    """Build a Selector from the schema-validated dict. Resolves the
    `exclude.populations → exclude.group_ids` alias and converts
    `any:` list to AnyBranch dataclasses."""
    date = None
    if "date" in d and isinstance(d["date"], dict):
        date = DateRange(
            min_calbp=d["date"].get("min_calbp"),
            max_calbp=d["date"].get("max_calbp"),
        )

    exclude = None
    if "exclude" in d and isinstance(d["exclude"], dict):
        ex = d["exclude"]
        # populations alias → group_ids.
        group_ids = list(ex.get("group_ids", []))
        if "populations" in ex:
            group_ids.extend(ex["populations"])
        # Dedup while preserving order. dict-from-keys is the idiomatic
        # order-preserving dedup since Python 3.7.
        group_ids = list(dict.fromkeys(group_ids))
        exclude = ExcludeBlock(
            group_ids=group_ids,
            individual_ids=list(ex.get("individual_ids", [])),
        )

    any_branches = []
    if "any" in d and isinstance(d["any"], list):
        for branch_dict in d["any"]:
            if not isinstance(branch_dict, dict):
                continue
            branch_date = None
            if "date" in branch_dict and isinstance(branch_dict["date"], dict):
                branch_date = DateRange(
                    min_calbp=branch_dict["date"].get("min_calbp"),
                    max_calbp=branch_dict["date"].get("max_calbp"),
                )
            ids_src = branch_dict.get("individual_ids_source")
            any_branches.append(
                AnyBranch(
                    populations=list(branch_dict.get("populations", [])),
                    individual_ids=list(branch_dict.get("individual_ids", [])),
                    individual_ids_source=Path(ids_src) if ids_src else None,
                    modern_only=branch_dict.get("modern_only"),
                    min_coverage=branch_dict.get("min_coverage"),
                    coverage_column=branch_dict.get("coverage_column"),
                    date=branch_date,
                )
            )

    ids_src = d.get("individual_ids_source")
    return Selector(
        populations=list(d.get("populations", [])),
        individual_ids=list(d.get("individual_ids", [])),
        individual_ids_source=Path(ids_src) if ids_src else None,
        modern_only=d.get("modern_only"),
        min_coverage=d.get("min_coverage"),
        coverage_column=d.get("coverage_column"),
        date=date,
        source_version=d.get("source_version"),
        resolve_to_version=d.get("resolve_to_version"),
        any_branches=any_branches,
        exclude=exclude,
        metadata=metadata,
    )


# --- ValidationError formatting ---


def format_validation_errors(errors: list[ValidationError]) -> str:
    """Render list[ValidationError] one-line-per-entry for stderr.

    Sorts by (line, col) ascending so users see a predictable top-to-bottom
    error flow regardless of internal collection order.
    """
    sorted_errors = sorted(errors, key=lambda e: (e.line, e.col))
    return "\n".join(e.format_line() for e in sorted_errors)


# --- Line/col lookup via ruamel.yaml ---


def _absolute_path_to_pointer(path_parts: list[Any]) -> str:
    """Convert jsonschema absolute_path (deque of str/int) → RFC 6901 pointer."""
    if not path_parts:
        return "/"
    # Escape '/' and '~' per RFC 6901.
    escaped = ["~1".join("~0".join(str(p).split("~")).split("/")) for p in path_parts]
    return "/" + "/".join(escaped)


def _locate_node(raw_text: str, path_parts: list[Any]) -> tuple[int, int]:
    """Walk a ruamel.yaml round-trip parse to find the (line, col) of the
    node at `path_parts`. Returns 1-indexed (line, col); defaults to (1, 1)
    if the node can't be resolved (defensive)."""
    try:
        yaml_loader = YAML(typ="rt")
        # Round-trip mode on multi-doc input: load_all returns generator of docs.
        docs = list(yaml_loader.load_all(io.StringIO(raw_text)))
        # Pick the last doc (the selector); single-doc form has 1, two-doc has 2.
        node = docs[-1] if docs else None
        if node is None:
            return (1, 1)
        for part in path_parts:
            if hasattr(node, "lc") and isinstance(part, str) and part in node:
                # Map node before stepping in.
                line, col = node.lc.key(part)
                node = node[part]
                # If this was the last part, return its key position.
                if path_parts[-1] == part and part is path_parts[-1]:
                    return (line + 1, col + 1)
            elif isinstance(part, int) and isinstance(node, list) and 0 <= part < len(node):
                # ruamel sequence: lc.item(i) gives row of i-th element.
                if hasattr(node, "lc"):
                    line, col = node.lc.item(part)
                    node = node[part]
                    if path_parts[-1] == part:
                        return (line + 1, col + 1)
                else:
                    node = node[part]
            else:
                # Can't resolve further; fall back to current node's start.
                break
        # Return current node's start if we walked deep but didn't return.
        if hasattr(node, "lc"):
            return (node.lc.line + 1, node.lc.col + 1)
        return (1, 1)
    except Exception:
        # Defensive: if ruamel can't parse for some reason (it should agree
        # with PyYAML on success), fall back to (1, 1).
        return (1, 1)
