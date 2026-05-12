"""Template discovery + load + emit tests (Day 8)."""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pytest

from aadr_subset.errors import IOFailure
from aadr_subset.templates import (
    TEMPLATES_DIR,
    emit_template,
    list_templates,
    load_template,
)


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "aadr_subset", *args],
        capture_output=True,
        text=True,
        check=False,
    )


# --- templates module ---


def test_list_templates_sorted_and_nonempty() -> None:
    names = list_templates()
    assert names == sorted(names)
    # We ship at least these starter templates.
    assert "modern_european" in names
    assert "iron_age_britain" in names


def test_load_template_returns_metadata_and_selector() -> None:
    metadata, selector = load_template("modern_european")
    assert "v66.0" in metadata.tested_against
    assert selector.modern_only is True
    # Populations populated (the literal list is allowed to drift; just
    # assert it has entries so we know parsing populated the dataclass).
    assert len(selector.populations) >= 1


def test_load_template_unknown_raises_with_discovery_hint() -> None:
    with pytest.raises(IOFailure) as exc:
        load_template("not_a_real_template")
    msg = str(exc.value)
    assert "not_a_real_template" in msg
    assert "Available templates:" in msg
    assert "modern_european" in msg


def test_emit_template_is_byte_verbatim(tmp_path: Path) -> None:
    """emit_template writes raw bytes — no YAML round-trip."""
    name = "modern_european"
    src_path = TEMPLATES_DIR / f"{name}.yaml"
    src_content = src_path.read_text(encoding="utf-8")
    buf = io.StringIO()
    emit_template(name, buf)
    assert buf.getvalue() == src_content
    # Comments and the metadata block survive verbatim.
    assert "# Modern European populations" in buf.getvalue()
    assert "tested_against: [v66.0]" in buf.getvalue()


def test_emit_template_unknown_raises() -> None:
    buf = io.StringIO()
    with pytest.raises(IOFailure):
        emit_template("does_not_exist", buf)


# --- CLI integration ---


def test_cli_template_no_arg_lists() -> None:
    """`aadr-subset template` with no argument lists shipped templates."""
    result = _run_cli("template")
    assert result.returncode == 0, result.stderr
    names = [line for line in result.stdout.splitlines() if line]
    assert names == sorted(names)
    assert "modern_european" in names


def test_cli_template_emit_to_stdout() -> None:
    """`aadr-subset template NAME` emits the YAML to stdout."""
    result = _run_cli("template", "modern_european")
    assert result.returncode == 0, result.stderr
    assert "tested_against: [v66.0]" in result.stdout
    assert "modern_only: true" in result.stdout
    # Comment-prefix lines survive (verbatim emit).
    assert "# Modern European populations" in result.stdout


def test_cli_template_emit_to_file(tmp_path: Path) -> None:
    out = tmp_path / "starter.yaml"
    result = _run_cli("template", "iron_age_britain", "-o", str(out))
    assert result.returncode == 0, result.stderr
    body = out.read_text(encoding="utf-8")
    assert "tested_against: [v66.0]" in body
    assert "England.IA" in body


def test_cli_template_unknown_exits_2() -> None:
    result = _run_cli("template", "no_such_template")
    assert result.returncode == 2
    assert "not found" in result.stderr
    assert "Available templates:" in result.stderr


def test_cli_template_help_documents_usage() -> None:
    result = _run_cli("template", "--help")
    assert result.returncode == 0
    assert "shipped selector template" in result.stdout
    assert "NAME" in result.stdout
    assert "-o" in result.stdout


# --- Sanity: every shipped template is valid YAML + JSON-schema-passing ---


@pytest.mark.parametrize("name", list_templates())
def test_every_shipped_template_loads_cleanly(name: str) -> None:
    """Every template under templates/ parses + validates through
    load_selector. This is the gate that catches a malformed addition
    before it ships."""
    metadata, selector = load_template(name)
    # Each template carries metadata and a non-empty selector body.
    assert metadata.tested_against, f"{name}: missing tested_against in metadata"
    has_content = (
        selector.populations
        or selector.individual_ids
        or selector.any_branches
        or selector.modern_only is not None
    )
    assert has_content, f"{name}: selector body parsed to empty"
