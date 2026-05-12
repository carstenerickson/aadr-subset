"""Shared pytest fixtures for aadr-subset unit + integration tests.

Day 1 fixtures: just selector-YAML scratch directories. AnnoFrame
session-scoped fixtures land on Day 2 when engine.py + aadr-resolve
integration are in scope.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def selector_dir(tmp_path: Path) -> Path:
    """Per-test temp directory for selector + cohort files. Resolves
    relative individual_ids_source paths against this dir."""
    return tmp_path


def write_yaml(path: Path, content: str) -> Path:
    """Helper: write a string to path, return path. Used by tests to
    construct selectors inline."""
    path.write_text(content, encoding="utf-8")
    return path
