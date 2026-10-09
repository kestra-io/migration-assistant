"""
The folder the tool writes its results to, and how every reader of an export skips it.

By default the results go to `<export folder>/kestra-assessment/`, inside the folder that is read, so
every walk over an export prunes that folder (and any other output folder `--out` names), and the tool
never reads its own output as input.
"""
from __future__ import annotations

import os

OUTPUT_DIRNAME = "kestra-assessment"
_SKIP: set[str] = set()


def _key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def skip(path: str) -> None:
    """Never read `path` as part of an export (the output folder `--out` names)."""
    _SKIP.add(_key(path))


def skipped(dirpath: str, name: str) -> bool:
    return name == OUTPUT_DIRNAME or _key(os.path.join(dirpath, name)) in _SKIP


def prune(dirpath: str, dirs: list[str]) -> None:
    """For os.walk: drop the output folders from `dirs`, in place."""
    dirs[:] = [d for d in dirs if not skipped(dirpath, d)]
