# aadr-subset

Declarative AADR panel subsetting from YAML selectors. The missing first-class tool for cohort definitions in ancient-DNA / population-genetics workflows — replaces ad-hoc awk pipelines with declarative, version-stable, PR-reviewable subset definitions.

**Status:** v0.1.0 in active development. See [HLD](https://github.com/carstenerickson/aadr-subset/blob/main/docs/hld.md) and [LLD](https://github.com/carstenerickson/aadr-subset/blob/main/docs/lld.md) for the design contract; this README will expand on Day 11 of the project plan with the canonical-use-cases framing + full CLI reference.

## Day 1 surface

`aadr-subset validate SELECTOR.yaml` — JSON-schema + semantic-constraint check on a selector YAML. No `.anno` load. Useful in CI as a fast gate before any `.anno` is available. Exits 0 on valid; 4 on schema or semantic-constraint violation.

```bash
# Quick check
aadr-subset validate selector.yaml

# Errors include precise file:line:col + JSON pointer:
$ aadr-subset validate broken.yaml
broken.yaml:7:5: at /populations/2: 42 is not of type 'string'
broken.yaml:12:3: at /any/0/min_coverage: -0.5 is less than the minimum of 0
```

`aadr-subset --version` reports the aadr-subset version (and, eventually, aadr-resolve).

## Install (development)

```bash
git clone https://github.com/carstenerickson/aadr-subset.git
cd aadr-subset
pip install -e ".[dev]"
pytest
```

Python 3.11+ required (uses `Literal` types, `match` statements, PEP 604 unions).

## Roadmap

Per HLD project plan:

- **Week 1**: skeleton + selector load/validate + any:/exclude: combinators + inspect + output formats + Day-1 tests
- **Week 2**: cross-version resolution (via aadr-resolve) + report mode + templates + ancestry-pipeline integration tests
- **Buffer**: docs + packaging + PyPI publish + self-dogfood

## License

MIT. See [LICENSE](LICENSE).
