"""Integration: every shipped template resolves to non-zero matches
against real AADR releases.

Runs against v62.0 (class D — needs the snps_hit_1240k derived
coverage proxy) and v66.0 (class E — native coverage column).

The .anno files are too large to commit (~10-15 MB each) and AADR's
release notes prohibit redistribution, so this test is gated on env
vars. CI does not have the files; the tests skip in CI and run only
locally when a contributor has the public releases downloaded.

Audit cadence: re-run when bumping `tested_against` in a template or
when adding a new template. This is the test that turns "tested
against v62.0 / v66.0" in template metadata from an aspiration into a
fact.
"""

from __future__ import annotations

import os
from pathlib import Path

import aadr_resolve
import pytest

from aadr_subset.engine import select_samples
from aadr_subset.templates import list_templates, load_template

# Each AADR release is gated by its own env var so contributors can run
# just the versions they have on disk. The values are absolute paths
# to the `.anno` file for that release.
ANNO_ENV_VARS: dict[str, str] = {
    "v62.0": "AADR_V62_ANNO_PATH",
    "v66.0": "AADR_V66_ANNO_PATH",
}


def _anno_path(version: str) -> Path | None:
    """Resolve the .anno path for a release; None if env var unset or
    points to a missing file."""
    raw = os.environ.get(ANNO_ENV_VARS[version])
    if not raw:
        return None
    p = Path(raw).expanduser()
    return p if p.is_file() else None


# Parametrize over (release, template) pairs so individual failures
# point at the specific cell that broke.
_TEMPLATE_NAMES = list_templates()
_PARAMS = [(v, t) for v in ANNO_ENV_VARS for t in _TEMPLATE_NAMES]


@pytest.mark.integration
@pytest.mark.parametrize(("version", "template_name"), _PARAMS)
def test_template_resolves_nonzero(version: str, template_name: str) -> None:
    """Every shipped template should match ≥1 sample in every release
    in `tested_against`. v62.0 needs the snps_hit_1240k derived coverage
    proxy (class D has no native column); v66.0 uses native coverage.

    Failures mean either:
      (a) wrong Group_ID literals for that release (audit + correct), or
      (b) a date window that excludes all samples for those groups in
          that release (relax the window or split into branches).
    """
    path = _anno_path(version)
    if path is None:
        pytest.skip(f"set {ANNO_ENV_VARS[version]}=/path/to/{version}.anno to run this test")

    metadata, selector = load_template(template_name)

    # Only assert against releases the template explicitly claims to
    # support. A template with tested_against: [v62.0] should not be
    # graded against v66.0 — that's an audit-pending state, not a bug.
    if version not in metadata.tested_against:
        pytest.skip(
            f"template {template_name!r} does not list {version} in "
            f"tested_against (current: {metadata.tested_against}); "
            f"the audit hasn't been run for this version yet"
        )

    anno = aadr_resolve.AnnoFrame.from_path(path, version_label=version)
    # v62.0 is class D (no native coverage); v66.0 is class E (native).
    cov_kwargs: dict[str, str | None] = (
        {"coverage_column": "snps_hit_1240k"} if anno.schema_class.value == "D" else {}
    )
    result = select_samples(anno, selector, **cov_kwargs)  # type: ignore[arg-type]

    assert result.n_matched > 0, (
        f"template {template_name!r} matched 0 samples in {version}; "
        f"verify Group_ID labels + date window. "
        f"Top 3 selector populations: {selector.populations[:3]} "
        f"any_branches: {len(selector.any_branches)}"
    )
