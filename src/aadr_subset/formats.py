"""Output writers.

Day 2 ships `write_ids` for the default `--format=ids` form. TSV + JSON
writers land on Day 4. The atomic-write helper (tempfile + rename +
fcntl.flock) is wired here from the start since `--format=ids` already
produces user-facing files and the atomicity contract should hold from
Day 2 forward — same code path will serve TSV/JSON later.
"""

from __future__ import annotations

import fcntl
import os
import sys
import tempfile
from pathlib import Path

from .errors import IOFailure


def write_ids(genetic_ids: list[str], out_path: Path | None) -> None:
    """One GeneticID per line, UTF-8, LF terminator (including the last
    line). No header.

    When out_path is None, writes to stdout (no atomicity contract).
    When set, uses atomic_write: tempfile + fsync + os.rename, gated
    by an advisory fcntl.flock on `{out_path}.lock`.
    """
    body = "\n".join(genetic_ids)
    if body:
        body += "\n"
    if out_path is None:
        sys.stdout.write(body)
        sys.stdout.flush()
        return
    atomic_write(out_path, body)


def atomic_write(path: Path, content: bytes | str) -> None:
    """Atomic write via tempfile + os.rename, gated by advisory
    fcntl.flock on `{path}.lock` (LOCK_EX | LOCK_NB).

    Sequence (per LLD §3.5):
    1. Acquire lock on `{path}.lock` (non-blocking; raises IOFailure
       if held by another process).
    2. Write content to a tempfile in path.parent.
    3. fsync the tempfile.
    4. os.rename the tempfile to path (POSIX-atomic).
    5. Release lock.

    Bytes-vs-string mode is auto-detected from content type — no `mode`
    kwarg.
    """
    is_bytes = isinstance(content, bytes)
    mode = "wb" if is_bytes else "w"

    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)

    lock_path = path.with_suffix(path.suffix + ".lock")
    try:
        # Use a context-manager pattern around the lock file. Persisting
        # the .lock file after run is intentional (avoids the create-vs-
        # acquire race; LLD §3.5 pin).
        with open(lock_path, "w") as lock_fp:
            try:
                fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as e:
                raise IOFailure(f"output lock held by another process: {lock_path}") from e
            try:
                tmp_kwargs: dict[str, object] = {
                    "dir": parent,
                    "prefix": path.name + ".tmp.",
                    "delete": False,
                    "mode": mode,
                }
                if not is_bytes:
                    tmp_kwargs["encoding"] = "utf-8"
                tmp = tempfile.NamedTemporaryFile(**tmp_kwargs)  # type: ignore[call-overload]
                tmp_path = Path(tmp.name)
                try:
                    tmp.write(content)
                    tmp.flush()
                    os.fsync(tmp.fileno())
                    tmp.close()
                    os.rename(tmp_path, path)
                except Exception:
                    # Clean up the tempfile on any failure.
                    tmp.close()
                    if tmp_path.exists():
                        tmp_path.unlink()
                    raise
            finally:
                fcntl.flock(lock_fp.fileno(), fcntl.LOCK_UN)
    except OSError as e:
        # Any other I/O error (open lock file, parent dir not writable,
        # rename failure) → IOFailure.
        raise IOFailure(f"failed to write {path}: {e}") from e


__all__ = ["atomic_write", "write_ids"]
