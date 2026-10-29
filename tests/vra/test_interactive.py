"""
The interactive run (1.2.0), driven with simulated terminal input, on the review/ conformance tree (three workflows
with their specs, no model):

- typed in a terminal, `run` says what it read and how to stop, asks the estate interview, runs the analysis and
  writes the brief at once, every question with a suggestion assumed and written in answers.yaml; then a short
  summary, and an offer (no by default) to answer at most five questions that sharpen it, the ones that change the
  brief most first; each answer is saved at once; s skips, q saves and stops; run again, it goes straight to the
  offer;
- a question whose choices are not exclusive takes several numbers; a yes-or-no question offers yes and no;
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
                    r"Re-planned|accept every suggested answer below|Choose 1-1\b|After the analysis", re.I)


class Person(io.StringIO):
    """Takes every suggestion (Enter) until the offer to sharpen the brief, then types `lines` one by one; records
    what the terminal showed and whether the brief was on disk at each prompt."""

    def __init__(self, lines: list[str], out: io.StringIO, export: str):
        super().__init__()
        self.lines, self.out, self.export, self.seen, self.offered = list(lines), out, export, [], False

    def readline(self, *a):
        prompt = self.out.getvalue().rsplit("\n", 1)[-1]
        brief = os.path.join(self.export, "kestra-assessment", "data", "3-brief", "brief.json")
        self.seen.append({"prompt": prompt, "brief": os.path.exists(brief)})
        self.offered = self.offered or prompt.startswith("Answer them now?")
        return (self.lines.pop(0) if self.offered and self.lines else "") + "\n"


def _typed_run(monkeypatch, export: str, lines: list[str]) -> tuple[int, str, Person]:
    import sys

    from kestra_migration_assistant.vra import cli
    out = io.StringIO()
    person = Person(lines, out, export)
    monkeypatch.setattr(cli, "is_terminal", lambda: True)
    monkeypatch.setattr(sys, "stdin", person)
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setenv("VRO_NO_MODEL", "1")
    code = cli.main(["run", export, "--no-model"], interactive=True)
    return code, out.getvalue(), person


def test_the_interactive_run_writes_the_brief_after_the_interview_then_offers_to_sharpen_it(tmp_path, monkeypatch):
    export = _export(tmp_path)
    code, out, person = _typed_run(monkeypatch, export, [""])     # Enter: every suggestion, then the default no
    assert code == 0
    # the first screen: what it read, that it only reads, how to stop, then the interview in one short line
    assert "Read:" in out and re.search(r"- review\S*: \d+ workflows?, \d+ actions?", out)
    assert re.search(r"- review-vra\S*: \d+ catalog items?", out)
    assert "The assessment only reads these files: it never connects to or runs anything on your systems" in out
    assert "Press Ctrl+C to stop at any time. Finished work is kept." in out
    assert "estate:" not in out and "answers.yaml" not in out.split("Question 1 of", 1)[0]
    interview = re.search(r"Before the analysis, (\d+) questions? about your estate\.", out)
    assert interview and out.index("Press Ctrl+C") < interview.start()
    # after the interview, nothing is required: the brief is written, then summed up, then offered to sharpen
    n = int(interview.group(1))
    after = out[interview.end():].split(f"Question {n} of {n}", 1)[-1]
    assert "Question" not in after.split("Answer them now?", 1)[0]
    summary = after.index("The brief recommends: ")
    offer = re.search(r"The brief is ready\. It makes (\d+) assumptions?, listed in it\. To make it sharper, you can "
                      r"answer (up to 5 questions|\w+ questions?) now \(about [\w ]+\), or later in \S*answers\.yaml\.",
                      re.sub(r"\s+", " ", after))
    assert offer and int(offer.group(1)) > 0 and summary < after.index("Answer them now? (y/N)")
    assert re.search(r"Open\s+\S*index\.html", after[summary:])
    at_offer = next(x for x in person.seen if x["prompt"].startswith("Answer them now?"))
    assert at_offer["brief"]                                       # the brief was on disk before the offer
    assert person.seen[-1] is at_offer                             # the default no ends the run: nothing else asked
    assert "Done in " in out.rsplit("Answer them now?", 1)[1]
    assert not HIDDEN.search(out), HIDDEN.search(out)
    # every assumption is in answers.yaml, each item it settles listed with the answer it takes
    doc = _answers(export)
    est = {e["id"]: e for e in doc["estate"]}
    answered = [k for k, e in est.items() if k.startswith("estate.") and e.get("answer")]
    assert answered and all(est[k]["answer"] == est[k]["suggested"] for k in answered)
    assumed = {k: e for k, e in est.items() if k.startswith("assume.")}
    assert {"assume.suggested", "assume.non-production", "assume.defects"} <= set(assumed)
    takes = {m["id"]: m["assumed"] for m in assumed["assume.suggested"]["each"]}
    assert takes["q4-off-limits"] == "nothing is off limits" and takes["q9-vra-front-end"] == "replace"
    assert all(m.get("assumed") and "answer" not in m for e in assumed.values() for m in e.get("each") or [])
    b = _brief(export)
    assert b["questions"]["open"] == _open_lines(doc)
    assert {a["id"] for a in b["assumptions"] if a["holds"]} >= {"assume.suggested", "assume.non-production"}


def test_non_production_certificates_off_limits_vra_and_suggestions_are_assumed_never_asked(tmp_path, monkeypatch):
    """What has an answer that keeps today's behavior, or that the code suggests, is assumed: the lab first then
    non-production where it exists, nothing off limits, vRA replaced, every suggestion taken."""
    export = _export(tmp_path)
    code, out, _p = _typed_run(monkeypatch, export, ["y"] + ["s"] * 5)
    after = out.split("The brief recommends: ", 1)[1]
    assert re.search(r"Question 1 of \d", after)                          # the offer was taken: questions follow
    for words in ("non-production", "off limits", "front end", "The code suggests these answers", "certificate"):
        assert words not in out.split("Before the analysis", 1)[1], words
    from kestra_migration_assistant.vra import interactive
    from kestra_migration_assistant.vra.stages import your_input as Y
    offered = interactive.to_offer(False)
    assert offered and all(not q.get("default") for q in offered)          # only what has no suggestion
    assert not {"q3-non-production-targets", "q4-off-limits", "q9-vra-front-end"} & {q["id"] for q in offered}
    assert Y.state("q9-vra-front-end")["answer"].startswith("replace")
    assert _brief(export)["trigger_plan"]["front_end"] == "replace"


def test_yes_asks_at_most_five_ranked_q_stops_and_the_brief_is_written_again(tmp_path, monkeypatch):
    from kestra_migration_assistant.vra import interactive
    from kestra_migration_assistant.vra.stages import planner
    from kestra_migration_assistant.vra.stages import your_input as Y
    monkeypatch.setattr(interactive, "OFFER_AT_MOST", 2)
    export = _export(tmp_path)
    _run(monkeypatch, export, None)
    offered = interactive.to_offer(False)
    assert len(offered) >= 2
    assert offered == planner.ranked(offered)                              # what changes the brief most first
    assert [(-(q["waves"]), -q["settles"]) for q in offered] == sorted((-(q["waves"]), -q["settles"]) for q in offered)
    before = os.path.getmtime(os.path.join(export, "kestra-assessment", "data", "3-brief", "brief.json"))
    code, out, _p = _typed_run(monkeypatch, export, ["y", "1", "q"])
    assert code == 0 and "you can answer up to 2 questions now" in re.sub(r"\s+", " ", out)
    asked = re.findall(r"Question (\d+) of (\d+)", out.split("Answer them now?", 1)[1])
    assert asked == [("1", "2"), ("2", "2")]                              # q stopped at the second
    assert not Y.is_open(Y.load()["entries"][offered[0]["id"]].get("answer"))
    assert "Updating the brief with your answers" in out and re.search(r"The brief is updated\. Open\s+\S*index\.html", out)
    assert os.path.getmtime(os.path.join(export, "kestra-assessment", "data", "3-brief", "brief.json")) >= before
    # run again: straight to the summary and the offer, no first screen and no interview
    code, out, person = _typed_run(monkeypatch, export, [""])
    assert code == 0 and "Read:" not in out and "Before the analysis" not in out
    assert "The brief recommends: " in out and [x["prompt"] for x in person.seen][-1].startswith("Answer them now?")


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
    # its choices are listed under it, not repeated in brackets
    q9 = interactive._strip_name({"question": "Will vRA stay? (keep, replace, or replace with a bridge until then)",
                                  "options": ["keep", "replace", "replace with a bridge until then"]})
    assert q9["question"] == "Will vRA stay?"
    # a defect several workflows share: the header counts them, the question says what it does once
    from kestra_migration_assistant.vra.stages import your_input as Y
    shared = Y.group_question("same.defect.join.other", [
        {"key": "defect.a.join.other", "family": "defect", "ask": "A: x", "question": "The join fails. Keep it as today, or fix it?"},
        {"key": "defect.b.join.other", "family": "defect", "ask": "B: x", "question": "The join fails. Keep it as today, or fix it?"}])
    assert shared.count("Keep") == 1, shared
    asked = interactive._strip_name({"question": shared, "options": ["keep them all, as today"], "about": ["A", "B"]})
    assert asked["question"] == "The join fails. Keep it everywhere, fix it everywhere, or decide each?", asked


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
    _run(monkeypatch, first, "\n" * n + "y\n1\n1\nq\n")
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
    from kestra_migration_assistant.vra.stages import your_input as Y
    for step in (None, "answer one", "override the suggestions"):
        if step == "answer one":
            Y.set_answer(_brief(export)["input"]["shown"][0]["id"], "1")
            _run(monkeypatch, export, None)
        elif step:
            Y.set_answer("assume.suggested", "no: ask each")      # every suggestion is asked again
            _run(monkeypatch, export, None)
        b, doc = _brief(export), _answers(export)
        n = _open_lines(doc)
        assert b["questions"]["open"] == n == sum(x["lines"] for x in b["input"]["shown"])
        st = status.status_of(b)
        assert st["open"] == n
        m = re.search(r"answer (\d+) questions?", st["line"])
        assert not m or int(m.group(1)) <= n
    assert "suggested" in {x["id"] for x in _brief(export)["input"]["shown"]}


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


def test_a_question_the_re_planning_added_survives_a_replay_in_a_fresh_folder(tmp_path, monkeypatch):
    export = _export(tmp_path)
    _run(monkeypatch, export, None)
    from kestra_migration_assistant.vra.stages import planner
    from kestra_migration_assistant.vra.stages import your_input as Y
    qs = [q for q in planner.questions() if not q.get("estate")]
    planner.apply({"close": [], "merge": [], "reword": [], "add": [
        {"about": "ca files", "question": "Can you supply the CA files?", "options": ["yes", "no"],
         "why": "raised"}]}, Y.load(), qs)
    _run(monkeypatch, export, None, "--batch")
    again = _export(tmp_path, "again")
    os.makedirs(os.path.join(again, "kestra-assessment", "your-input"))
    shutil.copy(os.path.join(export, "kestra-assessment", "your-input", "answers.yaml"),
                os.path.join(again, "kestra-assessment", "your-input", "answers.yaml"))
    _run(monkeypatch, again, None, "--batch")
    assert _brief(export)["questions"] == _brief(again)["questions"]
    # it has a suggestion, so it is assumed, and listed with its answer under the assumption: the file keeps it
    assert Y.load()["closed"]["added.ca-files"]["by"] == "assume.suggested"
