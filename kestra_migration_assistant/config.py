"""
Which model `kestra-migrate` calls, and how it connects. One model answers every call.

Three ways to connect:

  claude-code        the user's own Claude Code login, through `claude -p`
  anthropic          an Anthropic API key
  openai-compatible  a base URL and a key: a self-hosted model, or Azure OpenAI and Amazon Bedrock through a
                     proxy such as LiteLLM

Each setting comes from the first of these that gives it: a command-line flag, an environment variable
(KESTRA_MIGRATE_PROVIDER, KESTRA_MIGRATE_MODEL, KESTRA_MIGRATE_BASE_URL), the config file that
`kestra-migrate init` writes in the user's home folder, then the defaults. With no provider set anywhere,
`claude-code` is used if `claude` is installed and logged in, else `anthropic` if ANTHROPIC_API_KEY is set,
else the run goes ahead without a model and says how to add one.

The key is read from an environment variable the file names (`api_key_env`). The file may hold the key itself
(`api_key`) only when nobody but its owner can read it. Nothing here is ever written to the export folder.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass, field

PROVIDERS = ("claude-code", "anthropic", "openai-compatible")
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "openai-compatible": "OPENAI_API_KEY"}
ENV = {"provider": "KESTRA_MIGRATE_PROVIDER", "model": "KESTRA_MIGRATE_MODEL", "base_url": "KESTRA_MIGRATE_BASE_URL"}
FILE_KEYS = ("provider", "model", "base_url", "api_key_env", "api_key", "timeout_s", "max_cost_usd")
# Above this estimate (USD) a run asks before its first billed call; max_cost_usd in the config file changes it
DEFAULT_MAX_COST_USD = 20.0
# The call to action at the end of every report, to the landing page's form. Written here only.
HELP_URL = "https://kestra.io/resources/migration/vra/assistant?utm_source=migration-assistant&utm_medium=report"
HELP_ASK = "Need help running it, or want us to run it for you?"


# Set by the container image. A Claude Code login cannot be used inside it, and a config file written there is lost
# when the container stops, so the model is set with environment variables.
CONTAINER_ENV = "KESTRA_MIGRATE_CONTAINER"
CONTAINER_MODELS = ("an Anthropic API key (-e ANTHROPIC_API_KEY), or an OpenAI-compatible endpoint "
                    "(-e KESTRA_MIGRATE_PROVIDER=openai-compatible -e KESTRA_MIGRATE_BASE_URL -e KESTRA_MIGRATE_MODEL "
                    "-e OPENAI_API_KEY)")
CONTAINER_HELP = (f"In the container, a Claude Code login cannot be used. Pass {CONTAINER_MODELS}, or run with "
                  "--no-model.")


# Installed from the repository's version tag, with git; pipx and pip take the same git+ URL
REPO_URL = "https://github.com/kestra-io/migration-assistant"


def install_command(extra: str | None = None) -> str:
    """The command that installs this version, from its tag: `uv tool install git+...@v<version>`."""
    from kestra_migration_assistant import __version__
    url = f"git+{REPO_URL}@v{__version__}"
    return f"uv tool install '{f'kestra-migration-assistant[{extra}] @ {url}'}'" if extra else f"uv tool install {url}"


class ConfigError(ValueError):
    pass


def in_container() -> bool:
    return os.environ.get(CONTAINER_ENV) == "1"


@dataclass
class Settings:
    """The resolved settings. `provider` None means no model: `why` says why, and how to add one."""
    provider: str | None
    model: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    api_key: str | None = field(default=None, repr=False)
    timeout_s: int | None = None
    max_cost_usd: float = DEFAULT_MAX_COST_USD
    source: dict[str, str] = field(default_factory=dict)     # setting -> where it came from
    path: str = ""                                           # the config file looked at
    why: str = ""

    def key(self) -> str | None:
        """The API key: from the file when it holds one, else from the environment variable it names."""
        return self.api_key or (os.environ.get(self.api_key_env) if self.api_key_env else None)

    def describe(self) -> str:
        """One line for the terminal, never the key."""
        if not self.provider:
            return "no model"
        how = {"claude-code": "your Claude Code login", "anthropic": f"an Anthropic API key ({self.api_key_env})",
               "openai-compatible": f"{self.base_url}"}[self.provider]
        if self.provider == "anthropic" and self.api_key:
            how = "an Anthropic API key (from the config file)"
        return f"{self.model} through {how}"


def config_path() -> str:
    """~/.config/kestra-migrate/config.yaml ($XDG_CONFIG_HOME when set); %APPDATA%\\kestra-migrate on Windows."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "kestra-migrate", "config.yaml")


def owner_only(path: str) -> bool:
    """True when nobody but the file's owner can read or write it. On Windows a file under %APPDATA% is the
    user's own and has no mode bits to check."""
    if sys.platform == "win32":
        return True
    return not (os.stat(path).st_mode & (stat.S_IRWXG | stat.S_IRWXO))


def read_file(path: str) -> dict:
    """The config file's settings, checked. A missing file is no settings."""
    if not os.path.exists(path):
        return {}
    import yaml
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: expected settings like `provider: claude-code`, one per line")
    unknown = sorted(set(raw) - set(FILE_KEYS))
    if unknown:
        raise ConfigError(f"{path}: unknown setting(s) {', '.join(unknown)}; the settings are {', '.join(FILE_KEYS)}")
    out = {k: v for k, v in raw.items() if v not in (None, "")}
    for k in ("provider", "model", "base_url", "api_key_env", "api_key"):
        if k in out and not isinstance(out[k], str):
            raise ConfigError(f"{path}: {k} must be text")
    if "api_key" in out and not owner_only(path):
        raise ConfigError(f"{path} holds an API key but others can read it. Run `chmod 600 {path}`, or remove "
                          "api_key and put the key in the environment variable named by api_key_env")
    env = out.get("api_key_env")
    if env is not None and not env.replace("_", "").isalnum():
        raise ConfigError(f"{path}: api_key_env must be the name of an environment variable, like ANTHROPIC_API_KEY, "
                          "not the key itself")
    if "timeout_s" in out and not (isinstance(out["timeout_s"], int) and out["timeout_s"] > 0):
        raise ConfigError(f"{path}: timeout_s must be a whole number of seconds")
    if "max_cost_usd" in out and (isinstance(out["max_cost_usd"], bool)
                                  or not isinstance(out["max_cost_usd"], (int, float)) or out["max_cost_usd"] < 0):
        raise ConfigError(f"{path}: max_cost_usd must be a number of US dollars, like 20")
    return out


def claude_code_ready() -> bool:
    """`claude` is installed and logged in."""
    exe = shutil.which("claude")
    if not exe:
        return False
    try:
        r = subprocess.run([exe, "auth", "status"], capture_output=True, text=True, timeout=30)
        return r.returncode == 0 and bool(json.loads(r.stdout).get("loggedIn"))
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


def resolve(flags: dict | None = None, path: str | None = None, detect=None) -> Settings:
    """The settings, from flags, then the environment, then the config file, then the defaults."""
    path = path or config_path()
    detect = detect or (lambda: False if in_container() else claude_code_ready())
    layers = [("command line", {k: v for k, v in (flags or {}).items() if v}),
              ("environment", {k: os.environ[e] for k, e in ENV.items() if os.environ.get(e)}),
              (path, read_file(path))]
    got: dict = {}
    source: dict[str, str] = {}
    for where, values in layers:
        for k, v in values.items():
            if k not in got:
                got[k], source[k] = v, where
    prov = got.get("provider")
    if prov is None:
        if detect():
            prov, source["provider"] = "claude-code", "found: claude is installed and logged in"
        elif os.environ.get("ANTHROPIC_API_KEY"):
            prov, source["provider"] = "anthropic", "found: ANTHROPIC_API_KEY is set"
        else:
            return Settings(None, path=path, why=no_model_help(path))
    cap = float(got.get("max_cost_usd", DEFAULT_MAX_COST_USD))
    if prov not in PROVIDERS:
        raise ConfigError(f"provider {prov!r} ({source['provider']}) is not one of {', '.join(PROVIDERS)}")
    if prov == "claude-code" and in_container():
        raise ConfigError(f"provider claude-code ({source['provider']}) is not available. {CONTAINER_HELP}")
    s = Settings(prov, model=got.get("model"), base_url=got.get("base_url"), api_key_env=got.get("api_key_env"),
                 api_key=got.get("api_key"), timeout_s=got.get("timeout_s"), max_cost_usd=cap, source=source,
                 path=path)
    if prov == "openai-compatible":
        if not s.base_url:
            raise ConfigError("provider openai-compatible needs base_url: the server's address, like "
                              "http://localhost:4000/v1 (--base-url, KESTRA_MIGRATE_BASE_URL or the config file)")
        if not s.model:
            raise ConfigError("provider openai-compatible needs model: the name your server gives the model")
    if not s.model:
        s.model, s.source["model"] = DEFAULT_MODEL, "default"
    if prov != "claude-code" and not s.api_key:
        s.api_key_env = s.api_key_env or DEFAULT_KEY_ENV[prov]
        if prov == "anthropic" and not s.key():
            raise ConfigError(f"provider anthropic needs an API key in {s.api_key_env}")
    return s


def no_model_help(path: str) -> str:
    if in_container():
        return ("No model is set up, so this run uses no model: everything the code can find is in the brief, and "
                f"what needs a model is marked. To add one, pass {CONTAINER_MODELS}.")
    return ("No model is set up, so this run uses no model: everything the code can find is in the brief, and "
            "what needs a model is marked. To add one, either log in to Claude Code (`claude auth login`), set "
            f"ANTHROPIC_API_KEY, or run `kestra-migrate init` and edit {path}.")


TEMPLATE = """\
# kestra-migrate: which model it calls, and how it connects.
# Written by `kestra-migrate init`. A command-line flag or an environment variable overrides any line here
# (KESTRA_MIGRATE_PROVIDER, KESTRA_MIGRATE_MODEL, KESTRA_MIGRATE_BASE_URL).
# This file stays here: nothing from it is written to the export folder.

# How to connect. One of:
#   claude-code        your own Claude Code login. The `claude` command must be installed and logged in.
#   anthropic          an Anthropic API key.
#   openai-compatible  any server with an OpenAI-style chat completions API: a model you host yourself,
#                      or Azure OpenAI and Amazon Bedrock through a proxy such as LiteLLM.
provider: {provider}

# The model that answers every call. For claude-code and anthropic, a Claude model ID
# (claude-opus-5-5 is the tested default). For openai-compatible, the name your server gives the model.
model: {model}

# openai-compatible only: the server's address, ending in /v1.
# base_url: http://localhost:4000/v1

# anthropic and openai-compatible: the name of the environment variable that holds the key.
# If left out: ANTHROPIC_API_KEY for anthropic, OPENAI_API_KEY for openai-compatible.
# api_key_env: ANTHROPIC_API_KEY

# Or the key itself. This file was created so that only you can read it, and the tool refuses a key here
# if anyone else can. An environment variable is still the safer place.
# api_key:

# How long one call may take, in seconds, before it is given up. The default is 2700 (45 minutes).
# timeout_s: 2700

# Before its first billed call, a run estimates what it will cost and asks to go on when the estimate is above
# this many US dollars (`--yes` goes on without asking). With claude-code, the estimate is what the calls would
# cost at API prices; they count against your Claude plan instead of being billed separately.
# max_cost_usd: 20
"""


def init(path: str | None = None, force: bool = False, detect=None) -> tuple[str, str]:
    """Write the commented config file, readable by its owner only. Returns (path, the provider written)."""
    path = path or config_path()
    detect = detect or claude_code_ready
    if os.path.exists(path) and not force:
        raise FileExistsError(path)
    provider = "claude-code" if detect() or not os.environ.get("ANTHROPIC_API_KEY") else "anthropic"
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    if os.path.exists(path):
        os.remove(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(TEMPLATE.format(provider=provider, model=DEFAULT_MODEL))
    return path, provider
