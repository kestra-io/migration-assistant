"""
The waves page speaks to a customer: no internal defect or question IDs, possible defects and spec questions as one
counted line per wave linking to the workflow pages, and an earlier wave's exit said plainly (stages/visuals.py).
Run on the helpers conformance estate, whose spec gets two possible defects and a question here.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from kestra_migration_assistant.vra.stages import common as C

HELPERS = os.path.join(ROOT, "tests", "vra", "conformance", "helpers")
SPEC = "helpers-Web---Provision-Host.json"


@pytest.fixture
def estate(tmp_path, monkeypatch):
    from conftest import answer

    from kestra_migration_assistant.vra.stages import assess as A
    from kestra_migration_assistant.vra.stages import preflight as P
    monkeypatch.setenv("VRO_NO_MODEL", "1")
    specs = tmp_path / "specs"
    shutil.copytree(os.path.join(HELPERS, "specs"), specs)
    s = json.loads((specs / SPEC).read_text())
    s["suspected_defects"] = [
        {"what": "the wait for the host is shorter than its boot timeout", "evidence": "", "question": "Keep it?"},
        {"what": "a 404 from the inventory call is silently ignored", "evidence": "", "question": "Keep it?"}]
    s["open_questions"] = [{"question": "Which team owns the inventory service?", "why_it_matters": "owners",
                            "theme": "external contract"}]
    (specs / SPEC).write_text(json.dumps(s, indent=1))
    C.configure("helpers", source_dir=HELPERS, stages_dir=str(tmp_path / "stages"), spec_dir=str(specs),
                inputs={"helpers": os.path.join(HELPERS, "vro")},
                vra_inputs={"helpers-vra": os.path.join(HELPERS, "vra")})
    P.run()
    answer("Q9", "replace")
    A.run()
    return answer


def _waves() -> tuple[str, str]:
    from kestra_migration_assistant.vra.stages import brief as B
    from kestra_migration_assistant.vra.stages import front as F
    from kestra_migration_assistant.vra.stages import visuals as V
    b = B.run()
    V.run()
    page = open(os.path.join(C.PAGES, "waves.html")).read()
    assert b["suspected_defects"]
    return page, F.text_of(page)


def test_the_waves_page_counts_defects_and_questions_and_shows_no_ids(estate):
    page, text = _waves()
    assert not re.search(r"\b(defect|question)\.[a-z0-9-]+\.", page)              # no internal ID, anywhere
    assert "2 of 2 possible defects still to decide" in text
    assert "1 of 1 question from reading the workflows still to answer" in text
    assert text.count("possible defect") == 1                                      # one line for the wave
    assert re.search(r'possible defects still to decide.{0,200}<a href="workflows/web-provision-host\.html">', page,
                     re.S)
    appendix = open(C.BRIEF_MD).read().split("## Appendix B", 1)[1]
    assert "- open: 2 of 2 possible defects still to decide" in appendix
    assert not re.search(r"\b(defect|question)\.[a-z0-9-]+\.", appendix)
    estate("defects.web-provision-host", "keep them all")
    page, text = _waves()
    assert "2 possible defects, decided (listed on the pages of Web - Provision Host (2))" in text
    assert "still to decide" not in text


def test_an_earlier_wave_is_said_plainly(estate):
    page, text = _waves()
    assert "Starts once Wave 1 is migrated and verified" in text
    assert "is done first" not in text and "closes when" not in text


def _panels(page: str) -> dict[int, str]:
    return {int(m.group(1)): p for p in page.split('<div class="panel"')[1:]
            if (m := re.search(r"<h2>Wave (\d+)", p))}


def test_only_open_criteria_are_listed_and_the_met_ones_fold_into_one_line(estate):
    from test_gates import _answer_everything

    from kestra_migration_assistant.vra.stages import front as F
    from kestra_migration_assistant.vra.stages import your_input as Y
    # partly met: what is open is listed, with the entry that closes it, above "k of n met"
    estate("edition", "available")
    page, _ = _waves()
    w1 = _panels(page)[1]
    m = re.search(r"<details class='met'><summary>(\d+) of (\d+) met</summary>", w1)
    assert m and 0 < int(m.group(1)) < int(m.group(2))
    assert w1.index("<span class='status wait'>open</span>") < w1.index("<details")
    assert "<code>defects.web-provision-host</code>" in w1                        # the entry that closes it
    met = w1.split("<details", 1)[1]
    assert "Kestra Enterprise Edition" in met and "Kestra Enterprise Edition" not in w1.split("<details", 1)[0]
    assert "status wait" not in met
    md = open(C.BRIEF_MD).read().split("## Appendix B", 1)[1]
    md1 = md.split("### Wave 1", 1)[1].split("### Wave", 1)[0]
    assert f"\n{m.group(1)} of {m.group(2)} met.\n" in md1 and "- open: " in md1 and "- met" not in md1
    assert "`edition`" not in md1 and "`defects.web-provision-host`" in md1

    # fully met (Wave 1): nothing listed, one folded line; Wave 2 still waits on Wave 1's exit
    for _ in range(2):
        _answer_everything()
        _waves()
    Y.set_signature("full")
    page, _ = _waves()
    p = _panels(page)
    m = re.search(r"<details class='met'><summary>(\d+) of (\d+) met</summary>", p[1])
    assert m and m.group(1) == m.group(2)
    assert "status wait" not in p[1] and "<h3>Entry criteria</h3><details" in p[1]
    assert F.text_of(p[2]).count("open") == 1 and "Starts once Wave 1 is migrated and verified" in F.text_of(p[2])
    md = open(C.BRIEF_MD).read().split("## Appendix B", 1)[1]
    md1, md2 = md.split("### Wave 1", 1)[1].split("### Wave 2", 1)
    assert md1.strip() == f"{m.group(1)} of {m.group(2)} met."
    assert "- open: Starts once Wave 1 is migrated and verified" in md2
