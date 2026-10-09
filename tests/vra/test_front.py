"""
index.html is the brief, written as a person would write it for the customer (stages/front.py).

Built for a synthetic 60-workflow estate (tests/vra/conformance/large/build.py, invented names) and for an
8-workflow one: the front page has its sections in the guide's order, every list on it is grouped and capped, and
its length stays within a fixed bound whatever the size. The table's counts add up to the estate's total, the
status box and the list of what we need agree, and BRIEF.md (the same text plus an appendix) stays short.
"""
from __future__ import annotations

import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "vra", "conformance", "large"))

from kestra_migration_assistant.vra.stages import common as C

MAX_WORDS = 800           # the front page's fixed length, in words a reader sees (a four-workflow estate: about 670)
MAX_BRIEF_WORDS = 3000    # BRIEF.md for 60 workflows (a four-workflow estate: about 1,400; the cap there is 2,000)


def _estate(tmp, n):
    import build as LB

    from kestra_migration_assistant.vra.stages import assess as A
    from kestra_migration_assistant.vra.stages import brief as B
    from kestra_migration_assistant.vra.stages import front as F
    from kestra_migration_assistant.vra.stages import preflight as P
    from kestra_migration_assistant.vra.stages import visuals as V
    out = LB.build(str(tmp / "src"), n)
    spec_dir = str(tmp / "specs")
    os.makedirs(spec_dir, exist_ok=True)
    C.configure("large", source_dir=str(tmp / "src"), stages_dir=str(tmp / "stages"), spec_dir=spec_dir,
                inputs={"large": out["vro"]}, vra_inputs={})
    P.run()
    a = A.run()
    LB.write_specs(spec_dir, {u: w["name"] for u, w in a["workflows"].items()})
    a = A.run()
    b = B.run()
    V.run()
    page = open(C.INDEX).read()
    return {"assess": a, "brief": b, "page": page, "text": F.text_of(page), "pages": C.PAGES,
            "md": open(C.BRIEF_MD).read()}


@pytest.fixture(scope="module")
def big(tmp_path_factory):
    old = os.environ.get("VRO_NO_MODEL")
    os.environ["VRO_NO_MODEL"] = "1"
    try:
        yield _estate(tmp_path_factory.mktemp("front-60"), 60)
    finally:
        if old is None:
            os.environ.pop("VRO_NO_MODEL", None)


@pytest.fixture(scope="module")
def small(tmp_path_factory):
    old = os.environ.get("VRO_NO_MODEL")
    os.environ["VRO_NO_MODEL"] = "1"
    try:
        yield _estate(tmp_path_factory.mktemp("front-8"), 8)
    finally:
        if old is None:
            os.environ.pop("VRO_NO_MODEL", None)


def _sections(page):
    import html
    return [html.unescape(x) for x in re.findall(r"<h2[^>]*>([^<]+)</h2>", page)]


def test_the_60_workflow_estate_is_what_the_test_says(big):
    assert big["assess"]["counts"]["workflows"] == 60 and big["assess"]["counts"]["actions"] >= 29
    assert len([t for t in big["assess"]["targets"] if t not in ("vro-platform", "rest-host")]) >= 6
    assert len(big["brief"]["suspected_defects"]) == 20
    assert big["brief"]["open_questions"]["grouped"] > 60


@pytest.mark.parametrize("which", ["big", "small"])
def test_the_front_page_follows_the_guides_order(which, request):
    r = request.getfixturevalue(which)
    page = r["page"]
    main = page.split("<main>", 1)[1]
    assert main.lstrip().startswith("<h1>Migration brief: large</h1>")
    assert "Stage 1 of the" in r["text"] and 'href="https://kestra.io/resources/migration/vra"' in page
    assert re.search(r'class="panel statusbox"><p class="headline"><span class="status [a-z]+">(Next step: |Ready: |Ready to '
                     r'sign|Signed, ready)', main)
    secs = _sections(page)
    assert secs[0] == "What's in this estate" and secs[1].startswith("The recommendation: ")
    assert secs[1].split(": ", 1)[1] in ("convert", "a mix", "rebuild")
    assert secs[2:] == ["What we need from you", "The plan"]           # the navigation is where to check
    assert "have a path to Kestra" in r["text"]
    assert "**Direct**" not in page and "<strong>Direct</strong> converts one for one" in page


@pytest.mark.parametrize("which", ["big", "small"])
def test_the_front_page_stays_within_a_fixed_length(which, request):
    r = request.getfixturevalue(which)
    words = len(r["text"].split())
    assert words <= MAX_WORDS, f"{which}: {words} words"
    table = r["page"].split("<table>", 1)[1].split("</table>", 1)[0]
    assert table.count("<tr>") - 1 <= F_CAPS()["rows"] + 1
    plan = r["page"].split("<h2>The plan</h2>", 1)[1].split("</ul>", 1)[0]
    assert plan.count("<li>") <= F_CAPS()["waves"] + 1
    needs = r["page"].split("<h2>What we need from you</h2>", 1)[1].split("</ul>", 1)[0]
    assert needs.count("<li>") <= 8
    assert len(r["md"].split()) <= MAX_BRIEF_WORDS, len(r["md"].split())


def F_CAPS():
    from kestra_migration_assistant.vra.stages import front as F
    return F.CAPS


def test_a_larger_estate_does_not_make_a_longer_page(big, small):
    print("words: big", len(big["text"].split()), "small", len(small["text"].split()), "brief big", len(big["md"].split()))
    assert len(big["text"].split()) <= len(small["text"].split()) + 120


@pytest.mark.parametrize("which", ["big", "small"])
def test_the_front_page_says_it_in_the_guides_words(which, request):
    """The How column says Direct or Redesign, or what a row waits on in words (never "Open"); the
    recommendation says how many of the elements convert Direct, then what the rest depend on; until answers make
    something ready, the figures say what the answers unlock, not that nothing is ready."""
    from kestra_migration_assistant.vra.stages import front as F
    r = request.getfixturevalue(which)
    table = r["page"].split("<table>", 1)[1].split("</table>", 1)[0]
    hows = set(re.findall(r'<span class="status [a-z]+">([^<]+)</span>', table))
    assert hows <= {"Direct", "Redesign", "Stays in vRA", "Retired", *F.WAITS.values()}, hows
    assert "Open" not in hows and "no path until" not in r["text"]
    rows = F.merged_rows(r["assess"], r["brief"])
    sp = F.split(rows)
    if sp["Direct"]:
        assert f"{sp['Direct']} of the {sp['total']} elements convert Direct" in r["text"]
    if sp["Open"]:
        assert re.search(rf"The other {sp['Open']} depends? on ", r["text"]) or f"All {sp['Open']}" in r["text"]
    if not r["brief"]["translation"]["number"].get("elements_ready"):
        assert "are ready now" not in r["text"] and "ready now" not in F.translation_page(r["assess"], r["brief"])


@pytest.mark.parametrize("which", ["big", "small"])
def test_the_table_adds_up_to_the_estate_and_says_direct_or_redesign(which, request):
    from kestra_migration_assistant.vra.stages import front as F
    r = request.getfixturevalue(which)
    rows = F.merged_rows(r["assess"], r["brief"])
    num = r["brief"]["translation"]["number"]
    assert sum(x["count"] for x in rows) == num["elements"] == sum(t["count"] for t in r["brief"]["translation"]["rows"])
    assert {x["how"] for x in rows} <= {"Direct", "Redesign", "Keep", "Retire", "Open"}
    shown = re.findall(r'<td class="num">(\d+)</td>', r["page"].split("<table>", 1)[1].split("</table>", 1)[0])
    assert sum(int(x) for x in shown) == num["elements"]
    sp = F.split(rows)
    assert f"{sp['path']} of {sp['total']}" in r["text"]
    # every workflow and every action is in exactly one row of the translation table
    tr = r["brief"]["translation"]
    a = r["assess"]
    assert sum(x["count"] for x in tr["rows"] if x["id"].startswith("wf-")) == a["counts"]["workflows"]
    assert sum(x["count"] for x in tr["rows"] if x["id"].startswith("a-")) == a["counts"]["actions"]


@pytest.mark.parametrize("which", ["big", "small"])
def test_the_status_box_and_the_list_agree_on_what_blocks(which, request):
    from kestra_migration_assistant.vra.stages import status as S
    r = request.getfixturevalue(which)
    st = S.status_of(r["brief"])
    m = re.search(r"Next step: answer (\d+) open items?", r["text"])
    if st["blocking"]:
        # the status box counts the entries of answers.yaml that hold a wave (a group is one entry) ...
        held = [x for x in r["brief"]["input"]["shown"] if set(x["members"]) & set(st["blocking"])]
        assert m and int(m.group(1)) == len(held) == st["holding"]
        # ... and the list says the same: one line per entry, the possible defects one line for every workflow
        needs = r["page"].split("<h2>What we need from you</h2>", 1)[1].split("</ul>", 1)[0]
        items = [it for it in re.findall(r"<li>(.*?)</li>", needs, re.S) if "Nothing waits on these" not in it]
        from kestra_migration_assistant.vra.stages.front import WORDS
        back = {v: k for k, v in WORDS.items()}
        n = 0
        for it in items:
            d = re.search(r"in (\w+) workflows?, one entry per workflow", it)
            n += (int(d.group(1)) if d.group(1).isdigit() else back[d.group(1)]) if d else 1
        assert n == len(held), (n, items)
    else:
        assert not m


def test_the_recommendation_opens_with_what_happens_to_the_workflows():
    from kestra_migration_assistant.vra.stages import front as F

    def brief(verdict, outcomes, reason="exists to serve vRA: calls getVraThing", client=0.0):
        tri = {f"w{i}": {"outcome": o, "reason": reason} for i, o in enumerate(outcomes)}
        return {"recommendation": {"verdict": verdict, "client_share": client}, "triage": tri}

    rows = [{"key": ("client", "x", "Direct"), "how": "Direct", "count": 47},
            {"key": ("vra-front", "Redesign"), "how": "Redesign", "count": 12}]
    head, s = F.recommendation({}, brief("convert", ["Convert"] * 4), rows)
    assert head == "convert" and s[0] == "Every workflow can be converted to Kestra, and nothing has to be rebuilt from scratch."
    assert s[1] == "47 of the 59 elements convert Direct. The other 12 need a Redesign, mostly where vRA itself goes away."
    # the rest waits on whether vRA stays (Q9): said in plain words, with which question it is
    wait = [{"key": ("client", "x", "Direct"), "how": "Direct", "count": 50},
            {"key": ("vra-front", "Open"), "how": "Open", "count": 4, "waits": "vra"},
            {"key": ("vra-events", "Open"), "how": "Open", "count": 5, "waits": "vra"}]
    _, s = F.recommendation({}, brief("convert", ["Convert"] * 4), wait)
    assert s[1] == ("50 of the 59 elements convert Direct. The other 9 depend on whether vRA stays in front, which "
                    "is one of the questions below.")
    head, s = F.recommendation({}, brief("convert", ["Convert"] * 38 + ["Rebuild"] * 4), rows)
    assert s[0] == ("Most of this estate can be converted: 38 workflows convert, and 4 are rebuilt from scratch because "
                    "they only make sense inside vRA.")
    head, s = F.recommendation({}, brief("mix", ["Convert"] * 22 + ["Rebuild"] * 18 + ["Retire"] * 6,
                                         reason="client for a target with no Kestra route"), rows)
    assert head == "a mix" and s[0] == ("Part of this estate converts and part is better rebuilt: 22 workflows convert, "
                                        "18 are rebuilt from scratch, mostly hand-written clients that Kestra plugins "
                                        "replace, and 6 can be retired.")
    head, s = F.recommendation({}, brief("rebuild", ["Rebuild"] * 5, client=0.6), rows)
    assert head == "rebuild" and s[0] == ("We recommend rebuilding this estate in Kestra rather than converting it, because "
                                          "most of its code is vendor client code that Kestra plugins replace. The specs "
                                          "and tests from this run become the requirements for the new flows.")


def test_every_list_on_the_front_page_has_its_full_list_one_click_away(big):
    page = big["page"]
    assert 'href="report/translation.html"' in page and 'href="report/waves.html"' in page
    tp = open(os.path.join(big["pages"], "translation.html")).read()
    for r in big["brief"]["translation"]["rows"]:
        assert f'id="{r["id"]}"' in tp
    assert open(os.path.join(big["pages"], "workflows", "index.html")).read().count("<tr><td>") == 60
    for x in ("Appendix A. Every element", "Appendix B. Entry criteria per wave"):
        assert x in big["md"]


def test_no_workflow_id_and_no_internal_path_on_the_front_page(big):
    for u in big["assess"]["workflows"]:
        assert u not in big["text"]
    assert "internal/" not in big["page"]


def test_no_link_points_at_an_anchor_the_guide_does_not_have_yet(big):
    from kestra_migration_assistant.vra.stages import front as F
    assert "#" not in F.GUIDE_323                     # the guide's page until its anchors are published
    assert "ANCHOR" not in big["md"] and "ANCHOR" not in big["page"]


def test_the_front_page_names_a_stand_in_only_when_there_is_one():
    from kestra_migration_assistant.vra.stages import front as F
    assert F.stand_in({"input": {"stand_in": True, "sign": {}}})
    assert not F.stand_in({"input": {"stand_in": False, "sign": {"signed": True}}})
