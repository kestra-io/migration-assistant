"""
The brief of a run with no model: useful on its own. Everything code can find is shown (what starts each workflow,
what calls what, every system and the Kestra plugin that covers it, the libraries a plugin replaces, near-duplicates
and wrappers, the estate described from the code); no section is empty; nothing says "no spec"; where a verdict needs
a model, one plain line says so; and a short section says what a model adds, with the two ways forward.

Run with `kestra-migrate vra run --no-model` on the synthetic review estate (tests/vra/conformance/review, invented
names), copied without its specs.
"""
from __future__ import annotations

import html
import os
import re
import shutil
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from kestra_migration_assistant.config import HELP_URL

REVIEW = os.path.join(ROOT, "tests", "vra", "conformance", "review")
SECTIONS = ["What's in this estate", "What starts each workflow", "What calls what",
            "Systems and the Kestra plugins that cover them", "Libraries a plugin replaces",
            "Near-duplicates and wrappers", "What a model adds", "Where to check"]


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    from kestra_migration_assistant.vra import cli
    export = tmp_path_factory.mktemp("no-model") / "acme"
    shutil.copytree(os.path.join(REVIEW, "vro"), export / "vro")
    shutil.copytree(os.path.join(REVIEW, "vra"), export / "vra")
    old = os.environ.get("VRO_NO_MODEL")
    try:
        assert cli.main(["run", str(export), "--no-model"]) == 0
    finally:
        if old is None:
            os.environ.pop("VRO_NO_MODEL", None)
    return export / "kestra-assessment"


def _text(path) -> str:
    t = open(path, encoding="utf-8").read()
    t = re.sub(r"(?s)<(script|style|pre)\b.*?</\1>", " ", t)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t)))


def _sections(page: str) -> dict[str, str]:
    """h2 -> the text between it and the next h2 (or the page's end)."""
    body = re.sub(r"(?s)<(script|style)\b.*?</\1>", " ", page)
    parts = re.split(r"<h2>(.*?)</h2>", body)
    return {html.unescape(parts[i]): re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", parts[i + 1]))).strip()
            for i in range(1, len(parts), 2)}


def test_the_front_page_has_every_section_and_none_is_empty(out):
    secs = _sections(open(out / "index.html", encoding="utf-8").read())
    for name in SECTIONS:
        assert name in secs, name
    for name, text in secs.items():
        assert len(text) > 20, f"section {name!r} is empty"
    # the verdicts and the plan need a model: those sections are not there at all
    assert not [s for s in secs if s.startswith(("The recommendation", "The plan"))]


def test_no_page_says_no_spec(out):
    for d, _, files in os.walk(out):
        if os.sep + "data" in d or "vendor" in d:
            continue
        for f in files:
            if f.endswith((".html", ".md", ".yaml")):
                t = _text(os.path.join(d, f)) if f.endswith(".html") else open(os.path.join(d, f)).read()
                assert not re.search(r"no spec|manual \(no spec\)", t, re.I), os.path.join(d, f)


def test_one_plain_line_says_what_needs_a_model_and_two_ways_forward(out):
    t = _text(out / "index.html")
    assert "Next step: run it again with a model." in t
    assert "its possible defects, and whether to convert or rebuild it need a model" in t
    assert "`kestra-migrate vra run acme`" in open(out / "report" / "BRIEF.md").read()
    assert open(out / "index.html", encoding="utf-8").read().count(html.escape(HELP_URL)) == 1     # one call to action
    assert "—" not in t


def test_the_code_found_is_shown_in_full(out):
    secs = _sections(open(out / "index.html", encoding="utf-8").read())
    starts = secs["What starts each workflow"]
    assert "event subscription" in starts or "catalog item" in starts
    assert "calls" in secs["What calls what"]
    systems = secs["Systems and the Kestra plugins that cover them"]
    assert "How Kestra reaches it" in systems and "Infoblox" in systems
    assert "Kestra plugin catalog of" in systems


def test_each_workflow_says_it_needs_a_model_not_manual(out):
    import json
    brief = json.load(open(out / "data" / "3-brief" / "brief.json"))
    assert brief["recommendation"]["verdict"] == "needs a model"
    outcomes = {t["outcome"] for t in brief["triage"].values()}
    assert "Needs a model" in outcomes
    for t in brief["triage"].values():
        assert "no spec" not in t["reason"]
    assert not brief["plan"]["waves"]
    page = _text(next((out / "report" / "workflows").glob("dns-*.html")))
    assert "need a model" in page


def test_in_the_container_the_way_forward_offers_only_what_works_there(tmp_path, monkeypatch):
    """The image sets KESTRA_MIGRATE_CONTAINER=1. A Claude Code login and `kestra-migrate init` don't work there, so
    the page and BRIEF.md offer an API key or an OpenAI-compatible endpoint, with the arguments after the image."""
    from kestra_migration_assistant.config import CONTAINER_ENV
    from kestra_migration_assistant.vra import cli
    monkeypatch.setenv(CONTAINER_ENV, "1")
    monkeypatch.setenv("VRO_NO_MODEL", "1")
    export = tmp_path / "acme"
    shutil.copytree(os.path.join(REVIEW, "vro"), export / "vro")
    assert cli.main(["run", str(export), "--no-model"]) == 0
    out = export / "kestra-assessment"
    page = _sections(open(out / "index.html", encoding="utf-8").read())["What a model adds"]
    brief = (out / "report" / "BRIEF.md").read_text(encoding="utf-8")
    for text in (page, brief):
        assert "docker run" in text and "vra run" in text and "ANTHROPIC_API_KEY" in text
        assert "KESTRA_MIGRATE_PROVIDER=openai-compatible" in text
        assert "Claude Code login" not in text and "kestra-migrate init" not in text and "kestra-migrate vra" not in text
