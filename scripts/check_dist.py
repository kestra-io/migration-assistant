"""
Check the built packages before a release: the wheel and the source package hold exactly the package's tracked
files (the bundled data and assets included), and nothing from tests/, scripts/ or an output folder.

    python scripts/check_dist.py dist/ [--version 1.0.0]

Exit 1, listing what is wrong, if either package misses a file or carries one it should not.
"""
from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys
import tarfile
import zipfile

PACKAGE = "kestra_migration_assistant"
# Besides the package itself, the only files the source package may carry
SDIST_TOP = {"PKG-INFO", "pyproject.toml", "setup.cfg", "README.md", "LICENSE", "NOTICE"}
NEVER = ("tests/", "scripts/", "kestra-assessment/", ".github/", "__pycache__/", ".pyc")


def tracked() -> set[str]:
    """The package's files as git tracks them: what both packages must hold."""
    out = subprocess.run(["git", "ls-files", PACKAGE], capture_output=True, text=True, check=True).stdout
    return set(out.split())


def one(dist: str, pattern: str) -> str:
    found = glob.glob(os.path.join(dist, pattern))
    if len(found) != 1:
        raise SystemExit(f"check_dist: expected one {pattern} in {dist}, found {len(found)}")
    return found[0]


def compare(label: str, got: set[str], want: set[str], extra_ok) -> list[str]:
    errors = [f"{label}: missing {p}" for p in sorted(want - got)]
    errors += [f"{label}: should not carry {p}" for p in sorted(got - want) if not extra_ok(p)]
    errors += [f"{label}: should not carry {p}" for p in sorted(got) if any(n in p for n in NEVER)]
    return errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("dist")
    ap.add_argument("--version", help="the version both packages must carry, like 1.0.0")
    a = ap.parse_args(argv)
    want = tracked()
    errors: list[str] = []

    whl = one(a.dist, f"{PACKAGE}-*.whl")
    with zipfile.ZipFile(whl) as z:
        names = {n for n in z.namelist() if not n.endswith("/")}
    info = {n for n in names if n.split("/")[0].endswith(".dist-info")}
    errors += compare(os.path.basename(whl), names - info, want, lambda p: False)

    sdist = one(a.dist, f"{PACKAGE}-*.tar.gz")
    with tarfile.open(sdist) as t:
        members = [m.name for m in t.getmembers() if m.isfile()]
    top = os.path.commonpath(members)
    rel = {os.path.relpath(m, top).replace(os.sep, "/") for m in members}
    errors += compare(os.path.basename(sdist), rel, want,
                      lambda p: p in SDIST_TOP or p.startswith(f"{PACKAGE}.egg-info/"))

    if a.version:
        for path in (whl, sdist):
            if f"{PACKAGE}-{a.version}" not in os.path.basename(path):
                errors.append(f"{os.path.basename(path)}: expected version {a.version}")
    for e in errors:
        print(e, file=sys.stderr)
    if not errors:
        print(f"check_dist: {os.path.basename(whl)} and {os.path.basename(sdist)} hold the package's "
              f"{len(want)} files and nothing else")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
