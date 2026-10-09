"""Markdown views of assess.json. Everything here is rendered from that file."""
from __future__ import annotations

import os
import re
from collections import Counter

from . import common as C

T = C.md_table


def _w(name: str, text: str) -> None:
    C.write_text(os.path.join(C.ASSESS, name), C.no_em_dashes(text))


def write_all(r: dict) -> None:
    _w("estate-report.md", estate(r))
    _w("clusters.md", clusters(r))
    _w("parse-inventory.md", parse_inventory(r))
    _w("target-inventory.md", target_inventory(r))
    _w("unexported-calls.md", unexported(r))
    _w("domains.md", domains(r))
    _w("wrappers-and-libraries.md", wrappers(r))
    C.write_json(os.path.join(C.ASSESS, "targets.json"), r["targets"])
    C.write_csv(os.path.join(C.ASSESS, "unexported-calls.csv"), r["unexported_calls"],
                ["caller", "item", "mode", "workflow_id", "display_name", "kind", "reaches"])


def _n(r: dict, u: str) -> str:
    """A workflow as a person reads it: its name, not its ID."""
    return ((r.get("workflows") or {}).get(u) or {}).get("name") or u


def _header(r, title):
    return [f"# {title}", "",
            f"Generated {r['generated']} by `/vro-assess`. Code only: no model calls apart from the "
            "domain names, which come from one cached call over the cached cluster summaries.", ""]


def estate(r: dict) -> str:
    c = r["counts"]
    wf = r["workflows"]
    L = _header(r, "Estate report")
    L += ["## Headline", "",
          T(["Measure", "Value"], [
              ["Workflows in the export", c["workflows_in_export"]],
              ["Workflows assessed", c["workflows"]],
              ["Actions (script modules)", c["actions"]],
              ["Configuration elements", c["config_elements"]],
              ["Resource elements", c["resource_elements"]],
              ["Target systems", c["targets"]],
              ["Clusters (singletons)", f"{c['clusters']} ({c['singletons']})"],
              ["Domains", c["domains"]],
              ["Tests in the estate", "0 (vRO has no unit tests)"],
              ["Lines of script in workflows / actions", f"{c['script_lines_workflows']} / {c['script_lines_actions']}"],
              ["Parse failures", c["parse_failures"]],
              ["Wrappers flagged", c["wrappers"]],
              ["Actions that are clients for a target", c["libraries"]],
              ["Nested calls to something not in the export", c["unexported_calls"]],
              ["Workflows whose first task in the file is not where the run starts", c["doc_order_would_mislead"]],
              ["Entry points (vRA export and entry-points.yaml)", c.get("entry_points", 0)],
              ["Workflows started by an entry point", c.get("entry_point_workflows", 0)],
              ["Entry points naming a workflow not in the package", c.get("entry_points_unresolved", 0)],
          ]), ""]
    if r["open_preflight_items"]:
        L += [f"Open preflight items carried here: {', '.join(r['open_preflight_items'])} "
              "(see the readiness report).", ""]
    b = c["bands"]
    L += ["## Readiness colour", "",
          "A first colour score from the parse alone: green converts as is, amber needs choices, red has a hard blocker.", "",
          T(["Colour", "Workflows"], [[k, b.get(k, 0)] for k in ("green", "amber", "red")]), ""]
    reasons: Counter[str] = Counter()
    for w in wf.values():
        for x in w["red_reasons"]:
            reasons[x.split(":")[0].split(";")[0].split("(")[0].strip().lstrip("0123456789 ")] += 1
    L += ["Why red (a workflow can have several):", "",
          T(["Reason", "Workflows"], [[k, v] for k, v in reasons.most_common()]), "",
          "That first score counts a vendor plugin type as a red blocker when it has no Kestra route on file. "
          "The target inventory now gives each target its route (plugin, HTTP, replaced, or none). "
          "The brief uses the target inventory, not this colour, to decide reachability.", "",
          "Recomputed with the target inventory (the colour the explorer shows as readiness):", "",
          T(["Colour", "Workflows"], [[k, c["readiness"].get(k, 0)] for k in ("green", "amber", "red")]), ""]
    L += ["## Targets", "",
          T(["Target", "Kestra reach", "Workflows", "Actions", "Distinct operations (read / write)"],
            [[t["name"], t["reach"], len(t["workflows"]), len(t["actions"]),
              f"{len(t['operations'])} ({t['reads']} / {t['writes']})"]
             for k, t in r["targets"].items()]), "",
          "Details in [target-inventory.md](target-inventory.md).", ""]
    L += ["## Domains", "",
          T(["Domain", "Name", "Clusters", "Workflows"],
            [[d["id"], d["name"], ", ".join(d["clusters"]), d["workflows"]] for d in r["domains"]["domains"]]), "",
          "How each grouping was decided: [domains.md](domains.md).", ""]
    L += entry_points_section(r)
    if r.get("plugin_coverage"):
        from . import coverage as CV
        L += CV.markdown(r["plugin_coverage"])
    L += ["## Files", "",
          "- [parse-inventory.md](parse-inventory.md): every workflow and element as parsed",
          "- [clusters.md](clusters.md): the 30 clusters and their cached summaries",
          "- [target-inventory.md](target-inventory.md), `targets.json`",
          "- [wrappers-and-libraries.md](wrappers-and-libraries.md)",
          "- [unexported-calls.md](unexported-calls.md), `unexported-calls.csv`",
          "- [domains.md](domains.md)",
          "- `assess.json`: everything above, for the next stage",
          "- `your-input/corrections.yaml`: corrections a person may add (optional, see INPUTS.md), read on every run", ""]
    return "\n".join(L)


def entry_points_section(r: dict) -> list[str]:
    ep = r.get("entry_points") or {}
    model = ep.get("model") or {}
    res = ep.get("resolution") or {"unresolved": []}
    L = ["## Entry points", ""]
    if not r["counts"].get("entry_points"):
        return L + ["None on record: no vRA export in the estate folder and nothing in "
                    "`your-input/entry-points.yaml` (preflight Q2). A workflow nothing calls may be an entry "
                    "point or unused; the call graph cannot tell.", ""]
    L += ["What starts each workflow from outside vRO, from the vRA export and `your-input/entry-points.yaml`, "
          "matched by vRO workflow ID. Details in the readiness report.", "",
          T(["Workflow", "Entry points"],
            [[_n(r, u), "; ".join(
                (f"subscription {e['name']} on {e.get('event_topic')}" + (" (blocking)" if e.get("blocking") else "")
                 if e["kind"] == "subscription" else f"{e['kind']} {e['name']}") for e in w["entry_points"])
              or "none"] for u, w in sorted(r["workflows"].items())]), ""]
    for c in model.get("catalog_items", []):
        tpl = next((t for t in model.get("cloud_templates", []) if t["name"] == c.get("cloud_template")), None)
        L += [f"Catalog item **{c['name']}** ({c.get('content_source') or 'no content source'}) deploys "
              + (f"cloud template **{tpl['name']}**, which creates "
                 + ", ".join(f"{x.get('name')} ({x.get('type')})" for x in tpl.get("creates") or []) + "."
                 if tpl else (f"workflow {c.get('workflow') or c.get('workflow_id')}." if c.get("workflow_id")
                              else "a cloud template that is not in the export.")), ""]
    if res["unresolved"]:
        L += ["Not in the package:", "",
              T(["Kind", "Entry point", "Workflow", "Why"],
                [[u["kind"], u["name"], u.get("workflow_id") or u.get("workflow_name") or "", u.get("why", "")]
                 for u in res["unresolved"]]), ""]
    return L


def clusters(r: dict) -> str:
    L = _header(r, "Clusters")
    m = r["cluster_method"]
    L += [f"Method: {m['method']}, threshold {m['threshold']}. Weights: "
          + ", ".join(f"{k} {v}" for k, v in m["weights"].items()) + ".", "",
          "Clustered by a fingerprint of each workflow's item types, graph shape, plugin types, SDK calls, signature and normalised scripts.", "",
          "Names and summaries come from `2-understand/cluster-summaries.json` (model-written by Understand), "
          "matched to clusters by member set; a cluster without one has no name here.", ""]
    L += [T(["Cluster", "Size", "Domain", "Name (cached summary)", "First of a kind", "Colour mix"],
            [[c["cluster_id"], c["size"], c["domain"], c["summary_name"] or "", _n(r, c['first_of_a_kind']),
              ", ".join(f"{k} {v}" for k, v in sorted(c["colour_mix"].items()))] for c in r["clusters"]]), ""]
    for c in r["clusters"]:
        L += [f"## {c['cluster_id']}: {c['summary_name'] or ''}", ""]
        if c["summary"]:
            L += [c["summary"], ""]
        L += ["Members: " + ", ".join(_n(r, u) for u in c["members"]), ""]
        if c.get("strongest_pair"):
            p = c["strongest_pair"]
            L += [f"Strongest pair: {_n(r, p['a'])} and {_n(r, p['b'])} at {p['score']}.", ""]
    return "\n".join(L)


def parse_inventory(r: dict) -> str:
    L = _header(r, "Parse inventory")
    L += [f"{r['counts']['workflows_in_export']} workflows parsed with {r['counts']['parse_failures']} failures. "
          "Every workflow in the estate is listed and assessed.", "",
          "`first task` columns: the first task in the file, and the first task the run actually reaches "
          "from `root-name`. Where they differ, a converter that walks the file in order starts in the wrong place.", ""]
    rows = []
    for _u, w in sorted(r["workflows"].items()):
        rows.append([w["name"], w["source"], w["items"], w["script_lines"],
                     ", ".join(f"{k} {v}" for k, v in sorted(w["item_type_counts"].items())),
                     w["root_item"], w["first_task_in_doc_order"] or "",
                     w["first_task_on_root_path"] or "", "yes" if w["doc_order_would_mislead"] else "",
                     w["band"]])
    L += [T(["Workflow", "Source", "Items", "Script lines", "Item types", "Root", "First task in file",
             "First task run", "Differs", "Colour"], rows), ""]
    L += ["## Actions", "",
          T(["Action", "Source", "Lines", "Called by workflows", "Called by actions", "In forms (OGNL)"],
            [[f"`{a['module']}/{a['name']}`", a["source"], a["lines"], len(a["called_by_workflows"]),
              len(a["called_by_actions"]), len(a["referenced_by_forms"])] for a in r["actions"]]), ""]
    return "\n".join(L)


def shown_reach(t: dict) -> str:
    """The target's reach, or, when none of its operations has it (vRO's own services: their operations are
    replaced by design, per the plugin map's gaps), the reaches its operations do have."""
    got = sorted({o.get("reach") for o in t.get("operations") or [] if o.get("reach")})
    if got and t["reach"] not in got:
        return " + ".join(got)
    return t["reach"]


def target_inventory(r: dict) -> str:
    L = _header(r, "Target inventory")
    L += ["Every external system the workflows and the actions touch. Evidence: plugin object types, "
          "calls on typed variables, the Avi plugin client's REST calls, nested calls to workflows that are "
          "not in the export (attributed by the types passed to them), and the actions each workflow calls.", "",
          "Kestra reach comes from `knowledge/plugin-map.yaml`, checked against the Kestra plugin catalogue. "
          "`http` means no Kestra plugin exists and the system's REST API is called with "
          "`io.kestra.plugin.core.http.Request`. `replace` means vRO-internal state with nothing to call: the "
          "Kestra design changes instead. Read or write is judged from the operation's verb.", ""]
    L += [T(["Target", "Reach", "Confirmed", "Workflows", "Actions", "Operations", "Reads", "Writes"],
            [[t["name"], shown_reach(t) + (" (overridden)" if t["reach_overridden"] else ""),
              "yes" if t["confirmed"] else "no", len(t["workflows"]), len(t["actions"]),
              len(t["operations"]), t["reads"], t["writes"]] for t in r["targets"].values()]), ""]
    for k, t in r["targets"].items():
        det = t.get("detected_by")
        by_plugin = det is None or any(d in ("plugin", "plugin type", "plugin library workflow") for d in det)
        L += [f"## {t['name']} (`{k}`)", ""]
        if det:
            L.append("- Found by: " + "; ".join(det))
        L += [f"- vRO plugin: {t['vro_plugin']}" if by_plugin else
              "- vRO plugin: none used here; reached with the HTTP-REST plugin or a REST helper action",
              f"- Kestra reach: **{shown_reach(t)}**" + (", Enterprise Edition" if t["enterprise_only"] else ""),
              f"- Kestra tasks: {', '.join(f'`{x}`' for x in t['kestra_tasks'])}",
              f"- Evidence: {t['kestra_evidence']}"]
        if t.get("api"):
            L.append(f"- API: {t['api']}")
        if t.get("sdk_alternative"):
            L.append(f"- SDK alternative: {t['sdk_alternative']}")
        for u in t["unconfirmed"]:
            if not by_plugin and re.search(r"\bvRO plugin\b", u):
                continue                  # a caveat about the vRO plugin's behaviour, and no plugin is used here
            L.append(f"- Unconfirmed: {u}")
        L += ["", f"Workflows ({len(t['workflows'])}): " + ", ".join(_n(r, u) for u in t["workflows"]), ""]
        if t["declared_only"]:
            L += [f"Of these, {len(t['declared_only'])} only pass the object through (declared type, no call): "
                  + ", ".join(_n(r, u) for u in t["declared_only"]), ""]
        if t["operations"]:
            L += [T(["Operation (from workflows and the actions they call)", "Workflows", "Mode", "Reach"],
                    [[f"`{o['op']}`", o["workflows"], o["mode"], o["reach"]] for o in t["operations"]]), ""]
        if t["action_only_operations"]:
            L += ["Operations found only in actions no dev workflow calls: "
                  + ", ".join(f"`{o}`" for o in t["action_only_operations"]), ""]
    return "\n".join(L)


def unexported(r: dict) -> str:
    L = _header(r, "Unexported calls")
    rows = r["unexported_calls"]
    kinds = Counter(x["kind"] for x in rows)
    L += [f"{len(rows)} calls from workflows go to something that is not in the export. A migration that "
          "converts only the export breaks at each one.", "",
          T(["Kind", "Calls", "What it means"], [
              ["plugin library workflow", kinds.get("plugin library workflow", 0),
               "Shipped with a vRO plugin (the parameters are plugin objects). Replace with a call to the target's API."],
              ["vRO library workflow", kinds.get("vRO library workflow", 0),
               "Shipped with vRO itself. Replace by design (the Kestra side has its own mechanism)."],
              ["unknown workflow", kinds.get("unknown workflow", 0),
               "Nothing says what it is. Ask the owner (preflight Q1)."],
              ["action not in export", kinds.get("action not in export", 0),
               "An action from a module that was not exported."]]), ""]
    by: dict[str, list[dict]] = {}
    for x in rows:
        by.setdefault(x["caller"], []).append(x)
    for caller in sorted(by):
        L += [f"## {_n(r, caller)}", "",
              T(["Item", "Mode", "Called", "ID", "Kind", "Reaches"],
                [[x["item"] or "", x["mode"], x["display_name"] or "", f"`{x['workflow_id']}`", x["kind"],
                  ", ".join(x["reaches"])] for x in by[caller]]), ""]
    return "\n".join(L)


def domains(r: dict) -> str:
    d = r["domains"]
    L = _header(r, "Domains")
    L += ["## Rule", "", d["rule"], "",
          "Code makes the grouping. The model was only asked for a name and a sentence per domain, given the "
          "cached cluster summaries, and cannot move a cluster. A person can rename or move one by adding "
          "`your-input/corrections.yaml`, an optional file (INPUTS.md shows it).", ""]
    L += [T(["Domain", "Key", "Name", "Name from", "Clusters", "Workflows", "Targets"],
            [[x["id"], f"`{x['key']}`", x["name"], x["name_source"], ", ".join(x["clusters"]), x["workflows"],
              ", ".join(x["targets"])] for x in d["domains"]]), ""]
    for x in d["domains"]:
        if x.get("description"):
            L += [f"- **{x['name']}**: {x['description']}"]
    L += ["", "## How each cluster was placed", "",
          T(["Cluster", "Domain key", "Decided by"], [[c["cluster_id"], f"`{c['key']}`", c["how"]]
                                                     for c in d["cluster_rows"]]), ""]
    return "\n".join(L)


def wrappers(r: dict) -> str:
    wf = r["workflows"]
    L = _header(r, "Wrappers and libraries")
    L += ["## Wrapper workflows", "",
          "A wrapper is one call to something else (a nested workflow, an action, or one target operation) "
          "with at most three lines of its own logic and no decisions. The Avi plugin's session plumbing "
          "(initVroWorkflow, finishVroWorkflow, getAviVroClient) is not counted as a call.", "",
          T(["Workflow", "Why"], [[w["name"], w["wrapper_reason"]] for u, w in sorted(wf.items()) if w["wrapper"]]), ""]
    libs = [a for a in r["actions"] if a["library_for"]]
    L += ["## Actions that are clients for a target", "",
          f"{len(libs)} of {len(r['actions'])} actions call a target system. "
          f"{sum(a['lines'] for a in libs)} of {r['counts']['script_lines_actions']} action lines are in them.", "",
          T(["Action", "Target", "Lines", "Operations", "Called", "Wrapper"],
            [[f"`{a['module']}/{a['name']}`", ", ".join(a["library_for"]), a["lines"], len(a["ops"]),
              "yes" if a["called"] else "no", "yes" if a["wrapper"] else ""] for a in libs]), ""]
    others = [a for a in r["actions"] if not a["library_for"]]
    L += ["## Other actions", "",
          T(["Action", "Lines", "Called", "Same code as"],
            [[f"`{a['module']}/{a['name']}`", a["lines"], "yes" if a["called"] else "no",
              ", ".join(f"`{x}`" for x in a["same_code_as"])] for a in others]), ""]
    return "\n".join(L)
