"""
Entry criteria that name the item closing them, in your-input/answers.yaml (stages/gates.py, your_input.py).

Run on the helpers conformance estate (tests/vra/conformance/helpers/), which has a workflow wave and, with
preflight Q9 answered "replace", a vRA replacement wave: blocking steps, a resource type no Kestra task
creates, an App from a catalog item, a lifecycle group.
"""
from __future__ import annotations

import os
import re
import sys

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from kestra_migration_assistant.vra.stages import common as C
from kestra_migration_assistant.vra.stages import gates as G
from kestra_migration_assistant.vra.stages import your_input as Y

HELPERS = os.path.join(ROOT, "tests", "vra", "conformance", "helpers")


def _configure(tmp):
    C.configure("helpers", source_dir=HELPERS, stages_dir=str(tmp), spec_dir=os.path.join(HELPERS, "specs"),
                inputs={"helpers": os.path.join(HELPERS, "vro")},
                vra_inputs={"helpers-vra": os.path.join(HELPERS, "vra")})


def _answer(q, text):
    from conftest import answer
    answer(q, text)


@pytest.fixture
def estate(tmp_path, monkeypatch):
    monkeypatch.setenv("VRO_NO_MODEL", "1")
    _configure(tmp_path)
    from kestra_migration_assistant.vra.stages import assess as A
    from kestra_migration_assistant.vra.stages import brief as B
    from kestra_migration_assistant.vra.stages import preflight as P
    P.run()
    _answer("Q9", "replace")
    A.run()

    def brief():
        b = B.run()
        return b, open(C.BRIEF_MD).read()
    return brief


def _criteria(b):
    return [(w["wave"], e) for w in b["plan"]["waves"] for e in w["entry_criteria"]]


def _fill(updates: dict):
    from kestra_migration_assistant.vra.stages import your_input as Y
    for k, v in updates.items():
        Y.set_answer(k, v.get("decision", ""))


def _doc():
    return yaml.load(open(C.ANSWERS), Loader=Y.TextLoader)      # as the tool reads it: answers are text


def _flat():
    return Y.flat(_doc())


def test_every_criterion_names_the_file_and_field_that_close_it(estate):
    b, md = estate()
    crit = _criteria(b)
    assert crit and any(w.get("kind") == "vra-replacement" for w in b["plan"]["waves"])
    files = {G.ANSWERS_FILE, G.CORRECTIONS_FILE}
    keys = set(_flat()) | {"signed_for"}
    for n, e in crit:
        c = e.get("closes")
        if c and c["kind"] == "gate":           # an earlier wave's exit: the migration closes it, nobody answers it
            assert c["field"] not in keys and c["when"] == f"Starts once Wave {c['wave']} is migrated and verified"
            continue
        assert c and c["file"] in files and c["field"], (n, e["criterion"])
        assert e["status"] in ("open", "done", "blocked")
        if c["file"] == G.ANSWERS_FILE:
            assert c["field"] in keys, (c["field"], "is not an item of answers.yaml")
    # BRIEF.md's appendix gives every open criterion the key that closes it (possible defects and spec questions
    # are counted instead; met ones are a count)
    appendix = md.split("## Appendix B", 1)[1]
    for _, e in crit:
        if e["status"] == "done":                             # met: counted, not listed
            continue
        if e["closes"]["kind"] == "gate":
            assert f": {e['closes']['when']}" in appendix
            continue
        k = e["closes"]["field"]
        if k.startswith(("defect.", "question.")):           # counted, one line per wave (test_wave_page.py)
            continue
        assert f"(`{k}`)" in appendix, k


def test_answers_yaml_lists_every_decision_with_options(estate):
    b, _ = estate()
    asked = {e["closes"]["key"] for _, e in _criteria(b) if e["closes"]["kind"] == "decision"}
    flat = _flat()
    assert asked <= set(flat)
    assert "edition" in asked and any(k.startswith("timeout.") for k in asked) and any(k.startswith("mappings.") for k in asked)
    # the helpers template's network is `networkType: existing`: referenced, so a mapping question and not a gap
    assert not any(k.startswith("gap.") for k in asked)
    text = open(C.ANSWERS).read()
    assert "Is Kestra Enterprise Edition available for this migration? (available or not available)" in text
    # a decision the code recommends shows it as `suggested`, the answer blank; the rest are a question and a blank
    d = _doc()
    for k in asked:
        rec = G.entries(b["plan"])[k].get("recommended")
        e = Y.entry_of(d, k)
        if "assumed" in e:                  # assumed (a timeout kept, as today): the answer it takes, no line to fill
            assert set(e) == {"question", "assumed"} and Y.section_of(d, k) == "estate", k
            assert not rec or e["assumed"] == rec, k
            continue
        assert set(e) <= {"question", "suggested", "answer"} and e["question"] and e.get("answer") is None, k
        if rec:
            assert e["suggested"] == rec, k
        elif G.suggestion(G.entries(b["plan"])[k]) is None:
            assert "suggested" not in e, k
    assert text.startswith("# Answer what's open, then run: kestra-migrate vra run ") and "\n\nopen:\n" in text
    assert sum(1 for ln in text.splitlines() if ln.lstrip().startswith("#")) == 1


def test_a_decision_closes_every_criterion_it_covers(estate):
    b, _ = estate()
    ed = [(n, e) for n, e in _criteria(b) if e["closes"]["key"] == "edition"]
    assert ed and all(e["status"] == "open" for _, e in ed)
    _fill({"edition": {"decision": "available"}})
    b, md = estate()
    ed = [(n, e) for n, e in _criteria(b) if e["closes"]["key"] == "edition"]
    assert all(e["status"] == "done" and "available" in e["note"] for _, e in ed)
    assert Y.section_of(_doc(), "edition") == "answered"
    assert "(`edition`)" not in md.split("## Appendix B", 1)[1]           # met: counted, no longer listed


def test_a_blocking_option_marks_the_criterion_blocked(estate):
    estate()
    _fill({"edition": {"decision": "not available"}})
    b, md = estate()
    assert all(e["status"] == "blocked" for _, e in _criteria(b) if e["closes"]["key"] == "edition")
    assert "- blocked: Kestra Enterprise Edition available (`edition`)" in md


def test_a_suggested_value_stays_open_until_a_yes_takes_it(estate):
    b, _ = estate()
    k = next(k for k, d in G.entries(b["plan"]).items() if d.get("recommended"))
    rec = G.entries(b["plan"])[k]["recommended"]
    e = next(e for _, e in _criteria(b) if e["closes"]["key"] == k)
    assert e["status"] == "open"
    _fill({k: {"decision": "yes"}})
    b, _ = estate()
    assert all(e["status"] == "done" and rec in e["note"] for _, e in _criteria(b) if e["closes"]["key"] == k)
    assert Y.entry_of(_doc(), k)["answer"] == "yes" and Y.entry_of(_doc(), k)["suggested"] == rec


def test_a_rewrite_never_changes_an_answer(estate):
    from kestra_migration_assistant.vra.stages import your_input as Y
    b, _ = estate()
    first = sorted(G.entries(b["plan"]))[0]
    Y.set_answer(first, "my answer, said in the meeting:\n  two lines")
    d = _doc()
    next(e for _s, e in Y._walk(d) if e["id"] == first)["mine"] = "a field of my own"
    d["open"].append({"id": "gone.key", "question": "something", "answer": "an answer to something no longer asked"})
    open(C.ANSWERS, "w").write(yaml.safe_dump(d, sort_keys=False))
    estate()
    flat = _flat()
    assert flat[first]["answer"] == "my answer, said in the meeting:\n  two lines"
    assert flat[first]["mine"] == "a field of my own"
    in_group = first in Y.load()["group_of"]                     # a member stays under its group, open or not
    assert Y.section_of(_doc(), first) == ("open" if in_group else "answered")
    assert flat["gone.key"]["answer"] == "an answer to something no longer asked"
    assert flat["gone.key"]["question"] == "something"
    assert Y.section_of(_doc(), "gone.key") == "no_longer_asked"
    assert open(C.ANSWERS).read().startswith("# Answer what's open")


def test_a_file_that_does_not_parse_is_not_rewritten(estate):
    estate()
    open(C.ANSWERS, "a").write("\nanswer: [unclosed\n")
    before = open(C.ANSWERS).read()
    b, _ = estate()
    assert open(C.ANSWERS).read() == before
    assert any("could not be read" in w for w in b["input"]["warnings"])


def test_answers_close_the_preflight_criteria(estate):
    b, _ = estate()
    q3 = [e for _, e in _criteria(b) if e["closes"]["kind"] == "answer" and e["closes"]["key"] == "Q3"]
    assert q3 and all(e["status"] == "open" for e in q3)
    _answer("Q3", "Yes, a lab for each system")
    b, _ = estate()
    q3 = [e for _, e in _criteria(b) if e["closes"]["kind"] == "answer" and e["closes"]["key"] == "Q3"]
    assert all(e["status"] == "done" and "a lab for each system" in e["note"] for e in q3)
    # Q9 answered "replace": the trigger plan criterion is met
    q9 = [e for _, e in _criteria(b) if e["closes"]["key"] == "Q9"]
    assert all(e["status"] == "done" for e in q9)


def test_the_signature_closes_the_approval_gate_of_the_waves_it_covers(estate):
    from kestra_migration_assistant.vra.stages import your_input as Y
    estate()
    Y.set_signature("wave-1")
    assert _doc()["signed_for"] == "wave 1"
    b, _ = estate()
    by_wave = {n: e for n, e in _criteria(b) if e["closes"]["kind"] == "approval"}
    assert by_wave[1]["status"] == "done" and by_wave[1]["note"] == "signed for Wave 1"
    assert all(e["status"] == "open" and "wave-1 only" in e["note"] for n, e in by_wave.items() if n != 1)


def test_replacement_wave_decisions_carry_a_recommendation(estate):
    b, _ = estate()
    asked = G.entries(b["plan"])
    timeouts = [d for k, d in asked.items() if k.startswith("timeout.")]
    assert timeouts and all("keep vRA's timeout" in d["recommended"] for d in timeouts)
    assert any(k.startswith("existing-deployments.") for k in asked)
    # a vRO workflow run from two places is a note of the plan, not a gate
    rw = [w for w in b["plan"]["waves"] if w.get("kind") == "vra-replacement"]
    assert not any("runs at 2 places" in e["criterion"] for w in rw for e in w["entry_criteria"])


def test_client_route_recommendation_follows_the_size_of_the_client():
    from kestra_migration_assistant.vra.stages import waves as W
    t = {"name": "Thing", "sdk_alternative": "thingsdk on PyPI", "operations": []}
    small = {"plugin_coverage": {"rows": [{"key": "thing", "status": "http", "ops": [1, 2]}]},
             "actions": [{"lines": 100, "library_for": ["thing"]}]}
    big = {**small, "actions": [{"lines": W.SDK_LINES + 1, "library_for": ["thing"]}]}
    assert W._client_route("thing", t, small)["recommended"] == "http"
    assert W._client_route("thing", t, big)["recommended"] == "sdk"
    assert W._client_route("thing", {**t, "sdk_alternative": None}, big)["recommended"] == "http"   # no SDK known
    assert W._client_route("thing", t, {"actions": []})["recommended"] is None                   # nothing planned


def test_a_delete_path_step_does_not_run_the_create_cleanup():
    from kestra_migration_assistant.vra.stages import replace_vra as R
    from kestra_migration_assistant.vra.stages import waves as W
    assert R._path({"event_topic": "compute.removal.pre"}) == "delete"
    assert R._path({"event_topic": "compute.provision.post"}) == "create"
    d = W.timeout_policy("k", "Remove", 10, path="delete")
    assert "stops the deletion" in d["recommended"] and "create path" not in d["recommended"]
    c = W.timeout_policy("k", "Create", 15, path="create")
    assert "15 min" in c["recommended"] and "cleanup" in c["recommended"]


def _answer_everything():
    """Answer every open entry as a person would: yes to the suggestions and the specs, keep all to each workflow's
    defects, and a stand-in answer to everything asked one by one."""
    for e in (_doc()["open"] or []) + (_doc().get("waves") or []):
        k = e["id"]
        if "each" in e and "answer" not in e:
            for m in e["each"]:
                Y.set_answer(m["id"], "yes" if m.get("suggested") else "a stand-in answer")
        else:
            Y.set_answer(k, "keep all" if k.startswith("defects.") else
                         "yes" if e.get("suggested") or k in ("suggested", "specs") else "a stand-in answer")


def test_a_wave_exit_is_a_gate_the_migration_closes_not_an_open_item(estate):
    """Wave 2 (replacing vRA) runs Wave 1's flows. Wave 1's exit is not asked in answers.yaml and is not an open
    item; once every question is answered, the status line gives the order the waves run in instead."""
    from kestra_migration_assistant.vra.stages import front as F
    from kestra_migration_assistant.vra.stages import status as S
    from kestra_migration_assistant.vra.stages import visuals as V
    b, _ = estate()
    gate = [(n, e) for n, e in _criteria(b) if e["closes"]["kind"] == "gate"]
    assert [(n, e["closes"]["key"]) for n, e in gate] == [(2, "wave-exit.1")]
    assert not any(k.startswith("wave-exit") for k in _flat())
    st = S.status_of(b)
    assert st["blocking"] and not any("wave-exit" in k for k in st["blocking"])
    for _ in range(2):                                  # answering one item can ask another (Q3 names the systems)
        _answer_everything()
        b, md = estate()
    assert _doc()["open"] == []
    st = S.status_of(b)
    assert st["open"] == 0 and not st["blocking"] and not st["blocks"]
    assert st["line"] == "Ready to sign. Once signed, Wave 1 can start. Wave 2 starts once Wave 1 is done."
    # the gate stays on the waves page and in BRIEF.md's appendix, in one plain sentence
    assert "- open: Starts once Wave 1 is migrated and verified" in md.split("## Appendix B", 1)[1]
    V.run()
    waves = open(os.path.join(C.PAGES, "waves.html")).read()
    assert re.search(r"</span> Starts once Wave 1 is migrated and verified\s*</li>", waves)
    assert "is done first" not in waves and "closes when" not in waves
    assert "<code>wave-exit.1</code>" not in waves
    page = open(C.INDEX).read()
    text = F.text_of(page)
    assert "Ready to sign." in text and "Wave 2 starts once Wave 1 is done." in text and "Nothing is open." in text
    assert "wave-exit" not in page and "exit criteria" not in text
    Y.set_signature("full plan")
    b, _ = estate()
    assert S.status_of(b)["line"] == "Ready: Wave 1 can start. Wave 2 starts once Wave 1 is done."
