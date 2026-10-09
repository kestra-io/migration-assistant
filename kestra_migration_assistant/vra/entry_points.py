"""
Entry points: what starts an estate's workflows from outside vRO (preflight Q2).

One model, from two places:

1. A vRA / Aria Automation export (`.vra-ng`, read by `parse/vra.py`), normalized here.
2. `stages/<estate>/0-preflight/entry-points.yaml`, which a customer fills in by hand when there
   is no vRA export, or to add what one never holds (vRO scheduled tasks and policies, ITSM
   calls, scripts on the vRO REST API). Preflight writes it as a template with one commented
   example, and never overwrites it once it has an entry.

When both exist they are merged. Where they disagree on a field, the export's value is kept (it
is what vRA actually runs) and the disagreement is reported as a conflict for a person to settle,
by fixing whichever side is wrong.

Every record that runs a vRO workflow names it by `workflow_id`, the vRO workflow ID (the `id`
attribute of the workflow XML, which is what vRA's `runnableId` carries). `resolve` matches it
against the workflows in the package, by ID. A hand-written record with no ID may give
`workflow_name` instead; it resolves by exact name, and says so. Anything that does not resolve
is reported, never dropped.

The schema, per kind (every field optional apart from `name`):

  subscriptions:    name, event_topic, condition, blocking, disabled, priority, timeout_minutes,
                    projects, runnable_type, workflow_id, workflow_name, description, notes
  catalog_items:    name, content_source, source_type, cloud_template, workflow_id, workflow_name,
                    form_fields (list of {id, label, type, required, default}), projects,
                    description, notes
  cloud_templates:  name, inputs (names), creates (list of {name, type}), description, notes
  day2_actions:     name, kind (resource action | custom resource), resource_type, action,
                    workflow_id, workflow_name, notes
  other_triggers:   name, kind (vro scheduled task | vro policy | itsm | rest api | other),
                    workflow_id, workflow_name, schedule, notes
  complete:         true when the export plus this file list everything that starts these
                    workflows. Only then is Q2 fully answered.
"""
from __future__ import annotations

import os
import re
from typing import Any

from . import safety

KINDS = ("subscriptions", "catalog_items", "cloud_templates", "day2_actions", "other_triggers")
FIELDS = {
    "subscriptions": ("name", "event_topic", "condition", "blocking", "disabled", "priority", "timeout_minutes",
                      "projects", "runnable_type", "workflow_id", "workflow_name", "description", "notes"),
    "catalog_items": ("name", "content_source", "source_type", "cloud_template", "workflow_id", "workflow_name",
                      "form_fields", "projects", "description", "notes"),
    "cloud_templates": ("name", "inputs", "creates", "description", "notes"),
    "day2_actions": ("name", "kind", "resource_type", "action", "workflow_id", "workflow_name", "notes"),
    "other_triggers": ("name", "kind", "workflow_id", "workflow_name", "schedule", "notes"),
}
# Kinds whose records run a vRO workflow.
RUNS_WORKFLOW = ("subscriptions", "catalog_items", "day2_actions", "other_triggers")
TEMPLATE_MARKER = "# vro2k entry points template"


def key(kind: str, r: dict) -> tuple:
    """What makes two records the same entry point, across the export and the hand-written file."""
    n = str(r.get("name") or "").strip().casefold()
    if kind == "catalog_items":
        return (str(r.get("content_source") or "").strip().casefold(), n)
    if kind == "day2_actions":
        return (str(r.get("resource_type") or "").strip().casefold(), str(r.get("action") or "").casefold(), n)
    if kind == "other_triggers":
        return (str(r.get("kind") or "").strip().casefold(), n)
    return (n,)


def empty() -> dict:
    return {k: [] for k in KINDS} | {"complete": False}


# ------------------------------------------------------------------ from a vRA export

def from_vra(r: dict, label: str) -> dict:
    """Normalize one `parse.vra.read_vra` result into the entry points model."""
    out = empty()
    origin = f"vra:{label}"
    src_type = {s["name"]: s["type_id"] for s in r["content_sources"]}
    bp_by_name = {b["name"].casefold(): b for b in r["blueprints"]}
    wf_by_name = {w["name"].casefold(): w for w in r["catalog_workflows"]}
    projects: dict[str, list[str]] = {}
    for e in r["entitlements"]:
        if e["project"]:
            projects.setdefault(e["name"], []).append(e["project"])

    for s in r["subscriptions"]:
        is_vro = s["runnable_type"] in ("extensibility.vro", "") and s["type"] in ("RUNNABLE", "")
        out["subscriptions"].append({
            "name": s["name"], "event_topic": s["event_topic"], "condition": s["condition"],
            "blocking": s["blocking"], "disabled": s["disabled"], "priority": s["priority"],
            "timeout_minutes": s["timeout_minutes"], "projects": s["projects"],
            "runnable_type": s["runnable_type"],
            "workflow_id": s["runnable_id"] if is_vro else "",
            "action_id": "" if is_vro else s["runnable_id"],
            "description": s["description"], "origin": f"{origin}!{s['file']}"})

    for c in r["catalog_items"]:
        stype = src_type.get(c["content_source"]) or c["form"]["source_type"]
        tpl = wf = None
        if stype == "com.vmw.blueprint" or (not stype and c["name"].casefold() in bp_by_name):
            b = bp_by_name.get(c["name"].casefold())
            tpl = b["name"] if b else None
        if stype == "com.vmw.vro.workflow":
            wf = wf_by_name.get(c["name"].casefold())
        out["catalog_items"].append({
            "name": c["name"], "content_source": c["content_source"], "source_type": stype or "",
            "cloud_template": tpl or "", "workflow_id": (wf or {}).get("workflow_id", ""),
            "form_fields": [{k: f[k] for k in ("id", "label", "type", "required", "default")}
                            for f in c["form_fields"]],
            "projects": projects.get(c["content_source"], []),
            "description": c["description"], "origin": f"{origin}!{c['file']}",
            **({"template_missing": True} if stype == "com.vmw.blueprint" and not tpl else {})})

    for b in r["blueprints"]:
        out["cloud_templates"].append({
            "name": b["name"], "inputs": [i["name"] for i in b["inputs"]],
            "creates": [{"name": x["name"], "type": x["type"],
                         **{k: x[k] for k in ("count", "custom_properties", "uses_inputs", "network_type",
                                              "ip_assignment", "constraint_tags") if x.get(k)}}
                        for x in b["resources"]],
            "description": b["description"], "origin": f"{origin}!{b['file']}"})

    for a in r["resource_actions"]:
        run = a["runnable"] or {}
        out["day2_actions"].append({
            "name": a["name"], "kind": "resource action", "resource_type": a["resource_type"], "action": "",
            "workflow_id": run.get("id", "") if "vro" in (run.get("type") or a["provider"]) else "",
            "workflow_name": run.get("name", ""), "origin": f"{origin}!{a['file']}"})
    for c in r["custom_resources"]:
        for act in c["actions"]:
            out["day2_actions"].append({
                "name": c["name"], "kind": "custom resource", "resource_type": c["resource_type"],
                "action": act["action"], "workflow_id": act["id"] if "vro" in act["type"] else "",
                "workflow_name": act["name"], "origin": f"{origin}!{c['file']}"})
    # A vRO workflow published to the catalog with no catalog item in this export is still an entry point.
    listed = {c["name"].casefold() for c in out["catalog_items"]}
    for w in r["catalog_workflows"]:
        if w["name"].casefold() not in listed:
            out["catalog_items"].append({
                "name": w["name"], "content_source": "", "source_type": w["type_id"] or "com.vmw.vro.workflow",
                "cloud_template": "", "workflow_id": w["workflow_id"], "form_fields": [], "projects": [],
                "description": w["description"], "origin": f"{origin}!{w['file']}"})
    return out


# ------------------------------------------------------------------ the hand-written file

def template() -> str:
    return f"""{TEMPLATE_MARKER} (preflight Q2). Written by `vro.py preflight`.
#
# What starts these workflows from outside vRO. Fill this in when you have no vRA or Aria Automation
# export (.vra-ng), or to add what that export never holds: vRO scheduled tasks and policies, ITSM
# calls (for example ServiceNow), and scripts on the vRO REST API.
#
# The schema is the one the tool reads from a .vra-ng. When both exist they are merged; where they
# disagree, the export's value is kept and the conflict is listed in readiness.md, so fix whichever
# side is wrong. Name each workflow by its vRO ID (`workflow_id`, the id attribute of the workflow);
# `workflow_name` is used only when there is no ID.
#
# Every stage re-reads this file. Preflight never overwrites it once it has an entry.
# Set `complete: true` when the export and this file list everything that starts these workflows.
#
# Example (remove the leading "# " to use it):
#
# subscriptions:
#   - name: Add machine to pool
#     event_topic: compute.provision.post
#     condition: event.data.customProperties.lbEnabled == "true"
#     blocking: true
#     workflow_id: 00000000-0000-0000-0000-000000000000
#     workflow_name: Add Machine To Pool
# catalog_items:
#   - name: Web tier
#     content_source: Templates
#     source_type: com.vmw.blueprint
#     cloud_template: Web tier
#     form_fields: [{{id: vmCount, type: integer, required: true}}]
# cloud_templates:
#   - name: Web tier
#     inputs: [vmCount]
#     creates: [{{name: web, type: Cloud.vSphere.Machine}}]
# day2_actions:
#   - name: Add disk
#     kind: resource action
#     resource_type: Cloud.vSphere.Machine
#     workflow_id: 00000000-0000-0000-0000-000000000001
# other_triggers:
#   - name: Nightly clean-up
#     kind: vro scheduled task        # or: vro policy, itsm, rest api, other
#     schedule: every day at 02:00
#     workflow_id: 00000000-0000-0000-0000-000000000002

complete: false
subscriptions: []
catalog_items: []
cloud_templates: []
day2_actions: []
other_triggers: []
"""


def _clean(v, depth=0):
    """Plain data only: strings, numbers, booleans, lists and mappings of those."""
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    if isinstance(v, list) and depth < 4:
        return [_clean(x, depth + 1) for x in v]
    if isinstance(v, dict) and depth < 4:
        return {str(k): _clean(x, depth + 1) for k, x in v.items()}
    return str(v)


def load_manual(path: str) -> tuple[dict, list[str]]:
    """The hand-written file, normalized, and what was wrong with it. A missing file is empty."""
    out = empty()
    warnings: list[str] = []
    if not os.path.exists(path):
        return out, warnings
    try:
        data = safety.load_yaml(safety.read_file(path, safety.MAX_DOC_BYTES), os.path.basename(path))
    except Exception as e:
        return out, [f"{os.path.basename(path)} does not parse, so it was not used: {str(e)[:200]}"]
    if data is None:
        return out, warnings
    if not isinstance(data, dict):
        return out, [f"{os.path.basename(path)}: expected a mapping at the top, found {type(data).__name__}"]
    for k, v in data.items():
        if k == "complete":
            out["complete"] = v is True
            continue
        if k not in KINDS:
            warnings.append(f"unknown section {k!r} ignored (expected one of {', '.join(KINDS)}, complete)")
            continue
        if v is None:
            continue
        if not isinstance(v, list):
            warnings.append(f"{k}: expected a list, found {type(v).__name__}")
            continue
        for i, rec in enumerate(v):
            if not isinstance(rec, dict) or not str(rec.get("name") or "").strip():
                warnings.append(f"{k}[{i}]: each entry needs at least a name")
                continue
            extra = sorted(set(map(str, rec)) - set(FIELDS[k]))
            if extra:
                warnings.append(f"{k}[{i}] ({rec['name']}): unknown field(s) kept but not read: {', '.join(extra)}")
            r = {f: _clean(rec[f]) for f in rec if str(f) in FIELDS[k] or str(f) in extra}
            for f in ("workflow_id", "workflow_name", "name"):
                if f in r and r[f] is not None:
                    r[f] = str(r[f]).strip()
            r["origin"] = "manual"
            out[k].append(r)
    return out, warnings


def is_filled(path: str) -> bool:
    """True when the file has any entry, or is not a template this tool wrote. Then it is never rewritten."""
    if not os.path.exists(path):
        return False
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return True
    if TEMPLATE_MARKER not in text:
        return True
    data, _ = load_manual(path)
    return data["complete"] or any(data[k] for k in KINDS)


# ------------------------------------------------------------------ merge and resolve

def _blank(v) -> bool:
    return v is None or v == "" or v == []


def merge(parts: list[dict]) -> tuple[dict, list[dict]]:
    """Merge models in order (exports first, the hand-written file last). The first non-blank value wins."""
    out, conflicts = empty(), []
    for p in parts:
        out["complete"] = out["complete"] or bool(p.get("complete"))
        for k in KINDS:
            index = {key(k, r): r for r in out[k]}
            for r in p.get(k, []):
                have = index.get(key(k, r))
                if have is None:
                    rec = dict(r)
                    rec["origins"] = [r.get("origin", "")]
                    out[k].append(rec)
                    index[key(k, r)] = rec
                    continue
                have["origins"].append(r.get("origin", ""))
                for f, v in r.items():
                    if f in ("origin", "origins"):
                        continue
                    if _blank(have.get(f)):
                        if not _blank(v):
                            have[f] = v
                    elif not _blank(v) and _norm(have[f]) != _norm(v):
                        conflicts.append({"kind": k, "name": have["name"], "field": f, "kept": have[f],
                                          "kept_from": have["origins"][0], "other": v,
                                          "other_from": r.get("origin", "")})
    return out, conflicts


def _norm(v):
    if isinstance(v, str):
        return re.sub(r"\s+", " ", v.strip())
    if isinstance(v, list):
        return [_norm(x) for x in v]
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in v.items()}
    return v


def resolve(model: dict, workflows: dict[str, dict]) -> dict:
    """
    Match every workflow reference against the package's workflows ({vRO id: {"name", "uid"?}}), by ID,
    and by exact name only for a hand-written record with no ID. Adds `resolved` (True, False, or None
    for a record that runs no workflow), `resolved_by`, `workflow` (name in the package) and `workflow_uid`.
    Returns {"resolved": n, "unresolved": [records], "no_workflow": n}.
    """
    by_name: dict[str, list[str]] = {}
    for wid, w in workflows.items():
        by_name.setdefault((w.get("name") or "").casefold(), []).append(wid)
    unresolved, n_ok, n_none = [], 0, 0
    for k in RUNS_WORKFLOW:
        for r in model.get(k, []):
            wid, wname = (r.get("workflow_id") or "").strip(), (r.get("workflow_name") or "").strip()
            r.pop("workflow_uid", None)
            if not wid and not wname:
                # Runs no vRO workflow by design: a catalog item for a cloud template, or a subscription
                # that runs an ABX action. Anything else should name one.
                by_design = (k == "catalog_items" and r.get("source_type") != "com.vmw.vro.workflow") or \
                            (k == "subscriptions" and r.get("action_id"))
                r["resolved"], r["resolved_by"] = (None if by_design else False), None
                if by_design:
                    n_none += 1
                else:
                    r["why"] = "names no vRO workflow"
                    unresolved.append({"kind": k, **r})
                continue
            hit = workflows.get(wid) if wid else None
            by = "id" if hit else None
            if not hit and not wid and wname and len(by_name.get(wname.casefold(), [])) == 1:
                wid = by_name[wname.casefold()][0]
                hit, by = workflows[wid], "name"
            if hit:
                r.update({"resolved": True, "resolved_by": by, "workflow_id": wid, "workflow": hit.get("name"),
                          **({"workflow_uid": hit["uid"]} if hit.get("uid") else {})})
                r.pop("why", None)
                n_ok += 1
            else:
                r.update({"resolved": False, "resolved_by": None,
                          "why": (f"no workflow with ID {wid} in the package" if wid else
                                  f"no single workflow named {wname!r} in the package")})
                unresolved.append({"kind": k, **r})
    return {"resolved": n_ok, "unresolved": unresolved, "no_workflow": n_none}


def links(model: dict) -> list[dict]:
    """
    Edges for the call graph: catalog item -> cloud template, catalog item -> workflow,
    event topic -> workflow (labelled with the subscription), day-2 action / other trigger -> workflow.
    """
    out = []
    tpls = {t["name"].casefold() for t in model.get("cloud_templates", [])}
    for c in model.get("catalog_items", []):
        if c.get("cloud_template"):
            out.append({"from": ("catalog item", c["name"]), "to": ("cloud template", c["cloud_template"]),
                        "label": c.get("content_source") or "", "in_export": c["cloud_template"].casefold() in tpls})
        if c.get("workflow_id") or c.get("workflow_name"):
            out.append({"from": ("catalog item", c["name"]), "to": _wf_end(c), "label": "", "in_export": c.get("resolved")})
    for s in model.get("subscriptions", []):
        if s.get("workflow_id") or s.get("workflow_name"):
            out.append({"from": ("event topic", s.get("event_topic") or "(no topic)"), "to": _wf_end(s),
                        "label": s["name"] + (" (blocking)" if s.get("blocking") else "")
                        + (" (disabled)" if s.get("disabled") else ""), "in_export": s.get("resolved")})
    for k, what in (("day2_actions", "day-2 action"), ("other_triggers", "trigger")):
        for a in model.get(k, []):
            if a.get("workflow_id") or a.get("workflow_name"):
                out.append({"from": (what, a["name"] + (f" ({a['action']})" if a.get("action") else "")),
                            "to": _wf_end(a), "label": a.get("kind") or "", "in_export": a.get("resolved")})
    return out


def _wf_end(r: dict) -> tuple:
    if r.get("resolved"):
        return ("workflow", r.get("workflow_uid") or r["workflow_id"])
    return ("missing workflow", r.get("workflow_id") or r.get("workflow_name"))


def per_workflow(model: dict) -> dict[str, list[dict]]:
    """For each resolved workflow ID, the entry points that start it (a short record each)."""
    out: dict[str, list[dict]] = {}
    for k in RUNS_WORKFLOW:
        for r in model.get(k, []):
            if r.get("resolved"):
                rec = {"kind": k[:-1].replace("_", " "), "name": r["name"]}
                if k == "subscriptions":
                    rec.update({"event_topic": r.get("event_topic"), "condition": r.get("condition"),
                                "blocking": r.get("blocking"), "disabled": r.get("disabled"),
                                "timeout_minutes": r.get("timeout_minutes")})
                elif r.get("kind"):
                    rec["trigger"] = r["kind"]
                out.setdefault(r["workflow_id"], []).append(rec)
    return out


def total(model: dict) -> int:
    return sum(len(model.get(k, [])) for k in KINDS)


# ------------------------------------------------------------------ one estate

def collect(vra_inputs: dict[str, str], manual_path: str) -> dict:
    """
    Read every vRA export and the hand-written file, and merge them. Unreadable exports are reported.
    Returns {"model", "conflicts", "exports": [{label, path, counts, warnings, unknown, ...}],
    "manual": {path, exists, filled, entries, warnings}}.
    """
    from .parse import vra
    parts: list[dict] = []
    exports: list[dict[str, Any]] = []
    for label, path in vra_inputs.items():
        try:
            r = vra.read_vra(path)
        except Exception as e:
            exports.append({"label": label, "path": path, "error": f"{type(e).__name__}: {e}"})
            continue
        parts.append(from_vra(r, label))
        exports.append({"label": label, "path": path, "format": r["format"], "files": r["files"],
                        "counts": vra.counts(r), "warnings": r["warnings"], "unknown": r["unknown"],
                        "unread": r["unread"], "listed_but_missing": r["listed_but_missing"],
                        "present_but_unlisted": r["present_but_unlisted"], "raw": r})
    manual, mwarn = load_manual(manual_path)
    parts.append(manual)
    model, conflicts = merge(parts)
    return {"model": model, "conflicts": conflicts, "exports": exports,
            "manual": {"path": manual_path, "exists": os.path.exists(manual_path),
                       "filled": is_filled(manual_path), "entries": total(manual), "complete": manual["complete"],
                       "warnings": mwarn}}


def for_estate(workflows: dict[str, dict] | None = None) -> dict:
    """`collect` for the configured estate, resolved against `workflows` ({vRO id: {name, uid?}}) if given."""
    from .stages import common as C
    C.require_estate()
    ep = collect(C.VRA_INPUTS, C.ENTRY_POINTS)
    if workflows is not None:
        ep["resolution"] = resolve(ep["model"], workflows)
    return ep


def public(ep: dict) -> dict:
    """What is written to disk: everything but the raw reader output."""
    from .stages import common as C
    return {"model": ep["model"], "conflicts": ep["conflicts"],
            "resolution": ep.get("resolution"),
            "exports": [{**{k: v for k, v in x.items() if k != "raw"}, "path": C.rel(x["path"])} for x in ep["exports"]],
            "manual": {**ep["manual"], "path": C.rel(ep["manual"]["path"])}}


def q2_status(ep: dict) -> str:
    """'answered', 'partly answered' or 'open', from what the entry points hold."""
    if ep["model"]["complete"]:
        return "answered"
    if total(ep["model"]):
        return "partly answered"
    return "open"
