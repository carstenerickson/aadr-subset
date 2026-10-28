"""Tests for _cmd_helpers — validation shared by the CLI and select().

test_cross_version.py covers the same rules end-to-end through the CLI, but
in a subprocess where coverage isn't measured. These call the helpers
in-process so every branch is pinned down directly.
"""

from __future__ import annotations

from pathlib import Path

import aadr_resolve
import pytest
from aadr_resolve.types import SchemaClass

from aadr_subset._cmd_helpers import (
    normalize_coverage_flags,
    parse_schema_override,
    resolve_cross_version_inputs,
)
from aadr_subset.errors import IOFailure, UsageError
from aadr_subset.selector import load_selector
from aadr_subset.types import Selector
from tests.fixtures.synthesize import make_loschbour_v66_fixture, make_v62_class_d_fixture

_LIFT = "individual_ids: [Loschbour]\nsource_version: v62.0\nresolve_to_version: v66.0\n"


@pytest.fixture
def v62_v66_pair(tmp_path: Path) -> tuple[Path, aadr_resolve.AnnoFrame]:
    """Source v62.0 (class D) .anno path + pre-loaded v66.0 (class E) target.
    The source filename gives aadr-resolve a clean `v62.0` version label."""
    src = make_v62_class_d_fixture(tmp_path / "v62.0.anno")
    dst = make_loschbour_v66_fixture(tmp_path / "v66.0.anno")
    return src, aadr_resolve.AnnoFrame.from_path(dst, version_label="v66.0")


def _selector(tmp_path: Path, body: str) -> Selector:
    path = tmp_path / "s.yaml"
    path.write_text(body, encoding="utf-8")
    _, selector = load_selector(path)
    return selector


def _pointers(exc: UsageError) -> list[str]:
    return [e.pointer for e in exc.errors]


# --- normalize_coverage_flags ---


@pytest.mark.parametrize(
    ("column", "derive", "expected"),
    [
        (None, None, None),
        ("snps_hit_1240k", None, "snps_hit_1240k"),
        (None, "snps_hit_1240k", "snps_hit_1240k"),
    ],
)
def test_normalize_coverage_flags_accepts_either_alias(
    column: str | None, derive: str | None, expected: str | None
) -> None:
    assert normalize_coverage_flags(column, derive) == expected


def test_normalize_coverage_flags_rejects_both() -> None:
    with pytest.raises(UsageError) as excinfo:
        normalize_coverage_flags("snps_hit_1240k", "snps_hit_1240k")
    assert _pointers(excinfo.value) == ["/--coverage-column"]


# --- parse_schema_override ---


def test_parse_schema_override_none_passes_through() -> None:
    assert parse_schema_override(None) is None


def test_parse_schema_override_maps_class_letter() -> None:
    assert parse_schema_override("D") is SchemaClass.D


def test_parse_schema_override_rejects_unknown_letter() -> None:
    with pytest.raises(UsageError) as excinfo:
        parse_schema_override("Z")
    assert _pointers(excinfo.value) == ["/--schema-override"]
    assert "unknown schema class 'Z'" in excinfo.value.errors[0].message


# --- resolve_cross_version_inputs ---


def test_single_version_selector_needs_no_source_anno(
    tmp_path: Path, v62_v66_pair: tuple[Path, aadr_resolve.AnnoFrame]
) -> None:
    _, target = v62_v66_pair
    selector = _selector(tmp_path, "populations: [Western_HG]\n")
    source = resolve_cross_version_inputs(
        selector, source_anno=None, target_anno=target, schema_override_enum=None
    )
    assert source is None


def test_source_anno_without_resolve_to_version_is_usage_error(
    tmp_path: Path, v62_v66_pair: tuple[Path, aadr_resolve.AnnoFrame]
) -> None:
    src, target = v62_v66_pair
    selector = _selector(tmp_path, "populations: [Western_HG]\n")
    with pytest.raises(UsageError) as excinfo:
        resolve_cross_version_inputs(
            selector, source_anno=str(src), target_anno=target, schema_override_enum=None
        )
    assert _pointers(excinfo.value) == ["/--source-anno"]


def test_resolve_to_version_without_source_anno_is_usage_error(
    tmp_path: Path, v62_v66_pair: tuple[Path, aadr_resolve.AnnoFrame]
) -> None:
    _, target = v62_v66_pair
    with pytest.raises(UsageError) as excinfo:
        resolve_cross_version_inputs(
            _selector(tmp_path, _LIFT),
            source_anno=None,
            target_anno=target,
            schema_override_enum=None,
        )
    assert _pointers(excinfo.value) == ["/resolve_to_version"]


def test_loads_and_returns_source_anno(
    tmp_path: Path, v62_v66_pair: tuple[Path, aadr_resolve.AnnoFrame]
) -> None:
    src, target = v62_v66_pair
    source = resolve_cross_version_inputs(
        _selector(tmp_path, _LIFT),
        source_anno=str(src),
        target_anno=target,
        schema_override_enum=None,
    )
    assert source is not None
    assert source.version == "v62.0"
    assert source.schema_class is SchemaClass.D


def test_unreadable_source_anno_is_io_failure(
    tmp_path: Path, v62_v66_pair: tuple[Path, aadr_resolve.AnnoFrame]
) -> None:
    _, target = v62_v66_pair
    with pytest.raises(IOFailure, match="cannot load source"):
        resolve_cross_version_inputs(
            _selector(tmp_path, _LIFT),
            source_anno=str(tmp_path / "missing.anno"),
            target_anno=target,
            schema_override_enum=None,
        )


def test_unrecognized_source_schema_is_io_failure(
    tmp_path: Path, v62_v66_pair: tuple[Path, aadr_resolve.AnnoFrame]
) -> None:
    _, target = v62_v66_pair
    bad = tmp_path / "bad.anno"
    bad.write_text("foo\tbar\n1\t2\n", encoding="utf-8")
    with pytest.raises(IOFailure, match="schema unrecognized"):
        resolve_cross_version_inputs(
            _selector(tmp_path, _LIFT),
            source_anno=str(bad),
            target_anno=target,
            schema_override_enum=None,
        )


def test_source_version_mismatch_is_usage_error(
    tmp_path: Path, v62_v66_pair: tuple[Path, aadr_resolve.AnnoFrame]
) -> None:
    """Selector says the IDs come from v54.1, but the source .anno is v62.0."""
    src, target = v62_v66_pair
    selector = _selector(
        tmp_path,
        "individual_ids: [Loschbour]\nsource_version: v54.1\nresolve_to_version: v66.0\n",
    )
    with pytest.raises(UsageError) as excinfo:
        resolve_cross_version_inputs(
            selector, source_anno=str(src), target_anno=target, schema_override_enum=None
        )
    assert _pointers(excinfo.value) == ["/--source-anno"]
    assert "'v62.0'" in excinfo.value.errors[0].message


def test_target_version_mismatch_is_usage_error(
    tmp_path: Path, v62_v66_pair: tuple[Path, aadr_resolve.AnnoFrame]
) -> None:
    """Selector resolves to v66.0, but the target passed in is the v62.0 frame."""
    src, _ = v62_v66_pair
    wrong_target = aadr_resolve.AnnoFrame.from_path(src, version_label="v62.0")
    with pytest.raises(UsageError) as excinfo:
        resolve_cross_version_inputs(
            _selector(tmp_path, _LIFT),
            source_anno=str(src),
            target_anno=wrong_target,
            schema_override_enum=None,
        )
    err = excinfo.value.errors[0]
    assert err.pointer == "/resolve_to_version"
    assert err.file.endswith("v62.0.anno")
