"""
Groups in answers.yaml (stages/your_input.py): what a person settles together is one entry, and every stage still
reads each item by its own id.

- `suggested`: one `yes` takes every suggested value; any one can still be given its own answer.
- `defects.<workflow>`: keep them all, fix them all, or decide each; the defects are listed only after `decide each`.
- `specs`: yes, or name the workflow that is wrong.
- `endpoints`, `questions`, `about-the-estate`: gathered, answered one by one.

A group's answer counts for the members the file showed when it was given: a defect found later is asked on its
own, and the answer to a defect that goes is listed as no longer asked.
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


def _top(d, sec="open"):
    return [e["id"] for e in d.get(sec) or []]


def _defect(k, what, wave=True):
    return Y.item(k, "answer", f"W - Create: {what} (evidence). Keep it?", family="defect", options=["keep", "fix"],
                  unblocks=["Wave 1: defects"] if wave else [],
                  question=f"Possible defect in Create: {what}. Keep the current behavior, or fix it?")


def _suggestions():
    return [Y.item("tls.a", "confirm", "Check A's certificate?", suggested="check it", family="tls",
                   unblocks=["Wave 1: tls"]),
            Y.item("tls.b", "confirm", "Check B's certificate?", suggested="check it", family="tls"),
            Y.item("timeout.x", "confirm", "Timeout of x?", suggested="10 min", family="timeout")]


def _st(k):
    return Y.state(k, Y.load())


def test_one_yes_takes_every_suggestion_and_one_can_still_be_changed(tmp_path):
    _setup(tmp_path)
    Y.register("brief", _suggestions())
    d = _doc()
    assert _top(d) == ["suggested"]
    g = d["open"][0]
    assert g["answer"] is None and [m["id"] for m in g["each"]] == ["tls.a", "tls.b", "timeout.x"]
    assert all("answer" not in m and m["suggested"] for m in g["each"])        # nothing to fill in per member
    Y.set_answer("suggested", "yes")
    Y.set_answer("timeout.x", "15 min")                                        # added under the one to change
    Y.register("brief", _suggestions())
    assert _st("tls.a")["answer"] == "check it" and _st("tls.b")["state"] == "answered"
    assert _st("timeout.x")["answer"] == "15 min"
    d = _doc()
    assert _top(d) == [] and _top(d, "answered") == ["suggested"]
    # a suggestion asked later is not covered by the earlier yes: it is asked on its own
    Y.register("brief", [*_suggestions(), Y.item("auth.a", "confirm", "Sign in to A?", suggested="basic")])
    assert _top(_doc()) == ["auth.a"] and _st("auth.a")["state"] == "open"
    # and what the yes took does not change when the suggestion does
    its = _suggestions()
    its[0]["suggested"] = "do not check"
    Y.register("brief", its)
    assert _st("tls.a")["answer"] == "check it"


def test_defects_keep_all_fix_all_or_decide_each(tmp_path):
    _setup(tmp_path)
    ds = [_defect("defect.w-create.render.tls", "certificates are not checked"),
          _defect("defect.w-create.save.race", "two runs can save at once"),
          _defect("defect.w-create.name.name-collision", "names collide")]
    Y.register("brief", ds)
    d = _doc()
    assert _top(d) == ["defects.w-create"]
    assert d["open"][0]["question"].startswith("3 possible defects in W - Create: keep them all, fix them all, or "
                                               "decide each?")
    assert "each" not in d["open"][0]                                         # listed only after decide each
    Y.set_answer("defects.w-create", "fix all")
    Y.register("brief", ds)
    assert all(_st(x["key"])["answer"] == "fix" for x in ds)                 # what the gates read for each one
    assert _top(_doc(), "answered") == ["defects.w-create"]
    Y.set_answer("defects.w-create", "decide each")
    Y.register("brief", ds)
    d = _doc()
    assert _top(d) == ["defects.w-create"]
    each = d["open"][0]["each"]
    assert [m["id"] for m in each] == [x["key"] for x in ds] and all("answer" in m for m in each)
    assert each[0]["question"] == "Certificates are not checked. Keep the current behavior, or fix it?"
    assert all(_st(x["key"])["state"] == "open" for x in ds)
    for x, a in zip(ds, ("keep", "fix", "keep"), strict=True):
        Y.set_answer(x["key"], a)
    Y.register("brief", ds)
    assert [_st(x["key"])["answer"] for x in ds] == ["keep", "fix", "keep"]
    assert _top(_doc()) == []
    # the race goes on a re-run, a new defect appears: the race's answer is no longer asked, the new one is asked
    later = [ds[0], ds[2], _defect("defect.w-create.render.masked-error", "a 400 is taken for success")]
    Y.register("brief", later)
    d = _doc()
    assert Y.section_of(d, "defect.w-create.save.race") == "no_longer_asked"
    assert Y.entry_of(d, "defect.w-create.save.race")["answer"] == "fix"
    assert _st("defect.w-create.render.masked-error")["state"] == "open"


def test_a_defect_found_after_keep_all_is_asked_on_its_own(tmp_path):
    _setup(tmp_path)
    ds = [_defect("defect.w-create.a.race", "a"), _defect("defect.w-create.b.timing", "b")]
    Y.register("brief", ds)
    Y.set_answer("defects.w-create", "keep all")
    Y.register("brief", ds)
    Y.register("brief", [*ds, _defect("defect.w-create.c.tls", "c")])
    d = _doc()
    assert _top(d) == ["defect.w-create.c.tls"] and _top(d, "answered") == ["defects.w-create"]
    assert _st("defect.w-create.c.tls")["state"] == "open" and _st("defect.w-create.a.race")["answer"] == "keep"


def test_one_answer_reviews_every_spec_and_a_named_workflow_keeps_the_remark(tmp_path):
    _setup(tmp_path)
    specs = []
    for short, name in (("w-create", "W - Create"), ("w-delete", "W - Delete")):
        it = Y.item(f"spec.{short}", "review", f"Read the spec of {name}. Is it right?", family="spec",
                    fixed={"spec": "f1"})
        it["workflow"] = name
        specs.append(it)
    Y.register("brief", specs)
    d = _doc()
    assert _top(d) == ["specs"] and "W - Create, W - Delete" in d["open"][0]["question"]
    Y.set_answer("specs", "Delete: it also removes the DNS record")
    Y.register("brief", specs)
    assert _st("spec.w-create")["state"] == "reviewed" and _st("spec.w-create")["answer"] == "yes"
    assert _st("spec.w-delete")["answer"] == "Delete: it also removes the DNS record"
    # a spec regenerated since is asked again, on its own
    specs[0]["fixed"] = {"spec": "f2"}
    Y.register("brief", specs)
    assert "spec.w-create" in _top(_doc())


def test_gathered_groups_are_answered_one_by_one(tmp_path):
    _setup(tmp_path)
    its = [Y.item("q1-scope", "answer", "Q1. Whole estate?", family="preflight"),
           Y.item("q4-off-limits", "answer", "Q4. Off limits?", family="preflight", unblocks=["Wave 1: q4"]),
           Y.item("q9-vra-front-end", "answer", "Q9. Keep vRA?", family="preflight")]
    Y.register("preflight", its)
    d = _doc()
    assert _top(d) == ["about-the-estate", "q9-vra-front-end"]                # Q9, the big decision, alone
    g = d["open"][0]
    assert "answer" not in g and [m["id"] for m in g["each"]] == ["q1-scope", "q4-off-limits"]
    Y.set_answer("q1-scope", "yes")
    Y.register("preflight", its)
    assert _top(_doc()) == ["about-the-estate", "q9-vra-front-end"]
    Y.set_answer("q4-off-limits", "none")
    Y.register("preflight", its)
    assert _top(_doc(), "answered") == ["about-the-estate"]


def test_the_status_line_counts_entries_not_items(tmp_path):
    _setup(tmp_path)
    Y.register("brief", [*_suggestions(), _defect("defect.w-create.a.race", "a"), _defect("defect.w-create.b.timing", "b")])
    shown = Y.shown()
    assert [x["id"] for x in shown] == ["suggested", "defects.w-create"]
    assert all(x["blocks"] for x in shown) and len(shown[0]["members"]) == 3
