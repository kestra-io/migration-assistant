"""
The interactive run (1.2.0), driven with simulated terminal input, on the review/ conformance tree (three workflows
with their specs, no model):

- typed in a terminal, `run` says what it read and how to stop, asks the estate interview, runs the analysis, then
  asks what the plan needs; each answer is saved at once; s skips, q saves and stops; it never shows a count it keeps
  behind the scenes;
- a question whose choices are not exclusive takes several numbers; a yes-or-no question offers yes and no; a
  blanket answer settles every question of its kind at once;
- Ctrl+C stops the model calls in flight at once and keeps what is written;
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


HIDDEN = re.compile(r"settles? \d+|settled by other|questions? to answer|Round \d|settle the most|Saved\. |"
                    r"Re-planned|assumptions?\b|accept every suggested answer below|Choose 1-1\b", re.I)


def test_the_interactive_run_says_what_it_read_asks_the_interview_then_what_the_plan_needs(tmp_path, monkeypatch):
    n = _estate_questions(tmp_path, monkeypatch)
    export = _export(tmp_path)
    typed = "\n" * n + "1\n" * 40              # Enter takes a suggestion; a question without one takes a number
    code, out = _run(monkeypatch, export, typed)
    assert code == 0
    # the first screen: what it read, that it only reads, how to stop, then the interview in one short line
    assert "Read:" in out and re.search(r"- review\S*: \d+ workflows?, \d+ actions?", out)
    assert re.search(r"- review-vra\S*: \d+ catalog items?", out)
    assert "The assessment only reads these files: it never connects to or runs anything on your systems" in out
    assert "Press Ctrl+C to stop at any time. Finished work is kept." in out
    assert "estate:" not in out and "answers.yaml" not in out.split("Question 1 of", 1)[0]
    interview = re.search(r"Before the analysis, (\d+) questions? about your estate\.", out)
    assert interview and out.index("Press Ctrl+C") < interview.start()
    told = re.search(r"After the analysis, (\d+) questions?\.", out)
    assert told and interview.start() < told.start()
    # every question it asks is one it said it would: never "of" more than it said
    after = out[told.end():]
    asked = [(int(i), int(k)) for i, k in re.findall(r"Question (\d+) of (\d+)", after)]
    assert asked and all(k <= int(told.group(1)) for _i, k in asked)
    assert [i for i, _k in asked] == list(range(1, len(asked) + 1))
    # nothing it counts behind the scenes, no rounds, no "Saved" after each answer
    assert not HIDDEN.search(out), HIDDEN.search(out)
    doc = _answers(export)
    est = {e["id"]: e for e in doc["estate"]}
    answered = [k for k, e in est.items() if k.startswith("estate.") and e.get("answer")]
    assert answered and all(est[k]["answer"] == est[k]["suggested"] for k in answered)
    assert _brief(export)["questions"]["open"] == _open_lines(doc)


def test_the_interview_asks_only_what_the_analysis_reads(tmp_path, monkeypatch):
    """Whether there is a non-production instance, and whether to check certificates, only the plan needs: they are
    asked after the analysis, explained."""
    export = _export(tmp_path)
    code, out = _run(monkeypatch, export, "1\n" * 40)
    before, after = out.split("After the analysis", 1)
    assert "non-production" not in before and "Converted flows are tested in a simulated lab first" in after
    assert "checked against production, with care" not in out


def test_a_question_whose_choices_are_not_exclusive_takes_several_numbers(tmp_path, monkeypatch):
    from kestra_migration_assistant.vra.stages import interview
    from kestra_migration_assistant.vra.stages import your_input as Y
    export = _export(tmp_path)
    code, out = _run(monkeypatch, export, "2, 4\n" + "1\n" * 40)
    assert "Choose one or more, like 2, 4; Enter for 1, s to skip" in out
    a = Y._text(Y.load()["entries"]["estate.started-by"]["answer"])
    assert a == "an external platform or scripts, through the vRO API; an ITSM such as ServiceNow"
    it = Y.load()["items"]["estate.started-by"]
    assert interview.picks(it, a) == {2, 4}
    assert interview.started_by()["all"]          # the vRO API can start any workflow: history decides what is unused
    # in the file, numbers work too
    assert interview.option_text(it, "2, 3") == ("an external platform or scripts, through the vRO API; vRO schedules "
                                                 "or policies")


def test_a_blanket_answer_settles_every_question_of_its_kind_at_once(tmp_path, monkeypatch):
    """Possible defects that are a security problem are asked; keeping one "and every other" keeps them all, the rest
    of the list included, and the next question is not about a defect."""
    from kestra_migration_assistant.vra import interactive
    from kestra_migration_assistant.vra.stages import defaults
    from kestra_migration_assistant.vra.stages import your_input as Y
    real = defaults.fallback
    monkeypatch.setattr(defaults, "fallback", lambda it: {**real(it), "security": True}
                        if it.get("family") == "defect" else real(it))
    export = _export(tmp_path)
    _run(monkeypatch, export, None)
    defects = [it["key"] for it in Y.items() if it.get("family") == "defect"]
    assert len(defects) >= 2 and all(Y.state(k)["state"] == "open" for k in defects)
    qs = [q for q in interactive._plan_questions(False, set()) if q.get("defect")]
    assert qs
    # in the terminal: the first defect question offers to keep every one; nothing about a defect is asked after it
    out = io.StringIO()

    class Person(io.StringIO):
        def readline(self, *a):
            shown = out.getvalue().rsplit("Question ", 1)[-1]
            m = re.search(r"(\d+)\. keep this and every other possible defect asked here", shown)
            return (m.group(1) if m else "s") + "\n"
    interactive.after_analysis(interactive.Terminal(Person(), out), False, lambda: None)
    text = out.getvalue()
    first = text.index("keep this and every other possible defect asked here")
    assert "possible defect" not in text[first + 60:].lower() and "Keep it as today" not in text[first + 60:]
    assert all(Y.state(k)["answer"].startswith("keep") for k in defects)


def test_a_question_in_the_terminal_is_numbered_choices_ending_with_the_persons_own(monkeypatch):
    from kestra_migration_assistant.vra import interactive
    out = io.StringIO()
    t = interactive.Terminal(io.StringIO("9\nx\n3\nmy own words\n"), out)
    q = {"id": "estate.x", "question": "Where do settings live?", "options": ["the KV store", "variables"],
         "default": "the KV store", "about": ["Create DNS Record", "Delete DNS Record"]}
    assert t.pose(q, "Question 1 of 1") == "my own words"
    text = out.getvalue()
    assert "Question 1 of 1, affects 2 workflows" in text and "1. the KV store (suggested)" in text
    assert "3. None of these, enter my own answer" in text and text.count("Type a number from 1 to 3") == 2
    assert interactive.Terminal(io.StringIO("\n"), io.StringIO()).pose(q, "") == "the KV store"
    assert interactive.Terminal(io.StringIO("s\n"), io.StringIO()).pose(q, "") is None
    with pytest.raises(interactive.Quit):
        interactive.Terminal(io.StringIO("q\n"), io.StringIO()).pose(q, "")
    with pytest.raises(interactive.Quit):                             # end of input saves and stops
        interactive.Terminal(io.StringIO(""), io.StringIO()).pose(q, "")
    # one workflow: named in the header, and not again in the question
    one = interactive._strip_name({**q, "question": "Create DNS Record: should it retry?", "about": ["Create DNS Record"]})
    out = io.StringIO()
    interactive.Terminal(io.StringIO("1\n"), out).pose(one, "Question 2 of 3")
    assert "Question 2 of 3, about Create DNS Record\n  Should it retry?" in out.getvalue()


def test_every_yes_or_no_question_offers_yes_and_no_and_none_has_one_choice(tmp_path, monkeypatch):
    from kestra_migration_assistant.vra import interactive
    from kestra_migration_assistant.vra.stages import planner
    assert planner._yes_no(["yes"]) == ["yes", "no"] and planner._yes_no(["no"]) == ["yes", "no"]
    sug_only = {"key": "q8-plugin-inventory", "family": "preflight", "suggested": "Mail, SSH", "options": []}
    opts, default = planner._options_of(sug_only, None, {}, {})
    assert opts == ["yes", "no, enter my own answer"] and default == "yes"
    out = io.StringIO()
    assert interactive.Terminal(io.StringIO("2\nMail only\n"), out).pose(
        {"question": "Is that the list?", "options": opts, "default": default}, "Q") == "Mail only"
    assert "None of these" not in out.getvalue()                      # "no" takes the person's own words
    # a question with no choices takes words, never "Choose 1-1"
    out = io.StringIO()
    assert interactive.Terminal(io.StringIO("the platform team\n"), out).pose(
        {"question": "Who owns each area?", "options": []}, "Q") == "the platform team"
    assert "Choose" not in out.getvalue() and "Your answer (Enter to skip" in out.getvalue()
    export = _export(tmp_path)
    _run(monkeypatch, export, None)
    for q in planner.questions():
        assert len(q["options"]) != 1, q
        if [o.lower() for o in q["options"]][:1] in (["yes"], ["no"]):
            assert {"yes", "no"} <= {o.lower().split(",")[0] for o in q["options"]}, q


def test_interactive_mode_asks_before_any_billed_call_whatever_the_estimate():
    from kestra_migration_assistant import config as K
    from kestra_migration_assistant.vra import model as M
    s = K.Settings("claude-code", model="claude-haiku-5-5", max_cost_usd=20)
    asked: list[str] = []
    calls = [{"purpose": "understand", "chars": 30000}] * 3
    g = M.Gate(lambda cur: calls, lambda m: None, confirm=lambda q: asked.append(q) or False, always=True)
    with pytest.raises(M.CostNotApproved):
        g.before_billed("understand", "p", {}, {}, s)
    assert asked and asked[0].startswith("This will make 3 model calls, about $") and "Continue?" in asked[0]
    g = M.Gate(lambda cur: calls, lambda m: None, confirm=lambda q: True, always=True)
    g.before_billed("understand", "p", {}, {}, s)                   # yes: goes on


def test_ctrl_c_ends_the_calls_in_flight_at_once_and_starts_no_other():
    """Each call is a process of its own: Ctrl+C ends those running, the jobs not started never start, and no call
    starts after it."""
    import signal
    import subprocess
    import threading
    import time

    from kestra_migration_assistant.vra import model as M
    M.STOP.clear()
    started: list[int] = []
    procs: list = []

    def worker(job):
        started.append(job)
        p = subprocess.Popen(["sleep", "30"], start_new_session=True)
        procs.append(p)
        with M._procs_lock:
            M._procs.add(p)
        p.wait()
        M._check_stop()
        return job
    t0 = time.monotonic()
    try:
        # the person presses Ctrl+C: the terminal sends SIGINT
        threading.Timer(0.5, lambda: os.kill(os.getpid(), signal.SIGINT)).start()
        with pytest.raises(KeyboardInterrupt):
            M.run_parallel(list(range(20)), worker, max_workers=2)
        assert time.monotonic() - t0 < 5
        assert sorted(started) == [0, 1]            # nothing started after Ctrl+C
        time.sleep(0.2)
        assert sorted(started) == [0, 1]
        for _ in range(50):
            if all(p.poll() is not None for p in procs):
                break
            time.sleep(0.05)
        assert all(p.poll() is not None for p in procs)     # the calls in flight were ended
        with pytest.raises(M.Stopped):
            M._check_stop()                         # and no call starts after it
    finally:
        M.STOP.clear()


def test_ctrl_c_says_how_far_the_analysis_got_and_keeps_it(tmp_path, monkeypatch, capsys):
    import sys

    from kestra_migration_assistant.vra import cli
    export = _export(tmp_path)
    real = cli.run_stage

    def stage(s):
        if s == "understand":
            raise KeyboardInterrupt
        return real(s)
    monkeypatch.setattr(cli, "run_stage", stage)
    monkeypatch.setattr(sys, "stdin", NoInput())
    monkeypatch.setenv("VRO_NO_MODEL", "1")
    assert cli.main(["run", export, "--no-model"]) == 130
    out = capsys.readouterr().out
    assert re.search(r"Stopped\. 3 of 3 workflows analyzed and kept\. Run again to continue\.", out), out


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
    for answer in (None, "decide each", "yes"):
        if answer:
            from kestra_migration_assistant.vra.stages import your_input as Y
            gid = next(x["id"] for x in _brief(export)["input"]["shown"] if x["id"] == "suggested")
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
    inside = {m for ms in groups.values() for m in ms}
    plain = [q["id"] for q in qs if q["id"] not in groups]
    assert qs and len(plain) >= 2
    by = next(q["id"] for q in qs if q["id"] not in inside)
    a, b = [x for x in plain if x != by][-1], [x for x in plain if x != by][-2]
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
    r = planner.replan([{"id": by, "question": "q", "answer": "keep all, ask admin@corp.example.com"}],
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


def test_a_re_planning_closure_and_merge_survive_a_replay_in_a_fresh_folder(tmp_path, monkeypatch):
    """The stages before the brief rewrite answers.yaml before the brief's items exist: what the re-planning closed
    and merged must still be there when the brief asks them (the file is the only record)."""
    export = _export(tmp_path)
    _run(monkeypatch, export, None)
    from kestra_migration_assistant.vra.stages import planner
    from kestra_migration_assistant.vra.stages import your_input as Y
    qs = [q for q in planner.questions() if not q.get("estate")]
    groups = Y.load()["groups"]
    plain = [q["id"] for q in qs if q["id"] not in groups]
    by = qs[0]["id"]
    Y.set_answer(by, "keep all")
    Y.write()
    loaded = Y.load()
    planner.apply({"close": [{"id": plain[-1], "by": by, "answer": "as for the first", "why": "same"}],
                   "merge": [{"ids": plain[:2], "about": "two in one", "question": "Both at once?",
                              "options": ["yes"]}] if len(plain) >= 3 else [],
                   "reword": [], "add": []}, loaded, qs)
    _run(monkeypatch, export, None, "--batch")
    again = _export(tmp_path, "again")
    os.makedirs(os.path.join(again, "kestra-assessment", "your-input"))
    shutil.copy(os.path.join(export, "kestra-assessment", "your-input", "answers.yaml"),
                os.path.join(again, "kestra-assessment", "your-input", "answers.yaml"))
    assert _run(monkeypatch, again, None, "--batch")[0] == 0
    a, b = _brief(export), _brief(again)
    assert a["questions"] == b["questions"]
    assert Y.load()["closed"][plain[-1]]["by"] == by
    if len(plain) >= 3:
        assert "same.two-in-one" in Y.load()["groups"]


def test_a_merge_already_made_is_not_made_again(tmp_path, monkeypatch):
    export = _export(tmp_path)
    _run(monkeypatch, export, None)
    from kestra_migration_assistant.vra.stages import planner
    from kestra_migration_assistant.vra.stages import your_input as Y
    qs = [q for q in planner.questions() if not q.get("estate")]
    groups = Y.load()["groups"]
    plain = [q["id"] for q in qs if q["id"] not in groups]
    if len(plain) < 2:
        pytest.skip("needs two plain questions")
    m = {"ids": plain[:2], "about": "first words", "question": "Both?", "options": ["yes"]}
    assert planner.apply({"close": [], "merge": [m], "reword": [], "add": []}, Y.load(), qs)["merged"] == 1
    again = {**m, "about": "other words"}
    assert planner.apply({"close": [], "merge": [again], "reword": [], "add": []}, Y.load(), qs)["merged"] == 0
    assert list(Y.registry()["_merges"]) == ["same.first-words"]
