"""Exception hierarchy + exit-code constants + ValidationError value type.

Per LLD §2.2. Exit-code constants are the stable contract per HLD §Exit codes.
The CLI top-level handler in cli.py catches AadrSubsetError and maps each
subclass to its exit_code; uncaught exceptions exit 70 (BSD EX_SOFTWARE
convention, distinct from the 1-4 user-facing range).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# --- Exit code constants (stable across versions per HLD) ---

EXIT_SUCCESS = 0
EXIT_SOFT_VALIDATION = 1
EXIT_IO_FAILURE = 2
EXIT_INVARIANT_VIOLATION = 3
EXIT_USAGE_ERROR = 4
EXIT_UNEXPECTED = 70  # BSD EX_SOFTWARE; uncaught exception escape hatch


Severity = Literal["ERROR", "WARNING"]


# --- Exception hierarchy ---


class AadrSubsetError(Exception):
    """Base class for tool-internal errors. Subclass per exit-code regime."""

    exit_code: int = EXIT_INVARIANT_VIOLATION  # subclass overrides


class SoftValidationFailure(AadrSubsetError):
    """Exit 1: engine ran but the result isn't shippable."""

    exit_code = EXIT_SOFT_VALIDATION


class IOFailure(AadrSubsetError):
    """Exit 2: I/O or environment failure."""

    exit_code = EXIT_IO_FAILURE


class InvariantViolation(AadrSubsetError):
    """Exit 3: invariant violated by data or runtime environment."""

    exit_code = EXIT_INVARIANT_VIOLATION


class UsageError(AadrSubsetError):
    """Exit 4: bad CLI args, malformed selector YAML, JSON-schema or
    semantic-constraint violation.

    Carries `errors: list[ValidationError]` payload; the CLI handler
    formats one-line-per-error per HLD §JSON-schema error message format.
    """

    exit_code = EXIT_USAGE_ERROR

    def __init__(
        self,
        message: str = "",
        *,
        errors: list[ValidationError] | None = None,
    ):
        super().__init__(message)
        self.errors: list[ValidationError] = errors or []


# --- ValidationError value type ---


@dataclass(frozen=True, slots=True)
class ValidationError:
    """One row of error output from selector validation.

    Format (via .format_line()): `{file}:{line}:{col}: at {pointer}: {message}`
    per HLD §JSON-schema error message format. Lives in errors.py rather than
    types.py because it's tightly coupled to the UsageError payload.
    """

    file: str
    line: int  # 1-indexed
    col: int  # 1-indexed
    pointer: str  # RFC 6901 JSON pointer; "/" for root
    message: str
    severity: Severity = "ERROR"
    constraint: str | None = None  # populated for semantic constraints

    def format_line(self) -> str:
        prefix = f"{self.severity}: " if self.severity != "ERROR" else ""
        suffix = f" — semantic constraint: {self.constraint}" if self.constraint else ""
        return (
            f"{prefix}{self.file}:{self.line}:{self.col}: at {self.pointer}: {self.message}{suffix}"
        )
