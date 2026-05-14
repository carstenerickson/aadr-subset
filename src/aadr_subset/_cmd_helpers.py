"""Shared orchestration helpers used by both the CLI (select_cmd.py) and the
library API (api.py).

These are private (_-prefixed) to the package; not part of the public surface
in __init__.py. Extracted so that api.py can call the same validation +
cross-version loading logic without duplicating it.

Functions kept in select_cmd.py (not here):
  _emit_v62_coverage_warning_if_needed — writes to sys.stderr; the library
      api.py uses logging.warning() instead, so emission is caller-side.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import aadr_resolve

from .errors import IOFailure, UsageError, ValidationError

if TYPE_CHECKING:
    from aadr_resolve import AnnoFrame

    from .types import Selector


def normalize_coverage_flags(
    coverage_column: str | None, coverage_derive: str | None
) -> str | None:
    """Merge --coverage-column / --coverage-derive into a single value.

    Per HLD §Coverage handling the two flags are aliases; passing both is
    a usage error so the conflict is surfaced rather than silently
    favoring one.
    """
    if coverage_column is not None and coverage_derive is not None:
        raise UsageError(
            errors=[
                ValidationError(
                    file="<cli>",
                    line=1,
                    col=1,
                    pointer="/--coverage-column",
                    message=(
                        "--coverage-column and --coverage-derive are aliases; pass only one."
                    ),
                )
            ],
        )
    return coverage_column or coverage_derive


def resolve_cross_version_inputs(
    selector: Selector,
    *,
    source_anno: str | None,
    target_anno: AnnoFrame,
    schema_override_enum: aadr_resolve.types.SchemaClass | None,
) -> AnnoFrame | None:
    """Validate cross-version flag/selector combinations + load source .anno
    when both are present. Returns the source AnnoFrame or None.

    Rules:
    - resolve_to_version is None + source_anno is None: single-version path.
    - resolve_to_version is None + source_anno is set: UsageError.
    - resolve_to_version is set + source_anno is None: UsageError.
    - Both set: load source AnnoFrame; verify version match against
      selector.source_version (UsageError on mismatch); verify target
      anno.version matches selector.resolve_to_version (UsageError on
      mismatch).
    """
    if selector.resolve_to_version is None:
        if source_anno is not None:
            raise UsageError(
                errors=[
                    ValidationError(
                        file="<cli>",
                        line=1,
                        col=1,
                        pointer="/--source-anno",
                        message=(
                            "--source-anno is meaningful only with cross-version "
                            "resolution; selector does not set resolve_to_version"
                        ),
                    )
                ],
            )
        return None

    # resolve_to_version is set.
    if source_anno is None:
        raise UsageError(
            errors=[
                ValidationError(
                    file="<selector>",
                    line=1,
                    col=1,
                    pointer="/resolve_to_version",
                    message=(
                        f"selector sets resolve_to_version: "
                        f"{selector.resolve_to_version} but --source-anno was "
                        f"not provided"
                    ),
                )
            ],
        )

    # Load source AnnoFrame.
    try:
        source_af = aadr_resolve.AnnoFrame.from_path(
            source_anno,
            schema_override=schema_override_enum,
        )
    except aadr_resolve.SchemaDetectionError as e:
        raise IOFailure(f"source .anno schema unrecognized: {e}") from e
    except (OSError, aadr_resolve.IOFailure) as e:
        raise IOFailure(f"cannot load source .anno at {source_anno}: {e}") from e

    # Verify source version matches selector.source_version (if set).
    if selector.source_version is not None and source_af.version != selector.source_version:
        raise UsageError(
            errors=[
                ValidationError(
                    file=str(source_anno),
                    line=1,
                    col=1,
                    pointer="/--source-anno",
                    message=(
                        f"--source-anno version is {source_af.version!r} but "
                        f"selector source_version is {selector.source_version!r}"
                    ),
                )
            ],
        )

    # Verify target version matches selector.resolve_to_version.
    if target_anno.version != selector.resolve_to_version:
        raise UsageError(
            errors=[
                ValidationError(
                    file=str(target_anno.path) if target_anno.path else "<target-anno>",
                    line=1,
                    col=1,
                    pointer="/resolve_to_version",
                    message=(
                        f"target .anno version is {target_anno.version!r} but "
                        f"selector resolve_to_version is "
                        f"{selector.resolve_to_version!r}"
                    ),
                )
            ],
        )

    return source_af


def parse_schema_override(value: str | None) -> aadr_resolve.types.SchemaClass | None:
    """Map a CLI --schema-override CLASS letter to aadr_resolve.SchemaClass.
    None passes through (no override).
    """
    if value is None:
        return None
    from aadr_resolve.types import SchemaClass

    try:
        return SchemaClass[value]
    except KeyError as e:
        raise UsageError(
            errors=[
                ValidationError(
                    file="<cli>",
                    line=1,
                    col=1,
                    pointer="/--schema-override",
                    message=(
                        f"unknown schema class '{value}'; expected one of "
                        f"{[c.name for c in SchemaClass]}"
                    ),
                )
            ],
        ) from e
