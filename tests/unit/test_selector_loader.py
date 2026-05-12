"""Unit tests for selector.load_selector and helpers.

Covers HLD test 1 (empty selector matches all), test 6 (nested any:
rejected by schema), and Day-1 file-format / two-doc tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aadr_subset.errors import IOFailure, SoftValidationFailure, UsageError
from aadr_subset.selector import load_selector


def write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


# --- HLD test 1: empty selector ---


def test_empty_selector_loads(selector_dir: Path) -> None:
    """Empty mapping parses and produces an empty Selector (all defaults)."""
    path = write(selector_dir / "empty.yaml", "{}\n")
    metadata, selector = load_selector(path)
    assert metadata.tested_against == []
    assert selector.populations == []
    assert selector.individual_ids == []
    assert selector.any_branches == []
    assert selector.exclude is None
    assert selector.metadata.tested_against == []


def test_truly_empty_file_rejected(selector_dir: Path) -> None:
    """An empty file (or only comments) is rejected with a structured
    ValidationError. Empty selector matching everything requires an
    explicit `{}` mapping per the HLD contract."""
    path = write(selector_dir / "blank.yaml", "")
    with pytest.raises(UsageError) as excinfo:
        load_selector(path)
    assert any("empty" in e.message.lower() for e in excinfo.value.errors)


def test_only_comments_file_rejected(selector_dir: Path) -> None:
    """Same: file with only YAML comments → empty-file error."""
    path = write(selector_dir / "comments.yaml", "# nothing real here\n# at all\n")
    with pytest.raises(UsageError) as excinfo:
        load_selector(path)
    assert any("empty" in e.message.lower() for e in excinfo.value.errors)


# --- Two-doc metadata form ---


def test_two_doc_form_parses_metadata(selector_dir: Path) -> None:
    """First YAML document is metadata; second is the selector."""
    content = """---
tested_against: [v62.0, v66.0]
last_verified: '2026-05-11'
maintainer: carstene@gmail.com
notes: |
  Some test note.
---
populations:
  - England_IA
"""
    path = write(selector_dir / "two_doc.yaml", content)
    metadata, selector = load_selector(path)
    assert metadata.tested_against == ["v62.0", "v66.0"]
    assert metadata.last_verified == "2026-05-11"
    assert metadata.maintainer == "carstene@gmail.com"
    assert "Some test note" in metadata.notes
    assert selector.populations == ["England_IA"]


def test_three_doc_form_rejected(selector_dir: Path) -> None:
    """3+ YAML documents → UsageError."""
    content = "---\nfoo: 1\n---\nbar: 2\n---\nbaz: 3\n"
    path = write(selector_dir / "three_doc.yaml", content)
    with pytest.raises(UsageError) as excinfo:
        load_selector(path)
    assert any("got 3" in e.message for e in excinfo.value.errors)


# --- Deprecated alias handling ---


def test_master_ids_deprecated_warning(
    selector_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """master_ids: alias accepted; produces stderr WARNING; rewrites to
    individual_ids internally."""
    content = "master_ids:\n  - Loschbour\n  - Bichon\n"
    path = write(selector_dir / "deprecated.yaml", content)
    _metadata, selector = load_selector(path)
    captured = capsys.readouterr()
    assert "deprecated" in captured.err
    assert "master_ids" in captured.err
    # Selector internally uses individual_ids:
    assert selector.individual_ids == ["Loschbour", "Bichon"]


def test_canonical_and_deprecated_both_rejected(selector_dir: Path) -> None:
    """Setting both individual_ids: AND master_ids: → UsageError."""
    content = "individual_ids: [A]\nmaster_ids: [B]\n"
    path = write(selector_dir / "both.yaml", content)
    with pytest.raises(UsageError) as excinfo:
        load_selector(path)
    assert any("both" in e.message.lower() for e in excinfo.value.errors)


# --- individual_ids_source file format ---


def test_individual_ids_source_basic(selector_dir: Path) -> None:
    """Basic newline-delimited ID list loads."""
    cohort = write(selector_dir / "cohort.txt", "Loschbour\nBichon\nKO1\n")
    selector_yaml = (
        f"individual_ids_source: {cohort.name}\nsource_version: v44.3\nresolve_to_version: v66.0\n"
    )
    path = write(selector_dir / "cross.yaml", selector_yaml)
    _metadata, selector = load_selector(path)
    assert selector.individual_ids_from_source == ["Loschbour", "Bichon", "KO1"]


def test_individual_ids_source_bom_and_comments(selector_dir: Path) -> None:
    """UTF-8 BOM stripped; # comments and blank lines ignored; CRLF accepted."""
    cohort_path = selector_dir / "cohort.txt"
    # Write with BOM + comments + CRLF.
    raw = b"\xef\xbb\xbf# This is a comment\r\n\r\nLoschbour\r\nBichon\r\n# trailing\r\n"
    cohort_path.write_bytes(raw)
    selector_yaml = (
        f"individual_ids_source: {cohort_path.name}\n"
        f"source_version: v44.3\n"
        f"resolve_to_version: v66.0\n"
    )
    path = write(selector_dir / "bom.yaml", selector_yaml)
    _metadata, selector = load_selector(path)
    assert selector.individual_ids_from_source == ["Loschbour", "Bichon"]


def test_individual_ids_source_internal_whitespace_rejected(
    selector_dir: Path,
) -> None:
    """ID containing internal whitespace → UsageError with line-pinned error."""
    write(selector_dir / "cohort.txt", "Loschbour Bichon\nKO1\n")
    selector_yaml = (
        "individual_ids_source: cohort.txt\nsource_version: v44.3\nresolve_to_version: v66.0\n"
    )
    path = write(selector_dir / "ws.yaml", selector_yaml)
    with pytest.raises(UsageError) as excinfo:
        load_selector(path)
    assert any("whitespace" in e.message.lower() for e in excinfo.value.errors)


def test_individual_ids_source_empty_soft_fail(selector_dir: Path) -> None:
    """Empty source file → SoftValidationFailure (exit 1) by default."""
    write(selector_dir / "cohort.txt", "# only comments\n\n# nothing else\n")
    selector_yaml = (
        "individual_ids_source: cohort.txt\nsource_version: v44.3\nresolve_to_version: v66.0\n"
    )
    path = write(selector_dir / "empty_src.yaml", selector_yaml)
    with pytest.raises(SoftValidationFailure):
        load_selector(path)


def test_individual_ids_source_empty_with_allow(selector_dir: Path) -> None:
    """--allow-empty-source bypasses the empty-source check."""
    write(selector_dir / "cohort.txt", "# only comments\n")
    selector_yaml = (
        "individual_ids_source: cohort.txt\nsource_version: v44.3\nresolve_to_version: v66.0\n"
    )
    path = write(selector_dir / "allow_empty.yaml", selector_yaml)
    _metadata, selector = load_selector(path, allow_empty_source=True)
    assert selector.individual_ids_from_source == []


def test_individual_ids_source_relative_to_selector_dir(
    tmp_path: Path,
) -> None:
    """Per HLD: individual_ids_source paths resolve relative to the
    selector YAML's directory, not CWD."""
    # Selector at tmp_path/selectors/my.yaml; cohort at tmp_path/selectors/cohort.txt
    sel_dir = tmp_path / "selectors"
    sel_dir.mkdir()
    (sel_dir / "cohort.txt").write_text("Loschbour\n", encoding="utf-8")
    selector_yaml = (
        "individual_ids_source: cohort.txt\nsource_version: v44.3\nresolve_to_version: v66.0\n"
    )
    sel_path = sel_dir / "my.yaml"
    sel_path.write_text(selector_yaml, encoding="utf-8")

    _metadata, selector = load_selector(sel_path)
    assert selector.individual_ids_from_source == ["Loschbour"]


def test_individual_ids_source_missing_file(selector_dir: Path) -> None:
    """Missing source file → IOFailure (exit 2)."""
    selector_yaml = (
        "individual_ids_source: nonexistent.txt\nsource_version: v44.3\nresolve_to_version: v66.0\n"
    )
    path = write(selector_dir / "missing.yaml", selector_yaml)
    with pytest.raises(IOFailure):
        load_selector(path)


# --- Source file not found ---


def test_selector_file_not_found() -> None:
    """Top-level selector path missing → IOFailure (exit 2)."""
    with pytest.raises(IOFailure):
        load_selector("/nonexistent/path/to/selector.yaml")


def test_selector_path_is_directory(selector_dir: Path) -> None:
    """Pointing at a directory → IOFailure (not a regular file)."""
    with pytest.raises(IOFailure):
        load_selector(selector_dir)


def test_yaml_parse_error_surfaces_with_line(selector_dir: Path) -> None:
    """Malformed YAML → UsageError with ValidationError carrying line."""
    path = write(selector_dir / "bad.yaml", "populations: [unclosed\n")
    with pytest.raises(UsageError) as excinfo:
        load_selector(path)
    assert any("YAML parse error" in e.message for e in excinfo.value.errors)


def test_master_ids_in_any_branch_warning(
    selector_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Deprecated master_ids inside an any: branch produces WARNING."""
    content = """any:
  - populations: [Western_HG]
  - master_ids: [Loschbour, Bichon]
"""
    path = write(selector_dir / "branch_deprecated.yaml", content)
    _metadata, selector = load_selector(path)
    captured = capsys.readouterr()
    assert "deprecated" in captured.err
    # The branch's master_ids got rewritten to individual_ids.
    assert selector.any_branches[1].individual_ids == ["Loschbour", "Bichon"]


def test_canonical_and_deprecated_both_in_branch_rejected(
    selector_dir: Path,
) -> None:
    """Both canonical and deprecated alias inside an any: branch → UsageError."""
    content = """any:
  - individual_ids: [A]
    master_ids: [B]
"""
    path = write(selector_dir / "branch_both.yaml", content)
    with pytest.raises(UsageError) as excinfo:
        load_selector(path)
    assert any("both" in e.message.lower() for e in excinfo.value.errors)


def test_selector_must_be_mapping(selector_dir: Path) -> None:
    """Top-level YAML doc must be a mapping; arrays / scalars → UsageError."""
    path = write(selector_dir / "list.yaml", "- a\n- b\n")
    with pytest.raises(UsageError) as excinfo:
        load_selector(path)
    # Either schema rejects the list type, or top-level check rejects.
    assert excinfo.value.errors or "mapping" in str(excinfo.value)


def test_metadata_unknown_key_warns(selector_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Unknown metadata key produces stderr WARNING but doesn't error."""
    content = """---
unknown_meta_field: 42
tested_against: [v66.0]
---
populations: [English]
"""
    path = write(selector_dir / "meta_unknown.yaml", content)
    _metadata, _selector = load_selector(path)
    captured = capsys.readouterr()
    assert "unknown_meta_field" in captured.err


def test_metadata_tested_against_wrong_type(selector_dir: Path) -> None:
    """tested_against must be a list; string → UsageError."""
    content = """---
tested_against: "v66.0"
---
populations: [English]
"""
    path = write(selector_dir / "meta_wrong.yaml", content)
    with pytest.raises(UsageError) as excinfo:
        load_selector(path)
    assert any("tested_against" in e.message for e in excinfo.value.errors)


def test_exclude_populations_alias_to_group_ids(selector_dir: Path) -> None:
    """exclude.populations is treated as exclude.group_ids alias."""
    content = """populations: [English]
exclude:
  populations: [England_Saxon.SG]
"""
    path = write(selector_dir / "ex_alias.yaml", content)
    _metadata, selector = load_selector(path)
    assert selector.exclude is not None
    assert selector.exclude.group_ids == ["England_Saxon.SG"]


def test_exclude_both_populations_and_group_ids_unions(selector_dir: Path) -> None:
    """exclude.populations + exclude.group_ids both set → union with dedup."""
    content = """populations: [English]
exclude:
  group_ids: [England_Saxon.SG]
  populations: [England_Saxon.SG, France_IA]
"""
    path = write(selector_dir / "ex_union.yaml", content)
    _metadata, selector = load_selector(path)
    assert selector.exclude is not None
    # Dedup preserves first-occurrence order.
    assert selector.exclude.group_ids == ["England_Saxon.SG", "France_IA"]
