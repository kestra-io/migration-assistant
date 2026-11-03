"""What the tool assumes after the analysis instead of asking (stages/defaults.py), on the sorting call's reply."""
from __future__ import annotations

import pytest


def _items():
    from kestra_migration_assistant.vra.stages import your_input as Y
    return [
        Y.item("question.wf-a.error-semantics", "answer", "WF A: If the POST fails, should the run fail?",
               family="question"),
        Y.item("question.wf-a.data-shape", "answer", "WF A: Is owner always an email address like <EMAIL-01>?",
               family="question"),
        Y.item("question.wf-a.scope", "answer", "WF A: Which vCenters does it target?", family="question"),
        Y.item("question.wf-a.routing", "answer", "WF A: Should size large be sized or rejected?", family="question"),
        Y.item("defect.wf-a.other", "confirm", "WF A: workNotes is undefined (item3). Keep or fix?", family="defect"),
        Y.item("defect.wf-a.logs-password", "confirm", "WF A: the password is logged (item2). Keep or fix?",
               family="defect"),
    ]


@pytest.fixture
def sorted_items(monkeypatch):
    from kestra_migration_assistant.vra import model as M
    from kestra_migration_assistant.vra.stages import common as C
    from kestra_migration_assistant.vra.stages import defaults as D
    reply = {"questions": [
        {"id": "question.wf-a.error-semantics", "kind": "ask", "today": "", "question": "If the POST fails?",
         "options": ["fail the run, as it does now", "log and go on"], "why_kestra": "Kestra needs it written."},
        {"id": "question.wf-a.data-shape", "kind": "ask", "today": "", "question": "Is it an email address?",
         "options": ["yes", "no"], "why_kestra": ""},
        {"id": "question.wf-a.scope", "kind": "wave", "today": "", "question": "", "options": [], "why_kestra": ""},
        {"id": "question.wf-a.routing", "kind": "ask", "today": "", "question": "Size large: size it or reject it?",
         "options": ["reject it", "size it as 8 vCPU"], "why_kestra": ""}],
        "defects": [
        {"id": "defect.wf-a.other", "today": "The ServiceNow request is closed without its work notes.",
         "always_fails": False, "security": False},
        {"id": "defect.wf-a.logs-password", "today": "The ServiceNow password is written to the run's log.",
         "always_fails": False, "security": True}]}
    monkeypatch.delenv("VRO_NO_MODEL", raising=False)
    monkeypatch.setattr(C, "model", lambda: M)
    monkeypatch.setattr(C, "ESTATE", "test")
    monkeypatch.setattr(M, "ask", lambda *a, **k: reply)
    its = _items()
    D.sort(its, {"u": {"short": "wf-a", "name": "WF A"}})
    return {it["key"]: it for it in its}


def test_a_question_whose_suggestion_keeps_today_is_assumed_not_asked(sorted_items):
    from kestra_migration_assistant.vra.stages import defaults as D
    s = sorted_items["question.wf-a.error-semantics"]["sort"]
    assert s["kind"] == "today" and D._behavior({}, sorted_items["question.wf-a.error-semantics"]).startswith(
        "as today: fail the run, as it does now")
    # a real choice stays a question, with its choices and the suggestion first
    r = sorted_items["question.wf-a.routing"]
    assert r["sort"]["kind"] == "ask" and r["options"] == ["reject it", "size it as 8 vCPU"]


def test_never_ask_what_a_placeholder_stands_for(sorted_items):
    from kestra_migration_assistant.vra.stages import defaults as D
    it = sorted_items["question.wf-a.data-shape"]
    assert it["sort"]["kind"] == "placeholder" and D._placeholders({}, it)


def test_a_conversion_detail_waits_for_its_wave(sorted_items):
    from kestra_migration_assistant.vra.stages import defaults as D
    it = sorted_items["question.wf-a.scope"]
    D.wave_of([it], {"waves": [{"wave": 2, "workflows": ["u"]}]}, {"u": {"short": "wf-a", "name": "WF A"}})
    assert D.waits_for_wave(it) and it["wave"] == 2


def test_a_defect_is_kept_and_described_by_what_it_does_unless_it_is_a_security_problem(sorted_items):
    from kestra_migration_assistant.vra.stages import defaults as D
    kept, asked = sorted_items["defect.wf-a.other"], sorted_items["defect.wf-a.logs-password"]
    assert kept["question"] == "The ServiceNow request is closed without its work notes. Keep it as today, or fix it?"
    assert D._defects({}, kept).startswith("keep") and not D.asked(kept)
    # a security problem is kept too (the run never asks), and flagged: the brief lists it first, as a note
    assert D.asked(asked) and D._defects({}, asked).startswith("keep") and asked["suggested"] == "keep it, as today"


def test_without_a_model_the_rules_sort_them(monkeypatch):
    from kestra_migration_assistant.vra.stages import defaults as D
    monkeypatch.setenv("VRO_NO_MODEL", "1")
    its = {it["key"]: it for it in _items()}
    D.sort(list(its.values()), {"u": {"short": "wf-a", "name": "WF A"}})
    assert its["question.wf-a.error-semantics"]["sort"]["kind"] == "today"
    assert its["question.wf-a.data-shape"]["sort"]["kind"] == "placeholder"
    assert its["question.wf-a.scope"]["sort"]["kind"] == "wave"
    assert D.asked(its["defect.wf-a.logs-password"]) and not D.asked(its["defect.wf-a.other"])
