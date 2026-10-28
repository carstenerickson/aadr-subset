"""aadr-subset: declarative AADR panel subsetting from YAML selectors."""

__version__ = "0.4.1"

from aadr_subset.api import select
from aadr_subset.errors import (
    AadrSubsetError,
    InvariantViolation,
    IOFailure,
    SoftValidationFailure,
    UsageError,
    ValidationError,
)
from aadr_subset.selector import load_selector
from aadr_subset.types import SamplingSpec, Selector, SelectorMetadata, SubsetResult

__all__ = [  # noqa: RUF022 -- grouped by purpose, not alphabetized
    "__version__",
    # Primary entry point
    "select",
    "load_selector",
    # Result + selector types
    "SubsetResult",
    "Selector",
    "SelectorMetadata",
    "SamplingSpec",
    # Error hierarchy (for except clauses)
    "AadrSubsetError",
    "IOFailure",
    "InvariantViolation",
    "SoftValidationFailure",
    "UsageError",
    "ValidationError",
]
