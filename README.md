# aadr-subset

Declarative AADR panel subsetting from YAML selectors. Replaces ad-hoc
`awk` pipelines and one-off scripts with version-stable,
PR-reviewable cohort definitions. Built on top of
[aadr-resolve](https://github.com/carstenerickson/aadr-resolve) for
cross-AADR-version sample-ID mapping.

```yaml
# britain_iron_age.yaml
populations: [England_IA, England_IA.AG, England_IA.SG]
date: {min_calbp: 1900, max_calbp: 2400}
min_coverage: 0.3
exclude:
  individual_ids: [I12345]   # known contaminated sample
```

```bash
$ aadr-subset select britain_iron_age.yaml v66.HO.aadr.PUB.anno -o cohort.ids
Selector: britain_iron_age.yaml (sha256:1a2b3c4...d5e6f7g)
.anno:    v66.HO.aadr.PUB.anno (v66.0, class E)

Matched 45 samples across 1 populations.

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
- **Five subcommands** cover the full lifecycle: `validate`, `select`,
  `inspect`, `report`, `template`.

## Install

```bash
pip install aadr-subset            # once PyPI'd; currently:
pip install git+https://github.com/carstenerickson/aadr-subset.git
```

Python 3.11+. The only external dependency is `aadr-resolve` (also
installed via git URL until both ship to PyPI).

For development:

```bash
git clone https://github.com/carstenerickson/aadr-subset.git
cd aadr-subset
pip install -e ".[dev]"
pytest
```

## The five subcommands

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

### `select SELECTOR.yaml ANNO.anno [-o PATH] [--format ids|tsv|json]`

The main case: materialize a selector against a target `.anno` and
write matched sample IDs / TSV / JSON.

```bash
aadr-subset select britain_iron_age.yaml v66.HO.aadr.PUB.anno -o cohort.ids
aadr-subset select britain_iron_age.yaml v66.HO.aadr.PUB.anno --format tsv -o cohort.tsv
aadr-subset select britain_iron_age.yaml v66.HO.aadr.PUB.anno --format json -o cohort.json
```

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
| 4 | Usage error (schema violation, flag misuse, unknown template) |
| 70 | Internal error (please file an issue) |

## Selector grammar (overview)

Flat — one level of nesting maximum. Top-level keys AND-combine.

```yaml
# Top-level AND
populations: [Western_HG, Eastern_HG]   # match against group_id
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
  group_ids: [English.SG]
  individual_ids: [I12345]
```

Full spec: [aadr-subset HLD](https://github.com/carstenerickson/aadr-subset/blob/main/docs/hld.md).

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
