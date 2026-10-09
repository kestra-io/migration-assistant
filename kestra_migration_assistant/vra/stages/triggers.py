"""
Triggers, kept apart from logic.

A workflow started by a vRA event subscription has two parts that migrate differently: the trigger
(what starts it, whether vRA waits for it, under which condition) and the logic (what it does once
started). Triage decides the outcome on the logic alone. This module holds everything about the
trigger side, all from code and the entry points on record, no model:

- `position`: where an event sits in a deployment's life (request, each machine, removal, delete), from
  its topic and, for deployment.request.*, the event type its condition tests.
- `plan`: for every entry point, what starts it today and how that maps in Kestra, both if vRA stays
  (vRA calls a Kestra webhook) and if vRA is replaced (the catalog item becomes a Kestra App, the cloud
  template a flow, each lifecycle event a step in that flow).
- `event_branch_values`: the event types a workflow's own logic branches on. Two or more make it a
  dispatcher, whose logic only makes sense inside vRA (triage: Rebuild).
- `lifecycle_groups`: workflows started by lifecycle events that share state one of them writes. They
  form one deployment's life and migrate in the same wave.
- `notes`: blocking subscriptions, and one workflow run by several subscriptions.

The Kestra names used here (the Webhook trigger and its wait / returnOutputs, the Apps Execution app,
the KV tasks) are checked against the plugin snapshot bundled in data/plugins/ by the plugin
coverage view, which shows the date it was taken.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

# Front-end choice (preflight Q9). BRIDGE: vRA is replaced, and until then its subscriptions call Kestra's
# webhooks, so the converted flows run behind vRA in between.
KEEP, REPLACE, BOTH, BRIDGE = "keep", "replace", "both", "bridge"
REPLACING = (REPLACE, BRIDGE)

WEBHOOK = "io.kestra.plugin.core.trigger.Webhook"
APP = "io.kestra.plugin.ee.apps.Execution"
KV_TASKS = ("io.kestra.plugin.core.kv.Set", "io.kestra.plugin.core.kv.Get", "io.kestra.plugin.core.kv.Delete")

DESTROY = re.compile(r"DESTROY|DELETE", re.I)
CREATE = re.compile(r"CREATE|PROVISION", re.I)
FAILED = re.compile(r"FAILED|CANCEL", re.I)

# (topic pattern, needs a destroy condition, position, phase key)
# Position orders events along one deployment's life; phase groups them for the vRA view.
TOPICS = [
    (r"^deployment\.request\.pre$", False, 10, "request"),
    (r"^(catalog|blueprint)\.", False, 11, "request"),
    (r"^compute\.allocation\.pre$", None, 20, "machine"),
    (r"^compute\.allocation\.post$", None, 21, "machine"),
    (r"^network\.(configure|provision)", None, 22, "machine"),
    (r"^compute\.provision\.pre$", None, 30, "machine"),
    (r"^compute\.provision\.post$", None, 31, "machine"),
    (r"^compute\.initial\.power\.on$", None, 32, "machine"),
    (r"^deployment\.request\.post$", False, 40, "request"),
    (r"^(deployment\.resource\.action|deployment\.action|resource\.action)", None, 50, "day2"),
    (r"^deployment\.request\.pre$", True, 60, "delete"),
    (r"^compute\.removal\.pre$", None, 70, "removal"),
    (r"^compute\.removal\.post$", None, 71, "removal"),
    (r"^deployment\.request\.post$", True, 80, "delete"),
]
PHASES = {"request": "Request", "machine": "Each machine", "day2": "Day-2 action", "removal": "Machine removal",
          "delete": "Delete", "other": "Other event"}
PHASE_ORDER = ["request", "machine", "day2", "removal", "delete", "other"]
STAGE_LABEL = {10: "Request, before it runs", 11: "Request", 20: "Each machine, before placement",
               21: "Each machine, after placement", 22: "Each machine, network", 30: "Each machine, before create",
               31: "Each machine, after create", 32: "Each machine, first power-on", 40: "Request finished",
               50: "Day-2 action", 60: "Delete, before it runs", 70: "Each machine, before removal",
               71: "Each machine, after removal", 80: "Delete finished", 90: "Other event"}


def stage_label(pos: int) -> str:
    return STAGE_LABEL.get(pos, "Other event")

EVENT_VAR = r"[\w$.]*(?:event_?type|event_?topic(?:_?id)?|topic_?id|__metadata_eventTopicId)[\w$]*"
CMP = re.compile(r"(?i)\b(" + EVENT_VAR + r")\s*(?:===?|!==?)\s*[\"']([^\"'\n]+)[\"']|"
                 r"[\"']([^\"'\n]+)[\"']\s*(?:===?|!==?)\s*(" + EVENT_VAR + r")\b")
SWITCH = re.compile(r"(?i)\bswitch\s*\(\s*" + EVENT_VAR + r"\s*\)\s*\{")
CASE = re.compile(r"\bcase\s+[\"']([^\"'\n]+)[\"']\s*:")


def position(topic: str | None, condition: str | None) -> tuple[int, str]:
    """(position along a deployment's life, phase key) for an event topic and its condition."""
    t = (topic or "").strip().lower()
    cond = condition or ""
    destroy = bool(DESTROY.search(cond)) and not CREATE.search(cond)
    for rx, needs_destroy, pos, phase in TOPICS:
        if re.search(rx, t) and (needs_destroy is None or needs_destroy == destroy):
            return pos, phase
    return 90, "other"


def is_failed_cleanup(s: dict) -> bool:
    """A subscription on a finished request that runs only when it failed or was canceled: the create's cleanup."""
    pos = position(s.get("event_topic"), s.get("condition"))[0]
    return pos == 40 and bool(FAILED.search(s.get("condition") or ""))


def started_by_vra(w: dict) -> bool:
    """Something in vRA starts the workflow: a subscription, a catalog item or a day-2 action."""
    return any(e.get("kind") in ("subscription", "catalog item", "day2 action") and not e.get("disabled")
               for e in w.get("entry_points") or [])


def front_end(answer: str | None) -> str:
    """Preflight Q9: keep vRA as the front end, replace it, replace it with a bridge until then, or not
    answered (both plans shown)."""
    a = (answer or "").lower()
    # "no bridge", "without a bridge", "not through a bridge" say there is none
    bridge = re.sub(r"\b(no|without|not|never)\b(\s+\w+){0,2}?\s+bridge\w*", " ", a)
    if re.search(r"\bbridge", bridge) and re.search(r"\breplac", a):
        return BRIDGE
    rep = bool(re.search(r"\breplac|\bretire|\bdecommission|\bwithout vra|\bdrop vra", a))
    keep = bool(re.search(r"\bkeep|\bstays?\b|\bremain", a))
    if rep and not keep:
        return REPLACE
    if keep and not rep:
        return KEEP
    return BOTH


# ------------------------------------------------------------------ logic signals

def event_branch_values(items: list[dict]) -> list[str]:
    """Event types or topics the workflow's own items branch on (scripts and switch/decision items)."""
    vals = set()
    for it in items or []:
        s = it.get("script") or ""
        for m in CMP.finditer(s):
            vals.add(m.group(2) or m.group(3))
        for m in SWITCH.finditer(s):
            depth, i = 1, m.end()
            while i < len(s) and depth:
                depth += {"{": 1, "}": -1}.get(s[i], 0)
                i += 1
            vals.update(CASE.findall(s[m.end():i]))
        for c in it.get("conditions") or []:
            if re.fullmatch(r"(?i)" + EVENT_VAR, c.get("name") or "") and (c.get("value") or "").strip():
                vals.add(c["value"].strip())
    return sorted(v for v in vals if v and v != "__NULL__")


# Keys of vRA's event broker payload (deployment.*, compute.*, network.* topics), the ones a workflow reads
# to find out what happened. Reading one of them from the payload is parsing vRA's event.
PAYLOAD_KEYS = {"deploymentId", "deploymentName", "eventType", "status", "requestId", "requestInputs", "projectId",
                "projectName", "userName", "blueprintId", "catalogItemId", "resourceIds", "resourceNames",
                "addresses", "externalIds", "customProperties", "componentId", "componentTypeId", "endpointId",
                "zoneId", "flavor", "image", "hostSelections", "networkSelection", "tags", "__metadata"}
PAYLOAD_GET = re.compile(r"\b(inputProperties|payload)\s*(?:\.\s*get\s*\(\s*|\[\s*)[\"']([\w$]+)[\"']")


def payload_keys(script: str, params: dict | None = None) -> list[str]:
    """The vRA event payload keys a script reads from `inputProperties` (or `payload`): `x.get("key")` or
    `x["key"]`, for a key vRA's payload has."""
    keys = {m.group(2) for m in PAYLOAD_GET.finditer(script or "")}
    return sorted(k for k in keys if k in PAYLOAD_KEYS or k.startswith("__metadata"))


def state_paths(ops: list[dict]) -> tuple[set, set]:
    """(configuration element paths read, paths written) in one set of operations. A path counts as
    written when the same set holds a configuration element write: the element was read to be written."""
    reads = {o["op"][len("read configuration element "):] for o in ops
             if o.get("kind") == "configuration element" and o["op"].startswith("read configuration element ")}
    writes = any(o.get("kind") == "configuration element" and o["op"].startswith("write") for o in ops)
    return reads, (set(reads) if writes else set())


def lifecycle_groups(wf: dict[str, dict], state: dict[str, dict]) -> list[dict]:
    """Workflows started by deployment lifecycle events that share state one of them writes.

    state: uid -> {"reads": [...], "writes": [...]} (configuration element paths, see state_paths).
    """
    cand = {}
    for u, w in wf.items():
        subs = [e for e in w.get("entry_points") or [] if e.get("kind") == "subscription"]
        pos = sorted({position(e.get("event_topic"), e.get("condition")) for e in subs})
        pos = [p for p in pos if p[1] != "other"]
        if pos:
            cand[u] = pos
    written = {p for u in cand for p in (state.get(u) or {}).get("writes", [])}
    parent = {u: u for u in cand}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    by_path = defaultdict(list)
    for u in cand:
        for p in set((state.get(u) or {}).get("reads", [])) | set((state.get(u) or {}).get("writes", [])):
            if p in written:
                by_path[p].append(u)
    for us in by_path.values():
        for v in us[1:]:
            parent[find(v)] = find(us[0])
    groups = defaultdict(list)
    for u in cand:
        groups[find(u)].append(u)
    out: list[dict[str, Any]] = []
    for g in groups.values():
        phases = {ph for u in g for _, ph in cand[u]}
        if len(g) < 2 or len(phases) < 2:
            continue
        g.sort(key=lambda u: (min(cand[u])[0], u))
        shared = sorted(p for p, us in by_path.items() if set(us) & set(g) and len(set(us) & set(g)) >= 2)
        writers = sorted(u for u in g if set((state.get(u) or {}).get("writes", [])) & set(shared))
        out.append({"members": g, "phases": [ph for ph in PHASE_ORDER if ph in phases],
                    "shared_state": shared, "writers": writers,
                    "why": (f"started by {len(phases)} different stages of a deployment's life "
                            f"({', '.join(PHASES[p] for p in PHASE_ORDER if p in phases)}) and sharing the state kept "
                            f"in {', '.join(shared)}, written by "
                            f"{', '.join((wf.get(u) or {}).get('name') or u for u in writers)}. Converting one without "
                            "the others splits a deployment between vRO and Kestra: what one writes, the next reads")})
    for i, lg in enumerate(sorted(out, key=lambda g: g["members"]), 1):
        lg["id"] = f"L{i}"
    return sorted(out, key=lambda g: g["id"])


def notes(w: dict) -> list[str]:
    """Trigger facts a converter must carry, per workflow: blocking subscriptions, several subscriptions."""
    subs = [e for e in w.get("entry_points") or [] if e.get("kind") == "subscription" and not e.get("disabled")]
    out = []
    for e in subs:
        if e.get("blocking"):
            to = e.get("timeout_minutes")
            out.append(f"blocking subscription '{e['name']}' ({e.get('event_topic')}"
                       + (f", timeout {to} min" if to else "") + "): vRA waits for the run and a failure fails the "
                       "request. A timeout and failure policy has to be decided: what the Kestra side does when "
                       "it runs past the timeout, and whether a failure still stops the deployment")
    if len(subs) > 1:
        out.append(f"run by {len(subs)} subscriptions with different conditions ("
                   + "; ".join(f"'{e['name']}' on {e.get('event_topic')}"
                               + (f" when {e['condition']}" if e.get("condition") else "") for e in subs)
                   + "). The routing lives in vRA, not in the workflow; each becomes its own trigger condition "
                     "or its own step")
    return out


# ------------------------------------------------------------------ trigger plan

def _flow_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-") or "flow"


def _keep_sub(s: dict, flow: str) -> str:
    base = (f"The subscription stays in vRA (same topic, condition{', blocking' if s.get('blocking') else ''}). "
            f"It runs a small bridge, an ABX action or a one-task vRO workflow, since a subscription can only run "
            f"one of those, that posts the event payload to the `Webhook` trigger of Kestra flow `{flow}`.")
    if s.get("blocking"):
        to = s.get("timeout_minutes")
        base += (" Because vRA waits, the bridge calls the webhook with `wait: true` and `returnOutputs: true` and "
                 "fails when the execution fails, so a failure still stops the request; the flow needs a `timeout` "
                 + (f"under the subscription's {to} min" if to else "under the subscription's timeout") + ".")
    else:
        base += " vRA does not wait, so the bridge only starts the execution."
    return base


def _replace_step(s: dict, template: str | None) -> str:
    pos, phase = position(s.get("event_topic"), s.get("condition"))
    flow = f"`{_flow_id(template)}`" if template else "the deployment flow"
    cond = s.get("condition")
    where = {
        10: "a step at the start of the create path, before any machine is built",
        11: "a step at the start of the create path",
        20: "a step inside the per-machine loop, before the machine is placed",
        21: "a step inside the per-machine loop, after placement",
        22: "a step inside the per-machine loop, on its network",
        30: "a step inside the per-machine loop, before the create-VM task",
        31: "a step inside the per-machine loop, right after the create-VM task",
        32: "a step inside the per-machine loop, after first power-on",
        40: "the last step of the create path",
        50: "a flow of its own, started by the day-2 action's App",
        60: "a step at the start of the delete path",
        70: "a step inside the delete path's per-machine loop, before the delete-VM task",
        71: "a step inside the delete path's per-machine loop, after the delete-VM task",
        80: "the last step of the delete path",
    }.get(pos, "a step of the flow; its place follows the event")
    if pos == 40 and cond and FAILED.search(cond):
        where = ("the create path's `errors` branch: it runs when a create task fails (a canceled create is an "
                 "entry criterion of its own: Kestra's errors branch is documented for failures)")
    txt = f"In flow {flow}: {where}."
    if cond and not (pos == 40 and FAILED.search(cond)):
        txt += f" Its condition ({cond}) becomes an `If` on the step, or a choice the flow already makes."
    if s.get("blocking"):
        txt += " It was blocking: in the flow it simply runs in sequence, and its failure fails the flow."
    else:
        txt += " It was not blocking: vRA did not wait, so decide whether a failure here should fail the flow."
    return txt


def plan(model: dict, answer: str | None) -> dict:
    """The trigger plan for every entry point on record."""
    choice = front_end(answer)
    templates = {t["name"]: t for t in model.get("cloud_templates") or []}
    # Which cloud template a subscription belongs to: the one whose catalog item shares its project,
    # if exactly one does. vRA scopes subscriptions by project, not by template.
    tpl_of_project = defaultdict(set)
    for c in model.get("catalog_items") or []:
        if c.get("cloud_template"):
            for p in c.get("projects") or []:
                tpl_of_project[p].add(c["cloud_template"])
    rows = []
    for s in model.get("subscriptions") or []:
        cands = set().union(*[tpl_of_project[p] for p in s.get("projects") or []]) if s.get("projects") else set()
        if not s.get("projects") and len(templates) == 1:
            cands = set(templates)
        tpl = next(iter(cands)) if len(cands) == 1 else None
        pos, phase = position(s.get("event_topic"), s.get("condition"))
        flow = _flow_id(s.get("workflow") or s["name"])
        rows.append({
            "kind": "subscription", "name": s["name"], "workflow": s.get("workflow"),
            "workflow_uid": s.get("workflow_uid"), "resolved": s.get("resolved"),
            "today": f"vRA event subscription on `{s.get('event_topic')}`"
                     + (" (disabled)" if s.get("disabled") else ""),
            "event_topic": s.get("event_topic"), "blocking": bool(s.get("blocking")),
            "timeout_minutes": s.get("timeout_minutes"), "condition": s.get("condition") or "",
            "position": pos, "phase": phase, "cloud_template": tpl,
            "keep": _keep_sub(s, flow), "replace": _replace_step(s, tpl)})
    for c in model.get("catalog_items") or []:
        fields = [f.get("id") for f in c.get("form_fields") or []]
        runs = c.get("workflow") or c.get("cloud_template") or ""
        rows.append({
            "kind": "catalog item", "name": c["name"], "workflow": c.get("workflow"),
            "workflow_uid": c.get("workflow_uid"), "resolved": c.get("resolved"),
            "today": f"vRA catalog item ({c.get('source_type') or 'unknown source'})"
                     + (f" for cloud template `{c['cloud_template']}`" if c.get("cloud_template") else ""),
            "event_topic": None, "blocking": None, "timeout_minutes": None,
            "condition": "entitlement: " + (", ".join(c.get("projects") or []) or "not on record"),
            "position": 0, "phase": "request", "cloud_template": c.get("cloud_template"),
            "keep": "Stays in vRA; nothing in Kestra. The request reaches Kestra through the subscriptions below.",
            "replace": (f"A Kestra App (`{APP}`) with the catalog form's {len(fields)} field(s) as inputs "
                        f"({', '.join(fields) or 'none on record'}), starting flow `{_flow_id(runs)}`. "
                        "Entitlement becomes who may use the App.")})
    for t in model.get("cloud_templates") or []:
        rows.append({
            "kind": "cloud template", "name": t["name"], "workflow": None, "workflow_uid": None, "resolved": None,
            "today": "vRA cloud template: creates " + (", ".join(f"{x['name']} ({x['type']})" for x in t.get("creates") or [])
                                                       or "nothing on record"),
            "event_topic": None, "blocking": None, "timeout_minutes": None, "condition": "",
            "position": 1, "phase": "request", "cloud_template": t["name"],
            "keep": "Stays in vRA; vRA still builds the machines.",
            "replace": (f"Flow `{_flow_id(t['name'])}`: a create path (build each resource, with the subscriptions' "
                        "steps in place) and a delete path, chosen by an input. What it creates needs a Kestra "
                        "task each; see the plugin coverage view.")})
    for a in model.get("day2_actions") or []:
        rows.append({
            "kind": "day-2 action", "name": a["name"], "workflow": a.get("workflow"),
            "workflow_uid": a.get("workflow_uid"), "resolved": a.get("resolved"),
            "today": "vRA day-2 action" + (f" on {a['resource_type']}" if a.get("resource_type") else ""),
            "event_topic": None, "blocking": True, "timeout_minutes": None, "condition": "",
            "position": 50, "phase": "day2", "cloud_template": None,
            "keep": "Stays in vRA; its runnable becomes a bridge to the `Webhook` trigger of the converted flow.",
            "replace": f"A Kestra App (`{APP}`) on the converted flow, with the action's form as inputs."})
    for o in model.get("other_triggers") or []:
        k = (o.get("kind") or "").lower()
        kestra = ("a `Schedule` trigger on the converted flow" if "schedul" in k else
                  "a `Webhook` trigger on the converted flow, called by the same system" if k else
                  "a trigger to choose")
        rows.append({
            "kind": o.get("kind") or "other", "name": o["name"], "workflow": o.get("workflow"),
            "workflow_uid": o.get("workflow_uid"), "resolved": o.get("resolved"),
            "today": o.get("kind") or "other trigger", "event_topic": None, "blocking": None,
            "timeout_minutes": None, "condition": o.get("schedule") or o.get("condition") or "",
            "position": 95, "phase": "other", "cloud_template": None,
            "keep": kestra[0].upper() + kestra[1:] + ".", "replace": kestra[0].upper() + kestra[1:] + "."})
    rows.sort(key=lambda r: (r["cloud_template"] or "~", r["position"], r["name"]))
    return {"front_end": choice, "rows": rows,
            "has_vra": bool(model.get("subscriptions") or model.get("catalog_items") or model.get("cloud_templates")
                            or model.get("day2_actions"))}


def lifecycle_view(model: dict) -> list[dict]:
    """Per cloud template: its catalog items and form fields, what it creates, and the lifecycle events in
    order with the workflow each starts. Subscriptions no template claims are listed under None."""
    p = plan(model, None)
    out = []
    names = [t["name"] for t in model.get("cloud_templates") or []]
    for name in names + [None]:
        t = next((x for x in model.get("cloud_templates") or [] if x["name"] == name), None)
        cats = [c for c in model.get("catalog_items") or [] if c.get("cloud_template") == name] if name else []
        events = [r for r in p["rows"] if r["kind"] == "subscription" and r["cloud_template"] == name]
        if name is None and not events:
            continue
        out.append({"cloud_template": name, "template": t, "catalog_items": cats,
                    "events": sorted(events, key=lambda r: (r["position"], r["name"]))})
    return out


def tags(w: dict) -> list[str]:
    """Short forms of the trigger notes, for tables; the full notes are in the trigger plan."""
    subs = [e for e in w.get("entry_points") or [] if e.get("kind") == "subscription" and not e.get("disabled")]
    out = [f"blocking subscription ({e['timeout_minutes']} min)" if e.get("timeout_minutes") else "blocking subscription"
           for e in subs if e.get("blocking")]
    if len(subs) > 1:
        out.append(f"{len(subs)} subscriptions")
    if w.get("lifecycle_group"):
        out.append(f"lifecycle group {w['lifecycle_group']}")
    return out
