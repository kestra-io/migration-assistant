"""
The interactive run (1.2.0), driven with simulated terminal input, on the review/ conformance tree (three workflows
with their specs, no model):

- typed in a terminal, `run` asks the estate interview, then the analysis runs, then the rounds; each answer is
  saved at once and says what it settled; s skips, q saves and stops;
- called by a program, run without a terminal, or with --batch, it never waits for input;
- the same answers.yaml run again in batch mode in a fresh folder gives the same brief;
- the brief's counts are the file's, counted from the file itself;
- the re-planning reply is validated and only what refers to open questions and given answers is applied; a
  changed answer reopens what it closed.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil

import pytest

CONFORMANCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conformance")


def _export(tmp, name: str = "review") -> str:
    """An export folder of the review tree, with its specs already on record (so no model is needed)."""
    folder = tmp / name
    shutil.copytree(os.path.join(CONFORMANCE, "review", "vro"), folder / "review")
    shutil.copytree(os.path.join(CONFORMANCE, "review", "vra"), folder / "review-vra")
    spec_dir = folder / "kestra-assessment" / "data" / "2-understand"
    spec_dir.mkdir(parents=True)
    for f in os.listdir(os.path.join(CONFORMANCE, "review", "specs")):
        shutil.copy(os.path.join(CONFORMANCE, "review", "specs", f), spec_dir / f)
    return str(folder)


class NoInput(io.StringIO):
    def readline(self, *a):
        raise AssertionError("the run waited for input")


def _run(monkeypatch, export: str, typed: str | None, *args: str, terminal: bool = True) -> tuple[int, str]:
    """`kestra-migrate vra run` as a person types it (interactive) with `typed` as what they type; or, with typed
    None, as a program calls it."""
    import sys

    from kestra_migration_assistant.vra import cli
    out = io.StringIO()
    monkeypatch.setattr(cli, "is_terminal", lambda: terminal)
    monkeypatch.setattr(sys, "stdin", io.StringIO(typed) if typed is not None else NoInput())
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setenv("VRO_NO_MODEL", "1")
    code = cli.main(["run", export, "--no-model", *args], interactive=typed is not None)
    return code, out.getvalue()


def _answers(export: str) -> dict:
    from kestra_migration_assistant.vra.stages import your_input as Y
    return Y._yaml(open(os.path.join(export, "kestra-assessment", "your-input", "answers.yaml"), encoding="utf-8").read())


def _brief(export: str) -> dict:
    return json.load(open(os.path.join(export, "kestra-assessment", "data", "3-brief", "brief.json")))


def _open_lines(doc: dict) -> int:
    """The empty answer lines of a parsed answers.yaml, counted from the file alone."""
    from kestra_migration_assistant.vra.stages import your_input as Y
    n = 0
    for e in doc.get("estate") or []:
        n += str(e["id"]).startswith("estate.") and "answer" in e and Y.is_open(e.get("answer"))
    for e in doc.get("open") or []:
        n += "answer" in e and Y.is_open(e.get("answer"))
        n += sum(1 for m in e.get("each") or [] if "answer" in m and Y.is_open(m.get("answer")))
    return n


def test_a_program_calling_main_never_waits_for_input(tmp_path, monkeypatch):
    export = _export(tmp_path)
    assert _run(monkeypatch, export, None)[0] == 0                     # called by a program
    export2 = _export(tmp_path, "review2")
    import sys

    from kestra_migration_assistant.vra import cli
    monkeypatch.setattr(sys, "stdin", NoInput())
    monkeypatch.setattr(cli, "is_terminal", lambda: False)            # typed, but no terminal attached
    monkeypatch.setenv("VRO_NO_MODEL", "1")
    assert cli.main(["run", export2, "--no-model"], interactive=True) == 0
    monkeypatch.setattr(cli, "is_terminal", lambda: True)             # a terminal, and --batch
    assert cli.main(["run", export2, "--no-model", "--batch"], interactive=True) == 0


def _estate_questions(tmp_path, monkeypatch) -> int:
    probe = _export(tmp_path, "probe")
    _run(monkeypatch, probe, None)
    return sum(1 for e in _answers(probe)["estate"] if e["id"].startswith("estate."))


def test_the_interactive_run_asks_the_estate_then_rounds_and_saves_every_answer(tmp_path, monkeypatch):
    n = _estate_questions(tmp_path, monkeypatch)
    export = _export(tmp_path)
    # Enter takes each estate question's suggestion, then round 1: Enter, 1, s, then q
    typed = "\n" * n + "\n1\ns\nq\n"
    code, out = _run(monkeypatch, export, typed)
    assert code == 0
    assert out.index("About the estate") < out.index("Understand") < out.index("Round 1")
    assert "None of these, enter my own answer" in out and "(suggested)" in out
    assert re.search(r"Choose 1-\d+, Enter for 1, s to skip, q to save and quit", out)
    assert "Skipped: it stays open in answers.yaml" in out and "Saved and stopped." in out
    doc = _answers(export)
    est = {e["id"]: e for e in doc["estate"]}
    answered = [k for k, e in est.items() if k.startswith("estate.") and e.get("answer")]
    assert answered and all(est[k]["answer"] == est[k]["suggested"] for k in answered)
    # the estate answers settled what they could, listed under each
    assert any(est[k].get("closes") for k in answered)
    assert re.search(r"This answer settled \d+ questions", out)
    # the brief that the run ends with counts what the file holds
    assert _brief(export)["questions"]["open"] == _open_lines(doc)


def test_a_question_in_the_terminal_is_numbered_choices_ending_with_the_persons_own(monkeypatch):
    from kestra_migration_assistant.vra import interactive
    out = io.StringIO()
    t = interactive.Terminal(io.StringIO("9\nx\n3\nmy own words\n"), out)
    q = {"id": "estate.x", "question": "Where do settings live?", "options": ["the KV store", "variables"],
         "default": "the KV store", "settles": 12}
    assert t.pose(q, "Question 1 of 1") == "my own words"
    text = out.getvalue()
    assert "(settles 12 questions)" in text and "1. the KV store (suggested)" in text and "2. variables" in text
    assert "3. None of these, enter my own answer" in text and text.count("Type a number from 1 to 3") == 2
    assert interactive.Terminal(io.StringIO("\n"), io.StringIO()).pose(q, "") == "the KV store"
    assert interactive.Terminal(io.StringIO("s\n"), io.StringIO()).pose(q, "") is None
    with pytest.raises(interactive.Quit):
        interactive.Terminal(io.StringIO("q\n"), io.StringIO()).pose(q, "")
    with pytest.raises(interactive.Quit):                             # end of input saves and stops
        interactive.Terminal(io.StringIO(""), io.StringIO()).pose(q, "")


def test_the_same_answers_replayed_in_batch_give_the_same_brief(tmp_path, monkeypatch):
    n = _estate_questions(tmp_path, monkeypatch)
    first = _export(tmp_path, "alphaxq")
    _run(monkeypatch, first, "\n" * n + "1\n1\n1\nq\n")
    again = _export(tmp_path, "betaxq")
    os.makedirs(os.path.join(again, "kestra-assessment", "your-input"))
    shutil.copy(os.path.join(first, "kestra-assessment", "your-input", "answers.yaml"),
                os.path.join(again, "kestra-assessment", "your-input", "answers.yaml"))
    assert _run(monkeypatch, again, None, "--batch")[0] == 0
    a, b = _brief(first), _brief(again)
    for k in ("counts", "action_counts", "questions", "estate_answers", "assumptions", "package_additions",
              "recommendation"):
        assert json.dumps(a[k], sort_keys=True).replace("alphaxq", "X") == \
            json.dumps(b[k], sort_keys=True).replace("betaxq", "X"), k
    assert [w["workflows"] for w in a["plan"]["waves"]] == [w["workflows"] for w in b["plan"]["waves"]]
    assert {u: t["outcome"] for u, t in a["triage"].items()} == {u: t["outcome"] for u, t in b["triage"].items()}
    assert a["input"]["shown"] == b["input"]["shown"]


def test_the_brief_counts_are_the_files_counts(tmp_path, monkeypatch):
    export = _export(tmp_path)
    _run(monkeypatch, export, None)
    from kestra_migration_assistant.vra.stages import status
    for answer in (None, "keep all", "decide each"):
        if answer:
            from kestra_migration_assistant.vra.stages import your_input as Y
            gid = next(x["id"] for x in _brief(export)["input"]["shown"] if "defect" in x["id"])
            Y.set_answer(gid, answer)
            _run(monkeypatch, export, None)
        b, doc = _brief(export), _answers(export)
        n = _open_lines(doc)
        assert b["questions"]["open"] == n == sum(x["lines"] for x in b["input"]["shown"])
        st = status.status_of(b)
        assert st["open"] == n
        m = re.search(r"answer (\d+) questions?", st["line"])
        assert not m or int(m.group(1)) <= n


def test_the_re_planning_reply_is_checked_and_a_changed_answer_reopens_what_it_closed(tmp_path, monkeypatch):
    export = _export(tmp_path)
    _run(monkeypatch, export, None)
    from kestra_migration_assistant import config as K
    from kestra_migration_assistant.vra import model as M
    from kestra_migration_assistant.vra.stages import planner
    from kestra_migration_assistant.vra.stages import your_input as Y
    qs = [q for q in planner.questions() if not q.get("estate")]
    groups = Y.load()["groups"]
    plain = [q["id"] for q in qs if q["id"] not in groups]
    assert qs and len(plain) >= 2
    by, a, b = qs[0]["id"], plain[-1], plain[-2]
    Y.set_answer(by, "keep all")
    Y.write()
    seen = {}

    def fake_ask(purpose, wid, prompt, schema, **kw):
        seen["prompt"], seen["schema"] = prompt, schema
        return {"close": [{"id": a, "by": by, "answer": "as for the first", "why": "same thing"},
                          {"id": "invented.id", "by": by, "answer": "x", "why": "y"},
                          {"id": b, "by": "not.answered", "answer": "x", "why": "y"}],
                "merge": [], "reword": [{"id": b, "question": "A clearer question — with a dash?"}],
                "add": [{"about": "change window", "question": "When can changes run?", "options": ["nights"],
                         "why": "raised by the answer"}]}
    monkeypatch.setattr(M, "ask", fake_ask)
    monkeypatch.setattr(M, "settings", lambda: K.Settings(provider="claude-code", model="m"))
    r = planner.replan([{"id": by, "question": qs[0]["question"], "answer": "keep all, ask admin@corp.example.com"}],
                       use_model=True)
    assert r["model"] and r["closed"] >= 1 and r["reworded"] == 1 and r["added"] == 1
    assert "admin@corp.example.com" not in seen["prompt"] and "<ANSWER-EMAIL-01>" in seen["prompt"]
    assert seen["schema"] is planner.REPLAN_SCHEMA and seen["prompt"].startswith(M.safety.NOTICE)
    assert M.validate({"close": [{"id": 1}], "merge": [], "reword": [], "add": []}, planner.REPLAN_SCHEMA)
    loaded = Y.load()
    assert loaded["closed"][a]["by"] == by and b not in loaded["closed"] and "invented.id" not in loaded["closed"]
    assert "—" not in Y.registry()["_rewords"][b]
    assert "added.change-window" in Y.load()["items"]
    doc = _answers(export)
    closer = next(e for sec in ("open", "answered") for e in doc.get(sec) or [] if e["id"] == by)
    assert a in closer["closes"]
    # the answer that closed it changes: it is asked again
    Y.set_answer(by, "fix all")
    Y.write()
    assert a not in Y.load()["closed"]
