"""
Understand: the model reads a normalized workflow and writes a spec.

Leaves first. A workflow is only specified once every workflow it calls has been
specified, so a caller's prompt can carry its callees' specs. Actions are
specified before any workflow that calls them.

The spec is the only thing the converter and the test writer are given about
intent. Neither of them sees the other's output.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict

from kestra_migration_assistant.vra import model as M
from kestra_migration_assistant.vra import safety

SPEC_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["purpose", "inputs", "outputs", "external_systems", "decisions",
                 "side_effects", "assumptions", "open_questions", "confidence",
                 "confidence_reason"],
    "properties": {
        "purpose": {"type": "string", "minLength": 40},
        "inputs": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["name", "type", "meaning"],
            "properties": {"name": {"type": "string"}, "type": {"type": "string"},
                           "meaning": {"type": "string"},
                           "is_secret": {"type": "boolean"}}}},
        "outputs": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["name", "type", "meaning"],
            "properties": {"name": {"type": "string"}, "type": {"type": "string"},
                           "meaning": {"type": "string"}}}},
        "external_systems": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["system", "how"],
            "properties": {"system": {"type": "string"}, "how": {"type": "string"},
                           "items": {"type": "array", "items": {"type": "string"}}}}},
        "decisions": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["item", "condition", "branches"],
            "properties": {
                "item": {"type": "string"}, "condition": {"type": "string"},
                "branches": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["when", "goes_to", "does"],
                    "properties": {"when": {"type": "string"},
                                   "goes_to": {"type": "string"},
                                   "does": {"type": "string"}}}}}}},
        "side_effects": {"type": "array", "items": {"type": "string"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["question", "why_it_matters"],
            "properties": {"question": {"type": "string"},
                           "why_it_matters": {"type": "string"},
                           "theme": {"type": "string"}}}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "confidence_reason": {"type": "string"},
    },
}

# An estate spec (call_tree prompts) adds what looks unintended and the records kept between runs. The closed
# benchmark's SPEC_SCHEMA is left as it was.
ESTATE_SPEC_SCHEMA = json.loads(json.dumps(SPEC_SCHEMA))
ESTATE_SPEC_SCHEMA["required"] += ["suspected_defects", "state_records"]
ESTATE_SPEC_SCHEMA["properties"]["suspected_defects"] = {"type": "array", "items": {
    "type": "object", "additionalProperties": False,
    "required": ["what", "evidence", "question"],
    "properties": {"what": {"type": "string"}, "evidence": {"type": "string"}, "question": {"type": "string"}}}}
ESTATE_SPEC_SCHEMA["properties"]["state_records"] = {"type": "array", "items": {
    "type": "object", "additionalProperties": False,
    "required": ["where", "shape", "written_when"],
    "properties": {"where": {"type": "string"}, "shape": {"type": "string"}, "written_when": {"type": "string"}}}}

CLUSTER_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["name", "what_the_group_does", "how_members_differ",
                 "first_of_a_kind_reason"],
    "properties": {
        "name": {"type": "string"},
        "what_the_group_does": {"type": "string", "minLength": 40},
        "how_members_differ": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["member", "difference"],
            "properties": {"member": {"type": "string"},
                           "difference": {"type": "string"}}}},
        "first_of_a_kind_reason": {"type": "string"},
    },
}


def _trim_model(m: dict) -> dict:
    """What the model is shown. Full item graph and scripts, no shingles."""
    return {
        "name": m["name"],
        "description": m.get("description", ""),
        "category_path": m.get("category_path"),
        "root_item": m.get("root_item"),
        "inputs": m.get("inputs", []),
        "outputs": m.get("outputs", []),
        "attributes": m.get("attributes", []),
        "items": [{
            "name": i["name"], "type": i["type"], "display_name": i.get("display_name"),
            "out_name": i["attributes"].get("out-name"),
            "alt_out_name": i["attributes"].get("alt-out-name"),
            "catch_name": i["attributes"].get("catch-name"),
            "end_mode": i["attributes"].get("end-mode"),
            "linked_workflow_id": i["attributes"].get("linked-workflow-id"),
            "launched_workflow_id": i["attributes"].get("launched-workflow-id"),
            "script_module": i["attributes"].get("script-module"),
            "in_bindings": i.get("in_bindings", []),
            "out_bindings": i.get("out_bindings", []),
            "conditions": i.get("conditions", []),
            "references": i.get("references", []),
            "script": i.get("script"),
        } for i in m.get("items", [])],
        "edges": m.get("edges", []),
        "root_path_items": m.get("root_path_items", []),
        "presentation_ognl": m.get("presentation", {}).get("ognl", []),
        "plugin_types": m.get("plugin_types", []),
        "sdk_calls": m.get("sdk_calls", []),
        "config_refs": m.get("config_refs", []),
        "action_calls": m.get("action_calls", []),
        "graph": m.get("graph", {}),
    }


PROMPT = """You are reading one VMware vRealize Orchestrator workflow and writing a specification of what it does. You are not converting it. Someone else will do that from your specification, and a third person will write tests from it, and neither of them will see the XML. If you leave something out it is lost.

Read the item graph, not the document order. `root_item` is the entry point. Follow `out_name` for the normal path, `alt_out_name` for the false arm of a condition, `catch_name` for the error path, and the `conditions[].label` on a switch for each case target. An `end` item with `end_mode` "1" throws rather than finishing normally.

vRO specifics you need:
- `__NULL__` is vRO's null sentinel, not a string.
- A `link` item with `linked_workflow_id` calls another workflow and waits. With `launched_workflow_id` it starts one without waiting. A `foreach` item's `references[]` entry of type Workflow is the loop body.
- A `task` with `script_module` calls an action.
- `SecureString` means a secret.
- A type like `VC:VirtualMachine` or `VSPK:Session` is a plugin object: in vRO it arrives as a live authenticated session.
- `in_bindings` and `out_bindings` are how data moves between items. `export_name` is the workflow-scope name.

Be concrete. "Processes the request" is useless. Say which system, which object, which field.

For `open_questions`, list only things you genuinely cannot determine from the code and that change how it should be converted. Do not pad. Give each a short `theme` such as "endpoint identity", "error semantics", "data shape", "scope", "scheduling".

For `confidence`: high if the graph and scripts fully determine the behaviour; medium if you had to infer intent from names; low if key behaviour sits behind an action or nested workflow you were not given.
{extra_rules}
THE WORKFLOW
{model_json}
{callee_block}{action_block}
Return the specification as JSON."""


def _callee_block(callees: list[dict]) -> str:
    if not callees:
        return ""
    return ("\nWORKFLOWS THIS ONE CALLS, already specified. Use these rather than "
            "guessing what they do:\n" + safety.wrap("callee specs", json.dumps(callees, indent=1)) + "\n")


def _action_block(actions: list[dict], omitted: list[dict] | None = None) -> str:
    if not actions and not omitted:
        return ""
    tree = any(a.get("depth", 1) > 1 for a in actions)
    out = ("\nACTIONS (script modules) THIS ONE " + (
        "REACHES, directly or through other actions, with their source. `depth` 1 is called by the workflow, "
        "2 by a depth-1 action, and so on; `called_from` says by which:\n" if tree or omitted else "CALLS, with their source:\n")
        + safety.wrap("action sources", json.dumps(actions, indent=1)) + "\n")
    if omitted:
        out += ("\nACTIONS LEFT OUT to keep this prompt within its size budget. The workflow reaches them, but "
                "their source is not shown; do not guess what they do, and say in `open_questions` where one "
                "matters:\n" + safety.wrap("omitted actions", json.dumps(omitted, indent=1)) + "\n")
    return out


def _entry_block(entry: list[dict]) -> str:
    if not entry:
        return ""
    return ("\nWHAT STARTS THIS WORKFLOW, from the vRA export or the owner's entry-points file (facts, not "
            "questions: do not ask whether these are blocking or what their timeout is):\n"
            + safety.wrap("entry points", json.dumps(entry, indent=1)) + "\n")


# What an estate prompt asks beyond the benchmark's: the elements and the workflows sharing state are facts,
# and two more fields of the spec.
ESTATE_RULES = """
What this prompt shows besides the workflow is fact, not guesswork: the configuration and resource elements its code opens (attribute names and types always; values only where the export holds them, and a sanitized export holds none, so read the names and types as the shape), and the specs of the workflows that share state with this one. Do not ask in `open_questions` for anything they show: an attribute's name or type, a template's text, who writes a record. A value that is absent because the export has none is still worth a question when it changes the conversion.

For `suspected_defects`, list behaviour in the code that looks unintended: a check that is computed but never used, a cleanup that can remove something this run did not create, one value used for every item where each item has its own, a condition that skips work a later step needs. Give `what`, `evidence` (the item or action and the expression), and `question`, a yes or no question that lets the owner say whether to keep the behaviour or fix it. Put a suspected defect here and not in `assumptions` or `side_effects`. Leave the list empty when nothing looks unintended.

For `state_records`, list every record this workflow writes or reads that outlives the run (an attribute of a configuration element, a resource element): `where` (the element and the attribute key or naming rule), `shape` (its fields and types), and `written_when`. Leave it empty when it keeps none.
"""


def _elements_block(elements: list[dict]) -> str:
    if not elements:
        return ""
    return ("\nELEMENTS THIS WORKFLOW READS: the configuration and resource elements in the categories its code or "
            "its actions open, as the export holds them:\n" + safety.wrap("elements", json.dumps(elements, indent=1)) + "\n")


def _sharing_block(sharing: list[dict]) -> str:
    if not sharing:
        return ""
    return ("\nWORKFLOWS SHARING STATE WITH THIS ONE (they read or write the same elements; where one writes what this "
            "one reads, its spec comes first and is shown, cut to what it keeps; a workflow specified alongside this "
            "one is named only):\n" + safety.wrap("workflows sharing state", json.dumps(sharing, indent=1)) + "\n")


def spec_prompt(m: dict, callees: list[dict], acts: list[dict], omitted: list[dict] | None = None,
                entry: list[dict] | None = None, elements: list[dict] | None = None,
                sharing: list[dict] | None = None, estate: bool = False) -> str:
    """The Understand prompt for one workflow. Everything from the export is an untrusted block. `estate`:
    the estate prompt (ESTATE_RULES, elements, workflows sharing state); without it, the benchmark's."""
    return safety.guard(PROMPT.format(
        extra_rules=ESTATE_RULES if estate else "",
        model_json=safety.wrap("workflow", json.dumps(_trim_model(m), indent=1)),
        callee_block=_callee_block(callees),
        action_block=_action_block(acts, omitted) + _entry_block(entry or []) + _elements_block(elements or [])
        + _sharing_block(sharing or [])))


# Elements a prompt shows: every element in a category the workflow or an action it reaches opens. A text
# resource is cut at RESOURCE_TEXT_MAX (parse/corpus.py keeps that much) and all of them at ELEMENT_BUDGET.
ELEMENT_BUDGET = 48_000


def elements_for(scripts: list[str], other_elements: list[dict], budget: int | None = None) -> list[dict]:
    """The configuration and resource elements in the categories these scripts open, as a prompt shows them:
    a configuration element's attributes with name and type, and the value only where the export has one
    (never a SecureString's; vRO's null sentinel, which is also what sanitize leaves, shows as no value); a
    resource element's name, mime type and text."""
    from kestra_migration_assistant.vra.parse.jscalls import element_paths
    budget = ELEMENT_BUDGET if budget is None else budget
    wanted = []
    for s in scripts:
        for k in element_paths(s):
            if k not in wanted:
                wanted.append(k)
    out, used = [], 0
    for kind, path in wanted:
        for e in sorted((e for e in other_elements if e["kind"] == kind
                         and (e.get("category_path") or "").strip(".") == path), key=lambda e: e["name"]):
            rec = {"kind": "configuration element" if kind == "ConfigurationElement" else "resource element",
                   "category": path.replace(".", "/"), "name": e["name"]}
            if kind == "ConfigurationElement":
                attrs = []
                for a in e.get("attributes") or []:
                    x = {"name": a["name"], "type": a["type"]}
                    if a.get("is_secure"):
                        x["secret"] = True
                    elif a.get("value") not in (None, "") and not a.get("is_null_sentinel"):
                        x["value"] = a["value"]
                    attrs.append(x)
                rec["attributes"] = attrs
            else:
                rec["mime"] = e.get("mime") or ""
                if e.get("binary"):
                    rec["binary"] = True
                elif e.get("text") is not None:
                    text = e["text"]
                    if used + len(text) > budget:
                        rec["text_left_out"] = "prompt size budget reached"
                    else:
                        used += len(text)
                        rec["text"] = text
                        if e.get("text_cut"):
                            rec["text_cut"] = True
            out.append(rec)
    return out


# The call tree a spec prompt carries: every action the workflow reaches, breadth first (direct calls
# first), until the sources shown add up to ACTION_BUDGET characters. One action's source is cut at
# ACTION_SOURCE_MAX. What does not fit is named in the prompt and recorded in the spec (`context`).
ACTION_BUDGET = 60_000
ACTION_SOURCE_MAX = 6_000


def action_tree(m: dict, actions_by_module: dict, budget: int | None = None,
                per_action: int | None = None) -> tuple[list[dict], list[dict], list[str]]:
    """(included, omitted, truncated) for one workflow. Actions are found by module and name, their calls
    read from their source (parse/jscalls.py), so a call through a module variable counts too."""
    from kestra_migration_assistant.vra.parse.jscalls import module_calls
    budget = ACTION_BUDGET if budget is None else budget
    per_action = ACTION_SOURCE_MAX if per_action is None else per_action

    def find(mod, act):
        return next((c for c in actions_by_module.get(f"{mod}/{act}", [])), None) or \
            next((c for c in actions_by_module.get(act or "", []) if (c.get("category_path") or "") == mod), None)

    queue, seen = [], set()
    for a in m.get("action_calls", []):
        k = (a["module"], a.get("action") or "")
        if k not in seen:
            seen.add(k)
            queue.append((k, 1, None, a.get("item")))
    included, omitted, truncated, used = [], [], [], 0
    i = 0
    while i < len(queue):
        (mod, act), depth, parent, item = queue[i]
        i += 1
        c = find(mod, act)
        rec = {"module": mod, "action": act, "depth": depth}
        if item:
            rec["called_at_item"] = item
        if parent:
            rec["called_from"] = parent
        if c is None:
            omitted.append(dict(rec, why="not in the export"))
            continue
        src = c.get("script") or ""
        if len(src) > per_action:
            truncated.append(f"{mod}/{act}")
            src = src[:per_action]
        if used + len(src) > budget:
            omitted.append(dict(rec, why=f"size budget ({budget:,} characters of action source) reached"))
        else:
            used += len(src)
            included.append(dict(rec, source=src))
        for cm, ca in module_calls(c.get("script") or ""):
            k = (cm, ca)
            if k not in seen:
                seen.add(k)
                queue.append((k, depth + 1, f"{mod}/{act}", None))
    return included, omitted, truncated


def state_deps(groups: list[dict] | None, state: dict | None) -> dict[str, set[str]]:
    """Inside a lifecycle group, a workflow that reads a state path it does not write waits for the workflows
    that write it: their specs say what the record holds. Two workflows that both write a path wait for
    neither (a circle): each is named to the other, without a spec."""
    out: dict[str, set[str]] = defaultdict(set)
    for g in groups or []:
        for u in g["members"]:
            su = (state or {}).get(u) or {}
            reads_only = set(su.get("reads") or []) - set(su.get("writes") or [])
            for v in g["members"]:
                if v != u and reads_only & set(((state or {}).get(v) or {}).get("writes") or []):
                    out[u].add(v)
    return out


def sharing_with(u: str, groups: list[dict] | None, state: dict | None) -> list[tuple[str, list[str]]]:
    """(other member, state paths both touch) for every member of u's lifecycle group that shares one."""
    su = (state or {}).get(u) or {}
    mine = set(su.get("reads") or []) | set(su.get("writes") or [])
    out = []
    for g in groups or []:
        if u not in g["members"]:
            continue
        for v in g["members"]:
            sv = (state or {}).get(v) or {}
            both = mine & (set(sv.get("reads") or []) | set(sv.get("writes") or []))
            if v != u and both:
                out.append((v, sorted(both)))
    return out


def dependency_levels(uids: list[str], models: dict, call_graph: dict,
                      extra: dict[str, set[str]] | None = None) -> list[list[str]]:
    """
    Group into levels so every callee is in an earlier level than its callers (and, with `extra`, every
    workflow after the ones it waits for: state_deps). Everything inside one level is independent and can
    run in parallel.
    """
    wid_to_uid = {models[u]["wid"]: u for u in uids}
    deps: dict[str, set[str]] = {u: set() for u in uids}
    for e in call_graph["edges"]:
        src, dst = wid_to_uid.get(e["from"]), wid_to_uid.get(e["to"])
        if src in deps and dst in deps and src != dst:
            deps[src].add(dst)
    for u, vs in (extra or {}).items():
        if u in deps:
            deps[u] |= {v for v in vs if v in deps and v != u}

    levels, done = [], set()
    while len(done) < len(uids):
        ready = sorted(u for u in uids if u not in done and deps[u] <= done)
        if not ready:                      # a cycle: break it deterministically
            ready = [sorted(u for u in uids if u not in done)[0]]
        levels.append(ready)
        done |= set(ready)
    return levels


def order_leaves_first(uids: list[str], models: dict, call_graph: dict) -> list[str]:
    """Topological order so every callee precedes its callers. Cycles broken by uid."""
    wid_to_uid = {models[u]["wid"]: u for u in uids}
    deps: dict[str, set[str]] = {u: set() for u in uids}
    for e in call_graph["edges"]:
        src, dst = wid_to_uid.get(e["from"]), wid_to_uid.get(e["to"])
        if src in deps and dst in deps and src != dst:
            deps[src].add(dst)

    ordered, done = [], set()
    while len(ordered) < len(uids):
        ready = sorted(u for u in uids if u not in done and deps[u] <= done)
        if not ready:                      # a cycle: break it deterministically
            ready = [sorted(u for u in uids if u not in done)[0]]
        for u in ready:
            ordered.append(u)
            done.add(u)
    return ordered


def actions_index(idx: dict) -> dict[str, list[dict]]:
    """The parse's actions by `module/name` and by bare name."""
    out = defaultdict(list)
    for e in idx["other_elements"]:
        if e["kind"] in ("ScriptModule", "ScriptingAction"):
            out[f"{e['category_path']}/{e['name']}"].append(e)
            out[e["name"]].append(e)
    return out


def run(out_dir: str, uids: list[str], spec_dir: str | None = None, call_tree: bool = False,
        entry_points: dict | None = None, regenerate: set | None = None, dry: bool = False,
        lifecycle: list[dict] | None = None, state: dict | None = None) -> dict:
    """Specs for `uids`, leaves first. With `call_tree`, each prompt carries every action the workflow
    reaches (action_tree) and what starts it (`entry_points`: uid -> list); without it, only the actions it
    calls directly (the closed benchmark's prompt, unchanged). `regenerate`: uids whose spec on disk is
    replaced. `dry`: call no model; build each prompt from the specs on disk and return what each would
    carry (`contexts`) and its fingerprint (`prompt_sha`), so a caller can tell a spec whose input changed.
    With `call_tree`, `lifecycle` (Assess's lifecycle groups) and `state` (uid -> state paths read and
    written) order a group's writers before its readers and show each reader the writers' specs."""
    spec_dir = spec_dir or os.path.join(out_dir, "spec")
    os.makedirs(spec_dir, exist_ok=True)
    idx = json.load(open(os.path.join(out_dir, "index.json")))
    models = {u: json.load(open(os.path.join(out_dir, "model", f"{u}.json")))
              for u in uids}
    actions_by_module = actions_index(idx)

    levels = dependency_levels(uids, models, idx["call_graph"],
                               state_deps(lifecycle, state) if call_tree else None)
    order = [u for lvl in levels for u in lvl]
    wid_to_uid = {models[u]["wid"]: u for u in uids}
    specs: dict[str, dict] = {}
    errors: dict[str, str] = {}
    context: dict[str, dict] = {}
    shas: dict[str, str] = {}

    def build_prompt(uid: str) -> str:
        text = _build_prompt(uid)
        shas[uid] = prompt_sha(text)
        if uid in context:
            context[uid]["prompt_sha"] = shas[uid]
        return text

    def _build_prompt(uid: str) -> str:
        m = models[uid]

        callees = []
        for e in idx["call_graph"]["edges"]:
            if e["from"] != m["wid"]:
                continue
            cu = wid_to_uid.get(e["to"])
            if cu and cu in specs:
                callees.append({"workflow": models[cu]["name"], "called_at_item": e["item"],
                                "mode": e["mode"], "spec": specs[cu]})

        if call_tree:
            acts, omitted, truncated = action_tree(m, actions_by_module)
            context[uid] = {"actions_included": len(acts), "action_depth": max((a["depth"] for a in acts), default=0),
                            "actions_omitted": [f"{a['module']}/{a['action']} ({a['why']})" for a in omitted],
                            "sources_cut": truncated}
            entry = (entry_points or {}).get(uid) or []
            context[uid]["entry_points"] = len(entry)
            scripts = [i.get("script") or "" for i in m.get("items", [])] + [a["source"] for a in acts]
            elements = elements_for(scripts, idx["other_elements"])
            context[uid]["elements"] = [f"{e['category']}/{e['name']}" for e in elements]
            sharing = []
            for v, paths in sharing_with(uid, lifecycle, state):
                rec = {"workflow": models[v]["name"] if v in models else v, "shares": paths}
                if v in specs:
                    sv = specs[v]
                    rec.update({"purpose": sv.get("purpose"), "side_effects": sv.get("side_effects") or [],
                                "state_records": sv.get("state_records") or []})
                else:
                    rec["spec"] = "specified alongside this one: not shown"
                sharing.append(rec)
            context[uid]["shares_state_with"] = [f"{r['workflow']} ({'spec' if 'purpose' in r else 'name only'})"
                                                 for r in sharing]
            return spec_prompt(m, callees, acts, omitted, entry, elements, sharing, estate=True)

        acts = []
        seen = set()
        for a in m.get("action_calls", []):
            key = a["module"] + "/" + (a.get("action") or "")
            if key in seen:
                continue
            seen.add(key)
            cand = actions_by_module.get(a["module"]) or \
                actions_by_module.get((a.get("action") or "")) or []
            for c in cand[:1]:
                acts.append({"module": a["module"], "action": a.get("action"),
                             "called_at_item": a["item"],
                             "source": (c.get("script") or "")[:4000]})

        return spec_prompt(m, callees, acts)

    from kestra_migration_assistant.vra.stages import common as C
    to_write = [u for u in order if not os.path.exists(os.path.join(spec_dir, f"{u}.json")) or u in (regenerate or set())]
    written = {"n": 0}
    lock = threading.Lock()

    def work(uid: str):
        t0 = time.monotonic()
        spec = M.ask("understand", uid, build_prompt(uid), ESTATE_SPEC_SCHEMA if call_tree else SPEC_SCHEMA)
        if uid in context:
            # what the prompt carried, and what it left out: a reader of the spec sees the gaps
            spec = dict(spec, context=context[uid])
        with open(os.path.join(spec_dir, f"{uid}.json"), "w") as f:
            json.dump(spec, f, indent=1)
        with lock:
            written["n"] += 1
            C.progress(f"wrote spec {written['n']} of {len(to_write)}: {models[uid].get('name') or uid} "
                       f"({time.monotonic() - t0:.1f}s)")
        return spec

    if dry:
        for lvl in levels:
            for uid in lvl:                # a level's prompts see only earlier levels' specs, as in a real run
                build_prompt(uid)
            for uid in lvl:
                path = os.path.join(spec_dir, f"{uid}.json")
                if os.path.exists(path):
                    specs[uid] = json.load(open(path))
        return {"contexts": context, "prompt_sha": shas, "order": order}

    if to_write:
        C.progress(f"writing {len(to_write)} spec{'s' if len(to_write) != 1 else ''}, "
                   f"up to {min(M.MAX_PARALLEL, len(to_write))} at a time")
    for lvl in levels:
        todo = []
        for uid in lvl:
            path = os.path.join(spec_dir, f"{uid}.json")
            if os.path.exists(path) and uid not in (regenerate or set()):
                specs[uid] = json.load(open(path))
            else:
                todo.append(uid)
        if not todo:
            continue
        got, errs = M.run_parallel(todo, work)
        specs.update(got)
        errors.update(errs)

    return {"specs": sorted(specs), "errors": errors, "order": order,
            "levels": [len(l) for l in levels]}


def cluster_members(c: dict, spec_dir: str) -> list[dict]:
    """What a cluster summary is written from: each member's spec, abridged."""
    members = []
    for u in c["members"]:
        p = os.path.join(spec_dir, f"{u}.json")
        if not os.path.exists(p):
            continue
        s = json.load(open(p))
        members.append({"uid": u, "purpose": s["purpose"],
                        "inputs": [i["name"] for i in s["inputs"]],
                        "outputs": [o["name"] for o in s["outputs"]],
                        "decisions": len(s["decisions"])})
    return members


def cluster_prompt(c: dict, members: list[dict]) -> str:
    return safety.guard(
        "These VMware vRealize Orchestrator workflows were grouped by a "
        "fingerprint over their item-type sequence, graph shape, plugin object "
        "types, SDK calls, input and output signature, and normalized script "
        "text. Here are their specifications.\n\n"
        "Name the group in three to six words, say plainly what the group does, "
        "and say for each member what makes it different from the others. Then "
        f"say why `{c['first_of_a_kind']}` is a reasonable one to convert first "
        "and use as the template for the rest. If the grouping looks wrong, say "
        "so in `what_the_group_does`.\n\n"
        f"MEMBERS\n{safety.wrap('member specs', json.dumps(members, indent=1))}\n\nReturn JSON.")


def run_clusters(out_dir: str, label: str) -> dict:
    """One summary per cluster, written after the member specs exist."""
    res = json.load(open(os.path.join(out_dir, "assess", f"clusters-{label}.json")))
    spec_dir = os.path.join(out_dir, "spec")
    out_path = os.path.join(out_dir, "assess", f"cluster-summaries-{label}.json")
    summaries = json.load(open(out_path)) if os.path.exists(out_path) else {}

    for c in res["clusters"]:
        cid = c["cluster_id"]
        if cid in summaries:
            continue
        members = cluster_members(c, spec_dir)
        if not members:
            continue
        prompt = cluster_prompt(c, members)
        try:
            summaries[cid] = M.ask("cluster-summary", cid, prompt, CLUSTER_SCHEMA)
        except Exception as e:  # noqa: BLE001
            summaries[cid] = {"name": cid, "what_the_group_does": f"(failed: {e})",
                              "how_members_differ": [], "first_of_a_kind_reason": ""}
        with open(out_path, "w") as f:
            json.dump(summaries, f, indent=1)
    return summaries


# ---------------------------------------------------------------- per estate

def run_estate() -> dict:
    """
    Specs and cluster summaries for the configured estate (`vro.py understand --estate X`).

    Reuse first, then generate only what is missing:
    1. a spec already in the estate's data/2-understand/ is kept;
    2. otherwise it is generated through `run()` above, leaves first, so a caller's
       prompt carries its callees' specs. Each call is cached and logged with its cost.

    Cluster summaries: kept if one exists for the same member set; otherwise
    generated with the same prompt as `run_clusters`.
    """
    from kestra_migration_assistant.vra.stages import common as C

    C.require_estate()
    idx_path = os.path.join(C.WORK, "index.json")
    clusters_path = os.path.join(C.WORK, "assess", "clusters-estate.json")
    if not (os.path.exists(idx_path) and os.path.exists(clusters_path)):
        raise RuntimeError("run `vro.py assess --estate %s` first: understand reads its parse and clusters" % C.ESTATE)
    idx = json.load(open(idx_path))
    uids = sorted(w["uid"] for w in idx["workflows"])
    os.makedirs(C.SPEC_DIR, exist_ok=True)
    prov_path = os.path.join(C.SPEC_DIR, "PROVENANCE.json")
    prov = json.load(open(prov_path)) if os.path.exists(prov_path) else {"specs": {}, "cluster_summaries": {}}

    M_ = C.model()                      # cache and call log under stages/
    log_before = _log_len(C.STAGE_CALL_LOG)
    # A spec this command generated is stale when the prompt it came from has changed shape (PROMPT_CONTEXT:
    # the call tree and entry points it now carries), or when what that prompt would carry today differs from
    # what it carried then (input_changes). It is regenerated, and the old one kept beside it.
    stale_why = stale_specs(uids, prov)
    stale = set(stale_why)
    missing = [u for u in uids if not os.path.exists(os.path.join(C.SPEC_DIR, f"{u}.json")) or u in stale]
    errors: dict = {}
    if missing and not os.environ.get("VRO_NO_MODEL"):
        for u in sorted(stale):
            old_dir = os.path.join(C.SPEC_DIR, "previous")
            os.makedirs(old_dir, exist_ok=True)
            os.replace(os.path.join(C.SPEC_DIR, f"{u}.json"), os.path.join(old_dir, f"{u}.json"))
        res = run(C.WORK, uids, spec_dir=C.SPEC_DIR, call_tree=True, entry_points=_entry_points_by_uid(),
                  **_shared_state())
        errors = res["errors"]
        for u in missing:
            if os.path.exists(os.path.join(C.SPEC_DIR, f"{u}.json")):
                prov["specs"][u] = {"source": "generated by vro.py understand (kestra_migration_assistant/vra/understand.py PROMPT)",
                                    "generated": C.now(), "tier": M_.tier_for("understand"),
                                    "context": PROMPT_CONTEXT}
    elif missing:
        errors = {u: ("no spec and --no-model" if u not in stale else f"spec is stale ({stale_why[u]}); kept, "
                      "since --no-model") for u in missing}
    generated = 0 if os.environ.get("VRO_NO_MODEL") else \
        sum(1 for u in missing if os.path.exists(os.path.join(C.SPEC_DIR, f"{u}.json")) and u not in errors)

    # cluster summaries
    clusters = json.load(open(clusters_path))["clusters"]
    have = json.load(open(C.CLUSTER_SUMMARIES)) if os.path.exists(C.CLUSTER_SUMMARIES) else {}
    by_members = {tuple(sorted(v.get("members") or [])): v for v in have.values()}
    out, s_generated = {}, 0
    for c in clusters:
        key = tuple(sorted(c["members"]))
        cid = c["cluster_id"]
        fresh = set(key) & {u for u in missing if os.path.exists(os.path.join(C.SPEC_DIR, f"{u}.json"))
                            and not os.environ.get("VRO_NO_MODEL")}
        if key in by_members and not fresh:
            out[cid] = by_members[key]
            continue
        members = cluster_members(c, C.SPEC_DIR)
        if not members or os.environ.get("VRO_NO_MODEL"):
            continue
        t0 = time.monotonic()
        try:
            v = M.ask("cluster-summary", f"{C.ESTATE}/{cid}", cluster_prompt(c, members), CLUSTER_SCHEMA)
        except Exception as e:  # noqa: BLE001
            errors[f"cluster {cid}"] = f"{type(e).__name__}: {e}"
            continue
        out[cid] = {**v, "members": list(key)}
        C.progress(f"wrote the summary of group {cid} ({time.monotonic() - t0:.1f}s)")
        prov["cluster_summaries"][cid] = {"source": "generated by vro.py understand (cluster_prompt)",
                                          "generated": C.now(), "members": list(key)}
        s_generated += 1
    # written only when they change: Assess re-runs when either is newer than its output
    for path, obj in ((C.CLUSTER_SUMMARIES, out), (prov_path, prov)):
        text = json.dumps(obj, indent=1)
        if not os.path.exists(path) or open(path).read() != text:
            with open(path, "w") as f:
                f.write(text)

    new = _log_since(C.STAGE_CALL_LOG, log_before)
    billed = [r for r in new if not r.get("cached") and not r.get("event")]
    return {"generated": generated, "errors": errors, "stale": stale_why,
            "summaries_generated": s_generated,
            "billed_calls": len(billed), "cost_usd": round(sum(r.get("cost_usd") or 0 for r in billed), 4)}


# What an estate spec prompt carries beyond the workflow; a change here makes generated specs stale.
# What a spec's recorded context says its prompt carried; compared with today's when a spec has no prompt_sha.
CONTEXT_KEYS = ("actions_included", "action_depth", "actions_omitted", "sources_cut", "entry_points", "elements",
                "shares_state_with")


def stale_specs(uids: list[str] | None = None, prov: dict | None = None) -> dict[str, str]:
    """
    uid -> why, for every spec this tool generated whose prompt would differ today: the prompt changed shape
    (PROMPT_CONTEXT), or what it carries changed (input_changes). No model is called, so `status` and `brief`
    can say which specs describe other code than the estate's.
    """
    from kestra_migration_assistant.vra.stages import common as C
    if uids is None:
        idx_path = os.path.join(C.WORK, "index.json")
        if not os.path.exists(idx_path):
            return {}
        uids = sorted(w["uid"] for w in json.load(open(idx_path))["workflows"])
    if prov is None:
        pp = os.path.join(C.SPEC_DIR, "PROVENANCE.json")
        prov = json.load(open(pp)) if os.path.exists(pp) else {"specs": {}}
    generated = {u for u in uids if os.path.exists(os.path.join(C.SPEC_DIR, f"{u}.json"))
                 and (prov["specs"].get(u) or {}).get("source", "").startswith("generated")}
    why = {u: "the prompt changed shape" for u in generated
           if (prov["specs"].get(u) or {}).get("context") != PROMPT_CONTEXT}
    why.update(input_changes(uids, generated - set(why)))
    return why


def prompt_sha(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def input_changes(uids: list[str], check: set[str]) -> dict[str, str]:
    """
    Of the generated specs in `check`, those whose prompt would differ today, uid -> why. A spec written with a
    prompt fingerprint is compared by it; an older one by what its recorded context says the prompt carried
    (the actions included and left out, the entry points). No model is called.
    """
    from kestra_migration_assistant.vra.stages import common as C
    if not check:
        return {}
    probe = run(C.WORK, uids, spec_dir=C.SPEC_DIR, call_tree=True, entry_points=_entry_points_by_uid(), dry=True,
                **_shared_state())
    out = {}
    for u in sorted(check):
        ctx = (json.load(open(os.path.join(C.SPEC_DIR, f"{u}.json"))).get("context") or {})
        now = probe["contexts"].get(u) or {}
        if ctx.get("prompt_sha"):
            if ctx["prompt_sha"] != probe["prompt_sha"].get(u):
                out[u] = "its prompt's input changed since it was written"
            continue
        diff = [k for k in CONTEXT_KEYS if ctx.get(k) != now.get(k)]
        if diff:
            out[u] = "its prompt's input changed since it was written (" + ", ".join(
                f"{k}: {ctx.get(k)!r} then, {now.get(k)!r} now" for k in diff) + ")"
    return out


PROMPT_CONTEXT = (f"call tree (budget {ACTION_BUDGET}, per action {ACTION_SOURCE_MAX}) + entry points"
                  f" + elements (budget {ELEMENT_BUDGET}) + shared state + suspected defects")


def _shared_state() -> dict:
    """Assess's lifecycle groups and each workflow's state paths, for run()'s lifecycle and state."""
    from kestra_migration_assistant.vra.stages import common as C
    p = os.path.join(C.ASSESS, "assess.json")
    if not os.path.exists(p):
        return {"lifecycle": [], "state": {}}
    a = json.load(open(p))
    return {"lifecycle": a.get("lifecycle_groups") or [],
            "state": {u: w.get("state") or {} for u, w in (a.get("workflows") or {}).items()}}


def _entry_points_by_uid() -> dict:
    """uid -> the entry points that start it, from Assess (empty when Assess has none)."""
    from kestra_migration_assistant.vra.stages import common as C
    p = os.path.join(C.ASSESS, "assess.json")
    if not os.path.exists(p):
        return {}
    wf = json.load(open(p)).get("workflows") or {}
    return {u: w.get("entry_points") or [] for u, w in wf.items()}


def _log_len(path: str) -> int:
    if not os.path.exists(path):
        return 0
    with open(path) as f:
        return sum(1 for _ in f)


def _log_since(path: str, n: int) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path) as f:
        lines = f.readlines()[n:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
