"""
Stage 2, Brief: triage, waves, an estate recommendation, and the approval gate.

Reads Assess (re-running it if a correction is newer), the estate's specs in data/2-understand,
your-input/answers.yaml and your-input/corrections.yaml. Registers everything it asks a person in
answers.yaml (your_input.py), and writes brief.json, triage.csv, open-questions.md and walkthroughs.json in
data/3-brief, and report/BRIEF.md, the brief as a downloadable copy. The front page (index.html) is drawn
from brief.json by the visuals.

The signature (`signed_for:` in answers.yaml) is the gate a later convert stage reads.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict

from . import assess as A
from . import common as C
from . import gates as G, preflight, reviews as RV, triage as TR, triggers as TG, waves as W
from .. import safety

APPROVAL_RE = re.compile(r"<!-- approval:start -->(.*?)<!-- approval:end -->", re.S)
APPROVAL_BLANK = """```
Approved by: ________________  Date: __________
Approval covers: [ ] Wave 1 only   [ ] Full plan
```"""


def parse_approval(text: str) -> dict:
    """The approval block of a BRIEF.md written before 2026-10-01 (your_input.migrate carries it over)."""
    m = APPROVAL_RE.search(text or "")
    block = m.group(1) if m else ""
    who = re.search(r"Approved by:\s*([^\n]*?)\s+Date:", block)
    date = re.search(r"Date:\s*([^\n]*)", block)
    name = (who.group(1) if who else "").strip().strip("_").strip()
    d = (date.group(1) if date else "").strip().strip("_").strip()
    wave1 = bool(re.search(r"\[[xX]\]\s*Wave 1 only", block))
    full = bool(re.search(r"\[[xX]\]\s*Full plan", block))
    scope = "full" if full else ("wave-1" if wave1 else None)
    return {"present": bool(m), "signed": bool(name and scope), "approved_by": name or None,
            "date": d or None, "scope": scope, "block": block.strip()}


# ------------------------------------------------------------------ run

def run() -> dict:
    from . import corrections, your_input as Y
    C.ensure(C.BRIEF, C.PAGES)
    assess = A.load()
    answers = preflight.load_answers()
    wf = assess["workflows"]
    by_short = {w["short"]: u for u, w in wf.items()}
    overrides = corrections.outcomes(by_short)

    fok = {c["cluster_id"]: c["first_of_a_kind"] for c in assess["clusters"]}
    specs = {u: C.spec(u) for u in wf}
    stale = stale_specs()

    q9 = next((q for q in answers["items"] if q["id"] == "Q9"), {})
    trigger_plan = TG.plan((assess.get("entry_points") or {}).get("model") or {},
                           q9.get("answer") if q9.get("status") == "answered" else None)
    vra_replaced = trigger_plan.get("front_end") in TG.REPLACING
    coverage = {r["key"]: r for r in (assess.get("plugin_coverage") or {}).get("rows") or []}
    reaching = TR.reaching_workflows(assess["actions"])
    loaded = Y.load()
    reviews = RV.load(specs, wf, loaded)

    # Ask once: a spec question a decision of the plan already asks (TLS, authentication, endpoints, shared
    # state, a blocking step's timeout) is taken out of the spec's open questions and pointed at that decision.
    # The cover is proposed from the code, then kept only where the plan really asks that decision.
    cover = cover_questions(assess, specs)
    for _ in range(3):
        eff = {u: effective_spec(specs[u], cover.get(u)) for u in wf}
        tri, act_tri = _triage(assess, eff, fok, overrides, coverage, reaching, vra_replaced, stale)
        qgroups = group_questions(assess, eff)
        qkeys = question_keys(qgroups, wf)
        plan = W.plan(assess, tri, answers, reviews=reviews, act_tri=act_tri, trigger_plan=trigger_plan, specs=specs,
                      question_keys=qkeys)
        asked_keys = set(G.entries(plan))
        dropped = {u: [c for c in cs if c["key"] not in asked_keys] for u, cs in cover.items()}
        if not any(dropped.values()):
            break
        cover = {u: [c for c in cs if c["key"] in asked_keys] for u, cs in cover.items()}
    attach_covers(plan, cover, wf)

    retire = [x for x in (TR.retire_candidate(w, eff[u]) for u, w in wf.items()) if x]
    retire_actions = [a["key"] for a in assess["actions"] if not a["called"]]
    defects = suspected_defects(assess, specs)
    from . import translate
    translation = translate.table(assess, {"triage": tri, "action_triage": act_tri, "plan": plan,
                                           "trigger_plan": trigger_plan})
    rec = recommend(assess, tri, act_tri, answers, translation["number"])
    flows = walkthroughs(assess, tri, eff)

    counts = Counter(t["outcome"] for t in tri.values())
    acounts = Counter(t["outcome"] for t in act_tri.values())
    result = {
        "generated": C.now(),
        "estate": C.ESTATE,
        "inputs": {k: {"path": C.rel(p), "mtime": C.mtime(p)} for k, p in {
            "assess": os.path.join(C.ASSESS, "assess.json"),
            "answers": C.ANSWERS,
            "corrections": C.CORRECTIONS,
            "specs": C.SPEC_DIR}.items()},
        "names": {u: {"name": w["name"], "short": w["short"]} for u, w in wf.items()},
        "counts": {o: counts.get(o, 0) for o in TR.OUTCOMES},
        "action_counts": {o: acounts.get(o, 0) for o in TR.OUTCOMES},
        "triage": tri, "action_triage": act_tri,
        "retire_candidates": retire, "retire_candidate_actions": retire_actions,
        "plan": plan, "recommendation": rec, "walkthroughs": flows, "trigger_plan": trigger_plan,
        "open_preflight_items": answers["open"],
        "stale_specs": stale,
        "open_questions": {"asked": sum(len(g["workflows"]) for g in qgroups), "grouped": len(qgroups),
                           "groups": qgroups,
                           "covered": {u: cs for u, cs in cover.items() if cs}},
        "suspected_defects": defects,
        "reviews": {"path": reviews["path"], "warnings": reviews["warnings"], "by_uid": reviews["by_uid"],
                    "reviewed": sorted(u for u, x in reviews["by_uid"].items() if x["state"] == "reviewed")},
    }

    # everything a person answers goes in your-input/answers.yaml; register it, then read it back
    asked = G.entries(plan)
    Y.register("brief", G.input_items(asked) + spec_question_items(qgroups, wf, tri, qkeys, asked)
               + RV.input_items(specs, wf))
    Y.register_unblocks(preflight_unblocks(plan))
    loaded = Y.load()
    reviews = RV.load(specs, wf, loaded)
    result["reviews"].update(by_uid=reviews["by_uid"],
                             reviewed=sorted(u for u, x in reviews["by_uid"].items() if x["state"] == "reviewed"))
    approval = Y.signature(loaded)
    result["approval"] = approval
    decisions = G.load(asked, loaded)
    G.close(plan, answers=answers, reviews=reviews, decisions=decisions, approval=approval)
    result["decisions"] = {"path": decisions["path"], "asked": asked, "by_key": decisions["by_key"],
                           "warnings": decisions["warnings"]}
    result["input"] = input_summary(loaded)
    result["gates"] = G.summary(plan)
    # ready now: on a known path and nothing open concerns it (needs the criteria closed, so after G.close)
    translate.ready(assess, result, translation)
    result["translation"] = translation
    from . import status as S_
    result["input"] = input_summary(loaded)
    st_ = S_.status_of(result)
    from . import front
    rec["produced_by"] = front.recommendation_text(assess, result)
    C.write_json(os.path.join(C.BRIEF, "brief.json"), result)
    C.write_json(os.path.join(C.BRIEF, "walkthroughs.json"), flows)
    _write_csv(assess, tri, act_tri, specs)
    C.write_text(os.path.join(C.BRIEF, "open-questions.md"),
                 C.no_em_dashes(open_questions_md(assess, eff, tri, answers, cover)))
    from . import brief_md
    C.write_text(C.BRIEF_MD, C.no_em_dashes(brief_md.render(result, assess, eff, answers, approval)))
    return result


def _triage(assess, specs, fok, overrides, coverage, reaching, vra_replaced, stale):
    wf = assess["workflows"]
    tri = {}
    for u, w in wf.items():
        tri[u] = TR.triage_workflow(w, specs[u], wf[fok[w["cluster"]]], u == fok[w["cluster"]], overrides.get(u))
    # a spec written from other code or another prompt than today's: the triage stands on it, so say so
    for u, why in stale.items():
        if u in tri:
            tri[u]["spec_stale"] = why
            tri[u]["notes"].append(f"its spec was written from different code or input than the estate's now "
                                   f"({why}); run `vro.py understand` (billed) before relying on this outcome")
    # a near-duplicate follows its first of a kind
    for u, t in tri.items():
        if t["outcome"] == "Near-duplicate":
            f = t["evidence"]["first_of_a_kind"]
            t["follows"] = {"uid": f, "outcome": tri[f]["outcome"]}
            t["reason"] += f"; converts the same way as its first of a kind, which is {tri[f]['outcome']}"
    act_tri = {}
    for a in assess["actions"]:
        first = next((k for k in sorted(a["same_code_as"]) if k < a["key"]), None)
        us = reaching.get(a["key"]) or set()
        vra_only = bool(us) and all(TG.started_by_vra(wf[u]) for u in us if u in wf)
        act_tri[a["key"]] = TR.triage_action(a, assess["targets"], first, overrides.get(a["key"]) or overrides.get(a["name"]),
                                             coverage, vra_replaced=vra_replaced, vra_only=vra_only)
    # an action with nothing of its own to decide it shares the fate of what uses it
    TR.follow_callers(act_tri, assess["actions"], tri, wf)
    return tri, act_tri


# ------------------------------------------------------------------ ask once

# A spec question that one of the plan's decisions already asks, by what it is about. Each rule: the decision
# family, the words that say the question is about it, and (for state) the themes it applies to.
COVER_RULES = [
    ("tls", re.compile(r"\b(tls|hostVerification|host verification|self-signed|ssl verification|insecure|"
                       r"certificate (?:check\w*|validation|verification)|"
                       r"(?:verify|verif\w+|validat\w+|check\w*) (?:the )?(?:server |host |peer |its )?certificates?)\b", re.I)),
    ("auth", re.compile(r"\b(authenticat\w*|basic auth\w*|session login|session cookie|api tokens?|bearer tokens?|"
                        r"service accounts?|credentials?)\b", re.I)),
    ("endpoint", re.compile(r"\b(endpoints?|base url|controller address|host ?names? of|"
                            r"production (?:or|vs\.?|versus|and) (?:lab|test|non-production)|"
                            r"(?:lab|test) (?:or|vs\.?|versus|and) production|which (?:controller|device|appliance|instance)s?)\b", re.I)),
    ("state-store", re.compile(r"\b(concurren\w*|in parallel|simultaneous\w*|at the same time|race condition|"
                               r"lost update|atomic\w*|lock(?:ing|s)?|two (?:runs|requests|events) (?:for|on))\b", re.I)),
    ("timeout", re.compile(r"\b(time ?outs?|blocking subscription|how long (?:vRA|it) waits)\b", re.I)),
]


def cover_questions(assess, specs) -> dict[str, list[dict]]:
    """uid -> [{question, key, family}] for each open spec question a decision of the plan covers, from the code:
    TLS where the client turns checks off, authentication where it differs from the API's, the endpoint of each
    system the workflow reaches, shared state its lifecycle group writes, the timeout of a blocking subscription
    that runs it. A question naming one system is covered by that system's decision only."""
    wf, targets = assess["workflows"], assess["targets"]
    groups = {g["id"]: g for g in assess.get("lifecycle_groups") or []}
    out = {}
    for u, w in wf.items():
        sp = specs.get(u) or {}
        ext = [t for t in w["targets"] if t in targets and t not in ("vro-platform", "rest-host")]
        g = groups.get(w.get("lifecycle_group") or "")
        paths = sorted(set((w.get("state") or {}).get("writes") or []) | set((w.get("state") or {}).get("reads") or []))
        if g:
            gw = {p for v in g["members"] for p in (wf[v].get("state") or {}).get("writes") or []}
            paths = sorted(p for p in paths if p in gw) or sorted(gw)
        else:
            paths = sorted((w.get("state") or {}).get("writes") or [])
        subs = [e for e in w.get("entry_points") or [] if e.get("kind") == "subscription" and e.get("blocking")]
        for q in sp.get("open_questions") or []:
            text = q.get("question") or ""
            named = [t for t in ext if re.search(r"\b(" + "|".join(re.escape(x) for x in {
                targets[t].get("name") or t, targets[t].get("short_name") or t, t.split("-")[0]} if x) + r")\b", text, re.I)]
            on = named or ext
            keys = []
            for fam, rx in COVER_RULES:
                if not rx.search(text):
                    continue
                if fam == "tls":
                    keys += [f"tls.{G.key_part(t)}" for t in on if targets[t].get("tls_off")]
                elif fam == "auth":
                    keys += [f"auth.{G.key_part(t)}" if targets[t].get("client_auth") and targets[t].get("api_auth")
                             else f"endpoint.{G.key_part(t)}" for t in on]
                elif fam == "endpoint":
                    keys += [f"endpoint.{G.key_part(t)}" for t in on]
                elif fam == "state-store" and ((q.get("theme") or "").lower() == "concurrency"
                                               or re.search(r"\b(state|record|registry|entry|entries)\b", text, re.I)):
                    keys += [f"state-store.{G.key_part(p)}" for p in paths]
                elif fam == "timeout":
                    keys += [f"timeout.{G.key_part(e['name'])}" for e in subs]
            keys = list(dict.fromkeys(keys))
            if keys:
                out.setdefault(u, []).append({"question": text, "theme": q.get("theme"), "keys": keys,
                                              "key": keys[0]})
    # one entry per (question, key): a question covered by two decisions is answered by both
    flat = {}
    for u, cs in out.items():
        flat[u] = [dict(c, key=k) for c in cs for k in c["keys"]]
    return flat


def effective_spec(spec: dict | None, covered: list[dict] | None) -> dict | None:
    """The spec as the brief reads it: without the open questions a decision asks (they are still in the file)."""
    if not spec or not covered:
        return spec
    gone = {c["question"] for c in covered}
    return {**spec, "open_questions": [q for q in spec.get("open_questions") or [] if q.get("question") not in gone],
            "covered_questions": [{"question": q, "by": [c["key"] for c in covered if c["question"] == q]}
                                  for q in sorted(gone)]}


def attach_covers(plan: dict, cover: dict, wf: dict) -> None:
    """Each decision that covers a spec question says so, so the question is asked once, there."""
    by_key: dict[str, list[str]] = {}
    for u, cs in cover.items():
        for c in cs:
            q = f"{wf[u]['name']}: {c['question']}"
            if q not in by_key.setdefault(c["key"], []):
                by_key[c["key"]].append(q)
    for w in plan["waves"]:
        for e in w["entry_criteria"]:
            c = e.get("closes") or {}
            if c.get("kind") == "decision" and c["key"] in by_key:
                c["also_answers"] = by_key[c["key"]]


def question_keys(qgroups: list[dict], wf: dict) -> dict[tuple, str]:
    """(uid, question) -> the answers.yaml key of the grouped question it belongs to."""
    out = {}
    for g in qgroups:
        k = f"question.{wf[g['workflows'][0]]['short']}.{_hash(g['question'])}"
        g["key"] = k
        for x in g["wordings"]:
            out[(x["workflow"], x["question"])] = k
    return out


def _hash(s: str) -> str:
    import hashlib
    return hashlib.sha256(s.encode()).hexdigest()[:6]


def spec_question_items(qgroups, wf, tri, qkeys, asked) -> list[dict]:
    """The specs' open questions as answers.yaml items: one per grouped question. A blocking one is already a
    decision of its wave (the same key), so it is listed once, there."""
    from . import your_input as Y
    out = []
    for g in qgroups:
        if g["key"] in asked:
            continue
        names = ", ".join(wf[u]["name"] for u in g["workflows"])
        it = Y.item(g["key"], "answer", f"{names}: {g['question']}", why=g.get("why_it_matters"),
                    unblocks=([f"{wf[u]['name']} leaves Needs owner input" for u in g["workflows"]
                               if tri[u]["outcome"] == "Needs owner input"] if g["blocking"] else
                              ["the converter builds what you say (nothing waits on it)"]),
                    family="question", link=f"report/workflows/{wf[g['workflows'][0]]['short']}.html")
        it["family_title"] = "Spec questions"
        out.append(it)
    return out


def preflight_unblocks(plan: dict) -> dict[str, list[str]]:
    """Preflight question key -> the entry criteria it closes."""
    out: dict[str, list[str]] = {}
    for w in plan["waves"]:
        for e in w["entry_criteria"]:
            c = e.get("closes") or {}
            if c.get("kind") == "answer":
                out.setdefault(c["field"], []).append(f"Wave {w['wave']}: {e['criterion']}")
    return out


def input_summary(loaded: dict) -> dict:
    """What a person still has to do in answers.yaml, per group and family (the front page shows it)."""
    from . import your_input as Y
    sm = Y.summary(loaded)
    out = {"path": C.rel(C.ANSWERS), "warnings": loaded["warnings"], "sign": sm["sign"],
           "stand_in": bool(loaded.get("stand_in"))}
    for g in ("confirm", "answer", "review"):
        fam: dict[str, dict] = {}
        for bucket in ("open", "stale", "done"):
            for it in sm[g][bucket]:
                f = fam.setdefault(it.get("family_title") or it["family"],
                                   {"open": [], "done": 0, "stale": 0, "family": it["family"]})
                if bucket == "done":
                    f["done"] += 1
                else:
                    f["stale"] += bucket == "stale"
                    f["open"].append({"key": it["key"], "title": it["title"], "unblocks": it["unblocks"][:3],
                                      "stale": bucket == "stale"})
        out[g] = {"open": sum(len(f["open"]) for f in fam.values()), "done": sum(f["done"] for f in fam.values()),
                  "families": fam}
    return out


def _write_csv(assess, tri, act_tri, specs):
    wf = assess["workflows"]
    dom = {d["id"]: d["name"] for d in assess["domains"]["domains"]}
    rows = []
    for u in sorted(tri):
        t, w = tri[u], wf[u]
        rows.append({"kind": "workflow", "id": u, "name": w["name"], "source": w["source"],
                     "cluster": w["cluster"], "domain": dom.get(w["domain"], ""), "outcome": t["outcome"],
                     "reason": t["reason"], "also_matched": " | ".join(t["also"]),
                     "spec_confidence": w["spec_confidence"] or "",
                     "targets": " ".join(x for x in w["targets"] if x != "vro-platform"),
                     "reads": w["reads"], "writes": w["writes"], "script_lines": w["script_lines"],
                     "called_by": " ".join(w["called_by"]), "overridden": t["overridden"]})
    for k, t in sorted(act_tri.items()):
        a = next(x for x in assess["actions"] if x["key"] == k)
        rows.append({"kind": "action", "id": k, "name": a["name"], "source": a["source"], "cluster": "",
                     "domain": "", "outcome": t["outcome"], "reason": t["reason"],
                     "also_matched": " | ".join(t["also"]), "spec_confidence": "",
                     "targets": " ".join(a["library_for"]), "reads": "", "writes": "",
                     "script_lines": a["lines"],
                     "called_by": " ".join(a["called_by_workflows"] + a["called_by_actions"]),
                     "overridden": t["overridden"]})
    C.write_csv(os.path.join(C.BRIEF, "triage.csv"), rows,
                ["kind", "id", "name", "source", "cluster", "domain", "outcome", "reason", "also_matched",
                 "spec_confidence", "targets", "reads", "writes", "script_lines", "called_by", "overridden"])


# ------------------------------------------------------------------ recommendation

WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine"}
def recommend(assess, tri, act_tri, answers, number: dict | None = None) -> dict:
    wf = assess["workflows"]
    n = len(tri)
    c = Counter(t["outcome"] for t in tri.values())
    total_lines = assess["counts"]["script_lines_workflows"] + assess["counts"]["script_lines_actions"]
    actions = {a["key"]: a for a in assess["actions"]}
    plugin_lib_lines = sum(actions[k]["lines"] for k, t in act_tri.items() if t["outcome"] == "Plugin-covered library")
    # only client code: an action rebuilt because it is (or serves) a client for a system with no plugin. One
    # rebuilt because it keeps vRO's own state, or reads vRA's payload, is a known, small Kestra task, not a client.
    client_rebuild_lines = sum(actions[k]["lines"] for k, t in act_tri.items()
                               if t["outcome"] == "Rebuild" and t.get("basis") in ("client", "rest-helper"))
    wrapper_n = c.get("Wrapper", 0)
    manual_rebuild = c.get("Manual", 0) + c.get("Rebuild", 0)
    sizes = sorted((cl["size"] for cl in assess["clusters"]), reverse=True)
    top5 = sum(sizes[:5])
    singles = sum(1 for s in sizes if s == 1)
    total_lines = total_lines or 1
    client_targets = sorted({assess["targets"][t]["name"] for k, t_ in act_tri.items() if t_["outcome"] == "Rebuild"
                             and t_.get("basis") in ("client", "rest-helper")
                             for t in actions[k]["library_for"]
                             if t in assess["targets"] and assess["targets"][t]["reach"] != "plugin"})
    pl_share, cl_share = plugin_lib_lines / total_lines, client_rebuild_lines / total_lines
    signals = [
        {"signal": "Share of all script that is plugin-covered library",
         "value": f"{plugin_lib_lines} of {total_lines} lines ({pl_share:.0%})",
         "reads_as": ("Low. Almost nothing here is a client Kestra already ships." if pl_share < 0.1 else
                      "Material. This client code is dropped: a Kestra plugin replaces it.")},
        {"signal": "Share of all script that is a hand-written client for a target with no Kestra plugin",
         "value": f"{client_rebuild_lines} of {total_lines} lines ({cl_share:.0%})",
         "reads_as": (("High. " if cl_share >= 0.3 else "Some. ") + "This is client code for "
                      + ", ".join(client_targets) + ". It is rebuilt on the REST API or the vendor SDK, not converted."
                      if client_rebuild_lines else "None. No hand-written client for a target without a Kestra plugin.")},
        {"signal": "Share of workflows that are Manual or Rebuild (hand rewrite or new design)",
         "value": f"{manual_rebuild} of {n} ({manual_rebuild / n:.0%})",
         "reads_as": ("More than half cannot be converted by the tool." if manual_rebuild / n > 0.5 else
                      "A minority cannot be converted by the tool; the rest can, once gated.")},
        {"signal": "Workflows waiting only on owner answers (Needs owner input)",
         "value": f"{c.get('Needs owner input', 0)} of {n}",
         "reads_as": "Convertible once answered. Each question is an entry criterion of the workflow's wave."},
        {"signal": "Convert workflows whose spec an owner must review first (medium confidence)",
         "value": f"{sum(1 for t in tri.values() if t['outcome'] == 'Convert' and t['evidence'].get('spec_review'))} of {c.get('Convert', 0)}",
         "reads_as": "Converted by the tool, gated on the review in their wave's entry criteria."},
        {"signal": "Workflows that need no conversion (wrappers, near-duplicates)",
         "value": f"{wrapper_n + c.get('Near-duplicate', 0)} of {n}",
         "reads_as": "Removed from the work before any conversion starts."},
        {"signal": "How many patterns cover how many workflows",
         "value": f"{len(sizes)} clusters for {n} workflows; the largest 5 hold {top5} ({top5 / n:.0%}); {singles} are one of a kind",
         "reads_as": ("Little reuse. Converting one first of a kind rarely unlocks many others." if top5 / n < 0.4 else
                      "Real reuse. Converting a first of a kind unlocks the rest of its cluster.")},
        {"signal": "Open preflight items",
         "value": f"{len(answers['open'])} of {len(answers['items'])}",
         "reads_as": "Every wave that writes to a target is gated on Q3; the retire pass on Q2 and Q7."},
    ]
    share_mr = manual_rebuild / n
    reuse = top5 / n
    # The verdict reads the brief's number (translate.py): the share of elements and of lines with a known,
    # mechanical path. So the recommendation and the number cannot disagree.
    number = number or {}
    me, ml = number.get("share_elements", 0.0), number.get("share_lines", 0.0)
    signals.insert(0, {"signal": "Share of the estate with a known path (the number: Direct plus Redesign)",
                       "value": f"{me:.0%} of elements, {ml:.0%} of lines of script",
                       "reads_as": "Direct (a plugin, HTTP or core task, a direct conversion, a wrapper or a duplicate) "
                                   "or Redesign (the tool maps it, a person shapes it); the rest waits on a rebuild, "
                                   "a hand rewrite or an answer."})
    if (share_mr >= 0.6 and reuse < 0.4) or ml < 0.4:
        verdict = "rebuild"
        why = (f"Manual plus Rebuild workflows are {share_mr:.0%} (60% or more) and the five largest clusters hold "
               f"{reuse:.0%} (under 40%)" if share_mr >= 0.6 and reuse < 0.4 else
               f"only {ml:.0%} of the script has a Direct or Redesign path (under 40%)")
    elif share_mr <= 0.3 and me >= 0.7 and ml >= 0.7:
        verdict = "convert"
        why = (f"{me:.0%} of the elements and {ml:.0%} of the lines have a path (70% or more of both), "
               f"and {share_mr:.0%} of the workflows need redesign or a hand rewrite (30% or less)")
    else:
        verdict = "mix"
        held = [f"{share_mr:.0%} of the workflows need redesign or a hand rewrite (over 30%)" if share_mr > 0.3 else "",
                f"only {me:.0%} of the elements have a path (under 70%)" if me < 0.7 else "",
                f"only {ml:.0%} of the lines have a path (under 70%)" if ml < 0.7 else ""]
        why = (f"{me:.0%} of the elements and {ml:.0%} of the lines have a path, but "
               + "; ".join(x for x in held if x))
    # the signals the rule reads: the number and the workflow share (and, for a rebuild, the reuse)
    for sg in signals:
        sg["decides"] = sg["signal"].startswith(("Share of the estate with", "Share of workflows that are Manual",
                                                  "How many patterns"))
    fix_dom = next((d["id"] for d in assess["domains"]["domains"] if d["key"] == "none:fixtures"), None)
    fixtures = [u for u, w in wf.items() if assess["domains"]["cluster_domain"][w["cluster"]] == fix_dom]
    conv_fix = sum(1 for u in fixtures if tri[u]["outcome"] == "Convert")
    conv_ext = [u for u, t in tri.items() if t["outcome"] == "Convert" and u not in fixtures]
    return {
        "verdict": verdict,
        "produced_by": why,
        "signals": signals,
        "thresholds": "convert when at least 70% of the elements and of the lines have a Direct or Redesign path and at most "
                      "30% of the workflows are Manual or Rebuild; rebuild when under 40% of the lines have one, or "
                      "Manual plus Rebuild is at least 60% with the five largest clusters under 40%; otherwise a mix",
        "convert_part": {"fixtures": conv_fix, "external": conv_ext},
        "client_targets": client_targets,
        "client_share": round(cl_share, 3),
        "manual_rebuild_share": round(share_mr, 3),
        "top5_share": round(reuse, 3),
    }


# ------------------------------------------------------------------ walkthroughs

def _chain(start, wf, tri, seen=None):
    """Depth-first call chain from a workflow, through exported callees."""
    seen = seen or set()
    if start in seen or start not in wf:
        return []
    seen.add(start)
    out = [start]
    for n in wf[start]["nested"]:
        v = n.get("target_uid")
        if v and v in wf:
            out += _chain(v, wf, tri, seen)
    return out


def walkthrough_paths(assess, tri) -> list[dict]:
    """Code picks the path. The model only writes the words."""
    wf = assess["workflows"]
    paths = []
    # requester: the longest call chain in the estate
    roots = [u for u in wf if not [c for c in wf[u]["called_by"] if c in wf]]
    best = max(roots, key=lambda u: (len(_chain(u, wf, tri)), u))
    paths.append({"persona": "the requester", "rule": "longest call chain from a workflow nothing in the estate calls",
                  "nodes": _chain(best, wf, tri)[:8]})
    # approver: the workflows with user interaction items
    ui = sorted(u for u, w in wf.items() if w["user_interaction"])
    if ui:
        paths.append({"persona": "the approver", "rule": "workflows that stop for a person (user interaction items)",
                      "nodes": ui})
    # operator: day-two operations on one target, reads before writes
    dom_ops = {d["id"] for d in assess["domains"]["domains"] if d["stage"] in ("operations", "configuration")}
    entries = [u for u in wf if wf[u]["domain"] in dom_ops and not wf[u]["called_by"]]
    fam = Counter()
    for u in entries:
        fam[wf[u]["source"]] += 100 if (wf[u]["reads"] and not wf[u]["writes"]) else 1
    src = fam.most_common(1)[0][0] if fam else None
    cand = [u for u in wf if wf[u]["domain"] in dom_ops and wf[u]["source"] == src and not wf[u]["called_by"]
            and tri[u]["outcome"] != "Rebuild"]
    cand.sort(key=lambda u: (wf[u]["writes"] > 0, wf[u]["writes"], u))
    if cand:                              # a persona with no steps has nothing to walk through
        paths.append({"persona": "the operator",
                      "rule": f"entry workflows in the operations and configuration domains of the source with the most "
                              f"read-only entry workflows ({src}), excluding Rebuild, read-only first",
                      "nodes": cand[:5]})
    return paths


def walkthroughs_prompt(payload: list[dict]) -> str:
    return safety.guard(
        "Below are business flows through a VMware vRealize Orchestrator estate, one per persona. The steps and "
        "their order are fixed and must not change: keep every step, in order, with its workflow id. For each "
        "flow write a name a steering committee member would recognise (under 8 words), one plain sentence "
        "describing it from the persona's point of view, and for each step a one-sentence label in business "
        "language saying what happens for that person. Use only what each step's purpose says. Where the "
        "purpose says the workflow is a test fixture or demo, say so plainly. No marketing words, no em dashes.\n\n"
        + safety.wrap("flows and step purposes", json.dumps(payload, indent=1)))


def walkthroughs(assess, tri, specs) -> dict:
    paths = walkthrough_paths(assess, tri)
    wf = assess["workflows"]
    payload = [{"persona": p["persona"],
                "steps": [{"workflow": u, "name": wf[u]["name"],
                           "purpose": ((specs.get(u) or {}).get("purpose") or "")[:700],
                           "brief_outcome": tri[u]["outcome"]} for u in p["nodes"]]} for p in paths]
    schema = {"type": "object", "additionalProperties": False, "required": ["flows"],
              "properties": {"flows": {"type": "array", "items": {
                  "type": "object", "additionalProperties": False,
                  "required": ["persona", "name", "description", "steps"],
                  "properties": {"persona": {"type": "string"}, "name": {"type": "string"},
                                 "description": {"type": "string"},
                                 "steps": {"type": "array", "items": {
                                     "type": "object", "additionalProperties": False,
                                     "required": ["workflow", "label"],
                                     "properties": {"workflow": {"type": "string"},
                                                    "label": {"type": "string"}}}}}}}}}
    prompt = walkthroughs_prompt(payload)
    flows = None
    if not os.environ.get("VRO_NO_MODEL"):
        try:
            v = C.model().ask("stages-walkthroughs", "estate", prompt, schema)
            flows = v["flows"]
        except Exception as e:  # noqa: BLE001
            print(f"[brief] walkthrough text fell back to code labels: {e}")
    if flows is None:
        cached = os.path.join(C.BRIEF, "walkthroughs.json")
        if os.path.exists(cached):
            prev = C.read_json(cached)
            if [f["nodes"] for f in prev.get("paths", [])] == [p["nodes"] for p in paths]:
                return prev
        flows = [{"persona": p["persona"], "name": f"Flow for {p['persona']}", "description": p["rule"],
                  "steps": [{"workflow": u, "label": wf[u]["name"]} for u in p["nodes"]]} for p in paths]
        written_by = "code (no model)"
    else:
        written_by = "model"
    # Structure is the code's: re-attach the fixed path, drop anything the model invented.
    out = []
    for p, f in zip(paths, flows):
        labels = {s["workflow"]: C.no_em_dashes(s["label"]) for s in f.get("steps", [])}
        out.append({"persona": p["persona"], "name": C.no_em_dashes(f["name"]),
                    "description": C.no_em_dashes(f["description"]), "path_rule": p["rule"],
                    "steps": [{"workflow": u, "label": labels.get(u, wf[u]["name"]),
                               "outcome": tri[u]["outcome"]} for u in p["nodes"]]})
    return {"written_by": written_by, "paths": paths, "flows": out}


def stale_specs() -> dict[str, str]:
    """uid -> why, for generated specs whose prompt would differ today (understand.stale_specs). No model call."""
    from kestra_migration_assistant.vra import understand
    try:
        return understand.stale_specs()
    except (OSError, KeyError, ValueError) as e:
        print(f"[brief] could not check the specs for staleness: {e}")
        return {}


# ------------------------------------------------------------------ open questions

_STOP = set("a an the of to in on for and or is are be by with from that this it its as at which what who when "
            "does do should would can could will if any each per there their they them than into after before "
            "how than not no".split())


def _words(q: str) -> set:
    return {w for w in re.findall(r"[a-z0-9_./-]+", (q or "").lower()) if len(w) > 2 and w not in _STOP}


def group_questions(assess, specs, threshold: float = 0.5) -> list[dict]:
    """
    One entry per question an owner answers once. Inside a lifecycle group (or, outside one, a cluster) the
    specs of different workflows often ask the same thing in other words: two questions with the same theme
    whose words overlap by `threshold` or more (Jaccard, common words left out) are one question, listed with
    every workflow it applies to. Each entry: theme, question (the first wording), workflows, wordings.
    """
    wf = assess["workflows"]
    out = []
    by_scope = defaultdict(list)
    for u in sorted(wf):
        scope = wf[u].get("lifecycle_group") or wf[u].get("cluster") or u
        by_scope[scope].append(u)
    for scope, us in sorted(by_scope.items()):
        groups = []
        for u in us:
            for q in (specs.get(u) or {}).get("open_questions", []):
                theme = (q.get("theme") or "general").lower()
                ws = _words(q["question"])
                best = None
                for g in groups:
                    if g["theme"] != theme:
                        continue
                    j = len(ws & g["_words"]) / (len(ws | g["_words"]) or 1)
                    if j >= threshold and (best is None or j > best[0]):
                        best = (j, g)
                if best:
                    g = best[1]
                    if u not in g["workflows"]:
                        g["workflows"].append(u)
                    g["wordings"].append({"workflow": u, "question": q["question"]})
                    g["_words"] |= ws
                else:
                    groups.append({"scope": scope, "theme": theme, "question": q["question"],
                                   "why_it_matters": q.get("why_it_matters"),
                                   "blocking": theme in TR.BLOCKING_THEMES, "workflows": [u],
                                   "wordings": [{"workflow": u, "question": q["question"]}], "_words": ws})
        for g in groups:
            g["blocking"] = g["blocking"] or any(
                (q.get("theme") or "").lower() in TR.BLOCKING_THEMES for w in g["wordings"]
                for q in (specs.get(w["workflow"]) or {}).get("open_questions", []) if q["question"] == w["question"])
            g.pop("_words")
            out.append(g)
    return out


def suspected_defects(assess, specs) -> list[dict]:
    """Every suspected defect the specs name, with its workflow and the decision key that settles it."""
    wf = assess["workflows"]
    out = []
    for u in sorted(wf):
        for i, d in enumerate((specs.get(u) or {}).get("suspected_defects") or [], 1):
            out.append({"workflow": u, "name": wf[u]["name"], "short": wf[u]["short"], "n": i, "what": d.get("what"),
                        "evidence": d.get("evidence"), "question": d.get("question"),
                        "decision": f"defect.{wf[u]['short']}.{i}"})
    return out


def open_questions_md(assess, specs, tri, answers, cover=None) -> str:
    wf = assess["workflows"]
    doms = {d["id"]: d for d in assess["domains"]["domains"]}
    L = ["# Open questions for owners", "",
         "Every question below is an item in `your-input/answers.yaml`, under the key shown: answer it there and "
         "re-run. This page only lists them.", "",
         "## Preflight (the export cannot answer these)", ""]
    for q in answers["items"]:
        L.append(f"- [{' ' if q['open'] else 'x'}] **{q['id']}** {q['question']} (`{q['key']}`)")
    L += ["", "## Per workflow, by domain", "",
          "Questions whose theme is correctness, external contract or concurrency put a workflow in Needs owner input. "
          "A question a decision already asks is not asked again: it is listed under *Asked once* below.", ""]
    by_dom = defaultdict(list)
    for u, w in wf.items():
        by_dom[w["domain"]].append(u)
    groups = group_questions(assess, specs)
    keys = question_keys(groups, wf)  # noqa: F841  (sets each group's key)
    for did in sorted(by_dom, key=lambda d: int(d[1:])):
        d = doms[did]
        mine = [g for g in groups if set(g["workflows"]) & set(by_dom[did])]
        n = sum(len(g["wordings"]) for g in mine)
        L += [f"### {d['name']} ({len(mine)} question{'s' if len(mine) != 1 else ''}"
              + (f", asked {n} times across the specs" if n != len(mine) else "") + "; owner: preflight Q6)", ""]
        for g in mine:
            flag = " (blocking)" if g["blocking"] else ""
            who = ", ".join(f"{wf[u]['name']} ({tri[u]['outcome']})" for u in g["workflows"])
            L.append(f"- [ ] *{g['theme']}{flag}*: {g['question']} Applies to: {who}. (`{g['key']}`)")
            for w in g["wordings"][1:]:
                if w["question"] != g["question"]:
                    L.append(f"  - also asked for {wf[w['workflow']]['name']}: {w['question']}")
        L.append("")
    cov = [(u, c) for u, cs in sorted((cover or {}).items()) for c in cs]
    if cov:
        L += ["## Asked once", "",
              "Questions the specs ask that a decision of the plan already asks. They are answered there, once.", ""]
        seen = set()
        for u, c in cov:
            if (u, c["question"]) in seen:
                continue
            seen.add((u, c["question"]))
            ks = [cc["key"] for uu, cc in cov if uu == u and cc["question"] == c["question"]]
            L.append(f"- {wf[u]['name']}: {c['question']} Decided in " + ", ".join(f"`{k}`" for k in ks) + ".")
        L.append("")
    defects = suspected_defects(assess, specs)
    L += ["## Suspected defects", "",
          "Behavior the specs found in the code that looks unintended. Each one, with its evidence, is on its "
          "workflow's page, and is a keep-or-fix item in `your-input/answers.yaml`.", ""]
    if defects:
        by: dict[str, list] = {}
        for x in defects:
            by.setdefault(x["workflow"], []).append(x)
        for u, xs in by.items():
            w = wf.get(u) or {}
            L.append(f"- {w.get('name') or u}: {len(xs)} possible defect{'s' if len(xs) != 1 else ''}, on "
                     f"[its page](../../report/workflows/{w.get('short') or u}.html)")
    else:
        L.append("None found" + ("" if any(s and "suspected_defects" in s for s in specs.values()) else
                                  " (the specs predate the field: regenerate them with `vro.py understand`)") + ".")
    L.append("")
    return "\n".join(L)
