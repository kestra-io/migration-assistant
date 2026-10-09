"""
The one place that talks to a model.

One model answers every call. Which one, and how it connects, is resolved by
`kestra_migration_assistant.config` (flags, environment, the user's config file, defaults):

  claude-code        headless `claude -p` on the user's own Claude Code login. Each call runs in a fresh,
                     empty, private folder made for it and removed after it, with project settings, hooks,
                     CLAUDE.md files, MCP servers, skills and tools turned off
  anthropic          the official `anthropic` SDK, with an API key
  openai-compatible  POST {base_url}/chat/completions, with a key (a self-hosted model, or Azure OpenAI and
                     Bedrock through a proxy such as LiteLLM)

Contract
--------
ask(purpose, workflow_id, prompt, schema) -> dict validated against schema

- The prompt must start with the untrusted-input notice (`safety.guard`); an
  unguarded prompt is refused before anything is sent.
- Cached on sha256(provider + auth + model + prompt + schema). A rerun costs
  nothing and returns the same bytes.
- Structured output is enforced twice: the provider is given the JSON schema,
  and the reply is validated here. On a validation failure the call is retried
  once with the validation error appended, then raises.
- At most MAX_PARALLEL calls in flight; quota and rate limits back off and resume.
- Every call is appended to the call log with purpose, provider, model,
  workflow, ms and cost.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request

from kestra_migration_assistant import config as K

from . import safety

# Where replies are cached and calls logged: set by the caller before the first call (stages/common.py's
# model() points both at the estate's output folder). Nothing is written anywhere until they are set.
CACHE_DIR: str | None = None
LOG_PATH: str | None = None
LEGACY_BACKEND = "claude-code-headless"     # the cache key every call before the model settings used
MAX_PARALLEL = 4
TIMEOUT_S = 2700                            # a whole flow of 80+ tasks takes longer than 15 minutes

# USD per million tokens (input, output), for the call log when a provider reports tokens, not cost.
PRICES = {"claude-fable-5-1": (10, 50), "claude-fable-5": (10, 50), "claude-opus-5-5": (4, 20),
          "claude-opus-5": (5, 25), "claude-opus-4-8": (5, 25), "claude-sonnet-5-5": (2, 10),
          "claude-sonnet-5": (2, 10), "claude-sonnet-4-6": (3, 15), "claude-haiku-5-5": (0.1, 0.5),
          "claude-haiku-4-5": (1, 5)}

# The settings every call uses: set by the command line (configure), else resolved on the first call.
SETTINGS: K.Settings | None = None


def configure(settings: K.Settings | None) -> None:
    global SETTINGS
    SETTINGS = settings


def settings() -> K.Settings:
    global SETTINGS
    if SETTINGS is None:
        SETTINGS = K.resolve()
    return SETTINGS


def call_config(s: K.Settings) -> dict:
    """What a call is cached and logged under. The provider and auth strings are the ones replies were
    cached under before the three providers, so a cache made then is still found."""
    cfg: dict = {}
    if s.provider == "claude-code":
        cfg = {"provider": "anthropic", "auth": "claude-code"}
    elif s.provider == "anthropic":
        cfg = {"provider": "anthropic", "auth": "api-key"}
    else:
        cfg = {"provider": "openai-compatible", "auth": "api-key-env" if s.key() else "none",
               "base_url": s.base_url}
    cfg["model"] = s.model
    if s.timeout_s:
        cfg["timeout_s"] = s.timeout_s
    return cfg


# ---------------------------------------------------------------- claude-code backend and quota

# Trimming the dynamic sections of the system prompt cut a measured call from
# $0.234 to $0.078 with no change in output quality.
# Every prompt here is text in, JSON out, so the call gets nothing else: no tools (with tools the backend
# explored the folder it ran in and answered from files it found), and none of what Claude Code would load
# from that folder or the user's setup. --safe-mode turns off CLAUDE.md files, hooks, plugins, skills, MCP
# servers and custom agents; --setting-sources user skips project and local settings files. Without these,
# a CLAUDE.md or .claude/settings.json in the folder the call ran in could add instructions to every prompt
# or run commands through a hook (checked on Claude Code 2.1.295: a planted CLAUDE.md changed the reply and
# planted hooks ran; with these flags neither happened).
BASE_ARGS = [
    "--output-format", "json",
    "--exclude-dynamic-system-prompt-sections",
    "--safe-mode",
    "--setting-sources", "user",
    "--strict-mcp-config",
    "--disable-slash-commands",
    "--no-session-persistence",
    "--tools", "",
]

# Quota back-off. Headless `claude -p` shares the interactive session's quota,
# so a long run will hit a session limit partway through. That is backpressure,
# not failure: wait and resume.
QUOTA_BACKOFF_S = [60, 120, 300, 600, 900, 1800, 1800, 1800]
QUOTA_WAIT_BUDGET_S = 4 * 3600

_sem = threading.Semaphore(MAX_PARALLEL)
_log_lock = threading.Lock()

# One thread manages the wait; the rest park on the gate rather than each
# hammering the backend with calls that are certain to be refused.
_quota_gate = threading.Event()
_quota_gate.set()
_quota_lock = threading.Lock()
_quota_waited_s = 0.0
# Bumped once per completed wait. A thread that failed before the current
# generation does not need to sleep again: someone already waited for it.
_quota_generation = 0


class ModelError(RuntimeError):
    pass


class SchemaError(ValueError):
    pass


class QuotaError(ModelError):
    """
    The backend refused because the account or session ran out of quota.

    This is infrastructure, not a property of the workflow being converted, so
    it must never be recorded as that workflow failing. It happened here: a
    session limit at 05:38 produced 92 errors in three minutes, 90 of them
    "You've hit your session limit" and 2 rate-limit, with zero real failures
    among them. Without this distinction 22 workflows looked like content the
    converter could not handle.

    Raised only after the whole wait budget is spent. Short of that, `ask`
    backs off and resumes.
    """


QUOTA_MARKERS = (
    "session limit", "usage limit", "rate limit", "quota",
    "too many requests", "overloaded", "resets ",
)


def _is_quota(text: str) -> bool:
    t = (text or "").lower()
    return any(m in t for m in QUOTA_MARKERS)


def _key(prompt: str, schema: dict, cfg: dict) -> str:
    h = hashlib.sha256()
    h.update(f"{cfg['provider']}|{cfg['auth']}|{cfg.get('base_url', '')}".encode())
    h.update(cfg["model"].encode())
    h.update(json.dumps(schema, sort_keys=True).encode())
    h.update(prompt.encode())
    return h.hexdigest()


def _legacy_key(prompt: str, schema: dict) -> str:
    """The key calls made before the model settings used: headless claude, the CLI's default model."""
    h = hashlib.sha256()
    h.update(LEGACY_BACKEND.encode())
    h.update(b"default")
    h.update(json.dumps(schema, sort_keys=True).encode())
    h.update(prompt.encode())
    return h.hexdigest()


def _log(rec: dict) -> None:
    assert LOG_PATH is not None
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with _log_lock:
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(rec) + "\n")


# ---------------------------------------------------------------- validation

def validate(obj, schema: dict, path: str = "$") -> list[str]:
    """
    Enough of JSON Schema for the shapes used here: object with properties and
    required, array with items, the scalar types, and enum. Deliberately small
    and readable rather than a dependency.
    """
    errs: list[str] = []
    t = schema.get("type")
    if t == "object":
        if not isinstance(obj, dict):
            return [f"{path}: expected object, got {type(obj).__name__}"]
        for r in schema.get("required", []):
            if r not in obj:
                errs.append(f"{path}.{r}: required property missing")
        props = schema.get("properties", {})
        for k, v in obj.items():
            if k in props:
                errs += validate(v, props[k], f"{path}.{k}")
            elif schema.get("additionalProperties") is False:
                errs.append(f"{path}.{k}: unexpected property")
    elif t == "array":
        if not isinstance(obj, list):
            return [f"{path}: expected array, got {type(obj).__name__}"]
        if "items" in schema:
            for i, v in enumerate(obj):
                errs += validate(v, schema["items"], f"{path}[{i}]")
        if "minItems" in schema and len(obj) < schema["minItems"]:
            errs.append(f"{path}: needs at least {schema['minItems']} item(s), got {len(obj)}")
    elif t == "string":
        if not isinstance(obj, str):
            errs.append(f"{path}: expected string, got {type(obj).__name__}")
        elif schema.get("enum") and obj not in schema["enum"]:
            errs.append(f"{path}: {obj!r} not one of {schema['enum']}")
        elif schema.get("minLength") and len(obj) < schema["minLength"]:
            errs.append(f"{path}: shorter than {schema['minLength']}")
    elif t == "number":
        if not isinstance(obj, (int, float)) or isinstance(obj, bool):
            errs.append(f"{path}: expected number, got {type(obj).__name__}")
    elif t == "integer":
        if not isinstance(obj, int) or isinstance(obj, bool):
            errs.append(f"{path}: expected integer, got {type(obj).__name__}")
    elif t == "boolean":
        if not isinstance(obj, bool):
            errs.append(f"{path}: expected boolean, got {type(obj).__name__}")
    return errs


# ---------------------------------------------------------------- backend

def timeout_of(cfg: dict) -> int:
    """How long one call may take: `timeout_s` in the config file, else TIMEOUT_S. Not part of the cache key."""
    return int(cfg.get("timeout_s") or TIMEOUT_S)


class Provider:
    """One way of asking a model. `invoke` returns a normalised envelope:
    {"result": text, "is_error": bool, "total_cost_usd": float, "duration_api_ms": int}."""

    def __init__(self, cfg: dict, settings: K.Settings | None = None):
        self.cfg = cfg
        self.settings = settings

    def invoke(self, prompt: str, schema: dict) -> dict:
        raise NotImplementedError


class ClaudeCodeProvider(Provider):
    """Headless `claude -p` on the user's Claude Code login. Each call runs in a fresh, empty folder made for it
    (private to the user: mkdtemp creates it with mode 0700), which is removed afterwards."""

    def invoke(self, prompt: str, schema: dict) -> dict:
        exe = shutil.which("claude")
        if not exe:
            raise ModelError("provider claude-code needs the `claude` command, and it is not installed")
        args = [exe, "-p", prompt, *BASE_ARGS, "--json-schema", json.dumps(schema), "--model", self.cfg["model"]]
        workdir = tempfile.mkdtemp(prefix="kestra-migrate-call-")
        t0 = time.time()
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout_of(self.cfg), cwd=workdir)
        except subprocess.TimeoutExpired as e:
            raise ModelError(f"no reply within {timeout_of(self.cfg)} s (timeout_s in the config file)") from e
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        wall_ms = int((time.time() - t0) * 1000)
        if proc.returncode != 0 and not proc.stdout.strip():
            raise ModelError(f"claude exited {proc.returncode}: {proc.stderr[:400]}")
        try:
            env = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise ModelError(f"claude did not return JSON: {e}: {proc.stdout[:300]}") from e
        env["_wall_ms"] = wall_ms
        return env


def _price(model: str, tokens_in: int, tokens_out: int) -> float:
    p = PRICES.get(model)
    return round((tokens_in * p[0] + tokens_out * p[1]) / 1e6, 6) if p else 0.0


class AnthropicAPIProvider(Provider):
    """The official `anthropic` SDK, with an API key. Structured output through output_config, streamed so a
    long reply does not run into the HTTP timeout."""

    def invoke(self, prompt: str, schema: dict) -> dict:
        key = self.settings.key() if self.settings else os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ModelError("provider anthropic needs an API key (api_key_env in the config file, "
                             "ANTHROPIC_API_KEY by default)")
        try:
            import anthropic
        except ImportError as e:
            raise ModelError("provider anthropic needs the `anthropic` package: "
                             "uv tool install 'kestra-migration-assistant[anthropic]' "
                             "(or pip install anthropic)") from e
        client = anthropic.Anthropic(api_key=key, timeout=timeout_of(self.cfg))
        t0 = time.time()
        try:
            with client.messages.stream(
                    model=self.cfg["model"], max_tokens=64000,
                    messages=[{"role": "user", "content": prompt}],
                    output_config={"format": {"type": "json_schema", "schema": schema}}) as stream:
                r = stream.get_final_message()
        except anthropic.RateLimitError as e:
            return {"result": f"rate limit: {e}", "is_error": True}
        except anthropic.APIStatusError as e:
            txt = f"{e.status_code}: {e}"
            return {"result": ("overloaded " + txt) if e.status_code >= 500 else txt, "is_error": True}
        except anthropic.APIConnectionError as e:
            raise ModelError(f"cannot reach the Anthropic API: {e}") from e
        if r.stop_reason == "refusal":
            return {"result": "the model declined to answer (refusal)", "is_error": True}
        text = next((b.text for b in r.content if b.type == "text"), "")
        return {"result": text, "is_error": False, "duration_api_ms": int((time.time() - t0) * 1000),
                "total_cost_usd": _price(self.cfg["model"], r.usage.input_tokens, r.usage.output_tokens)}


class OpenAICompatibleProvider(Provider):
    """POST {base_url}/chat/completions: a self-hosted model (Ollama, vLLM), or Azure OpenAI and Bedrock through a
    proxy such as LiteLLM. A server that refuses `response_format` is asked again without it, with the schema in
    the prompt; the reply is validated here either way."""

    def invoke(self, prompt: str, schema: dict) -> dict:
        headers = {"Content-Type": "application/json"}
        key = self.settings.key() if self.settings else None
        if key:
            headers["Authorization"] = f"Bearer {key}"
        elif self.settings and self.settings.api_key_env and self.settings.api_key_env != "OPENAI_API_KEY":
            raise ModelError(f"api_key_env {self.settings.api_key_env} is not set in the environment")
        body: dict = {"model": self.cfg["model"], "messages": [{"role": "user", "content": prompt}],
                      "response_format": {"type": "json_schema",
                                          "json_schema": {"name": "reply", "schema": schema, "strict": False}}}
        t0 = time.time()
        data = self._post(body, headers)
        if data.get("_status") == 400 and "response_format" in data.get("_detail", ""):
            body.pop("response_format")
            body["messages"] = [{"role": "user", "content": prompt + "\n\nReply with one JSON object, and nothing "
                                 "else, that matches this JSON Schema:\n" + json.dumps(schema)}]
            data = self._post(body, headers)
        if "_status" in data:
            code = data["_status"]
            quota = "rate limit " if code == 429 else ("overloaded " if code >= 500 else "")
            return {"result": f"{quota}HTTP {code}: {data['_detail']}", "is_error": True}
        text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        return {"result": text, "is_error": False, "duration_api_ms": int((time.time() - t0) * 1000),
                "total_cost_usd": 0.0, "usage": data.get("usage")}

    def _post(self, body: dict, headers: dict) -> dict:
        req = urllib.request.Request(self.cfg["base_url"].rstrip("/") + "/chat/completions",
                                     data=json.dumps(body).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout_of(self.cfg)) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return {"_status": e.code, "_detail": e.read()[:300].decode("utf-8", "replace")}
        except urllib.error.URLError as e:
            raise ModelError(f"{self.cfg['base_url']}: {e.reason}") from e
        except json.JSONDecodeError as e:
            raise ModelError(f"{self.cfg['base_url']} did not return JSON: {e}") from e


def provider_for(cfg: dict, settings: K.Settings | None = None) -> Provider:
    if cfg["provider"] == "anthropic":
        if cfg["auth"] == "claude-code":
            return ClaudeCodeProvider(cfg, settings)
        return AnthropicAPIProvider(cfg, settings)
    return OpenAICompatibleProvider(cfg, settings)


def ask(purpose: str, workflow_id: str, prompt: str, schema: dict,
        model: str | None = None, retries: int = 1) -> dict:
    """Ask for one JSON object against `schema`. Guarded, cached, logged, validated."""
    if not safety.is_guarded(prompt):
        raise ModelError(f"{purpose}/{workflow_id}: prompt does not start with the untrusted-input notice "
                         "(wrap export text with safety.wrap and the prompt with safety.guard)")
    s = settings()
    if not s.provider:
        raise ModelError(f"{purpose}/{workflow_id}: no model is set up. {s.why}")
    cfg = call_config(s)
    if model:
        cfg["model"] = model
    who = {"provider": cfg["provider"], "auth": cfg["auth"], "model": cfg["model"]}
    if not CACHE_DIR or not LOG_PATH:
        raise ModelError("no model cache or call log set: configure an estate first (stages/common.py model())")
    os.makedirs(CACHE_DIR, exist_ok=True)
    key = _key(prompt, schema, cfg)
    cache_path = os.path.join(CACHE_DIR, f"{key}.json")
    hit_path = cache_path if os.path.exists(cache_path) else None
    if not hit_path and cfg["provider"] == "anthropic" and cfg["auth"] == "claude-code":
        # Replies cached before the model settings were made by the same headless claude, on the CLI's
        # default model; reuse them rather than pay again, and say so in the log.
        legacy = os.path.join(CACHE_DIR, f"{_legacy_key(prompt, schema)}.json")
        hit_path = legacy if os.path.exists(legacy) else None
    if hit_path:
        with open(hit_path) as f:
            hit = json.load(f)
        _log({"purpose": purpose, "workflow": workflow_id, "key": key, **who,
              "cached": True, "legacy_cache": hit_path != cache_path, "ms": 0, "cost_usd": 0.0,
              "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        return hit["value"]

    backend = provider_for(cfg, s)
    attempt_prompt = prompt
    last_errs: list[str] = []
    quota_attempt = 0
    with _sem:
        attempt = 0
        while attempt <= retries:
            _quota_gate.wait()            # park while another thread backs off
            gen_before = _quota_generation
            env = backend.invoke(attempt_prompt, schema)
            cost = env.get("total_cost_usd") or 0.0
            ms = env.get("duration_api_ms") or env.get("_wall_ms") or 0
            text = env.get("result") or ""

            rec = {
                "purpose": purpose, "workflow": workflow_id, "key": key, **who,
                "cached": False, "attempt": attempt, "ms": ms, "cost_usd": cost,
                "is_error": bool(env.get("is_error")),
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }

            if env.get("is_error"):
                rec["error"] = text[:300]
                rec["quota"] = _is_quota(text)
                _log(rec)
                if _is_quota(text):
                    # Not a schema retry and not a conversion failure. Wait and
                    # try the same prompt again; `attempt` is deliberately not
                    # incremented.
                    if _quota_backoff(purpose, workflow_id, quota_attempt, text,
                                      seen_generation=gen_before):
                        quota_attempt += 1
                        continue
                    raise QuotaError(
                        f"{purpose}/{workflow_id}: backend still out of quota after "
                        f"{int(_quota_waited_s)}s of waiting: {text[:200]}")
                raise ModelError(f"{purpose}/{workflow_id}: backend error: {text[:300]}")

            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                # Tolerate a fenced block even though the schema should prevent it.
                stripped = text.strip()
                if stripped.startswith("```"):
                    stripped = stripped.split("\n", 1)[-1].rsplit("```", 1)[0]
                try:
                    value = json.loads(stripped)
                except json.JSONDecodeError as e:
                    last_errs = [f"reply was not JSON: {e}"]
                    rec["schema_errors"] = last_errs
                    _log(rec)
                    attempt_prompt = _retry_prompt(prompt, last_errs)
                    attempt += 1
                    continue

            errs = validate(value, schema)
            rec["schema_errors"] = errs
            _log(rec)
            if not errs:
                with open(cache_path, "w") as f:
                    json.dump({"purpose": purpose, "workflow": workflow_id, **who,
                               "prompt_sha": key, "value": value}, f, indent=1)
                return value
            last_errs = errs
            attempt_prompt = _retry_prompt(prompt, errs)
            attempt += 1

    raise SchemaError(
        f"{purpose}/{workflow_id}: {cfg['model']} gave a reply that does not have the expected structure, "
        f"{retries + 1} time(s): " + "; ".join(last_errs[:6])
        + ". Run the same command again (finished work is kept), or use one of the tested models (README)")


def _quota_backoff(purpose: str, workflow_id: str, attempt: int, detail: str,
                   seen_generation: int = -1) -> bool:
    """
    Pause every worker, wait, then let them resume. Returns False when the wait
    budget is spent, at which point the caller gives up and raises QuotaError.

    Only one thread sleeps per round. A thread whose failure predates the
    current generation returns immediately, so four workers cost one wait
    rather than four.
    """
    global _quota_waited_s, _quota_generation
    with _quota_lock:
        if seen_generation >= 0 and _quota_generation > seen_generation:
            return True               # another thread already waited this out
        if _quota_waited_s >= QUOTA_WAIT_BUDGET_S:
            return False
        wait: float = QUOTA_BACKOFF_S[min(attempt, len(QUOTA_BACKOFF_S) - 1)]
        wait = min(wait, QUOTA_WAIT_BUDGET_S - _quota_waited_s)
        _quota_gate.clear()
        _log({"purpose": purpose, "workflow": workflow_id, "event": "quota-backoff",
              "attempt": attempt, "wait_s": wait, "detail": detail[:200],
              "cached": False, "ms": 0, "cost_usd": 0.0,
              "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        print(f"[model] quota exhausted ({detail[:80]}); waiting {wait}s then "
              f"resuming", flush=True)
        time.sleep(wait)
        _quota_waited_s += wait
        _quota_generation += 1
        _quota_gate.set()
        return True


def _retry_prompt(prompt: str, errs: list[str]) -> str:
    return (prompt + "\n\nYour previous reply did not satisfy the schema:\n- "
            + "\n- ".join(errs[:10])
            + "\n\nReturn only the corrected JSON object.")


# ---------------------------------------------------------------- helpers

def run_parallel(jobs, worker, max_workers: int = MAX_PARALLEL):
    """
    Run `worker(job)` over jobs with a bounded pool, collecting exceptions.

    `ask` now waits quota out, so a QuotaError here means the whole wait budget
    was spent. That stops the batch rather than burning the remaining jobs on
    calls certain to be refused. Whatever finished is kept and cached; rerunning
    the same command resumes.
    """
    from concurrent.futures import ThreadPoolExecutor
    results, errors = {}, {}
    quota_hit = threading.Event()

    def guarded(job):
        if quota_hit.is_set():
            raise QuotaError("skipped: batch stopped after a quota error")
        try:
            return worker(job)
        except QuotaError:
            quota_hit.set()
            raise

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(guarded, j): j for j in jobs}
        for fut, job in futs.items():
            try:
                results[_job_key(job)] = fut.result()
            except Exception as e:
                errors[_job_key(job)] = f"{type(e).__name__}: {e}"
    if quota_hit.is_set():
        errors["_quota"] = ("backend ran out of quota; the batch was stopped. "
                            "Rerun the same command once quota returns: finished "
                            "work is cached and will not be repeated.")
    return results, errors


def _job_key(job):
    if isinstance(job, dict):
        return job.get("uid") or job.get("id") or str(job)
    if isinstance(job, (list, tuple)) and job:
        return str(job[0])
    return str(job)


def cost_summary() -> dict:
    """Read back the call log for the report."""
    if not LOG_PATH or not os.path.exists(LOG_PATH):
        return {"calls": 0, "billed_calls": 0, "cached_calls": 0,
                "cost_usd": 0.0, "ms": 0, "by_purpose": {}}
    calls, cached, cost, ms = 0, 0, 0.0, 0
    by_purpose: dict[str, dict] = {}
    with open(LOG_PATH) as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            calls += 1
            if r.get("cached"):
                cached += 1
            cost += r.get("cost_usd") or 0.0
            ms += r.get("ms") or 0
            p = by_purpose.setdefault(r.get("purpose", "?"),
                                      {"calls": 0, "cached": 0, "cost_usd": 0.0, "ms": 0})
            p["calls"] += 1
            p["cached"] += 1 if r.get("cached") else 0
            p["cost_usd"] += r.get("cost_usd") or 0.0
            p["ms"] += r.get("ms") or 0
    return {"calls": calls, "billed_calls": calls - cached, "cached_calls": cached,
            "cost_usd": round(cost, 4), "ms": ms,
            "by_purpose": {k: {**v, "cost_usd": round(v["cost_usd"], 4)}
                           for k, v in sorted(by_purpose.items())}}
