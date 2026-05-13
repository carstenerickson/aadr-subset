"""Selector loader + JSON-schema validator + semantic-constraint checker
+ RFC 8785 JCS signature.

Day 1 surface per LLD §3.3:
- load_selector: top-level entry that takes a path/stream and returns
  (SelectorMetadata, Selector), raising UsageError / IOFailure /
  SoftValidationFailure on the appropriate failure modes.
- validate_schema: jsonschema.Draft202012Validator.iter_errors() mapped to
  ValidationError with file/line/col via ruamel.yaml AST walk.
- check_semantic_constraints: validations the JSON schema can't express.
- load_individual_ids_source: newline-delimited ID file per HLD format spec.
- format_validation_errors: render list[ValidationError] for stderr.

Day 5 added:
- compute_signature: RFC 8785 JCS canonical-form SHA-256 over selector intent,
  for the `selector_signature` field on SubsetResult (HLD §Reproducibility,
  LLD §3.3).
"""

from __future__ import annotations

import hashlib
import importlib.resources
import io
import json
import sys
from pathlib import Path
from typing import Any, TextIO

import jsonschema
import rfc8785
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
    SamplingPolicy,
    SamplingSpec,
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

    # v0.2: master_ids / master_ids_source are removed (were deprecated
    # aliases in v0.1 with warn-and-rewrite). Detect any occurrence and
    # surface as a ValidationError; the rest of the validation pipeline
    # still runs so the user sees every issue in one pass.
    removed_alias_errors = _check_removed_aliases(
        selector_dict,
        source_label=source_label,
        raw_text=raw_text,
    )

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

    all_errors = removed_alias_errors + schema_errors + semantic_errors

    if all_errors:
        # Fail fast unless caller is collecting (validate subcommand wants
        # every error in one pass); behavior is the same — UsageError
        # either way — but documented for clarity.
        raise UsageError(errors=all_errors)

    # Parse metadata + selector dataclasses.
    metadata = _parse_metadata(metadata_dict, source_label=source_label)
    selector = _build_selector(selector_dict, metadata=metadata)

    # Load individual_ids_source files (top-level + each any: branch).
    # AnyBranch and Selector are both frozen, so we rebuild via
    # dataclasses.replace. v0.2 extends top-level-only loading from v0.1
    # to also recurse into branches per HLD §Selector grammar semantics.
    from dataclasses import replace

    if selector.individual_ids_source is not None:
        from_source = _load_individual_ids_source(
            selector.individual_ids_source,
            base_dir=base_dir,
            allow_empty=allow_empty_source,
            source_label=source_label,
        )
        selector = replace(selector, individual_ids_from_source=from_source)

    if selector.any_branches:
        new_branches: list[AnyBranch] = []
        for i, branch in enumerate(selector.any_branches):
            if branch.individual_ids_source is not None:
                branch_ids = _load_individual_ids_source(
                    branch.individual_ids_source,
                    base_dir=base_dir,
                    allow_empty=allow_empty_source,
                    source_label=f"{source_label} (any[{i}])",
                )
                new_branches.append(replace(branch, individual_ids_from_source=branch_ids))
            else:
                new_branches.append(branch)
        selector = replace(selector, any_branches=new_branches)

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


# --- Removed-key detection (v0.2: master_ids / master_ids_source are errors) ---


def _check_removed_aliases(
    d: dict[str, Any],
    *,
    source_label: str,
    raw_text: str,
) -> list[ValidationError]:
    """Detect occurrences of `master_ids` / `master_ids_source` and return
    a ValidationError per occurrence with a renamed-in message.

    v0.1 accepted these as deprecated aliases (warn + rewrite). v0.2
    removes them: any selector still using `master_ids:` /
    `master_ids_source:` errors out at exit 4. Errors are collected at
    every occurrence (top-level + each any: branch) so the user sees
    every site to fix in one pass.
    """
    errors: list[ValidationError] = []

    for old_key, new_key in _DEPRECATED_ALIASES.items():
        if old_key in d:
            line, col = _locate_node(raw_text, [old_key])
            errors.append(
                ValidationError(
                    file=source_label,
                    line=line,
                    col=col,
                    pointer=f"/{old_key}",
                    message=(
                        f"'{old_key}' was a deprecated alias in v0.1 and is removed "
                        f"in v0.2; use '{new_key}' instead."
                    ),
                    constraint="removed_deprecated_alias",
                )
            )

    branches = d.get("any")
    if isinstance(branches, list):
        for i, branch in enumerate(branches):
            if not isinstance(branch, dict):
                continue
            for old_key, new_key in _DEPRECATED_ALIASES.items():
                if old_key in branch:
                    line, col = _locate_node(raw_text, ["any", i, old_key])
                    errors.append(
                        ValidationError(
                            file=source_label,
                            line=line,
                            col=col,
                            pointer=f"/any/{i}/{old_key}",
                            message=(
                                f"'{old_key}' was a deprecated alias in v0.1 and is "
                                f"removed in v0.2; use '{new_key}' instead."
                            ),
                            constraint="removed_deprecated_alias",
                        )
                    )

    return errors


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

    # v0.3: sampling spec. Schema rejects empty `sampling: {}` upstream
    # via anyOf, so a present `sampling` key always has at least one cap
    # field. We still defend against an empty dict here (cheap; means
    # the schema check was skipped, e.g. via a test fixture).
    sampling = None
    sampling_dict = d.get("sampling")
    if isinstance(sampling_dict, dict) and sampling_dict:
        policy_str = sampling_dict.get("policy", SamplingPolicy.TOP_COVERAGE.value)
        sampling = SamplingSpec(
            max_per_population=sampling_dict.get("max_per_population"),
            max_per_individual=sampling_dict.get("max_per_individual"),
            policy=SamplingPolicy(policy_str),
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
        sampling=sampling,
        metadata=metadata,
    )


# --- Selector signature (RFC 8785 JCS over canonicalized intent) ---


def compute_signature(
    selector: Selector,
    *,
    cli_coverage_column: str | None,
    cli_max_per_population: int | None = None,
    cli_max_per_individual: int | None = None,
) -> str:
    """SHA-256 over the RFC 8785 (JCS) canonical form of selector intent.

    Per LLD §3.3 algorithm:
      1. Build a plain dict from selector: scalar fields + flattened
         exclude / date / any_branches.
      2. Drop `individual_ids_source` (path is not signature-relevant;
         the file's content is) and drop `individual_ids_from_source`
         (folded into step 3).
      3. Union YAML-inlined + source-file IDs (sorted, deduped); set
         the dict's `individual_ids` to that union when non-empty.
      4. If selector.coverage_column is None AND cli_coverage_column is
         not None: inject coverage_column=cli_coverage_column. Selector
         wins; this is the CLI-fallback inclusion per HLD §Coverage.
      5. Drop the metadata block (cohort-irrelevant prose).
      6. rfc8785.dumps for JCS canonicalization.
      7. Return "sha256:" + hexdigest.

    Pure function. Same selector + same coverage env → same hash regardless
    of YAML key ordering or list internal order (lists that are order-
    sensitive — e.g., `any_branches` — keep their order; lists that are
    set-like — `individual_ids`, `populations`, exclude lists — get sorted
    here to break user-input ordering noise).
    """
    payload: dict[str, Any] = {}

    # Top-level set-like ID lists: dedup + sort.
    if selector.populations:
        payload["populations"] = sorted(set(selector.populations))

    union_ids = sorted(set(selector.individual_ids) | set(selector.individual_ids_from_source))
    if union_ids:
        payload["individual_ids"] = union_ids

    if selector.modern_only is not None:
        payload["modern_only"] = selector.modern_only

    if selector.min_coverage is not None:
        payload["min_coverage"] = selector.min_coverage

    # coverage_column: selector wins; fall back to CLI value (step 4).
    effective_coverage_column = selector.coverage_column or cli_coverage_column
    if effective_coverage_column is not None:
        payload["coverage_column"] = effective_coverage_column

    if selector.date is not None:
        date_dict: dict[str, int] = {}
        if selector.date.min_calbp is not None:
            date_dict["min_calbp"] = selector.date.min_calbp
        if selector.date.max_calbp is not None:
            date_dict["max_calbp"] = selector.date.max_calbp
        if date_dict:
            payload["date"] = date_dict

    if selector.source_version is not None:
        payload["source_version"] = selector.source_version
    if selector.resolve_to_version is not None:
        payload["resolve_to_version"] = selector.resolve_to_version

    if selector.any_branches:
        # any_branches order matters (HLD: branches are indexed in
        # per_branch_counts as any[0], any[1], ...).
        payload["any"] = [_canonical_any_branch(b) for b in selector.any_branches]

    if selector.exclude is not None:
        ex_dict: dict[str, list[str]] = {}
        if selector.exclude.group_ids:
            ex_dict["group_ids"] = sorted(set(selector.exclude.group_ids))
        if selector.exclude.individual_ids:
            ex_dict["individual_ids"] = sorted(set(selector.exclude.individual_ids))
        if ex_dict:
            payload["exclude"] = ex_dict

    # v0.3: sampling sub-dict. Per-field selector-vs-CLI merge; defaults
    # elided (so `policy: top_coverage` explicit and omitted produce the
    # same signature). Same intent-not-expansion rule as Group_ID globs.
    sampling_payload: dict[str, Any] = {}
    eff_max_pop = (
        selector.sampling.max_per_population
        if (selector.sampling and selector.sampling.max_per_population is not None)
        else cli_max_per_population
    )
    if eff_max_pop is not None:
        sampling_payload["max_per_population"] = eff_max_pop
    eff_max_iid = (
        selector.sampling.max_per_individual
        if (selector.sampling and selector.sampling.max_per_individual is not None)
        else cli_max_per_individual
    )
    if eff_max_iid is not None:
        sampling_payload["max_per_individual"] = eff_max_iid
    # Policy: include only when non-default. v0.3 only ships
    # SamplingPolicy.TOP_COVERAGE, but keep the elision rule so a future
    # `policy: random` lands cleanly without churning existing signatures.
    eff_policy = (
        selector.sampling.policy if selector.sampling is not None else SamplingPolicy.TOP_COVERAGE
    )
    if eff_policy != SamplingPolicy.TOP_COVERAGE:
        sampling_payload["policy"] = eff_policy.value
    if sampling_payload:
        payload["sampling"] = sampling_payload

    body = rfc8785.dumps(payload)
    digest = hashlib.sha256(body).hexdigest()
    return f"sha256:{digest}"


def _canonical_any_branch(branch: AnyBranch) -> dict[str, Any]:
    """Branch dict for JCS serialization. Same canonicalization rules as
    top-level: drop `individual_ids_source` (path); union YAML-inline +
    file-loaded IDs; set-like ID lists sorted+deduped; absent (None / [])
    fields omitted."""
    b: dict[str, Any] = {}
    if branch.populations:
        b["populations"] = sorted(set(branch.populations))
    branch_union_ids = sorted(set(branch.individual_ids) | set(branch.individual_ids_from_source))
    if branch_union_ids:
        b["individual_ids"] = branch_union_ids
    if branch.modern_only is not None:
        b["modern_only"] = branch.modern_only
    if branch.min_coverage is not None:
        b["min_coverage"] = branch.min_coverage
    if branch.coverage_column is not None:
        b["coverage_column"] = branch.coverage_column
    if branch.date is not None:
        d: dict[str, int] = {}
        if branch.date.min_calbp is not None:
            d["min_calbp"] = branch.date.min_calbp
        if branch.date.max_calbp is not None:
            d["max_calbp"] = branch.date.max_calbp
        if d:
            b["date"] = d
    return b


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
