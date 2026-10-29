"""
The interactive run (1.2.0), driven with simulated terminal input, on the review/ conformance tree (three workflows
with their specs, no model):

- typed in a terminal, `run` says what it read and how to stop, asks the estate interview, runs the analysis and
  writes the brief at once, every question with a suggestion assumed and written in answers.yaml; then a short
  summary, the brief's file:// link, "Open the brief now? (Y/n)" (Enter opens it, in the browser) and one closing
  line: no question is offered; run again, it asks at most five questions that sharpen the brief, the ones that
  change it most first (each answer saved at once, s skips, q stops), and ends the same way;
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
import pathlib
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


def _more_open_questions() -> None:
    """Three questions with no suggestion, as the re-planning adds them: since 1.2.0 the review tree leaves only one
    open (who owns each area), and these tests need a few."""
    from kestra_migration_assistant.vra.stages import planner
    from kestra_migration_assistant.vra.stages import your_input as Y
    qs = [q for q in planner.questions() if not q.get("estate")]
    planner.apply({"close": [], "merge": [], "reword": [], "add": [
        {"about": "maintenance window", "question": "When can changes run in production?", "options": [], "why": "w"},
        {"about": "approver", "question": "Who approves a change to a production system?", "options": [],
         "why": "w"},
        {"about": "rollback", "question": "Who decides to roll back a failed change?", "options": [], "why": "w"}]},
        Y.load(), qs)


HIDDEN = re.compile(r"settles? \d+|settled by other|questions? to answer|Round \d|settle the most|Saved\. |"
                    r"Re-planned|accept every suggested answer below|Choose 1-1\b|After the analysis", re.I)


class Person(io.StringIO):
    """Takes every suggestion (Enter) while the estate has no brief, then types `lines` one by one (Enter once they
    run out); records what the terminal showed and whether the brief was on disk at each prompt."""

    def __init__(self, lines: list[str], out: io.StringIO, export: str):
        super().__init__()
        self.lines, self.out, self.export, self.seen = list(lines), out, export, []

    def readline(self, *a):
        prompt = self.out.getvalue().rsplit("\n", 1)[-1]
        brief = os.path.exists(os.path.join(self.export, "kestra-assessment", "data", "3-brief", "brief.json"))
        self.seen.append({"prompt": prompt, "brief": brief})
        return (self.lines.pop(0) if brief and self.lines else "") + "\n"


def _typed_run(monkeypatch, export: str, lines: list[str], browser: bool = True) -> tuple[int, str, Person]:
    """`run` typed in a terminal; `browser`: whether one can be opened. What would open in it is in OPENED."""
    import sys
    import webbrowser

    from kestra_migration_assistant.vra import cli, interactive
    out = io.StringIO()
    person = Person(lines, out, export)
    OPENED.clear()
    monkeypatch.setattr(cli, "is_terminal", lambda: True)
    monkeypatch.setattr(interactive, "can_open_browser", lambda: browser)
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: OPENED.append(url) or True)
    monkeypatch.setattr(sys, "stdin", person)
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setenv("VRO_NO_MODEL", "1")
    code = cli.main(["run", export, "--no-model"], interactive=True)
    return code, out.getvalue(), person


OPENED: list[str] = []
AGAIN = "To answer more questions and sharpen the brief, run the same command again. Your answers are kept."


def _ending(out: str) -> str:
    """The end of a run, from the summary on, on one line."""
    return re.sub(r"\s+", " ", out[out.rindex("The brief recommends: "):]).strip()


def test_a_first_run_writes_the_brief_after_the_interview_and_stops_at_it(tmp_path, monkeypatch):
    export = _export(tmp_path)
    code, out, person = _typed_run(monkeypatch, export, [""])     # Enter: every suggestion, then Enter opens the brief
    assert code == 0
    # the first screen: what it read, that it only reads, how to stop, then the interview in one short line
    assert "Read:" in out and re.search(r"- review\S*: \d+ workflows?, \d+ actions?", out)
    assert re.search(r"- review-vra\S*: \d+ catalog items?", out)
    assert "The assessment only reads these files: it never connects to or runs anything on your systems" in out
    assert "Press Ctrl+C to stop at any time. Finished work is kept." in out
    assert "estate:" not in out and "answers.yaml" not in out.split("Question 1 of", 1)[0]
    interview = re.search(r"Before the analysis, (\d+) questions? about your estate\.", out)
    assert interview and out.index("Press Ctrl+C") < interview.start()
    # after the interview, nothing is asked: the summary, the time, the link, the offer to open it, the closing line
    n = int(interview.group(1))
    after = out[interview.end():].split(f"Question {n} of {n}", 1)[-1]
    assert "Question" not in after and "Answer them now" not in out
    end = _ending(out)
    url = pathlib.Path(os.path.abspath(os.path.join(export, "kestra-assessment", "index.html"))).as_uri()
    assert re.fullmatch(r"The brief recommends: .+ Done in \d+s\. The brief: (\S+) Open the brief now\? \(Y/n\) "
                        + re.escape(AGAIN), end) and f"The brief: {url} " in end
    at_open = person.seen[-1]
    assert at_open["prompt"] == "Open the brief now? (Y/n) " and at_open["brief"]
    assert OPENED == [url]                                          # Enter opened it
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
    # the brief says the closing line too, at the top of its assumptions
    md = open(os.path.join(export, "kestra-assessment", "report", "BRIEF.md"), encoding="utf-8").read()
    assert md.split("## Assumptions\n\n", 1)[1].startswith(AGAIN + "\n")


@pytest.mark.parametrize("n", [1, 3])
def test_the_first_screen_says_how_much_code_is_missing_and_leaves_the_list_to_the_brief(tmp_path, monkeypatch, n):
    from kestra_migration_assistant.vra import interactive as I
    from kestra_migration_assistant.vra.stages import common as C
    monkeypatch.setattr(C, "PRE", str(tmp_path))
    monkeypatch.setattr(C, "ASSESS", str(tmp_path))
    missing = [{"what": f"action com.example/act{i}", "called_by": ["Lab - Enable Override"], "how": "Add it"}
               for i in range(n)]
    (tmp_path / "patterns.json").write_text(json.dumps({"missing": missing}))
    out = io.StringIO()
    I.first_screen(I.Terminal(io.StringIO(), out), use_model=False, n_questions=0)
    text = " ".join(out.getvalue().split())
    said = {1: "Not in the package: the workflows call 1 piece of code that isn't in it. You can carry on: the "
               "analysis runs without it, but the workflows that call it get less certain results. The brief lists it "
               "and how to add it.",
            3: "Not in the package: the workflows call 3 pieces of code that aren't in it. You can carry on: the "
               "analysis runs without them, but the workflows that call them get less certain results. The brief "
               "lists each one and how to add it."}[n]
    assert said in text
    assert "act0" not in text and "- " not in out.getvalue().split("Not in the package", 1)[1]


def test_n_declines_and_no_browser_skips_the_question(tmp_path, monkeypatch):
    export = _export(tmp_path)
    _typed_run(monkeypatch, export, [""])
    _code, out, person = _typed_run(monkeypatch, export, ["s", "n"])      # skip the one question, then no
    assert [x["prompt"].strip() for x in person.seen] == [
        "Your answer (Enter or s to skip, q to save and quit):", "Open the brief now? (Y/n)"] and OPENED == []
    assert "Nothing answered: the brief stays as it is." in out and _ending(out).endswith(AGAIN)
    _code, out, person = _typed_run(monkeypatch, export, ["s"], browser=False)
    assert "Open the brief now?" not in out and OPENED == [] and "The brief: file://" in out
    assert _ending(out).endswith(AGAIN)


def test_non_production_certificates_off_limits_vra_and_suggestions_are_assumed_never_asked(tmp_path, monkeypatch):
    """What has an answer that keeps today's behavior, or that the code suggests, is assumed: the lab first then
    non-production where it exists, nothing off limits, vRA replaced, every suggestion taken; and Enterprise
    Edition is available."""
    export = _export(tmp_path)
    _typed_run(monkeypatch, export, [""])                          # the first run asks only the interview
    code, out, _p = _typed_run(monkeypatch, export, ["s", "n"])
    assert re.search(r"Question 1 of \d", out)                     # the second run asks
    for words in ("non-production", "off limits", "front end", "The code suggests these answers", "certificate",
                  "Enterprise Edition"):
        assert words not in out, words
    from kestra_migration_assistant.vra import interactive
    from kestra_migration_assistant.vra.stages import your_input as Y
    offered = interactive.to_offer(False)
    assert offered and all(not q.get("default") for q in offered)          # only what has no suggestion
    assert not {"q3-non-production-targets", "q4-off-limits", "q9-vra-front-end", "edition"} & {q["id"] for q in offered}
    assert Y.state("q9-vra-front-end")["answer"].startswith("replace")
    assert _brief(export)["trigger_plan"]["front_end"] == "replace"


def test_a_run_after_the_brief_asks_at_most_five_ranked_q_stops_and_it_ends_like_the_first(tmp_path, monkeypatch):
    from kestra_migration_assistant.vra import interactive
    from kestra_migration_assistant.vra.stages import planner
    from kestra_migration_assistant.vra.stages import your_input as Y
    monkeypatch.setattr(interactive, "OFFER_AT_MOST", 2)
    export = _export(tmp_path)
    _typed_run(monkeypatch, export, [""])
    _more_open_questions()
    offered = interactive.to_offer(False)
    assert len(offered) >= 2
    assert offered == planner.ranked(offered)                              # what changes the brief most first
    assert [(-(q["waves"]), -q["settles"]) for q in offered] == sorted((-(q["waves"]), -q["settles"]) for q in offered)
    before = os.path.getmtime(os.path.join(export, "kestra-assessment", "data", "3-brief", "brief.json"))
    code, out, person = _typed_run(monkeypatch, export, ["the platform team", "q", ""])
    assert code == 0 and "Read:" not in out and "Before the analysis" not in out
    assert "To sharpen the brief: 2 questions (about a minute), the ones that change it most first." in out
    asked = re.findall(r"Question (\d+) of (\d+)", out)
    assert asked == [("1", "2"), ("2", "2")]                              # q stopped at the second
    assert Y.load()["entries"][offered[0]["id"]].get("answer") == "the platform team"
    assert "Updating the brief with your answers" in out
    assert os.path.getmtime(os.path.join(export, "kestra-assessment", "data", "3-brief", "brief.json")) >= before
    # then it ends exactly like a first run: the summary, the time, the link, Enter opens it, the closing line
    assert out.index("Updating the brief") < out.index("The brief recommends: ")
    assert re.fullmatch(r"The brief recommends: .+ Done in \d+s\. The brief: file://\S+ Open the brief now\? "
                        r"\(Y/n\) " + re.escape(AGAIN), _ending(out))
    assert len(OPENED) == 1 and person.seen[-1]["prompt"] == "Open the brief now? (Y/n) "


def test_done_in_counts_the_tools_working_time_not_the_time_spent_answering(tmp_path, monkeypatch):
    import time as T
    export = _export(tmp_path)
    _typed_run(monkeypatch, export, [""])
    _more_open_questions()
    clock = {"t": 1000.0}
    real = Person.readline

    def monotonic() -> float:
        clock["t"] += 0.01
        return clock["t"]

    def ten_minutes_each(self, *a):
        clock["t"] += 600
        return real(self, *a)
    monkeypatch.setattr(T, "monotonic", monotonic)
    monkeypatch.setattr(Person, "readline", ten_minutes_each)
    _code, out, person = _typed_run(monkeypatch, export, ["the platform team", "s", "s", "n"])
    assert len(person.seen) >= 3                                    # half an hour or more spent answering
    took = re.search(r"Done in (\S+ ?\S*)\.", out).group(1)
    assert re.fullmatch(r"\d+s", took), took                        # not counted


def test_a_spec_check_quotes_the_brief_and_waits_for_it(tmp_path, monkeypatch):
    """A spec check is asked only once the brief exists, one per workflow: "The brief says <workflow> <what it does>.
    Is that right?"."""
    from kestra_migration_assistant.vra import interactive
    from kestra_migration_assistant.vra.stages import reviews
    assert reviews.first_sentence("Adds one user to a group, then closes the request. It is started by vRA.") == \
        "adds one user to a group, then closes the request"
    assert reviews.first_sentence("Hourly datastore capacity check for vSphere. More.") == \
        "is an hourly datastore capacity check for vSphere"                 # what it is, not what it does
    assert reviews.first_sentence("Day 2 action that patches one machine.") == "is a day 2 action that patches one machine"
    assert reviews.first_sentence("Extend VM Disk is a Day 2 workflow.", "Extend VM Disk") == "is a Day 2 workflow"
    assert reviews.first_sentence("Subscribed to an event, this workflow creates a CI.") == "is subscribed to an event"
    q = {"id": "specs", "question": "Are the specs of A and B right?", "members": ["spec.a", "spec.b"],
         "options": ["yes", "no: I will say which is wrong"], "default": None, "about": ["A", "B"], "group": "specs"}
    monkeypatch.setattr(interactive, "has_brief", lambda: False)
    from kestra_migration_assistant.vra.stages import planner
    from kestra_migration_assistant.vra.stages import your_input as Y
    monkeypatch.setattr(planner, "questions", lambda: [q])
    monkeypatch.setattr(planner, "ranked", lambda qs: qs)
    assert interactive.to_offer(False) == []                               # no brief yet: no spec check
    monkeypatch.setattr(interactive, "has_brief", lambda: True)
    monkeypatch.setattr(Y, "items", lambda: [
        {"key": "spec.a", "workflow": "Add User to AD Group", "says": "adds one user to one managed AD group"},
        {"key": "spec.b", "workflow": "Reconcile DNS Records", "says": "compares DNS records with the CMDB"}])
    got = interactive.to_offer(False)
    assert [x["id"] for x in got] == ["spec.a", "spec.b"]
    assert got[0]["question"] == "The brief says Add User to AD Group adds one user to one managed AD group. Is that right?"
    assert got[1]["options"] == ["yes", interactive.NO_OWN] and not got[1]["default"]


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
    assert "Choose" not in out.getvalue() and "Your answer (Enter or s to skip" in out.getvalue()
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
    _more_open_questions()
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
    _more_open_questions()
    from kestra_migration_assistant.vra.stages import planner
    from kestra_migration_assistant.vra.stages import your_input as Y
    qs = [q for q in planner.questions() if not q.get("estate")]
    groups = Y.load()["groups"]
    plain = [q["id"] for q in qs if q["id"] not in groups]
    # an added question answers, a question of the tool's own is closed by it, and two others are merged
    by = next(x for x in plain if x.startswith("added."))
    shut = next(x for x in plain if not x.startswith("added."))
    two = [x for x in plain if x not in (by, shut)][:2]
    Y.set_answer(by, "keep all")
    Y.write()
    loaded = Y.load()
    planner.apply({"close": [{"id": shut, "by": by, "answer": "as for the first", "why": "same"}],
                   "merge": [{"ids": two, "about": "two in one", "question": "Both at once?",
                              "options": ["yes"]}] if len(two) == 2 else [],
                   "reword": [], "add": []}, loaded, qs)
    _run(monkeypatch, export, None, "--batch")
    again = _export(tmp_path, "again")
    os.makedirs(os.path.join(again, "kestra-assessment", "your-input"))
    shutil.copy(os.path.join(export, "kestra-assessment", "your-input", "answers.yaml"),
                os.path.join(again, "kestra-assessment", "your-input", "answers.yaml"))
    assert _run(monkeypatch, again, None, "--batch")[0] == 0
    a, b = _brief(export), _brief(again)
    assert a["questions"] == b["questions"]
    assert Y.load()["closed"][shut]["by"] == by
    assert len(two) == 2 and "same.two-in-one" in Y.load()["groups"]


def test_a_merge_already_made_is_not_made_again(tmp_path, monkeypatch):
    export = _export(tmp_path)
    _run(monkeypatch, export, None)
    _more_open_questions()
    from kestra_migration_assistant.vra.stages import planner
    from kestra_migration_assistant.vra.stages import your_input as Y
    qs = [q for q in planner.questions() if not q.get("estate")]
    groups = Y.load()["groups"]
    plain = [q["id"] for q in qs if q["id"] not in groups]
    assert len(plain) >= 2
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
