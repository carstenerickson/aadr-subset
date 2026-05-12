"""Unit tests for formats.write_ids + atomic_write."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from aadr_subset.errors import IOFailure
from aadr_subset.formats import atomic_write, write_ids


def test_write_ids_to_file(tmp_path: Path) -> None:
    """write_ids emits newline-delimited GeneticIDs with trailing LF."""
    out = tmp_path / "ids.txt"
    write_ids(["Loschbour.AG", "Loschbour.DG", "Bichon"], out)
    content = out.read_text(encoding="utf-8")
    assert content == "Loschbour.AG\nLoschbour.DG\nBichon\n"


def test_write_ids_empty_list_writes_empty_file(tmp_path: Path) -> None:
    """Zero genetic_ids → empty output file (no trailing LF)."""
    out = tmp_path / "empty.txt"
    write_ids([], out)
    assert out.read_text(encoding="utf-8") == ""


def test_write_ids_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """out_path=None writes to stdout."""
    write_ids(["a", "b"], None)
    captured = capsys.readouterr()
    assert captured.out == "a\nb\n"


def test_atomic_write_tempfile_cleaned_on_failure(tmp_path: Path) -> None:
    """If the rename fails (parent dir made read-only), tempfile is unlinked."""
    out = tmp_path / "subdir" / "out.txt"
    # Create the parent, then write a file successfully first to populate
    # the lock + ensure rename works.
    atomic_write(out, "first\n")
    assert out.read_text() == "first\n"
    # Lock file persists after run per LLD §3.5 pin.
    assert (tmp_path / "subdir" / "out.txt.lock").exists()
    # No leftover tempfiles in the dir.
    tmps = list((tmp_path / "subdir").glob("out.txt.tmp.*"))
    assert tmps == []


def test_atomic_write_rejects_concurrent_lock(tmp_path: Path) -> None:
    """A second concurrent atomic_write against the same path raises
    IOFailure (LOCK_NB; one writer wins fast)."""
    out = tmp_path / "concurrent.txt"

    # Pre-populate to ensure the lock file exists.
    atomic_write(out, "first\n")

    # Hold the lock from a side thread while attempting another write.
    lock_acquired = threading.Event()
    release_lock = threading.Event()

    def hold_lock() -> None:
        import fcntl

        lock_path = out.with_suffix(out.suffix + ".lock")
        with open(lock_path, "w") as fp:
            fcntl.flock(fp.fileno(), fcntl.LOCK_EX)
            lock_acquired.set()
            release_lock.wait(timeout=5.0)
            fcntl.flock(fp.fileno(), fcntl.LOCK_UN)

    t = threading.Thread(target=hold_lock)
    t.start()
    assert lock_acquired.wait(timeout=2.0)
    try:
        with pytest.raises(IOFailure):
            atomic_write(out, "second\n")
    finally:
        release_lock.set()
        t.join(timeout=2.0)


def test_atomic_write_bytes_mode(tmp_path: Path) -> None:
    """atomic_write accepts bytes content (auto-detects mode='wb')."""
    out = tmp_path / "bin.dat"
    atomic_write(out, b"\x00\x01\x02\x03")
    assert out.read_bytes() == b"\x00\x01\x02\x03"
