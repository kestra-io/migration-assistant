"""
The open-core boundary: vro2k_assess must stand alone.

vro2k_assess (parse, sanitize, Preflight, Assess, Understand, Brief, visuals,
status: everything up to and including the brief) may be shipped without
vro2k_migrate (Tests, Convert, Verify, the test-and-convert contract, the Kestra
reference). So nothing in vro2k_assess may import vro2k_migrate, name it in a
dynamic import, or read the Kestra reference folder that vro2k_migrate owns (apart from its plugins/ snapshot,
which the plugin coverage view reads).
vro2k_migrate importing vro2k_assess is fine.

The same holds for the command line: `plugin/scripts/vro.py` runs every assess
command without importing vro2k_migrate, and works when vro2k_migrate is absent.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSESS = os.path.join(ROOT, "vro2k_assess")
SHIM = os.path.join(ROOT, "plugin", "scripts", "vro.py")


def _py_files(top: str):
    for dp, dirs, files in os.walk(top):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(dp, f)


def _violations(path: str) -> list[str]:
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src, path)
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bad += [a.name for a in node.names if a.name.split(".")[0] == "vro2k_migrate"]
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == "vro2k_migrate":
                bad.append(node.module)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # importlib.import_module("vro2k_migrate...") or a path into its reference
            # kestra-reference/plugins/ is the one shared part: the dated plugin snapshot the
            # coverage view reads. The per-release folders stay migrate's.
            ref = node.value.replace("kestra-reference/plugins", "")
            if node.value.startswith("vro2k_migrate") or "kestra-reference" in ref:
                bad.append(repr(node.value))
    return bad


def test_assess_never_imports_migrate():
    found = {}
    for p in _py_files(ASSESS):
        v = _violations(p)
        if v:
            found[os.path.relpath(p, ROOT)] = v
    assert not found, f"vro2k_assess reaches into vro2k_migrate: {found}"


def test_the_check_catches_a_violation(tmp_path):
    p = tmp_path / "bad.py"
    p.write_text("from vro2k_migrate import convert\nimport vro2k_migrate.verify as V\n"
                 "x = __import__('importlib').import_module('vro2k_migrate.tests_gen')\n")
    assert len(_violations(str(p))) == 3


def test_assess_imports_cleanly_without_migrate_on_the_path(tmp_path):
    """Copy only vro2k_assess somewhere else and import every stage module from there."""
    import shutil
    shutil.copytree(ASSESS, tmp_path / "vro2k_assess",
                    ignore=shutil.ignore_patterns("__pycache__"))
    mods = sorted(
        os.path.relpath(p, ROOT)[:-3].replace(os.sep, ".").removesuffix(".__init__")
        for p in _py_files(ASSESS) if not p.endswith("__main__.py"))   # __main__ runs the command line
    code = ("import sys, importlib\nsys.path.insert(0, %r)\n" % str(tmp_path)
            + "".join(f"importlib.import_module({m!r})\n" for m in mods)
            + "assert 'vro2k_migrate' not in sys.modules\nprint('ok')\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       cwd=str(tmp_path), env={**os.environ, "VRO2K_ROOT": str(tmp_path)})
    assert r.returncode == 0 and "ok" in r.stdout, r.stderr[-2000:]


# ------------------------------------------------------------ the command line

def _assess_only_copy(tmp_path):
    """vro2k_assess, the vro.py shim and the public samples, and nothing of vro2k_migrate."""
    import shutil
    ign = shutil.ignore_patterns("__pycache__")
    shutil.copytree(ASSESS, tmp_path / "vro2k_assess", ignore=ign)
    shutil.copytree(os.path.join(ROOT, "samples"), tmp_path / "samples", ignore=ign)
    (tmp_path / "plugin" / "scripts").mkdir(parents=True)
    shutil.copy(SHIM, tmp_path / "plugin" / "scripts" / "vro.py")
    return tmp_path


def _run(args, cwd, root=None):
    env = {**os.environ, "VRO2K_ROOT": str(root)} if root else dict(os.environ)
    env.pop("VRO_STAGES_DIR", None)
    return subprocess.run([sys.executable, *args], capture_output=True, text=True, cwd=str(cwd), env=env)


def test_shim_imports_migrate_only_inside_a_function():
    """A module-level import would load vro2k_migrate for every command, assess ones included."""
    with open(SHIM, encoding="utf-8") as f:
        tree = ast.parse(f.read(), SHIM)
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] + [getattr(node, "module", None) or ""]
            assert not any(n.split(".")[0] == "vro2k_migrate" for n in names), ast.unparse(node)


# A customer checkout of the open half has no vro2k_migrate/: the halves of these tests that need it
# only run where it is on disk.
HAS_MIGRATE = os.path.isdir(os.path.join(ROOT, "vro2k_migrate"))


def test_no_assess_command_loads_an_extension():
    sys.path.insert(0, os.path.dirname(SHIM))
    try:
        import vro as shim
    finally:
        sys.path.remove(os.path.dirname(SHIM))
    from vro2k_assess import cli
    assert {"understand", "brief", "all", "estates", "check-intake"} <= cli.COMMANDS
    for cmd in cli.COMMANDS:
        assert shim.extensions([cmd, "--estate", "x"]) == (), cmd
    if HAS_MIGRATE:
        assert [m.__name__ for m in shim.extensions(["prototype", "cost"])] == ["vro2k_migrate.commands"]
    else:
        assert shim.extensions(["prototype", "cost"]) == ()


def test_assess_command_in_full_checkout_leaves_migrate_unimported(tmp_path):
    code = ("import sys, runpy\nsys.argv = ['vro.py', 'check-intake', %r]\n"
            "try:\n    runpy.run_path(%r, run_name='__main__')\nexcept SystemExit as e:\n    rc = e.code\n"
            "assert not [m for m in sys.modules if m.startswith('vro2k_migrate')], sorted(sys.modules)\n"
            "print('rc', rc)\n") % (os.path.join(ROOT, "samples", "avi"), SHIM)
    r = _run(["-c", code], ROOT)
    # the sample carries secret-shaped values, so check-intake rejects it (exit 1); what matters is the import
    assert r.returncode == 0 and "rc 1" in r.stdout, r.stderr[-2000:]


def test_migrate_commands_appear_only_when_migrate_is_present(tmp_path):
    full = _run([SHIM, "--help"], ROOT)
    assert full.returncode == 0 and (("prototype" in full.stdout) == HAS_MIGRATE), full.stderr
    iso = _assess_only_copy(tmp_path)
    alone = _run(["plugin/scripts/vro.py", "--help"], iso, root=iso)
    assert alone.returncode == 0, alone.stderr[-2000:]
    assert "prototype" not in alone.stdout and "understand" in alone.stdout
    missing = _run(["plugin/scripts/vro.py", "prototype", "cost"], iso, root=iso)
    assert missing.returncode == 2 and "invalid choice" in missing.stderr


def test_assess_runs_end_to_end_without_migrate(tmp_path):
    """use-sample, then every stage through the brief and visuals, with vro2k_migrate not on disk."""
    iso = _assess_only_copy(tmp_path)
    for args in (["plugin/scripts/vro.py", "use-sample", "avi"],
                 ["plugin/scripts/vro.py", "all", "--estate", "avi-sample", "--no-model"],
                 ["plugin/scripts/vro.py", "understand", "--estate", "avi-sample", "--no-model"],
                 ["plugin/scripts/vro.py", "run", "--estate", "avi-sample", "--no-model"],
                 ["-m", "vro2k_assess", "status", "--estate", "avi-sample"]):
        r = _run(args, iso, root=iso)
        assert r.returncode == 0, (args, r.stderr[-2000:])
        assert "vro2k_migrate" not in r.stderr, (args, r.stderr[-2000:])
    est = iso / "stages" / "avi-sample"
    assert (est / "report" / "BRIEF.md").exists() and (est / "your-input" / "answers.yaml").exists()
    assert (est / "data" / "2-understand" / "PROVENANCE.json").exists()          # seeded from samples/avi/model-output/
    assert (est / "index.html").exists() and (est / "report" / "reports" / "brief.html").exists()
    assert {"index.html", "your-input", "report", "data"} <= {p.name for p in est.iterdir()}
    assert not {"specs", "2-brief", "visuals", "0-preflight", "3-brief", "pages"} & {p.name for p in est.iterdir()}
    assert not (iso / "sources" / "avi-sample" / "model-output").exists()
    assert not (tmp_path / "vro2k_migrate").exists()


def test_an_extension_cannot_shadow_an_assess_command():
    import argparse
    import types

    import pytest

    from vro2k_assess import cli

    def register(sub):
        sub.add_parser("brief-x")
        return {"brief": lambda a: 0}
    with pytest.raises((ValueError, argparse.ArgumentError)):
        cli.main(["estates"], (types.SimpleNamespace(__name__="bad", register=register),))
