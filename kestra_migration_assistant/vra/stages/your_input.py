"""
your-input/answers.yaml: the one file a person fills in.

    # Answer what's open, then run: kestra-migrate vra run lb-usecase

    open:
      - id: suggested
        question: 'Accept every suggested answer below? (yes; to change one, add answer: under it)'
        answer:
        each:
          - id: tls.f5-bigip
            question: Do the calls to F5 BIG-IP check its certificate? (vRO's do not)
            suggested: check it (the CA goes in the Kestra setup)
      - id: defects.lb-create-load-balancer
        question: '3 possible defects in LB - Create Load Balancer: keep them all, fix them all, or decide each? ...'
        answer:
    answered:
      - id: q1-scope
        question: Is this export the whole vRO estate?
        answer: yes, the whole estate, from one vRO node

    signed_for: full plan

Two lists, `open` (what a wave waits on first) and `answered`, a third (`no_longer_asked`) when an answered item is
no longer asked, then `signed_for` (`wave 1` or `full plan`, empty until signed). Each entry is an `id`, one plain
`question`, a `suggested` value when the code points to one, and the `answer`; a group's items are under its
`each:` (see "groups" below). Writing `yes` takes the suggested value; any other answer is the answer. One
comment line at the top, nothing else: the page with an item's context is named in its question only where it
is needed.

The stages register what they ask (`register(section, items)`): Preflight its questions, the Brief its
decisions, the specs' open questions, the reviews. The registry is kept in data/input-items.json, so each stage
replaces only its own part and the file always shows everything asked. The file is rewritten from the registry
on every run, and **an answer is never changed**: the tool moves an item to `answered` once it has one, keeps
the answer exactly as written (and any field a person added), and never drops an id that has an answer: an
item no longer asked moves to a third list, `no_longer_asked`, with the question it was answered for. An item's
id says what it is about, not where it falls in a list (ids.py), so an answer never lands on another item; an
answer under an id of an older run moves to the new id only when the item is asked in exactly the same words. A file that does not parse is never
rewritten: the run says what is wrong and reads nothing from it. The version before each rewrite that changes
anything is kept in data/answers.yaml.previous.

What the file no longer shows is kept in the registry (`_memo`): the value a `yes` took, the `suggested` value
next to it when it was written (so a later suggestion does not change what was agreed), and the spec fingerprint a review was written for (a review of a spec
regenerated since is stale until its answer changes).

Before session 2g the file had sections blocking/open/done (or confirm/answer/review), entries keyed by id
with `confirmed:`, `by:`, `date:` and `note:`, and a `sign:` block. `load()` reads that shape too; the first
rewrite carries every answer and the signature into this one and keeps the old file as
data/answers.yaml.before-2g. Corrections to what the tool found are not answers: they go in the optional
your-input/corrections.yaml (corrections.py), which the tool reads but never creates.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from typing import Any

import yaml

from .. import safety
from . import common as C

HEADER = "# Answer what's open, then run: "


def header() -> str:
    return HEADER + C.run_command()
GROUPS = ("confirm", "answer", "review", "sign")
YES = ("yes", "y", "true", "confirmed", "confirm", "ok", "x")
TOOL_FIELDS = ("id", "question", "options", "suggested", "assumed", "answer", "closes", "settles", "each")
OLD_SECTIONS = ("blocking", "open", "done", "confirm", "answer", "review", "no_longer_asked")
OLD_FIELDS = ("answer", "confirmed", "by", "date", "note", "spec")    # the old format's own fields
GONE = "No longer asked: "
SECTIONS = ("estate", "open", "answered", "no_longer_asked")
ESTATE_FAMILIES = ("estate", "assume")
SIGN = {"full": "full plan", "wave-1": "wave 1"}


def is_open(answer) -> bool:
    a = str(answer if answer is not None else "").strip().lower().rstrip(".")
    return a in ("", "don't know", "dont know", "unknown", "n/a", "tbd", "?", "none yet")


def _yes(v) -> bool:
    return v is True or str(v if v is not None else "").strip().lower().rstrip(".") in YES


def _text(v) -> str:
    if v is None:
        return ""
    return (", ".join(map(str, v)) if isinstance(v, list) else str(v)).strip()


# ------------------------------------------------------------------ the registry

def _registry_path() -> str:
    return os.path.join(C.DATA, "input-items.json")


def registry() -> dict:
    p = _registry_path()
    return C.read_json(p) if os.path.exists(p) else {}


def items() -> list[dict]:
    """Every item asked, in the order the file shows them: preflight first, then the brief's. What each
    unblocks is the item's own list plus what a later stage registered for it (register_unblocks)."""
    reg = registry()
    extra = reg.get("_unblocks") or {}
    out, seen = [], set()
    for section in ("estate", "preflight", "brief",
                    *sorted(k for k in reg if k not in ("estate", "preflight", "brief") and not k.startswith("_"))):
        for it in reg.get(section) or []:
            if it["key"] not in seen:
                seen.add(it["key"])
                ub = list(it.get("unblocks") or []) + [x for x in extra.get(it["key"]) or [] if x not in (it.get("unblocks") or [])]
                out.append({**it, "unblocks": ub, "section": section})
    return out


def register_unblocks(m: dict[str, list[str]]) -> None:
    """What a later stage found each item unblocks (the brief, for preflight's questions). Rewrites nothing."""
    reg = registry()
    reg["_unblocks"] = m
    C.write_json(_registry_path(), reg)


def item(key: str, group: str, ask: str, *, title: str | None = None, why: str | None = None,
         unblocks: list[str] | None = None, options: list[str] | None = None, suggested: str | None = None,
         found: str | None = None, family: str | None = None, link: str | None = None, fixed: dict | None = None,
         blocks: list[str] | None = None, question: str | None = None, systems: list[dict] | None = None) -> dict:
    """One thing a person answers. `ask` is the full question the pages show; `question` the plain one
    answers.yaml shows (derived from `ask` when not given). `suggested` (group confirm) is what the code points
    to; `fixed` holds what the tool tracks for the item (a spec's fingerprint); `systems` the systems an item
    asks about, by key and name (Q3)."""
    return {**({"systems": systems} if systems else {}), "key": key, "group": group, "ask": ask, "question": question, "title": title or ask, "why": why,
            "unblocks": unblocks or [], "options": options or [], "suggested": suggested, "found": found,
            "family": family or key.split(".", 1)[0], "link": link, "fixed": fixed or {}, "blocks": blocks or []}


def register(section: str, new_items: list[dict]) -> dict:
    """Replace one section's items in the registry and rewrite answers.yaml. Returns write()'s result."""
    reg = registry()
    renames = _renames(reg.get(section) or [], new_items)
    reg[section] = new_items
    C.write_json(_registry_path(), reg)
    return write(renames)


def _renames(old: list[dict], new: list[dict]) -> dict[str, str]:
    """Old key -> new key of an item asked again in exactly the same words under another key (an estate answered
    before the IDs said what an item is about). Only identical text counts, never a position."""
    by_ask: dict[str, list[str]] = {}
    for it in new:
        by_ask.setdefault(str(it.get("ask")), []).append(it["key"])
    new_keys = {it["key"] for it in new}
    return {it["key"]: by_ask[str(it.get("ask"))][0] for it in old
            if it["key"] not in new_keys and len(by_ask.get(str(it.get("ask"))) or []) == 1}


def _memo(reg: dict | None = None) -> dict:
    m = (reg if reg is not None else registry()).get("_memo") or {}
    return {"confirmed": dict(m.get("confirmed") or {}), "reviewed": dict(m.get("reviewed") or {}),
            "groups": dict(m.get("groups") or {}), "stand_in": bool(m.get("stand_in")),
            "closures": dict(m.get("closures") or {})}


# ------------------------------------------------------------------ reading

# A person's words, as typed: every plain scalar is text (YAML 1.1 would read `yes`, `no`, `on`, `off` and `y` as
# booleans and `42` as a number, and write them back as `true` or `false`); only an empty value is empty. The
# dumper resolves the same way, so it writes `yes` back plain, not quoted.
_TEXT_RESOLVERS = {"": [("tag:yaml.org,2002:null", re.compile(r"^$"))]}


class TextLoader(yaml.SafeLoader):
    yaml_implicit_resolvers = _TEXT_RESOLVERS


class TextDumper(yaml.SafeDumper):
    yaml_implicit_resolvers = _TEXT_RESOLVERS


def _yaml(text: str):
    return yaml.load(text, Loader=TextLoader)


def _read(path: str) -> tuple[dict | None, list[str]]:
    """The parsed file, or None and why when it cannot be read."""
    if not os.path.exists(path):
        return {}, []
    try:
        data = safety.load_yaml(open(path, "rb").read(), os.path.basename(path), loader=TextLoader) or {}
    except (OSError, ValueError, yaml.YAMLError) as e:
        return None, [f"{C.rel(path)} could not be read, so nothing in it counts and the tool did not rewrite it: {e}"]
    if not isinstance(data, dict):
        return None, [f"{C.rel(path)}: expected open, answered and signed_for at the top"]
    return data, []


def is_old_format(data: dict) -> bool:
    """The file as written before session 2g: sections keyed by id, a sign: block, answered_by at the top."""
    return any(isinstance(data.get(g), dict) for g in OLD_SECTIONS) or "sign" in data or "answered_by" in data


def load(path: str | None = None) -> dict:
    """{"path", "exists", "ok", "old_format", "entries": {id: entry}, "sign": {"signed_for"}, "memo",
    "stand_in", "warnings"}. An entry holds what the file says for one id: answer, question, suggested, and
    any field a person added."""
    path = path or C.ANSWERS
    data, warnings = _read(path)
    reg = registry()
    out: dict[str, Any] = {"path": C.rel(path), "exists": os.path.exists(path), "ok": data is not None, "warnings": warnings,
           "old_format": False, "entries": {}, "sign": {}, "memo": _memo(reg)}
    if data and is_old_format(data):
        out["old_format"] = True
        _from_old(data, reg, out)
    elif data:
        for sec in SECTIONS:
            block = data.get(sec)
            if block is None:
                continue
            if not isinstance(block, list):
                out["warnings"].append(f"`{sec}:` should be a list of items, each starting with `- id:`")
                continue
            for e in block:
                if not isinstance(e, dict) or not str(e.get("id") or "").strip():
                    out["warnings"].append(f"an item under `{sec}:` has no `id:`, so it is not read")
                    continue
                each = e.get("each")
                subs: list = each if isinstance(each, list) else []
                if subs:
                    out.setdefault("file_groups", {})[str(e["id"]).strip()] = [
                        str(x["id"]).strip() for x in subs if isinstance(x, dict) and str(x.get("id") or "").strip()]
                for sub in [e, *subs]:
                    if not isinstance(sub, dict) or not str(sub.get("id") or "").strip():
                        out["warnings"].append(f"an item under `{sec}:` has no `id:`, so it is not read")
                        continue
                    k = str(sub["id"]).strip()
                    if k in out["entries"]:
                        out["warnings"].append(f"{k} appears twice; the first is read")
                        continue
                    out["entries"][k] = {f: v for f, v in sub.items() if f not in ("id", "each")}
        out["sign"] = {"signed_for": _text(data.get("signed_for"))}
    texts = " ".join(_text(e.get("answer")) for e in out["entries"].values()).lower()
    out["stand_in"] = out["memo"]["stand_in"] or "stand-in" in texts
    its = items()
    out["items"] = {it["key"]: it for it in its}
    out["groups"] = groups(its, out)
    out["group_of"] = {k: g for g, ks in out["groups"].items() for k in ks}
    from . import planner
    out["closed"] = planner.closed(its, out)
    return out


def _from_old(data: dict, reg: dict, out: dict) -> None:
    """Read the pre-2g file into today's entries: every answer as written, a pre-filled value nobody confirmed
    or changed left out (it was the tool's, and becomes `suggested`), by/date/note/confirmed dropped (the old
    file is kept in data/), a review's spec fingerprint and any stand-in mark kept in the memo."""
    by_key = {it["key"]: it for it in items()}
    prefilled = reg.get("_prefilled") or {}
    memo = out["memo"]
    stand_in = "stand-in" in str(data.get("answered_by") or "").lower()
    for g in OLD_SECTIONS:
        block = data.get(g)
        if not isinstance(block, dict):
            continue
        for k, e in block.items():
            k = str(k)
            if k in out["entries"]:
                continue
            e = dict(e) if isinstance(e, dict) else {"answer": e}
            stand_in = stand_in or any("stand-in" in str(e.get(f) or "").lower() for f in ("by", "note"))
            it = by_key.get(k) or {}
            ans = e.get("answer")
            if it.get("group") == "confirm" or "confirmed" in e:
                tool = prefilled.get(k, it.get("suggested"))
                if not _yes(e.get("confirmed")) and (is_open(ans) or (tool is not None and _text(ans) == _text(tool))):
                    ans = None                       # the tool's pre-fill, never confirmed
            entry = {"answer": ans}
            entry.update({f: v for f, v in e.items() if f not in OLD_FIELDS})
            if g == "no_longer_asked" and not it:
                entry["question"] = GONE.strip()
            out["entries"][k] = entry
            if e.get("spec") and not is_open(ans):
                memo["reviewed"].setdefault(k, {"spec": e["spec"], "answer": _text(ans)})
    s: Any = data.get("sign") if isinstance(data.get("sign"), dict) else {}
    cov = str(s.get("covers") or "").lower().replace(" ", "-")
    scope = "full" if cov in ("full", "full-plan", "all") else ("wave-1" if cov in ("wave-1", "wave1", "1") else None)
    signed = bool(str(s.get("signed_by") or "").strip() and str(s.get("date") or "").strip() and scope)
    stand_in = stand_in or "stand-in" in str(s.get("signed_by") or "").lower()
    out["sign"] = {"signed_for": SIGN[scope] if signed and scope else ""}
    memo["stand_in"] = memo["stand_in"] or stand_in


def state(key: str, loaded: dict | None = None, it: dict | None = None) -> dict:
    """
    One item's state: {"state", "answer", "by", "date", "note", "why"}. state is "answered" (a review:
    "reviewed"), "stale" (a review of an older spec), or "open". `answer` is what counts: for a `yes` to a
    suggestion, the suggested value it agreed to. by, date and note are no longer in the file (None).
    """
    loaded = loaded if loaded is not None else load()
    it = it or next((x for x in items() if x["key"] == key), None) or {"group": "answer", "suggested": None, "fixed": {}}
    e = loaded["entries"].get(key) or {}
    ans = _text(e.get("answer"))
    if is_open(ans):
        ans = via_group(key, loaded) or ans
    memo = loaded.get("memo") or {}
    rec = {"state": "open", "answer": ans or None, "by": None, "date": None, "note": None, "why": "not answered"}
    if is_open(ans):
        shut = (loaded.get("closed") or {}).get(key)
        if shut:
            rec.update(state="answered", answer=shut["answer"], why=f"settled by {shut['by']}: {shut['answer']}")
        return rec
    opts = it.get("options") or []
    if ans.strip().isdigit() and 1 <= int(ans.strip()) <= len(opts):
        ans = str(opts[int(ans.strip()) - 1])                # an option, by its number
        rec["answer"] = ans
    sug = _text(it.get("suggested"))
    if it.get("group") == "confirm" and sug and _yes(ans):
        took = (memo.get("confirmed") or {}).get(key) or _text(e.get("suggested")) or sug
        rec.update(state="answered", answer=took, why=f"confirmed: {took}")
        return rec
    if it.get("group") == "review":
        then = (memo.get("reviewed") or {}).get(key) or {}
        now = str((it.get("fixed") or {}).get("spec") or "").strip()
        if then.get("spec") and now and then["spec"] != now and then.get("answer") == ans:
            rec.update(state="stale", why="reviewed, but the spec was regenerated since")
            return rec
        rec.update(state="reviewed", why="reviewed" + ("" if ans.lower().rstrip(".") in YES else f": {ans}"))
        return rec
    rec.update(state="answered", why=("confirmed" if sug and ans == sug else "answered") + f": {ans}")
    return rec


def signature(loaded: dict | None = None) -> dict:
    """The signature as the approval gate reads it: {present, signed, approved_by, date, scope, block}."""
    loaded = loaded if loaded is not None else load()
    raw = str((loaded.get("sign") or {}).get("signed_for") or "")
    v = raw.lower().replace("-", " ").replace("_", " ").strip()
    scope = ("full" if v in ("full plan", "full", "all", "the full plan") else
             "wave-1" if v in ("wave 1", "wave1", "1", "wave one") else None)
    return {"present": bool(raw), "signed": bool(scope), "approved_by": None, "date": None, "scope": scope,
            "block": f"signed_for: {raw}"}


# ------------------------------------------------------------------ the plain question

SHORT = 200


def blocks_a_wave(it: dict) -> bool:
    """An item one of the plan's waves names in its entry criteria."""
    return any(re.match(r"Wave \d+\b", str(u)) for u in it.get("unblocks") or [])


def _workflow_of(it: dict) -> str | None:
    """The workflow a defect or spec question is about: its ask starts with the workflow's name."""
    if it.get("family") in ("defect", "question") and ": " in it["ask"]:
        return it["ask"].split(": ", 1)[0]
    return None


def question_of(it: dict) -> str:
    """The plain question answers.yaml shows: the item's own, or the ask up to its question (the scene and
    the question when the run-up is long), with the choices when they are short and nothing is suggested."""
    if it.get("question"):
        return it["question"]
    a = re.sub(r"\s+", " ", str(it["ask"])).strip()
    a = re.sub(r"^Q(\d+)\. ", "", a)
    w = _workflow_of(it)
    if w:
        a = a[len(w) + 2:]
    sentences = [x for x in re.split(r"(?<=[.?])\s+(?=[A-Z'`])", a) if x]
    upto = []
    for x in sentences:
        upto.append(x)
        if x.endswith("?"):
            break
    q = " ".join(upto)
    if len(q) > SHORT and len(upto) > 1:
        q = f"{upto[0]} {upto[-1]}"
    if len(q) > SHORT:
        q = re.sub(r"\s*\([^)]*\)", "", q)
    if w:
        q = f"{w}: {q}"
    opts = it.get("options") or []
    if opts and not it.get("suggested") and len(" | ".join(opts)) <= 60:
        q += f" ({', '.join(opts[:-1])} or {opts[-1]})" if len(opts) > 1 else f" ({opts[0]})"
    return q


# ------------------------------------------------------------------ writing

def _scalar(v) -> str:
    if v is None or (isinstance(v, str) and v == ""):
        return ""
    if isinstance(v, str) and ("\n" in v or "\t" in v):
        return " " + json.dumps(v, ensure_ascii=False)       # one line, read back exactly
    s = yaml.dump(v, Dumper=TextDumper, allow_unicode=True, width=float("inf"), default_flow_style=None)
    s = s.removesuffix("\n").removesuffix("\n...").rstrip("\n")
    if isinstance(v, str) and s.startswith("'") and "'" in v:
        return " " + json.dumps(v, ensure_ascii=False)       # "it's", not 'it''s'
    return " " + s


def _field(name: str, v, first: bool) -> list[str]:
    pre = "  - " if first else "    "
    if isinstance(v, (dict, list)) and v:
        body = yaml.dump(v, Dumper=TextDumper, allow_unicode=True, width=100, default_flow_style=False, sort_keys=False)
        return [f"{pre}{name}:"] + ["      " + ln for ln in body.rstrip("\n").split("\n")]
    return [f"{pre}{name}:{_scalar(v)}"]


def _entry(k: str, e: dict, each: list[tuple[str, dict]] | None = None) -> list[str]:
    L = _field("id", k, True)
    for f in ("question", "assumed", "options", "suggested", "answer"):
        if f in e and (f == "answer" or e[f] not in (None, "", [])):
            L += _field(f, e[f], False)
    for f, v in e.items():                           # a field of the person's own
        if f not in TOOL_FIELDS:
            L += _field(f, v, False)
    for f in ("closes", "settles"):
        if e.get(f):
            L += _field(f, e[f], False)
    if each:
        L.append("    each:")
        for sk, se in each:
            L += ["    " + ln for ln in _entry(sk, se)]
    return L


# ------------------------------------------------------------------ groups
#
# Items a person settles together are one entry in the file, with the members under `each:`. Three groups take one
# answer for all their members: `suggested` (every value the code suggests: `yes` takes them all, and any one can
# still be given its own answer), `defects.<workflow>` (keep them all, fix them all, or decide each; the defects
# are listed only after `decide each`) and `specs` (yes, or name the workflow that is wrong). Three only gather
# what one person answers in one sitting, one answer each: `endpoints` (the non-production systems), `questions`
# (the specs' questions) and `about-the-estate` (what the export cannot say). A group of one is its item.
#
# A group's answer counts for the members the file showed when it was given (the memo keeps them), never for an
# item that appears later: that one is asked on its own. Every stage still reads each item by its own id (state).

SETTLED_BY_ONE = ("suggested", "defects", "specs", "same")
DEFECT_ANSWERS = {"keep all": "keep", "keep them all": "keep", "keep": "keep", "keep each": "keep",
                  "fix all": "fix", "fix them all": "fix", "fix": "fix", "fix each": "fix"}


def group_of(it: dict) -> str | None:
    k, fam = it["key"], it.get("family")
    if it.get("group") == "confirm" and it.get("suggested"):
        return "suggested"
    if fam == "defect" and k.count(".") >= 2:
        return "defects." + k.split(".")[1]
    if it.get("group") == "review":
        return "specs"
    if fam == "endpoint" or k.startswith("q3-"):
        return "endpoints"
    if fam == "question":
        return "questions"
    if it.get("family") == "preflight" and not k.startswith("q9-"):      # Q9 (keep or replace vRA) stays alone
        return "about-the-estate"
    return None


def merges(loaded: dict | None = None) -> dict[str, list[str]]:
    """Groups the re-planning made (`same.<what>`), from the registry and from the file itself (so a file replayed
    in a fresh folder keeps them): group id -> member keys."""
    out: dict[str, list[str]] = {}
    for gid, rec in ((registry().get("_merges") or {}).items()):
        out[gid] = list(rec.get("members") or [])
    for gid, ms in ((loaded or {}).get("file_groups") or {}).items():
        if gid.startswith("same."):
            out[gid] = list(dict.fromkeys(out.get(gid, []) + ms))
    return out


def groups(its: list[dict], loaded: dict | None = None) -> dict[str, list[str]]:
    """Group id -> member keys, in the order asked: groups of two or more, and a group already answered. One
    question per pattern first (`same.<what>`: a shared action's possible defect, the same question of near-duplicate
    workflows, a group the re-planning made), then the groups by kind."""
    out: dict[str, list[str]] = {}
    entries = (loaded or {}).get("entries") or {}
    pats: dict[str, list[str]] = {}
    for it in its:
        if it.get("pattern"):
            pats.setdefault(it["pattern"], []).append(it["key"])
    merged = {k: g for g, ks in merges(loaded).items() for k in ks}
    for it in its:
        if it.get("family") in ESTATE_FAMILIES:
            continue
        p = it.get("pattern")
        use_p = p and (len(pats[p]) > 1 or not is_open((entries.get(p) or {}).get("answer")))
        g = merged.get(it["key"]) or (p if use_p else None) or group_of(it)
        if g:
            out.setdefault(g, []).append(it["key"])
    answered = {g for g in out if loaded and not is_open((loaded["entries"].get(g) or {}).get("answer"))}
    return {g: ks for g, ks in out.items() if len(ks) > 1 or g in answered}


def _kind(gid: str) -> str:
    return gid.split(".", 1)[0]


def covered(gid: str, loaded: dict) -> list[str]:
    """The members a group's answer counts for: those the file showed when it was answered."""
    rec = ((loaded.get("memo") or {}).get("groups") or {}).get(gid) or {}
    if rec.get("members") is not None:
        return list(rec["members"])
    shown = (loaded.get("file_groups") or {}).get(gid)          # a file replayed in a fresh folder: what it lists
    if shown and not is_open((loaded["entries"].get(gid) or {}).get("answer")):
        return list(dict.fromkeys(shown + list(loaded["groups"].get(gid) or [])))
    return list(loaded["groups"].get(gid) or [])


def via_group(key: str, loaded: dict) -> str | None:
    """The answer an item takes from its group's answer, or None."""
    gid = (loaded.get("group_of") or {}).get(key)
    if not gid or _kind(gid) not in SETTLED_BY_ONE:
        return None
    ga = _text((loaded["entries"].get(gid) or {}).get("answer"))
    if is_open(ga) or key not in covered(gid, loaded):
        return None
    kind = _kind(gid)
    if kind == "suggested":
        return "yes" if _yes(ga) else None
    if kind == "defects":
        return DEFECT_ANSWERS.get(ga.lower().rstrip("."))
    if kind == "same":
        if _all_defects(gid, loaded):
            return DEFECT_ANSWERS.get(ga.lower().rstrip("."))
        return ga
    # specs: yes, or a remark for the workflows it names (all of them when it names none)
    if _yes(ga):
        return "yes"
    its = loaded.get("items") or {}
    named = [k for k in covered(gid, loaded) if _names_workflow(ga, str((its.get(k) or {}).get("workflow") or ""))]
    return ga if not named or key in named else "yes"


def _all_defects(gid: str, loaded: dict) -> bool:
    its = loaded.get("items") or {}
    ms = covered(gid, loaded)
    return bool(ms) and all((its.get(m) or {}).get("family") == "defect" for m in ms)


def _names_workflow(text: str, name: str) -> bool:
    if not name:
        return False
    t = text.lower()
    bare = re.sub(r"^[^-]{1,12} - ", "", name).lower()           # "LB - Create Load Balancer" -> "create load balancer"
    return name.lower() in t or bare in t


def _expands(gid: str, loaded: dict) -> bool:
    """A group answered with something other than what settles it: its members are answered one by one."""
    if _kind(gid) not in SETTLED_BY_ONE:
        return True
    ga = _text((loaded["entries"].get(gid) or {}).get("answer"))
    if is_open(ga) or _kind(gid) == "specs":
        return False
    return (_kind(gid) == "suggested" and not _yes(ga)) or ((_kind(gid) == "defects" or (
        _kind(gid) == "same" and _all_defects(gid, loaded))) and ga.lower().rstrip(".") not in DEFECT_ANSWERS)


def group_question(gid: str, its: list[dict]) -> str:
    n = len(its)
    kind = _kind(gid)
    if kind == "same":
        rec = (registry().get("_merges") or {}).get(gid) or {}
        if rec.get("question"):
            return str(rec["question"])
        wfs = list(dict.fromkeys(w for w in (_workflow_of(it) for it in its) if w))
        where = (f"{len(wfs)} workflows ({', '.join(wfs[:3])}{f' and {len(wfs) - 3} more' if len(wfs) > 3 else ''})"
                 if len(wfs) > 1 else f"{n} places")
        q = _member_question(its[0])
        if all(it.get("family") == "defect" for it in its):
            q = re.sub(r"\s*Keep the current behavior, or fix it\?$", "", q)
            return f"The same possible defect in {where}: {q} Keep it everywhere, fix it everywhere, or decide each?"
        return f"Asked of {where}, one answer for all: {q}"
    if kind == "suggested":
        return "Accept every suggested answer below? (yes; to change one, add answer: under it)"
    if kind == "defects":
        name = _workflow_of(its[0]) or gid.split(".", 1)[1]
        page = (its[0].get("link") or f"report/workflows/{gid.split('.', 1)[1]}.html")
        return (f"{n} possible defects in {name}: keep them all, fix them all, or decide each? (they are listed on "
                f"{page})")
    if kind == "specs":
        names = [str(it.get("workflow") or it["key"]) for it in its]
        which = ", ".join(names) if n <= 6 else f"the {n} workflows"
        return f"Are the specs of {which} right? Yes, or name the workflow that is wrong and say what."
    if kind == "endpoints":
        return "Where can a converted flow run before production?"
    if kind == "questions":
        return f"{n} questions from reading the workflows: answer those you can."
    return "What the export cannot say about the estate:"


def q3_systems(it: dict, keys: list[str]) -> list[str]:
    """The systems Q3 (`it`) still asks about when the items `keys` are asked too: an endpoint item
    (endpoint.<system key>) asks for its own system. Matched on the system's key, never its display name."""
    from . import gates as G
    asked = {k.split(".", 1)[1] for k in keys if k.startswith("endpoint.")}
    if it.get("systems"):
        return [s["name"] for s in it["systems"] if G.key_part(s["key"]) not in asked]
    return [x for x in (re.sub(r" \(from [^;]*\)\.?$", "", p.strip()) for p in str(it.get("found") or "").split("; "))
            if x]


def _member_question(it: dict, siblings: list[dict] | None = None) -> str:
    if it["key"].startswith("q3-") and it.get("found"):
        # Q3 asks for a non-production instance of each system; an endpoint item (endpoint.<system key>) already
        # asks for its system, so Q3 asks only for the others, matched on the key, never on a display name
        rest = q3_systems(it, [s["key"] for s in siblings or [] if s is not it])
        return (f"{_and(rest)}: is there a non-production instance? And name any other system the workflows reach."
                if rest else "Do the workflows reach any other system? Name it, or answer none.")
    q = question_of(it)
    if it.get("family") == "defect":
        q = re.sub(r"^Possible defect in [^:]+: ", "", q)
        q = q[:1].upper() + q[1:]
    return q


# ------------------------------------------------------------------ the file


def _and(xs: list[str]) -> str:
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " or " + xs[-1]


def plan(its: list[dict], loaded: dict) -> tuple[list[tuple], list[tuple], list[tuple]]:
    """What goes under open, answered and no_longer_asked, in order: (id, entry, each). Open: what a wave waits
    on, then the rest. Answered: in the order asked. No longer asked: an answer whose item a run no longer finds,
    with the question as it was last asked, so the answer never lands on another item."""
    by_key = {it["key"]: it for it in its}
    gs = groups(its, loaded)
    gof = {k: g for g, ks in gs.items() for k in ks}
    states = {it["key"]: state(it["key"], loaded, it) for it in its}
    shut = closers(loaded)

    def closed(k: str) -> bool:
        """Settled by another answer (estate, assumption, re-planning) and not answered on its own: listed under
        the answer that settled it (`closes:`), not asked."""
        return (k in (loaded.get("closed") or {}) or k in (loaded.get("pending") or {})) and is_open(
            (loaded["entries"].get(k) or {}).get("answer"))

    def single(it: dict, qtext: str | None = None) -> dict:
        k = it["key"]
        old = loaded["entries"].get(k) or {}
        st = states[k]
        e: dict[str, Any] = {"question": qtext or question_of(it)}
        if st["state"] == "stale":
            e["question"] = "The spec changed after this answer. Read it again, then change the answer: " + e["question"]
        sug = it.get("suggested")
        if it.get("group") == "confirm" and sug:
            e["suggested"] = st["answer"] if st["state"] == "answered" and _yes(old.get("answer")) else sug
        e["answer"] = old.get("answer")
        e.update({f: v for f, v in old.items() if f not in TOOL_FIELDS})
        if shut.get(k):
            e["closes"] = shut[k]
        return e

    def done(k: str) -> bool:
        return states[k]["state"] in ("answered", "reviewed")

    units: list[tuple[str, dict, list, bool, bool]] = []          # id, entry, each, open, blocks a wave
    seen: set[str] = set()
    for it in its:
        k = it["key"]
        if k in seen or it.get("family") in ESTATE_FAMILIES:
            continue
        gid = gof.get(k)
        if not gid:
            seen.add(k)
            if closed(k):
                continue
            units.append((k, single(it), [], not done(k), blocks_a_wave(it)))
            continue
        members = [by_key[m] for m in gs[gid] if not closed(m)]
        seen |= set(gs[gid])
        if not members and is_open((loaded["entries"].get(gid) or {}).get("answer")):
            continue
        kind = _kind(gid)
        gold = loaded["entries"].get(gid) or {}
        ga = _text(gold.get("answer"))
        cov = set(covered(gid, loaded))
        expand = _expands(gid, loaded)
        alone, inside = [], []
        for m in members:
            mk = m["key"]
            own = not is_open((loaded["entries"].get(mk) or {}).get("answer"))
            if kind in SETTLED_BY_ONE and not is_open(ga) and not expand and not own and (not done(mk) or mk not in cov):
                alone.append(m)           # not covered by the group's answer, or reviewed before its spec changed
            else:
                inside.append(m)
        e: dict[str, Any] = {"question": group_question(gid, members)}
        if kind in SETTLED_BY_ONE or not is_open(gold.get("answer")):      # a line of the person's own is kept
            e["answer"] = gold.get("answer")
        e.update({f: v for f, v in gold.items() if f not in TOOL_FIELDS})
        if shut.get(gid):
            e["closes"] = shut[gid]
        each = []
        for m in inside:
            mk = m["key"]
            own = not is_open((loaded["entries"].get(mk) or {}).get("answer"))
            show = (kind in ("suggested", "same") or kind not in SETTLED_BY_ONE or own
                    or (expand and kind == "defects"))
            if not show:
                continue
            me = single(m, _member_question(m, members))
            if kind in SETTLED_BY_ONE and not own and not expand:
                me.pop("answer", None)    # a member the group's answer settles: no line to fill in
            each.append((mk, me))
        if kind in SETTLED_BY_ONE:
            is_open_ = is_open(ga) or (expand and any(not done(m["key"]) for m in inside))
        else:
            is_open_ = any(not done(m["key"]) for m in inside)
        if not members:
            is_open_ = False
        units.append((gid, e, each, is_open_, any(blocks_a_wave(m) and not done(m["key"]) for m in members)))
        for m in alone:
            units.append((m["key"], single(m), [], True, blocks_a_wave(m)))
    opened = [(k, e, ea) for k, e, ea, o, b in units if o and b] + [(k, e, ea) for k, e, ea, o, b in units if o and not b]
    answered = [(k, e, ea) for k, e, ea, o, _b in units if not o]
    shown = {k for k, _e, _ea, _o, _b in units} | {sk for _k, _e, ea, _o, _b in units for sk, _ in ea}
    gone: list[tuple] = []
    for k, old in loaded["entries"].items():
        if k in shown or k in by_key or k in gs or is_open(old.get("answer")) and not any(f not in TOOL_FIELDS for f in old):
            continue
        q = str(old.get("question") or "").strip()
        q = q[len(GONE):].strip() if q.startswith(GONE) else "" if q == GONE.strip() else q
        e = {"question": q} if q else {}
        if old.get("suggested") not in (None, ""):
            e["suggested"] = old["suggested"]
        e["answer"] = old.get("answer")
        e.update({f: v for f, v in old.items() if f not in TOOL_FIELDS})
        gone.append((k, e, []))
    return opened, answered, sorted(gone, key=lambda x: x[0])


def closers(loaded: dict) -> dict[str, list[str]]:
    """Answer id -> the items its answer settled, as answers.yaml lists them under it (`closes:`)."""
    out: dict[str, list[str]] = {}
    for k, c in sorted((loaded.get("closed") or {}).items()):
        if is_open((loaded["entries"].get(k) or {}).get("answer")):
            out.setdefault(c["by"], []).append(k)
    return out


def estate_rows(its: list[dict], loaded: dict) -> list[tuple]:
    """The `estate:` section: the estate questions, then the assumptions, each with what its answer settles."""
    shut = closers(loaded)
    rows: list[tuple] = []
    for fam in ESTATE_FAMILIES:
        for it in its:
            if it.get("family") != fam:
                continue
            k = it["key"]
            old = loaded["entries"].get(k) or {}
            e: dict[str, Any] = {}
            if fam == "assume":
                e["assumed"] = it.get("assumed") or it["ask"]
            else:
                e["question"] = it.get("question") or it["ask"]
                if it.get("options"):
                    e["options"] = list(it["options"])
                if it.get("suggested"):
                    e["suggested"] = it["suggested"]
            e["answer"] = old.get("answer")
            e.update({f: v for f, v in old.items() if f not in TOOL_FIELDS})
            if shut.get(k):
                e["closes"] = shut[k]
            later = sorted(x for x, by in (loaded.get("pending") or {}).items() if by == k)
            if later:
                e["settles"] = later
            rows.append((k, e, []))
    return rows


def _lines(e: dict, each: list) -> int:
    """How many answers an entry still waits for: its own answer line, and each member's."""
    return int("answer" in e and is_open(e.get("answer"))) + sum(
        1 for _k, me in each if "answer" in me and is_open(me.get("answer")))


def count(loaded: dict | None = None) -> dict:
    """What answers.yaml waits for, counted the way the file shows it: `open` is the number of empty answer lines
    a person can fill (an estate question, an entry, a member listed under a group), `entries` the open entries,
    `closed` the items another answer settled, `assumed` the assumptions in force. The brief and the terminal
    show these numbers, so they always match the file."""
    loaded = loaded if loaded is not None else load()
    its = items()
    opened, _a, _g = plan(its, loaded)
    est = [r for r in estate_rows(its, loaded) if r[0].startswith("estate.")]
    est_open = sum(1 for _k, e, _ea in est if is_open(e.get("answer")))
    assumed = sum(1 for it in its if it.get("family") == "assume"
                  and str(_text((loaded["entries"].get(it["key"]) or {}).get("answer"))).strip().lower() in
                  ("", "yes", "y", "keep", "keep it", "ok", "agreed", "true"))
    return {"open": est_open + sum(_lines(e, ea) for _k, e, ea in opened),
            "entries": est_open + len(opened), "estate_open": est_open,
            "closed": len([k for k in (loaded.get("closed") or {})
                           if is_open((loaded["entries"].get(k) or {}).get("answer"))]),
            "assumed": assumed}


def shown(loaded: dict | None = None) -> list[dict]:
    """The open entries of the file as a person sees them: {"id", "members", "blocks"}; a group counts once."""
    loaded = loaded if loaded is not None else load()
    its = items()
    by_key = {it["key"]: it for it in its}
    gs = groups(its, loaded)
    opened, _a, _g = plan(its, loaded)
    est = [{"id": k, "members": [k], "blocks": False, "lines": 1}
           for k, e, _ea in estate_rows(its, loaded) if k.startswith("estate.") and is_open(e.get("answer"))]
    return est + [{"id": k, "members": gs.get(k) or [k], "lines": _lines(e, ea),
                   "blocks": any(blocks_a_wave(by_key[m]) for m in (gs.get(k) or [k]) if m in by_key)}
                  for k, e, ea in opened]


def render(its: list[dict], loaded: dict) -> str:
    opened, answered, gone = plan(its, loaded)
    L = [header(), ""]
    est = estate_rows(its, loaded)
    for sec, rows in (("estate", est), ("open", opened), ("answered", answered), ("no_longer_asked", gone)):
        if not rows and sec in ("estate", "no_longer_asked"):
            continue
        if not rows:
            L += [f"{sec}: []", ""]
            continue
        L.append(f"{sec}:")
        for k, e, each in rows:
            L += _entry(k, e, each)
        L.append("")
    L.append("signed_for:" + _scalar((loaded.get("sign") or {}).get("signed_for") or ""))
    return "\n".join(L) + "\n"


def _remember(its: list[dict], loaded: dict, reg: dict) -> bool:
    """Record in the memo what the file will no longer show: the value each `yes` took, the spec each review
    was written for, and the members each group's answer counts for (those the file showed when it was given).
    True when the memo changed."""
    memo = _memo(reg)
    before = json.dumps(memo, sort_keys=True)
    memo["stand_in"] = bool(loaded.get("memo", {}).get("stand_in") or memo["stand_in"])
    for k, v in (loaded.get("memo", {}).get("reviewed") or {}).items():
        memo["reviewed"].setdefault(k, v)
    for gid, shown in (loaded.get("file_groups") or {}).items():
        # an answered group as the file lists it, kept before a stage that does not ask it yet rewrites the file
        ga = _text((loaded["entries"].get(gid) or {}).get("answer"))
        if not is_open(ga) and gid not in memo["groups"]:
            memo["groups"][gid] = {"members": list(shown), "answer": ga}
    for gid, members in groups(its, loaded).items():
        ga = _text((loaded["entries"].get(gid) or {}).get("answer"))
        rec = memo["groups"].get(gid)
        if is_open(ga):
            memo["groups"][gid] = {"members": members, "answer": None}      # what the file shows now
        elif not rec:
            shown = (loaded.get("file_groups") or {}).get(gid) or []
            memo["groups"][gid] = {"members": list(dict.fromkeys(shown + members)), "answer": ga}
        elif rec.get("answer") != ga:
            memo["groups"][gid] = {**rec, "answer": ga}
    loaded["memo"] = {**(loaded.get("memo") or {}), "groups": memo["groups"]}
    for it in its:
        k = it["key"]
        e = loaded["entries"].get(k) or {}
        ans = _text(e.get("answer"))
        if is_open(ans):
            ans = via_group(k, loaded) or ans
        if it.get("group") == "confirm" and it.get("suggested"):
            if _yes(ans):
                # the value the person saw next to their yes, else today's suggestion
                memo["confirmed"].setdefault(k, _text(e.get("suggested")) or _text(it["suggested"]))
            else:
                memo["confirmed"].pop(k, None)
        if it.get("group") == "review":
            now = str((it.get("fixed") or {}).get("spec") or "")
            then = memo["reviewed"].get(k)
            if is_open(ans):
                memo["reviewed"].pop(k, None)
            elif not then or then.get("answer") != ans:
                memo["reviewed"][k] = {"spec": now, "answer": ans}
    from . import planner
    planner.remember(loaded, memo)
    if json.dumps(memo, sort_keys=True) == before:
        return False
    reg["_memo"] = memo
    return True


def write(renames: dict[str, str] | None = None) -> dict:
    """Rewrite answers.yaml from the registry, keeping every answer. Never over a file that does not parse.
    `renames` carries an answer from an item's old key to its new one (register)."""
    C.ensure(C.INPUT)
    path = C.ANSWERS
    loaded = load(path)
    if not loaded["ok"]:
        return {"written": False, "warnings": loaded["warnings"]}
    reg = registry()
    if _rename(loaded, reg, renames or {}):
        C.write_json(_registry_path(), reg)
    its = items()
    if _remember(its, loaded, reg):
        C.write_json(_registry_path(), reg)
        loaded["memo"] = _memo(reg)
    text = render(its, loaded)
    old = open(path, encoding="utf-8").read() if os.path.exists(path) else None
    if old == text:
        return {"written": False, "warnings": loaded["warnings"]}
    # the rewrite must read back to the same answers, with no id lost: if it would not, keep the file as it is
    back = load_text(text)
    for k, e in loaded["entries"].items():
        if is_open(e.get("answer")) and not any(f not in TOOL_FIELDS for f in e):
            continue
        b = back.get(k)
        if b is None:
            return {"written": False, "warnings": loaded["warnings"] + [f"answers.yaml was not rewritten: {k} would be lost"]}
        for f, v in e.items():
            if (f == "answer" or f not in TOOL_FIELDS) and not _same(b.get(f), v):
                return {"written": False, "warnings": loaded["warnings"] + [
                    f"answers.yaml was not rewritten: {k}.{f} would not read back the same"]}
    if old is not None:
        C.write_text(os.path.join(C.DATA, "answers.yaml.previous"), old)
        before = os.path.join(C.DATA, "answers.yaml.before-2g")
        if loaded["old_format"] and not os.path.exists(before):
            C.write_text(before, old)
    st = os.stat(path) if old is not None else None
    C.write_text(path, text)
    if st is not None and not loaded["old_format"] and _answers_of(old) == _answers_of(text):
        # only the tool's part changed: keep the file's time, so a stage that compares times does not take the
        # rewrite for a person's edit
        os.utime(path, (st.st_atime, st.st_mtime))
    return {"written": True, "warnings": loaded["warnings"], "migrated": loaded["old_format"]}


def _rename(loaded: dict, reg: dict, renames: dict[str, str]) -> bool:
    """Move each answer (and what the memo keeps for it) from an old key to its new one, unless the new key has an
    answer of its own. True when the memo changed."""
    memo = reg.get("_memo") or {}
    changed = False
    for old, new in renames.items():
        e = loaded["entries"].get(old)
        if e is None or not is_open((loaded["entries"].get(new) or {}).get("answer")):
            continue
        loaded["entries"][new] = {**e, "question": None}
        del loaded["entries"][old]
        for part in ("confirmed", "reviewed"):
            for m in (memo.get(part), (loaded.get("memo") or {}).get(part)):
                if isinstance(m, dict) and old in m:
                    m[new] = m.pop(old)
                    changed = True
    return changed


def _same(a, b) -> bool:
    return a == b or (a in (None, "") and b in (None, ""))


def load_text(text: str) -> dict[str, dict]:
    """id -> entry of a file's text in today's format (the read-back check)."""
    return flat(_yaml(text) or {})


def _answers_of(text: str | None) -> str:
    try:
        d = _yaml(text or "") or {}
    except yaml.YAMLError:
        return text or ""
    if not isinstance(d, dict) or is_old_format(d):
        return text or ""
    keep = {"signed_for": d.get("signed_for")}
    for k, e in load_text(text or "").items():
        keep[k] = {f: v for f, v in e.items() if f not in ("question", "suggested")}
    return json.dumps(keep, sort_keys=True, default=str)


def fingerprint() -> str:
    """What a person wrote in answers.yaml, as one string: changes only when an answer does."""
    p = C.ANSWERS
    return _answers_of(open(p, encoding="utf-8").read()) if os.path.exists(p) else ""


# ------------------------------------------------------------------ what is still asked

def summary(loaded: dict | None = None) -> dict:
    """Per group: open and done items; per family within a group, the counts (the front page groups by it)."""
    loaded = loaded if loaded is not None else load()
    out: dict[str, Any] = {g: {"open": [], "done": [], "stale": []} for g in GROUPS[:3]}
    for it in items():
        if it["group"] not in out:
            continue                                          # an assumption: nothing to answer
        st = state(it["key"], loaded, it)
        bucket = "done" if st["state"] in ("answered", "reviewed") else ("stale" if st["state"] == "stale" else "open")
        out[it["group"]][bucket].append({**it, "status": st})
    out["sign"] = signature(loaded)
    return out


# ------------------------------------------------------------------ migration from the 2c/2d files

def migrate() -> list[str]:
    """
    Carry the answers of an estate written before answers.yaml into it, once: preflight answers.md, the
    brief's decisions.yaml and reviews.yaml, the BRIEF.md signature. Each old file is renamed `<name>.migrated`
    (kept, read by nothing) once its answers are in. Run after the stages have registered their items, so
    every answer lands on its item; an answer with no item today stays under `answered` as no longer asked.
    Returns one line per file carried.
    """
    pre, brief = C.PRE, C.BRIEF
    sources = {"answers.md": os.path.join(pre, "answers.md"), "decisions.yaml": os.path.join(brief, "decisions.yaml"),
               "reviews.yaml": os.path.join(brief, "reviews.yaml"), "BRIEF.md": os.path.join(brief, "BRIEF.md")}
    present = {n: p for n, p in sources.items() if os.path.exists(p)}
    if not present:
        return []
    loaded = load()
    if not loaded["ok"]:
        return [f"not migrated: {w}" for w in loaded["warnings"]]
    by_key = {it["key"]: it for it in items()}
    done = []
    memo = loaded["memo"]

    def put(key, answer, who=None, spec=None):
        cur = loaded["entries"].get(key) or {}
        if not is_open(cur.get("answer")):
            return False                        # an answer already in answers.yaml wins
        it = by_key.get(key) or {}
        if it.get("group") == "confirm" and (_yes(answer) or str(answer).lower().startswith("confirmed")):
            answer = it.get("suggested") or answer
        loaded["entries"][key] = {**cur, "answer": answer}
        if spec:
            memo["reviewed"][key] = {"spec": spec, "answer": _text(answer)}
        if "stand-in" in str(who or "").lower():
            memo["stand_in"] = True
        return True

    if "answers.md" in present:
        from . import preflight
        text = open(present["answers.md"]).read()
        parsed = preflight._parse_answers(text)
        wm = re.search(r"^Answered by:[ \t]*(.*)$", text, re.M)
        who = wm.group(1).strip() if wm else ""
        n = sum(put(preflight.answer_key(qid), ans, who) for qid, ans in parsed.items() if not is_open(ans))
        done.append(f"answers.md: {n} preflight answer(s)")
    if "decisions.yaml" in present:
        raw = safety.load_yaml(open(present["decisions.yaml"], "rb").read(), "decisions.yaml") or {}
        n = 0
        for k, e in ((raw.get("decisions") or {}) if isinstance(raw, dict) else {}).items():
            if not isinstance(e, dict) or is_open(e.get("decision")):
                continue
            n += put(rename_decision(str(k)), str(e["decision"]), e.get("decided_by"))
        done.append(f"decisions.yaml: {n} decision(s)")
    if "reviews.yaml" in present:
        raw = safety.load_yaml(open(present["reviews.yaml"], "rb").read(), "reviews.yaml") or {}
        n = 0
        for u, e in ((raw.get("reviews") or {}) if isinstance(raw, dict) else {}).items():
            if not isinstance(e, dict) or not str(e.get("reviewed_by") or "").strip():
                continue
            n += put(rename_review(str(u)), "yes", e.get("reviewed_by"), e.get("spec"))
        done.append(f"reviews.yaml: {n} review(s)")
    if "BRIEF.md" in present:
        from . import brief as B
        ap = B.parse_approval(open(present["BRIEF.md"]).read())
        if ap.get("signed") and not signature(loaded)["signed"]:
            loaded["sign"] = {"signed_for": SIGN.get(ap["scope"], "")}
            if "stand-in" in str(ap.get("approved_by") or "").lower():
                memo["stand_in"] = True
            done.append(f"BRIEF.md: the signature ({ap['scope']})")
        else:
            done.append("BRIEF.md: no signature to carry")
    reg = registry()
    reg["_memo"] = memo
    C.write_json(_registry_path(), reg)
    C.ensure(C.INPUT)
    C.write_text(C.ANSWERS, render(items(), loaded))
    write()
    for p in present.values():
        os.replace(p, p + ".migrated")
    return done


# Old decision and review keys carried a workflow's full ID; today's carry its short name.
RENAMES: dict[str, str] = {}


def rename_decision(k: str) -> str:
    """A decision key of an older estate as today's: full workflow IDs became short names, and runs of '-' one."""
    return RENAMES.get(k) or re.sub(r"-{2,}", "-", k)


def rename_review(uid: str) -> str:
    return RENAMES.get(uid, f"spec.{uid}")


def set_renames(m: dict[str, str]) -> None:
    RENAMES.clear()
    RENAMES.update(m)


# ------------------------------------------------------------------ helpers for tests and scripts

def _walk(data: dict):
    """(section, entry) for every entry of a parsed answers.yaml, a group's members (under `each:`) included."""
    for sec in SECTIONS:
        for e in data.get(sec) or []:
            if not isinstance(e, dict):
                continue
            yield sec, e
            for sub in e.get("each") or [] if isinstance(e.get("each"), list) else []:
                if isinstance(sub, dict):
                    yield sec, sub


def flat(data: dict) -> dict[str, dict]:
    """id -> entry (without the id) of a parsed answers.yaml, from every list and group (tests use it)."""
    return {str(e["id"]): {f: v for f, v in e.items() if f not in ("id", "each")}
            for _sec, e in _walk(data) if "id" in e}


def entry_of(data: dict, key: str) -> dict | None:
    """One entry of a parsed answers.yaml, whichever list or group it sits in (tests use it)."""
    for _sec, e in _walk(data):
        if str(e.get("id")) == key:
            return {f: v for f, v in e.items() if f not in ("id", "each")}
    return None


def section_of(data: dict, key: str) -> str | None:
    for sec, e in _walk(data):
        if str(e.get("id")) == key:
            return sec
    return None


def set_answer(key: str, value, path: str | None = None) -> None:
    """Write one answer into answers.yaml as a person would: only the item's `answer:` changes (tests and the
    demo scripts use it). The next run moves the item."""
    path = path or C.ANSWERS
    C.write_text(path, _edit(path, lambda d: _set(d, key, "answer", value)))


def set_signature(covers: str, path: str | None = None) -> None:
    """Sign as a person would: `signed_for:` set to `wave 1`, `full plan`, or empty."""
    path = path or C.ANSWERS
    C.write_text(path, _edit(path, lambda d: d.__setitem__("signed_for", SIGN.get(covers, covers) or None)))


def _set(d: dict, key: str, field: str, value) -> None:
    for _sec, e in _walk(d):
        if str(e.get("id")) == key:
            e[field] = value
            return
    d.setdefault("open", [])
    d["open"] = d["open"] or []
    d["open"].append({"id": key, field: value})


def _edit(path: str, change) -> str:
    """The file after one change, keeping today's shape (an old-format file is rendered in today's first)."""
    loaded = load(path)
    text = open(path, encoding="utf-8").read() if os.path.exists(path) else render(items(), loaded)
    d = _yaml(text) or {}
    if is_old_format(d):
        d = _yaml(render(items(), loaded)) or {}
    change(d)
    ld = {"entries": {}, "sign": {"signed_for": _text(d.get("signed_for"))}, "memo": loaded["memo"]}
    order = []
    each: dict[str, list] = {}
    for sec in SECTIONS:
        for e in d.get(sec) or []:
            ld["entries"][str(e["id"])] = {f: v for f, v in e.items() if f not in ("id", "each")}
            each[str(e["id"])] = [(str(x["id"]), {f: v for f, v in x.items() if f != "id"})
                                  for x in e.get("each") or [] if isinstance(x, dict) and "id" in x]
            order.append((sec, str(e["id"])))
    L = [header(), ""]
    for sec in SECTIONS:
        rows = [k for s, k in order if s == sec]
        if not rows and sec == "no_longer_asked":
            continue
        if not rows:
            L += [f"{sec}: []", ""]
            continue
        L.append(f"{sec}:")
        for k in rows:
            L += _entry(k, ld["entries"][k], each.get(k))
        L.append("")
    L.append("signed_for:" + _scalar(ld["sign"]["signed_for"]))
    return "\n".join(L) + "\n"


def today() -> str:
    return _dt.date.today().isoformat()
