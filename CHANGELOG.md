# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] — 2026-05-12

### Added

- **Stratified sampling** — cap your cohort to at most N samples per
  Group_ID and/or per Individual_ID. Designed in
  [cs-wiki/projects/aadr-subset-stratified-sampling.md](https://github.com/carstenerickson/claude/blob/main/cs-wiki/projects/aadr-subset-stratified-sampling.md);
  18 LLD pins; 35 tests. Use cases: PCA balance (cap 50 per population
  so 4,000 modern English don't swamp 50 Yamnaya), multi-library dedup
  (`max_per_individual: 1` picks the best library per individual,
  collapsing AADR's `.AG` / `.DG` / `.SG` triples).

  - **Selector grammar** gains a `sampling:` block:

    ```yaml
    sampling:
      max_per_population: 50      # integer ≥ 1
      max_per_individual: 1
      policy: top_coverage        # default; future: random+seed
    ```

  - **CLI flags** `--max-per-population N` / `--max-per-individual N`
    on `select`, `inspect`, `report`. `click.IntRange(min=1)` rejects
    zero / negatives at click parse time. Selector wins per-field
    (selector pins `max_per_population: 50` + CLI is
    `--max-per-individual 1` → both apply).
  - **Algorithm**: `top_coverage` policy sorts within each group by
    `af.coverage` descending, takes top-N. Stable sort means tie-break
    is `.anno` row order — deterministic without a seed. NaN coverage
    sinks via `na_position='last'`. Per-individual cap fires BEFORE
    per-population — broader-policy-second rule yields more survivors
    when caps interact (counter-example in the design doc §4).
  - **Signature semantics**: caps + policy enter the signature payload
    per-field (selector wins; CLI fills omissions). Same selector
    against v62 vs v66 produces the same hash even when the resolved
    cohort differs — same intent-not-expansion rule as Group_ID globs.
    Default `policy: top_coverage` elided from canonical form so
    explicit vs omitted produce equal hashes.
  - **`SubsetResult.sampling_drops: list[SamplingDrop]`** mirrors the
    `excluded_counts` list-of-objects shape. JSON output gains a
    top-level `sampling_drops` field (additive — no JSON_SCHEMA_VERSION
    bump). Per-individual entries first, then per-population — same
    sequence the engine applied. Sparse: only populated keys appear.
  - **Class-D + sampling without `--coverage-derive` is a hard fail**
    (IOFailure). Without a coverage column, prioritization is undefined
    — silent degrade would violate principle of least surprise on a
    feature the user explicitly asked for.
  - **`inspect` summary** gains a "Downsampled" section showing
    per-population drops explicitly + a one-line per-individual
    aggregate (per-IID rows can be in the thousands; aggregate keeps
    inspect readable, JSON output preserves the per-IID detail).
  - **Cross-version + sampling**: per-individual cap operates on
    TARGET Individual_IDs after the IID lift; coverage priority uses
    the target `.anno`'s coverage. Integration-tested with the v62→v66
    Loschbour fixture.
  - **Branch `coverage_column` doesn't propagate to sampling** —
    sampling uses the top-level effective coverage column.

### Changed

- **JSON output gains `sampling_drops`** as the 6th top-level field
  (between `excluded_counts` and `matched_criteria`). Additive — old
  consumers continue to work. JSON-schema version unchanged.

- **`_compute_per_branch_counts` semantic**: branch contributions now
  reflect post-sampling counts. The function gained an optional
  `final_mask` parameter; engine wires the reduced (post-sampling)
  mask through. Without sampling, behavior unchanged.

- **`SamplingPolicy` enum schema-locked to `top_coverage`** in v0.3.
  YAML with `policy: random` errors at validate time (selector schema
  enum rejection) rather than at engine runtime. v0.4+ will extend
  the enum.

### Engine internals

- New `engine._apply_sampling(af, candidates_mask, *, spec,
  coverage_column) -> (reduced_mask, drops)` — public-ish private
  helper. Sorts via `pd.DataFrame.sort_values(kind='stable',
  na_position='last')` for cross-pandas determinism; pinned via a
  snapshot test.
- New `engine._merge_sampling_spec(selector_spec, *, cli_*)` — per-
  field merge of selector vs CLI values; returns None when nothing is
  set (engine skips the sampling layer entirely).

### Deferred to v0.4+

Per the design doc §10:
- `policy: random` with required `seed:` field (signature includes
  seed; uses `numpy.random.default_rng` for platform-independent
  reproducibility).
- `policy: stratified_by_date_bins`.
- Branch-level `sampling:` in `any:` branches.
- `min_per_population: N` (floor for qpAdm source requirements).
- `sample_n_total: N` (global cap regardless of group).
- Fraction syntax (`max_per_population: 0.5` → 50% of each group).
- Per-sex / per-haplogroup caps.
- `select --sampling-manifest PATH.tsv` (per-candidate decision +
  reason TSV for audit).

---

## [0.2.0] — 2026-05-13

### Added

- **`aadr-subset diff SELECTOR_A.yaml SELECTOR_B.yaml ANNO.anno`** — new
  sixth subcommand. Set-difference of two selectors against the same
  target `.anno`: which samples does A match that B doesn't, and vice
  versa, plus a per-population delta. Default `human` format is a
  multi-line stdout summary (header lines with short-form selector
  signatures, A/B/Both sample counts, per-population delta table with
  `+N`/`-N` deltas, sample preview truncated at 10 with tail count).
  `--format json -o PATH` writes a structured object with
  `a_only[]` / `b_only[]` / `both[]` arrays, `per_population_delta[]`
  rows (`{group_id, n_a, n_b, delta}`), both signatures, anno
  metadata, schema_version. Diagnostic by design — always exits 0;
  even an empty diff (selectors that match identically) is itself a
  signal worth reporting. Cross-version selectors (`resolve_to_version:`
  set on either side) are rejected with a UsageError in v0.2; the
  workaround is to materialize each selector via `select` separately
  and diff the resulting ID lists.

- **Group_ID glob patterns** (HLD: `populations:` literal matching now
  supports fnmatch-style `*`, `?`, `[abc]`). A literal containing any of
  `*`, `?`, `[` is treated as a pattern and expanded against the target
  `.anno`'s Group_ID set at engine evaluation; plain literals pass
  through unchanged.
  - Works in `populations:`, `exclude.group_ids:`, and any-branch
    `populations:`.
  - Mixed literal+pattern lists are deduped in first-appearance order.
  - Patterns that expand to zero matching labels in the target `.anno`
    are recorded in `SubsetResult.warnings.empty_glob_patterns` and
    surfaced via stderr WARNING from `run_select`. They're a near-certain
    bug signal (typo'd pattern, wrong AADR version).
  - `exclude.group_ids` glob expansion is reported in `excluded_counts`
    per concrete label (one ExcludeCount per matched Group_ID, not one
    aggregate row for the pattern).
  - **Signature pin**: `compute_signature` hashes the **pattern** as
    written, not the resolved expansion. The same selector against v62
    vs v66 produces the same signature — the cohort definition (the
    pattern, capturing user intent) is what's hashed; the resolved set
    depends on the `.anno` and isn't part of the signature contract.

- **Branch-level `individual_ids_source`** (closes a v0.1 deferred
  feature). `any:` branches can now load Individual_IDs from a file the
  same way the top-level Selector can. The branch's
  `individual_ids_from_source` field is populated by `selector.load_selector`
  (recursing into branches) and unioned with the inline `individual_ids`
  in both engine evaluation and `compute_signature` canonicalization.

### Changed (breaking)

- **`master_ids:` / `master_ids_source:` are now errors.** In v0.1 they
  were deprecated aliases (warn + rewrite to `individual_ids:` /
  `individual_ids_source:`). v0.2 removes the alias entirely: any
  selector still using them errors with a clear "renamed to … in v0.1;
  removed in v0.2" `ValidationError` (`constraint='removed_deprecated_alias'`,
  exit 4). The diagnostic is per-occurrence — top-level AND every
  any-branch site are reported in one pass so the user fixes them all
  at once.

### Removed

- `SelectorWarnings.deprecated_selector_keys` field (was declared in v0.1
  but never populated; obsolete now that aliases are hard errors).

- **Templates refreshed with globs.** Five of six shipped templates now
  use Group_ID glob patterns where they consolidate v62 suffix variants
  + v66 site-fragmented labels in fewer lines. Match counts changed:
  - `bronze_age_europe`: v62 199 → 236, v66 259 → 367 (Germany_*_BellBeaker
    glob captures v66's site-prefixed labels).
  - `wsh_steppe_pool`: v62 21 → 24, v66 72 → 152 (Russia_*EBA_Yamnaya*
    glob captures dozens of country-prefixed Yamnaya variants).
  - `iron_age_britain`: 3 literals → 2 (England_IA + England_IA.[AS]G).
  - `viking_period_scandinavian`: explicit country list → per-country
    globs (Norway_Viking*, Sweden_Viking*, etc.).
  - `neolithic_anatolia`: explicit Marmara breakdown → Turkey_*Barcin*
    / Turkey_*Catalhoyuk_*N* / etc. globs.
  - `modern_european` unchanged — explicit reference panel labels are
    clearer than globs that would over-match.

### Engine internals

- `_build_predicate_mask` now accepts `populations: list[str] | None`:
  `None` = no constraint, `[]` = constraint set but resolves empty
  (match nothing, all-False contribution), non-empty = `isin` filter.
  This distinguishes "user didn't set populations" from "user set a
  populations glob that matched zero Group_IDs" — the former is no-op,
  the latter is meaningful (match nothing). Same tri-state applies to
  any-branch populations.

---

## [0.1.0] — 2026-05-12

First public release.

### Highlights

`aadr-subset` is a declarative AADR panel-subsetting CLI + library:
ship a YAML selector, get back a sample-ID list / TSV / JSON or a
per-population aggregate report. Built on
[aadr-resolve](https://pypi.org/project/aadr-resolve/) for cross-AADR-
version sample-ID mapping.

The five subcommands cover the full cohort lifecycle:
- `validate` — JSON-schema + semantic check, no `.anno` required (CI gate).
- `select` — materialize the cohort. `--format {ids,tsv,json}`,
  `-o PATH` (atomic), `--source-anno` for cross-version lift,
  `--coverage-column`/`--coverage-derive` for v62-class-D proxy
  coverage, `--strict-resolve` for fail-on-missing-IID, `--allow-empty`.
- `inspect` — diagnostic dry-run; always exits 0.
- `report` — per-population aggregates (group_id, n_matched, n_in_anno,
  pct_matched, date range, coverage stats) as TSV or JSON.
- `template` — discover + emit 6 starter templates.

### Reproducibility

Every `SubsetResult` carries a `selector_signature` — RFC 8785 JCS
canonical-form SHA-256 over selector intent. Invariant to YAML key
ordering, list ordering for set-like fields, `individual_ids_source`
path differences (file *content* drives the hash, not the path), and
metadata-block changes. Captures the effective `coverage_column` so
two runs with different `--coverage-derive` values produce different
signatures.

JSON output records the full run-env: AADR version + schema class,
selector + source-anno paths, aadr-subset + aadr-resolve versions,
coverage column used, schema_version (additive new keys are
non-breaking).

### Template catalog (verified against v62.0 + v66.0)

- `modern_european` — modern reference set (570 v66 / 493 v62 matched)
- `iron_age_britain` — England_IA cohort (45 / 9)
- `bronze_age_europe` — Bell Beaker + Corded Ware + Yamnaya + Unetice (259 / 199)
- `wsh_steppe_pool` — Yamnaya + Poltavka + Eneolithic-steppe (72 / 21)
- `neolithic_anatolia` — Catalhoyuk + Barcin + consolidated Turkey_N (58 / 28)
- `viking_period_scandinavian` — homeland + diaspora (238 / 224)

Templates auto-skip releases they don't claim in `tested_against:`
metadata, so adding a new template against just the current AADR
release doesn't fail audits for older versions.

### Performance (v66.HO, 27,755 samples, class E, MacBook Air M2)

- `AnnoFrame.from_path`: ~460 ms
- `engine.select_samples`: ~7 ms per call (warm; per-AnnoFrame caches
  on first call)
- `compute_signature`: ~0.03 ms
- End-to-end `select` CLI (incl. Python + click startup): ~600 ms

### Engine surface (HLD v0.1 grammar complete)

Top-level AND of: `populations`, `individual_ids` +
`individual_ids_source`, `modern_only`, `date.{min,max}_calbp`,
`min_coverage` + optional `coverage_column`. Plus one-level `any:` OR
(per-branch fields + optional per-branch `coverage_column` override)
and one-level `exclude:` NOT-of-OR. Cross-version via `source_version`
+ `resolve_to_version` lifts source Individual_IDs to target via
`aadr_resolve.resolve_master_ids`.

### CI + release

- GitHub Actions matrix: Python 3.11/3.12/3.13 × Ubuntu/macOS
- Coverage gate: 90% (currently ~92%)
- mypy `--strict`; ruff lint + format
- Release pipeline: build → smoke-test on full matrix → OIDC PyPI publish
- 188 unit tests + 12 integration tests (latter gated on
  `AADR_V62_ANNO_PATH` / `AADR_V66_ANNO_PATH`)

### Dependencies

- `aadr-resolve >=0.2.0, <0.3` (PyPI)
- `pandas >=2.2, <3`
- `click >=8.1, <9`
- `pyyaml >=6.0`
- `ruamel.yaml >=0.18`
- `jsonschema >=4.20`
- `rfc8785 >=0.1`

---

## Pre-release development log

The day-by-day implementation history is preserved below for context.
Production use should reference the `[0.1.0]` section above.

### Changed (Day 10 — v66.0 template verification + README rewrite)

Day-9 verified templates against v62.0 only. Day 10 extends the audit
to AADR v66.0 (Mallick & Reich 2023) and rewrites the README around the
five-subcommand surface that landed across Days 1-8.

- **Every template now resolves on both v62.0 and v66.0.** AADR's
  labeling convention changed substantially at v66: the `.AG` / `.SG` /
  `.DG` / `.HO` suffixes were dropped for canonical labels, and several
  large horizons (Germany_BellBeaker, Germany_CordedWare) were
  fragmented into site-specific labels. Each template now lists both
  v62 and v66 forms — unmatched literals are harmless to the OR-of-
  literals match. Notable changes:
  - `modern_european` lists `English.{DG,HO}` + bare `English` (and
    same for French, Italian_North, Spanish, Norwegian, Finnish, Greek,
    Russian). 493 matched in v62.0 → 570 in v66.0.
  - `wsh_steppe_pool` adds the v66 names: `Russia_Samara_MBA_Poltavka`
    (replaces v62's site-less `Russia_MBA_Poltavka.AG`),
    `Russia_Eneolithic_Steppe` (region/period order reversed from
    v62's `Russia_Steppe_Eneolithic.AG`).
  - `neolithic_anatolia` retains v62 Barcin labels but adds v66's
    consolidated `Turkey_Catalhoyuk_N` + `Turkey_N` — Barcin was
    dropped from v66 HO (still in 1240K). 28 matched in v62 → 58 in
    v66.
  - `iron_age_britain` adds bare `England_IA` alongside the v62
    `England_IA.AG` / `England_IA.SG`. 9 matched in v62 → 45 in v66.
  - Every template's `tested_against:` bumped `[v62.0]` →
    `[v62.0, v66.0]`.
- **Integration test extended to both versions.** New parametrization
  is `(version, template_name)` — 12 cells total. Each cell loads the
  template, runs through `engine.select_samples` against the gated
  release's `.anno`, and asserts `n_matched > 0`. Skips releases the
  template doesn't claim (so a v62-only template doesn't fail the v66
  cell — that's audit-pending, not broken).
- **Two env vars** — `AADR_V62_ANNO_PATH` and `AADR_V66_ANNO_PATH`.
  Each release is gated independently so contributors can run just the
  versions they have on disk.
- **README rewrite.** Was Day-1-stale ("Day 1 surface", "this README
  will expand on Day 11"). New shape: opening 4-line example showing
  the full select cycle, then "Why it exists" framing, install
  instructions, one section per subcommand (validate, select, inspect,
  report, template), exit-code table, selector-grammar overview, and
  a plink2 composition snippet.

### Changed (Day 9 — verified template Group_ID literals against real v62.0)

The Day-8 starter templates shipped with plausible-looking Group_ID
literals that turned out to be largely wrong: AADR uses
`Country_Period_Culture.{AG|SG|DG}` rather than the short
`Region.Period` form the templates assumed. Audited all six against a
real AADR v62.0 release and rewrote them with verified labels.
Re-running each template through the engine now produces non-zero
matches across the corpus.

- **All 6 templates rewritten with v62.0-verified Group_IDs.**
  Highlights:
  - `iron_age_britain`: was `England.IA` (no match) → `England_IA.AG`
    + `England_IA.SG` (9 matched samples in v62.0).
  - `bronze_age_europe`: was `Bell_Beaker_Britain` etc. (no match) →
    country-prefixed `England_BellBeaker.AG`, `Germany_CordedWare.AG`,
    `Russia_Samara_EBA_Yamnaya.AG`, etc. (199 matched).
  - `wsh_steppe_pool`: was `Yamnaya_Samara`, `Poltavka`,
    `Eneolithic_steppe` → `Russia_Samara_EBA_Yamnaya.AG`,
    `Russia_MBA_Poltavka.AG`, `Russia_Steppe_Eneolithic.AG` (21
    matched).
  - `neolithic_anatolia`: was `Anatolia_N`, `Barcin_N` → AADR's
    `Turkey_Marmara_Barcin_N.{SG,AG,DG}` +
    `Turkey_Central_Catalhoyuk_N.SG` etc. (28 matched).
  - `viking_period_scandinavian`: was `Norway.VA`, `Sweden.VA` →
    `Norway_Viking.SG`, `Sweden_Viking.SG` + the diaspora
    (`Iceland_Viking.SG`, `England_Viking.SG`, ...). Two `any:`
    branches split homeland from diaspora. (224 matched.)
  - `modern_european`: was `English.SG`, `Italian_North.SG` (modern
    samples don't carry `.SG` in AADR) → `English.{DG,HO}`,
    `Italian_North.{DG,HO}`, `Russian.{DG,HO}`, etc. (493 matched.)
  - Each template's metadata `tested_against:` updated `[v66.0]` →
    `[v62.0]` to reflect what we actually verified against. Notes
    block updated with explanation of the AADR labeling convention
    and pointers to add-on labels (Roman, EarlyMedieval, etc.) for
    each cohort.
- **New `tests/integration/test_templates_against_real_anno.py`** —
  parametrized over `list_templates()`; each test loads the
  corresponding template, runs it through `engine.select_samples`
  against a real v62.0 .anno, and asserts `n_matched > 0`. v62.0 is
  class D (no native coverage), so the test passes
  `coverage_column="snps_hit_1240k"` to route min_coverage filters
  through the derived proxy.
- **Gating via `AADR_V62_ANNO_PATH` env var.** AADR's data release
  notes prohibit redistribution, so the .anno file can't be committed
  to the repo. Tests self-skip with a clear message when the env var
  isn't set (CI; first-time clones); contributors who have the public
  release export `AADR_V62_ANNO_PATH=/path/to/v62.0_HO_public.anno`
  to enable them locally. This is the audit-cadence test — re-run
  when bumping `tested_against` or adding new templates.

### Added (Day 8 — template subcommand + 6 starter templates)

`aadr-subset template` ships as the discovery aid for new users —
no-arg form lists the bundled starting points, name-arg form emits
the verbatim YAML (comments + metadata block preserved) to stdout or
a file. Six starter templates ship under `aadr_subset/templates/`.

- **`templates.py`** — discovery API per LLD §3.7:
  - `list_templates()` returns sorted basenames (no `.yaml`). Discovery
    is by directory listing only; no manifest file, so adding a new
    template is a one-file PR.
  - `load_template(name)` parses via `selector.load_selector`, returning
    `(metadata, selector)`. Unknown name → `IOFailure` with a discovery
    hint listing the shipped names.
  - `emit_template(name, out)` writes the raw bytes — no YAML
    round-trip. The pin: round-tripping through ruamel.yaml is lossy
    for edge cases (block-scalar vs literal-scalar, anchor/alias
    preservation). Byte-verbatim emit guarantees users editing a
    saved template start from the same baseline aadr-subset ships.
  - `.yaml` only — `.yml` rejected for consistency.
- **`commands/template_cmd.run_template`** — list mode when `name=None`
  (sorted names to stdout, one per line); emit mode otherwise (verbatim
  YAML to stdout or `--out PATH` via `atomic_write`). Unknown name
  bubbles up the `IOFailure` from `_template_path` → exit 2.
- **CLI**: new `template [NAME] [-o PATH]` subcommand.
- **Six starter templates** under `aadr_subset/templates/` —
  `modern_european`, `iron_age_britain`, `bronze_age_europe`,
  `wsh_steppe_pool`, `neolithic_anatolia`,
  `viking_period_scandinavian`. Each is a two-document YAML with a
  metadata block (`tested_against`, `last_verified`, `maintainer`,
  `notes:`) and a working selector body. Templates are STARTING POINTS
  — Group_ID labels in the .anno corpus drift across releases, so the
  notes section warns users to audit against their actual target
  before production use.
- **Parametrized "every shipped template loads cleanly" test** — a
  guard that catches a malformed addition at PR time. Each shipped
  template must (a) parse through `load_selector` without
  `UsageError`, (b) carry `tested_against` metadata, (c) have a
  non-empty selector body.

### Added (Day 7 — --coverage-column / --coverage-derive flags)

Closes out the last HLD v0.1 selector key. `min_coverage` filters now
route through `AnnoFrame.coverage_via(name)` when a `coverage_column:`
override is supplied (either in the selector YAML or via CLI), making
v62.0 (class D, no native coverage column) usable instead of silently
empty.

- **`engine.select_samples` gains `coverage_column: str | None`**.
  Effective top-level value is `selector.coverage_column or
  cli_coverage_column` (selector wins per HLD §Coverage handling).
  Each `any:` branch resolves to
  `branch.coverage_column or top_effective` so a branch can pin its
  own override while inheriting otherwise.
- **`engine._coverage_series`** — new helper that picks `af.coverage`
  when no override is set, else `af.coverage_via(name)`. Maps
  `aadr_resolve.MissingNativeFieldError` to `IOFailure` so the user
  sees a clean exit-2 message ("coverage column 'X' is not available
  in v66.0 (schema class E)") instead of an internal traceback.
- **`commands/select_cmd._normalize_coverage_flags`** — merges
  `--coverage-column` and `--coverage-derive` (HLD §Coverage handling
  aliases). Both-set is `UsageError` (exit 4) rather than silent
  precedence.
- **`run_select`** now:
  - threads `cli_coverage_column` into engine + signature compute.
  - records `result.coverage_column_used` (the effective post-merge
    value), exposed via the JSON output's `coverage_column` top-level key.
  - suppresses the v62 class-D coverage warning when any override is
    supplied (selector-level or CLI-level) — the proxy path makes the
    "silently empty" failure mode go away.
- **`selector.compute_signature` integration**: `cli_coverage_column`
  enters the signature only when the selector itself doesn't set
  `coverage_column:`. Run reproducibility now captures the effective
  column.
- **CLI**: `select` gains `--coverage-column NAME` and
  `--coverage-derive NAME` (aliases). `--help` documents both.
- **Engine feature gate is now empty.** All HLD v0.1 selector grammar
  is wired. Day 7 closes the implementation phase; days 8-15 cover
  templates, polish, performance, and v0.1.0 release.

### Added (Day 6 — cross-version IID lift via aadr-resolve)

Cross-version is now a fully wired path through engine + run_select.
The selector key `resolve_to_version:` activates the lift: source
Individual_IDs are mapped to target Individual_IDs through
`aadr_resolve.resolve_master_ids`, then the engine's predicate mask
matches `af.individual_id.isin(target_iids)`. The target-IID set
captures every row for each individual in target (multi-library /
multi-data-type), per HLD test 16.

- **`engine.select_samples` gains three kwargs**: `source_anno`,
  `mid_bridge`, `strict_resolve`. When `selector.resolve_to_version`
  is set, engine calls `_resolve_cross_version` before mask
  construction; `target_iids` supersedes
  `selector.individual_ids` for that run. `strict_resolve=True`
  raises `SoftValidationFailure` (exit 1) when any source IID fails to
  place in target; default behavior surfaces those IDs via
  `SubsetResult.warnings.missing_after_resolve` and continues.
- **`engine._resolve_cross_version`** — wraps
  `aadr_resolve.resolve_master_ids(ids, src_version, dst_version,
  anno_paths={...}, mid_bridge=...)`. CollisionDetected from
  aadr-resolve is re-raised as `InvariantViolation` (cross-lab MID
  collision is a bridge-quality problem, not user input). Defensive
  None-check on `source_anno.path` / `target_anno.path` guards against
  AnnoFrames built outside `from_path()`.
- **`engine._lift_gid_to_iid`** — `aadr_resolve.resolve_master_ids`
  returns target *Genetic_IDs*; the engine lifts each back to its
  target Individual_ID via a per-AnnoFrame `(gid → iid)` cache so the
  later `af.individual_id.isin(...)` mask catches every row for that
  individual. Cache is keyed by `id(af)` and built lazily on first
  call.
- **`commands/select_cmd._resolve_cross_version_inputs`** — validates
  the four flag/selector combinations up front (LLD §4.1 step 4):
  no-op when neither side is set; `UsageError` on each of the three
  malformed combinations; verifies target `anno.version` matches
  `selector.resolve_to_version` and (when set) source `anno.version`
  matches `selector.source_version`.
- **Non-strict missing-IID warning** — after engine returns,
  `run_select` surfaces `warnings.missing_after_resolve` to stderr
  unless `--strict-resolve` was passed (in which case engine already
  raised). First 10 IDs shown inline; tail count appended as
  `(+N more)` when the list is longer.
- **CLI**: `select` gains `--source-anno PATH`, `--mid-bridge PATH`,
  `--strict-resolve`. `--source-anno` and the selector's
  `resolve_to_version:` form a hard requirement pair (each errors with
  a clear message when used without the other).
- **Feature gate shrinks** to just `coverage_column:` (pending the
  `--coverage-column` / `--coverage-derive` CLI flags). Day 6 closes
  out the last HLD-listed v0.1 engine feature.

### Added (Day 5 — selector signature + report subcommand)

Reproducibility primitive (selector signature) and the third output
surface (`report` per-population aggregates) both land. `select` and
`inspect` now populate `selector_signature` on every result; `report`
emits per-group TSV / JSON aggregates with date + coverage stats.

- **`selector.compute_signature(selector, *, cli_coverage_column)`** —
  SHA-256 over the RFC 8785 (JCS) canonical form of selector intent.
  Returns `"sha256:" + hexdigest`. Canonicalization rules per LLD §3.3:
  YAML-inlined + file-loaded `individual_ids` unioned (sorted, deduped);
  `populations` and `exclude.{group_ids,individual_ids}` deduped and
  sorted; `individual_ids_source` path dropped (file *content* is the
  signature input, not the path); `any_branches` order preserved
  (they're indexed by `any[i]`); metadata block stripped; CLI
  `--coverage-column` injected only when the selector itself doesn't
  set `coverage_column:` (selector wins per HLD §Coverage handling).
  Pure function — same selector + same coverage env produces the same
  hash regardless of YAML key ordering or list internal order.
- **Wired into `run_select` + `run_inspect`** — both populate
  `SubsetResult.selector_signature` before returning. JSON output now
  emits the real hash; `format_stdout_summary` shows the short form
  (`sha256:abcdefg...hijklmn`) in the header; `format_inspect_summary`
  shows the full hash as its trailing line.
- **`reporting.write_report_tsv`** — 7-column TSV (`group_id`,
  `n_matched`, `n_in_anno`, `pct_matched`, `date_min_calbp`,
  `date_max_calbp`, `coverage_median`). `pct_matched` rendered to 1
  decimal place via `f"{pct*100:.1f}"`; no `%` suffix in cells.
  Date / coverage aggregates computed over MATCHED rows only.
  `include_empty_groups=False` (the default) emits only rows where
  `n_matched > 0`; `=True` additionally emits all other groups present
  in `.anno` with zero-filled counts (population-survey workflows).
- **`reporting.write_report_json`** — structured JSON; top-level keys
  `selector_signature`, `anno_version`, `schema_version`,
  `aadr_subset_version`, `populations[]`. Per-population entries:
  `group_id`, `n_matched`, `n_in_anno`, `pct_matched`,
  `date_min_calbp`, `date_max_calbp`, `coverage_median`,
  `coverage_min`, `coverage_max`. `pct_matched` is a fraction (0.0-1.0),
  NOT rendered like the TSV — JSON consumers do their own formatting.
- **`commands/report_cmd.run_report`** — orchestrator parallel to
  `run_select`: load selector → load AnnoFrame → compute signature →
  engine eval → zero-match `--allow-empty` gate → run-env metadata →
  write report. Stdout summary is intentionally a single line
  (`Wrote report.tsv (4 populations, 287 samples) in 0.12s.`) — no
  parse/eval/write breakdown like select.
- **CLI**: new `report SELECTOR ANNO` subcommand with `--format tsv|json`
  (default `tsv`), `-o/--out`, `--schema-override`, `--allow-empty`,
  `--allow-empty-source`, `--include-empty-groups`.

### Added (Day 4 — inspect mode + tsv/json output formats + stdout summary)

Output surface filled out: `select` gains `--format tsv` / `--format json`
alongside the Day-2 `ids`; new `inspect` subcommand prints a human-friendly
breakdown without producing files. Stdout summary moved to a dedicated
`reporting` module shared by both commands.

- **`formats.write_tsv`** — 6-column TSV (`genetic_id`, `individual_id`,
  `group_id`, `date_calbp`, `coverage`, `matched_criteria`) with header
  row. Empty cells for `<NA>` date / NaN coverage. Coverage rendered as
  plain float (`{:g}`) — no `x` suffix, so downstream parsers can read
  the column as numeric. `matched_criteria` cell is semicolon-joined; an
  empty string when `--include-matched-criteria` is off (the default).
  CSV writer uses `csv.QUOTE_NONE` since AADR Group_IDs / IIDs don't
  contain tab characters in practice.
- **`formats.write_json`** — full `SubsetResult`-shape JSON with the
  16-key insertion order pinned per LLD §3.5 (HLD §Output JSON).
  `matched_criteria` is **omitted entirely** when empty (the
  `--include-matched-criteria=False` default), reducing the key count
  to 15. `aadr_resolve_version` resolved at write time via
  `getattr(aadr_resolve, "__version__", "unknown")` so the artifact
  records the exact resolver pinned at run time. `schema_version: 1`
  always present; only bumped on breaking JSON shape changes (additive
  new keys are non-breaking).
- **`formats.write_select_output`** dispatcher routes by `OutputFormat`
  enum; `out_path=None` → stdout (no atomicity contract); `out_path`
  set → `atomic_write` per LLD §3.5.
- **`reporting.format_stdout_summary`** — multi-line stderr summary
  shared by `select`. Inline form for <10 populations
  (`Per-population: A=187, B=34, ...`); columnar form ≥10. Timing
  breakdown (parse / eval / write / total). Header includes selector
  signature (short form `sha256:abcdefg...hijklmn`) when populated;
  Day-2 / Day-3 results have empty signature so the line is omitted.
- **`reporting.format_inspect_summary`** — always-columnar layout for
  the inspect subcommand. Sections: per-population, branch
  contributions, exclusions, date range + coverage range over matched
  rows. No timing block — inspect's purpose is debugging the selector.
- **`commands/inspect_cmd.run_inspect`** — wraps `engine.select_samples`
  with `include_matched_criteria=True` so the per-row criteria are
  available even though inspect doesn't emit them per-row. Always
  returns `EXIT_SUCCESS`, even on zero matches (inspect is diagnostic;
  zero matches is itself useful information). `--strict-resolve`
  surfaces a diagnostic line but never changes the exit code.
- **CLI surface**: `select --format {ids,tsv,json}` (default `ids`);
  new `inspect SELECTOR ANNO` subcommand with `--schema-override`,
  `--allow-empty-source`, `--strict-resolve`.

### Added (Day 3 — any:/exclude: combinators + date/modern_only/min_coverage)

Selector evaluation algorithm wired end-to-end (HLD §Selector evaluation
algorithm). Feature gate shrinks: `any:`, `exclude:`, `date:`,
`modern_only:`, `min_coverage:` all now execute. Remaining gated:
`coverage_column:` (pending --coverage-column CLI flag) and cross-version
(`source_version:` + `resolve_to_version:`; Day 6).

- **Top-level AND mask** now includes `date.min_calbp` / `date.max_calbp`,
  `modern_only: true` (shorthand for `date_calbp <= 70`, per HLD §Modern
  vs ancient detection), and `min_coverage:` (NaN coverage FAILS the
  threshold per HLD §Coverage handling).
- **`any:` OR-block** — list of branches; each branch is a full
  AND-predicate evaluated against the AnnoFrame; branch masks OR-
  combined. Branch-internal `individual_ids_source:` loading deferred
  to v0.2 (no current use case; schema allows the key but engine
  treats branch-source as empty).
- **`exclude:` NOT-of-OR block** — per-condition OR over
  `exclude.group_ids` + `exclude.individual_ids`; final mask is
  top_and AND any_or AND NOT(exclude_or). `excluded_counts` populated
  with one ExcludeCount per excluded literal (per HLD v4b list-of-
  objects form).
- **`per_branch_counts`** populated: `top_level` + `any[0]` /
  `any[1]` / ... keys, each counting that branch's CONTRIBUTION to
  the final result (intersection with top_and + exclude_keep), not
  the branch's gross mask. Per HLD pin.
- **v62 class-D coverage warning** wired in `commands/select_cmd.py`:
  when target `.anno` is class D (no native coverage column) AND
  selector contains `min_coverage:` (at top level or any: branch),
  stderr WARNING points at the `--coverage-derive snps_hit_1240k`
  opt-in (pending CLI flag). Check uses `af.schema_class.value == "D"`
  (canonical) rather than `af.version` (fragile against test-fixture
  filename inference).

### Tests (Day 3)

- 19 new engine unit tests (47 total): modern_only boundary, date
  range (single + both bounds), min_coverage threshold + NaN
  semantics, flat AND combining populations + date + coverage,
  any:-block (3-branch, dedup, per-branch counts), exclude:
  (group_ids, individual_ids, per-literal excluded_counts), complex
  AND+OR+NOT compound selector.
- 9 new select-CLI integration tests via subprocess covering each
  Day-3 feature end-to-end + v62 class-D coverage-warning regression
  + still-gated coverage_column feature.
- New `make_v62_class_d_fixture` synthesizer for class-D `.anno`
  fixtures (4-sample Loschbour-style v62.0).

HLD test coverage update:
- Test 1 (empty selector) — covered Day 2.
- Test 2 (single-population) — covered Day 2.
- Test 3 (flat AND) — covered Day 3.
- Test 4 (any: OR 3-branch) — covered Day 3.
- Test 5 (exclude: NOT) — covered Day 3.
- Test 6 (nested any: rejected) — covered Day 1 (schema).
- Test 12 (modern_only boundary at 70) — covered Day 3.
- Test 13 (NaN coverage fails threshold) — covered Day 3.

Local CI: 95 tests pass, ruff + format + mypy clean, coverage 93%.

### Deferred to later days

- Day 4: inspect mode; TSV and JSON output formats.
- Day 5: tests 1-14 + 23-27 + 30 (file format edge cases, signature
  canonicalization, etc.).
- Day 6: cross-version (`resolve_to_version:` + `--source-anno`);
  `--coverage-column` / `--coverage-derive` CLI flags.
- Day 7: selector_signature (RFC 8785 JCS).
- Day 8: templates + template subcommand.

### Added (Day 2 — aadr-resolve library integration + basic select)

- `aadr-subset select SELECTOR ANNO [-o OUT]` subcommand end-to-end.
  Day-2 surface supports the simplest predicate path: `populations`
  and/or `individual_ids` matched against a single target `.anno`.
  Output via `--format=ids` (default) writes a newline-delimited
  GeneticID list to stdout or `-o PATH`. Atomic write via tempfile +
  `os.rename` + advisory `fcntl.flock` on `{PATH}.lock` (LOCK_NB; one
  writer wins fast).
- `--schema-override CLASS` flag (A|B|C|D|E) forwarded to
  `AnnoFrame.from_path(schema_override=)` for renamed `.anno` files
  matching an existing class signature.
- `--allow-empty` flag — downgrades zero-match exit 1 to exit 0
  (writes an empty output file; CI sentinel).
- `--allow-empty-source` flag — allows `individual_ids_source:`
  to be empty without raising `SoftValidationFailure`.
- `--include-matched-criteria` flag — opt-in for JSON output's
  matched_criteria field (declared; JSON format lands Day 4).
- `engine.select_samples`: vectorized pandas filter pipeline over
  `populations` and `individual_ids` predicates. Multi-row IIDs
  (multi-library individuals like Loschbour with `.AG` + `.DG`)
  naturally produce multiple GeneticID rows in the output, matching
  HLD §within-version multi-row IIDs are normal.
- Feature gate in engine: Day-3+ selector features (`any:`, `exclude:`,
  `date:`, `modern_only:`, `min_coverage:`, `coverage_column:`,
  `resolve_to_version:`) produce `UsageError` with
  `constraint="feature_not_implemented"` until their day lands. Note:
  validate accepts these at the grammar level since the spec is
  well-formed; engine refuses to execute them.
- `formats.atomic_write` + `formats.write_ids`: ships now since the
  output-atomicity contract should hold from Day 2 forward; the same
  code path will carry TSV/JSON writers on Day 4.
- aadr-resolve library wired in: top-level handler maps
  `aadr_resolve.SchemaDetectionError` to `IOFailure` (exit 2);
  `aadr_resolve.IOFailure` also caught.
- Test fixture synthesizer: `tests/fixtures/synthesize.py` builds
  class-E (.v66.0) `.anno` files on the fly from aadr-resolve's
  shipped `class_E.yaml`. Used by select-CLI integration tests to
  exercise the real `AnnoFrame.from_path` parse path with a known
  6-sample fixture (Loschbour x2 + Bichon + KO1 + English x2).

### Tests (Day 2)

- 31 new tests (74 total): engine unit tests with `FakeAnnoFrame`
  mock, atomic-write concurrency test, select-CLI integration tests
  via subprocess against the synthetic v66 fixture.
- HLD test 1 (empty selector matches all) — covered.
- HLD test 2 (single-population selector) — covered.
- HLD test 3 (flat AND) — implicitly covered via populations + IIDs.
- HLD test 16 (multi-GID per IID handling) — single-version variant
  covered; cross-version variant Day 6.

### Deferred to later days

- Day 3: `any:` and `exclude:` combinators; `modern_only:`, `date:`,
  `min_coverage:` selectors.
- Day 4: inspect mode; TSV and JSON output formats.
- Day 6: cross-version (`resolve_to_version:` + `--source-anno`).
- Day 7: `selector_signature` (RFC 8785 JCS).
- Day 8: templates + template subcommand.

### Added (Day 1 — project skeleton + validate subcommand)

- Project skeleton: `pyproject.toml`, README, LICENSE (MIT), `.gitignore`, `.python-version`.
- Package layout under `src/aadr_subset/`: `__init__.py`, `__main__.py`, `py.typed`, `cli.py`, `types.py`, `errors.py`, `selector.py`, `schemas/selector.schema.json`, `commands/validate_cmd.py`.
- `aadr-subset validate SELECTOR.yaml` subcommand end-to-end: loads selector via `ruamel.yaml` (line/col preserved), validates against the in-package Draft 2020-12 JSON Schema, runs semantic-constraint checks, accumulates all errors in one pass, exits 4 on any violation with `{file}:{line}:{col}: at {pointer}: {message}` per HLD §JSON-schema error message format.
- `aadr-subset --version` reports `aadr-subset {VERSION}` (and `aadr-resolve {VERSION}` once aadr-resolve is wired in on Day 2).
- Selector grammar surface (per HLD §Selector grammar + LLD §2.3): `populations`, `individual_ids`, `individual_ids_source`, `source_version`, `resolve_to_version`, `modern_only`, `min_coverage`, `coverage_column`, `date`, `any`, `exclude`. Deprecated aliases `master_ids` / `master_ids_source` accepted with stderr WARNING + ValidationError captured for JSON capture (used by `select` Day 3+).
- Exception hierarchy in `errors.py`: `AadrSubsetError` base + `SoftValidationFailure` / `IOFailure` / `InvariantViolation` / `UsageError` mapping to exits 1/2/3/4. `ValidationError` value-type for accumulated error reporting.
- GitHub Actions CI workflow: matrix Python 3.11/3.12/3.13 × Ubuntu/macOS. ruff lint + format-check + mypy strict + pytest with coverage gate (90% on src/aadr_subset/ excluding cli.py and __main__.py).

### Tests (Day 1)

- Unit tests for `load_selector`, `validate_schema`, `check_semantic_constraints`, deprecated-alias handling, file format edge cases.
- `aadr-subset validate` integration smoke (subprocess + exit code).

### Deferred to later days

- `aadr-resolve` library imports (Day 2): `AnnoFrame.from_path`, `resolve_master_ids`, `CollisionDetected`.
- `select` / `inspect` / `report` / `template` subcommands (Days 3-8).
- Cross-version resolution flow + `--source-anno` / `--mid-bridge` (Day 6).
- Templates (Day 8).
- PyPI publish workflow (Day 12).
