"""
The estate interview: a few questions about the whole estate, asked once, built from what the code shows
(patterns.py), with no model. And the vRO mechanics the tool assumes instead of asking.

Both live at the top of answers.yaml, under `estate:`. Someone who knows the estate can fill them in before the
first run; every run applies them everywhere, and asks nothing they settle.

    estate:
      - id: estate.config
        question: 9 workflows call modules that pick a configuration element at run time (getEndpoint, ...). ...
        options: [Kestra's KV store, one entry per element, read by the same name, ...]
        suggested: Kestra's KV store, one entry per element, read by the same name
        answer:
        closes: [question.create-dns-record.endpoint-identity, ...]
      - id: assume.trust-store
        assumed: Kestra checks each system's certificate against the CAs in its trust store ...
        answer:                     # leave empty to keep the assumption, or say what to do instead

An estate question's answer is one of its options (or its number), `yes` for the suggested one, or words of the
person's own. What an answer settles is decided by RULES below, from the answer and what each item is about (its
family, its kind, its workflow), never by the model; `closes:` lists them, and a changed answer reopens them. An
assumption holds while its answer is empty (or yes, keep); any other answer overrides it, and the items it settled
are asked again.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from . import common as C
from . import ids as I

MAX_QUESTIONS = 8
NONE_OF_THESE = "None of these, enter my own answer"
KEEP = ("", "yes", "y", "keep", "keep it", "ok", "agreed", "true")


def _n(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _names(xs: list[str], k: int = 3) -> str:
    xs = list(xs)
    if len(xs) <= k:
        return ", ".join(xs[:-1]) + (" and " if len(xs) > 1 else "") + xs[-1] if xs else ""
    return ", ".join(xs[:k]) + f" and {len(xs) - k} more"


def _q(key: str, question: str, options: list[str], *, why: str, needs_model: bool = False,
       topic: str | None = None, about: dict | None = None) -> dict:
    from . import your_input as Y
    it = Y.item(key, "confirm" if options else "answer", question, question=question, why=why,
                options=options, suggested=options[0] if options else None, family="estate",
                unblocks=["applies to the whole estate"])
    it["family_title"] = "About the estate"
    it["topic"] = topic or key.split(".", 1)[1]
    it["about"] = about or {}
    it["needs_model"] = needs_model
    return it


def _assume(key: str, assumed: str, where: list[str]) -> dict:
    from . import your_input as Y
    it = Y.item(key, "assume", assumed, question=None, family="assume",
                why="a vRO mechanic with a Kestra equivalent: assumed, not asked",
                unblocks=["applies to the whole estate"])
    it["assumed"] = assumed
    it["topic"] = key.split(".", 1)[1]
    it["where"] = where
    it["family_title"] = "Assumed"
    return it


# ------------------------------------------------------------------ the questions

def questions(pat: dict, assess: dict, has_vra: bool, systems: list[str]) -> list[dict]:
    """The estate questions for this estate, biggest first, at most MAX_QUESTIONS."""
    wf = assess.get("workflows") or {}
    names = {u: w["name"] for u, w in wf.items()}
    out: list[dict] = []

    if has_vra:
        out.append(_q("estate.started-by",
                      "vRA starts workflows through the catalog items and subscriptions in its export. What else "
                      "starts these workflows from outside vRO?",
                      ["nothing else: vRA, and workflows calling each other",
                       "also an external platform or scripts, through the vRO API",
                       "also vRO schedules or policies",
                       "also an ITSM such as ServiceNow"],
                      why="a workflow nothing in the export starts may still be started from outside; the answer "
                          "applies to every one of them"))
    else:
        out.append(_q("estate.started-by",
                      f"There is no vRA export. Who starts these {_n(len(wf), 'workflow')}?",
                      ["an external platform, through the vRO API",
                       "people, from the vRO client",
                       "vRO schedules and policies",
                       "vRA: I will add its export to the folder"],
                      why="what starts a workflow becomes its trigger in Kestra; the answer applies to every "
                          "workflow nothing in the export starts"))

    rc = pat.get("runtime_config") or []
    if rc:
        users = sorted({u for r in rc for u in r["workflows"]})
        mods = [r["name"] for r in sorted(rc, key=lambda r: -len(r["workflows"]))]
        cats = sorted({c for r in rc for c in r["categories"]})
        out.append(_q("estate.config",
                      f"{_n(len(users), 'workflow calls', 'workflows call')} "
                      f"{'a module' if len(mods) == 1 else 'modules'} named {_names(mods)}, which "
                      f"{'picks' if len(mods) == 1 else 'pick'} a configuration element at run time"
                      + (f" (under {_names(cats)})" if cats else "") + ". Where should those settings live in Kestra?",
                      ["Kestra's KV store, one entry per element, read by the same name",
                       "namespace variables, one set per environment",
                       "the system they come from today, which the flows call"],
                      why="the flows read the same settings by the same names; questions about which host or value a "
                          "workflow uses are settled by where the settings live",
                      about={"modules": mods, "workflows": users}))

    va = pat.get("vault") or []
    if va:
        users = sorted({u for v in va for u in v["workflows"]})
        out.append(_q("estate.secrets",
                      f"{_n(len(users), 'workflow reads', 'workflows read')} secrets from HashiCorp Vault through "
                      f"{_names([v['name'] for v in va])}. Where do the converted flows get them?",
                      ["from the same Vault paths, through Kestra's Vault secret manager",
                       "from Kestra secrets, copied from Vault once",
                       "they keep calling Vault's API themselves"],
                      why="every credential question is settled by where secrets come from",
                      about={"modules": [v["name"] for v in va], "workflows": users}))
    else:
        out.append(_q("estate.secrets",
                      "The workflows sign in to the systems they call with credentials kept in vRO (configuration "
                      "elements, SecureString attributes). Where do the converted flows get them?",
                      ["Kestra secrets, one per credential",
                       "an external secret manager Kestra reads (HashiCorp Vault, AWS, Azure or Google)"],
                      why="every credential question is settled by where secrets come from"))

    if systems:
        out.append(_q("estate.non-production",
                      f"The workflows reach {_n(len(systems), 'system')} ({_names(systems, 4)}). Is there a "
                      "non-production instance of each for converted flows to run against first?",
                      ["yes, of each: their addresses and credentials go in Kestra per environment",
                       "only of some: I will say which in answers.yaml",
                       "none: the flows are checked against production, with care"],
                      why="a wave that writes to a system is verified on a non-production instance first",
                      about={"systems": systems}))

    rm = pat.get("remote") or []
    if rm:
        whom = sorted({r["name"] for r in rm if r.get("workflow")} |
                      {names.get(u, u) for r in rm for u in r.get("workflows") or []})
        out.append(_q("estate.remote",
                      f"{_n(len(whom), 'workflow calls', 'workflows call')} workflows on another vRO cluster "
                      f"({_names(whom)}). Those are not in this export (the brief lists what to add). How should the "
                      "converted flows reach them?",
                      ["they move to Kestra too: I will add their packages to the export",
                       "they stay in vRO: the flows call them through the vRO API",
                       "they are out of scope"],
                      why="a call to another cluster becomes a subflow or an HTTP call to vRO",
                      about={"workflows": whom}))

    sc = pat.get("scope") or []
    if sc:
        out.append(_q("estate.scope",
                      f"{_n(len(sc), 'workflow looks', 'workflows look')} like an old copy or a test, from "
                      f"{'its' if len(sc) == 1 else 'their'} name or folder: {_names([s['name'] for s in sc], 5)}. "
                      "Leave them out of the migration?",
                      ["yes, leave them all out", "no, migrate them too"],
                      why="nothing is asked about a workflow that is left out, and it is marked Retire",
                      about={"shorts": [s["short"] for s in sc], "names": [s["name"] for s in sc]}))

    covered = {r["name"] for r in rc} | {v["name"] for v in va} | {
        m["name"] for m in (pat.get("rest_in_code") or {}).get("modules") or []}
    for s in pat.get("shared") or []:
        if len(out) >= MAX_QUESTIONS:
            break
        if s["name"] in covered:
            continue
        what = []
        if s.get("targets"):
            what.append(f"calls {_names(s['targets'])}" + (f" ({_names(s['ops'], 2)})" if s.get("ops") else ""))
        out.append(_q(f"estate.module.{I.slug(s['name'])}",
                      f"{_n(len(s['workflows']), 'workflow calls', 'workflows call')} a module named {s['name']}. "
                      "What does it do?" + ("" if what else " (Needs a model to read its code and suggest an answer.)"),
                      what + [],
                      why="one answer for every workflow that calls it", needs_model=not what,
                      topic="module", about={"module": s["name"], "workflows": s["workflows"]}))
    return out[:MAX_QUESTIONS]


def assumptions(pat: dict, any_calls: bool) -> list[dict]:
    """The vRO mechanics assumed instead of asked. Each says what Kestra does instead; each can be overridden."""
    mech = pat.get("mechanics") or {}
    rest = (pat.get("rest_in_code") or {})
    out = []
    if any_calls or mech.get("trust_store"):
        out.append(_assume("assume.trust-store",
                           "Kestra checks each system's certificate against the CAs in its trust store. The CAs vRO "
                           "imported go there once, in the Kestra setup, not in each flow.",
                           mech.get("trust_store") or []))
    if any_calls or mech.get("host_verification"):
        out.append(_assume("assume.host-verification",
                           "Where vRO turned host verification off, the converted flows verify the host. Name a "
                           "system here to keep it off for that system only.",
                           mech.get("host_verification") or []))
    if mech.get("retries") or any_calls:
        out.append(_assume("assume.retries",
                           "A retry loop or attempt counter in vRO code becomes the task's retry setting in Kestra "
                           "(the same number of attempts and the same wait), not a loop in a script.",
                           mech.get("retries") or []))
    out.append(_assume("assume.counters",
                       "Loop counters and indexes kept in workflow attributes become Kestra loops (ForEach) and "
                       "expressions. Nothing is kept between runs unless a question says so.", []))
    if rest.get("workflows") or rest.get("modules"):
        out.append(_assume("assume.rest-hosts",
                           "REST hosts made in code (the HTTP-REST plugin installed, its inventory not configured) "
                           "become Kestra HTTP tasks, with the address from the settings. Nothing is configured in "
                           "a plugin.",
                           [x["name"] for x in rest.get("workflows") or []] + [m["module"] for m in rest.get("modules") or []]))
    return out


# ------------------------------------------------------------------ what an answer settles

def answer_of(it: dict, entry: dict | None) -> str | None:
    """An estate item's answer as text, its options resolved (a number, or yes for the suggested one); an
    assumption's answer is None while the assumption holds. None when open."""
    from . import your_input as Y
    a = Y._text((entry or {}).get("answer"))
    if it.get("family") == "assume":
        return None if a.strip().lower().rstrip(".") in KEEP else a
    if Y.is_open(a):
        return None
    return option_text(it, a)


def option_text(it: dict, a: str) -> str:
    from . import your_input as Y
    opts = it.get("options") or []
    s = a.strip()
    if s.isdigit() and 1 <= int(s) <= len(opts):
        return opts[int(s) - 1]
    if Y._yes(s) and it.get("suggested"):
        return str(it["suggested"])
    return s


def picked(it: dict, text: str) -> int | None:
    """The option an answer is (1-based), or None for words of the person's own."""
    opts = [o.lower() for o in it.get("options") or []]
    t = text.strip().lower()
    return opts.index(t) + 1 if t in opts else None


def _workflow_of(key: str) -> str | None:
    fam, _, rest = key.partition(".")
    if fam in ("defect", "question", "spec") and rest:
        return rest.split(".")[0]
    return None


def _kind(key: str) -> str:
    return I.kind_part(key)


def _text_of(it: dict) -> str:
    return f"{it.get('ask') or ''} {it.get('question') or ''}".lower()


Rule = Callable[[dict, str, int | None, dict], "str | None"]


def _started_by(est: dict, ans: str, opt: int | None, it: dict) -> str | None:
    if it["key"].startswith("q2-"):
        return ans if opt != 4 or "vRA" not in ans else None
    if it["key"].startswith("question.") and _kind(it["key"]) == "scheduling":
        return f"what starts the workflows: {ans}"
    return None


def _config(est: dict, ans: str, opt: int | None, it: dict) -> str | None:
    if it["key"].startswith("question.") and _kind(it["key"]) in ("endpoint-identity", "configuration-values"):
        return f"read from the settings: {ans}"
    return None


def _secrets(est: dict, ans: str, opt: int | None, it: dict) -> str | None:
    if it["key"].startswith("question.") and _kind(it["key"]) == "credentials":
        return f"credentials: {ans}"
    return None


def _non_production(est: dict, ans: str, opt: int | None, it: dict) -> str | None:
    if opt == 2:                       # only some: each system is asked on its own
        return None
    if it["key"].startswith("q3-"):
        return ans
    if it["key"].startswith("endpoint."):
        return f"{ans}; credentials as in estate.secrets"
    return None


def _remote(est: dict, ans: str, opt: int | None, it: dict) -> str | None:
    whom = {I.slug(n) for n in (est.get("about") or {}).get("workflows") or []}
    w = _workflow_of(it["key"])
    if w and I.slug(w) in whom and _kind(it["key"]) in ("external-integration", "endpoint-identity"):
        return f"calls to the other cluster: {ans}"
    return None


def _scope(est: dict, ans: str, opt: int | None, it: dict) -> str | None:
    if opt != 1:
        return None
    w = _workflow_of(it["key"])
    if w and w in set((est.get("about") or {}).get("shorts") or []):
        return "left out of the migration (estate.scope)"
    return None


def _module(est: dict, ans: str, opt: int | None, it: dict) -> str | None:
    mod = I.slug((est.get("about") or {}).get("module") or "")
    parts = it["key"].split(".")
    if mod and it["key"].startswith("question.") and len(parts) >= 4 and parts[2] == mod:
        return f"{(est.get('about') or {}).get('module')}: {ans}"
    return None


RULES: dict[str, Rule] = {"started-by": _started_by, "config": _config, "secrets": _secrets,
                          "non-production": _non_production, "remote": _remote, "scope": _scope, "module": _module}


def _tls(est: dict, it: dict) -> str | None:
    if it["key"].startswith("tls."):
        return "check it (the CA goes in the Kestra setup)"
    return None


def _host_verification(est: dict, it: dict) -> str | None:
    if it["key"].startswith(("defect.", "question.")) and _kind(it["key"]) == "tls":
        return "fix: the flow verifies the certificate and the host (assumed, assume.host-verification)"
    return None


def _retries(est: dict, it: dict) -> str | None:
    if it["key"].startswith("question.") and re.search(r"\bretr(y|ies|ied)\b|\battempts?\b", _text_of(it)):
        return "the task's retry setting, as in vRO (assumed, assume.retries)"
    return None


def _counters(est: dict, it: dict) -> str | None:
    if it["key"].startswith("question.") and re.search(r"\bcounter\b|\bloop index\b|\bindex attribute\b",
                                                         _text_of(it)):
        return "a Kestra loop and expressions (assumed, assume.counters)"
    return None


ASSUMED: dict[str, Callable[[dict, dict], str | None]] = {
    "trust-store": _tls, "host-verification": _host_verification, "retries": _retries, "counters": _counters}


def closures(its: list[dict], entries: dict) -> dict[str, dict]:
    """key -> {"by", "answer"}: what the estate answers and the assumptions settle. An item answered on its own
    is never settled by them (its answer wins)."""
    from . import your_input as Y
    out: dict[str, dict] = {}
    estate = [it for it in its if it.get("family") in ("estate", "assume")]
    others = [it for it in its if it.get("family") not in ("estate", "assume")
              and Y.is_open((entries.get(it["key"]) or {}).get("answer"))]
    for est in estate:
        topic = est.get("topic") or est["key"].split(".", 1)[1]
        if est.get("family") == "assume":
            if answer_of(est, entries.get(est["key"])) is not None:
                continue                                # overridden: nothing assumed
            rule = ASSUMED.get(topic)
            if rule is None:
                continue
            for it in others:
                if it["key"] not in out and (a := rule(est, it)):
                    out[it["key"]] = {"by": est["key"], "answer": a}
            continue
        ans = answer_of(est, entries.get(est["key"]))
        if ans is None or est.get("topic") not in RULES:
            continue
        opt = picked(est, ans)
        for it in others:
            if it["key"] not in out and (a := RULES[est["topic"]](est, ans, opt, it)):
                out[it["key"]] = {"by": est["key"], "answer": a}
    return out


# ------------------------------------------------------------------ applied estate-wide

def scope_outcomes(by_short: dict[str, str] | None = None) -> dict[str, dict]:
    """uid -> outcome override for the workflows estate.scope leaves out (corrections.yaml still wins)."""
    from . import your_input as Y
    loaded = Y.load()
    it = (loaded.get("items") or {}).get("estate.scope")
    if not it:
        return {}
    ans = answer_of(it, loaded["entries"].get("estate.scope"))
    if ans is None or picked(it, ans) != 1:
        return {}
    by_short = by_short or {}
    return {by_short.get(s, s): {"outcome": "Retire", "reason": "out of scope: the owner left it out (estate.scope)"}
            for s in (it.get("about") or {}).get("shorts") or []}


def context() -> str:
    """The estate answers as context for the model's prompts: each answered estate question and each overridden
    assumption, sanitized like the export. Empty when none is answered, so a prompt without estate answers is the
    same as before (and its cached reply still counts)."""
    from . import your_input as Y
    loaded = Y.load()
    lines = []
    for k, it in sorted((loaded.get("items") or {}).items()):
        if it.get("family") not in ("estate", "assume"):
            continue
        a = answer_of(it, loaded["entries"].get(k))
        if a is None:
            continue
        if it.get("family") == "assume":
            lines.append(f"- Instead of the assumption \"{it['assumed']}\": {a}")
        else:
            lines.append(f"- {it['question']} Answer: {a}")
    if not lines:
        return ""
    return sanitize_text("\n".join(lines))


def sanitize_text(text: str) -> str:
    """A person's words, redacted like the export before any model sees them. Placeholders are named ANSWER-...,
    so they never read as one of the export's."""
    from .. import sanitize as S
    r = S.Redactor(names=_code_names())
    out = r.text(text)
    cats = "|".join(S.CATEGORIES)
    return re.sub(rf"<({cats})-(\d+)>", r"<ANSWER-\1-\2>", out)


def _code_names() -> set[str]:
    import os
    p = os.path.join(C.ASSESS, "assess.json")
    if not os.path.exists(p):
        return set()
    a = C.read_json(p)
    return {x["module"].lower() for x in a.get("actions") or []} | {
        str(w.get("category_path") or "").lower() for w in (a.get("workflows") or {}).values()}


def register() -> dict:
    """Build the estate questions and assumptions from what Assess and patterns.py found, and register them in
    answers.yaml (section `estate`). Returns the patterns."""
    from . import patterns as P
    from . import preflight
    from . import your_input as Y
    pat = P.run()
    assess = C.read_json(f"{C.ASSESS}/assess.json")
    f = preflight.facts(assess=assess)
    systems = [n for n, _ in f.get("systems") or []]
    rest = pat.get("rest_in_code") or {}
    any_calls = bool(systems) or bool(rest.get("modules")) or bool(rest.get("workflows"))
    items: list[dict[str, Any]] = questions(pat, assess, bool(f.get("vra")), systems) + assumptions(pat, any_calls)
    Y.register("estate", items)
    return pat
