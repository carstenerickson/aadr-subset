"""Shared dataclasses + enums. Pure data; no imports from other aadr_subset modules.

Per LLD §2.3 / §2.4. All dataclasses are frozen + slots for immutability and
memory efficiency. types.py is a leaf in the module dependency graph.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

# --- Enums ---


class OutputFormat(StrEnum):
    """--format value for select/inspect. click-friendly via click.Choice."""

    IDS = "ids"
    TSV = "tsv"
    JSON = "json"


class ReportFormat(StrEnum):
    """--format value for report (no IDS option)."""

    TSV = "tsv"
    JSON = "json"


# --- Selector sub-types ---


@dataclass(frozen=True, slots=True)
class DateRange:
    """A `date:` block in a selector. At least one of min/max must be set
    (schema-enforced via minProperties: 1)."""

    min_calbp: int | None = None
    max_calbp: int | None = None


@dataclass(frozen=True, slots=True)
class ExcludeBlock:
    """An `exclude:` block. populations is a load-time alias for group_ids
    (resolved by selector.py before this dataclass is constructed)."""

    group_ids: list[str] = field(default_factory=list)
    individual_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class AnyBranch:
    """One branch of an `any:` block. Same filter-predicate fields as the
    top-level selector minus `any`, `exclude`, `source_version`,
    `resolve_to_version`, and `metadata` (all top-level-only)."""

    populations: list[str] = field(default_factory=list)
    individual_ids: list[str] = field(default_factory=list)
    individual_ids_source: Path | None = None
    modern_only: bool | None = None
    min_coverage: float | None = None
    coverage_column: str | None = None
    date: DateRange | None = None


@dataclass(frozen=True, slots=True)
class SelectorMetadata:
    """Optional metadata block from the first document of a two-doc YAML
    selector (template-style). Plain single-doc selectors return an empty
    SelectorMetadata."""

    tested_against: list[str] = field(default_factory=list)
    last_verified: str | None = None  # ISO date as string
    maintainer: str | None = None
    notes: str = ""


@dataclass(frozen=True, slots=True)
class Selector:
    """Parsed selector. Construct via selector.load_selector(); not directly.

    Field-value semantics:
    - list[str] defaults to []; an empty list means "this filter key is
      absent" (the JSON schema rejects present-but-empty lists, so [] is
      unambiguously "absent" at the dataclass layer).
    - bool | None: None means "absent"; True/False are explicit.
    - DateRange | None / ExcludeBlock | None: None means "absent".

    `individual_ids_from_source` holds IDs loaded from
    `individual_ids_source` file only (NOT merged with `individual_ids`).
    Engine + signature compute the union independently.
    """

    # Filter predicates (top-level)
    populations: list[str] = field(default_factory=list)
    individual_ids: list[str] = field(default_factory=list)
    individual_ids_source: Path | None = None
    modern_only: bool | None = None
    min_coverage: float | None = None
    coverage_column: str | None = None
    date: DateRange | None = None

    # Cross-version metadata
    source_version: str | None = None
    resolve_to_version: str | None = None

    # Combinators
    any_branches: list[AnyBranch] = field(default_factory=list)
    exclude: ExcludeBlock | None = None

    # Resolved cohort: IDs loaded from individual_ids_source ONLY (not unioned
    # with individual_ids; that happens in engine + compute_signature).
    individual_ids_from_source: list[str] = field(default_factory=list)

    # Metadata from first YAML document (two-doc selector form)
    metadata: SelectorMetadata = field(default_factory=SelectorMetadata)


# --- Engine result types (Day 1: declared; populated starting Day 2/3) ---


@dataclass(frozen=True, slots=True)
class ExcludeCount:
    """One row in SubsetResult.excluded_counts. List-of-objects form
    per HLD v4b §Output JSON."""

    key: str  # "group_ids" or "individual_ids"
    value: str
    count: int


@dataclass(frozen=True, slots=True)
class SelectorWarnings:
    """Non-fatal warnings collected during engine evaluation.

    `deprecated_selector_keys` holds unique deprecated YAML key names
    (e.g., ["master_ids", "master_ids_source"]) — NOT one entry per
    occurrence in the selector.
    """

    missing_after_resolve: list[str] = field(default_factory=list)
    duplicate_genetic_ids: list[str] = field(default_factory=list)
    deprecated_selector_keys: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SubsetResult:
    """Result of engine.select_samples(). Day 1 ships the type; engine.py
    landing on Day 2-3 populates it."""

    genetic_ids: list[str]
    n_matched: int
    per_population_counts: dict[str, int] = field(default_factory=dict)
    per_branch_counts: dict[str, int] = field(default_factory=dict)
    excluded_counts: list[ExcludeCount] = field(default_factory=list)
    matched_criteria: dict[str, list[str]] = field(default_factory=dict)
    warnings: SelectorWarnings = field(default_factory=SelectorWarnings)
    selector_signature: str = ""

    # Run-environment metadata (populated by run_select before return)
    anno_file: str = ""
    anno_version: str = ""
    schema_class: str = ""
    selector_file: str = ""
    coverage_column_used: str | None = None
