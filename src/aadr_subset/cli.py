"""click entry point + subcommand routing.

Day 1: `validate` subcommand wired end-to-end. `select` / `inspect` /
`report` / `template` land on Day 3+ per HLD project plan.

Top-level exception handler maps AadrSubsetError subclasses → exit codes
per LLD §3.8 pin. standalone_mode=False prevents click from intercepting
exceptions before our handler runs.
"""

from __future__ import annotations

import sys

import click

from . import __version__
from .commands.validate_cmd import run_validate
from .errors import EXIT_UNEXPECTED, AadrSubsetError


def _version_message() -> str:
    """Build the --version output. aadr-resolve version reported when the
    import succeeds (it will not on Day 1 since aadr-resolve isn't imported
    by validate). Day 2+ will pull aadr_resolve.__version__ here."""
    try:
        import aadr_resolve

        aadr_resolve_v = getattr(aadr_resolve, "__version__", "<unknown>")
        return f"aadr-subset {__version__}\naadr-resolve {aadr_resolve_v}"
    except ImportError:
        return f"aadr-subset {__version__}\naadr-resolve <not installed>"


@click.group(invoke_without_command=False)
@click.version_option(version=__version__, prog_name="aadr-subset", message=_version_message())
@click.option(
    "--quiet",
    is_flag=True,
    help="Suppress stdout summary on success; warnings to stderr; errors to stderr.",
)
@click.pass_context
def cli(ctx: click.Context, quiet: bool) -> None:
    """aadr-subset: declarative AADR panel subsetting from YAML selectors."""
    ctx.ensure_object(dict)
    ctx.obj["quiet"] = quiet


@cli.command("validate")
@click.argument("selector_path", type=click.STRING)
@click.pass_context
def validate_command(ctx: click.Context, selector_path: str) -> None:
    """JSON-schema + semantic-constraint check on a selector YAML. No .anno
    loaded. Useful in CI as a fast gate before any .anno is available."""
    exit_code = run_validate(
        selector_path=selector_path,
        quiet=ctx.obj["quiet"],
    )
    sys.exit(exit_code)


def main() -> None:
    """Top-level entry point. Maps AadrSubsetError subclasses to exit codes;
    uncaught exceptions exit 70 (BSD EX_SOFTWARE)."""
    try:
        cli(standalone_mode=False)
    except click.UsageError as e:
        # click's own usage error (bad arg counts, etc.) → exit 2 by default;
        # we map to 4 to align with HLD §Exit codes (usage error = 4).
        sys.stderr.write(f"Usage error: {e.format_message()}\n")
        sys.exit(4)
    except click.exceptions.Abort:
        # ctrl-C, etc. → exit 130 (conventional SIGINT exit code).
        sys.exit(130)
    except AadrSubsetError as e:
        # Tool-internal errors carry exit_code.
        if str(e):
            sys.stderr.write(f"{e}\n")
        sys.exit(e.exit_code)
    except SystemExit:
        # run_<verb> orchestrators raise SystemExit via sys.exit() — pass
        # through.
        raise
    except Exception:
        # Uncaught exception → exit 70 with traceback to stderr.
        import traceback

        sys.stderr.write("INTERNAL ERROR: uncaught exception (please report):\n")
        traceback.print_exc(file=sys.stderr)
        sys.exit(EXIT_UNEXPECTED)
