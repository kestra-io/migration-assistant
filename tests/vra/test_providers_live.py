"""
The anthropic and openai-compatible providers, called for real. Every test here is skipped unless its
environment is set, so `make check` never calls a model:

- openai-compatible: KESTRA_MIGRATE_LIVE_OPENAI_BASE_URL (for Ollama, http://localhost:11434/v1) and
  KESTRA_MIGRATE_LIVE_OPENAI_MODEL (a model the server has, e.g. qwen3:4b). OPENAI_API_KEY is sent if set.
- anthropic: ANTHROPIC_API_KEY, and the SDK (`uv sync --extra anthropic`). It bills: one short call, then the
  synthetic helpers estate (two workflows), on KESTRA_MIGRATE_LIVE_ANTHROPIC_MODEL (default claude-haiku-5-5,
  the cheapest), a few cents.

For each provider: one call returns JSON that matches its schema, is logged with the provider and model, and is
cached (a second call is not sent); then `kestra-migrate vra run` on the helpers estate finishes every stage with
a spec for each workflow, and a second run sends no call.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil

import pytest

from kestra_migration_assistant import config as K
from kestra_migration_assistant.vra import model as M
from kestra_migration_assistant.vra import safety

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HELPERS = os.path.join(ROOT, "tests", "vra", "conformance", "helpers")
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["system", "writes"],
          "properties": {"system": {"type": "string"}, "writes": {"type": "boolean"}}}
PROMPT = safety.guard("Read the action below. Which system does it call, and does the call change anything there? "
                      + safety.wrap("System.getModule('net').call('POST', 'https://ipam.example/api/records', body)",
                                    "action source"))

OPENAI_URL = os.environ.get("KESTRA_MIGRATE_LIVE_OPENAI_BASE_URL")
OPENAI_MODEL = os.environ.get("KESTRA_MIGRATE_LIVE_OPENAI_MODEL")
ANTHROPIC_MODEL = os.environ.get("KESTRA_MIGRATE_LIVE_ANTHROPIC_MODEL", "claude-haiku-5-5")

openai_live = pytest.mark.skipif(not (OPENAI_URL and OPENAI_MODEL),
                                 reason="set KESTRA_MIGRATE_LIVE_OPENAI_BASE_URL and KESTRA_MIGRATE_LIVE_OPENAI_MODEL")
anthropic_live = pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY") or not importlib.util.find_spec("anthropic"),
                                    reason="set ANTHROPIC_API_KEY and install the anthropic extra; this bills")

PROVIDERS = [
    pytest.param(lambda: K.Settings("openai-compatible", model=OPENAI_MODEL, base_url=OPENAI_URL,
                                    api_key_env="OPENAI_API_KEY", timeout_s=1800),
                 ["--provider", "openai-compatible", "--model", str(OPENAI_MODEL), "--base-url", str(OPENAI_URL)],
                 marks=openai_live, id="openai-compatible"),
    pytest.param(lambda: K.Settings("anthropic", model=ANTHROPIC_MODEL, api_key_env="ANTHROPIC_API_KEY"),
                 ["--provider", "anthropic", "--model", ANTHROPIC_MODEL],
                 marks=anthropic_live, id="anthropic"),
]


@pytest.mark.parametrize("settings,flags", PROVIDERS)
def test_live_one_call_matches_its_schema_and_is_cached(settings, flags, tmp_path, monkeypatch):
    s = settings()
    monkeypatch.setattr(M, "SETTINGS", s)
    monkeypatch.setattr(M, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(M, "LOG_PATH", str(tmp_path / "calls.jsonl"))
    r = M.ask("understand", "live", PROMPT, SCHEMA)
    assert set(r) == {"system", "writes"} and isinstance(r["writes"], bool)
    assert r == M.ask("understand", "live", PROMPT, SCHEMA)
    log = [json.loads(x) for x in open(tmp_path / "calls.jsonl")]
    assert [x["cached"] for x in log] == [False, True]
    assert log[0]["model"] == s.model and not log[0]["is_error"]


@pytest.mark.parametrize("settings,flags", PROVIDERS)
def test_live_the_helpers_estate_runs_end_to_end(settings, flags, tmp_path, monkeypatch):
    from kestra_migration_assistant import cli
    monkeypatch.delenv("VRO_NO_MODEL", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "no-config"))      # nothing from this machine's config
    export = tmp_path / "helpers-estate"
    shutil.copytree(os.path.join(HELPERS, "vro"), export / "vro")
    shutil.copytree(os.path.join(HELPERS, "vra"), export / "helpers-vra")
    assert cli.main(["vra", "run", str(export), *flags]) == 0
    out = export / "kestra-assessment"
    specs = [f for f in os.listdir(out / "data" / "2-understand")
             if f.endswith(".json") and f not in ("PROVENANCE.json", "cluster-summaries.json")]
    brief = json.load(open(out / "data" / "3-brief" / "brief.json"))
    assert len(specs) == len(brief["triage"]) == 2                          # a spec for each workflow
    assert all(t["outcome"] != "Needs a model" for t in brief["triage"].values())
    calls = [json.loads(x) for x in open(out / "data" / "model-calls.jsonl")]
    assert calls and all(not c["is_error"] for c in calls if not c["cached"])
    n = len(calls)
    assert cli.main(["vra", "run", str(export), *flags]) == 0               # again: every reply from the cache
    again = [json.loads(x) for x in open(out / "data" / "model-calls.jsonl")][n:]
    assert again and all(c["cached"] for c in again)
