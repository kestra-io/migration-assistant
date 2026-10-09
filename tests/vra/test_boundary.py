"""
The open-core boundary: kestra_migration_assistant stands alone.

The assessment (parse, sanitize, Preflight, Assess, Understand, Brief, the pages, status) is published on its
own. The migration half (tests, conversion, verification) is closed, and lives elsewhere, under the names in
CLOSED. So nothing in kestra_migration_assistant may import the closed half, name it in a dynamic import or a
string, or read the Kestra reference folder that the closed half owns.

The closed half importing kestra_migration_assistant is fine: it extends the command line through
`kestra_migration_assistant.vra.cli.main(extensions=...)`.

And the package runs from wherever it is installed: a copy of it alone, outside this repository, with no
tests and nothing of the closed half next to it, runs an export end to end.
"""
from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PACKAGE = os.path.join(ROOT, "kestra_migration_assistant")
# Every name the closed half has had, as a module or as a repository.
CLOSED = ("vro2k_migrate", "kestra_migration_assistant_ee", "migration-assistant-ee", "migration_assistant_ee",
          "kestra-reference")


def _py_files(top: str):
    for dp, dirs, files in os.walk(top):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(dp, f)


def _violations(path: str) -> list[str]:
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), path)
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bad += [a.name for a in node.names if a.name.split(".")[0] in CLOSED]
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in CLOSED:
                bad.append(node.module)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # importlib.import_module("vro2k_migrate..."), a path into the closed half's reference, or its name
            if any(c in node.value for c in CLOSED):
                bad.append(repr(node.value))
    return bad


def test_the_package_never_imports_or_names_the_closed_half():
    found = {}
    for p in _py_files(PACKAGE):
        v = _violations(p)
        if v:
            found[os.path.relpath(p, ROOT)] = v
    assert not found, f"kestra_migration_assistant reaches into the closed half: {found}"


def test_no_bundled_file_names_the_closed_half():
    """The data and page assets the package ships (YAML, JSON, HTML, CSS) do not name it either."""
    found = []
    for dp, dirs, files in os.walk(PACKAGE):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if f.endswith((".py", ".pyc", ".ttf", ".js")):
                continue
            with open(os.path.join(dp, f), encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            found += [f"{os.path.relpath(os.path.join(dp, f), ROOT)}: {c}" for c in CLOSED if c in text]
    assert not found, found


def test_the_check_catches_a_violation(tmp_path):
    p = tmp_path / "bad.py"
    p.write_text("from vro2k_migrate import convert\nimport kestra_migration_assistant_ee.verify as V\n"
                 "x = __import__('importlib').import_module('kestra_migration_assistant_ee.tests_gen')\n"
                 "y = 'kestra-reference/2.0/20-kestra-2.0-reference.md'\n")
    assert len(_violations(str(p))) == 4


def _package_alone(tmp_path):
    """A copy of the package and nothing else, outside the repository: what an install puts on the path."""
    site = tmp_path / "site"
    shutil.copytree(PACKAGE, site / "kestra_migration_assistant", ignore=shutil.ignore_patterns("__pycache__"))
    return site


def _run(args, cwd, site):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("VRO_", "PYTHON"))}
    env["PYTHONPATH"] = str(site)
    env["VRO_NO_MODEL"] = "1"
    return subprocess.run([sys.executable, "-B", *args], capture_output=True, text=True,
                          cwd=str(cwd), env=env)


def test_every_module_imports_without_the_closed_half(tmp_path):
    site = _package_alone(tmp_path)
    mods = sorted(
        os.path.relpath(p, ROOT)[:-3].replace(os.sep, ".").removesuffix(".__init__")
        for p in _py_files(PACKAGE) if not p.endswith("__main__.py"))   # __main__ runs the command line
    code = ("import importlib, sys\n"
            + "".join(f"importlib.import_module({m!r})\n" for m in mods)
            + f"assert not [m for m in sys.modules if m.split('.')[0] in {CLOSED!r}]\nprint('ok')\n")
    r = _run(["-c", code], tmp_path, site)
    assert r.returncode == 0 and "ok" in r.stdout, r.stderr[-2000:]


def test_the_command_runs_end_to_end_from_the_package_alone(tmp_path):
    """run, then status, on a copy of a conformance estate's export (tests/vra/conformance/review/: a vRO tree
    and a vRA export), from a copy of the package outside the repository."""
    site = _package_alone(tmp_path)
    review = os.path.join(ROOT, "tests", "vra", "conformance", "review")
    export = tmp_path / "work" / "review estate"
    for part in ("vro", "vra"):
        shutil.copytree(os.path.join(review, part), export / part)
    for args in (["-m", "kestra_migration_assistant", "vra", "run", str(export), "--no-model"],
                 ["-m", "kestra_migration_assistant", "vra", "status", str(export)]):
        r = _run(args, tmp_path, site)
        assert r.returncode == 0, (args, r.stdout[-1500:], r.stderr[-2000:])
    out = export / "kestra-assessment"
    assert (out / "index.html").exists() and (out / "your-input" / "answers.yaml").exists()
    # the bundled plugin snapshot was found inside the package: the coverage page names it
    cov = (out / "report" / "plugins.html").read_text(encoding="utf-8")
    assert "kestra-plugins-" in cov and "No plugin snapshot" not in cov
    assert not (tmp_path / "site" / "kestra_migration_assistant" / "kestra-assessment").exists()


def test_the_help_lists_only_the_public_commands(tmp_path):
    site = _package_alone(tmp_path)
    r = _run(["-m", "kestra_migration_assistant", "vra", "--help"], tmp_path, site)
    assert r.returncode == 0, r.stderr
    assert "{run,status,check-intake}" in r.stdout
    for hidden in ("preflight", "understand", "use-sample", "visuals"):
        assert hidden not in r.stdout, hidden
