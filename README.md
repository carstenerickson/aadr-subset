# aadr-subset

Declarative AADR panel subsetting from YAML selectors. Replaces ad-hoc
`awk` pipelines and one-off scripts with version-stable,
PR-reviewable cohort definitions. Built on top of
[aadr-resolve](https://github.com/carstenerickson/aadr-resolve) for
cross-AADR-version sample-ID mapping.

```yaml
# britain_iron_age.yaml
populations: ["England_IA*"]   # matches England_IA + v62 .AG/.SG/.DG variants
date: {min_calbp: 1900, max_calbp: 2400}
min_coverage: 0.3              # 1240k-target coverage (unitless ratio)
exclude:
  individual_ids: [I12345]     # known contaminated sample
```

```bash
$ aadr-subset select britain_iron_age.yaml v66.HO.aadr.PUB.anno -o cohort.ids
Selector: britain_iron_age.yaml (sha256:1a2b3c4...d5e6f7g)
.anno:    v66.HO.aadr.PUB.anno (v66.0, class E)

Matched 45 samples across 1 population.

Per-population: England_IA=45

Wrote cohort.ids (45 lines)
Done in 0.18s (parse 0.16s, eval 0.02s, write 0.00s).

$ plink2 --pfile aadr_v66 --keep cohort.ids --make-pgen --out britain_iron_age
```

## Why it exists

Ancient-DNA workflows live and die on cohort definitions — *which samples
go into this analysis*. Today that's typically a hand-curated set of
Group_ID literals in someone's shell script, prone to: silent breakage
when AADR releases a new version with renamed labels; no version pinning
in commit history; no way to share the exact cohort between collaborators
short of swapping `.ind` files.

`aadr-subset` makes the cohort itself a first-class artifact:

- **Selector YAMLs are version-stable.** They cite AADR releases via
  `tested_against:` metadata; the `selector_signature` (RFC 8785 JCS
  SHA-256 over the canonical form) gives you a hash that survives
  YAML formatting churn.
- **Reviewable in PRs.** The grammar is flat (top-level AND with
  one-level `any:` OR and one-level `exclude:` NOT). What you see is
  what runs.
- **Cross-version via `aadr-resolve`.** `resolve_to_version:` lifts
  Individual_IDs from an older release to the newer one through the
  GID-stable bridge + MID-rename map.
- **Six subcommands** cover the full lifecycle: `validate`, `select`,
  `inspect`, `report`, `diff`, `template`.

## Install

```bash
pip install aadr-subset
```

Python 3.11+. The only external dependency is
[aadr-resolve](https://github.com/carstenerickson/aadr-resolve),
pulled in automatically.

For development setup see [CONTRIBUTING.md](CONTRIBUTING.md).

## The six subcommands

### `validate SELECTOR.yaml`

JSON-schema + semantic-constraint check on a selector. No `.anno`
required. Useful as a CI gate.

```bash
$ aadr-subset validate britain_iron_age.yaml
# exit 0 on valid; exit 4 on schema or semantic violation
# Errors carry precise file:line:col + JSON pointer:
$ aadr-subset validate broken.yaml
broken.yaml:7:5: at /populations/2: 42 is not of type 'string'
broken.yaml:12:3: at /any/0/min_coverage: -0.5 is less than the minimum of 0
```

### `select SELECTOR.yaml ANNO.anno [ANNO2.anno …] [-o PATH] [--format ids|tsv|json]`

The main case: materialize a selector against a target `.anno` and
write matched sample IDs / TSV / JSON.

```bash
aadr-subset select britain_iron_age.yaml v66.HO.aadr.PUB.anno -o cohort.ids
aadr-subset select britain_iron_age.yaml v66.HO.aadr.PUB.anno --format tsv -o cohort.tsv
aadr-subset select britain_iron_age.yaml v66.HO.aadr.PUB.anno --format json -o cohort.json
```

Stratified sampling caps can be specified inline on the CLI (or in the
selector YAML — selector wins per-field):

```bash
# cap to 1 library per individual (dedup .AG/.SG/.DG triplicates),
# then at most 50 samples per population
aadr-subset select europe_neolithic.yaml v66.HO.aadr.PUB.anno \
    --max-per-individual 1 --max-per-population 50 -o cohort.ids
```

**Multi-anno select (v0.4+)**: pass more than one `.anno` to merge
cohorts across releases in a single command. The selector is evaluated
against each `.anno` independently; results are union-deduplicated on
`genetic_id` (newer-version rows win on collision):

```bash
aadr-subset select britain_iron_age.yaml v44.3_HO.anno v66.HO.aadr.PUB.anno \
    -o cohort.ids
```

TSV output gains a `source_version` column identifying which `.anno`
each row came from. JSON output gains `anno_versions`, `anno_files`, and
`per_anno_n_matched` keys alongside the single-anno fields for
backwards compatibility. `resolve_to_version:` selectors are
incompatible with multi-anno mode (hard error — use single-anno +
`--source-anno` for cross-version IID lifting instead).

Cross-version flow (selector defined against an older release than the
materialized one):

```yaml
# britain_v62_lift.yaml
individual_ids: [I12345, I12346]
source_version: v62.0
resolve_to_version: v66.0
```

```bash
aadr-subset select britain_v62_lift.yaml v66.HO.aadr.PUB.anno \
    --source-anno v62.0_HO_public.anno \
    -o lifted.ids
```

The positional `.anno` is the **target** (where the lifted cohort
materializes); `--source-anno` is the **source** (where the selector's
Individual_IDs are originally defined). `aadr-resolve` bridges the two.

v62.0 inputs (class D — no native coverage column) need a derived proxy
for `min_coverage:` filters:

```bash
aadr-subset select britain_iron_age.yaml v62.0_HO_public.anno \
    --coverage-derive snps_hit_1240k -o cohort.ids
```

### `inspect SELECTOR.yaml ANNO.anno`

Dry-run: shows what a selector matches without writing any file.
Always exits 0 — meant for debugging selector logic.

```
$ aadr-subset inspect britain_iron_age.yaml v66.HO.aadr.PUB.anno
Selector: britain_iron_age.yaml
.anno:    v66.HO.aadr.PUB.anno (v66.0, class E, 27,755 samples)

Matched: 45 samples across 1 population

Per-population breakdown:
  England_IA  45

Branch contributions:
  top_level  45

Date range of matched: 1934 - 2398 calBP (median 2103)
Coverage range:        0.34 - 4.81x (median 1.28)

Selector signature: sha256:1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p7q8r9s0t1u2v3w4x5y6z7a8b9c0d1e
```

When sampling caps are active a "Downsampled" section appears (v0.3+).
Per-population entries are listed individually; per-individual drops
are aggregated (they can run into the thousands; the JSON output
preserves per-IID detail for callers that need it):

```
$ aadr-subset inspect europe_neolithic.yaml v66.HO.aadr.PUB.anno \
      --max-per-individual 1 --max-per-population 50
...
Matched: 312 samples across 8 populations

Downsampled:
  Anatolia_N  12 samples dropped
  Iran_N       8 samples dropped
  per-individual aggregate: 177 samples dropped across 177 individual(s)
```

### `report SELECTOR.yaml ANNO.anno [-o PATH] [--format tsv|json]`

Per-population aggregates: how many samples each Group_ID contributed,
with date range and coverage stats.

```
$ aadr-subset report britain_iron_age.yaml v66.HO.aadr.PUB.anno
group_id    n_matched   n_in_anno   pct_matched   date_min_calbp   date_max_calbp   coverage_median
England_IA  45          51          88.2          1934             2398             1.28
```

`--include-empty-groups` adds rows for `.anno` groups that matched
zero samples (useful for population-survey workflows).

### `diff SELECTOR_A.yaml SELECTOR_B.yaml ANNO.anno [-o PATH] [--format human|json]`

Set-difference of two selectors against the same `.anno`: which samples
does A match that B doesn't, and vice versa, plus a per-population
delta. Always exits 0 — diagnostic, not a gate. Useful for PR review
of selector changes.

```
$ aadr-subset diff old.yaml new.yaml v66.HO.aadr.PUB.anno
Selector A: old.yaml (sha256:1a2b3c4...d5e6f7g)
Selector B: new.yaml (sha256:9z8y7x6...w5v4u3t)
.anno:      v66.HO.aadr.PUB.anno (v66.0, class E)

A only: 5 samples
B only: 12 samples
Both:   38 samples

Per-population delta:
  group_id             A   B  delta
  England_IA          43  40     -3
  England_IA-o         0  10    +10
  England_BellBeaker   0   2     +2

A only sample preview: ['I12345', 'I12346', 'I12347', 'I12348', 'I12349']
B only sample preview: ['I20001', 'I20002', ...] (+2 more)
```

`--format json -o diff.json` writes a structured object with
`a_only[]`, `b_only[]`, `both[]`, `per_population_delta[]` arrays plus
both signatures — suitable for pipeline integration / dashboards.

### `template [NAME] [-o PATH]`

Ships starter selectors for common cohorts. No-arg form lists
shipped templates; arg form emits the verbatim YAML (comments + metadata
block preserved) to stdout or `--out PATH`.

```
$ aadr-subset template
bronze_age_europe
iron_age_britain
modern_european
neolithic_anatolia
viking_period_scandinavian
wsh_steppe_pool

$ aadr-subset template iron_age_britain -o britain.yaml
# britain.yaml now contains a working starting point — edit + extend.
```

All shipped templates are verified against AADR **v62.0** and **v66.0** —
each template's `tested_against:` metadata reflects the releases it
resolves to non-zero matches against.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Soft validation failure (e.g. zero-match without `--allow-empty`, `--strict-resolve` missing IIDs) |
| 2 | I/O failure (file not found, `.anno` schema unrecognized, etc.) |
| 3 | Invariant violation (internal consistency check failed — please file an issue) |
| 4 | Usage error (schema violation, flag misuse, unknown template) |
| 70 | Internal error (uncaught exception escape hatch — please file an issue) |

## Selector grammar (overview)

Flat — one level of nesting maximum. Top-level keys AND-combine.

```yaml
# Top-level AND
populations: [Western_HG, "England_*"]   # group_id literals + fnmatch globs (v0.2+)
individual_ids: [Loschbour, KO1]         # match against individual_id
individual_ids_source: ids.txt           # newline-delimited file
modern_only: true                        # shorthand: date_calbp <= 70
min_coverage: 0.3
coverage_column: snps_hit_1240k          # override; selector-side wins over --coverage-derive
date:
  min_calbp: 1900
  max_calbp: 2400
source_version: v62.0                    # cross-version lift
resolve_to_version: v66.0

# One-level OR (matches any branch)
any:
  - populations: [Western_HG]
    min_coverage: 1.0
  - populations: [Eastern_HG]
    min_coverage: 0.5

# One-level NOT-of-OR (drops matches)
exclude:
  group_ids: [English.SG, "*_o.SG"]      # literals + globs
  individual_ids: [I12345]

# Stratified sampling caps (v0.3+; applied after exclude, before dedup)
sampling:
  max_per_population: 50                 # cap per group_id (integer ≥ 1)
  max_per_individual: 1                  # cap per individual_id (1 = pick best library)
  policy: top_coverage                   # default; v0.3 ships only this
```

**Group_ID globs (v0.2+)**: any string containing `*`, `?`, or `[abc]`
is treated as an fnmatch pattern against the target `.anno`'s
Group_IDs. Patterns work in `populations:`, `exclude.group_ids:`, and
any-branch `populations:`. The selector signature hashes the **pattern**,
not the resolved set — so the same selector against v62 vs v66 produces
the same signature even when the pattern resolves to different concrete
labels. A pattern that matches zero Group_IDs surfaces as a warning
(CLI: stderr; library API: `logging.getLogger("aadr_subset")`) — likely
a typo.

**Stratified sampling (v0.3+)**: `sampling.max_per_population` /
`max_per_individual` cap the cohort within each Group_ID / Individual_ID.
Per-individual fires first, then per-population — `max_per_individual: 1`
is the canonical "one library per individual" dedup; combined with a
per-population cap it picks the cap-many distinct individuals with the
highest coverage. `--max-per-population N` / `--max-per-individual N`
CLI flags also work; selector wins per-field. Both feed the signature
(intent-not-expansion — same caps against v62 vs v66 = same hash). On
class-D inputs (v62.0, no native coverage column), sampling requires
`--coverage-derive snps_hit_1240k` for priority — without it, sampling
errors out (the engine refuses to "prioritize" against an undefined
coverage column).

For the full grammar reference see the [JSON
Schema](src/aadr_subset/schemas/selector.schema.json); for grammar
semantics and edge cases (NaN handling, branch independence, signature
canonicalization) see the inline schema `description:` fields and the
[CHANGELOG](CHANGELOG.md).

## Library API (v0.4+)

`aadr-subset` exposes a Python API for programmatic use — no subprocess
required:

```python
from aadr_subset import select, SubsetResult

result: SubsetResult = select("britain_iron_age.yaml", "v66.HO.aadr.PUB.anno")
print(result.genetic_ids)      # list[str] of matched sample IDs
print(result.n_matched)        # int
print(result.anno_version)     # e.g. "v66.0"
print(result.selector_signature)  # "sha256:..."
```

Full signature:

```python
from pathlib import Path
from aadr_subset import select, load_selector
import aadr_resolve

result = select(
    selector,             # str | Path | Selector — YAML path or pre-loaded object
    anno,                 # str | Path | AnnoFrame — .anno path or pre-loaded object
    *,
    allow_empty=True,     # True by default (differs from CLI default of False)
    allow_empty_source=False,
    include_matched_criteria=False,
    source_anno=None,     # str | Path | AnnoFrame — cross-version source
    mid_bridge=None,      # str | Path — MID-rename bridge for multi-anno dedup
    strict_resolve=False,
    coverage_column=None, # str — override coverage column
    coverage_derive=None, # str — derive proxy for class-D inputs
    max_per_population=None,   # int
    max_per_individual=None,   # int
    schema_override=None,      # str — force schema class detection
    quiet=False,
)
```

Key differences from the CLI:

- **`allow_empty=True`** by default — the API doesn't abort on
  zero matches; callers check `result.n_matched` themselves.
- **Warnings via `logging`** — the `"aadr_subset"` logger emits
  warnings (zero-match globs, v62 coverage proxy) instead of writing
  to stderr. Configure with `logging.getLogger("aadr_subset")`.
- **Pre-loaded objects accepted** — pass a `Selector` or `AnnoFrame`
  directly to skip repeated file I/O in loops.

Public symbols exported from `aadr_subset`:
`select`, `load_selector`, `SubsetResult`, `Selector`, `SelectorMetadata`,
`SamplingSpec`, `AadrSubsetError`, `IOFailure`, `InvariantViolation`,
`SoftValidationFailure`, `UsageError`, `ValidationError`.

## Composing with `plink2`

```bash
# Materialize a cohort
aadr-subset select britain_iron_age.yaml v66.HO.aadr.PUB.anno -o cohort.ids

# Use it as a plink2 keep set
plink2 --pfile aadr_v66 \
       --keep cohort.ids \
       --make-pgen --out britain_iron_age_subset
```

`select --format json` produces a structured artifact suitable for
pipeline metadata logging (records the selector signature, AADR version,
schema class, and effective coverage column).

## License

MIT. See [LICENSE](LICENSE).
