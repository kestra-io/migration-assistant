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
