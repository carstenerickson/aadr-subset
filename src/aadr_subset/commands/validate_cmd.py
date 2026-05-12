"""validate subcommand: JSON-schema + semantic-constraint check on a selector.

Per LLD §3.11 + §4.4. No .anno load; collects ALL errors in one pass
(collect_all_errors=True) so a single CI run surfaces every defect.
"""

from __future__ import annotations

import sys

from ..errors import EXIT_SUCCESS, EXIT_USAGE_ERROR, UsageError
from ..selector import format_validation_errors, load_selector


def run_validate(
    *,
    selector_path: str,
    quiet: bool,
) -> int:
    """Orchestrate `aadr-subset validate`. Returns exit code per HLD §Exit codes.

    Returns 0 (selector parses and passes all semantic constraints) or
    4 (JSON-schema or semantic-constraint violation; details on stderr).

    Other AadrSubsetError subclasses (IOFailure for missing files,
    SoftValidationFailure for empty individual_ids_source) propagate to
    cli.main's top-level handler.
    """
    try:
        load_selector(selector_path, collect_all_errors=True)
    except UsageError as e:
        if e.errors:
            sys.stderr.write(format_validation_errors(e.errors) + "\n")
        else:
            sys.stderr.write(f"{selector_path}: {e}\n")
        return EXIT_USAGE_ERROR

    if not quiet:
        sys.stdout.write(f"OK: {selector_path} parses and passes all semantic constraints.\n")
    return EXIT_SUCCESS
