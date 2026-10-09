"""
The model settings: the order they are read in (flags, environment, config file, defaults), the fallback when
nothing is set up, the file `kestra-migrate init` writes, and where the key may live. Nothing calls a model.
"""
from __future__ import annotations

import os
import stat
import sys

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from kestra_migration_assistant import cli
from kestra_migration_assistant import config as K


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for v in (*K.ENV.values(), "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "MY_KEY"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "home-config"))
    monkeypatch.setattr(K, "claude_code_ready", lambda: False)


def _file(tmp_path, text, mode=0o600):
    p = tmp_path / "config.yaml"
    p.write_text(text)
    p.chmod(mode)
    return str(p)


def test_the_config_file_is_in_the_home_folder_never_the_export(tmp_path, monkeypatch):
    assert K.config_path() == str(tmp_path / "home-config" / "kestra-migrate" / "config.yaml")
    monkeypatch.setattr(K.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", r"C:\Users\ana\AppData\Roaming")
    assert K.config_path() == os.path.join(r"C:\Users\ana\AppData\Roaming", "kestra-migrate", "config.yaml")


def test_flags_then_environment_then_file_then_defaults(tmp_path, monkeypatch):
    path = _file(tmp_path, "provider: openai-compatible\nmodel: file-model\nbase_url: http://file/v1\n")
    s = K.resolve(path=path)
    assert (s.provider, s.model, s.base_url) == ("openai-compatible", "file-model", "http://file/v1")
    assert s.source["model"] == path
    monkeypatch.setenv("KESTRA_MIGRATE_MODEL", "env-model")
    s = K.resolve(path=path)
    assert s.model == "env-model" and s.source["model"] == "environment" and s.base_url == "http://file/v1"
    s = K.resolve({"model": "flag-model", "provider": None}, path=path)
    assert s.model == "flag-model" and s.source["model"] == "command line"
    # nothing anywhere for the model: the default
    monkeypatch.delenv("KESTRA_MIGRATE_MODEL")
    s = K.resolve({"provider": "claude-code"}, path=str(tmp_path / "none.yaml"))
    assert s.model == K.DEFAULT_MODEL and s.source["model"] == "default"


def test_with_nothing_set_claude_code_is_used_when_installed_and_logged_in(tmp_path):
    s = K.resolve(path=str(tmp_path / "none.yaml"), detect=lambda: True)
    assert s.provider == "claude-code" and s.model == K.DEFAULT_MODEL
    assert s.describe() == f"{K.DEFAULT_MODEL} through your Claude Code login"


def test_with_nothing_set_and_no_claude_an_api_key_is_used(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    s = K.resolve(path=str(tmp_path / "none.yaml"))
    assert (s.provider, s.api_key_env, s.key()) == ("anthropic", "ANTHROPIC_API_KEY", "x")


def test_with_nothing_set_up_the_run_has_no_model_and_says_how_to_add_one(tmp_path):
    s = K.resolve(path=str(tmp_path / "none.yaml"))
    assert s.provider is None
    assert "claude auth login" in s.why and "ANTHROPIC_API_KEY" in s.why and "kestra-migrate init" in s.why


def test_init_writes_a_commented_file_only_its_owner_can_read(tmp_path, capsys):
    assert cli.main(["init"]) == 0
    path = K.config_path()
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    text = open(path).read()
    assert yaml.safe_load(text) == {"provider": "claude-code", "model": K.DEFAULT_MODEL}
    for setting in ("provider:", "model:", "base_url:", "api_key_env:", "api_key:", "timeout_s:"):
        i = text.index(setting)
        assert text[:i].rstrip().splitlines()[-1].startswith("#"), f"{setting} has a comment above it"
    for words in ("claude-code", "anthropic", "openai-compatible", "LiteLLM", "export folder"):
        assert words in text
    assert "—" not in text
    out = capsys.readouterr().out
    assert path in out and "readable by you only" in out
    # never over an existing file without --force
    assert cli.main(["init"]) == 1
    assert cli.main(["init", "--force"]) == 0


def test_a_key_in_the_file_only_when_nobody_else_can_read_it(tmp_path, monkeypatch):
    text = "provider: anthropic\napi_key: file-held-placeholder\n"
    s = K.resolve(path=_file(tmp_path, text))
    assert s.key() == "file-held-placeholder" and "file-held-placeholder" not in repr(s)
    if sys.platform != "win32":
        with pytest.raises(K.ConfigError, match="chmod 600"):
            K.resolve(path=_file(tmp_path, text, mode=0o644))


def test_the_key_comes_from_the_variable_the_file_names(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_KEY", "from-env")
    s = K.resolve(path=_file(tmp_path, "provider: openai-compatible\nbase_url: http://x/v1\nmodel: m\n"
                                       "api_key_env: MY_KEY\n"))
    assert s.key() == "from-env"
    with pytest.raises(K.ConfigError, match="name of an environment variable"):
        K.resolve(path=_file(tmp_path, "provider: anthropic\napi_key_env: 'abc def'\n"))


@pytest.mark.parametrize("text,err", [
    ("provider: bedrock\n", "not one of"),
    ("provider: openai-compatible\nmodel: m\n", "needs base_url"),
    ("provider: openai-compatible\nbase_url: http://x/v1\n", "needs model"),
    ("provider: anthropic\n", "needs an API key in ANTHROPIC_API_KEY"),
    ("reasoning:\n  model: m\n", "unknown setting"),
    ("provider: claude-code\ntimeout_s: soon\n", "whole number"),
])
def test_a_wrong_setting_is_a_clear_error(tmp_path, text, err):
    with pytest.raises(K.ConfigError, match=err):
        K.resolve(path=_file(tmp_path, text))


def test_no_model_setting_is_written_to_the_export_folder(tmp_path, monkeypatch):
    """A run with a config file set up: nothing in the output folder names the provider, base URL or key."""
    from kestra_migration_assistant.vra import cli as vra_cli
    path = K.config_path()
    os.makedirs(os.path.dirname(path))
    with open(path, "w") as f:
        f.write("provider: openai-compatible\nbase_url: http://models.internal.example/v1\nmodel: local-m\n"
                "api_key: file-held-placeholder\n")
    os.chmod(path, 0o600)
    helpers = os.path.join(ROOT, "tests", "vra", "conformance", "helpers")
    import shutil
    export = tmp_path / "export"
    shutil.copytree(os.path.join(helpers, "vro"), export / "vro")
    monkeypatch.setenv("VRO_NO_MODEL", "1")         # resolve the settings, but make no call
    from kestra_migration_assistant.vra import model
    monkeypatch.setattr(model, "SETTINGS", None)
    try:
        assert vra_cli.main(["all", str(export)]) == 0
    finally:
        model.configure(None)
    for d, _, files in os.walk(export / "kestra-assessment"):
        for f in files:
            body = open(os.path.join(d, f), "rb").read()
            for secret in (b"models.internal.example", b"file-held-placeholder"):
                assert secret not in body, (f, secret)


def test_a_run_set_to_claude_code_without_claude_says_so_before_starting(tmp_path, monkeypatch, capsys):
    from kestra_migration_assistant.vra import cli as vra_cli
    monkeypatch.setattr(vra_cli.shutil, "which", lambda name: None)
    assert vra_cli.main(["run", str(tmp_path / "acme"), "--provider", "claude-code"]) == 2
    err = capsys.readouterr().err
    assert "needs the `claude` command" in err and "--provider" in err
