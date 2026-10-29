"""
index.html is the brief, written the way a person would write it for the customer, and BRIEF.md is the same
text with an appendix. Both come from one document built here (`document`), so they cannot disagree.

In this order: the title and one opening line (what this is, and what it was built from); the status box (the
next step, how much is ready today, a stand-in line when an answer or the signature is a stand-in's); what is in
the estate, as one paragraph; the recommendation (one sentence on the workflows, the Direct and Redesign split,
two figures, the table merged by kind, its legend); what we need from you, grouped by kind with counts, blocking
first; the plan, one bullet per wave; and where to check. Every list is grouped and capped (CAPS), so the page
keeps its length as the estate grows: tests/test_front.py builds it for a 60-workflow estate.

The words follow the vRA migration guide: Direct and Redesign, a wave's first of a kind, estate, targets,
recommendations, Day 0, Day 1 and Day 2, vRA and vRO. Kestra task class names and answers.yaml keys stay off the front
page's running text (tests/test_writing.py checks every customer-facing file).

Also here: the two detail pages the front page leans on most, translation.html (every element, in its row of the
front page's table, with why) and workflows/index.html (every workflow, with its outcome and wave).
"""
from __future__ import annotations

import datetime as _dt
import html
import re
from typing import Any

from . import common as C
from . import status as S
from . import theme as TH
from . import triggers as TG
from . import your_input as Y

GUIDE = "https://kestra.io/resources/migration/vra"
# Section 3.2.3 of the guide (how to classify the conversion effort): the guide's page, with no anchor, until the
# guide is published and its anchors are known.
GUIDE_323 = GUIDE

CAPS = {"rows": 8, "waves": 6, "systems": 4, "domains": 3, "clients": 4}
WORDS = {0: "no", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight",
         9: "nine", 10: "ten"}
HOW_CLASS = {"Direct": "done", "Redesign": "todo", "Keep": "done", "Retire": "done", "Open": "wait"}
# What the How column shows: the guide's two terms, Direct and Redesign; anything else in its own words
WAITS = {"vra": "Waits on whether vRA stays", "answer": "Waits on an answer", "model": "Waits on a model"}
HOW_SHOWN = {"Keep": "Stays in vRA", "Retire": "Retired"}
# The interactive run's closing line, said again at the top of the brief's assumptions
AGAIN = "To answer more questions and sharpen the brief, run the same command again. Your answers are kept."


# ------------------------------------------------------------------ small words

def word(n: int) -> str:
    return WORDS.get(n, str(n))


def cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _s(n: int, one: str, many: str | None = None) -> str:
    return one if n == 1 else (many or one + "s")


def _and(xs: list[str], conj: str = "and") -> str:
    """Words in a list: 'a, b and c'."""
    xs = [x for x in xs if x]
    return "" if not xs else xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + f" {conj} " + xs[-1]


def _clauses(xs: list[str]) -> str:
    """Clauses in a list, each set off by a comma: 'a, b, and c'; 'a, and b'."""
    xs = [x for x in xs if x]
    return "" if not xs else xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + ", and " + xs[-1]


def _capped(xs: list[str], n: int, more: str) -> str:
    return _and(xs[:n] + ([f"{len(xs) - n} {more}"] if len(xs) > n else []))


def article(s: str) -> str:
    """'an' before a vowel sound: a vowel, or a spelled-out letter such as F, H, L, M, N, R, S or X."""
    first = s.split()[0] if s.split() else s
    vowel = first[:1].lower() in "aeio" or (first[:1] in "FHLMNRSX" and (len(first) == 1 or not first[1:2].islower()))
    return ("an " if vowel else "a ") + s


def _date(iso: str) -> str:
    try:
        d = _dt.date.fromisoformat(str(iso)[:10])
    except ValueError:
        return str(iso)
    return f"{d.strftime('%B')} {d.day}, {d.year}"


ACTION_NAME = re.compile(r"(add|apply|change|close|create|decommission|delete|deploy|deregister|destroy|extend|get|"
                         r"grant|install|join|move|open|patch|power|provision|register|remove|request|reserve|reset|"
                         r"resize|restart|revert|revoke|run|set|update)\b", re.I)


def phrase_name(name: str) -> str:
    """A catalog item's name as a phrase: lower case, and a noun plus participle joined ('load-balanced')."""
    ws = name.split()
    out, i = [], 0
    while i < len(ws):
        if i + 2 < len(ws) and ws[i + 1].lower().endswith("ed") and ws[i].isalpha():
            out.append(f"{ws[i]}-{ws[i + 1]}".lower())
            i += 2
            continue
        out.append(ws[i] if ws[i].isupper() and len(ws[i]) > 1 else ws[i].lower())
        i += 1
    return " ".join(out)


def short_names(assess: dict) -> dict:
    """Workflow names without the tag every workflow in the estate shares ('LB - ')."""
    names = {u: w["name"] for u, w in assess["workflows"].items()}
    vals = list(names.values())
    tag = ""
    m = re.match(r"^(.{1,12}?\s[-:]\s)", vals[0]) if vals else None
    if m and len(vals) > 1 and all(v.startswith(m.group(1)) for v in vals):
        tag = m.group(1)
    return {u: n[len(tag):] if tag else n for u, n in names.items()}


def tname(t: dict) -> str:
    return t.get("short_name") or t["name"]


# ------------------------------------------------------------------ the table, merged by kind

def how_of(r: dict) -> str:
    """A translation row's How, in the guide's words."""
    if r["how"] == "retire":
        return "Retire"
    if r["how"] in ("needs an answer", "needs a model"):
        return "Open"
    if r["id"].startswith("vra") and r["how"] == "core task":
        return "Keep"                                     # vRA stays and keeps doing this
    return "Direct" if r["path"] == "mechanical" else "Redesign"


def _row_systems(r: dict, assess: dict, brief: dict) -> list[str]:
    """The targets a client row's actions reach (for merging rows by system)."""
    if r.get("systems"):
        return sorted(set(r["systems"]) - {"vro-platform", "rest-host"})
    actions = {a["key"]: a for a in assess["actions"]}
    atri = brief["action_triage"]
    out = set()
    for i in r["items"]:
        k = (i.get("eid") or "")[2:]
        a = actions.get(k) or {}
        out |= {x for x in (a.get("library_for") or []) + (a.get("reaches") or []) if x in assess["targets"]}
        out |= {u.get("system") for u in (atri.get(k) or {}).get("uses") or [] if u.get("system") in assess["targets"]}
    return sorted(out - {"vro-platform", "rest-host"})


def _plugin_title(plugin: str, target: dict) -> str:
    """'plugin-ee-f5' -> 'F5', spelled the way the target's name spells it."""
    base = re.sub(r"^plugin-(ee-)?", "", plugin)
    for w in re.findall(r"[A-Za-z0-9]+", target["name"]):
        if w.lower() == base.lower():
            return w
    return base.replace("-", " ").title()


def merged_rows(assess: dict, brief: dict) -> list[dict]:
    """
    The translation table merged by kind of element and how it converts, for the front page: client actions per
    system; the workflows with their helper actions; vRO's state, settings and templates; vRA's catalog items and
    cloud templates; vRA's event subscriptions with the actions that read its events. Every element is in one
    merged row, so the counts add up to the estate's total. A client action reaching several systems joins the
    system it still needs HTTP code for.
    """
    tr = (brief.get("translation") or {}).get("rows") or []
    targets = assess["targets"]
    cov = {r["key"]: r for r in (assess.get("plugin_coverage") or {}).get("rows") or []}
    wf = assess["workflows"]
    tp = brief.get("trigger_plan") or {}
    replacing = tp.get("front_end") in TG.REPLACING
    groups: dict[tuple, dict] = {}

    def g(key, order, how):
        k = key + (how,)
        if k not in groups:
            groups[k] = {"key": k, "order": order, "how": how, "count": 0, "lines": 0, "rows": [], "systems": set()}
        return groups[k]

    for r in tr:
        how = how_of(r)
        rid = r["id"]
        if rid.startswith(("a-client", "a-rest-helper")):
            sysk = _row_systems(r, assess, brief)
            http = [s for s in sysk if (targets.get(s) or {}).get("reach") != "plugin"]
            home = (http[0] if len(http) == 1 else sysk[0] if len(sysk) == 1 else "several") if sysk else "several"
            x = g(("client", home), 0, how)
            x["systems"] |= set(sysk)
        elif rid.startswith("wf-") or rid in ("a-with-flows",) or (rid.startswith("a-") and how in ("Redesign", "Open")
                                                                  and rid not in ("a-payload", "a-vro-state")):
            x = g(("workflows",), 1, how)
        elif rid.startswith("a-") and how == "Retire":
            x = g(("workflows",), 1, how)
        elif rid.startswith("a-") and rid not in ("a-payload", "a-vro-state"):
            x = g(("workflows",), 1, how)                 # duplicate and wrapper actions go with the workflows
        elif rid in ("a-vro-state",) or rid.startswith("el-"):
            x = g(("vro-state",), 2, how)
        elif rid in ("vra-sub", "a-payload"):
            x = g(("vra-events",), 4, how)
        elif rid.startswith("vra"):
            x = g(("vra-front",), 3, how)
        else:
            x = g(("other", rid), 5, how)
        x["rows"].append(r)
        x["count"] += r["count"]
        x["lines"] += r["lines"] or 0

    out = sorted(groups.values(), key=lambda x: (x["order"], -x["count"] if x["order"] == 0 else 0,
                                                 ["Direct", "Redesign", "Keep", "Open", "Retire"].index(x["how"])))
    # many client systems: the smallest are said together, so the table keeps its length
    clients = [x for x in out if x["order"] == 0]
    if len(clients) > CAPS["clients"]:
        keep, rest = clients[:CAPS["clients"] - 1], clients[CAPS["clients"] - 1:]
        by_how: dict[str, dict] = {}
        for x in rest:
            y = by_how.setdefault(x["how"], {"key": ("client", "others", x["how"]), "order": 0, "how": x["how"],
                                             "count": 0, "lines": 0, "rows": [], "systems": set(), "others": True})
            y["count"] += x["count"]
            y["lines"] += x["lines"]
            y["rows"] += x["rows"]
            y["systems"] |= x["systems"]
        out = keep + list(by_how.values()) + [x for x in out if x["order"] != 0]

    n_wf = len(wf)
    life = all(w.get("lifecycle_group") for w in wf.values())
    for x in out:
        x["label"], x["kestra"] = _say(x, assess, brief, cov, replacing, n_wf, life)
        x["id"] = "-".join(str(k) for k in x["key"]).lower().replace(" ", "-")
        x["evidence"] = _evidence(x)
        x["ready"] = sum(r.get("ready", 0) for r in x["rows"])
        x["systems"] = sorted(x["systems"])
        x["waits"] = _waits(x) if x["how"] == "Open" else None
        x["how_shown"] = WAITS[x["waits"]] if x["waits"] else HOW_SHOWN.get(x["how"], x["how"])
    return out


def _waits(x: dict) -> str:
    """What an Open row waits on: the vRA question (Q9) when all its rows are vRA's, else an answer, or a model."""
    if any(r["how"] == "needs a model" for r in x["rows"]):
        return "model"
    if all(r["id"].startswith("vra") or r["id"] == "a-payload" for r in x["rows"]):
        return "vra"
    return "answer"


def _say(x, assess, brief, cov, replacing, n_wf, life) -> tuple[str, str]:
    """What a merged row is called, and what it becomes in Kestra, in plain words."""
    targets = assess["targets"]
    kind, how = x["key"][0], x["how"]
    ids = {r["id"] for r in x["rows"]}
    if kind == "client":
        if x.get("others"):
            names = [tname(targets[s]) for s in x["systems"] if s in targets]
            # clients of REST hosts no signature names count as one more system: their rows name none
            unnamed = any(not _row_systems(r, assess, brief) for r in x["rows"])
            label = f"Client actions for {word(len(names) + unnamed)} other systems"
            return label, ("tasks of Kestra plugins, or HTTP tasks" if how == "Direct" else "new clients, designed for Kestra")
        home = x["key"][1]
        t = targets.get(home)
        sysn = tname(t) if t else "several systems"
        label = f"{sysn} client actions" if t else "Client actions for several systems"
        if not t and not x["systems"]:
            label = "HTTP client actions"                 # REST hosts the export does not name
        if how != "Direct":
            return label, "a new client, designed for Kestra"
        if t and t.get("reach") == "plugin":
            plugs = ((cov.get(home) or {}).get("catalog") or {}).get("plugins") or []
            if plugs and t.get("pattern_gap"):
                return label, (f"Kestra's {_plugin_title(plugs[0]['name'], t)} plugin, which works another way, "
                               f"or HTTP tasks on {sysn}'s API")
            if plugs:
                p = plugs[0]
                return label, (f"tasks of Kestra's {_plugin_title(p['name'], t)} plugin"
                               + (" (Enterprise)" if p.get("license") == "EE" or t.get("enterprise_only") else ""))
            return label, "tasks of a Kestra plugin"
        return label, (f"HTTP tasks on {sysn}'s REST API" if t else "HTTP tasks on each system's REST API")
    if kind == "workflows":
        n = sum(r["count"] for r in x["rows"] if r["id"].startswith("wf-"))
        acts = sum(r["count"] for r in x["rows"] if not r["id"].startswith("wf-"))
        what = (f"The {word(n)} lifecycle workflows" if life and n == n_wf else
                f"The {word(n)} {_s(n, 'workflow')}" if n == n_wf else f"{cap(word(n))} {_s(n, 'workflow')}")
        if how == "Retire":
            label = (f"{cap(word(n))} {_s(n, 'workflow')} and {word(acts)} {_s(acts, 'action')} nothing uses"
                     if n and acts else f"{cap(word(n or acts))} {_s(n, 'workflow') if n else _s(acts, 'action')} nothing uses")
            return label, "nothing: retired once you confirm it"
        if not n:
            label = f"{cap(word(acts))} {_s(acts, 'action')} " + ("waiting on an answer" if how == "Open" else
                                                                  "rebuilt with the flows that call them")
        else:
            label = what + (", and their helper actions" if acts else "")
        return label, {"Direct": "Kestra flows", "Redesign": "new Kestra flows, rebuilt from their specs",
                       "Open": "Kestra flows, once your answer is in"}.get(how, "Kestra flows")
    if kind == "vro-state":
        state = bool(ids & {"el-state", "a-vro-state"})
        settings = "el-settings" in ids or "a-vro-state" in ids
        files = "el-resource" in ids or "a-vro-state" in ids
        parts = [p for p, on in (("state", state), ("settings", "el-settings" in ids), ("templates", "el-resource" in ids))
                 if on] or ["state"]
        kt = [p for p, on in (("KV store", state), ("variables", settings), ("secrets", settings),
                              ("namespace files", files)) if on]
        return f"{cap(_and(parts))} in vRO", "Kestra's " + _and(kt)
    if kind == "vra-front":
        model = (assess.get("entry_points") or {}).get("model") or {}
        nc, nt = len(model.get("catalog_items") or []), len(model.get("cloud_templates") or [])
        bits = [("the catalog item" if nc == 1 else f"the {word(nc)} catalog items") if nc else "",
                ("cloud template" if nc == 1 and nt == 1 else "the cloud template" if nt == 1 else
                 f"the {word(nt)} cloud templates") if nt else ""]
        label = cap(_and(bits))
        if how == "Keep":
            return label, "stay in vRA, which calls Kestra"
        if how == "Open":
            return label, "a Kestra App and a flow, or they stay in vRA (your answer decides)"
        plug = ""
        for k, row in cov.items():
            if row.get("created_by"):
                ps = (row.get("catalog") or {}).get("plugins") or []
                t = targets.get(k) or {"name": row.get("system") or k}
                if ps:
                    plug = f" with the {_plugin_title(ps[0]['name'], t)} plugin"
        app = "a Kestra App and " if nc else ""
        return label, f"{app}a flow that creates the machines{plug}"
    if kind == "vra-events":
        model = (assess.get("entry_points") or {}).get("model") or {}
        ns = len(model.get("subscriptions") or [])
        pay = "a-payload" in ids
        label = (f"The {word(ns)} event {_s(ns, 'subscription')}" if ns else "") + (
            ", and the actions that read vRA's events" if pay and ns else "The actions that read vRA's events" if pay else "")
        if how == "Keep":
            return label, "Kestra webhooks vRA calls"
        if how == "Open":
            return label, "webhooks or steps of a flow (your answer decides)"
        return label, ("steps in that flow, with typed inputs" if replacing else "steps of a flow, with typed inputs")
    r = x["rows"][0]
    return r["kind"], r["becomes"]


def _evidence(x: dict) -> dict | None:
    """One link per row: the plugin's documentation where there is one, else the page that shows the rest."""
    for r in x["rows"]:
        for e in r.get("evidence") or []:
            if e.get("external") and "plugin" in (e.get("label") or ""):
                return e
    for r in x["rows"]:
        for e in r.get("evidence") or []:
            if e.get("href") and not e["href"].startswith("translation.html"):
                return e
    return None


def split(rows: list[dict]) -> dict:
    out = {h: sum(x["count"] for x in rows if x["how"] == h) for h in HOW_CLASS}
    out["total"] = sum(x["count"] for x in rows)
    out["path"] = out["total"] - out["Open"]
    return out


# ------------------------------------------------------------------ the recommendation

def _wf_counts(brief: dict) -> dict:
    c: dict[str, int] = {}
    for t in brief["triage"].values():
        o = t["outcome"]
        if o == "Needs a model":
            continue
        k = ("rebuilt" if o in ("Rebuild", "Manual") else "retired" if o == "Retire" else "convert")
        c[k] = c.get(k, 0) + 1
    return {k: c.get(k, 0) for k in ("convert", "rebuilt", "retired")}


def _rebuild_reason(brief: dict) -> str:
    rs = [t["reason"] for t in brief["triage"].values() if t["outcome"] in ("Rebuild", "Manual")]
    kinds: dict[str, int] = {}
    for r in rs:
        k = ("vra" if ("vRA" in r or "dispatcher" in r) else "state" if "vRO's own state" in r else
             "client" if "client" in r else "hand")
        kinds[k] = kinds.get(k, 0) + 1
    top = max(kinds, key=lambda k: kinds[k]) if kinds else "hand"
    return {"vra": "they only make sense inside vRA",
            "state": "they only manage vRO itself",
            "client": "they are hand-written clients that Kestra plugins replace",
            "hand": "the code alone does not say enough to convert them"}[top]


def _own_conversions(brief: dict, converting: int) -> str:
    """The conversion work itself, when the count of workflows that convert says more: a near-duplicate follows its
    first of a kind, and a wrapper goes, its callers calling what it calls. Empty when the two are the same."""
    outs = [t["outcome"] for t in brief["triage"].values()]
    own = sum(1 for o in outs if o in ("Convert", "Needs owner input"))
    nd, wr = outs.count("Near-duplicate"), outs.count("Wrapper")
    if not (nd or wr) or own >= converting:
        return ""
    parts = ([f"{word(nd)} {_s(nd, 'is a near-duplicate that follows its', 'are near-duplicates that follow their')} "
              "first of a kind"] if nd else []) + \
            ([f"{word(wr)} {_s(wr, 'is a wrapper', 'are wrappers')}, whose callers call what "
              f"{_s(wr, 'it calls', 'they call')} directly"] if wr else [])
    return (f" {cap(word(own))} of the {word(converting)} need a conversion of their own: {_and(parts)}.")


def recommendation(assess: dict, brief: dict, rows: list[dict]) -> tuple[str, list[str]]:
    """The heading's word, and the opening sentences: what happens to the workflows, then the split."""
    rec = brief["recommendation"]
    v = rec["verdict"]
    c = _wf_counts(brief)
    n = sum(c.values())
    why = _rebuild_reason(brief)
    retire = (f", and {c['retired']} can be retired" if c["retired"] else "")
    if v == "rebuild":
        client = (rec.get("client_share") or 0) + 0.0
        because = ("most of its code is vendor client code that Kestra plugins replace" if client >= 0.3 else
                   {"they only make sense inside vRA": "most of its workflows only make sense inside vRA",
                    "they only manage vRO itself": "most of its workflows only manage vRO itself",
                    "they are hand-written clients that Kestra plugins replace":
                        "most of its workflows are hand-written clients that Kestra plugins replace"}.get(
                       why, "most of its workflows need a hand rewrite, because the code alone does not say enough "
                            "to convert them"))
        first = (f"We recommend rebuilding this estate in Kestra rather than converting it, because {because}. "
                 "The specs and tests from this run become the requirements for the new flows.")
        head = "rebuild"
    elif v == "mix":
        mostly = why.replace("they are ", "", 1).replace("they ", "workflows that ", 1)
        first = (f"Part of this estate converts and part is better rebuilt: {c['convert']} "
                 f"{_s(c['convert'], 'workflow')} convert, {c['rebuilt']} {_s(c['rebuilt'], 'is', 'are')} rebuilt "
                 f"from scratch, mostly {mostly}" + (f", and {c['retired']} can be retired." if c["retired"] else "."))
        head = "a mix"
    elif c["rebuilt"] == 0 and c["retired"] == 0:
        wait = sum(1 for t in brief["triage"].values() if t["outcome"] == "Needs owner input")
        first = ("Every workflow can be converted to Kestra" + (
            f", {'all' if wait == n else wait} of them once a question is answered" if wait else "")
            + ", and nothing has to be rebuilt from scratch.")
        head = "convert"
    elif c["rebuilt"] == 0:
        first = (f"Most of this estate can be converted: {c['convert']} {_s(c['convert'], 'workflow')} convert"
                 f"{retire}, and nothing has to be rebuilt from scratch.")
        head = "convert"
    else:
        first = (f"Most of this estate can be converted: {c['convert']} {_s(c['convert'], 'workflow')} convert, "
                 f"and {c['rebuilt']} {_s(c['rebuilt'], 'is', 'are')} rebuilt from scratch because {why}{retire}.")
        head = "convert"
    if v != "rebuild":
        first += _own_conversions(brief, c["convert"])
    sp = split(rows)
    red = [x for x in rows if x["how"] == "Redesign"]
    vra_red = sum(x["count"] for x in red if x["key"][0].startswith("vra"))
    mostly = ""
    if sp["Redesign"]:
        mostly = (", mostly where vRA itself goes away" if vra_red * 2 > sp["Redesign"] else
                  ", mostly workflows rebuilt from their specs")
    say = {"vra": "on whether vRA stays in front, which is one of the questions below",
           "answer": "on a question below", "model": "on a model, which this run did not have"}
    waits: dict[str, int] = {}
    for x in rows:
        if x["how"] == "Open":
            waits[x["waits"]] = waits.get(x["waits"], 0) + x["count"]
    # (count, what it does, with "depends" or "needs" agreeing with the count)
    rest = [(n, w) for n, w in (
        (sp["Redesign"], lambda n: f"{_s(n, 'needs', 'need')} a Redesign{mostly}"),
        (sp["Keep"], lambda n: f"{_s(n, 'stays', 'stay')} in vRA"),
        (sp["Retire"], lambda n: "can be retired"),
        *[(n, (lambda n, w=w: f"{_s(n, 'depends', 'depend')} {say[w]}")) for w, n in waits.items()]) if n]
    m = sum(n for n, _ in rest)
    if sp["Direct"]:
        second = f"{sp['Direct']} of the {sp['total']} elements convert Direct."
        if len(rest) == 1:
            n, w = rest[0]
            second += f" The other {n} {w(n)}."
        elif rest:
            second += f" Of the other {m}, " + _clauses([f"{n} {w(n)}" for n, w in rest]) + "."
    elif len(rest) == 1:
        n, w = rest[0]
        second = f"All {n} elements {w(n)}."
    elif rest:
        second = f"Of the {sp['total']} elements, " + _clauses([f"{n} {w(n)}" for n, w in rest]) + "."
    else:
        second = ""
    return head, [first, second] if second else [first]


# ------------------------------------------------------------------ what we need from you

VRA_BUILD = {"mappings": "the image and size mappings", "ip-allocation": "who assigns IP addresses",
             "day2-scale": "how scaling works on Day 2", "gap": "how to build what no Kestra task creates",
             "existing-deployments": "what happens to the deployments vRA already made",
             "app": "who uses the Kestra App", "deployment-record": "where each deployment is recorded",
             "typed-inputs": "the flows' typed inputs"}
PLAN = {"cancel": "what a cancelled request should undo", "timeout": "how long a blocking step may take",
        "failure": "what a failed step does", "lifecycle": "whether the lifecycle converts together",
        "writes": "which calls write", "state-store": "where the state lives", "edition": "which Kestra edition you run",
        "q3-non-production-targets": "whether non-production systems exist",
        "q4-off-limits": "whether anything is off limits", "q9-vra-front-end": "whether vRA stays",
        "added": "a question your answers raised"}
REACH = {"tls": "whether the flows check certificates", "auth": "how the flows sign in",
         "client-route": "how the flows call each system", "reads-only": "which systems are read only"}
QUESTION = {"q1-scope": "which vRO nodes are in scope", "q2-external-callers": "who else calls these workflows",
            "q5-prior-attempts": "whether a migration was tried before", "q6-owners": "who owns each workflow",
            "q7-execution-history": "whether run history can be exported", "q8-plugin-inventory": "which vRO plugins are in use"}


def _fam(key: str) -> str:
    return key if re.match(r"q\d", key) else key.split(".", 1)[0]


def _short_defect(brief: dict) -> str:
    """The plainest suspected defect, cut to its first clause, as an example: no code in it, a sentence that
    starts with an article, about nine words."""
    best = None
    for d in brief.get("suspected_defects") or []:
        s = re.split(r"[.;:]\s|[.;:]$|, (?:which|so|because|while|and|leaving)\b", d["what"].strip())[0].rstrip(".")
        code = bool(re.search(r"[_(){}\[\]'\"`/<>]|[a-z][A-Z]", s))
        score = (code, not re.match(r"(A|An|The)\s", s), abs(len(s.split()) - 9))
        if best is None or score < best[0]:
            best = (score, s)
    if not best:
        return ""
    s = best[1]
    first = s.split()[0] if s.split() else ""
    return s[:1].lower() + s[1:] if first in ("A", "An", "The", "Every", "Each", "One", "Any") else s


def needs(assess: dict, brief: dict, st: dict) -> list[str]:
    """What we need from you: one line per open entry of answers.yaml (a group is one entry), those that hold a
    wave first. Possible defects are one line for every workflow that has them."""
    inp = brief.get("input") or {}
    shown = inp.get("shown") or []
    blocking = set(st.get("blocking") or [])
    targets = assess["targets"]
    by_kp = {_kp(t): tname(x) for t, x in targets.items()}
    phrase = {**REACH, **PLAN, **QUESTION, **VRA_BUILD}

    def holds(x: dict) -> bool:
        return bool(set(x["members"]) & blocking)

    out: list[tuple[bool, str]] = []
    adds = brief.get("package_additions") or []
    if adds:
        said = [f"{a['what']} (called by {_and(a['called_by'][:2])})" for a in adds[:4]]
        out.append((True, f"**{cap(word(len(adds)))} {_s(len(adds), 'piece')} of code the export does not have**: "
                          f"{_and(said + ([f'{len(adds) - 4} more'] if len(adds) > 4 else []))}. Add "
                          f"{_s(len(adds), 'it', 'them')} to the package and run again: \"Add to the package\" "
                          "below says how."))
    est = [x for x in shown if x["id"].startswith("estate.")]
    if est:
        topics = [EST_TOPIC.get(x["id"].split(".")[1], "a module many workflows call") for x in est]
        out.append((True, f"**{cap(word(len(est)))} {_s(len(est), 'question')} about the whole estate**, under "
                          f"`estate:` at the top of `answers.yaml`: {_and(list(dict.fromkeys(topics)))}. Each answer "
                          "settles many questions at once."))
    shown = [x for x in shown if not x["id"].startswith("estate.")]
    defects = [x for x in shown if x["id"].startswith(("defects.", "defect.", "same.defect."))]
    missing = [x for x in shown if x["id"].startswith("missing.")]
    sq = [x for x in shown if x["id"].startswith("same.") and x not in defects]
    asked = {it["key"]: (it.get("ask") or "") + " " + (it.get("question") or "") for it in Y.items()} if missing else {}
    for x in shown:
        k, ms = x["id"], x["members"]
        n = len(ms)
        if x in missing:
            # one line for all the code the export lacks, naming each piece and what calls it
            if x is not missing[0]:
                continue
            said = []
            for m in missing:
                q = asked.get(m["id"], "")
                mm = re.match(r"(.+?) is called by (.+?) but is not in the export", q.strip())
                said.append(f"{mm.group(1)} (called by {mm.group(2)})" if mm else m["id"].split(".", 1)[1])
            nm = len(missing)
            out.append((any(holds(m) for m in missing),
                        f"**{cap(word(nm))} {_s(nm, 'piece')} of code the export does not have**: {_and(said)}. "
                        "Add it to the export folder and run again, or say what it does."))
            continue
        if x in defects:
            if x is not defects[0]:
                continue
            total = sum(len(d["members"]) for d in defects)
            out.append((any(holds(d) for d in defects),
                        f"**{cap(word(total)) if total <= 10 else total} possible {_s(total, 'defect')}** that "
                        f"{_s(total, 'makes its workflow fail every time, or is', 'make their workflow fail every time, or are')} "
                        "a security problem: keep or fix each. They are on [each workflow's page](workflows/index.html)."))
            continue
        if x in sq:
            if x is not sq[0]:
                continue
            n_sq = sum(d.get("lines", 1) for d in sq)
            n_it = sum(len(d["members"]) for d in sq)
            out.append((any(holds(d) for d in sq),
                        f"**{cap(word(n_sq)) if n_sq <= 10 else n_sq} {_s(n_sq, 'question')} asked once for "
                        f"{n_it} places** where workflows ask the same thing."))
            continue
        if k == "suggested":
            says: list[str] = []
            for m in ms:
                p = phrase.get(_fam(m), _fam(m).replace("-", " "))
                if p not in says:
                    says.append(p)
            line = (f"**{cap(word(n)) if n <= 10 else n} suggested answers**, on "
                    + _clauses(says if len(says) <= 3 else says[:3] + [f"{len(says) - 3} more"])
                    + ": one yes takes them all.")
        elif k == "endpoints":
            names = [by_kp.get(m.split(".", 1)[1], m) for m in ms if m.startswith("endpoint.")]
            q3 = next((it for it in Y.items() if it["key"] in ms and it["key"].startswith("q3-")), None)
            names += Y.q3_systems(q3, ms) if q3 else []
            line = "**The non-production systems** the flows run against" + (f": {_and(names)}." if names else ".")
        elif k == "specs":
            line = f"**One look at the {word(n)} specs**: are they right?"
        elif k == "questions":
            n = x.get("lines", n)
            line = f"**{cap(word(n)) if n <= 10 else n} {_s(n, 'question')} from reading the workflows.**"
        elif k == "about-the-estate":
            ex = next((QUESTION[m] for m in ms if m in QUESTION and m != "q1-scope"), "")
            line = (f"**{cap(word(n))} more {_s(n, 'question')}** the export cannot answer"
                    + (f", such as {ex}." if ex else "."))
        elif k == "edition":
            line = "**Your Kestra edition**: is Enterprise Edition available?"
        elif k.startswith("q9-"):
            line = "**Whether vRA stays** as the front end, or Kestra replaces it."
        elif k.startswith("spec."):
            line = f"**A spec to read again**: {k.split('.', 1)[1]} changed since it was reviewed."
        else:
            f = _fam(k)
            line = f"**{cap(phrase.get(f, f.replace('-', ' ')))}.**"
        out.append((holds(x), line))
    lines = [line for h, line in out if h] + [line + " Nothing waits on these yet." for h, line in out if not h]
    kept = next((a for a in brief.get("assumptions") or [] if a["id"] == "assume.defects" and a["holds"]), None)
    if kept and kept.get("settles"):
        n = kept["settles"]
        lines.append(f"Nothing to do for **{cap(word(n)) if n <= 10 else n} possible {_s(n, 'defect')}**: "
                     f"{_s(n, 'it is', 'they are')} kept as they are today, so the converted flows can be tested "
                     "against the originals. Each is on its [workflow's page](workflows/index.html); to fix one, "
                     "write `answer: fix` under it in `answers.yaml`.")
    return lines


EST_TOPIC = {"started-by": "what starts the workflows", "config": "where settings live", "secrets": "where secrets come "
             "from", "non-production": "where flows run before production", "remote": "calls to other vRO clusters",
             "scope": "what is out of scope", "host-verification": "whether the flows check certificates"}


def estate_lines(brief: dict) -> list[str]:
    """The estate answers, for the brief: what applies estate-wide and how much it settled."""
    out = []
    for e in brief.get("estate_answers") or []:
        if e.get("answer"):
            out.append(f"{e['question']} **{e['answer']}**" + (f" (settled {e['settles']} {_s(e['settles'], 'question')})"
                                                               if e.get("settles") else ""))
    return out


def assumption_lines(brief: dict) -> list[str]:
    """Every assumption the brief makes, with how many items it settles and where to change it; then the ones an
    answer overrode."""
    out = []
    for a in brief.get("assumptions") or []:
        if a["holds"]:
            # written for answers.yaml, where "here" is the assumption and its items are listed below it
            text = (str(a["assumed"]).replace(" here.", f" under `{a['id']}`.")
                    .replace("Each is listed below as it behaves today", "Each is in Appendix C as it behaves today")
                    .replace(", listed in the brief", ""))
            out.append(text + ((f" Settles 1 item, listed under `{a['id']}`." if a.get("settles") == 1 else
                                f" Settles {a['settles']} items, each listed under `{a['id']}`.")
                               if a.get("settles") else f" (`{a['id']}`)"))
    for a in brief.get("assumptions") or []:
        if not a["holds"]:
            out.append(f"Not assumed, by your answer: {a['assumed']} Instead: **{a['instead']}**")
    return out


CHECKS_OFF = re.compile(r"certificate|host ?name|verif|trust", re.I)


def _n_names(names: list[str]) -> str:
    """Workflows (and the actions where a check is turned off, named module/action), counted and named."""
    if len(names) == 1:
        return names[0]
    kind = "workflows and actions" if any("/" in n for n in names) else "workflows"
    return f"{word(len(names))} {kind} ({_capped(sorted(names, key=lambda n: ('/' in n, n.lower())), 4, 'more')})"


def taken_lines(brief: dict) -> list[str]:
    """Each answer the brief takes from a suggestion (assume.suggested), as question and answer."""
    a = next((a for a in brief.get("assumptions") or [] if a["id"] == "assume.suggested" and a["holds"]), None)
    lines = [f"{t['question'].rstrip()} **{t['answer']}**" for t in (a or {}).get("takes") or []]
    seen: dict[str, int] = {}
    for line in lines:                   # the same question asked of each wave, with the same answer: one line
        seen[line] = seen.get(line, 0) + 1
    return [line + (f" ({word(n)} entries)" if n > 1 else "") for line, n in seen.items()]


def note_lines(brief: dict) -> list[str]:
    """What the conversion keeps as today but is worth fixing after the migration: security first (checks turned
    off, credentials exposed), then what fails on every run. Notes, not questions: each is kept, so the converted
    flows can be tested against the originals."""
    out = []
    held = next((a for a in brief.get("assumptions") or [] if a["id"] == "assume.host-checks" and a["holds"]), None)
    flagged = [d for d in brief.get("suspected_defects") or [] if d.get("flag") and d.get("in_plan")
               and str(d.get("answer") or "keep").lower().startswith("keep")]     # an answer to fix it: no note
    # every certificate or host check turned off is one note, however many workflows do it
    checks = [d for d in flagged if d["flag"] == "security" and CHECKS_OFF.search(f"{d.get('today')} {d.get('what')}")]
    names = list(dict.fromkeys(sorted([*(held or {}).get("where", []), *(d["name"] for d in checks)], key=str.lower)))
    if names:
        how = ("write `answer: check them` under `assume.host-checks`" if held else
               "write `answer: fix` under each one's possible defect")
        out.append(f"**Security: certificate or host checks turned off** in {_n_names(names)}, so a connection to the "
                   "wrong server is not noticed. The converted flows keep them off, as today. To turn them on in the "
                   f"conversion instead, {how} in `your-input/answers.yaml`.")
    for flag, head in (("security", "Security"), ("fails", "Fails on every run")):
        for d in sorted((d for d in flagged if d["flag"] == flag and d not in checks), key=lambda d: d["name"].lower()):
            today = str(d.get("today") or d.get("what") or "").rstrip(".")
            out.append(f"**{head}: {d['name']}.** {today[:1].upper()}{today[1:]}. Kept as today; to fix it in the "
                       f"conversion, write `answer: fix` under `{d['decision']}`.")
    return out


def package_lines(brief: dict) -> list[str]:
    """What to add to the package, each with what calls it and how."""
    return [f"**{a['what']}**, called by {_and(a['called_by'][:3])}"
            + (f" and {len(a['called_by']) - 3} more" if len(a["called_by"]) > 3 else "") + f". {a['how']}."
            for a in brief.get("package_additions") or []]


def retire_line(assess: dict, brief: dict) -> str | None:
    """The retire candidates corrections.yaml has not settled: workflows nothing in the export calls and no entry
    point on record starts. They are not answers.yaml entries: an owner names what starts one, or retires it."""
    tri = brief.get("triage") or {}
    names = sorted(assess["workflows"][x["uid"]]["name"] for x in brief.get("retire_candidates") or []
                   if x["uid"] in assess["workflows"] and not (tri.get(x["uid"]) or {}).get("overridden"))
    if not names:
        return None
    n = len(names)
    return (f"**{cap(word(n))} {_s(n, 'workflow')} nothing starts**, so {_s(n, 'it', 'they')} may be unused: "
            f"{_capped(names, 5, 'more')}. Often a vRO scheduled task, an ITSM or a script starts such a workflow, "
            "and the export cannot show it: name what does in the question on other entry points, or in "
            "`your-input/entry-points.yaml`. To retire one, confirm it in `your-input/corrections.yaml`.")


def _kp(t: str) -> str:
    from .gates import key_part
    return key_part(t)


def _order_targets(assess: dict, brief: dict, ts: list[str]) -> list[str]:
    """Targets, the one with the most client actions first (the order of the table)."""
    n: dict[str, int] = {}
    for k, _t in brief["action_triage"].items():
        act: dict = next((a for a in assess["actions"] if a["key"] == k), {}) or {}
        for s in act.get("library_for") or []:
            n[s] = n.get(s, 0) + 1
    return sorted(ts, key=lambda t: (-n.get(t, 0), tname(assess["targets"][t])))


# ------------------------------------------------------------------ what is in the estate

def _verbs(subs: list[dict]) -> list[str]:
    out = []
    for s in subs:
        t, cnd = (s.get("event_topic") or "").lower(), (s.get("condition") or "").upper()
        if t.startswith("compute.provision") or t.startswith("compute.removal"):
            out.append("scaled")
        elif t.startswith("deployment.request") and "DESTROY" in cnd or "delete" in t:
            out.append("deleted")
        elif t.startswith("deployment.request") and "CREATE" in cnd:
            out.append("created")
        elif "action" in t or "day2" in t:
            out.append("changed on Day 2")
    order = ["created", "scaled", "changed on Day 2", "deleted"]
    return [v for v in order if v in out]


def _short_system(row: dict, targets: dict) -> str:
    """A system's short name, from the estate's targets or the plugin map (a system only vRA reaches)."""
    if row["key"] in targets:
        return tname(targets[row["key"]])
    t = ((C.load_yaml(C.PLUGIN_MAP) or {}).get("targets") or {}).get(row["key"]) or {}
    return t.get("short_name") or row.get("system") or row["key"]


def estate_paragraph(assess: dict, brief: dict) -> str:
    wf = assess["workflows"]
    c = assess["counts"]
    model = (assess.get("entry_points") or {}).get("model") or {}
    cats, temps, subs = (model.get(k) or [] for k in ("catalog_items", "cloud_templates", "subscriptions"))
    targets = assess["targets"]
    cov = (assess.get("plugin_coverage") or {}).get("rows") or []
    created = [_short_system(r, targets) for r in cov if r.get("created_by")]
    nw, na = c["workflows"], c["actions"]
    S_ = []
    # catalog items named for what they do ("Add User to AD Group") are not things the estate provisions
    verbal = any(ACTION_NAME.match(x["name"]) for x in cats[:3])
    if cats and verbal:
        names = [x["name"] for x in cats]
        S_.append(f"Users request {word(len(cats))} Service Broker catalog {_s(len(cats), 'item')}"
                  + (f", such as {_and(names[:3])}" if len(cats) > 3 else f": {_and(names)}") + ".")
    elif cats:
        S_.append(f"This estate provisions {_and([article(phrase_name(x['name'])) for x in cats[:3]])}"
                  + (f" and {len(cats) - 3} more" if len(cats) > 3 else "") + ".")
    else:
        doms = [d["name"] for d in assess["domains"]["domains"]]
        S_.append(f"This estate's workflows fall into {word(len(doms))} {_s(len(doms), 'area')}: "
                  + _capped(doms, CAPS["domains"], "more") + ".")
    if cats or temps or subs:
        bits = []
        if cats and not verbal:
            bits.append(f"A user requests it from {word(len(cats))} Service Broker catalog {_s(len(cats), 'item')}")
        if temps:
            bits.append(("a cloud template" if len(temps) == 1 else f"{word(len(temps))} cloud templates")
                        + (f" {'creates' if len(temps) == 1 else 'create'} the machines on {_and(created)}" if created
                           else f" {_s(len(temps), 'describes', 'describe')} the deployment"))
        if subs:
            run = len({s.get("workflow_uid") for s in subs if s.get("workflow_uid")})
            vs = _verbs(subs)
            bits.append(f"{word(len(subs))} event {_s(len(subs), 'subscription runs', 'subscriptions run')} {word(run)} vRO "
                        f"{_s(run, 'workflow')}" + (f" as the deployment is {_and(vs)}" if vs else ""))
        sent = _clauses(bits)
        if sent:
            S_.append(cap(sent) + ".")
    # what the workflows touch, alternatives of one category said with "or"
    sysk = [k for k in targets if k not in ("vro-platform", "rest-host", "vra")]
    sysk = _order_targets(assess, brief, sysk)
    writes = any((targets[k].get("writes") or 0) for k in sysk)
    cat_of: dict[str, list] = {}
    for k in sysk:
        cat_of.setdefault(targets[k].get("category") or "", []).append(k)
    phr = []
    for cat, ks in cat_of.items():
        if cat and len(ks) > 1:
            phr.append(_and([article(tname(targets[k])) for k in ks[:-1]] + [article(tname(targets[ks[-1]])) + " " + cat],
                            "or"))
        else:
            phr += [tname(targets[k]) for k in ks]
    sub = "The workflows" if nw > 1 else "The workflow"
    touch = (f"{sub} {'configure' if writes else 'read from'} {_capped(phr, CAPS['systems'], 'other systems')}"
             if phr else f"{sub} call no external system")
    touch += f" through {na} {_s(na, 'action')}" if na else ""
    state = sorted({p for w in wf.values() for p in (w.get("state") or {}).get("writes") or []})
    if state:
        per = "each deployment's state" if subs and any(w.get("lifecycle_group") for w in wf.values()) else "their state"
        touch += f", and keep {per} in {article('vRO configuration element') if len(state) == 1 else f'{word(len(state))} vRO configuration elements'}"
    S_.append(touch + ".")
    return " ".join(S_)


# ------------------------------------------------------------------ the plan

def wave_title(w: dict, assess: dict) -> str:
    """'Wave 1, the four workflows'; 'Wave 2, replacing vRA'."""
    if w.get("kind") == "vra-replacement":
        return f"Wave {w['wave']}, replacing vRA"
    n = len(w["workflows"])
    allw = n == len(assess["workflows"])
    name = (f"the {word(n)} workflows" if allw and n > 1 else "the workflow" if allw else
            f"{word(n)} {_s(n, 'workflow')}")
    targets = assess["targets"]
    wr = [tname(targets[t]) for t in w.get("writes_to") or [] if t in targets]
    if not allw and wr:
        name += f" that write to {_and(wr[:2])}" + (" and more" if len(wr) > 2 else "")
    return f"Wave {w['wave']}, {name}"


def _entries_holding(w: dict, brief: dict) -> int:
    """How many open entries of answers.yaml hold this wave."""
    fields = {(e.get("closes") or {}).get("field") for e in w["entry_criteria"] if e.get("status") != "done"}
    return sum(1 for x in (brief.get("input") or {}).get("shown") or [] if set(x["members"]) & fields)


def _wave_bullet(w: dict, assess: dict, brief: dict, sn: dict) -> str:
    crit = [e for e in w["entry_criteria"] if e.get("status") != "done"
            and (e.get("closes") or {}).get("kind") not in ("approval", "gate")]
    fams = []
    for e in crit:
        f = _fam(str((e.get("closes") or {}).get("field") or ""))
        p = {"defect": "the defects", "endpoint": "the endpoints", "spec": "the spec reviews",
             "question": "the spec questions"}.get(f) or ("building machines" if f in VRA_BUILD else
                                                          "the plan" if f in PLAN else
                                                          "reaching your systems" if f in REACH else
                                                          "the open questions")
        if p and p not in fams:
            fams.append(p)
    targets = assess["targets"]
    after = [f"Wave {d}" for d in w.get("depends_on") or []]
    if w.get("kind") == "vra-replacement":
        sc = w.get("scope") or []
        has = lambda k: any(s["kind"] == k for s in sc)
        nt = len(w.get("cloud_templates") or [])
        bits = (["the catalog item becomes a Kestra App"] if has("App") else []) + \
               ([("the cloud template" if nt == 1 else "the cloud templates") + " a provisioning flow"] if has("flow") else []) + \
               (["the subscriptions its steps"] if has("lifecycle step") else [])
        s = f"**{wave_title(w, assess)}.** " + (cap(_clauses(bits)) + "." if bits else "")
        start = "It starts" + (f" after {_and(after)}" if after else "")
        on = [f for f in fams if f in ("building machines", "the plan", "reaching your systems")]
        other = [f for f in fams if f not in on]
        if on or other:
            start += ((", once " if after else " once ") + _and((["the answers on " + _and(on)] if on else []) + other)
                      + " are in")
        return s + " " + start + "."
    us = w["workflows"]
    n = len(us)
    parts = [f"**{wave_title(w, assess)}.**"]
    groups = [g for g in assess.get("lifecycle_groups") or [] if set(g["members"]) & set(us)]
    if groups and n > 1 and any(g.get("shared_state") for g in groups):
        parts.append("They share state, so they convert together.")
    if w.get("pilot"):
        # names in full: cutting the words they share left "LB - Create", and a lone name its first word
        first, then = sn.get(w["pilot"], w["pilot"]), [sn.get(u, u) for u in w.get("pilot_with") or []]
        parts.append(f"{first} is the first of a kind" + (f", then {_and(then)}." if then else "."))
    wr = _order_targets(assess, brief, [t for t in w.get("writes_to") or [] if t in targets])
    wn = _and([tname(targets[t]) for t in wr])
    k = _entries_holding(w, brief)
    if fams:
        parts.append((f"It writes to {wn}, and starts" if wn else "It starts")
                 + (f" after {_and(after)} and" if after else "")
                 + (f" once the {word(k)} {_s(k, 'item')} above {_s(k, 'is', 'are')} answered." if k else
                    " once its entry criteria are met."))
    elif after:
        parts.append(f"It starts after {_and(after)}.")
    else:
        parts.append("Its entry criteria are met." if not wn else f"The wave writes to {wn}, and its entry criteria are met.")
    return " ".join(parts)


# ------------------------------------------------------------------ the document

def _status(brief: dict, st: dict, num: dict) -> dict:
    sign = ((brief.get("input") or {}).get("sign") or brief.get("approval") or {})
    pk = len(st.get("package") or [])
    add = f" and add {pk} missing {_s(pk, 'piece')} of code to the package" if pk else ""
    if st["blocking"] and st.get("holding"):
        k = st["holding"]
        head = (f"To start {_and(st['blocks'])}: answer {k} {_s(k, 'question')}{add}, then run "
                f"`{C.run_command()}` again.")
        body = ("The brief below is complete: where a question has a suggested answer, it takes it, and lists it "
                f"under Assumptions. {'These' if k != 1 else 'This one'} only the owner can answer, in "
                "`your-input/answers.yaml`" + (", and the pieces to add are listed below" if pk else "") + ".")
        cls = "wait"
    elif pk:
        head = (f"Next step: add {pk} missing {_s(pk, 'piece')} of code to the package, then run "
                f"`{C.run_command()}` again.")
        body = "Each one is listed below with what calls it and how to add it."
        cls = "wait"
    elif not brief["plan"]["waves"]:
        nb = len(brief["plan"]["blocked"]) + len(brief["plan"].get("not_waved") or [])
        head = "Next step: no wave can start yet."
        body = ("Nothing in the estate can convert until the work the wave page lists is done"
                + (f": {nb} {_s(nb, 'workflow waits', 'workflows wait')} on it." if nb else "."))
        cls = "wait"
    elif st["blocks"]:
        head = "Next step: nothing from you yet."
        body = f"{_and(st['blocks'])} cannot start until the earlier waves have run. The wave page says what each waits on."
        cls = "todo"
    elif not st["signed"]:
        waves = brief["plan"]["waves"]
        head = "Ready to sign."
        body = (("Every question is answered. " if not st["open"] else "") + "Signing covers "
                + ("the full plan, " + _and([f"Wave {w['wave']}" for w in waves]) if len(waves) > 1
                   else "Wave 1") + ": the waves, their first of a kind and their entry criteria as "
                "they stand. Sign with `signed_for:` in `your-input/answers.yaml` (`wave 1` or `full plan`)."
                + (f" Once signed, {_and(st['start'])} can start." if st.get("start") else "")
                + (f" {st['order']}" if st.get("order") else ""))
        cls = "todo"
    else:
        scope = "the full plan" if sign.get("scope") == "full" else "Wave 1"
        head = f"Ready: {_and(st['start'])} can start." if st.get("start") else "Signed, ready to convert."
        body = f"Signed for {scope}." + (f" {st['order']}" if st.get("order") else "")
        cls = "done"
    return {"head": head, "body": body, "cls": cls}


def stand_in(brief: dict) -> str:
    inp = brief.get("input") or {}
    return "Some answers and the signature here are a stand-in's, not the customer's." if inp.get("stand_in") else ""


def no_model(brief: dict) -> bool:
    """The run had no model for most workflows: the verdicts, the plan and the defects wait on one."""
    return (brief.get("recommendation") or {}).get("verdict") == "needs a model"


def _sources(assess: dict) -> tuple[str, int]:
    ins = (assess.get("inputs") or {}).get("export_inputs") or {}
    n_pkg = sum(1 for p in ins.values() if str(p).endswith(".package")) or len(ins)
    n_vra = len((assess.get("entry_points") or {}).get("exports") or [])
    pkg = "package" if all(str(p).endswith(".package") for p in ins.values()) else "export"
    return f"{n_pkg} vRO {_s(n_pkg, pkg)}" + (f" and {n_vra} vRA {_s(n_vra, 'export')}" if n_vra else ""), n_vra


def _started_by(w: dict, names: dict) -> str:
    """What starts one workflow, in plain words, from the vRA export and the call graph."""
    eps = w.get("entry_points") or []
    bits = []
    for e in eps:
        if e.get("kind") == "subscription":
            bits.append(f"the event subscription “{e['name']}” on `{e.get('event_topic')}`"
                        + (" (it waits for the workflow)" if e.get("blocking") else ""))
        elif e.get("kind") in ("catalog item", "catalog_item"):
            bits.append(f"the catalog item “{e['name']}”")
        else:
            bits.append(f"{(e.get('kind') or 'an entry point').replace('_', ' ')} “{e.get('name')}”")
    callers = [names.get(u, u) for u in w.get("called_by") or []]
    if callers:
        bits.append("called by " + _and(sorted(callers)))
    return _and(bits) if bits else "nothing in the export starts it or calls it"


def _reach_words(t: dict) -> str:
    r = t.get("reach")
    if r == "plugin":
        grp = (t.get("kestra_group") or "").rsplit(".", 1)[-1]
        return (f"Kestra's {grp.upper() if len(grp) <= 3 else grp.title()} plugin" if grp else "a Kestra plugin") \
            + (" (Enterprise)" if t.get("enterprise_only") else "")
    return {"http": "HTTP calls on its REST API (no plugin)", "replace": "nothing to call: Kestra keeps this itself",
            "none": "no route found yet"}.get(r or "none", r or "")


def _with_a_model(est: str) -> str:
    """How to run it again with a model. In the container, only what works there: an API key or an endpoint."""
    from kestra_migration_assistant import config as K
    if K.in_container():
        args = C.run_command().removeprefix("kestra-migrate ")
        return (f"**Run it again with a model:** the same `docker run`, ending in `{args}`, with an Anthropic API key "
                "(`-e ANTHROPIC_API_KEY`) or an OpenAI-compatible endpoint (`-e KESTRA_MIGRATE_PROVIDER=openai-compatible "
                "-e KESTRA_MIGRATE_BASE_URL -e KESTRA_MIGRATE_MODEL -e OPENAI_API_KEY`). What this run found is kept.")
    return (f"**Run it again with a model:** `{C.run_command()}`, from the folder that holds {est}. "
            "It uses your Claude Code login if `claude` is installed and logged in, or an Anthropic API key in "
            "ANTHROPIC_API_KEY, and `kestra-migrate init` sets up another model. What this run found is kept.")


def document_no_model(assess: dict, brief: dict) -> list[tuple]:
    """The front page of a run with no model: everything the code found, in full, and one plain line where a
    verdict needs a model. No recommendation and no plan: both follow from verdicts this run could not make."""
    st = S.status_of(brief)
    est = C.ESTATE or brief.get("estate") or "vRO"
    wf = assess["workflows"]
    names = {u: w["name"] for u, w in wf.items()}
    c = assess["counts"]
    src, n_vra = _sources(assess)
    targets = assess["targets"]
    actions = {a["key"]: a for a in assess["actions"]}
    atri = brief.get("action_triage") or {}
    tri = brief["triage"]
    n_model = sum(1 for t in tri.values() if t["outcome"] == "Needs a model")
    B: list[tuple[str, Any]] = [
        ("h1", f"Migration brief: {est}"),
        ("p", f"What the code found in the {est} export, read on this machine from {src} on "
              f"{_date(brief['generated'])}. This run used no model, so everything below comes from the code itself. "
              f"It is Stage 1 of the [vRA migration guide]({GUIDE}), without the recommendations."),
        ("status", {"head": "Next step: run it again with a model.",
                    "body": f"What each of the {word(n_model)} {_s(n_model, 'workflow')} does, its possible defects, "
                            "and whether to convert or rebuild it need a model. Everything else is below.",
                    "cls": "todo", "stand_in": stand_in(brief)})]

    B += [("h2", "What's in this estate"), ("p", estate_paragraph(assess, brief)),
          ("p", f"{cap(word(c['workflows']))} {_s(c['workflows'], 'workflow')} and {word(c['actions'])} "
                f"{_s(c['actions'], 'action')}, with {c['script_lines_workflows'] + c['script_lines_actions']} lines "
                f"of script; {word(c['config_elements'])} configuration {_s(c['config_elements'], 'element')} and "
                f"{word(c['resource_elements'])} resource {_s(c['resource_elements'], 'element')}.")]

    order = sorted(wf, key=lambda u: wf[u]["name"].lower())
    started = [f"**{wf[u]['name']}**: {_started_by(wf[u], names)}." for u in order]
    B += [("h2", "What starts each workflow"), ("ul", started[:CAPS["rows"]]
           + ([f"{len(started) - CAPS['rows']} more on the workflows page."] if len(started) > CAPS["rows"] else []))]
    model = (assess.get("entry_points") or {}).get("model") or {}
    if n_vra:
        cats, subs = model.get("catalog_items") or [], model.get("subscriptions") or []
        B.append(("p", f"The vRA export has {word(len(cats))} catalog {_s(len(cats), 'item')} and {word(len(subs))} "
                       f"event {_s(len(subs), 'subscription')}. [The vRA flow](vra.html) draws how a request reaches "
                       "each workflow."))
    else:
        B.append(("p", "No vRA export was read, so what starts each workflow comes from the call graph alone. "
                       "INPUTS.md says how to add the vRA export."))

    called = {k for u in wf for a in wf[u]["called_actions"] if a.get("in_export") for k in a.get("keys") or []}
    shared = sorted(k for k in called if len({u for u in wf for a in wf[u]["called_actions"]
                                               if k in (a.get("keys") or [])}) > 1)
    unused = sorted(k for k, a in actions.items() if not a["called_by_workflows"] and not a["called_by_actions"]
                    and not a.get("referenced_by_forms"))
    nested = sum(len(w["nested"]) for w in wf.values())
    unexp = len(assess.get("unexported_calls") or [])
    calls = []
    for u in order:
        w = wf[u]
        sysn = sorted({tname(targets[t]) for t in w["targets"] if t in targets and t not in ("vro-platform",)})
        k = len({x for a in w["called_actions"] for x in (a.get("keys") or [a.get("action")])})
        calls.append(f"**{w['name']}** calls {word(k)} {_s(k, 'action')}"
                     + (f" and {word(len(w['nested']))} {_s(len(w['nested']), 'workflow')}" if w["nested"] else "")
                     + (f", and reaches {_and(sysn)}" if sysn else ", and reaches no external system") + ".")
    B += [("h2", "What calls what"),
          ("p", f"The workflows call {word(len(called))} of the {word(c['actions'])} actions directly"
                + (f"; {word(len(shared))} of them are shared by more than one workflow" if shared else "")
                + (f"; {word(len(unused))} {_s(len(unused), 'action is', 'actions are')} called by nothing" if unused
                   else "")
                + (f". Workflows call other workflows {word(nested)} {_s(nested, 'time')}" if nested else "")
                + (f", and {word(unexp)} {_s(unexp, 'call goes', 'calls go')} to something not in the export"
                   if unexp else "") + ". [The call graph](call-graph.html) shows every call."),
          ("ul", calls[:CAPS["rows"]] + ([f"{len(calls) - CAPS['rows']} more on the call graph."]
                                         if len(calls) > CAPS["rows"] else []))]

    ext = [k for k in _order_targets(assess, brief, list(targets)) if k != "vro-platform"]
    rows = [[tname(targets[k]), _reach_words(targets[k]),
             f"{len(targets[k]['operations'])} ({targets[k]['reads']} read, {targets[k]['writes']} write)",
             str(len(targets[k]["workflows"]))] for k in ext]
    snap = ((assess.get("plugin_coverage") or {}).get("snapshot") or {})
    B.append(("h2", "Systems and the Kestra plugins that cover them"))
    if rows:
        B.append(("table", (["System", "How Kestra reaches it", "Operations", "Workflows"], rows)))
    B.append(("p", (f"{cap(word(len(ext)))} external {_s(len(ext), 'system')}. " if ext else
                    "The workflows reach no external system. ")
              + ("vRO's own state, settings and templates move to Kestra's KV store, variables, secrets and "
                 "namespace files. " if "vro-platform" in targets else "")
              + (f"Checked against the Kestra plugin catalog of {_date(snap['taken'])}, bundled with this tool. "
                 if snap.get("taken") else "")
              + "[Plugin coverage](plugins.html) lists every operation."))

    lib: dict[str, list[str]] = {}
    for k, t in atri.items():
        if t["outcome"] == "Plugin-covered library":
            a = actions.get(k) or {}
            # a helper of a client goes with the client: the system of the actions that call it
            sysk = a.get("library_for") or sorted({x for c_ in a.get("called_by_actions") or []
                                                    for x in (actions.get(c_) or {}).get("library_for") or []})
            for s_ in sysk[:1] or ["other"]:
                lib.setdefault(s_, []).append(a.get("name") or k)
    B.append(("h2", "Libraries a plugin replaces"))
    if lib:
        B.append(("ul", [f"{cap(word(len(v)))} {tname(targets[k]) + ' client' if k in targets else 'client'} "
                         f"{_s(len(v), 'action')} ({_capped(sorted(v), 4, 'more')}): "
                         f"{_reach_words(targets[k]) if k in targets else 'a Kestra plugin'} does this work, so "
                         f"{'it is' if len(v) == 1 else 'they are'} not converted." for k, v in sorted(lib.items())]))
    else:
        B.append(("p", "None: no action here is a client for a system a Kestra plugin covers. Client code for a system "
                       "with no plugin is converted to HTTP calls, as the table above says."))

    dup = [u for u in order if tri[u]["outcome"] == "Near-duplicate"]
    wrap = [u for u in order if tri[u]["outcome"] == "Wrapper"]
    wrap_a = [k for k, t in atri.items() if t["outcome"] == "Wrapper"]
    same = [a for a in actions.values() if a.get("same_code_as")]
    shapes = [cl for cl in assess["clusters"] if cl["size"] > 1]
    B.append(("h2", "Near-duplicates and wrappers"))
    items = []
    if dup:
        items.append(f"{cap(word(len(dup)))} near-{_s(len(dup), 'duplicate')}: "
                     + _and([f"{wf[u]['name']} (of {wf.get(tri[u]['evidence'].get('first_of_a_kind'), {}).get('name', '?')})"
                             for u in dup[:CAPS['rows']]]) + ". Each is made from its first of a kind, not converted.")
    for cl in shapes[:CAPS["domains"]]:
        items.append(f"{cap(word(cl['size']))} workflows share one shape: "
                     + _capped(sorted(wf[u]["name"] for u in cl["members"] if u in wf), 4, "more")
                     + f". Converting {wf[cl['first_of_a_kind']]['name']} first shows how to do the rest.")
    if wrap or wrap_a:
        items.append(_and(([f"{word(len(wrap))} wrapper {_s(len(wrap), 'workflow')} ({_capped([wf[u]['name'] for u in wrap], 4, 'more')})"]
                           if wrap else [])
                          + ([f"{word(len(wrap_a))} wrapper {_s(len(wrap_a), 'action')}"] if wrap_a else []))
                     .capitalize() + ": each only calls something else, and its callers call that directly.")
    if same:
        items.append(f"{cap(word(len(same)))} {_s(len(same), 'action has', 'actions have')} the same code as another.")
    if items:
        B.append(("ul", items))
    else:
        B.append(("p", "None found: every workflow and action has logic of its own, and none only calls another."))

    B += [("h2", "What a model adds"),
          ("p", "A model reads each workflow's code and writes down what it does. From that come the three things this "
                "run leaves open: what each workflow does, its possible defects, and whether to convert or rebuild it. "
                "The recommendation and the migration plan in waves follow from them."),
          ("p", _with_a_model(est))]

    nd = needs(assess, brief, st)
    if nd:
        B += [("h2", "What we need from you"), ("ul", nd),
              ("p", "Each one is a question in `your-input/answers.yaml`. Where the code points to an answer, it's "
                    "under `suggested`: write yes to take it.")]
    rl = retire_line(assess, brief)
    if rl:
        B += ([] if nd else [("h2", "What we need from you")]) + [("p", rl)]
    B += _estate_blocks(brief)

    B.append(("h2", "Where to check"))
    where = ([("vra.html", "The vRA flow", "how a request reaches each workflow")] if n_vra else []) + [
        ("call-graph.html", "The call graph", "which workflow calls which action"),
        ("plugins.html", "Plugin coverage", "every system, its operations and the Kestra plugin for each"),
        ("workflows/index.html", "Workflows", "each workflow's flowchart and what it calls"),
        ("reports/index.html", "All reports", "every other page, the explorer among them")]
    B.append(("ul", [f"[{t}]({h}): {d}." for h, t, d in where]))
    return B


def document(assess: dict, brief: dict) -> list[tuple]:
    """The front page as blocks: ("h1"|"h2"|"p"|"ul"|"table"|"kpis"|"status"|"sub", content)."""
    if no_model(brief):
        return document_no_model(assess, brief)
    st = S.status_of(brief)
    num = (brief.get("translation") or {}).get("number") or {}
    est = C.ESTATE or brief.get("estate") or "vRO"
    sn = short_names(assess)
    ins = (assess.get("inputs") or {}).get("export_inputs") or {}
    n_pkg = sum(1 for p in ins.values() if str(p).endswith(".package")) or len(ins)
    n_vra = len((assess.get("entry_points") or {}).get("exports") or [])
    pkg = "package" if all(str(p).endswith(".package") for p in ins.values()) else "export"
    src = f"{n_pkg} vRO {_s(n_pkg, pkg)}" + (f" and {n_vra} vRA {_s(n_vra, 'export')}" if n_vra else "")
    B: list[tuple[str, Any]] = [("h1", f"Migration brief: {est}"),
         ("p", f"Stage 1 of the [vRA migration guide]({GUIDE}), done for you from {src}: what the export holds, what "
               "we recommend, and what we need from you before anything is converted. It covers what is in the "
               "export.")]
    B.append(("status", dict(_status(brief, st, num), stand_in=stand_in(brief))))
    stale = brief.get("stale_specs") or {}
    if stale:
        names = [assess["workflows"][u]["name"] if u in assess["workflows"] else u for u in sorted(stale)]
        B.append(("sub", f"{len(stale)} {_s(len(stale), 'spec describes', 'specs describe')} other code or another "
                         f"prompt than today's ({_capped(names, 3, 'more')}). Running `{C.run_command()}` with a model writes "
                         f"{_s(len(stale), 'it', 'them')} again, and that is billed."))

    B += [("h2", "What's in this estate"), ("p", estate_paragraph(assess, brief))]

    rows = merged_rows(assess, brief)
    head, sents = recommendation(assess, brief, rows)
    sp = split(rows)
    B += [("h2", f"The recommendation: {head}"), ("p", " ".join(sents)),
          ("kpis", [(f"{sp['path']} of {sp['total']}", "have a path to Kestra"), _second_kpi(brief, st, num, sp)])]
    shown = rows[:CAPS["rows"]]
    tbl = [[x["label"], x["kestra"], x["how_shown"], str(x["count"])] for x in shown]
    if len(rows) > len(shown):
        rest = rows[len(shown):]
        tbl.append([f"{len(rest)} more kinds", "on the translation page", "", str(sum(x["count"] for x in rest))])
    B.append(("table", (["In vRA and vRO" if n_vra else "In vRO", "In Kestra", "How", "Count"], tbl)))
    B.append(("p", "**Direct** converts one for one; **Redesign** does the same job another way "
                   f"([section 3.2.3]({GUIDE_323}) of the guide). The counts come from reading the export, so they "
                   "show a path, not a proven conversion. [Every element, and why](translation.html)."))

    nd = needs(assess, brief, st)
    B.append(("h2", "What we need from you"))
    if nd:
        B.append(("ul", nd))
        B.append(("p", "Each is an entry in `your-input/answers.yaml`, except the code to add to the package."))
    else:
        B.append(("p", "Nothing is open. " + ("Sign in `your-input/answers.yaml` when the plan reads right."
                                              if not st["signed"] else "The plan is signed.")))
    rl = retire_line(assess, brief)
    if rl:
        B.append(("p", rl))
    nl = note_lines(brief)
    if nl:
        B += [("h2", "Notes to fix after the migration"),
              ("p", "Each converted flow does what its workflow does today, so the two can be tested against each "
                    "other. These are kept as they are, and worth fixing once the migration is done."), ("ul", nl)]
    B += _estate_blocks(brief)
    warn = (brief.get("input") or {}).get("warnings")
    if warn:
        B.append(("sub", "Problems in your-input/answers.yaml: " + "; ".join(warn)))

    waves = brief["plan"]["waves"]
    B.append(("h2", "The plan"))
    if waves:
        B.append(("ul", [_wave_bullet(w, assess, brief, sn) for w in waves[:CAPS["waves"]]]
                  + ([f"{len(waves) - CAPS['waves']} more waves on the wave page."] if len(waves) > CAPS["waves"] else [])))
        nb = len(brief["plan"]["blocked"])
        B.append(("p", (f"{cap(word(nb))} {_s(nb, 'workflow')} {_s(nb, 'waits', 'wait')} outside the waves on work "
                        "that cannot convert yet. " if nb else "") + "[Entry criteria for each wave](waves.html)."))
    else:
        B.append(("p", "No wave yet: nothing can convert until the open items are answered. "
                       "[What each workflow waits on](waves.html)."))

    B += [("h2", "Next steps"), ("ul", next_steps())]
    B.append(("sub", "Every other page, the explorer among them, is under [All reports](reports/index.html)."))
    return B


def next_steps() -> list[str]:
    """What to do with the brief, besides answering: check which workflows still run."""
    return ["**Check each workflow's run history** in vRO to find those that no longer run, and retire them in "
            "`your-input/corrections.yaml` (`outcome: Retire`) instead of converting them."]


# ------------------------------------------------------------------ rendering

def _inline(md: str) -> str:
    from .mdhtml import inline
    return inline(md)


def page(assess: dict, brief: dict, pre: dict) -> str:
    parts = []
    for kind, x in document(assess, brief):
        if kind == "h1":
            parts.append(f"<h1>{C.esc(x)}</h1>")
        elif kind == "h2":
            parts.append(f"<h2>{C.esc(x)}</h2>")
        elif kind == "p":
            parts.append(f"<p>{_inline(x)}</p>")
        elif kind == "sub":
            parts.append(f'<p class="sub">{_inline(x)}</p>')
        elif kind == "ul":
            parts.append("<ul>" + "".join(f"<li>{_inline(i)}</li>" for i in x) + "</ul>")
        elif kind == "status":
            parts.append(f'<div class="panel statusbox"><p class="headline"><span class="status {x["cls"]}">'
                         f'{_inline(x["head"])}</span></p><p>{_inline(x["body"])}</p>'
                         + (f'<p class="sub">{_inline(x["stand_in"])}</p>' if x.get("stand_in") else "") + "</div>")
        elif kind == "kpis":
            parts.append('<div class="grid kpis">' + "".join(
                f'<div class="panel kpi"><div class="v">{C.esc(v)}</div><div class="l">{C.esc(l)}</div></div>'
                for v, l in x) + "</div>")
        elif kind == "table":
            head, rows = x
            parts.append('<div class="scroll"><table><tr>' + "".join(f"<th>{C.esc(h)}</th>" for h in head) + "</tr>"
                         + "".join("<tr>" + "".join(
                             (f'<td><span class="status {_how_class(c)}">{C.esc(c)}</span></td>'
                              if j == 2 and c and head[2] == "How"
                              else f'<td class="num">{C.esc(c)}</td>' if j == 3 else f"<td>{_inline(c)}</td>")
                             for j, c in enumerate(r)) + "</tr>" for r in rows) + "</table></div>")
    parts.append('<p class="sub"><a href="BRIEF.md">Download the brief</a> (Markdown).</p>')
    return TH.page(f"Migration brief: {C.ESTATE}", "\n".join(parts), active="index.html")


def _how_class(shown: str) -> str:
    for how, cls in HOW_CLASS.items():
        if shown in (how, HOW_SHOWN.get(how)):
            return cls
    return "wait" if shown in WAITS.values() else "todo"


def _estate_blocks(brief: dict) -> list[tuple]:
    """The estate answers and assumptions, and what to add to the package, each under its own heading."""
    B: list[tuple] = []
    pl = package_lines(brief)
    if pl:
        B += [("h2", "Add to the package"),
              ("p", "These are called by the workflows but are not in the export. No question is asked about them: "
                    "add each one and run again, and its calls are read like the rest."), ("ul", pl)]
    al = assumption_lines(brief)
    if al:
        B += [("h2", "Assumptions"),
              ("p", AGAIN),
              ("p", "Where a question has an answer that keeps today's behavior, or one the code suggests, the brief "
                    "takes it instead of asking. Each is under `estate:` in `your-input/answers.yaml` with every item "
                    "it settles: to change one, write your answer under it and run the assessment again."),
              ("ul", al)]
        tl = taken_lines(brief)
        if tl:
            B += [("p", "The answers taken from a suggestion (`assume.suggested`):"), ("ul", tl)]
    el = estate_lines(brief)
    if el:
        B += [("h2", "Your answers about the estate"),
              ("p", "Under `estate:` in `your-input/answers.yaml`. Change an answer there."), ("ul", el)]
    return B


def _second_kpi(brief: dict, st: dict, num: dict, sp: dict) -> tuple[str, str]:
    """What is ready, once answers have made something ready; until then, what the open answers unlock."""
    if not num.get("elements_ready") and st["blocking"] and st["blocks"]:
        k = st.get("holding") or len(st["blocking"])
        return f"Answer {k} {_s(k, 'item')}", f"to start {_and(st['blocks'])}"
    return f"{num.get('elements_ready', 0)} of {sp['total']}", "are ready now"


def markdown(assess: dict, brief: dict) -> list[str]:
    """The same document as Markdown (BRIEF.md's body)."""
    L: list[str] = []
    for kind, x in document(assess, brief):
        if kind == "h1":
            L += [f"# {x}", ""]
        elif kind == "h2":
            L += [f"## {x}", ""]
        elif kind in ("p", "sub"):
            L += [x, ""]
        elif kind == "ul":
            L += [f"- {i}" for i in x] + [""]
        elif kind == "status":
            L += [f"> **{x['head']}** {x['body']}" + (f" {x['stand_in']}" if x.get("stand_in") else ""), ""]
        elif kind == "kpis":
            L += [" · ".join(f"**{v}** {l}" for v, l in x), ""]
        elif kind == "table":
            head, rows = x
            L += [C.md_table(head, rows), ""]
    return L


def recommendation_text(assess: dict, brief: dict) -> str:
    """The recommendation in two sentences (the CLI and brief.json carry it)."""
    rows = merged_rows(assess, brief)
    head, sents = recommendation(assess, brief, rows)
    return f"{cap(head)}. " + " ".join(sents)


# ------------------------------------------------------------------ detail pages

def translation_page(assess: dict, brief: dict) -> str:
    rows = merged_rows(assess, brief)
    sp = split(rows)
    num = (brief.get("translation") or {}).get("number") or {}
    parts = ["<h1>Translation</h1>",
             f"<p>Every element of the estate, in the row of the brief's table that holds it, with what it becomes and "
             f"why: {sp['total']} elements"
             + (f", {num['elements_ready']} ready now" if num.get("elements_ready") else "") + ".</p>"]
    for x in rows:
        ev = x.get("evidence")
        evl = (f' Evidence: <a href="{C.esc(ev["href"])}">{C.esc(ev["label"])}</a>.' if ev else "")
        items = "".join(
            f"<tr><td>{_link(i)}</td><td>{C.esc(r['kind'])}</td>"
            f"<td>{'yes' if i.get('ready') else 'waits on ' + C.esc(', '.join(i.get('waits_on') or ['its path']))}</td>"
            f"<td>{C.esc(i.get('why') or '')}</td></tr>" for r in x["rows"] for i in r["items"])
        ids = "".join(f'<span id="{C.esc(r["id"])}"></span>' for r in x["rows"])
        parts.append(f'<h2 id="{C.esc(x["id"])}">{ids}{C.esc(x["label"])} <span class="status '
                     f'{HOW_CLASS.get(x["how"], "todo")}">{C.esc(x["how_shown"])}</span></h2>'
                     f'<p>In Kestra: {C.esc(x["kestra"])}. {x["count"]} {_s(x["count"], "element")}'
                     + (f", {x['lines']} lines of script" if x["lines"] else "") + f".{evl}</p>"
                     '<div class="scroll"><table><tr><th>Element</th><th>Kind</th><th>Ready now</th>'
                     f"<th>Why it is here</th></tr>{items}</table></div>")
    return TH.page("Translation", "\n".join(parts), active="translation.html")


def _link(i: dict) -> str:
    return f'<a href="{C.esc(i["link"])}">{C.esc(i["name"])}</a>' if i.get("link") else C.esc(i["name"])


def workflows_index(assess: dict, brief: dict) -> str:
    wf, tri = assess["workflows"], brief["triage"]
    wave_of = {u: w["wave"] for w in brief["plan"]["waves"] for u in w.get("workflows") or []}
    blocked = {b["uid"] for b in brief["plan"]["blocked"]}
    ndef: dict[Any, int] = {}
    for d in brief.get("suspected_defects") or []:
        ndef[d.get("short")] = ndef.get(d.get("short"), 0) + 1
    rows = "".join(
        f'<tr><td><a href="{C.esc(w.get("short") or u)}.html">{C.esc(w["name"])}</a></td>'
        f'<td>{TH.outcome_badge(tri[u]["outcome"])}</td>'
        f'<td>{("Wave " + str(wave_of[u])) if u in wave_of else ("blocked" if u in blocked else "not in a wave")}</td>'
        f'<td class="num">{w["script_lines"]}</td><td class="num">{ndef.get(w.get("short"), 0)}</td>'
        f'<td>{C.esc(w.get("spec_confidence") or "needs a model")}</td></tr>'
        for u, w in sorted(wf.items(), key=lambda kv: kv[1]["name"]))
    body = (f"<h1>Workflows ({len(wf)})</h1><p>Each workflow's spec, flowchart and possible defects, one page "
            "each.</p>"
            '<div class="scroll"><table><tr><th>Workflow</th><th>Outcome</th><th>Wave</th>'
            f"<th>Lines</th><th>Possible defects</th><th>Spec</th></tr>{rows}</table></div>")
    return TH.page("Workflows", body, depth=1, active="workflows/index.html")


def text_of(page_html: str) -> str:
    """The words a reader sees (tests use it to check the page's length)."""
    body = page_html.split("<main>", 1)[-1].split("</main>", 1)[0]
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))).strip()
