"""
The writing rules, on every customer-facing file: the pages (index.html and everything under report/, the rendered
reports included), BRIEF.md and the comments of your-input/answers.yaml.

The words follow the vRA migration guide: a wave starts with its first of a kind, never a "pilot"; elements
convert Direct or need a Redesign, never "mechanical" or "designed" work; no "prototype"; no em dash; and nothing
points at Kestra's own files under internal/. On the front page, no Kestra task class name and no answers.yaml key
appears in the running text.

Run with `vro.py run --no-model` on three synthetic estates (every name in them is invented): tests/vra/conformance/
review (vRA replaced, with specs and suspected defects), tests/vra/conformance/helpers (no specs) and a 12-workflow
estate from tests/vra/conformance/large.
"""
from __future__ import annotations

import glob
import html
import os
import re
import shutil
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "vra", "conformance", "large"))

RULES = {
    "pilot": re.compile(r"\bpilots?\b", re.I),
    "mechanical": re.compile(r"\bmechanical", re.I),
    # "designed" as a class of work; the verb ("designed for", "designed by") is fine
    "designed as a class": re.compile(r"\bdesigned\b(?!\s+(for|by|to|as|in)\b)", re.I),
    "prototype": re.compile(r"\bprototype", re.I),
    "em dash": re.compile("—"),
    "internal/": re.compile(r"\binternal/"),
    "Aria": re.compile(r"\bAria (Automation|Orchestrator)\b"),
}


def _text(path: str) -> str:
    t = open(path, encoding="utf-8").read()
    if path.endswith(".html"):
        t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", t, flags=re.S)
        t = re.sub(r"<[^>]+>", " ", t)
        t = html.unescape(t)
    elif path.endswith(".yaml"):
        t = "\n".join(x for x in t.splitlines() if x.lstrip().startswith("#"))
    return t


def _files(est: str) -> list[str]:
    out = [os.path.join(est, "index.html"), os.path.join(est, "report", "BRIEF.md"),
           os.path.join(est, "your-input", "answers.yaml")]
    out += [p for p in glob.glob(os.path.join(est, "report", "**", "*.html"), recursive=True)
            if os.sep + "vendor" + os.sep not in p]
    return [p for p in out if os.path.exists(p)]


def _run(tmp, name, copy, specs=None, answer=None, write_specs=None):
    from kestra_migration_assistant.vra import cli
    from kestra_migration_assistant.vra.stages import common as C
    src = tmp / "sources" / name
    for s, d in copy:
        shutil.copytree(s, src / d)
    stages = tmp / "stages" / name
    if specs:
        os.makedirs(stages / "data" / "2-understand", exist_ok=True)
        for f in glob.glob(os.path.join(specs, "*.json")):
            shutil.copy(f, stages / "data" / "2-understand")
    mp = pytest.MonkeyPatch()
    mp.setenv("VRO_NO_MODEL", "1")
    try:
        assert cli.main(["run", str(src), "--out", str(stages), "--no-model"]) == 0
        if write_specs:
            a = C.read_json(str(stages / "data" / "1-assess" / "assess.json"))
            write_specs(str(stages / "data" / "2-understand"), {u: w["name"] for u, w in a["workflows"].items()})
            assert cli.main(["run", str(src), "--out", str(stages), "--no-model"]) == 0
        if answer:
            from kestra_migration_assistant.vra.stages import your_input as Y
            for k, v in answer.items():
                Y.set_answer(k, v)
            assert cli.main(["run", str(src), "--out", str(stages), "--no-model"]) == 0
    finally:
        mp.undo()
    return str(stages)


@pytest.fixture(scope="module")
def estates(tmp_path_factory):
    review = os.path.join(ROOT, "tests", "vra", "conformance", "review")
    helpers = os.path.join(ROOT, "tests", "vra", "conformance", "helpers")
    out = {"review": _run(tmp_path_factory.mktemp("w-review"), "rev",
                          [(os.path.join(review, "vro"), "review"), (os.path.join(review, "vra"), "review-vra")],
                          specs=os.path.join(review, "specs"), answer={"q9-vra-front-end": "replace"}),
           "helpers": _run(tmp_path_factory.mktemp("w-helpers"), "hel",
                           [(os.path.join(helpers, "vro"), "vro"), (os.path.join(helpers, "vra"), "hel-vra")])}
    import build as LB
    tmp = tmp_path_factory.mktemp("w-large")
    built = LB.build(str(tmp / "gen"), 12)
    shutil.copytree(built["vro"], tmp / "sources" / "big" / "vro")
    out["large"] = _run(tmp, "big", [], write_specs=LB.write_specs)
    return out


@pytest.mark.parametrize("which", ["review", "helpers", "large"])
def test_no_banned_word_in_customer_facing_output(which, estates):
    est = estates[which]
    files = _files(est)
    assert any(p.endswith("index.html") for p in files) and any(p.endswith("BRIEF.md") for p in files)
    bad = []
    for p in files:
        t = _text(p)
        for rule, rx in RULES.items():
            for m in rx.finditer(t):
                bad.append(f"{os.path.relpath(p, est)}: {rule}: ...{t[max(0, m.start() - 50):m.end() + 30]!r}")
    assert not bad, "\n".join(bad[:30])


@pytest.mark.parametrize("which", ["review", "helpers", "large"])
def test_the_front_page_running_text_has_no_task_class_and_no_answers_key(which, estates):
    page = open(os.path.join(estates[which], "index.html"), encoding="utf-8").read()
    body = page.split("<main>", 1)[1].split("</main>", 1)[0]
    body = re.sub(r"<code>[^<]*</code>", " ", body)        # a file name in a code span is not running text
    text = html.unescape(re.sub(r"<[^>]+>", " ", body))
    assert not re.search(r"io\.kestra\.[a-z]", text), re.search(r".{40}io\.kestra\..{40}", text)
    keys = re.compile(r"\b(defect|endpoint|tls|auth|client-route|mappings|ip-allocation|day2-scale|cancel|wave-exit|"
                      r"timeout|failure|state-store|question)\.[a-z0-9-]+|\bq\d-[a-z-]+")
    assert not keys.search(text), keys.search(text)


def test_each_page_opens_with_what_it_lets_you_check(estates):
    est = estates["review"]
    pages = ["report/translation.html", "report/waves.html", "report/workflows/index.html",
             "report/reports/index.html", "report/vra.html", "report/call-graph.html", "report/architecture.html",
             "report/plugins.html", "report/run.html"]
    pages += [os.path.relpath(p, est) for p in glob.glob(os.path.join(est, "report", "workflows", "*.html"))]
    for rel in pages:
        page = open(os.path.join(est, rel), encoding="utf-8").read()
        first = re.search(r"</h1>\s*<p[^>]*>(.*?)</p>", page.split("<main>", 1)[1], re.S)
        assert first and len(re.sub(r"<[^>]+>", "", first.group(1)).split()) >= 6, rel
