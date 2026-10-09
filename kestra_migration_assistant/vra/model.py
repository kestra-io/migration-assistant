"""
The one place that talks to a model.

Which model answers is set in `models.yaml` (the default bundled in data/, or the
file named by VRO_MODELS_CONFIG), in two tiers, deliberately not per stage:

  reasoning   Understand (specs, cluster summaries)
  drafting    domain names, walkthrough text

Each tier names a provider and how it authenticates:

  anthropic + claude-code        headless `claude -p` on the local Claude Code login (the default,
                                 and how every run so far was made)
  anthropic + api-key            the official `anthropic` SDK, key from ANTHROPIC_API_KEY
  openai-compatible              POST {base_url}/chat/completions, key from the environment variable
                                 named in api_key_env (OpenAI, Ollama, vLLM, an internal gateway)

Keys never go in the file; the loader refuses one that looks like it holds a key.

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
- Every call is appended to the call log with purpose, tier, provider, model,
  workflow, ms and cost.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request

from . import safety

# Where replies are cached and calls logged: set by the caller before the first call (stages/common.py's
# model() points both at the estate's output folder). Nothing is written anywhere until they are set.
CACHE_DIR: str | None = None
LOG_PATH: str | None = None
# The bundled default, inside the installed package, unless VRO_MODELS_CONFIG names another file.
CONFIG_PATH = os.environ.get("VRO_MODELS_CONFIG") or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                                   "data", "models.yaml")

LEGACY_BACKEND = "claude-code-headless"     # the cache key every call before models.yaml used
MAX_PARALLEL = 4
TIMEOUT_S = 900

# ---------------------------------------------------------------- config

TIERS = ("reasoning", "drafting")
PROVIDERS = ("anthropic", "openai-compatible")
AUTHS = ("claude-code", "api-key")
TIER_OF_PURPOSE = {
    "understand": "reasoning", "cluster-summary": "reasoning", "tests": "reasoning", "review": "reasoning",
    "plan": "reasoning",
    "convert": "drafting", "stages-domain-names": "drafting", "stages-walkthroughs": "drafting",
}
DEFAULT_CONFIG = {
    "reasoning": {"provider": "anthropic", "auth": "claude-code", "model": "claude-fable-5-1"},
    "drafting": {"provider": "anthropic", "auth": "claude-code", "model": "claude-sonnet-5"},
}
_SECRET_KEYS = ("api_key", "apikey", "key", "token", "secret", "password", "authorization")
# USD per million tokens (input, output), for the call log when a provider reports tokens, not cost.
PRICES = {"claude-fable-5-1": (10, 50), "claude-fable-5": (10, 50), "claude-opus-5-5": (4, 20),
          "claude-opus-5": (5, 25), "claude-opus-4-8": (5, 25), "claude-sonnet-5": (2, 10),
          "claude-sonnet-4-6": (3, 15), "claude-haiku-4-5": (1, 5)}


class ConfigError(ValueError):
    pass


def load_config(path: str | None = None) -> dict:
    """The two tiers, validated. A missing file means the defaults (both tiers on claude-code)."""
    path = path or CONFIG_PATH
    if not os.path.exists(path):
        return json.loads(json.dumps(DEFAULT_CONFIG))
    import yaml
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: expected a mapping with 'reasoning' and 'drafting'")
    unknown = sorted(set(raw) - set(TIERS))
    if unknown:
        raise ConfigError(f"{path}: unknown tier(s) {unknown}; the tiers are {list(TIERS)}")
    cfg = {}
    for tier in TIERS:
        t = dict(raw.get(tier) or DEFAULT_CONFIG[tier])
        for k, v in t.items():
            if k.lower() in _SECRET_KEYS or (isinstance(v, str) and v.startswith(("sk-", "sk_"))):
                raise ConfigError(f"{path}: {tier}.{k} looks like a key. Keys never go in this file: "
                                  "use auth: api-key (ANTHROPIC_API_KEY) or api_key_env: <VARIABLE NAME>")
        prov = t.get("provider")
        if prov not in PROVIDERS:
            raise ConfigError(f"{path}: {tier}.provider must be one of {list(PROVIDERS)}, not {prov!r}")
        if not t.get("model"):
            raise ConfigError(f"{path}: {tier}.model is required")
        if prov == "anthropic":
            t.setdefault("auth", "claude-code")
            if t["auth"] not in AUTHS:
                raise ConfigError(f"{path}: {tier}.auth must be one of {list(AUTHS)}, not {t['auth']!r}")
        else:
            if not t.get("base_url"):
                raise ConfigError(f"{path}: {tier}.base_url is required for openai-compatible")
            env = t.get("api_key_env")
            if env is not None and (not isinstance(env, str) or not env.replace("_", "").isalnum()):
                raise ConfigError(f"{path}: {tier}.api_key_env must be an environment variable name")
            t["auth"] = "api-key-env" if env else "none"
        if "timeout_s" in t and not (isinstance(t["timeout_s"], int) and t["timeout_s"] > 0):
            raise ConfigError(f"{path}: {tier}.timeout_s must be a positive number of seconds")
        cfg[tier] = t
    return cfg


def tier_for(purpose: str) -> str:
    return TIER_OF_PURPOSE.get(purpose, "reasoning")


_cfg_cache: dict = {}


def config() -> dict:
    if CONFIG_PATH not in _cfg_cache:
        _cfg_cache[CONFIG_PATH] = load_config(CONFIG_PATH)
    return _cfg_cache[CONFIG_PATH]


# ---------------------------------------------------------------- claude-code backend and quota

# Trimming the dynamic sections of the system prompt cut a measured call from
# $0.234 to $0.078 with no change in output quality. Tool schemas are not needed:
# every prompt here is a pure text-in, JSON-out transformation.
BASE_ARGS = [
    "--output-format", "json",
    "--exclude-dynamic-system-prompt-sections",
    # Every prompt here is text in, JSON out. With tools available the backend
    # explored the repository and answered from files it found, which is both
    # non-deterministic and, for the held-out run, a leak. Tools off.
    "--disallowed-tools", "Bash", "Read", "Write", "Edit", "Glob", "Grep",
    "WebFetch", "WebSearch", "Task", "Agent", "TodoWrite", "NotebookEdit",
    "SlashCommand",
]

# Run the backend from a neutral directory so no project context is picked up.
NEUTRAL_CWD = "/tmp"

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


def _key(prompt: str, schema: dict, tier_cfg: dict) -> str:
    h = hashlib.sha256()
    h.update(f"{tier_cfg['provider']}|{tier_cfg['auth']}|{tier_cfg.get('base_url', '')}".encode())
    h.update(tier_cfg["model"].encode())
    h.update(json.dumps(schema, sort_keys=True).encode())
    h.update(prompt.encode())
    return h.hexdigest()


def _legacy_key(prompt: str, schema: dict) -> str:
    """The key calls made before models.yaml used: headless claude, the CLI's default model."""
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
    """How long one call may take: the tier's `timeout_s` in models.yaml, else TIMEOUT_S. Not part of the cache key."""
    return int(cfg.get("timeout_s") or TIMEOUT_S)


class Provider:
    """One way of asking a model. `invoke` returns a normalised envelope:
    {"result": text, "is_error": bool, "total_cost_usd": float, "duration_api_ms": int}."""

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def invoke(self, prompt: str, schema: dict) -> dict:
        raise NotImplementedError


class ClaudeCodeProvider(Provider):
    """Headless `claude -p` on the local Claude Code login. Tools off, neutral working directory."""

    def invoke(self, prompt, schema):
        args = ["claude", "-p", prompt, *BASE_ARGS, "--json-schema", json.dumps(schema),
                "--model", self.cfg["model"]]
        t0 = time.time()
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout_of(self.cfg),
                                  cwd=NEUTRAL_CWD)
        except subprocess.TimeoutExpired as e:
            raise ModelError(f"backend gave no reply within {timeout_of(self.cfg)} s (timeout_s in models.yaml)") from e
        wall_ms = int((time.time() - t0) * 1000)
        if proc.returncode != 0 and not proc.stdout.strip():
            raise ModelError(f"backend exited {proc.returncode}: {proc.stderr[:400]}")
        try:
            env = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise ModelError(f"backend did not return JSON: {e}: {proc.stdout[:300]}") from e
        env["_wall_ms"] = wall_ms
        return env


def _price(model: str, tokens_in: int, tokens_out: int) -> float:
    p = PRICES.get(model)
    return round((tokens_in * p[0] + tokens_out * p[1]) / 1e6, 6) if p else 0.0


class AnthropicAPIProvider(Provider):
    """The official `anthropic` SDK, key from ANTHROPIC_API_KEY. Structured output via output_config."""

    def invoke(self, prompt, schema):
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise ModelError("auth: api-key needs ANTHROPIC_API_KEY in the environment")
        try:
            import anthropic
        except ImportError as e:
            raise ModelError("auth: api-key needs the `anthropic` package: pip install anthropic") from e
        client = anthropic.Anthropic(timeout=timeout_of(self.cfg))
        t0 = time.time()
        try:
            r = client.messages.create(
                model=self.cfg["model"], max_tokens=16000,
                messages=[{"role": "user", "content": prompt}],
                output_config={"format": {"type": "json_schema", "schema": schema}})
        except anthropic.RateLimitError as e:
            return {"result": f"rate limit: {e}", "is_error": True}
        except anthropic.APIStatusError as e:
            txt = f"{e.status_code}: {e}"
            return {"result": ("overloaded " + txt) if e.status_code >= 500 else txt, "is_error": True}
        if r.stop_reason == "refusal":
            return {"result": "refusal", "is_error": True}
        text = next((b.text for b in r.content if b.type == "text"), "")
        return {"result": text, "is_error": False, "duration_api_ms": int((time.time() - t0) * 1000),
                "total_cost_usd": _price(self.cfg["model"], r.usage.input_tokens, r.usage.output_tokens)}


class OpenAICompatibleProvider(Provider):
    """POST {base_url}/chat/completions. Covers OpenAI, Ollama, vLLM and internal gateways."""

    def invoke(self, prompt, schema):
        headers = {"Content-Type": "application/json"}
        env = self.cfg.get("api_key_env")
        if env:
            key = os.environ.get(env)
            if not key:
                raise ModelError(f"api_key_env {env} is not set in the environment")
            headers["Authorization"] = f"Bearer {key}"
        body = {"model": self.cfg["model"], "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_schema",
                                    "json_schema": {"name": "reply", "schema": schema, "strict": False}}}
        req = urllib.request.Request(self.cfg["base_url"].rstrip("/") + "/chat/completions",
                                     data=json.dumps(body).encode(), headers=headers, method="POST")
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout_of(self.cfg)) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode("utf-8", "replace")
            quota = "rate limit " if e.code == 429 else ("overloaded " if e.code >= 500 else "")
            return {"result": f"{quota}HTTP {e.code}: {detail}", "is_error": True}
        except urllib.error.URLError as e:
            raise ModelError(f"{self.cfg['base_url']}: {e.reason}") from e
        text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        return {"result": text, "is_error": False, "duration_api_ms": int((time.time() - t0) * 1000),
                "total_cost_usd": 0.0, "usage": data.get("usage")}


def provider_for(tier_cfg: dict) -> Provider:
    if tier_cfg["provider"] == "anthropic":
        return ClaudeCodeProvider(tier_cfg) if tier_cfg["auth"] == "claude-code" else AnthropicAPIProvider(tier_cfg)
    return OpenAICompatibleProvider(tier_cfg)


def ask(purpose: str, workflow_id: str, prompt: str, schema: dict,
        model: str | None = None, retries: int = 1) -> dict:
    """Ask for one JSON object against `schema`. Guarded, cached, logged, validated."""
    if not safety.is_guarded(prompt):
        raise ModelError(f"{purpose}/{workflow_id}: prompt does not start with the untrusted-input notice "
                         "(wrap export text with safety.wrap and the prompt with safety.guard)")
    tier = tier_for(purpose)
    cfg = dict(config()[tier])
    if model:
        cfg["model"] = model
    who = {"tier": tier, "provider": cfg["provider"], "auth": cfg["auth"], "model": cfg["model"]}
    if not CACHE_DIR or not LOG_PATH:
        raise ModelError("no model cache or call log set: configure an estate first (stages/common.py model())")
    os.makedirs(CACHE_DIR, exist_ok=True)
    key = _key(prompt, schema, cfg)
    cache_path = os.path.join(CACHE_DIR, f"{key}.json")
    hit_path = cache_path if os.path.exists(cache_path) else None
    if not hit_path and cfg["provider"] == "anthropic" and cfg["auth"] == "claude-code":
        # Replies cached before models.yaml were made by the same headless claude, on the CLI's default
        # model; reuse them rather than pay again, and say so in the log.
        legacy = os.path.join(CACHE_DIR, f"{_legacy_key(prompt, schema)}.json")
        hit_path = legacy if os.path.exists(legacy) else None
    if hit_path:
        with open(hit_path) as f:
            hit = json.load(f)
        _log({"purpose": purpose, "workflow": workflow_id, "key": key, **who,
              "cached": True, "legacy_cache": hit_path != cache_path, "ms": 0, "cost_usd": 0.0,
              "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        return hit["value"]

    backend = provider_for(cfg)
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
        f"{purpose}/{workflow_id}: reply failed schema after {retries + 1} attempt(s): "
        + "; ".join(last_errs[:6]))


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
