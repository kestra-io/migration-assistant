"""
An answer attaches to what it was given for, not to a position (stages/ids.py).

The review estate (tests/vra/conformance/review/) is briefed from its committed specs, every possible defect and
spec question is answered, and the brief is run again on specs whose model wrote the same findings in other words
and in reverse order, with one defect gone. Each answer stays with its finding; the answer to the finding that
went is listed under no_longer_asked and lands on nothing else. An answers.yaml written when the IDs were
positions (defect.<workflow>.<n>) carries over only where an item's text is the same, never by its number.
"""
from __future__ import annotations

import json
import os
import shutil
import sys

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from kestra_migration_assistant.vra.stages import common as C
from kestra_migration_assistant.vra.stages import ids as I
from kestra_migration_assistant.vra.stages import your_input as Y

REVIEW = os.path.join(ROOT, "tests", "vra", "conformance", "review")
CREATE, DELETE = "review-Dns---Create-Zone", "review-Dns---Delete-Zone"


@pytest.fixture
def estate(tmp_path, monkeypatch):
    from kestra_migration_assistant.vra.stages import assess as A
    from kestra_migration_assistant.vra.stages import preflight as P
    monkeypatch.setenv("VRO_NO_MODEL", "1")
    specs = tmp_path / "specs"
    shutil.copytree(os.path.join(REVIEW, "specs"), specs)
    C.configure("review", source_dir=REVIEW, stages_dir=str(tmp_path / "stages"), spec_dir=str(specs),
                inputs={"review": os.path.join(REVIEW, "vro")}, vra_inputs={"review-vra": os.path.join(REVIEW, "vra")})
    P.run()
    A.run()
    return specs


def _brief() -> dict:
    from kestra_migration_assistant.vra.stages import brief as B
    return B.run()


def _doc() -> dict:
    return yaml.safe_load(open(C.ANSWERS))


def _rewrite(specs, u, change) -> None:
    p = specs / f"{u}.json"
    s = json.loads(p.read_text())
    change(s)
    p.write_text(json.dumps(s, indent=1))


def _ids_by_what(b) -> dict[str, str]:
    return {d["what"]: d["decision"] for d in b["suspected_defects"]}


def test_ids_name_the_workflow_the_element_and_the_kind():
    names = ["renderBody", "removeHostRecord", "shouldDropZone"]
    ds = [{"what": "a failed create also drops the zone, but nothing passes the failed status here",
           "evidence": "shouldDropZone(eventType, status)"},
          {"what": "host records are removed with the zone's reference, not each record's own",
           "evidence": "removeHostRecord(ib, state.ref)"},
          {"what": "Certificate checks are off for every call", "evidence": "hostVerification = false"}]
    ids = I.defect_ids("dns-delete-zone", ds, names)
    assert ids == ["defect.dns-delete-zone.should-drop-zone.other",
                   "defect.dns-delete-zone.remove-host-record.same-value-for-all",
                   "defect.dns-delete-zone.tls"]
    assert I.defect_ids("dns-delete-zone", ds[::-1], names) == ids[::-1]      # the order changes nothing
    # an element the export does not have is never part of an ID
    assert I.defect_ids("w", [{"what": "madeUpHelper ignores the status", "evidence": ""}], names) == ["defect.w.masked-error"]
    # two of one kind are told apart by what each cites and the other doesn't, never by their order: a name from the
    # code, else the words that gave them their kind
    two = [{"what": "the monitor's receive_timeout is not under its send_interval", "evidence": ""},
           {"what": "the retry wait is shorter than the controller's apply_delay", "evidence": ""}]
    assert I.defect_ids("w", two, []) == ["defect.w.timing_receive-timeout", "defect.w.timing_apply-delay"]
    assert I.defect_ids("w", two[::-1], []) == ["defect.w.timing_apply-delay", "defect.w.timing_receive-timeout"]
    two = [{"what": "b: the timeout is too short", "evidence": ""}, {"what": "a: the interval is too long", "evidence": ""}]
    assert I.defect_ids("w", two, []) == ["defect.w.timing_timeout", "defect.w.timing_interval"]
    assert I.kind_part("defect.w.x.timing_interval") == "timing" and I.defect_words("defect.w.x.tls_y") == "tls (x)"


def test_an_answer_survives_a_reworded_rerun_in_another_order(estate):
    b = _brief()
    by_what = _ids_by_what(b)
    assert len(by_what) == 3 and len(set(by_what.values())) == 3
    assert not any(k.rsplit(".", 1)[-1].isdigit() for k in by_what.values())   # no position in any ID
    placeholder = by_what["the rendered body is parsed without checking for placeholders left unfilled"]
    own_ref = by_what["host records are removed with the zone's reference, not each record's own"]
    cleanup = by_what["a failed create also drops the zone, but nothing passes the failed status here"]
    questions = [g for g in b["open_questions"]["groups"]]
    ttl = next(g["key"] for g in questions if "TTL" in g["question"])
    for k, a in ((placeholder, "fix"), (own_ref, "keep: records share the zone reference by design"),
                 (cleanup, "fix"), (ttl, "3600 seconds")):
        Y.set_answer(k, a)
    _brief()

    # the model words every finding differently, lists them in reverse order, and no longer finds the cleanup defect
    def reword_create(s):
        s["suspected_defects"] = [{"what": "Unfilled placeholders are not detected: the body goes straight to "
                                           "JSON.parse without checking for any {{ }} left behind",
                                   "evidence": "renderBody joins the template, then JSON.parse(text)",
                                   "question": "Stop the run when a placeholder stays unfilled?"}]
        s["open_questions"] = [{"question": "In production, what TTL do newly created zones get?",
                                "why_it_matters": "the zone settings", "theme": "scope"},
                               {"question": "Is a failed Infoblox call retried before the step fails?",
                                "why_it_matters": "retries", "theme": "error semantics"}]

    def reword_delete(s):
        s["suspected_defects"] = [{"what": "Each host record is deleted using the zone's reference instead of "
                                           "each record's own",
                                   "evidence": "the loop calls removeHostRecord(ib, state.ref) for every record",
                                   "question": "Delete each record by its own reference?"}]
    _rewrite(estate, CREATE, reword_create)
    _rewrite(estate, DELETE, reword_delete)
    b2 = _brief()
    after = _ids_by_what(b2)
    assert sorted(after.values()) == sorted([placeholder, own_ref])
    d = _doc()
    assert Y.entry_of(d, placeholder)["answer"] == "fix"
    assert Y.entry_of(d, own_ref)["answer"] == "keep: records share the zone reference by design"
    assert Y.entry_of(d, ttl)["answer"] == "3600 seconds"
    assert "In production, what TTL" in Y.entry_of(d, ttl)["question"]               # today's words
    # the answer to the defect that went is listed as no longer asked, with the question it was given for
    assert Y.section_of(d, cleanup) == "no_longer_asked"
    gone = Y.entry_of(d, cleanup)
    assert gone["answer"] == "fix" and "nothing passes the failed status" in gone["question"]
    # and it is on nothing else: every other answer is the one given for that item
    answers = {k: e.get("answer") for k, e in Y.flat(d).items() if e.get("answer")}
    assert sorted(k for k, a in answers.items() if a == "fix") == sorted([placeholder, cleanup])


def test_two_defects_of_one_kind_on_one_action_keep_their_answers_when_reworded_and_reversed(estate):
    # two masked errors in removeHostRecord: one swallows a 404, the other ignores the DNS restart's result
    def two(s):
        s["suspected_defects"] = [
            {"what": "removeHostRecord swallows a 404 on the record, so a record already gone is reported as removed",
             "evidence": "catch (e) { if (e.status == 404) return; }", "question": "Fail on a 404?"},
            {"what": "removeHostRecord ignores the result of the DNS restart, so a failed restart passes silently",
             "evidence": "ib.restartServices() is called and its result dropped", "question": "Check the restart?"}]
    _rewrite(estate, DELETE, two)
    by_what = _ids_by_what(_brief())
    swallow, restart = (by_what[w] for w in sorted(by_what) if w.startswith("removeHostRecord"))
    if "swallows" not in next(w for w, k in by_what.items() if k == swallow):
        swallow, restart = restart, swallow
    assert swallow != restart and {swallow.split("_")[0], restart.split("_")[0]} == {
        "defect.dns-delete-zone.remove-host-record.masked-error"}
    assert restart.endswith("_ib-restart-services") and swallow.endswith("_e-status")   # what each one cites
    Y.set_answer(swallow, "keep: a record already gone is fine")
    Y.set_answer(restart, "fix")

    # the model words both differently and lists them the other way round
    def reworded(s):
        s["suspected_defects"] = [
            {"what": "A failed DNS restart is silently missed: removeHostRecord never looks at what ib.restartServices() "
                     "returned", "evidence": "the restart's reply is dropped", "question": "Check it?"},
            {"what": "A 404 from removeHostRecord is tolerated, and the record counts as removed",
             "evidence": "returns when e.status == 404", "question": "Fail on it?"}]
    _rewrite(estate, DELETE, reworded)
    after = _ids_by_what(_brief())
    assert after["A failed DNS restart is silently missed: removeHostRecord never looks at what ib.restartServices() "
                 "returned"] == restart
    assert after["A 404 from removeHostRecord is tolerated, and the record counts as removed"] == swallow
    d = _doc()
    assert Y.entry_of(d, swallow)["answer"] == "keep: a record already gone is fine"
    assert Y.entry_of(d, restart)["answer"] == "fix"
    assert Y.section_of(d, swallow) != "no_longer_asked" and Y.section_of(d, restart) != "no_longer_asked"


def test_an_answer_under_a_positional_id_carries_over_only_when_the_text_is_the_same(estate):
    b = _brief()
    reg = Y.registry()
    # the estate as answered before: the same items under the positional keys of then
    old = {d["decision"]: f"defect.{d['short']}.{d['n']}" for d in b["suspected_defects"]}
    for it in reg["brief"]:
        if it["key"] in old:
            it["key"] = old[it["key"]]
    reg["brief"].append(Y.item(f"defect.{b['suspected_defects'][0]['short']}.9", "answer",
                               "a defect worded in a way no run finds today"))
    C.write_json(os.path.join(C.DATA, "input-items.json"), reg)
    text = Y.render(Y.items(), Y.load())
    C.write_text(C.ANSWERS, text)
    for i, k in enumerate(old.values()):
        Y.set_answer(k, f"answer {i}")
    Y.set_answer(f"defect.{b['suspected_defects'][0]['short']}.9", "fix")
    _brief()
    d = _doc()
    for i, (new, k) in enumerate(old.items()):
        assert Y.entry_of(d, new)["answer"] == f"answer {i}" and Y.entry_of(d, k) is None
    nine = f"defect.{b['suspected_defects'][0]['short']}.9"
    assert Y.section_of(d, nine) == "no_longer_asked" and Y.entry_of(d, nine)["answer"] == "fix"
