"""
Preflight's questions are asked from what is on record: the export check, and after Assess its findings.
A question never asks for what the tool already has (contradictions()), and rewriting the questions keeps
every answer byte for byte. Checked on the synthetic helpers estate (tests/conformance/helpers/, invented
names), once with its vRA content export and once without.
"""
from __future__ import annotations

import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vro2k_assess.stages import common as C, your_input as Y  # noqa: E402

HELPERS = os.path.join(ROOT, "tests", "conformance", "helpers")


def _configure(tmp, vra: bool):
    C.configure("helpers", source_dir=HELPERS, stages_dir=str(tmp), spec_dir=os.path.join(HELPERS, "specs"),
                inputs={"helpers": os.path.join(HELPERS, "vro")},
                vra_inputs={"helpers-vra": os.path.join(HELPERS, "vra")} if vra else {})


def _run(tmp, vra: bool) -> dict:
    """Preflight, then Assess, then the refresh `vro.py assess` and `all` make after Assess."""
    old = os.environ.get("VRO_NO_MODEL")
    os.environ["VRO_NO_MODEL"] = "1"
    try:
        _configure(tmp, vra)
        from vro2k_assess.stages import assess as A, preflight as P
        before = P.run()
        A.run()
        refresh = P.refresh_questions()
        ans = P.load_answers()
        f = P.facts()
    finally:
        if old is None:
            os.environ.pop("VRO_NO_MODEL", None)
    return {"before": before, "refresh": refresh, "answers": ans, "facts": f, "tmp": str(tmp),
            "q": {q["id"]: q for q in ans["items"]},
            "file": open(C.ANSWERS).read()}


@pytest.fixture(scope="module")
def with_vra(tmp_path_factory):
    return _run(tmp_path_factory.mktemp("pfq-vra"), True)


@pytest.fixture(scope="module")
def without_vra(tmp_path_factory):
    return _run(tmp_path_factory.mktemp("pfq-novra"), False)


# ------------------------------------------------------------------ no question asks for what is present

def test_no_question_contradicts_what_the_tool_found(with_vra):
    from vro2k_assess.stages import preflight as P
    assert with_vra["facts"]["vra"] and with_vra["facts"]["systems"] and with_vra["facts"]["plugins"]
    assert P.contradictions(with_vra["answers"]["items"], with_vra["facts"]) == []
    assert with_vra["refresh"]["contradictions"] == []
    # the same on the file a customer fills in, not only on the parsed items
    import yaml
    flat = Y.flat(yaml.safe_load(with_vra["file"]))
    for qid in ("Q2", "Q3", "Q8"):
        # the item's question in answers.yaml is the question as asked today
        q = with_vra["q"][qid]
        it = {"ask": f"{qid}. {q['question']}", "family": "preflight", "question": q.get("plain"),
              "options": [], "suggested": q.get("suggested")}
        assert flat[P.answer_key(qid)]["question"] == Y.question_of(it)
    assert "entry-points.yaml" not in with_vra["file"]          # a vRA export: Q2 is answered in answers.yaml


def test_q2_with_a_vra_export_asks_only_for_what_an_export_cannot_hold(with_vra):
    q = with_vra["q"]["Q2"]
    assert not re.search(r"\.vra-ng|content export", q["question"], re.I), q["question"]
    assert all(w in q["question"] for w in ("scheduled tasks", "ITSM", "REST API"))
    assert "helpers-vra" in q["found"] and "1 catalog item" in q["found"] and "1 event subscription" in q["found"]


def test_q3_names_the_systems_detected(with_vra):
    q = with_vra["q"]["Q3"]
    for name in ("Infoblox", "Nuage VSD", "vCenter"):
        assert name in q["question"], q["question"]
    for generic in ("NSX", "Avi", "vCloud Director", "IPAM", "ITSM"):
        assert not re.search(r"\b" + generic + r"\b", q["question"]), generic
    assert "cloud templates" in q["found"]            # vCenter comes from the cloud template's machines


def test_q8_is_prefilled_from_the_code_and_left_for_the_customer_to_confirm(with_vra):
    q = with_vra["q"]["Q8"]
    # a confirm item: the code's list pre-filled, open until the customer confirms or corrects it
    assert "HTTP-REST" in q["question"] and "callApi" in q["found"] and q["suggested"] == "HTTP-REST plugin (built in)"
    assert q["status"] == "open" and q["answer"] == ""
    import yaml
    doc = yaml.safe_load(with_vra["file"])
    assert Y.entry_of(doc, q["key"]) == {"question": "The code uses these vRO plugins: HTTP-REST plugin (built in). "
                                                     "Is that the list?",
                                         "suggested": "HTTP-REST plugin (built in)", "answer": None}


def test_q9_offers_replace_with_a_bridge(with_vra):
    assert "bridge" in with_vra["q"]["Q9"]["question"] and with_vra["q"]["Q9"]["status"] == "open"


def test_without_a_vra_export_q2_still_asks_for_it(without_vra):
    from vro2k_assess.stages import preflight as P
    q2 = without_vra["q"]["Q2"]
    assert ".vra-ng" in q2["question"] and not q2.get("found")
    assert without_vra["q"]["Q9"]["status"] == "not applicable"
    assert "vCenter" not in without_vra["q"]["Q3"]["question"]     # no cloud template on record
    assert P.contradictions(without_vra["answers"]["items"], without_vra["facts"]) == []


def test_before_assess_q3_falls_back_to_the_generic_list(with_vra):
    q3 = next(q for q in with_vra["before"]["answers"]["items"] if q["id"] == "Q3")
    assert "Nuage VSD, vCloud Director" in q3["question"] and not q3.get("found")
    assert with_vra["before"]["question_contradictions"] == []


def test_the_static_questions_contradict_these_findings():
    """The check itself: the questions as they were before (one wording for every estate) are caught."""
    from vro2k_assess.stages import preflight as P
    f = {"vra": {"exports": ["x-vra"], "catalog_items": 1, "cloud_templates": 1, "subscriptions": 2, "day2_actions": 0},
         "systems": [("Infoblox", "the workflows' code")], "plugins": {"HTTP-REST plugin (built in)": ["callApi"]},
         "history_files": ["runs.csv"]}
    got = P.contradictions(P.QUESTIONS, f)
    assert [g.split(" ", 1)[0] for g in got] == ["Q2", "Q3", "Q8", "Q7"], got
    assert P.contradictions(P.questions_for(f), f) == []


# (answers surviving a rewrite of answers.yaml: tests/test_your_input.py)


def test_your_input_holds_only_what_the_customer_needs(with_vra, without_vra):
    import yaml
    from vro2k_assess.stages import preflight as P
    # with a vRA export: answers.yaml alone (no entry-points.yaml, never a corrections.yaml)
    assert os.listdir(os.path.join(with_vra["tmp"], "your-input")) == ["answers.yaml"]
    # without one: entry-points.yaml too, and exactly one open question points to it (Q2)
    assert sorted(os.listdir(os.path.join(without_vra["tmp"], "your-input"))) == ["answers.yaml", "entry-points.yaml"]
    doc = yaml.safe_load(without_vra["file"])
    pointing = [e["id"] for e in doc["open"] if "entry-points.yaml" in e["question"]]
    assert pointing == [P.answer_key("Q2")]
    assert "No vRA export was found" in Y.entry_of(doc, P.answer_key("Q2"))["question"]
