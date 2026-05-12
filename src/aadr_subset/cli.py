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
from .commands.inspect_cmd import run_inspect
from .commands.report_cmd import run_report
from .commands.select_cmd import run_select
from .commands.validate_cmd import run_validate
from .errors import EXIT_UNEXPECTED, AadrSubsetError, UsageError
from .selector import format_validation_errors


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


@cli.command("select")
@click.argument("selector_path", type=click.STRING)
@click.argument("anno_path", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "-o",
    "--out",
    type=click.Path(dir_okay=False),
    default=None,
    help="Output file path (default: stdout).",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["ids", "tsv", "json"]),
    default="ids",
    show_default=True,
    help="Output format. `ids`=newline-delimited GeneticIDs; `tsv`=TSV with "
    "genetic_id/individual_id/group_id/date_calbp/coverage/matched_criteria; "
    "`json`=structured SubsetResult.",
)
@click.option(
    "--schema-override",
    type=click.Choice(["A", "B", "C", "D", "E"]),
    default=None,
    help="Force AnnoFrame schema class (A-E). Use when .anno is renamed but "
    "matches an existing class signature.",
)
@click.option(
    "--allow-empty",
    is_flag=True,
    help="Downgrade zero-match exit 1 to exit 0 (write an empty output file).",
)
@click.option(
    "--allow-empty-source",
    is_flag=True,
    help="Allow individual_ids_source to be empty (exits 0 instead of 1).",
)
@click.option(
    "--include-matched-criteria",
    is_flag=True,
    help="Include per-sample matched_criteria in JSON output (off by default).",
)
@click.option(
    "--source-anno",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Source .anno for cross-version IID lift. Required when selector sets resolve_to_version.",
)
@click.option(
    "--mid-bridge",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Optional MID-rename bridge TSV (4 cols: v_old_label, mid_old, "
    "v_new_label, mid_new). Layers on top of aadr-resolve's GID-stable "
    "auto-detection.",
)
@click.option(
    "--strict-resolve",
    is_flag=True,
    help="On cross-version resolution, fail exit 1 if any source Individual_ID "
    "fails to resolve. Default: warn to stderr and proceed with the resolvable "
    "subset.",
)
@click.option(
    "--coverage-column",
    default=None,
    metavar="NAME",
    help="Canonical coverage field for min_coverage filters. Routed through "
    "AnnoFrame.coverage_via(NAME). Useful for v62.0 (class D, no native "
    "coverage column) — pass e.g. 'snps_hit_1240k' for a derived proxy. "
    "Selector's coverage_column: takes precedence when both are set.",
)
@click.option(
    "--coverage-derive",
    default=None,
    metavar="NAME",
    help="Alias for --coverage-column (only one of the two may be set). "
    "Mnemonic for the v62-class-D derived-proxy use case.",
)
@click.pass_context
def select_command(
    ctx: click.Context,
    selector_path: str,
    anno_path: str,
    out: str | None,
    fmt: str,
    schema_override: str | None,
    allow_empty: bool,
    allow_empty_source: bool,
    include_matched_criteria: bool,
    source_anno: str | None,
    mid_bridge: str | None,
    strict_resolve: bool,
    coverage_column: str | None,
    coverage_derive: str | None,
) -> None:
    """Materialize a selector against a target AADR .anno; emit sample IDs / TSV / JSON.

    Full HLD select surface: populations + individual_ids + date +
    modern_only + min_coverage + any:/exclude: combinators against a
    single .anno; ids / tsv / json output; cross-version IID lift via
    --source-anno + selector.resolve_to_version.
    """
    exit_code = run_select(
        selector_path=selector_path,
        anno_path=anno_path,
        out=out,
        fmt=fmt,
        schema_override=schema_override,
        allow_empty=allow_empty,
        allow_empty_source=allow_empty_source,
        include_matched_criteria=include_matched_criteria,
        source_anno=source_anno,
        mid_bridge=mid_bridge,
        strict_resolve=strict_resolve,
        coverage_column=coverage_column,
        coverage_derive=coverage_derive,
        quiet=ctx.obj["quiet"],
    )
    sys.exit(exit_code)


@cli.command("inspect")
@click.argument("selector_path", type=click.STRING)
@click.argument("anno_path", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--schema-override",
    type=click.Choice(["A", "B", "C", "D", "E"]),
    default=None,
    help="Force AnnoFrame schema class (A-E).",
)
@click.option(
    "--allow-empty-source",
    is_flag=True,
    help="Allow individual_ids_source to be empty.",
)
@click.option(
    "--strict-resolve",
    is_flag=True,
    help="Show STRICT-RESOLVE diagnostic in the summary when missing-after-"
    "resolve IDs are present. Per HLD §Inspect mode, --strict-resolve is "
    "accepted for diagnostic display but never changes inspect's exit code "
    "(inspect always exits 0).",
)
@click.pass_context
def inspect_command(
    ctx: click.Context,
    selector_path: str,
    anno_path: str,
    schema_override: str | None,
    allow_empty_source: bool,
    strict_resolve: bool,
) -> None:
    """Diagnostic dry-run: shows what a selector matches against a target
    .anno without writing any output. Always exits 0 (informational)."""
    exit_code = run_inspect(
        selector_path=selector_path,
        anno_path=anno_path,
        schema_override=schema_override,
        allow_empty_source=allow_empty_source,
        strict_resolve=strict_resolve,
        quiet=ctx.obj["quiet"],
    )
    sys.exit(exit_code)


@cli.command("report")
@click.argument("selector_path", type=click.STRING)
@click.argument("anno_path", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "-o",
    "--out",
    type=click.Path(dir_okay=False),
    default=None,
    help="Output file path (default: stdout).",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["tsv", "json"]),
    default="tsv",
    show_default=True,
    help="Report format. `tsv`=per-group columns; `json`=structured object.",
)
@click.option(
    "--schema-override",
    type=click.Choice(["A", "B", "C", "D", "E"]),
    default=None,
    help="Force AnnoFrame schema class (A-E).",
)
@click.option(
    "--allow-empty",
    is_flag=True,
    help="Downgrade zero-match exit 1 to exit 0 (write a header-only report).",
)
@click.option(
    "--allow-empty-source",
    is_flag=True,
    help="Allow individual_ids_source to be empty.",
)
@click.option(
    "--include-empty-groups",
    is_flag=True,
    help="Include rows for .anno groups with zero matches (n_matched=0). "
    "Useful for population-survey workflows.",
)
@click.pass_context
def report_command(
    ctx: click.Context,
    selector_path: str,
    anno_path: str,
    out: str | None,
    fmt: str,
    schema_override: str | None,
    allow_empty: bool,
    allow_empty_source: bool,
    include_empty_groups: bool,
) -> None:
    """Per-population aggregate output: group_id, n_matched, n_in_anno,
    pct_matched, date_min/max_calbp, coverage_median (+ JSON adds
    coverage_min/max). Atomic write."""
    exit_code = run_report(
        selector_path=selector_path,
        anno_path=anno_path,
        out=out,
        fmt=fmt,
        schema_override=schema_override,
        allow_empty=allow_empty,
        allow_empty_source=allow_empty_source,
        include_empty_groups=include_empty_groups,
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
    except UsageError as e:
        # UsageError may carry a list[ValidationError] payload (from
        # selector load) or a plain message (from engine feature-gate).
        if e.errors:
            sys.stderr.write(format_validation_errors(e.errors) + "\n")
        elif str(e):
            sys.stderr.write(f"{e}\n")
        sys.exit(e.exit_code)
    except AadrSubsetError as e:
        # Other tool-internal errors carry exit_code.
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
