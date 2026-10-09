"""
Triage: every workflow, and every action, gets exactly one outcome.

The rules are applied in a fixed order and the first that matches decides.
Every other rule that also matched is kept as evidence, so a reviewer can see
why, say, a wrapper was not also called Manual.

Order, and why:

1. Retire        Never assigned by code without run history. The rule is "no
                 executions in twelve months, or no caller and no trigger".
                 Without run history the pass is only half testable (callers
                 and the entry points on record). Candidates are listed for
                 owner confirmation; an owner confirms one with overrides.yaml.
2. Near-duplicate  Comes before anything that looks at the workflow itself,
                 because its fate is its first of a kind's.
3. Wrapper       A wrapper is not converted, whatever it calls: its callers
                 call the target directly.
4. Plugin-covered library  Actions only: every system the action reaches has a
                 Kestra plugin (a helper shared with an uncovered system is
                 Rebuild). An action with nothing of its own to decide it
                 follows its callers (follow_callers).
5. Rebuild       Exists because vRA or vRO did, so converting it faithfully
                 would carry the wrong design across. Judged on the workflow's
                 own logic (vRA actions it calls, an event-type dispatcher,
                 vRO's own state), never on being started by a subscription.
6. Manual        Needs a hand rewrite whatever an owner answers: OGNL form
                 logic, user interaction, a cycle, a file path parameter, a
                 target with no way to reach it, or a low-confidence spec (the
                 model could not read the workflow, so a person specifies it
                 again).
   Needs a model No spec, and nothing in the code decides it: what it does,
                 its possible defects and whether to convert or rebuild it
                 need a model (a run without one, or a call that failed).
                 Run again with a model and it gets its own outcome.
   Needs owner input  Blocked only on things an owner can answer: a blocking
                 open question, or a callee that is not in the export. Once
                 answered it converts like Convert, so it goes into a wave with
                 the answers as entry criteria.
7. Convert       Everything left: its own logic, no hard blockers, every
                 target reachable, spec confidence high or medium. Medium
                 adds "owner reviews the spec" to its wave's entry criteria.

Open questions: on the combined corpus every one of the 68 specs had some (1 to 7, 292 in
all). Treating any open question as blocking would hold every workflow and say nothing. A question
holds a workflow (Needs owner input) only when its theme says the spec
itself may be wrong about behaviour (BLOCKING_THEMES). The rest become entry
criteria for the workflow's wave: an owner answers them before it converts.
"""
from __future__ import annotations

import re
from typing import Any

OUTCOMES = ["Retire", "Near-duplicate", "Wrapper", "Plugin-covered library", "Convert", "Needs owner input",
            "Needs a model", "Manual", "Rebuild"]
NEEDS_MODEL = "what it does, its possible defects, and whether to convert or rebuild it need a model"
BLOCKING_THEMES = {"correctness", "external contract", "concurrency"}
VRA_SPEC = re.compile(r"\b(vRA|vRealize Automation|Aria Automation|service broker|blueprint)\b", re.I)
DISPATCH_NAME = re.compile(r"^process .* notification$", re.I)
TEST_FIXTURE_SPEC = re.compile(r"\b(test fixture|fixture|demo)\b", re.I)


def _wf_signals(w: dict, spec: dict | None, first: dict | None, is_first: bool) -> list[dict]:
    """All outcome signals for one workflow, in rule order. Each: outcome, reason, evidence."""
    sig = []
    sp = spec or {}

    # 2. near-duplicate of the cluster's first of a kind
    if first is not None and not is_first:
        same = (w["shape_sig"] == first["shape_sig"] and w["graph_sig"] == first["graph_sig"]
                and w["script_hashes"] == first["script_hashes"]
                and w["item_type_counts"] == first["item_type_counts"]
                and sorted(w["plugin_types"]) == sorted(first["plugin_types"]))
        if same:
            io_diff = sorted(set(w["io"]) ^ set(first["io"]))
            sig.append({"outcome": "Near-duplicate",
                        "reason": f"same cluster ({w['cluster']}), same shape, graph and normalised scripts as "
                                  f"first of a kind {first['uid']}; differs only in values"
                                  + (f" and {len(io_diff)} signature entr{'y' if len(io_diff) == 1 else 'ies'}" if io_diff else ""),
                        "evidence": {"first_of_a_kind": first["uid"], "shape": w["shape_sig"],
                                     "signature_differences": io_diff}})

    # 3. wrapper
    if w.get("wrapper"):
        sig.append({"outcome": "Wrapper", "reason": w["wrapper_reason"],
                    "evidence": {"calls": w["n_calls"], "logic_lines": w["logic_lines"]}})

    # 5. rebuild. Decided on what the workflow does, not on what starts it: a workflow a vRA
    # subscription starts is not "serving vRA" for that alone (its trigger is planned separately,
    # stages/triggers.py). A spec that names vRA counts only when no vRA entry point explains it.
    vra_actions = [a["action"] for a in w["called_actions"] if "VRA" in a["action"]]
    purpose = sp.get("purpose") or ""
    vra_started = any(e.get("kind") in ("subscription", "catalog item", "day2 action")
                      for e in w.get("entry_points") or [])
    spec_vra = bool(VRA_SPEC.search(purpose)) and not vra_started
    if vra_actions or spec_vra:
        sig.append({"outcome": "Rebuild",
                    "reason": "exists to serve vRA: " + (
                        f"calls {', '.join(sorted(set(vra_actions)))}" if vra_actions else "spec says vRA is the consumer")
                              + (" and the spec names vRA as the consumer" if vra_actions and spec_vra else ""),
                    "evidence": {"vra_actions": vra_actions, "spec_mentions_vra": bool(VRA_SPEC.search(purpose)),
                                 "started_by_vra": vra_started}})
    branches = w.get("event_branches") or []
    if len(branches) >= 2:
        sig.append({"outcome": "Rebuild",
                    "reason": f"event dispatcher: its own logic branches on {len(branches)} vRA event types "
                              f"({', '.join(branches[:5])}), which only makes sense inside vRA. In Kestra each "
                              "event gets its own trigger or its own step, and the switch goes away.",
                    "evidence": {"event_branches": branches}})
    elif DISPATCH_NAME.match(w["name"]) and w["item_type_counts"].get("switch") and len(w["nested"]) >= 2:
        sig.append({"outcome": "Rebuild",
                    "reason": "event subscription dispatcher: switches on an action type and calls one workflow "
                              "per event. In Kestra the subscription becomes a trigger and each event a flow.",
                    "evidence": {"inputs": w["inputs"], "nested": [n["display_name"] for n in w["nested"]]}})
    platform_writes = [o["op"] for o in w["ops"] if o["reach"] == "replace" and o["mode"] == "write"]
    other_writes = [o for o in w["ops"] if o["mode"] == "write" and o["reach"] not in ("replace",)
                    and o["target"] != "vro-platform"]
    if platform_writes and not other_writes:
        sig.append({"outcome": "Rebuild",
                    "reason": "its job is vRO's own state (" + ", ".join(sorted(set(platform_writes)))
                              + "). Kestra keeps endpoints and certificates as secrets and plugin defaults, "
                                "so there is nothing to translate.",
                    "evidence": {"vro_internal_writes": sorted(set(platform_writes))}})

    # 6. manual (hand rewrite) and needs owner input (answers only)
    rewrite, ask = [], []
    conf = sp.get("confidence")
    if conf == "low":
        rewrite.append("spec confidence low")
    if w["ognl"]:
        rewrite.append(f"{w['ognl']} OGNL presentation qualifier(s): the input form's logic needs redesign")
    if w["user_interaction"]:
        rewrite.append(f"{w['user_interaction']} user interaction item(s): approval, assignee and timeout need restating")
    if w["cycle"]:
        rewrite.append("a cycle in the item graph; a Kestra flow is a DAG")
    if w["file_typed_params"]:
        rewrite.append("file-typed parameter (" + ", ".join(w["file_typed_params"]) + "): a path on the vRO appliance")
    if w["unreachable_ops"]:
        rewrite.append("target with no way to reach it: " + ", ".join(w["unreachable_ops"]))
    unknown_callees = [n for n in w["nested"] if not n["in_export"] and n.get("kind") == "unknown workflow"]
    if unknown_callees:
        ask.append("calls a workflow that is not in the export and not recognised: "
                   + ", ".join(sorted({n["display_name"] or n["workflow_id"] for n in unknown_callees}))
                   + " (the owner supplies it or says what it does)")
    missing_actions = [a["action"] for a in w["called_actions"] if not a["in_export"]]
    if missing_actions:
        ask.append("calls an action that is not in the export: " + ", ".join(sorted(set(missing_actions)))
                   + " (the owner supplies it or names the library it comes from)")
    blocking_q = [q for q in sp.get("open_questions", []) if (q.get("theme") or "").lower() in BLOCKING_THEMES]
    if blocking_q:
        ask.append(f"{len(blocking_q)} open question(s) on whether the spec has the behaviour right")
    if rewrite:
        sig.append({"outcome": "Manual", "reason": "; ".join(rewrite + ask),
                    "evidence": {"blockers": rewrite, "owner_questions": ask}})
    elif conf is None:
        sig.append({"outcome": "Needs a model", "reason": NEEDS_MODEL + ("; it also " + "; ".join(ask) if ask else ""),
                    "evidence": {"blockers": [], "owner_questions": ask}})
    elif ask:
        sig.append({"outcome": "Needs owner input", "reason": "; ".join(ask).rstrip(".")
                    + ". Nothing else stops conversion: once answered it converts like Convert",
                    "evidence": {"blockers": [], "owner_questions": ask,
                                 "blocking_questions": [q["question"] for q in blocking_q],
                                 "missing": sorted({n["display_name"] or n["workflow_id"] for n in unknown_callees}
                                                   | set(missing_actions)),
                                 "spec_review": conf == "medium"}})

    # 7. convert: spec confidence high, or medium with an owner review of the spec as a wave gate
    if not rewrite and not ask and conf is not None:
        ext = [t for t in w["targets"] if t != "vro-platform"]
        where = f" ({', '.join(ext)})" if ext else " (no external target)"
        if conf == "high":
            sig.append({"outcome": "Convert",
                        "reason": "own logic, spec confidence high, no hard blockers, every target reachable" + where,
                        "evidence": {"spec_review": False}})
        else:
            sig.append({"outcome": "Convert",
                        "reason": f"own logic, spec confidence {conf}, no hard blockers, every target reachable" + where
                                  + "; its wave's entry criteria include an owner review of the spec",
                        "evidence": {"spec_review": True}})
    return sig


def triage_workflow(w: dict, spec: dict | None, first: dict | None, is_first: bool,
                    override: dict | None = None) -> dict:
    sig = _wf_signals(w, spec, first, is_first)
    chosen = sig[0]
    rec = {"uid": w["uid"], "kind": "workflow", "outcome": chosen["outcome"], "reason": chosen["reason"],
           "also": [f"{s['outcome']}: {s['reason']}" for s in sig[1:] if s["outcome"] != chosen["outcome"]],
           "evidence": chosen["evidence"], "overridden": False}
    rec["notes"] = _conversion_notes(w)
    from .triggers import notes as trigger_notes
    from .triggers import tags as trigger_tags
    rec["trigger_notes"] = trigger_notes(w)
    rec["trigger_tags"] = trigger_tags(w)
    if w.get("lifecycle_group"):
        rec["trigger_notes"].append(f"part of deployment lifecycle group {w['lifecycle_group']}: it migrates in the "
                                    "same wave as the rest of the group")
    if override:
        rec.update({"outcome": override["outcome"], "overridden": True,
                    "reason": f"owner decision: {override.get('reason') or 'set in corrections.yaml'} "
                              f"(rules said {chosen['outcome']}: {chosen['reason']})"})
    return rec


def _conversion_notes(w: dict) -> list[str]:
    """Facts a converter must carry that do not change the outcome."""
    notes = []
    h = w.get("default_error_handler")
    if h and h.get("target"):
        path = w.get("error_handler_path") or [h["target"]]
        notes.append(f"default error handler: any uncaught error goes to {h['target']}"
                     + (f" (binds {h['throw_bind_name']})" if h.get("throw_bind_name") else "")
                     + f", path {' > '.join(path)}; in Kestra this is the flow-level `errors` block")
    return notes


def _covered(t: str, ops: list[str], targets: dict, coverage: dict | None) -> bool:
    """A system is covered for these operations when Kestra has a plugin for it and, where the coverage view
    maps operations to tasks, every one of them has a task in the snapshot."""
    if (targets.get(t) or {}).get("reach") != "plugin":
        return False
    row = (coverage or {}).get(t)
    if not row:
        return True
    if row["status"] in ("http", "new plugin"):
        return False
    # an operation the map does map to tasks, with no task for it in the snapshot; with no operation-level
    # map for the system at all, the plugin's presence decides
    return not (row["status"] == "partial" and row.get("task_map") and set(ops) & set(row.get("unmatched_ops") or []))


def _uses(a: dict, targets: dict, coverage: dict | None) -> list[dict]:
    """Per system an action reaches: its operations (or, for a REST helper, its call sites), whether a Kestra
    plugin covers them, and what replaces them."""
    by: dict[str, dict] = {}
    if a.get("rest_helper"):
        for x in a.get("uses") or []:
            if x["system"] == "rest-host":
                continue
            d = by.setdefault(x["system"], {"ops": [], "callers": []})
            d["ops"].append(x["op"])
            if x["caller_name"] not in d["callers"]:
                d["callers"].append(x["caller_name"])
    elif "all_ops" not in a:
        for t in a.get("library_for") or []:
            by.setdefault(t, {"ops": [o.get("op") for o in a.get("ops") or [] if o.get("target", t) == t], "callers": []})
    else:
        for o in a.get("all_ops") or []:
            if o["target"] in ("vro-platform", "rest-host") or o.get("generic"):
                continue
            d = by.setdefault(o["target"], {"ops": [], "callers": []})
            d["ops"].append(o["op"])
    out = []
    for t, d in sorted(by.items()):
        cov = _covered(t, d["ops"], targets, coverage)
        tg = targets.get(t) or {}
        row = (coverage or {}).get(t) or {}
        tasks = sorted({m["task"] for m in row.get("task_map") or [] if m["op"] in d["ops"]})
        out.append({"system": t, "name": tg.get("name") or t, "covered": cov, "ops": sorted(set(d["ops"])),
                    "call_sites": len(d["ops"]), "callers": d["callers"], "tasks": tasks,
                    "replaced_by": (tasks_short(tasks) if tasks else (tg.get("kestra_tasks") or ["a plugin task"])[0])
                    if cov else "io.kestra.plugin.core.http.Request"})
    return out


def tasks_short(tasks: list[str]) -> str:
    """One task class in full; several under their shared package: `io.kestra.plugin.ee.f5` pool.Create, ..."""
    if len(tasks) <= 1:
        return ", ".join(tasks)
    parts = [t.split(".") for t in tasks]
    n = 0
    while all(len(p) > n + 1 for p in parts) and len({p[n] for p in parts}) == 1:
        n += 1
    if n < 3:
        return ", ".join(tasks)
    return ".".join(parts[0][:n]) + " tasks " + ", ".join(".".join(p[n:]) for p in parts)


def _sites(u: dict) -> str:
    n = len(u["callers"])
    return (f"{u['call_sites']} call site{'s' if u['call_sites'] != 1 else ''}"
            + (f" in {n} caller{'s' if n != 1 else ''}" if n else ""))


ELEMENT_KINDS = ("configuration element", "resource element")


def _vro_state(a: dict) -> str | None:
    """What a Kestra flow uses instead, when every operation of the action's own code is vRO element access."""
    own = a.get("ops") or []
    if not own or any(o.get("target") != "vro-platform" or o.get("kind") not in ELEMENT_KINDS for o in own):
        return None
    kinds = []
    if any(o["kind"] == "configuration element" and o.get("mode") == "write" for o in own):
        kinds.append("state written at run time goes to the KV store")
    if any(o["kind"] == "configuration element" and o.get("mode") != "write" for o in own):
        kinds.append("settings become namespace variables or KV entries, credentials Kestra secrets")
    if any(o["kind"] == "resource element" for o in own):
        kinds.append("a resource element becomes a namespace file, read with read() or rendered with Pebble")
    return "; ".join(dict.fromkeys(kinds))


def triage_action(a: dict, targets: dict, first_with_same_code: str | None, override: dict | None = None,
                  coverage: dict | None = None, vra_replaced: bool = False, vra_only: bool = False) -> dict:
    """One action's outcome from its own code. An action with nothing of its own to decide it (no system,
    no duplicate, not a wrapper) is marked `by_caller`: follow_callers() then judges it by what uses it.
    coverage: the plugin coverage rows by target key (assess plugin_coverage). vra_replaced: preflight Q9 says
    vRA goes (replace, or replace with a bridge); vra_only: every workflow that reaches the action is started
    by vRA. With both, an action that reads vRA's event payload or decides on its event type is Rebuild: the
    flow takes typed inputs and its trigger or path decides."""
    sig: list[tuple[Any, str]] = []
    uses = _uses(a, targets, coverage)
    covered = [u for u in uses if u["covered"]]
    not_covered = [u for u in uses if not u["covered"]]
    if a.get("rest_helper"):
        if not uses or not not_covered:
            sig.append(("Plugin-covered library",
                        "generic REST client: it sends whatever method and path its callers pass. Each call site "
                        "becomes a Kestra task on its own, "
                        + ("a plugin task (" + "; ".join(f"{u['name']}: {u['replaced_by']}" for u in uses) + ")"
                           if uses else "a plugin task where the system has a plugin, else "
                                        "io.kestra.plugin.core.http.Request")))
        elif covered:
            sig.append(("Rebuild",
                        "shared REST client, not covered by a plugin as a whole: a Kestra plugin replaces its uses for "
                        + "; ".join(f"{u['name']} ({_sites(u)}, replaced by {u['replaced_by']})" for u in covered)
                        + ", but its uses for " + "; ".join(f"{u['name']} ({_sites(u)})" for u in not_covered)
                        + " have no plugin and are rebuilt as HTTP tasks (io.kestra.plugin.core.http.Request). "
                          "It goes once the last of those is rebuilt"))
        else:
            sig.append(("Rebuild",
                        "generic REST client for " + ", ".join(u["name"] for u in not_covered)
                        + ", which has no Kestra plugin. Each call site becomes an HTTP task "
                          "(io.kestra.plugin.core.http.Request) rather than carrying this JavaScript across"))
    elif a["library_for"]:
        if not not_covered:
            sig.append(("Plugin-covered library",
                        "hand-written client for " + ", ".join(u["name"] for u in covered)
                        + ", which Kestra has a plugin for (" + ", ".join(u["replaced_by"] for u in covered) + ")"))
        elif all(targets.get(u["system"], {}).get("reach") in ("plugin", "http") for u in not_covered):
            sdk = [targets[u["system"]].get("sdk_alternative") for u in not_covered
                   if targets[u["system"]].get("sdk_alternative")]
            sig.append(("Rebuild",
                        "hand-written client for " + ", ".join(u["name"] for u in not_covered)
                        + ", which has no Kestra plugin. Call its REST API from HTTP tasks"
                        + (f", or use {sdk[0].split(',')[0]}" if sdk else "")
                        + ", rather than carrying this JavaScript across"
                        + ("; its calls to " + ", ".join(f"{u['name']} ({u['replaced_by']})" for u in covered)
                           + " are replaced by a plugin" if covered else "")))
        else:
            sig.append(("Manual", "client for a target with no Kestra route: "
                        + ", ".join(u["name"] for u in not_covered
                                    if targets.get(u["system"], {}).get("reach") not in ("plugin", "http"))))
        if sig and a.get("client_through"):
            # a body builder: no system of its own, it builds requests on the client's actions
            o, r = sig[0]
            sig[0] = (o, "part of the client it builds on (" + ", ".join(a["client_through"]) + ")"
                      + (", with helpers that reach no system (" + ", ".join(a["builder_helpers"]) + ")"
                         if a.get("builder_helpers") else "") + ": " + r)
    # the workflow-level Rebuild rules, for an action's own code: vRO's own state, vRA's event payload
    if not sig:
        st = _vro_state(a)
        if st:
            ops = sorted({o["op"] for o in a["ops"]})
            sig.append(("Rebuild", f"its job is vRO's own state ({'; '.join(ops)}): in Kestra {st}"))
    if not sig and vra_replaced and vra_only and (a.get("event_payload_keys") or a.get("event_branches")):
        what = []
        if a.get("event_payload_keys"):
            what.append("reads vRA's event payload (" + ", ".join(a["event_payload_keys"]) + ")")
        if a.get("event_branches"):
            what.append("decides on vRA's event type (" + ", ".join(a["event_branches"]) + ")")
        sig.append(("Rebuild", " and ".join(what) + ". With vRA replaced, each flow takes typed inputs and its "
                               "trigger or the path it is on decides, so this is not converted"))
    if a["wrapper"]:
        one = (a["ops"][0]["op"] if a["ops"] else f"{a['calls_actions'][0]['action']}")
        sig.append(("Wrapper", f"one call ({one}) and little else"))
    if first_with_same_code:
        # a duplicate's fate is its twin's only for code that is converted; a client or a rebuild keeps its own
        # outcome (two clients with the same code map to different plugin tasks) and the match is kept as `also`
        dup = ("Near-duplicate", f"same normalised code as {first_with_same_code.rsplit('/', 1)[-1]}; "
                                 "it goes the way that one does")
        if sig and sig[0][0] in ("Plugin-covered library", "Rebuild", "Manual"):
            sig.append(dup)
        else:
            sig.insert(0, dup)
    by_caller = not sig
    if by_caller:
        sig.append((None, ""))
    outcome, reason = sig[0]
    # what the outcome rests on, for the brief's numbers and its translation table (same order as the rules)
    basis = ({"Near-duplicate": "duplicate", "Wrapper": "wrapper"}.get(outcome)
             or ("rest-helper" if a.get("rest_helper") else "client" if a["library_for"] else
                 "vro-state" if _vro_state(a) else "payload" if outcome == "Rebuild" else None))
    rec = {"uid": a["uid"], "key": a["key"], "name": a.get("name") or a["key"], "kind": "action", "outcome": outcome,
           "reason": reason, "also": [f"{o}: {r}" for o, r in sig[1:] if o != outcome], "overridden": False,
           "uses": uses, "by_caller": by_caller, "same_as": first_with_same_code, "basis": basis}
    if override:
        rec.update({"outcome": override["outcome"], "overridden": True, "by_caller": False, "basis": "owner",
                    "reason": f"owner decision: {override.get('reason') or 'set in corrections.yaml'} "
                              f"(rules said {outcome or 'follow its callers'}: {reason or 'no signal of its own'})"})
    return rec


# How a caller's outcome carries to an action judged by its callers, strongest first.
_CARRY = {"Convert": "Convert", "Near-duplicate": "Convert", "Needs owner input": "Convert", "Wrapper": "Convert",
          "Rebuild": "Rebuild", "Manual": "Manual", "Needs a model": "Needs a model",
          "Plugin-covered library": "Plugin-covered library", "Retire": "Retire"}
_CARRY_ORDER = ["Convert", "Rebuild", "Manual", "Needs a model", "Plugin-covered library", "Retire"]


def _names(xs: list[str], n: int = 4) -> str:
    xs = sorted(set(xs))
    return ", ".join(xs[:n]) + (f" and {len(xs) - n} more" if len(xs) > n else "")


def reaching_workflows(actions: list[dict]) -> dict[str, set]:
    """action key -> the workflows that reach it, directly, through a form, or through other actions."""
    by_key = {a["key"]: a for a in actions}

    def up(k, seen):
        if k in seen or k not in by_key:
            return set()
        seen.add(k)
        a = by_key[k]
        out = set(a["called_by_workflows"]) | set(a.get("referenced_by_forms") or [])
        for c in a["called_by_actions"]:
            out |= up(c, seen)
        return out
    return {k: up(k, set()) for k in by_key}


def follow_callers(act_tri: dict, actions: list[dict], tri: dict, wf: dict) -> None:
    """Judge each action marked `by_caller` by what uses it (in place). It has no system of its own and no
    blocker of its own, so it shares its callers' fate: used by a workflow that converts, it converts with
    it, as part of that flow; used only by a client a plugin replaces, it goes with it; used only by work
    that is rebuilt or rewritten by hand, it is rebuilt or rewritten with that work, and the reason names the
    caller and its blocker. Nothing in the export calls it: an owner says what does."""
    by_key = {a["key"]: a for a in actions}
    memo: dict[str, str | None] = {}

    def wf_outcome(u):
        t = tri.get(u) or {}
        return (t.get("follows") or {}).get("outcome") if t.get("outcome") == "Near-duplicate" else t.get("outcome")

    def effective(k, stack=()):
        """The outcome an action passes on to what it calls."""
        if k in memo:
            return memo[k]
        if k in stack or k not in act_tri:
            return None
        t = act_tri[k]
        if t["outcome"] == "Near-duplicate" and not t.get("by_caller"):
            first = t.get("same_as")
            out = effective(first, stack + (k,)) if first else "Convert"
        elif t.get("by_caller") or t["outcome"] == "Wrapper":
            out = judge(k, stack + (k,))[0]
        else:
            out = t["outcome"]
        memo[k] = out
        return out

    def workflows_reaching(k, seen=None):
        seen = seen if seen is not None else set()
        if k in seen:
            return set()
        seen.add(k)
        a = by_key[k]
        out = set(a["called_by_workflows"]) | set(a.get("referenced_by_forms") or [])
        for c in a["called_by_actions"]:
            if c in by_key:
                out |= workflows_reaching(c, seen)
        return out

    def judge(k, stack):
        a = by_key[k]
        callers = []                      # (outcome carried, display name, kind, reason)
        for u in sorted(set(a["called_by_workflows"]) | set(a.get("referenced_by_forms") or [])):
            o = _CARRY.get(wf_outcome(u) or "")
            if o:
                callers.append((o, (wf.get(u) or {}).get("name") or u, "workflow", (tri.get(u) or {}).get("reason", "")))
        for c in sorted(a["called_by_actions"]):
            o = effective(c, stack)
            if o:
                callers.append((o, by_key[c]["name"] if c in by_key else c, "action", act_tri[c]["reason"]))
        if not callers:
            if a["called_by_workflows"] or a["called_by_actions"] or a.get("referenced_by_forms"):
                return None, "", callers
            return ("Needs owner input", "nothing in the export calls it: an owner says what does (it then converts "
                    "with that caller) or confirms it is unused", callers)
        got = {o for o, *_ in callers}
        outcome = next(o for o in _CARRY_ORDER if o in got)
        return outcome, "", callers

    for k, t in act_tri.items():
        if not t.get("by_caller"):
            continue
        outcome, reason, callers = judge(k, (k,))
        memo[k] = outcome
        of = lambda o: [n for x, n, *_ in callers if x == o]
        wfs = sorted((wf.get(u) or {}).get("name") or u for u in workflows_reaching(k))
        through = sorted({n for x, n, kind, _ in callers if kind == "action" and x == outcome})
        if outcome == "Convert":
            reason = ("converts with the workflows that use it, as part of their conversion: " + _names(wfs)
                      + (f" (through {_names(through)})" if through else "")
                      + ". Its logic moves into their flows; it is not a flow of its own")
        elif outcome == "Rebuild":
            reason = f"rebuilt with what uses it ({_names(of('Rebuild'))})"
        elif outcome == "Manual":
            first = next((r for x, n, kind, r in callers if x == "Manual"), "")
            reason = (f"its only callers are rewritten by hand, and it is rewritten with them: {_names(of('Manual'))}"
                      + (f" ({first.split(';')[0]})" if first else ""))
        elif outcome == "Needs a model":
            reason = (f"goes with the workflows that use it ({_names(of('Needs a model'))}), whose conversion "
                      "needs a model to judge")
        elif outcome == "Plugin-covered library":
            reason = (f"used only by {_names(of('Plugin-covered library'))}, which a Kestra plugin replaces; "
                      "it goes with them")
        elif outcome == "Retire":
            reason = f"used only by {_names(of('Retire'))}, which is retired"
        elif outcome is None:
            outcome, reason = "Needs owner input", ("its callers form a loop with no workflow above it: an owner says "
                                                    "what starts it")
        rest = [(o, n) for o, n, *_ in callers if o != outcome]
        if outcome in ("Convert", "Rebuild") and rest:
            goes = [n for o, n in rest if o == "Plugin-covered library"]
            hand = [n for o, n in rest if o == "Manual"]
            reb = [n for o, n in rest if o == "Rebuild"]
            if goes:
                reason += f"; its use in {_names(goes)} goes with the plugin that replaces {'it' if len(goes) == 1 else 'them'}"
            if reb:
                reason += f"; {_names(reb)} {'is' if len(reb) == 1 else 'are'} rebuilt and take{'s' if len(reb) == 1 else ''} the same logic"
            if hand:
                reason += f"; {_names(hand)} {'is' if len(hand) == 1 else 'are'} rewritten by hand and reuse{'s' if len(hand) == 1 else ''} it"
        # the basis it carries: a helper rebuilt with a client is client code, one converted with its flows is
        # converted logic
        carried = [act_tri[c]["basis"] for c in sorted(by_key[k]["called_by_actions"])
                   if c in act_tri and effective(c, (k,)) == outcome and act_tri[c].get("basis")]
        basis = {"Convert": "with-flows", "Plugin-covered library": "client", "Retire": "retire"}.get(outcome) \
            or (carried[0] if carried else ("workflow" if outcome == "Rebuild" else None))
        t.update({"outcome": outcome, "reason": reason, "callers": [{"outcome": o, "name": n, "kind": kind}
                                                                     for o, n, kind, _ in callers],
                  "workflows": wfs, "basis": basis})
    for k, t in act_tri.items():
        t["workflow_uids"] = sorted(workflows_reaching(k)) if k in by_key else []


def retire_candidate(w: dict, spec: dict | None) -> dict | None:
    """No caller anywhere in the export and no entry point on record (vRA export or entry-points.yaml)."""
    if w["called_by"] or w.get("entry_points"):
        return None
    notes = []
    p = (spec or {}).get("purpose") or ""
    if re.search(r"deprecated", w["name"], re.I):
        notes.append("named deprecated")
    if TEST_FIXTURE_SPEC.search(p):
        notes.append("spec calls it a test fixture or demo")
    return {"uid": w["uid"], "why": "nothing in the export calls it, and no entry point on record starts it",
            "supporting": notes}
