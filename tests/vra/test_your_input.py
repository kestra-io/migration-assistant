"""
your-input/answers.yaml (stages/your_input.py): the one file a person fills in, rewritten around their answers.

Two lists, open and answered, then signed_for. Each item is an id, one plain question, a suggested value when
the code points to one, and the answer; one comment line at the top. An item moves to answered once it has an
answer, the answer is kept exactly as written, a `yes` takes the suggested value (and keeps it when the
suggestion changes later), and no id with an answer is ever lost. A file in the format before session 2g is
carried over with every answer and the signature. The time of the file is kept when only the tool's text
changed. And the recommendation counts only client code as client code.
"""
from __future__ import annotations

import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from kestra_migration_assistant.vra.stages import common as C
from kestra_migration_assistant.vra.stages import your_input as Y

HELPERS = os.path.join(ROOT, "tests", "vra", "conformance", "helpers")


def _setup(tmp):
    C.configure("helpers", source_dir=HELPERS, stages_dir=str(tmp), inputs={"helpers": os.path.join(HELPERS, "vro")},
                vra_inputs={})


def _doc():
    return yaml.load(open(C.ANSWERS), Loader=Y.TextLoader)      # as the tool reads it: answers are text


def test_the_file_has_one_comment_two_lists_and_the_signature(tmp_path):
    _setup(tmp_path)
    Y.register("t", [Y.item("a", "answer", "A? More words.", unblocks=["Wave 1: a"]),
                     Y.item("b", "confirm", "B?", suggested="one"), Y.item("c", "answer", "C?")])
    text = open(C.ANSWERS).read()
    assert text.splitlines()[0].startswith("# Answer what's open, then run: kestra-migrate vra run ")
    assert sum(1 for ln in text.splitlines() if ln.lstrip().startswith("#")) == 1
    d = _doc()
    assert list(d) == ["open", "answered", "signed_for"] and d["answered"] == [] and d["signed_for"] is None
    assert d["open"][0] == {"id": "a", "question": "A?", "answer": None}          # what a wave waits on first
    assert d["open"][1] == {"id": "b", "question": "B?", "suggested": "one", "answer": None}


def test_an_answer_moves_the_item_and_is_kept_exactly(tmp_path):
    _setup(tmp_path)
    its = [Y.item("a", "answer", "A?"), Y.item("b", "answer", "B?")]
    Y.register("t", its)
    Y.set_answer("b", "Yes: two lines,\n  the second indented")
    Y.register("t", its)
    d = _doc()
    assert [e["id"] for e in d["open"]] == ["a"]
    assert d["answered"] == [{"id": "b", "question": "B?", "answer": "Yes: two lines,\n  the second indented"}]
    Y.register("t", its)                                 # and again: nothing moves, nothing changes
    assert _doc() == d
    Y.register("t", [Y.item("q", "answer", "'Step one' is blocking: what does a failure do?")])
    assert "    question: \"'Step one' is blocking: what does a failure do?\"" in open(C.ANSWERS).read()


def test_yes_takes_the_suggestion_and_keeps_it_when_the_suggestion_changes(tmp_path):
    _setup(tmp_path)
    Y.register("t", [Y.item("a", "confirm", "A?", suggested="one"), Y.item("b", "confirm", "B?", suggested="one")])
    Y.set_answer("a", "yes")
    Y.set_answer("b", "mine")
    Y.register("t", [Y.item("a", "confirm", "A?", suggested="two"), Y.item("b", "confirm", "B?", suggested="two")])
    st = {it["key"]: Y.state(it["key"], Y.load(), it) for it in Y.items()}
    assert st["a"]["state"] == "answered" and st["a"]["answer"] == "one"     # what the yes agreed to
    assert st["b"]["answer"] == "mine"
    assert Y.entry_of(_doc(), "a") == {"question": "A?", "suggested": "one", "answer": "yes"}


def test_no_id_with_an_answer_is_ever_lost(tmp_path):
    _setup(tmp_path)
    Y.register("t", [Y.item("x", "answer", "X?"), Y.item("y", "confirm", "Y?", suggested="s")])
    Y.set_answer("x", "an answer")
    Y.register("t", [])
    d = _doc()
    assert d["no_longer_asked"] == [{"id": "x", "question": "X?", "answer": "an answer"}]
    assert d["open"] == [] and d["answered"] == []      # an item with no answer simply goes
    Y.register("t", [])
    assert _doc() == d                                   # and stays where it is
    Y.register("t", [Y.item("x", "answer", "X?")])       # asked again: the answer is back on it
    assert _doc()["answered"] == [{"id": "x", "question": "X?", "answer": "an answer"}] and "no_longer_asked" not in _doc()


def test_a_rewrite_of_the_tools_text_keeps_the_files_time(tmp_path):
    _setup(tmp_path)
    Y.register("t", [Y.item("x", "answer", "X, first wording?")])
    Y.set_answer("x", "kept")
    Y.register("t", [Y.item("x", "answer", "X, first wording?")])
    os.utime(C.ANSWERS, (1_000_000_000, 1_000_000_000))
    Y.register("t", [Y.item("x", "answer", "X, second wording?")])
    assert "second wording" in open(C.ANSWERS).read() and os.path.getmtime(C.ANSWERS) == 1_000_000_000
    assert Y.entry_of(_doc(), "x")["answer"] == "kept"
    assert os.path.exists(os.path.join(C.DATA, "answers.yaml.previous"))


def test_the_signature_is_wave_1_or_full_plan(tmp_path):
    _setup(tmp_path)
    Y.register("t", [])
    assert not Y.signature()["signed"]
    Y.set_signature("full")
    assert _doc()["signed_for"] == "full plan"
    assert Y.signature()["signed"] and Y.signature()["scope"] == "full"
    Y.set_signature("wave 1")
    assert Y.signature()["scope"] == "wave-1"
    Y.set_signature("")
    assert not Y.signature()["signed"]


def test_a_file_in_the_old_format_is_carried_over(tmp_path):
    _setup(tmp_path)
    Y.register("t", [Y.item("a", "answer", "A?"), Y.item("b", "confirm", "B?", suggested="one"),
                     Y.item("c", "confirm", "C?", suggested="one"), Y.item("d", "confirm", "D?", suggested="one"),
                     Y.item("spec.w", "review", "Read the spec. Is it right?", fixed={"spec": "f1"})])
    old = {"answered_by": "demo customer, stand-in", "date": "2026-10-01",
           "blocking": {"a": {"answer": "", "note": "a note on an open item"}},
           "done": {"b": {"answer": "one", "confirmed": "yes", "by": "x", "date": "2026-10-01"},
                    "c": {"answer": "two", "confirmed": "no", "note": "changed"},
                    "spec.w": {"answer": "yes", "spec": "f0", "by": "x"}},
           "open": {"d": {"answer": "one", "confirmed": "no"}},
           "no_longer_asked": {"gone": {"answer": "kept anyway"}},
           "sign": {"signed_by": "an owner", "date": "2026-10-01", "covers": "wave-1"}}
    open(C.ANSWERS, "w").write(yaml.safe_dump(old, sort_keys=False))
    before = open(C.ANSWERS).read()
    Y.write()
    d = _doc()
    f = Y.flat(d)
    assert f["b"]["answer"] == "one" and f["c"]["answer"] == "two" and f["gone"]["answer"] == "kept anyway"
    assert f["d"].get("answer") is None and f["d"]["suggested"] == "one"  # the tool's pre-fill, never confirmed
    assert f["a"]["answer"] is None and set(f["a"]) == {"question", "answer"}
    assert d["signed_for"] == "wave 1"
    assert open(os.path.join(C.DATA, "answers.yaml.before-2g")).read() == before
    assert Y.load()["stand_in"]
    # the review was written for spec f0, and the spec is f1 now: stale until the answer changes
    st = Y.state("spec.w", Y.load(), {"group": "review", "fixed": {"spec": "f1"}})
    assert st["state"] == "stale"
    assert Y.section_of(d, "spec.w") == "open" and f["spec.w"]["answer"] == "yes"


def test_corrections_yaml_is_read_but_never_created(tmp_path):
    from kestra_migration_assistant.vra.stages import corrections as CO
    _setup(tmp_path)
    assert CO.load() == {} and not os.path.exists(C.CORRECTIONS)
    C.ensure(C.INPUT)
    open(C.CORRECTIONS, "w").write(CO.TEMPLATE)                     # the blank template an earlier version wrote
    assert CO.load() == {} and not os.path.exists(C.CORRECTIONS)
    assert os.path.exists(os.path.join(C.DATA, "corrections.yaml.blank"))
    open(C.CORRECTIONS, "w").write(CO.TEMPLATE.replace("reach: {}", "reach: {avi: plugin}"))
    assert CO.load()["targets"]["reach"] == {"avi": "plugin"} and os.path.exists(C.CORRECTIONS)


def test_the_recommendation_counts_only_client_code_as_client_code():
    from kestra_migration_assistant.vra.stages import brief as B
    wf = {f"w{i}": {"cluster": "C1"} for i in range(4)}
    tri = {u: {"outcome": "Convert", "evidence": {}} for u in wf}
    acts = [{"key": "client", "lines": 10, "library_for": ["sys"]}, {"key": "state", "lines": 90, "library_for": []}]
    atri = {"client": {"outcome": "Rebuild", "basis": "client"}, "state": {"outcome": "Rebuild", "basis": "vro-state"}}
    assess = {"workflows": wf, "actions": acts, "clusters": [{"size": 4}],
              "counts": {"script_lines_workflows": 0, "script_lines_actions": 100},
              "targets": {"sys": {"name": "Sys", "reach": "http"}}, "domains": {"domains": [], "cluster_domain": {"C1": "D1"}}}
    r = B.recommend(assess, tri, atri, {"open": [], "items": []},
                    {"share_elements": 0.9, "share_lines": 0.9})
    assert r["client_share"] == 0.1                      # the vRO-state action is not a client
    assert r["verdict"] == "convert" and "90% of the elements" in r["produced_by"]
    r = B.recommend(assess, tri, atri, {"open": [], "items": []}, {"share_elements": 0.6, "share_lines": 0.9})
    assert r["verdict"] == "mix" and "only 60% of the elements" in r["produced_by"]
