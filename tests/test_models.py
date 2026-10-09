"""
models.yaml: the loader, the tier each purpose uses, and which provider answers.

No real model calls: providers are replaced with fakes, and subprocess / HTTP are never reached.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from kestra_migration_assistant.vra import model as M
from kestra_migration_assistant.vra import safety

SCHEMA = {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}


def _write(tmp_path, text):
    p = tmp_path / "models.yaml"
    p.write_text(text)
    return str(p)


def test_repo_models_yaml_defaults_both_tiers_to_claude_code():
    cfg = M.load_config(os.path.join(ROOT, "models.yaml"))
    for tier in ("reasoning", "drafting"):
        assert cfg[tier]["provider"] == "anthropic" and cfg[tier]["auth"] == "claude-code"
        assert cfg[tier]["model"].startswith("claude-")


def test_missing_file_means_defaults(tmp_path):
    cfg = M.load_config(str(tmp_path / "nope.yaml"))
    assert cfg == M.DEFAULT_CONFIG


def test_openai_compatible_needs_base_url(tmp_path):
    with pytest.raises(M.ConfigError, match="base_url"):
        M.load_config(_write(tmp_path, "reasoning:\n  provider: openai-compatible\n  model: m\n"))


def test_openai_compatible_with_env_name(tmp_path):
    cfg = M.load_config(_write(tmp_path, "drafting:\n  provider: openai-compatible\n  base_url: http://localhost:11434/v1\n"
                                         "  api_key_env: OLLAMA_KEY\n  model: qwen\n"))
    assert cfg["drafting"]["auth"] == "api-key-env" and cfg["reasoning"] == M.DEFAULT_CONFIG["reasoning"]


@pytest.mark.parametrize("bad", ["  api_key: sk-ant-123\n", "  token: abc\n", "  model: sk-live-abc\n"])
def test_a_key_in_the_file_is_refused(tmp_path, bad):
    text = "reasoning:\n  provider: anthropic\n  auth: api-key\n" + ("  model: m\n" if "model" not in bad else "") + bad
    with pytest.raises(M.ConfigError, match="Keys never go in this file"):
        M.load_config(_write(tmp_path, text))


@pytest.mark.parametrize("text,msg", [
    ("reasoning:\n  provider: bedrock\n  model: m\n", "provider must be"),
    ("reasoning:\n  provider: anthropic\n  auth: oauth\n  model: m\n", "auth must be"),
    ("reasoning:\n  provider: anthropic\n", "model is required"),
    ("fast:\n  provider: anthropic\n  model: m\n", "unknown tier"),
    ("reasoning:\n  provider: openai-compatible\n  base_url: http://x\n  api_key_env: 'my key'\n  model: m\n",
     "environment variable name"),
])
def test_invalid_configs(tmp_path, text, msg):
    with pytest.raises(M.ConfigError, match=msg):
        M.load_config(_write(tmp_path, text))


def test_tiers_by_purpose():
    assert {p: M.tier_for(p) for p in ("understand", "cluster-summary", "tests", "review")} == dict.fromkeys(
        ("understand", "cluster-summary", "tests", "review"), "reasoning")
    assert {p: M.tier_for(p) for p in ("convert", "stages-domain-names", "stages-walkthroughs")} == dict.fromkeys(
        ("convert", "stages-domain-names", "stages-walkthroughs"), "drafting")
    assert M.tier_for("something-new") == "reasoning"


def test_provider_selection():
    assert isinstance(M.provider_for({"provider": "anthropic", "auth": "claude-code", "model": "m"}), M.ClaudeCodeProvider)
    assert isinstance(M.provider_for({"provider": "anthropic", "auth": "api-key", "model": "m"}), M.AnthropicAPIProvider)
    assert isinstance(M.provider_for({"provider": "openai-compatible", "auth": "none", "model": "m",
                                      "base_url": "http://x"}), M.OpenAICompatibleProvider)


def test_api_key_provider_reads_the_environment_variable(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(M.ModelError, match="ANTHROPIC_API_KEY"):
        M.AnthropicAPIProvider({"provider": "anthropic", "auth": "api-key", "model": "m"}).invoke("p", SCHEMA)


def test_openai_provider_reads_the_named_variable(monkeypatch):
    monkeypatch.delenv("VRO_TEST_KEY", raising=False)
    p = M.OpenAICompatibleProvider({"provider": "openai-compatible", "auth": "api-key-env", "model": "m",
                                    "base_url": "http://127.0.0.1:9", "api_key_env": "VRO_TEST_KEY"})
    with pytest.raises(M.ModelError, match="VRO_TEST_KEY"):
        p.invoke("p", SCHEMA)


# ---- ask(): tier and model recorded, cache per model, guard enforced -------

class Fake(M.Provider):
    calls = []

    def invoke(self, prompt, schema):
        Fake.calls.append((self.cfg["model"], prompt))
        return {"result": json.dumps({"ok": True}), "is_error": False, "total_cost_usd": 0.01, "duration_api_ms": 5}


@pytest.fixture
def sandboxed(tmp_path, monkeypatch):
    cfg = tmp_path / "models.yaml"
    cfg.write_text("reasoning:\n  provider: anthropic\n  auth: api-key\n  model: big\n"
                   "drafting:\n  provider: openai-compatible\n  base_url: http://x\n  model: small\n")
    monkeypatch.setattr(M, "CONFIG_PATH", str(cfg))
    monkeypatch.setattr(M, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(M, "LOG_PATH", str(tmp_path / "calls.jsonl"))
    monkeypatch.setattr(M, "provider_for", lambda c: Fake(c))
    M._cfg_cache.clear()
    Fake.calls = []
    yield tmp_path
    M._cfg_cache.clear()


def test_ask_records_tier_provider_and_model(sandboxed):
    p = safety.guard("do it " + safety.wrap("x", "data"))
    assert M.ask("understand", "w1", p, SCHEMA) == {"ok": True}
    assert M.ask("convert", "w1", p, SCHEMA) == {"ok": True}
    assert [m for m, _ in Fake.calls] == ["big", "small"]
    log = [json.loads(line) for line in open(sandboxed / "calls.jsonl")]
    assert [(r["tier"], r["provider"], r["model"]) for r in log] == [
        ("reasoning", "anthropic", "big"), ("drafting", "openai-compatible", "small")]


def test_ask_caches_per_model(sandboxed):
    p = safety.guard("do it")
    M.ask("understand", "w", p, SCHEMA)
    M.ask("understand", "w", p, SCHEMA)
    assert len(Fake.calls) == 1
    log = [json.loads(line) for line in open(sandboxed / "calls.jsonl")]
    assert log[1]["cached"] is True and log[1]["model"] == "big"


def test_ask_refuses_an_unguarded_prompt(sandboxed):
    with pytest.raises(M.ModelError, match="untrusted-input notice"):
        M.ask("understand", "w", "no notice here", SCHEMA)
    assert Fake.calls == []


def test_ask_retries_once_on_schema_failure(sandboxed, monkeypatch):
    replies = iter([{"result": '{"nope": 1}', "is_error": False}, {"result": '{"ok": false}', "is_error": False}])

    class Flaky(M.Provider):
        def invoke(self, prompt, schema):
            return next(replies)
    monkeypatch.setattr(M, "provider_for", lambda c: Flaky(c))
    assert M.ask("understand", "w", safety.guard("x"), SCHEMA) == {"ok": False}


def test_quota_errors_back_off_and_resume(sandboxed, monkeypatch):
    replies = iter([{"result": "rate limit: slow down", "is_error": True}, {"result": '{"ok": true}', "is_error": False}])
    waits = []

    class Limited(M.Provider):
        def invoke(self, prompt, schema):
            return next(replies)
    monkeypatch.setattr(M, "provider_for", lambda c: Limited(c))
    monkeypatch.setattr(M.time, "sleep", lambda s: waits.append(s))
    assert M.ask("understand", "w", safety.guard("x"), SCHEMA) == {"ok": True}
    assert waits == [M.QUOTA_BACKOFF_S[0]]


def test_legacy_cache_is_reused_for_claude_code(tmp_path, monkeypatch):
    """Replies cached before models.yaml (headless claude, default model) are reused, not paid again."""
    cfg = tmp_path / "models.yaml"
    cfg.write_text("reasoning:\n  provider: anthropic\n  auth: claude-code\n  model: claude-fable-5-1\n")
    monkeypatch.setattr(M, "CONFIG_PATH", str(cfg))
    monkeypatch.setattr(M, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(M, "LOG_PATH", str(tmp_path / "calls.jsonl"))
    M._cfg_cache.clear()
    os.makedirs(tmp_path / "cache")
    p = safety.guard("old prompt")
    with open(tmp_path / "cache" / f"{M._legacy_key(p, SCHEMA)}.json", "w") as f:
        json.dump({"value": {"ok": True}}, f)
    monkeypatch.setattr(M, "provider_for", lambda c: pytest.fail("must not call the model"))
    assert M.ask("understand", "w", p, SCHEMA) == {"ok": True}
    rec = json.loads(open(tmp_path / "calls.jsonl").read().splitlines()[0])
    assert rec["legacy_cache"] is True
    M._cfg_cache.clear()


def test_a_tier_may_set_its_own_timeout_and_it_does_not_change_the_cache_key(tmp_path):
    import pytest as _pt

    from kestra_migration_assistant.vra import model as M
    p = tmp_path / "models.yaml"
    p.write_text("drafting:\n  provider: anthropic\n  model: m\n  timeout_s: 1234\n")
    cfg = M.load_config(str(p))
    assert M.timeout_of(cfg["drafting"]) == 1234 and M.timeout_of(cfg["reasoning"]) == M.TIMEOUT_S
    assert M._key("p", {}, cfg["drafting"]) == M._key("p", {}, {k: v for k, v in cfg["drafting"].items()
                                                                 if k != "timeout_s"})
    p.write_text("drafting:\n  provider: anthropic\n  model: m\n  timeout_s: soon\n")
    with _pt.raises(M.ConfigError):
        M.load_config(str(p))
