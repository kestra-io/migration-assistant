"""
End of input (Ctrl+D, or stdin closed) in the interactive run, on the review/ conformance tree:

- at a question that leads to billed calls (the cost question, a question that sharpens the brief with a model) it
  is a no: the run stops before any billed call and says "Stopped before anything was billed.";
- at any other question (the model, the estate interview, opening the brief) it saves and stops asking, like `q`.
"""
from __future__ import annotations

import io
import os
import shutil
import sys

import pytest

CONFORMANCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conformance")
ENDED = "Stopped before anything was billed."


def _export(tmp, specs: bool) -> str:
    folder = tmp / "review"
    shutil.copytree(os.path.join(CONFORMANCE, "review", "vro"), folder / "review")
    shutil.copytree(os.path.join(CONFORMANCE, "review", "vra"), folder / "review-vra")
    if specs:
        d = folder / "kestra-assessment" / "data" / "2-understand"
        d.mkdir(parents=True)
        for f in os.listdir(os.path.join(CONFORMANCE, "review", "specs")):
            shutil.copy(os.path.join(CONFORMANCE, "review", "specs", f), d / f)
    return str(folder)


class Billed(Exception):
    pass


def _no_billing(monkeypatch) -> list:
    """Every model call that would be billed is recorded and refused."""
    from kestra_migration_assistant.vra import model as M
    calls: list = []

    class Refuse:
        def invoke(self, prompt, schema):
            calls.append(prompt[:80])
            raise Billed("a billed call was made")
    monkeypatch.setattr(M, "provider_for", lambda cfg, s=None: Refuse())
    return calls


def _typed(monkeypatch, typed: str):
    from kestra_migration_assistant.vra import cli, interactive
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(cli, "is_terminal", lambda: True)
    monkeypatch.setattr(interactive, "can_open_browser", lambda: False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(typed))
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    return out, err


def test_end_of_input_at_the_cost_question_bills_nothing(tmp_path, monkeypatch):
    """The input ends at once: the model question keeps the model, the interview saves and goes on like `q`, and the
    cost question takes it as a no. Nothing is billed."""
    from kestra_migration_assistant.vra import cli
    export = _export(tmp_path, specs=False)                 # no specs on record: the analysis needs billed calls
    calls = _no_billing(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    monkeypatch.delenv("VRO_NO_MODEL", raising=False)
    out, err = _typed(monkeypatch, "")
    code = cli.main(["run", export, "--provider", "openai-compatible", "--base-url", "http://127.0.0.1:9/v1",
                     "--model", "some-model"], interactive=True)
    assert code == 3, out.getvalue() + err.getvalue()
    assert calls == []
    assert ENDED in err.getvalue()
    assert "Continue? Enter or y to continue" in out.getvalue()       # the question was asked, and not answered
    assert os.path.exists(os.path.join(export, "kestra-assessment", "your-input", "answers.yaml"))


def test_enter_at_the_cost_question_still_goes_on_and_n_stops():
    from kestra_migration_assistant import config as K
    from kestra_migration_assistant.vra import cli
    from kestra_migration_assistant.vra import model as M
    s = K.Settings("claude-code", model="claude-haiku-5-5", max_cost_usd=20)
    calls = [{"purpose": "understand", "chars": 30000}]
    for typed, go, why in (("\n", True, ""), ("y\n", True, ""), ("n\n", False, "as asked"), ("", False, ENDED)):
        real = sys.stdin
        sys.stdin = io.StringIO(typed)
        try:
            g = cli.cost_gate(always=True, out=io.StringIO())
            g.planner = lambda cur: calls
            if go:
                g.before_billed("understand", "p", {}, {}, s)
            else:
                with pytest.raises(M.CostNotApproved) as e:
                    g.before_billed("understand", "p", {}, {}, s)
                assert why in str(e.value)
        finally:
            sys.stdin = real


def test_end_of_input_above_the_threshold_outside_the_interactive_run_is_a_no(monkeypatch):
    from kestra_migration_assistant import config as K
    from kestra_migration_assistant.vra import cli
    from kestra_migration_assistant.vra import model as M

    class Tty(io.StringIO):
        def isatty(self):
            return True
    monkeypatch.setattr(sys, "stdin", Tty(""))
    s = K.Settings("claude-code", model="claude-opus-5-5", max_cost_usd=0)
    g = cli.cost_gate(out=io.StringIO())
    g.planner = lambda cur: [{"purpose": "understand", "chars": 300000}]
    with pytest.raises(M.CostNotApproved) as e:
        g.before_billed("understand", "p", {}, {}, s)
    assert str(e.value) == ENDED


def test_end_of_input_while_sharpening_with_a_model_saves_the_answers_and_bills_nothing(monkeypatch):
    """A question that sharpens the brief leads to the re-planning call: the input ends after one answer, so that
    answer is saved, and neither the re-planning call nor the brief's rewrite runs."""
    from kestra_migration_assistant.vra import interactive
    from kestra_migration_assistant.vra.stages import planner
    recorded: list = []
    monkeypatch.setattr(planner, "record", lambda q, a: recorded.append((q["id"], a)))
    monkeypatch.setattr(planner, "replan", lambda *a: pytest.fail("the re-planning call was made"))
    out = io.StringIO()
    term = interactive.Terminal(io.StringIO("the platform team\n"), out)
    qs = [{"id": f"added.q{i}", "question": f"Who owns area {i}?", "options": []} for i in (1, 2)]
    done = interactive.sharpen(term, True, qs, lambda: pytest.fail("the brief was rewritten"))
    assert recorded == [("added.q1", "the platform team")]
    assert done["stopped"] and done.get("ended")
    assert ENDED in out.getvalue() and "answers are saved" in out.getvalue()


def test_end_of_input_at_the_other_questions_saves_and_stops_asking_like_q(tmp_path, monkeypatch):
    """No model, so no question leads to billed calls: the interview ends like `q` (saved, the run goes on with what
    was answered), the brief is written, and the run ends normally."""
    from kestra_migration_assistant.vra import cli, interactive
    export = _export(tmp_path, specs=True)
    monkeypatch.setenv("VRO_NO_MODEL", "1")
    out, err = _typed(monkeypatch, "")
    assert cli.main(["run", export, "--no-model"], interactive=True) == 0, err.getvalue()
    text = out.getvalue()
    assert "Saved. The run goes on with what you answered" in text
    assert os.path.exists(os.path.join(export, "kestra-assessment", "data", "3-brief", "brief.json"))
    assert "Traceback" not in err.getvalue()
    # the model question: end of input keeps the model
    from kestra_migration_assistant import config as K
    s = K.Settings("claude-code", model="claude-opus-5-5")
    term = interactive.Terminal(io.StringIO(""), io.StringIO())
    assert interactive.choose_model(term, s) is s
    # opening the brief: end of input does not open it
    opened: list = []
    import webbrowser
    monkeypatch.setattr(interactive, "can_open_browser", lambda: True)
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: opened.append(url) or True)
    term = interactive.Terminal(io.StringIO(""), io.StringIO())
    interactive.finish(term, os.path.join(export, "kestra-assessment", "index.html"))
    assert opened == []

