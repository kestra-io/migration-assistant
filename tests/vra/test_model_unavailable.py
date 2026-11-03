"""
A model that cannot be used, on the review/ conformance tree with no specs on record (so the analysis calls it):

- the anthropic provider without the `anthropic` package is refused before anything is asked or billed, with how to
  install it, and the terminal does not offer it as a choice;
- a key that is rejected, a model that does not exist, or no network stops the run at the first call, in plain
  words, and no brief is written: never a "needs a model" brief after a model was chosen and its cost agreed;
- a workflow the model could not analyze also stops the run before the brief.

The Anthropic API is a stand-in `anthropic` module (no network, no key needed).
"""
from __future__ import annotations

import io
import os
import shutil
import sys
import types

import pytest

CONFORMANCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conformance")


def _export(tmp) -> str:
    folder = tmp / "review"
    shutil.copytree(os.path.join(CONFORMANCE, "review", "vro"), folder / "review")
    shutil.copytree(os.path.join(CONFORMANCE, "review", "vra"), folder / "review-vra")
    return str(folder)


def _brief_written(export: str) -> bool:
    return os.path.exists(os.path.join(export, "kestra-assessment", "data", "3-brief", "brief.json"))


def _fake_sdk(monkeypatch, behavior: str) -> list:
    """A stand-in for the `anthropic` package: every call fails the way `behavior` says. Returns the calls made."""
    calls: list = []

    class APIStatusError(Exception):
        def __init__(self, message: str, status_code: int):
            super().__init__(message)
            self.status_code = status_code

    class RateLimitError(APIStatusError):
        pass

    class APIConnectionError(Exception):
        pass

    class Stream:
        def __enter__(self):
            calls.append(behavior)
            if behavior == "401":
                raise APIStatusError("Error code: 401 - authentication_error: invalid x-api-key", 401)
            if behavior == "404":
                raise APIStatusError("Error code: 404 - not_found_error: model: claude-haiku-9", 404)
            if behavior == "offline":
                raise APIConnectionError("Connection error.")
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            # a reply that never matches the schema: the workflow fails, the run does not stop on its own
            block = types.SimpleNamespace(type="text", text='{"nothing": "useful"}')
            return types.SimpleNamespace(stop_reason="end_turn", content=[block],
                                         usage=types.SimpleNamespace(input_tokens=10, output_tokens=10))

    class Anthropic:
        def __init__(self, api_key=None, timeout=None):
            self.messages = types.SimpleNamespace(stream=lambda **kw: Stream())

    mod = types.ModuleType("anthropic")
    mod.Anthropic, mod.APIStatusError, mod.RateLimitError, mod.APIConnectionError = (
        Anthropic, APIStatusError, RateLimitError, APIConnectionError)
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    return calls


def _run(monkeypatch, export: str, *args: str, typed: str | None = None) -> tuple[int, str]:
    from kestra_migration_assistant.vra import cli
    out = io.StringIO()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    monkeypatch.delenv("VRO_NO_MODEL", raising=False)
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", out)
    if typed is not None:
        from kestra_migration_assistant.vra import interactive
        monkeypatch.setattr(cli, "is_terminal", lambda: True)
        monkeypatch.setattr(interactive, "can_open_browser", lambda: False)
        monkeypatch.setattr(sys, "stdin", io.StringIO(typed))
    code = cli.main(["run", export, "--provider", "anthropic", "--model", "claude-haiku-5-5", *args],
                    interactive=typed is not None)
    return code, out.getvalue()


def test_without_the_anthropic_package_the_run_is_refused_before_anything(tmp_path, monkeypatch):
    """rc1: the run went on, every call failed on the missing package, and it wrote a "needs a model" brief."""
    from kestra_migration_assistant import config as K
    monkeypatch.setattr(K, "has_anthropic_package", lambda: False, raising=False)
    monkeypatch.setitem(sys.modules, "anthropic", None)                     # `import anthropic` fails
    export = _export(tmp_path)
    code, out = _run(monkeypatch, export, typed="\n" * 20)
    assert code != 0
    assert "The model cannot be used" in out and "kestra-migration-assistant[anthropic]" in out
    assert not _brief_written(export)


def test_the_terminal_does_not_offer_a_key_route_this_installation_cannot_use(monkeypatch):
    from kestra_migration_assistant import config as K
    from kestra_migration_assistant.vra import interactive
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    s = K.Settings("claude-code", model="claude-opus-5-5")
    monkeypatch.setattr(K, "has_anthropic_package", lambda: True, raising=False)
    assert any("Anthropic API key" in o["label"] for o in interactive.model_choices(s))
    monkeypatch.setattr(K, "has_anthropic_package", lambda: False, raising=False)
    assert not any("Anthropic API key" in o["label"] for o in interactive.model_choices(s))


@pytest.mark.parametrize("behavior, says", [
    ("401", "the Anthropic API rejected the API key"),
    ("404", "there is no model named claude-haiku-5-5"),
    ("offline", "the Anthropic API cannot be reached"),
])
def test_a_model_that_cannot_be_used_stops_at_the_first_call_and_writes_no_brief(tmp_path, monkeypatch, behavior,
                                                                                  says):
    """The person chose the model and agreed the cost (Enter at every question): the first call fails for a reason
    every call would hit, so the run stops there, says why, and writes no brief."""
    calls = _fake_sdk(monkeypatch, behavior)
    export = _export(tmp_path)
    code, out = _run(monkeypatch, export, typed="\n" * 20)
    assert "Continue? Enter or y to continue" in out                        # the cost was asked, and agreed
    assert code == 69, out[-1500:]
    assert f"The model cannot be used: {says}" in out
    assert not _brief_written(export)
    assert "needs a model" not in out
    assert len(calls) <= 8                                                  # the calls already in flight, no more


def test_in_batch_too_a_rejected_key_writes_no_brief(tmp_path, monkeypatch):
    _fake_sdk(monkeypatch, "401")
    export = _export(tmp_path)
    code, out = _run(monkeypatch, export, "--batch", "--yes")
    assert code == 69 and "rejected the API key" in out
    assert not _brief_written(export)


def test_a_workflow_the_model_could_not_analyze_stops_the_run_before_the_brief(tmp_path, monkeypatch):
    """Not a problem with the model as such (each reply has the wrong shape): the run still writes no brief from an
    analysis it did not finish, says how many failed and why, and keeps what it has."""
    _fake_sdk(monkeypatch, "wrong-shape")
    export = _export(tmp_path)
    code, out = _run(monkeypatch, export, "--batch", "--yes")
    assert code == 1, out[-1500:]
    assert "workflows could not be analyzed, so no brief was written" in out
    assert "does not have the expected structure" in out
    assert not _brief_written(export)


def test_a_remembered_model_that_cannot_be_used_is_set_aside_not_a_dead_end(tmp_path, monkeypatch):
    """The export remembers the model chosen in the terminal (data/model.json). If that one cannot be used any more
    (here: an API key route, and no `anthropic` package), the run says so and starts with the usual model, instead
    of refusing every run before the person can choose another."""
    import json as _json

    from kestra_migration_assistant import config as K
    from kestra_migration_assistant.vra import cli, interactive
    monkeypatch.setattr(K, "has_anthropic_package", lambda: False, raising=False)
    monkeypatch.setattr(K, "claude_code_ready", lambda: False)
    monkeypatch.setitem(sys.modules, "anthropic", None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    export = _export(tmp_path)
    data = os.path.join(export, "kestra-assessment", "data")
    os.makedirs(data)
    with open(os.path.join(data, "model.json"), "w", encoding="utf-8") as f:
        _json.dump({"provider": "anthropic", "model": "claude-haiku-5-5"}, f)
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", out)
    monkeypatch.setattr(cli, "is_terminal", lambda: True)
    monkeypatch.setattr(interactive, "can_open_browser", lambda: False)
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n" * 20))
    monkeypatch.delenv("VRO_NO_MODEL", raising=False)
    code = cli.main(["run", export], interactive=True)
    text = out.getvalue()
    assert "The model chosen earlier for this export cannot be used: the anthropic provider needs" in text
    assert code == 2 and "The model cannot be used" in text        # the usual model here is the same key route
