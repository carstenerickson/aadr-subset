"""Shared dataclasses + enums. Pure data; no imports from other aadr_subset modules.

Per LLD §2.3 / §2.4. All dataclasses are frozen + slots for immutability and
memory efficiency. types.py is a leaf in the module dependency graph.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal

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


class DiffFormat(StrEnum):
    """--format value for diff. `human` is a multi-line summary to stdout
    (default); `json` is a structured object for pipeline integration."""

    HUMAN = "human"
    JSON = "json"


class SamplingPolicy(StrEnum):
    """Stratified-sampling policy (v0.3+).

    v0.3 ships TOP_COVERAGE only; JSON-schema enforces the enum at
    validate time so `policy: random` errors at the validate step,
    not at engine runtime. RANDOM lands in v0.4+ alongside a required
    seed field (see cs-wiki/projects/aadr-subset-stratified-sampling.md
    §10 deferred items).
    """

    TOP_COVERAGE = "top_coverage"


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
    `resolve_to_version`, and `metadata` (all top-level-only).

    `individual_ids_from_source` holds IDs loaded from this branch's
    `individual_ids_source` file (v0.2+). Engine evaluation and
    `compute_signature` each union it with `individual_ids` independently
    — same pattern as the top-level Selector pair.
    """

    populations: list[str] = field(default_factory=list)
    individual_ids: list[str] = field(default_factory=list)
    individual_ids_source: Path | None = None
    modern_only: bool | None = None
    min_coverage: float | None = None
    coverage_column: str | None = None
    date: DateRange | None = None
    individual_ids_from_source: list[str] = field(default_factory=list)


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
class SamplingSpec:
    """Selector-side stratified-sampling spec (v0.3+).

    Both cap fields are independent — either or both can be None.
    Selector-side caps are union-merged with CLI flags
    (--max-per-population, --max-per-individual) at the engine entry;
    selector wins per-field per LLD pin (compute_signature follows the
    same merge so two equivalent selectors produce the same signature).

    Empty SamplingSpec() — both caps None AND no non-default policy —
    is schema-rejected at YAML validate time per `anyOf` in
    selector.schema.json. The dataclass allows construction (e.g. test
    fixtures) but a Selector with such a SamplingSpec never reaches
    the engine via load_selector.
    """

    max_per_population: int | None = None
    max_per_individual: int | None = None
    policy: SamplingPolicy = SamplingPolicy.TOP_COVERAGE


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

    # Stratified-sampling spec (v0.3+; None = no sampling layer).
    sampling: SamplingSpec | None = None

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
class SamplingDrop:
    """One row in SubsetResult.sampling_drops (v0.3+).

    Mirrors the ExcludeCount list-of-objects shape. List ordering in
    SubsetResult.sampling_drops matches engine application order:
    per-individual entries first (since per-IID applies first per
    LLD pin), then per-population — so consumers reading the list
    see the same sequence the engine applied.
    """

    dimension: Literal["population", "individual"]
    key: str  # group_id (when dimension="population") or individual_id
    count: int  # number of candidate rows dropped at this key


@dataclass(frozen=True, slots=True)
class SelectorWarnings:
    """Non-fatal warnings collected during engine evaluation.

    `empty_glob_patterns` (v0.2+) lists Group_ID glob patterns from the
    selector that expanded to zero matching labels in the target .anno
    — a typo signal, since matching nothing in the corpus is almost
    never intentional.
    """

    missing_after_resolve: list[str] = field(default_factory=list)
    duplicate_genetic_ids: list[str] = field(default_factory=list)
    empty_glob_patterns: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SubsetResult:
    """Result of engine.select_samples(). Day 1 ships the type; engine.py
    landing on Day 2-3 populates it."""

    genetic_ids: list[str]
    n_matched: int
    per_population_counts: dict[str, int] = field(default_factory=dict)
    per_branch_counts: dict[str, int] = field(default_factory=dict)
    excluded_counts: list[ExcludeCount] = field(default_factory=list)
    sampling_drops: list[SamplingDrop] = field(default_factory=list)
    matched_criteria: dict[str, list[str]] = field(default_factory=dict)
    warnings: SelectorWarnings = field(default_factory=SelectorWarnings)
    selector_signature: str = ""

    # Run-environment metadata (populated by run_select before return)
    anno_file: str = ""
    anno_version: str = ""
    schema_class: str = ""
    selector_file: str = ""
    coverage_column_used: str | None = None


@dataclass(frozen=True, slots=True)
class DiffResult:
    """Result of `aadr-subset diff selA.yaml selB.yaml ANNO.anno`.

    Set-difference of two SubsetResult.genetic_ids lists computed against
    the same target AnnoFrame, plus per-population deltas (n_a, n_b per
    Group_ID that either side matched).

    GeneticID list ordering: a_only, b_only, and both preserve the .anno
    row order of their respective SubsetResult — the lists are produced by
    filtering the union genetic_ids set against each SubsetResult, so
    callers can downstream-iterate them in a stable order.
    """

    a_only: list[str]
    b_only: list[str]
    both: list[str]
    per_population_delta: dict[str, tuple[int, int]]  # group_id -> (n_a, n_b)

    a_signature: str
    b_signature: str
    selector_a_file: str
    selector_b_file: str

    anno_file: str
    anno_version: str
    schema_class: str
