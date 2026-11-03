"""
The translation table, and the number it produces: what each kind of thing in the estate becomes in Kestra.

One row per kind of thing (the F5 client actions, the lifecycle workflows, the catalog items, the state kept in
configuration elements ...), with what it becomes, how (a named plugin, HTTP, a core task, a direct conversion,
or redesign, manual work, an answer), how many there are and their lines of script. Every element of the estate
is in exactly one row: each workflow and action once (by its triage outcome and what it rests on), each
configuration and resource element, and, from the vRA export, each catalog item, cloud template, resource and
subscription.

The number is the share of the estate with a known, mechanical migration path (plugin, HTTP, core task, direct
conversion, wrapper, duplicate, retire) against the share that needs redesign, manual work or an answer, by
element and by lines of script. It is an estimate from the brief's own outcomes, not a measured conversion
rate: nothing has been converted. It is computed from the table, so the two cannot disagree.
"""
from __future__ import annotations

from collections import defaultdict

from . import ids as I
from . import triggers as TG

MECHANICAL = ("plugin", "HTTP", "plugin and HTTP", "core task", "direct conversion", "wrapper", "duplicate", "retire")
# mapped by the tool, but a person still designs it: a catalog item becoming an App, a cloud template a flow,
# subscriptions its steps, vRA's payload typed inputs
DESIGNED = ("designed",)
NOT_MECHANICAL = ("redesign", "manual", "needs an answer", "needs a model")


def path_class(how: str) -> str:
    """mechanical, designed, or open (redesign, manual work, an answer)."""
    return "mechanical" if how in MECHANICAL else ("designed" if how in DESIGNED else "open")

WF_HOW = {
    "Convert": ("a Kestra flow", "direct conversion"),
    "Near-duplicate": ("a Kestra flow, stamped from its first of a kind", "duplicate"),
    "Wrapper": ("nothing: its callers call what it wraps", "wrapper"),
    "Retire": ("deleted", "retire"),
    "Needs owner input": ("a Kestra flow, once its question is answered", "needs an answer"),
    "Manual": ("a flow written by hand", "manual"),
    "Needs a model": ("decided once a model has read it", "needs a model"),
    "Rebuild": ("rebuilt for Kestra", "redesign"),
    "Plugin-covered library": ("a Kestra plugin", "plugin"),
}
PLUGIN_DOCS = "https://kestra.io/plugins/"


def _docs(plugin: str | None) -> str | None:
    return PLUGIN_DOCS + plugin if plugin else None


def table(assess: dict, brief: dict) -> dict:
    wf, tri, atri = assess["workflows"], brief["triage"], brief["action_triage"]
    targets = assess["targets"]
    cov = {r["key"]: r for r in (assess.get("plugin_coverage") or {}).get("rows") or []}
    actions = {a["key"]: a for a in assess["actions"]}
    rows: dict[tuple, dict] = {}

    def row(rid, kind, becomes, how, *, lines=True, evidence=None):
        r = rows.get(rid)
        if r is None:
            r = rows[rid] = {"id": "-".join(str(x) for x in rid).lower().replace(" ", "-"), "kind": kind,
                             "becomes": becomes, "how": how, "mechanical": how in MECHANICAL,
                             "path": path_class(how), "count": 0,
                             "lines": 0 if lines else None, "items": [], "evidence": list(evidence or [])}
        return r

    def add(r, name, *, lines=0, link=None, why=None, eid=None):
        r["count"] += 1
        if r["lines"] is not None:
            r["lines"] += lines or 0
        r["items"].append({"name": name, "link": link, "why": why, "eid": eid, "lines": lines or 0})

    # ---- workflows: by lifecycle group or domain, and outcome
    for u in sorted(wf, key=lambda u: wf[u]["name"]):
        w, t = wf[u], tri[u]
        o = t["outcome"]
        if o == "Near-duplicate" and (t.get("follows") or {}).get("outcome") not in (None, "Convert", "Near-duplicate"):
            o = t["follows"]["outcome"]
        becomes, how = WF_HOW[o]
        where = "Lifecycle workflows" if w.get("lifecycle_group") else "Workflows"
        if w.get("lifecycle_group") and TG.started_by_vra(w):
            where = "Lifecycle workflows (started by vRA events)"
        r = row(("wf", where, how), where, becomes, how,
                evidence=[{"label": "the specs", "href": "workflows/index.html"}])
        add(r, w["name"], lines=w["script_lines"], link=f"workflows/{w['short']}.html", why=t["reason"], eid=f"wf:{u}")

    # ---- actions: by what the outcome rests on, the system, and the outcome
    def systems(k, seen=None) -> list[str]:
        seen = seen or set()
        if k in seen or k not in actions:
            return []
        seen.add(k)
        a = actions[k]
        own = [x for x in a.get("library_for") or [] if x in targets and x not in ("rest-host",)]
        if own:
            return own
        out = []
        for c in a.get("called_by_actions") or []:
            if (atri.get(c) or {}).get("basis") in ("client",):
                out += systems(c, seen)
        return sorted(set(out))

    for k in sorted(atri, key=lambda k: actions[k]["name"]):
        t, a = atri[k], actions[k]
        o, basis = t["outcome"], t.get("basis")
        sysk = systems(k)
        sysn = " and ".join(targets[x].get("short_name") or targets[x]["name"] for x in sysk)
        ev = []
        if o == "Plugin-covered library" and basis == "client":
            plugs = sorted({p["name"] for x in sysk for p in ((cov.get(x) or {}).get("catalog") or {}).get("plugins") or []})
            ev = [{"label": f"{p} documentation", "href": _docs(p), "external": True} for p in plugs]
            ev += [{"label": "plugin coverage", "href": "plugins.html"}]
            gap = [x for x in sysk if (targets.get(x) or {}).get("pattern_gap")]
            if not sysk:
                # a client of REST hosts the export does not name: HTTP tasks, whatever the host is
                r = row(("a", "client", "", "plugin"), "HTTP client actions",
                        "HTTP tasks (io.kestra.plugin.core.http.Request)", "plugin",
                        evidence=[{"label": "core HTTP task documentation", "href": _docs("core"), "external": True}])
            else:
                r = row(("a", "client", sysn, "plugin"), f"{sysn} client actions",
                        "tasks of " + (", ".join(plugs) or "a Kestra plugin")
                        + (", which does not cover this way of calling it, or HTTP tasks (see why)" if gap else ""),
                        "plugin", evidence=ev)
        elif o == "Rebuild" and basis == "client":
            # rebuilt on HTTP where the system has no plugin; its uses for a system with one go with the plugin
            http = bool(sysk) and all((targets.get(x) or {}).get("reach") in ("http", "plugin") for x in sysk) \
                and any((targets.get(x) or {}).get("reach") == "http" for x in sysk)
            r = row(("a", "client", sysn, "http" if http else "redesign"), f"{sysn or 'Client'} client actions",
                    "HTTP tasks (io.kestra.plugin.core.http.Request) on the system's REST API" if http
                    else "a new client, rebuilt for Kestra", "HTTP" if http else "redesign",
                    evidence=[{"label": "plugin coverage", "href": "plugins.html"},
                              {"label": "core HTTP task documentation", "href": _docs("core"), "external": True}])
        elif o == "Rebuild" and basis == "rest-helper":
            r = row(("a", "rest-helper"), "Shared REST helpers",
                    "one task per call site: " + _first(sorted({f"{u['name']}: {u['replaced_by']}"
                                                                for u in t.get("uses") or []}), 3),
                    "plugin and HTTP", evidence=[{"label": "plugin coverage", "href": "plugins.html"}])
        elif o == "Plugin-covered library" and basis == "rest-helper":
            r = row(("a", "rest-helper-pc"), "Generic REST helpers",
                    "one plugin task per call site", "plugin", evidence=[{"label": "plugin coverage", "href": "plugins.html"}])
        elif o == "Rebuild" and basis == "vro-state":
            r = row(("a", "vro-state"), "Actions that read or write vRO's own state",
                    "Kestra's KV store, namespace variables, namespace files and secrets (core tasks)", "core task",
                    evidence=[{"label": "KV store documentation", "href": "https://kestra.io/docs/concepts/kv-store",
                               "external": True}])
        elif o == "Rebuild" and basis == "payload":
            r = row(("a", "payload"), "Actions that parse vRA's event payload",
                    "typed flow inputs, one set per flow (nothing to convert)", "designed",
                    evidence=[{"label": "vRA view", "href": "vra.html"}])
        elif o == "Convert":
            r = row(("a", "with-flows"), "Helper actions", "logic inside the flows that use them",
                    "direct conversion")
        else:
            becomes, how = WF_HOW.get(o, ("rebuilt for Kestra", "redesign"))
            label = {"Near-duplicate": "Duplicate actions", "Wrapper": "Wrapper actions", "Retire": "Unused actions",
                     "Needs owner input": "Actions waiting on an answer", "Manual": "Actions rewritten by hand",
                     "Needs a model": "Actions that go with workflows a model has not read",
                     "Rebuild": "Actions rebuilt with their callers"}.get(o, f"{o} actions")
            r = row(("a", o), label, becomes.replace("a Kestra flow", "converted with its callers"), how)
        sy = set(sysk) | {u.get("system") for u in t.get("uses") or [] if u.get("system")}
        r["systems"] = sorted(set(r.get("systems") or []) | {x for x in sy if x in targets})
        add(r, a["name"], lines=a["lines"], why=t["reason"], eid=f"a:{k}")

    # ---- configuration and resource elements
    state_paths = {p for w in wf.values() for p in (w.get("state") or {}).get("writes") or []}
    for e in sorted(assess.get("elements") or [], key=lambda e: (e["kind"], e["category"], e["name"])):
        path = e["category"].replace(".", "/")
        if e["kind"] == "ConfigurationElement" and path in state_paths:
            r = row(("el", "state"), "State in configuration elements", "Kestra's KV store (decided per path)",
                    "core task", lines=False,
                    evidence=[{"label": "KV store documentation", "href": "https://kestra.io/docs/concepts/kv-store",
                               "external": True}])
        elif e["kind"] == "ConfigurationElement":
            r = row(("el", "settings"), "Settings in configuration elements",
                    "namespace variables, KV entries and secrets", "core task", lines=False)
        else:
            r = row(("el", "resource"), "Templates and files in resource elements", "namespace files", "core task",
                    lines=False)
        add(r, f"{path}/{e['name']}", eid=("el-state:" if r["id"] == "el-state" else "el:") + f"{path}/{e['name']}")

    # ---- vRA: catalog items, cloud templates, their resources, subscriptions
    tp = brief.get("trigger_plan") or {}
    front = tp.get("front_end")
    model = (assess.get("entry_points") or {}).get("model") or {}
    q9 = front == TG.BOTH
    rep = [w for w in brief["plan"]["waves"] if w.get("kind") == "vra-replacement"]
    ev_vra = [{"label": "vRA view", "href": "vra.html"}]
    for c in model.get("catalog_items") or []:
        r = (row(("vra", "catalog"), "Catalog items", "a Kestra App or vRA calling Kestra (depends on whether vRA stays)",
                 "needs an answer", lines=False, evidence=ev_vra) if q9 else
             row(("vra", "catalog"), "Catalog items", "a Kestra App (io.kestra.plugin.ee.apps.Execution) with the "
                 "form's fields as inputs" if front in TG.REPLACING else "stays in vRA; reaches Kestra through "
                 "its subscriptions", "designed" if front in TG.REPLACING else "core task", lines=False,
                 evidence=ev_vra))
        add(r, c["name"], link="vra.html", eid=f"vra:{c['name']}")
    for t in model.get("cloud_templates") or []:
        r = (row(("vra", "template"), "Cloud templates", "a Kestra flow, or stays in vRA (depends on whether vRA stays)",
                  "needs an answer", lines=False, evidence=ev_vra) if q9 else
             row(("vra", "template"), "Cloud templates", "a Kestra flow with a create and a delete path"
                 if front in TG.REPLACING else "stays in vRA", "designed" if front in TG.REPLACING else "core task",
                 lines=False, evidence=ev_vra))
        add(r, t["name"], link="vra.html", eid=f"vra:{t['name']}")
        for x in t.get("creates") or []:
            sc = next((s for w in rep for s in w["scope"] if s["kind"] == "resource"
                       and s["name"].startswith(f"{x['name']} (")), None)
            if q9 or front not in TG.REPLACING:
                r = row(("vra", "resource", "q9" if q9 else "kept"), "Cloud template resources",
                        "built by Kestra tasks, or by vRA (depends on whether vRA stays)" if q9 else "built by vRA, as today",
                        "needs an answer" if q9 else "core task", lines=False, evidence=ev_vra)
            elif sc and sc.get("gap"):
                r = row(("vra", "resource", "gap"), "Cloud template resources with no Kestra task",
                        "rebuilt: nothing in the plugin snapshot creates them", "redesign", lines=False,
                        evidence=[{"label": "plugin coverage", "href": "plugins.html"}])
            elif sc and "existing" in sc["kestra"]:
                r = row(("vra", "resource", "existing"), "Existing networks the template places machines on",
                        "referenced by name in the flow (nothing is created)", "designed", lines=False,
                        evidence=ev_vra)
            else:
                r = row(("vra", "resource", "plugin"), "Cloud template machines",
                        ((sc or {}).get("kestra") or "Kestra plugin tasks") + ", with the image, flavor and placement "
                        "set in the flow", "designed", lines=False,
                        evidence=[{"label": "plugin-ee-vmware documentation", "href": _docs("plugin-ee-vmware"),
                                   "external": True}, {"label": "plugin coverage", "href": "plugins.html"}])
            add(r, f"{t['name']}: {x['name']} ({x['type']})", link="vra.html", eid=f"vra:{t['name']}")
    for s in model.get("subscriptions") or []:
        r = (row(("vra", "sub"), "Event subscriptions", "a webhook trigger or a step of the flow (depends on whether vRA stays)",
                  "needs an answer", lines=False, evidence=ev_vra) if q9 else
             row(("vra", "sub"), "Event subscriptions", "steps of the cloud template's flow (Subflow)"
                 if front in TG.REPLACING else "a Kestra webhook vRA calls",
                 "designed" if front in TG.REPLACING else "core task", lines=False, evidence=ev_vra))
        add(r, s["name"], link="vra.html", why=f"{s.get('event_topic')}" + (", blocking" if s.get("blocking") else ""),
            eid=f"vra-sub:{s['name']}")

    # largest kinds first; a kind's rows together, the mechanical one first
    size: defaultdict[str, int] = defaultdict(int)
    for r in rows.values():
        size[r["kind"]] += r["count"]
    out = sorted(rows.values(), key=lambda r: (-size[r["kind"]], r["kind"],
                                               ("mechanical", "designed", "open").index(r["path"]), -r["count"]))
    for r in out:
        r["evidence"].append({"label": f"the {r['count']} item{'s' if r['count'] != 1 else ''}",
                              "href": f"translation.html#{r['id']}"})
    return {"rows": out, "number": number(out)}


def _first(xs: list[str], n: int) -> str:
    return "; ".join(xs[:n]) + (f"; and {len(xs) - n} more" if len(xs) > n else "")


def number(rows: list[dict]) -> dict:
    """The shares, by element and by lines: mechanical, designed (together: a known path), and open."""
    el = sum(r["count"] for r in rows)
    ln = sum(r["lines"] or 0 for r in rows)
    out = {"elements": el, "lines": ln}
    for c in ("mechanical", "designed", "open"):
        out[f"elements_{c}"] = sum(r["count"] for r in rows if r["path"] == c)
        out[f"lines_{c}"] = sum(r["lines"] or 0 for r in rows if r["path"] == c)
    out["elements_known"] = out["elements_mechanical"] + out["elements_designed"]
    out["lines_known"] = out["lines_mechanical"] + out["lines_designed"]
    for k in ("mechanical", "designed", "known"):
        out[f"share_{k}_elements"] = round(out[f"elements_{k}"] / el, 3) if el else 0.0
        out[f"share_{k}_lines"] = round(out[f"lines_{k}"] / ln, 3) if ln else 0.0
    # the recommendation reads the known path
    out["share_elements"], out["share_lines"] = out["share_known_elements"], out["share_known_lines"]
    by_how: defaultdict[str, dict[str, int]] = defaultdict(lambda: {"elements": 0, "lines": 0})
    for r in rows:
        by_how[r["how"]]["elements"] += r["count"]
        by_how[r["how"]]["lines"] += r["lines"] or 0
    out["by_how"] = dict(by_how)
    out["label"] = "an estimate from reading the export, not a measured conversion rate (nothing has been converted)"
    return out


# ------------------------------------------------------------------ ready now

def ready(assess: dict, brief: dict, table_: dict) -> dict:
    """
    Which elements are ready now: on a known path, in a wave, and no open entry criterion concerns them. Each
    criterion concerns part of its wave, from what closes it: a defect, spec question or review its workflow; an
    endpoint, TLS, authentication or client route the elements reaching that system; the state store the
    elements keeping that state; the edition the elements on Enterprise-only tasks; the vRA replacement's
    choices the vRA items; anything else (Q3, Q4, Q9, the lifecycle group, a wave's exit) the
    whole wave. Sets each item's `ready` and returns the counts, written into the number.
    """
    wf, targets, atri = assess["workflows"], assess["targets"], brief["action_triage"]
    by_short = {w["short"]: u for u, w in wf.items()}
    actions = {a["key"]: a for a in assess["actions"]}
    items = [i for r in table_["rows"] for i in r["items"]]

    def a_sys(k):
        a = actions.get(k) or {}
        own = set(a.get("library_for") or []) | set(a.get("reaches") or [])
        own |= {x["system"] for x in (atri.get(k) or {}).get("uses") or []}
        return own
    waves = brief["plan"]["waves"]
    member: dict[str, set] = {}
    for w in waves:
        if w.get("kind") == "vra-replacement":
            ids = {i["eid"] for i in items if i["eid"] and i["eid"].startswith("vra")}
            ids |= {i["eid"] for i in items if i["eid"] and i["eid"].startswith("a:")
                    and (atri.get(i["eid"][2:]) or {}).get("basis") == "payload"}
        else:
            us = set(w["workflows"])
            ids = {f"wf:{u}" for u in us}
            ids |= {f"a:{k}" for k, t in atri.items() if set(t.get("workflow_uids") or []) & us}
            ids |= {i["eid"] for i in items if i["eid"] and i["eid"].startswith(("el:", "el-state:"))}
        for e in ids:
            member.setdefault(e, set()).add(w["wave"])

    def concerns(w, c):
        key = str(c.get("field") or c.get("key") or "")
        fam, _, rest = key.partition(".")
        ids = {e for e, ws in member.items() if w["wave"] in ws}
        if c.get("kind") == "outcome":
            return {f"wf:{c['key']}"}
        if fam in ("defect", "question", "spec", "missing"):
            u = by_short.get(rest.split(".")[0])
            return {f"wf:{u}"} if u else ids
        if fam in ("endpoint", "tls", "auth", "client-route", "reads-only"):
            ts = set(rest.split("+"))
            ts = {t for t in targets if G_key(t) in ts}
            return {e for e in ids if (e.startswith("a:") and a_sys(e[2:]) & ts)
                    or (e.startswith("wf:") and set(wf[e[3:]].get("targets") or []) & ts)}
        if fam == "state-store":
            return {e for e in ids if e.startswith("el-state:") or (e.startswith("a:")
                    and (atri.get(e[2:]) or {}).get("basis") == "vro-state")
                    or (e.startswith("wf:") and (wf[e[3:]].get("state") or {}).get("writes"))}
        if fam == "edition":
            ee = {t for t, x in targets.items() if x.get("enterprise_only")}
            return {e for e in ids if e.startswith("vra") or (e.startswith("a:") and a_sys(e[2:]) & ee)
                    or (e.startswith("wf:") and set(wf[e[3:]].get("targets") or []) & ee)}
        if fam in ("typed-inputs",):
            return {e for e in ids if e.startswith("a:") and (atri.get(e[2:]) or {}).get("basis") == "payload"}
        if fam in ("mappings", "ip-allocation", "day2-scale", "app", "existing-deployments", "gap",
                   "deployment-record", "cancel", "failure", "timeout"):
            return {e for e in ids if e.startswith("vra")} or ids
        return ids                                   # Q3/Q4/Q9, lifecycle, writes, wave exit

    blocked: dict[str, list[str]] = {}
    for w in waves:
        for e in w["entry_criteria"]:
            if e.get("status") == "done":
                continue
            c = e.get("closes") or {}
            for x in concerns(w, c):
                blocked.setdefault(x, []).append(I.entry_key(str(c.get("field") or c.get("key") or e["criterion"])))
    n_el = n_ln = 0
    for r in table_["rows"]:
        for i in r["items"]:
            ok = r["path"] != "open" and i["eid"] in member and not blocked.get(i["eid"])
            i["ready"] = ok
            i["waits_on"] = sorted(set(blocked.get(i["eid"]) or []))[:5]
            if ok:
                n_el += 1
                n_ln += i["lines"] or 0
        r["ready"] = sum(1 for i in r["items"] if i["ready"])
    num = table_["number"]
    num.update(elements_ready=n_el, lines_ready=n_ln,
               share_ready_elements=round(n_el / num["elements"], 3) if num["elements"] else 0.0,
               share_ready_lines=round(n_ln / num["lines"], 3) if num["lines"] else 0.0)
    return num


def G_key(t: str) -> str:
    from .gates import key_part
    return key_part(t)


def pct(x: float, ready_share: float | None = None) -> str:
    """A share as a person reads it (ready_share is accepted and ignored: counts sit next to every share)."""
    return f"{x:.0%}"
