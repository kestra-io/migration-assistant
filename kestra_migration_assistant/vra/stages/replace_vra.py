"""
Replacing vRA, planned as work: when preflight Q9 says vRA goes, what vRA did becomes Kestra work in waves.

Per cloud template:
- an App (io.kestra.plugin.ee.apps.Execution) per catalog item that orders it, with the catalog form's
  fields as the App's inputs, and the entitlement as who may use it;
- a flow built from the cloud template: one step per resource it creates, with the Kestra task that does it
  taken from the plugin coverage view (the snapshot of what exists), and a gap named for every resource type
  no Kestra task creates;
- the lifecycle steps the subscriptions become, in lifecycle order, each running the flow its vRO workflow
  converts to (a Subflow), and each blocking one with its timeout and failure policy.

Templates are grouped into one replacement wave per set of workflow waves they depend on (a template's steps
run converted workflows, so it waits for the waves that hold them). The replacement waves come after the
workflow waves and have entry criteria like any other wave.
"""
from __future__ import annotations

from collections import defaultdict
from typing import cast

from . import gates as G
from . import triggers as TG

APP = TG.APP
SUBFLOW = "io.kestra.plugin.core.flow.Subflow"
MACHINE_WORDS = ("Machine",)


def _tasks_for(rtype: str, rows: list[dict]) -> tuple[list[str], dict | None]:
    """The Kestra tasks that create a resource type, and the coverage row, from the plugin coverage view."""
    for r in rows:
        types = [t.strip() for t in (r.get("resource_type") or "").split(",") if t.strip()]
        if rtype in types:
            tasks = [m["task"] for m in r.get("task_map") or [] if m["op"] == f"create and delete {rtype}"
                     and any(v for v in (m.get("where") or {}).values())]
            return tasks, r
    return [], None


def _path(step: dict) -> str:
    """The flow path a lifecycle step is on: its event's position in the lifecycle (triggers.position)."""
    pos = TG.position(step.get("event_topic"), step.get("condition"))[0]
    return "delete" if 60 <= pos < 90 else ("day-2" if pos == 50 else "create")


def _existing(x: dict) -> bool:
    """A resource the template only refers to: a network with `networkType: existing` is picked from what is
    there (by the network profile and its constraint tags), not created."""
    return (x.get("network_type") or "").lower() == "existing"


def _input_of(count) -> str | None:
    """The input a machine count comes from (`${input.vmCount}` -> vmCount), or None."""
    import re
    m = re.search(r"input\.(\w+)", str(count or ""))
    return m.group(1) if m else None


def _short(cls: str) -> str:
    parts = cls.split(".")
    return ".".join(parts[-2:]) if len(parts) > 3 else cls


def plan(assess: dict, triage: dict, answers: dict, trigger_plan: dict | None, wave_of: dict[str, int],
         first_number: int, blocked: dict[str, list[str]]) -> list[dict]:
    """Replacement waves, numbered from `first_number`. Empty unless the trigger plan says vRA is replaced."""
    if not trigger_plan or trigger_plan.get("front_end") not in TG.REPLACING:
        return []
    model = (assess.get("entry_points") or {}).get("model") or {}
    templates = model.get("cloud_templates") or []
    if not templates:
        return []
    wf = assess["workflows"]
    open_q = set(answers.get("open") or [])
    cov_rows = (assess.get("plugin_coverage") or {}).get("rows") or []
    vra_row = next((r for r in cov_rows if r.get("key") == "vra"), None)
    app_where = next((m.get("where") for m in (vra_row or {}).get("task_map") or [] if m["task"] == APP), None)
    rows = trigger_plan.get("rows") or []

    items = []
    for t in templates:
        name = t["name"]
        cats = [c for c in model.get("catalog_items") or [] if c.get("cloud_template") == name]
        steps = sorted((r for r in rows if r["kind"] == "subscription" and r.get("cloud_template") == name),
                       key=lambda r: (r["position"], r["name"]))
        resources = []
        for x in t.get("creates") or []:
            tasks, row = _tasks_for(x["type"], cov_rows)
            resources.append({"name": x["name"], "type": x["type"], "count": x.get("count"),
                              "custom_properties": x.get("custom_properties") or [],
                              "network_type": x.get("network_type"), "ip_assignment": x.get("ip_assignment") or [],
                              "constraint_tags": x.get("constraint_tags") or [],
                              "existing": _existing(x),
                              "tasks": tasks, "system": (row or {}).get("system"),
                              "system_key": (row or {}).get("key"),
                              "enterprise_only": bool((row or {}).get("enterprise_only"))})
        needs = sorted({wave_of[r["workflow_uid"]] for r in steps if r.get("workflow_uid") in wave_of})
        not_waved = sorted({r["workflow_uid"] for r in steps if r.get("workflow_uid") and r["workflow_uid"] not in wave_of})
        items.append({"cloud_template": name, "flow": TG._flow_id(name), "catalog_items": cats, "resources": resources,
                      "steps": steps, "depends_on": needs, "not_waved": not_waved})

    groups = defaultdict(list)
    for it in items:
        groups[tuple(it["depends_on"])].append(it)
    out = []
    for n, (deps, its) in enumerate(sorted(groups.items(), key=lambda kv: (len(kv[0]), kv[0])), first_number):
        out.append(_wave(n, list(deps), its, wf, triage, open_q, app_where, blocked, assess))
    return out


def _wave(n, deps, its, wf, triage, open_q, app_where, blocked, assess) -> dict:
    from . import waves as W
    crit = [{"criterion": "The signature covers this wave", "source": "signature", "closes": G.by_approval()}]
    for d in deps:
        crit.append({"criterion": f"Wave {d} has met its exit criteria (its flows are the steps this wave's flow runs)",
                     "source": "waves", "closes": W._wave_exit(d)})
    for it in its:
        for u in it["not_waved"]:
            why = (blocked.get(u) or [f"it is {triage[u]['outcome']}"])[0] if u in triage else "it is not in the export"
            crit.append({"criterion": f"{(wf.get(u) or {}).get('name') or u}, which a step of flow `{it['flow']}` "
                                      f"runs, has a Kestra flow ({why})", "source": "triage", "closes": G.by_outcome(u, (wf.get(u) or {}).get("short"))})
    systems = sorted({(r["system"], r["system_key"]) for it in its for r in it["resources"] if r["system"]})
    for sname, _key in systems:
        crit.append({"criterion": f"A non-production {sname} is named for this wave's flow to create resources in",
                     "source": "preflight Q3", "closes": G.by_answer("Q3")})
    ee = sorted({_short(t) for it in its for r in it["resources"] if r["enterprise_only"] for t in r["tasks"]})
    if its and any(it["catalog_items"] for it in its):
        ee = [APP] + ee
    if ee:
        crit.append({"criterion": "Kestra Enterprise Edition available (" + ", ".join(ee) + " "
                                  + ("is" if len(ee) == 1 else "are") + " EE)"
                                  + ("" if app_where is None or any(app_where.values()) else
                                     "; the App type is not in the plugin snapshot"),
                     "source": "plugin coverage", "closes": W.edition()})
    for it in its:
        fk = G.key_part(it["flow"])
        for r in it["resources"]:
            if not r["tasks"] and not r["existing"]:
                crit.append({"criterion": f"No Kestra task creates {r['type']} (`{r['name']}` in cloud template "
                                          f"'{it['cloud_template']}'): decide how flow `{it['flow']}` gets it: an "
                                          "existing one passed as an input, a call to the system's API over HTTP "
                                          "(io.kestra.plugin.core.http.Request), or a new plugin task",
                             "source": "plugin coverage",
                             "closes": G.by_decision(
                                 f"gap.{fk}.{G.key_part(r['name'])}",
                                 f"No Kestra task creates {r['type']} (`{r['name']}`). How does flow `{it['flow']}` "
                                 "get it? Say which one in the answer if it already exists.",
                                 ["existing, referenced by name or passed as an input", "created over HTTP",
                                  "a new plugin task"],
                                 why="the plugin snapshot has no task that creates this resource type")})
        machines = [r for r in it["resources"] if any(w in r["type"] for w in MACHINE_WORDS)]
        existing = [r for r in it["resources"] if r["existing"]]
        if machines:
            net = ("; and which vSphere network (port group) " + ", ".join(
                f"`{r['name']}`" + (f" (constraint tag{'s' if len(r['constraint_tags']) > 1 else ''} "
                                    + ", ".join(r["constraint_tags"]) + ")" if r["constraint_tags"] else "")
                for r in existing) + " means: an existing network vRA picked through its network profile"
                   if existing else "")
            crit.append({"criterion": f"What vRA resolved for cloud template '{it['cloud_template']}' is restated for "
                                      "Kestra: which VM template each image means, which CPU and memory each flavor "
                                      "means, where each machine is placed, and which network it joins (vRA's image "
                                      "and flavor mappings, cloud zones and network profiles do not come across)",
                         "source": "vRA export",
                         "closes": G.by_decision(
                             f"mappings.{fk}",
                             f"For cloud template '{it['cloud_template']}': which VM template each image input means, "
                             "which CPU and memory each flavor means, which vCenter cluster, datastore and folder "
                             "each machine goes to" + (net or ", and which network each machine joins")
                             + ". Write them in the answer.",
                             why="vRA's image and flavor mappings, cloud zones and network profiles are not in the "
                                 "export")})
        static = sorted({r["name"] for r in machines if "static" in [a.lower() for a in r["ip_assignment"]]})
        if static:
            crit.append({"criterion": f"Who gives the machines of flow `{it['flow']}` their static IP addresses is "
                                      "decided: vRA allocated them from its network profile's IP range or IPAM "
                                      f"(`assignment: static` on {', '.join(static)}), and the steps after create "
                                      "read them", "source": "vRA export",
                         "closes": G.by_decision(
                             f"ip-allocation.{fk}",
                             f"Machines {', '.join(static)} of cloud template '{it['cloud_template']}' get a static IP "
                             "that vRA allocated (network profile range or IPAM). Who allocates it in flow "
                             f"`{it['flow']}`?",
                             ["an IPAM system's task (say which)", "a range the flow keeps (KV)",
                              "DHCP reservations", "other (say how)"],
                             why="the cloud template sets `assignment: static`; vRA's network profile, which held the "
                                 "range, is not in the export")})
        if machines:
            crit.append({"criterion": f"Flow `{it['flow']}` records which machines and resources belong to each "
                                      "deployment: vRA kept that record, and the delete path, a scale-in and a "
                                      "re-run read it", "source": "vRA export",
                         "closes": G.by_decision(
                             f"deployment-record.{fk}",
                             f"vRA kept the record of what each deployment of '{it['cloud_template']}' is made of. "
                             f"Where does flow `{it['flow']}` keep it?",
                             ["the KV store, one entry per deployment", "the state the converted workflows already keep",
                              "an external CMDB (say which)"],
                             recommended="the KV store, one entry per deployment",
                             why="the delete path runs in another execution than the create, so it needs the record; "
                                 "Kestra's KV store keeps it per namespace")})
        scaled = cast("list[tuple[str, str]]",
                      [(r["name"], _input_of(r.get("count"))) for r in machines if _input_of(r.get("count"))])
        if scaled:
            per_machine = [s_["name"] for s_ in it["steps"] if TG.position(s_.get("event_topic"), s_.get("condition"))[1]
                           in ("machine", "removal")]
            crit.append({"criterion": f"Day 2 for flow `{it['flow']}` is decided: changing "
                                      + ", ".join(f"`{i}` (the count of {n})" for n, i in scaled)
                                      + " on a deployment made vRA add or remove machines and fire the per-machine "
                                        "events again" + (f" ({', '.join(per_machine)})" if per_machine else ""),
                         "source": "vRA export",
                         "closes": G.by_decision(
                             f"day2-scale.{fk}",
                             f"In vRA, changing {', '.join(i for _, i in scaled)} on a deployment of "
                             f"'{it['cloud_template']}' scales it and runs the per-machine steps for each machine added "
                             "or removed. Does the Kestra side offer that?",
                             ["a day-2 App that scales and runs the same steps", "not offered: redeploy instead"],
                             why="the machine count comes from an input, so vRA allowed it to change after the "
                                 "deployment")})
        for c in it["catalog_items"]:
            fields = [f["id"] for f in c.get("form_fields") or []]
            users = ", ".join(c.get("projects") or []) or "entitlement not on record"
            crit.append({"criterion": f"The App for catalog item '{c['name']}' is agreed: it takes the catalog form's "
                                      f"{len(fields)} input(s) ({', '.join(fields) or 'none on record'}) with their "
                                      "defaults and choices, and who may use it is decided (today: " + users + ")",
                         "source": "vRA export",
                         "closes": G.by_decision(
                             f"app.{G.key_part(c['name'])}",
                             f"Who may use the App for catalog item '{c['name']}'? It takes the catalog form's "
                             f"{len(fields)} inputs with their defaults and choices; say in the answer if any "
                             "should change.",
                             ["the catalog item's users (" + users + ")", "other (say who)"],
                             recommended="the catalog item's users (" + users + ")",
                             why="the catalog item's entitlement in the vRA export")})
        for s in it["steps"]:
            path = _path(s)
            if s.get("blocking"):
                crit.append({"criterion": f"Step '{s['name']}' in flow `{it['flow']}` has a timeout and failure "
                                          f"policy: vRA waited "
                                          + (f"{s['timeout_minutes']} min" if s.get("timeout_minutes") else "for it")
                                          + (" and a failure stopped the deletion; say whether it still does, and "
                                             "what state the deployment is left in for a retry" if path == "delete" else
                                             " and a failure stopped the deployment; say whether it still does, and "
                                             "what undoes the steps before it"), "source": "entry points",
                             "closes": W.timeout_policy(f"timeout.{fk}.{G.key_part(s['name'])}", s["name"],
                                                        s.get("timeout_minutes"), path=path)})
            else:
                crit.append({"criterion": f"Step '{s['name']}' in flow `{it['flow']}` has a failure policy: vRA did "
                                          "not wait for it, so a failure did not stop the "
                                          + ("deletion" if path == "delete" else "deployment")
                                          + "; say whether it fails the flow now", "source": "entry points",
                             "closes": W.failure_policy(f"failure.{fk}.{G.key_part(s['name'])}", s["name"], path)})
            if TG.is_failed_cleanup(s):
                crit.append({"criterion": f"Step '{s['name']}' runs in vRA when a create fails or is canceled. In "
                                          f"flow `{it['flow']}` it is the create path's `errors` branch, which runs "
                                          "when a task fails; whether a killed (canceled) execution runs it is checked "
                                          "on the target Kestra, and if not the cancel case runs the same step from "
                                          "`afterExecution` with a condition on the execution's state",
                             "source": "entry points",
                             "closes": G.by_decision(
                                 f"cancel.{fk}.{G.key_part(s['name'])}",
                                 f"'{s['name']}' also ran for a canceled create. How does flow `{it['flow']}` run it "
                                 "when an execution is killed?",
                                 ["errors runs for a killed execution (checked on the target Kestra)",
                                  "afterExecution with a condition on the execution's state",
                                  "not needed (say why)"],
                                 why="Kestra's errors branch is documented for failed tasks; this tool has not "
                                     "checked a killed execution")})
        crit.append({"criterion": f"A plan for the deployments vRA already made from '{it['cloud_template']}': deleted "
                                  "through vRA before it goes, or recorded where flow "
                                  f"`{it['flow']}`'s delete path can find them (the state its steps read)",
                     "source": "vRA export",
                     "closes": G.by_decision(
                         f"existing-deployments.{fk}",
                         f"What happens to the deployments vRA already made from '{it['cloud_template']}'?",
                         ["there are none", "deleted through vRA before it goes",
                          "recorded for the delete path of flow " + it["flow"]])})
    if "Q4" in open_q:
        crit.append({"criterion": "Preflight Q4 answered: nothing here is off limits", "source": "preflight Q4",
                     "closes": G.by_answer("Q4")})

    scope = []
    for it in its:
        for c in it["catalog_items"]:
            scope.append({"kind": "App", "name": c["name"], "kestra": APP,
                          "detail": f"inputs from the catalog form: {', '.join(f['id'] for f in c.get('form_fields') or []) or 'none'}; "
                                    f"starts flow `{it['flow']}`"})
        scope.append({"kind": "flow", "name": it["flow"], "kestra": "flow",
                      "detail": f"from cloud template '{it['cloud_template']}': a create path and a delete path, "
                                "chosen by an input"})
        for r in it["resources"]:
            if r["existing"]:
                scope.append({"kind": "resource", "name": f"{r['name']} ({r['type']})",
                              "kestra": "an existing network, referenced by name",
                              "detail": "`networkType: existing`: nothing is created; which network it means is part "
                                        "of the mappings decision"
                                        + (f" (constraint tags {', '.join(r['constraint_tags'])})"
                                           if r["constraint_tags"] else ""),
                              "gap": False})
                continue
            scope.append({"kind": "resource", "name": f"{r['name']} ({r['type']})",
                          "kestra": ", ".join(_short(t) for t in r["tasks"]) or "no Kestra task",
                          "detail": ("count " + str(r["count"]) + "; " if r.get("count") else "")
                                    + (f"custom properties {', '.join(r['custom_properties'])}; "
                                       if r["custom_properties"] else "")
                                    + (f"IP assignment {', '.join(r['ip_assignment'])}; " if r["ip_assignment"] else "")
                                    + ("create on the create path, delete on the delete path"
                                       if r["tasks"] else "nothing in the plugin snapshot creates it"),
                          "gap": not r["tasks"]})
        for s in it["steps"]:
            u = s.get("workflow_uid")
            scope.append({"kind": "lifecycle step", "name": s["name"], "kestra": SUBFLOW if u else "a task to choose",
                          "detail": s["replace"].split(": ", 1)[-1].split(". ")[0]
                                    + (f"; runs the flow {(wf.get(u) or {}).get('name') or u} converts to" if u else ""),
                          "workflow_uid": u, "blocking": bool(s.get("blocking"))})
    # one vRO workflow run by two subscriptions becomes two steps calling one converted flow: a fact of the plan,
    # stated with the scope, not a gate (nothing is left for a person to decide)
    notes = []
    for it in its:
        twice = defaultdict(list)
        for s in it["steps"]:
            if s.get("workflow_uid"):
                twice[s["workflow_uid"]].append(s["name"])
        for u, names in sorted(twice.items()):
            if len(names) > 1:
                notes.append(f"{(wf.get(u) or {}).get('name') or u} runs at {len(names)} places in flow `{it['flow']}` "
                             f"({'; '.join(names)}): one vRO workflow becomes {len(names)} steps that call the same "
                             "converted flow, each with the inputs its place has")
    tnames = sorted({r["system"] for it in its for r in it["resources"] if r["system"]})
    gaps = [f"{r['type']} ({r['name']})" for it in its for r in it["resources"] if not r["tasks"] and not r["existing"]]
    first = next((c["name"] for it in its for c in it["catalog_items"]), None)
    return {
        "wave": n, "kind": "vra-replacement", "clusters": [], "workflows": [], "n": 0,
        "name": "replace vRA: " + ", ".join(it["cloud_template"] for it in its),
        "cloud_templates": [it["cloud_template"] for it in its], "flows": [it["flow"] for it in its],
        "scope": scope, "notes": notes, "gaps": gaps, "primary_target": None, "owner_input": [], "spec_review": [],
        "targets": sorted({r["system_key"] for it in its for r in it["resources"] if r["system_key"]}),
        "target_names": tnames, "writes_to": [], "reads_from": [], "risk": "writes", "depends_on": deps,
        "scale": "M", "script_lines": 0, "actions": {},
        "entry_criteria": crit,
        "exit_criteria": [
            (f"A deployment ordered from the App for '{first}'" if first else "A deployment started from the flow")
            + " in non-production creates every resource, runs every lifecycle step in order, and ends in the "
              "state the vRA deployment did",
            "Deleting that deployment runs the delete path's steps and leaves nothing behind on any system",
            "A failed create runs the create path's error steps and leaves nothing behind",
            "Only then are the vRA catalog item and its subscriptions switched off",
        ],
        "pilot": (f"one deployment ordered from the App for '{first}', in non-production" if first else
                  "one deployment started from the flow, in non-production"),
    }
