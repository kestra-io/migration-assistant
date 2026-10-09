"""
What a customer sees of the product: its name, the command to run again, every page's footer, and the one call
to action.

The helpers estate is run as a customer runs it (`kestra-migrate vra run <export folder>`, no model), and every
text file it writes is read: none names the internal tool, every HTML page ends with the call to action once and
a footer with the tool version, the model, the run date and the plugin snapshot's date, and BRIEF.md ends the
same way. The command's help names the product.
"""
from __future__ import annotations

import html
import json
import os
import re
import shutil

import pytest

from kestra_migration_assistant import __version__
from kestra_migration_assistant.config import HELP_ASK, HELP_URL
from kestra_migration_assistant.vra.stages import common as C

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HELPERS = os.path.join(ROOT, "tests", "vra", "conformance", "helpers")
PRODUCT = "Kestra Migration Assistant for vRA"
INTERNAL = re.compile(r"vro2k|vro\.py", re.I)


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    from kestra_migration_assistant import cli
    old = os.environ.get("VRO_NO_MODEL")
    os.environ["VRO_NO_MODEL"] = "1"
    try:
        export = tmp_path_factory.mktemp("product") / "acme estate"
        shutil.copytree(os.path.join(HELPERS, "vro"), export / "vro")
        shutil.copytree(os.path.join(HELPERS, "vra"), export / "helpers-vra")
        assert cli.main(["vra", "run", str(export), "--no-model"]) == 0
    finally:
        if old is None:
            os.environ.pop("VRO_NO_MODEL", None)
    out = export / "kestra-assessment"
    files = {}
    for dp, _d, fs in os.walk(out):
        if os.sep + "vendor" in dp or os.sep + "sanitized" in dp:
            continue                         # third-party assets, and the customer's own export
        for f in fs:
            if f.endswith((".html", ".md", ".yaml", ".json", ".csv", ".txt")):
                files[os.path.relpath(os.path.join(dp, f), out)] = open(os.path.join(dp, f), encoding="utf-8").read()
    return {"out": out, "files": files}


def test_no_output_names_the_internal_tool(run):
    assert len(run["files"]) > 30
    named = [p for p, t in run["files"].items() if INTERNAL.search(t)]
    assert named == []


def test_no_output_outside_the_caches_names_this_machine(run):
    """The output folder is zipped and shared: nothing in it says where the tool or the export sat on this machine.
    (The parse cache, data/1-assess/work/, still records absolute paths of the export.)"""
    home = os.path.expanduser("~")
    leaks = [p for p, t in run["files"].items()
             if not p.startswith(os.path.join("data", "1-assess", "work")) and (home in t or "/Users/" in t or "../../../" in t)]
    assert leaks == []
    assess = json.loads(run["files"][os.path.join("data", "1-assess", "assess.json")])
    assert assess["inputs"]["plugin_map"] == "kestra_migration_assistant/vra/data/plugin-map.yaml"


def test_every_page_ends_with_the_call_to_action_and_the_footer(run):
    pages = {p: t for p, t in run["files"].items() if p.endswith(".html")}
    assert len(pages) > 15
    snap = C.run_facts()["snapshot"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", snap)
    for p, t in pages.items():
        foot = t[t.index("<footer"):]
        assert foot.index("</footer>") > 0, p
        assert t.count(html.escape(HELP_URL)) == 1, p                    # one call to action, in the footer
        assert html.escape(HELP_ASK) in foot
        assert f"{PRODUCT} {__version__}" in foot and "model: no model" in foot, p
        assert re.search(r"run on \d{4}-\d{2}-\d{2}", foot) and f"snapshot of {snap}" in foot, p
        if not p.endswith("explorer.html"):                              # a canvas, its footer a panel
            assert foot.rstrip().endswith("</footer>\n</body></html>"), p
            assert f'<span class="product">{PRODUCT}</span>' in t, p     # the title area


def test_brief_md_ends_the_same_way(run):
    md = run["files"][os.path.join("report", "BRIEF.md")]
    tail = md.rstrip().splitlines()[-3:]
    assert tail[0] == f"[{HELP_ASK}]({HELP_URL})"
    assert tail[-1].startswith(f"{PRODUCT} {__version__} · model: no model · run on ")
    assert md.count(HELP_URL) == 1


def test_the_next_step_is_the_real_command(run):
    answers = run["files"][os.path.join("your-input", "answers.yaml")]
    assert answers.splitlines()[0] == "# Answer what's open, then run: kestra-migrate vra run \"acme estate\""


def test_the_help_names_the_product(capsys):
    from kestra_migration_assistant import cli
    for argv in (["--help"], ["vra", "--help"]):
        with pytest.raises(SystemExit):
            cli.main(argv)
        assert PRODUCT in capsys.readouterr().out.replace("\n", " ")


def test_the_call_to_action_is_one_constant():
    """The landing page is a placeholder: its address is in config.HELP_URL and nowhere else in the package."""
    pkg = os.path.join(ROOT, "kestra_migration_assistant")
    found = []
    for dp, _d, fs in os.walk(pkg):
        for f in fs:
            if f.endswith(".py") and "vra-migration" in open(os.path.join(dp, f), encoding="utf-8").read():
                found.append(f)
    assert found == ["config.py"]
