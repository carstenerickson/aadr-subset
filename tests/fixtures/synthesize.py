"""Deterministic synthesizer for aadr-resolve .anno test fixtures.

Day 2 ships a class-E (v66.0) synthesizer; Days 3-6 add classes A-D for
cross-version-bridge testing. Output matches aadr-resolve's class_E.yaml
column-order schema so AnnoFrame.from_path can parse it.

Pragmatic: not a full re-implementation of AADR's metadata model — just
the columns aadr-resolve's loader needs to populate AnnoFrame accessors.
Other columns get blank cells. Tests that touch coverage / date / etc.
populate those columns explicitly via SynthRow.
"""

from __future__ import annotations

import importlib.resources
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class SynthRow:
    """One synthetic .anno row. Populate the fields tests care about;
    others default to empty/zero."""

    genetic_id: str
    individual_id: str
    group_id: str
    date_calbp: int = 0
    date_sd_bp: int = 0
    coverage: float | None = None  # class E coverage_1240k_native; None → empty cell
    snps_hit_1240k: int = 0
    persistent_genetic_id: int = 0  # class E only
    sex: str = "U"
    country: str = ""

    # Allow tests to override arbitrary fields by canonical name.
    extra: dict[str, str] = field(default_factory=dict)


def write_class_e_anno(out_path: Path, rows: list[SynthRow]) -> Path:
    """Synthesize a class-E (.v66.0) .anno file at out_path. Returns the path.

    Header line uses each field's `display_header` from class_E.yaml;
    data rows fill `genetic_id`, `persistent_genetic_id`, `individual_id`,
    `group_id`, and the date/coverage columns; unfilled columns are
    blank (empty cells).
    """
    schema_text = (
        importlib.resources.files("aadr_resolve") / "schemas" / "class_E.yaml"
    ).read_text(encoding="utf-8")
    schema: dict[str, Any] = yaml.safe_load(schema_text)

    n_columns: int = schema["n_columns"]
    fields_by_column: dict[int, dict[str, Any]] = {}
    for name, info in schema["fields"].items():
        fields_by_column[info["column"]] = {"name": name, **info}

    # Class E schema uses 1-indexed columns in YAML; column 1 is the first
    # cell. Detection signature uses 0-indexed col_0/col_1 internally. The
    # fields list maps column → field. Columns without a field get an
    # empty cell.
    header_row: list[str] = []
    for col_idx in range(1, n_columns + 1):
        info = fields_by_column.get(col_idx)
        if info is None:
            # Unmapped column → placeholder header.
            header_row.append(f"unmapped_{col_idx}")
        else:
            header_row.append(info["display_header"])

    lines = ["\t".join(header_row)]
    for row in rows:
        cells = [""] * n_columns
        for col_idx in range(1, n_columns + 1):
            info = fields_by_column.get(col_idx)
            if info is None:
                continue
            name = info["name"]
            value = _value_for_field(name, row)
            cells[col_idx - 1] = value
        lines.append("\t".join(cells))

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def _value_for_field(name: str, row: SynthRow) -> str:
    """Map canonical field name → cell content from SynthRow."""
    direct = {
        "genetic_id": row.genetic_id,
        "individual_id": row.individual_id,
        "group_id": row.group_id,
        "persistent_genetic_id": str(row.persistent_genetic_id),
        "date_mean_bp": str(row.date_calbp),
        "date_sd_bp": str(row.date_sd_bp),
        "coverage_1240k": ("" if row.coverage is None else f"{row.coverage:.6f}"),
        "snps_hit_1240k": str(row.snps_hit_1240k),
        "sex": row.sex,
        "country": row.country,
    }
    if name in direct:
        return direct[name]
    return row.extra.get(name, "")


def write_class_d_anno(out_path: Path, rows: list[SynthRow]) -> Path:
    """Synthesize a class-D (v62.0) .anno file. Class D has no native
    coverage column — used by the v62 coverage-warning regression test.

    Detection signature: col_0_normalized=genetic_id,
    col_1_normalized=master_id (NOT individual_id; the v66 rename
    landed at class E).
    """
    schema_text = (
        importlib.resources.files("aadr_resolve") / "schemas" / "class_D.yaml"
    ).read_text(encoding="utf-8")
    schema: dict[str, Any] = yaml.safe_load(schema_text)

    n_columns: int = schema["n_columns"]
    fields_by_column: dict[int, dict[str, Any]] = {}
    for name, info in schema["fields"].items():
        fields_by_column[info["column"]] = {"name": name, **info}

    header_row: list[str] = []
    for col_idx in range(1, n_columns + 1):
        info = fields_by_column.get(col_idx)
        header_row.append(info["display_header"] if info else f"unmapped_{col_idx}")

    lines = ["\t".join(header_row)]
    for row in rows:
        cells = [""] * n_columns
        for col_idx in range(1, n_columns + 1):
            info = fields_by_column.get(col_idx)
            if info is None:
                continue
            name = info["name"]
            cells[col_idx - 1] = _value_for_field(name, row)
        lines.append("\t".join(cells))

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def make_v62_class_d_fixture(out_path: Path) -> Path:
    """4-sample class-D (v62.0) fixture. No coverage column natively —
    designed to exercise the class-D coverage-warning path."""
    rows = [
        SynthRow(
            genetic_id="I0001.AG",
            individual_id="Loschbour",
            group_id="Western_HG",
            date_calbp=8000,
            snps_hit_1240k=1_120_000,
            sex="M",
            country="Luxembourg",
        ),
        SynthRow(
            genetic_id="Loschbour.DG",
            individual_id="Loschbour",
            group_id="Western_HG",
            date_calbp=8000,
            snps_hit_1240k=987_000,
            sex="M",
            country="Luxembourg",
        ),
        SynthRow(
            genetic_id="Bichon",
            individual_id="Bichon",
            group_id="Western_HG",
            date_calbp=13700,
            snps_hit_1240k=965_000,
            sex="M",
            country="Switzerland",
        ),
        SynthRow(
            genetic_id="KO1",
            individual_id="KO1",
            group_id="Eastern_HG",
            date_calbp=7700,
            snps_hit_1240k=1_148_000,
            sex="M",
            country="Hungary",
        ),
    ]
    return write_class_d_anno(out_path, rows)


def make_loschbour_v66_fixture(out_path: Path) -> Path:
    """6-sample class-E fixture: Loschbour x2 + Bichon + KO1 + English x2.

    Used by select_cmd integration tests in Day 2 to exercise the real
    AnnoFrame parse + engine evaluation against aadr-resolve.
    """
    rows = [
        SynthRow(
            genetic_id="Loschbour.AG",
            individual_id="Loschbour",
            group_id="Western_HG",
            date_calbp=8000,
            coverage=1.21,
            snps_hit_1240k=1_142_000,
            persistent_genetic_id=33,
            sex="M",
            country="Luxembourg",
        ),
        SynthRow(
            genetic_id="Loschbour.DG",
            individual_id="Loschbour",
            group_id="Western_HG",
            date_calbp=8000,
            coverage=0.78,
            snps_hit_1240k=987_000,
            persistent_genetic_id=39136,
            sex="M",
            country="Luxembourg",
        ),
        SynthRow(
            genetic_id="Bichon",
            individual_id="Bichon",
            group_id="Western_HG",
            date_calbp=13700,
            coverage=0.82,
            snps_hit_1240k=965_000,
            persistent_genetic_id=34,
            sex="M",
            country="Switzerland",
        ),
        SynthRow(
            genetic_id="KO1",
            individual_id="KO1",
            group_id="Eastern_HG",
            date_calbp=7700,
            coverage=2.40,
            snps_hit_1240k=1_148_000,
            persistent_genetic_id=104,
            sex="M",
            country="Hungary",
        ),
        SynthRow(
            genetic_id="English.1",
            individual_id="Eng1",
            group_id="English.SG",
            date_calbp=70,
            coverage=None,
            snps_hit_1240k=1_233_000,
            persistent_genetic_id=20_001,
            sex="F",
            country="England",
        ),
        SynthRow(
            genetic_id="English.2",
            individual_id="Eng2",
            group_id="English.SG",
            date_calbp=70,
            coverage=None,
            snps_hit_1240k=1_241_000,
            persistent_genetic_id=20_002,
            sex="M",
            country="England",
        ),
    ]
    return write_class_e_anno(out_path, rows)
