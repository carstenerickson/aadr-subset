"""Group_ID glob expansion tests (v0.2).

Patterns are fnmatch-style (`*`, `?`, `[abc]`); detected by presence of
any of those characters. Plain literals pass through unchanged.

Signature pin: globs are hashed verbatim — the EXPANSION depends on the
.anno being matched against, but the SIGNATURE captures user intent
(the pattern), so the same selector against v62 vs v66 produces the
same signature.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from aadr_subset.engine import _expand_group_id_patterns, select_samples
from aadr_subset.selector import compute_signature, load_selector
from aadr_subset.types import AnyBranch, ExcludeBlock, Selector
from tests.unit.test_engine import make_fake_af


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "aadr_subset", *args],
        capture_output=True,
        text=True,
        check=False,
    )


# --- _expand_group_id_patterns unit ---


def test_expand_plain_literals_pass_through() -> None:
    empties: list[str] = []
    out = _expand_group_id_patterns(
        ["Western_HG", "Eastern_HG"], {"Western_HG", "Eastern_HG", "Modern"}, empties
    )
    assert out == ["Western_HG", "Eastern_HG"]
    assert empties == []


def test_expand_star_matches_prefix() -> None:
    empties: list[str] = []
    out = _expand_group_id_patterns(
        ["England_*"],
        {"England_IA", "England_BellBeaker", "England_Viking", "Scotland_IA"},
        empties,
    )
    # Order: lexicographic among expanded matches.
    assert out == ["England_BellBeaker", "England_IA", "England_Viking"]
    assert empties == []


def test_expand_question_mark_matches_one_char() -> None:
    empties: list[str] = []
    out = _expand_group_id_patterns(["A?C"], {"ABC", "AXC", "AC", "ABBC"}, empties)
    assert sorted(out) == ["ABC", "AXC"]


def test_expand_charclass_matches() -> None:
    empties: list[str] = []
    out = _expand_group_id_patterns(["IA_[12]"], {"IA_1", "IA_2", "IA_3", "IA_x"}, empties)
    assert sorted(out) == ["IA_1", "IA_2"]


def test_expand_pattern_with_no_matches_recorded() -> None:
    empties: list[str] = []
    out = _expand_group_id_patterns(["Egnland_*"], {"England_IA"}, empties)
    assert out == []
    assert empties == ["Egnland_*"]


def test_expand_mixed_pattern_and_literal_dedupes() -> None:
    """A literal that also matches a glob in the same list should
    appear once in the output, in first-appearance order."""
    empties: list[str] = []
    out = _expand_group_id_patterns(
        ["England_IA", "England_*"], {"England_IA", "England_BellBeaker"}, empties
    )
    # England_IA appears first (as a literal), then England_BellBeaker from
    # the glob expansion. England_IA is NOT re-added by the glob.
    assert out == ["England_IA", "England_BellBeaker"]


# --- engine.select_samples with globs ---


def test_engine_glob_in_populations() -> None:
    af = make_fake_af()
    sel = Selector(populations=["Western_*"])
    r = select_samples(af, sel)  # type: ignore[arg-type]
    # Fixture has Western_HG (5 rows); glob matches that group.
    assert r.n_matched == 4
    assert r.per_population_counts == {"Western_HG": 4}


def test_engine_glob_with_no_match_recorded_in_warnings() -> None:
    af = make_fake_af()
    sel = Selector(populations=["Nonexistent_*"])
    r = select_samples(af, sel)  # type: ignore[arg-type]
    assert r.n_matched == 0
    assert r.warnings.empty_glob_patterns == ["Nonexistent_*"]


def test_engine_glob_in_exclude_expanded_per_label() -> None:
    af = make_fake_af()
    # Match all, exclude any group starting with W.
    sel = Selector(exclude=ExcludeBlock(group_ids=["W*"]))
    r = select_samples(af, sel)  # type: ignore[arg-type]
    # The fixture has Western_HG (4 rows after IDs are spread). Excluding
    # W* drops them; Modern + Eastern_HG remain.
    excluded_values = {ec.value for ec in r.excluded_counts}
    # The expanded ExcludeCount reports the concrete label, not the pattern.
    assert "Western_HG" in excluded_values
    assert "W*" not in excluded_values


def test_engine_glob_in_any_branch() -> None:
    af = make_fake_af()
    sel = Selector(any_branches=[AnyBranch(populations=["East*"])])
    r = select_samples(af, sel)  # type: ignore[arg-type]
    # Branch expands to Eastern_HG (1 row in fixture).
    assert r.n_matched == 1
    assert list(r.per_population_counts.keys()) == ["Eastern_HG"]


def test_engine_mixed_glob_and_literal_in_same_key() -> None:
    """populations: [literal, glob*] combines as OR; engine treats both."""
    af = make_fake_af()
    sel = Selector(populations=["Modern", "East*"])
    r = select_samples(af, sel)  # type: ignore[arg-type]
    # Modern (1 row) + Eastern_HG (1 row) = 2 matched.
    assert r.n_matched == 2
    assert set(r.per_population_counts.keys()) == {"Modern", "Eastern_HG"}


# --- Signature semantics: hash the pattern, not the expansion ---


def test_signature_globs_invariant_to_anno_state(tmp_path: Path) -> None:
    """Same selector with a glob produces the same signature regardless
    of what .anno it's evaluated against — the signature captures the
    PATTERN (user intent), not the resolved set (.anno-dependent)."""
    sel_path = tmp_path / "s.yaml"
    sel_path.write_text("populations: [England_*]\n", encoding="utf-8")
    _, selector = load_selector(sel_path)
    sig = compute_signature(selector, cli_coverage_column=None)
    # Sanity: the signature is computed without touching any .anno.
    assert sig.startswith("sha256:")
    # The same selector loaded again produces the same hash.
    _, selector_again = load_selector(sel_path)
    assert compute_signature(selector_again, cli_coverage_column=None) == sig


def test_signature_glob_differs_from_literal(tmp_path: Path) -> None:
    """`populations: [England_*]` and `populations: [England_IA]` produce
    different signatures even when they resolve to the same set against
    a specific .anno (intent differs)."""
    pat = tmp_path / "pat.yaml"
    pat.write_text("populations: [England_*]\n", encoding="utf-8")
    lit = tmp_path / "lit.yaml"
    lit.write_text("populations: [England_IA]\n", encoding="utf-8")
    _, sel_pat = load_selector(pat)
    _, sel_lit = load_selector(lit)
    assert compute_signature(sel_pat, cli_coverage_column=None) != compute_signature(
        sel_lit, cli_coverage_column=None
    )


# --- CLI integration ---


def test_cli_glob_warning_when_empty(tmp_path: Path) -> None:
    """An empty-expansion glob fires a stderr WARNING and the run continues
    (the empty-pattern warning is informational; the cohort-level exit-1
    gate on n_matched==0 may also fire — pass --allow-empty to see just
    the warning)."""
    from tests.fixtures.synthesize import make_loschbour_v66_fixture

    anno = tmp_path / "v66.0.anno"
    make_loschbour_v66_fixture(anno)
    sel = tmp_path / "s.yaml"
    sel.write_text("populations: [Nonexistent_*]\n", encoding="utf-8")
    result = _run_cli("select", str(sel), str(anno), "--allow-empty")
    assert result.returncode == 0, result.stderr
    assert "Nonexistent_*" in result.stderr
    assert "matched zero" in result.stderr


def test_cli_glob_match_real_anno(tmp_path: Path) -> None:
    """Glob against the synthetic v66 fixture matches Western_HG rows."""
    from tests.fixtures.synthesize import make_loschbour_v66_fixture

    anno = tmp_path / "v66.0.anno"
    make_loschbour_v66_fixture(anno)
    sel = tmp_path / "s.yaml"
    sel.write_text("populations: [W*_HG]\n", encoding="utf-8")
    out = tmp_path / "out.ids"
    result = _run_cli("select", str(sel), str(anno), "-o", str(out))
    assert result.returncode == 0, result.stderr
    ids = sorted(out.read_text(encoding="utf-8").strip().splitlines())
    # 3 Western_HG samples in the loschbour fixture (Loschbour.AG/.DG, Bichon).
    assert ids == ["Bichon", "Loschbour.AG", "Loschbour.DG"]


def test_cli_glob_in_json_output_warnings(tmp_path: Path) -> None:
    """empty_glob_patterns surfaces via JSON output warnings field."""
    from tests.fixtures.synthesize import make_loschbour_v66_fixture

    anno = tmp_path / "v66.0.anno"
    make_loschbour_v66_fixture(anno)
    sel = tmp_path / "s.yaml"
    # Mix a matching glob with an empty one so n_matched > 0.
    sel.write_text("populations: [W*_HG, Egnland_*]\n", encoding="utf-8")
    out = tmp_path / "out.json"
    result = _run_cli("select", str(sel), str(anno), "--format", "json", "-o", str(out))
    assert result.returncode == 0, result.stderr
    parsed = json.loads(out.read_text(encoding="utf-8"))
    assert parsed["warnings"]["empty_glob_patterns"] == ["Egnland_*"]
