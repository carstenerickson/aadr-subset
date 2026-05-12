"""template subcommand orchestrator.

Two modes (LLD §3.12):
- list mode (name is None): print shipped template names to stdout, one
  per line, sorted lexicographically. Always exits 0 even if zero
  templates ship.
- emit mode (name set): write the template's verbatim YAML to stdout or
  --out PATH (atomic write when out is set).

No `.anno` involved either way; templates are starter selectors, not
materialized cohorts.
"""

from __future__ import annotations

import sys
from pathlib import Path

from ..errors import EXIT_SUCCESS
from ..formats import atomic_write
from ..templates import emit_template, list_templates


def run_template(*, name: str | None, out: str | None, quiet: bool) -> int:
    """Orchestrate `aadr-subset template`.

    list mode: name=None. Prints sorted names to stdout (one per line).
    Returns EXIT_SUCCESS.

    emit mode: name=<n>. Writes the template content to stdout or to
    --out PATH (atomic_write). Returns EXIT_SUCCESS. Unknown name →
    IOFailure (exit 2) raised by templates._template_path.

    `quiet` has no effect — template's output IS the listing / emitted
    YAML; there is no stdout summary to suppress.
    """
    _ = quiet  # accepted for signature uniformity; nothing to suppress.

    if name is None:
        names = list_templates()
        if names:
            sys.stdout.write("\n".join(names) + "\n")
            sys.stdout.flush()
        return EXIT_SUCCESS

    if out is None:
        emit_template(name, sys.stdout)
        sys.stdout.flush()
        return EXIT_SUCCESS

    # Capture into a string and atomic-write so the on-disk file appears
    # all-or-nothing.
    import io

    buf = io.StringIO()
    emit_template(name, buf)
    atomic_write(Path(out), buf.getvalue())
    return EXIT_SUCCESS
