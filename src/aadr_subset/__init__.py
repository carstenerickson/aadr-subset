"""aadr-subset: declarative AADR panel subsetting from YAML selectors."""

__version__ = "0.4.0"

from aadr_subset.api import select
from aadr_subset.errors import (
    AadrSubsetError,
    IOFailure,
    InvariantViolation,
    SoftValidationFailure,
    UsageError,
    ValidationError,
)
from aadr_subset.selector import load_selector
from aadr_subset.types import Selector, SelectorMetadata, SamplingSpec, SubsetResult

__all__ = [
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
