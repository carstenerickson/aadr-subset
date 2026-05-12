"""Selector template discovery + load + verbatim emit.

Thin layer over `selector.load_selector` for the `aadr-subset template`
subcommand. Templates ship as package data under `aadr_subset/templates/`;
discovery is by directory listing (no manifest file). See LLD §3.7.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path
from typing import TextIO

from . import selector as selector_mod
from .errors import IOFailure
from .types import Selector, SelectorMetadata

# Resolved at import time. importlib.resources is the portable form;
# Path(__file__).parent would break for zipped wheels.
TEMPLATES_DIR: Path = Path(str(importlib.resources.files("aadr_subset") / "templates"))


def list_templates() -> list[str]:
    """Sorted list of template basenames (no .yaml suffix).

    NAMES ONLY — does NOT parse each template. Callers that want
    metadata iterate this list and call `load_template(name)` per entry
    (one parse per name, on demand).

    `.yaml` extension only — `.yml` is rejected for consistency with the
    rest of the project.
    """
    if not TEMPLATES_DIR.exists():
        return []
    names = sorted(p.stem for p in TEMPLATES_DIR.glob("*.yaml"))
    return names


def load_template(name: str) -> tuple[SelectorMetadata, Selector]:
    """Locate templates/<name>.yaml and parse via selector.load_selector.

    Raises IOFailure when the template doesn't exist; error message
    includes the sorted list of available templates as a discovery aid.
    """
    path = _template_path(name)
    return selector_mod.load_selector(path)


def emit_template(name: str, out: TextIO) -> None:
    """Write templates/<name>.yaml verbatim to `out`.

    Byte-verbatim copy — no YAML round-trip. Preserves comments, document
    separators, and the metadata block exactly as shipped so users who
    save the output and edit it start from the same baseline.

    Raises IOFailure if the template doesn't exist.
    """
    path = _template_path(name)
    out.write(path.read_text(encoding="utf-8"))


def _template_path(name: str) -> Path:
    """Resolve templates/<name>.yaml. Raises IOFailure with a discovery
    hint when the name isn't shipped."""
    candidate = TEMPLATES_DIR / f"{name}.yaml"
    if not candidate.is_file():
        available = list_templates()
        if available:
            hint = "Available templates: " + ", ".join(available)
        else:
            hint = "No templates are currently shipped."
        raise IOFailure(f"template not found: {name!r}. {hint}")
    return candidate


__all__ = ["TEMPLATES_DIR", "emit_template", "list_templates", "load_template"]
