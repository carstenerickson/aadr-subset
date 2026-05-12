"""Integration: every shipped template resolves to non-zero matches
against a real AADR v62.0 .anno.

The .anno file is too large to commit (~10 MB) and the AADR data
release notes prohibit redistribution, so this test is gated on the
`AADR_V62_ANNO_PATH` environment variable. CI does not have the file;
this test is therefore skipped in CI and runs only locally when a
contributor has the public release downloaded.

Audit cadence: re-run when bumping `tested_against` in a template or
when adding a new template. This is the test that turns "tested
against v62.0" in template metadata from an aspiration into a fact.
"""

from __future__ import annotations

import os
from pathlib import Path

import aadr_resolve
import pytest

from aadr_subset.engine import select_samples
from aadr_subset.templates import list_templates, load_template

V62_ANNO_PATH_ENV = "AADR_V62_ANNO_PATH"


def _v62_path() -> Path | None:
    raw = os.environ.get(V62_ANNO_PATH_ENV)
    if not raw:
        return None
    p = Path(raw).expanduser()
    return p if p.is_file() else None


@pytest.fixture(scope="module")
def v62_anno() -> aadr_resolve.AnnoFrame:
    p = _v62_path()
    if p is None:
        pytest.skip(f"set {V62_ANNO_PATH_ENV}=/path/to/v62.0_HO_public.anno to run this test")
    return aadr_resolve.AnnoFrame.from_path(p, version_label="v62.0")


@pytest.mark.integration
@pytest.mark.parametrize("template_name", list_templates())
def test_template_resolves_nonzero_against_v62(
    template_name: str, v62_anno: aadr_resolve.AnnoFrame
) -> None:
    """Every shipped template should match ≥1 sample in v62.0.

    v62.0 is class D (no native coverage column), so min_coverage
    filters get routed through the snps_hit_1240k derived proxy via
    --coverage-derive. Any template that fails this test has either:
      (a) wrong Group_ID literals (audit + correct), or
      (b) a date window that excludes all samples for those groups in
          v62.0 (relax the window or split into branches).
    """
    _metadata, selector = load_template(template_name)
    result = select_samples(v62_anno, selector, coverage_column="snps_hit_1240k")
    assert result.n_matched > 0, (
        f"template {template_name!r} matched 0 samples in v62.0; "
        f"verify Group_ID labels + date window. "
        f"Top 3 selector populations: {selector.populations[:3]} "
        f"any_branches: {len(selector.any_branches)}"
    )
