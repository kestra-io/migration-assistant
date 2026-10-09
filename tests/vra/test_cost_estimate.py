"""
The cost estimate before a run's first billed call (model.Gate, stages/estimate.py), on the synthetic helpers
estate with a stand-in model that bills nothing:

- the estimate counts the calls the run then makes, and prices them;
- above max_cost_usd a run asks in the terminal, and stops on a no;
- --yes goes ahead without asking;
- with no terminal to ask in, a run above the threshold stops before any call;
- a re-run whose replies are all cached estimates nothing and calls nothing, and a cached call costs $0;
- with the Claude Code login, the estimate is the API price and says it counts against the Claude plan.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_command import _export, _main, _minimal


class _Stdin(io.StringIO):
    def __init__(self, text: str, tty: bool):
        super().__init__(text)
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty


@pytest.fixture
def stand_in(tmp_path, monkeypatch):
    """A model that answers every schema with its smallest value, bills nothing, and counts its calls; the
    settings from a config file in a temporary home (max_cost_usd set per test)."""
    from kestra_migration_assistant.vra import model as M
    monkeypatch.delenv("VRO_NO_MODEL", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "stand-in")
    monkeypatch.setattr(M, "SETTINGS", None)
    calls: list[str] = []

    class StandIn(M.Provider):
        def invoke(self, prompt, schema):
            calls.append(prompt)
            return {"result": json.dumps(_minimal(schema)), "is_error": False, "total_cost_usd": 0.0}

    monkeypatch.setattr(M, "provider_for", lambda cfg, s=None: StandIn(cfg))

    def config(max_cost_usd=None):
        d = tmp_path / "home" / "kestra-migrate"
        d.mkdir(parents=True, exist_ok=True)
        (d / "config.yaml").write_text("provider: anthropic\nmodel: claude-opus-5-5\n"
                                       + (f"max_cost_usd: {max_cost_usd}\n" if max_cost_usd is not None else ""))
    config()
    return {"calls": calls, "config": config, "export": _export(tmp_path / "estate")}


def _estimate_line(out: str) -> str:
    return next(x for x in out.splitlines() if x.startswith("Cost") and "Estimate:" in x)


def test_the_estimate_counts_the_calls_the_run_makes_and_prices_them(stand_in, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", _Stdin("", tty=False))
    assert _main("run", stand_in["export"]) == 0
    line = _estimate_line(capsys.readouterr().out)
    n = int(line.split("Estimate: ", 1)[1].split(" ", 1)[0])
    assert n == len(stand_in["calls"]) > 1, (line, len(stand_in["calls"]))
    usd = float(line.split("about $", 1)[1].split(" ", 1)[0])
    assert 0 < usd < 20
    assert "Claude Opus 5.5's API prices" in line and "Claude plan" not in line     # an API key: billed


def test_with_no_terminal_a_run_above_the_threshold_stops_before_any_call(stand_in, monkeypatch, capsys):
    stand_in["config"](max_cost_usd=0)
    monkeypatch.setattr(sys, "stdin", _Stdin("", tty=False))
    assert _main("run", stand_in["export"]) == 3
    out = capsys.readouterr()
    assert _estimate_line(out.out) and "--yes" in out.err and "max_cost_usd" in out.err
    assert stand_in["calls"] == []


def test_above_the_threshold_it_asks_and_a_no_stops_it(stand_in, monkeypatch, capsys):
    stand_in["config"](max_cost_usd=0)
    monkeypatch.setattr(sys, "stdin", _Stdin("n\n", tty=True))
    assert _main("run", stand_in["export"]) == 3
    assert "Go ahead? [y/N]" in capsys.readouterr().out and stand_in["calls"] == []
    monkeypatch.setattr(sys, "stdin", _Stdin("y\n", tty=True))
    assert _main("run", stand_in["export"]) == 0
    assert "Go ahead? [y/N]" in capsys.readouterr().out and stand_in["calls"]


def test_yes_goes_ahead_without_asking(stand_in, monkeypatch, capsys):
    stand_in["config"](max_cost_usd=0)
    monkeypatch.setattr(sys, "stdin", _Stdin("", tty=False))
    assert _main("run", stand_in["export"], "--yes") == 0
    out = capsys.readouterr().out
    assert _estimate_line(out) and "Go ahead?" not in out and stand_in["calls"]


def test_a_fully_cached_re_run_estimates_nothing_and_a_cached_call_costs_nothing(stand_in, monkeypatch, capsys):
    from kestra_migration_assistant import config as K
    from kestra_migration_assistant.vra import model as M
    from kestra_migration_assistant.vra.stages import common as C
    from kestra_migration_assistant.vra.stages import estimate as E
    monkeypatch.setattr(sys, "stdin", _Stdin("", tty=False))
    assert _main("run", stand_in["export"]) == 0
    first = len(stand_in["calls"])
    capsys.readouterr()
    stand_in["config"](max_cost_usd=0)                  # any billed call would now stop the run
    assert _main("run", stand_in["export"]) == 0
    assert len(stand_in["calls"]) == first and "Estimate:" not in capsys.readouterr().out
    # the spec calls, priced from the prompts Understand would send: all cached, so $0
    for f in os.listdir(C.SPEC_DIR):
        if f.endswith(".json") and f != "PROVENANCE.json" and not f.startswith("cluster"):
            shutil.move(os.path.join(C.SPEC_DIR, f), os.path.join(str(C.STAGES), f))
    specs = E._spec_calls(E._missing_specs())
    assert specs and all("prompt" in c for c in specs)
    est = M.estimate(specs, K.Settings("anthropic", model="claude-opus-5-5"))
    assert est == {"calls": len(specs), "billed": 0, "cached": len(specs), "usd": 0.0}


def test_the_claude_code_login_shows_the_api_price_and_the_plan():
    from kestra_migration_assistant import config as K
    from kestra_migration_assistant.vra import model as M
    s = K.Settings("claude-code", model="claude-opus-5-5")
    est = M.estimate([{"purpose": "understand", "chars": 35_000}] * 4, s)
    assert est["billed"] == 4 and 0.5 < est["usd"] < 2
    words = M.estimate_words(est, s)
    assert f"about ${est['usd']:.2f} at Claude Opus 5.5's API prices" in words
    assert "not billed separately: it counts against your Claude plan" in words
    assert "no estimate in dollars" in M.estimate_words(
        M.estimate([{"purpose": "understand", "chars": 1000}], K.Settings("openai-compatible", model="qwen3:4b")),
        K.Settings("openai-compatible", model="qwen3:4b"))
