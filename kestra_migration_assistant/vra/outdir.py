"""
The folder the tool writes its results to, and how every reader of an export skips it.

By default the results go to `<export folder>/kestra-assessment/`, inside the folder that is read, so
every walk over an export prunes that folder (and any other output folder `--out` names), and the tool
never reads its own output as input. A ZIP that holds a kestra-assessment/ folder (someone zipped the results to
share them, and left the ZIP in the export folder) is the tool's output too, wherever it sits, and is skipped the
same way.
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


def own_output_zip(path: str) -> bool:
    """A ZIP of the tool's output: one of its entries sits in a kestra-assessment/ folder (macOS's __MACOSX/ copies
    aside). Only the ZIP's directory is read, never an entry."""
    import zipfile
    if not path.lower().endswith(".zip") or not os.path.isfile(path) or os.path.islink(path):
        return False
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
    except (OSError, zipfile.BadZipFile, ValueError):
        return False
    for n in names:
        parts = n.replace("\\", "/").split("/")
        if parts[0] != "__MACOSX" and OUTPUT_DIRNAME in parts[:-1]:
            return True
    return False


def skipped(dirpath: str, name: str) -> bool:
    """The tool's own output, never read as part of an export: the output folder (by name, or the one `--out`
    names) and a ZIP of it."""
    p = os.path.join(dirpath, name)
    return name == OUTPUT_DIRNAME or _key(p) in _SKIP or own_output_zip(p)


def prune(dirpath: str, dirs: list[str]) -> None:
    """For os.walk: drop the output folders from `dirs`, in place."""
    dirs[:] = [d for d in dirs if not skipped(dirpath, d)]
