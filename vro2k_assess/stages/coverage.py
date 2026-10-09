"""
Plugin coverage: for every system the estate touches, whether a Kestra plugin covers it today, whether it
would need a new plugin, or whether Kestra reaches it over HTTP.

Built only from the plugin snapshot in kestra-reference/plugins/ (written by tools/kestra_plugins.py from
a running Kestra's /api/v1/plugins and the kestra.io catalogue), never from memory. The plugin map
(data/plugin-map.yaml) says which plugin group or search words stand for a system and which task does
which operation; the snapshot says whether that plugin and those tasks exist, where, and in which
version. When the instance and the catalogue disagree, both are shown.

Systems touched are the Assess targets, plus what the vRA cloud templates create (they matter once vRA
is replaced: Kestra then creates those resources itself), plus vRA itself when an export is on record.
"""
from __future__ import annotations

import glob
import json
import os
import re

from . import common as C

SNAPSHOT_DIR = os.path.join(C.ROOT, "kestra-reference/plugins")
SOURCES = ("installed", "catalog")
HTTP_TASK = "io.kestra.plugin.core.http.Request"
STATUS_LABEL = {"plugin": "Kestra plugin", "partial": "Kestra plugin, some operations over HTTP",
                "http": "Over HTTP (no plugin)", "replace": "Replaced by Kestra features",
                "new plugin": "Needs a new plugin", "trigger": "Kestra trigger"}


def latest_snapshot(folder: str | None = None) -> dict | None:
    files = sorted(glob.glob(os.path.join(folder or SNAPSHOT_DIR, "kestra-plugins-*.json")))
    if not files:
        return None
    snap = json.load(open(files[-1], encoding="utf-8"))
    snap["file"] = os.path.relpath(files[-1], C.ROOT)
    return snap


class Snapshot:
    def __init__(self, snap: dict | None):
        self.snap = snap or {"sources": {}}
        self.by_source = {}
        for s in SOURCES:
            src = self.snap["sources"].get(s) or {}
            self.by_source[s] = src.get("plugins") or [] if src.get("available") else None

    def available(self, s):
        return self.by_source.get(s) is not None

    def group(self, s: str, group: str) -> list[dict]:
        return [p for p in self.by_source.get(s) or [] if p.get("group") == group]

    def owner(self, s: str, cls: str) -> dict | None:
        for p in self.by_source.get(s) or []:
            for kind in ("tasks", "triggers", "apps"):
                if any(t["cls"] == cls for t in p.get(kind) or []):
                    return p
        return None

    def search(self, s: str, words: list[str]) -> list[dict]:
        hits = []
        for p in self.by_source.get(s) or []:
            hay = " ".join([p.get("name") or "", p.get("title") or "", p.get("group") or ""]
                           + [t["cls"] + " " + t.get("title", "") for k in ("tasks", "triggers") for t in p.get(k) or []]).lower()
            if any(re.search(r"(?<![a-z0-9])" + re.escape(w.lower()) + r"(?![a-z0-9])", hay) for w in words):
                hits.append(p)
        return hits


def _classes(plugins: list[dict]) -> set[str]:
    return {t["cls"] for p in plugins for k in ("tasks", "triggers", "apps") for t in p.get(k) or []}


def _side(snap: Snapshot, s: str, group: str | None, words: list[str]) -> dict:
    if not snap.available(s):
        return {"available": False}
    ps = snap.group(s, group) if group else []
    found_by = "group" if ps else None
    if not ps and words:
        ps = snap.search(s, words)
        found_by = "search" if ps else None
    return {"available": True, "found_by": found_by,
            "plugins": [{"name": p["name"], "group": p.get("group"), "version": p.get("version"),
                         "license": p.get("license"), "tasks": [t["cls"] for t in p.get("tasks") or []],
                         "triggers": [t["cls"] for t in p.get("triggers") or []]} for p in ps]}


def _disagree(inst: dict, cat: dict) -> list[str]:
    if not (inst.get("available") and cat.get("available")):
        return []
    out = []
    def by_name(ps):
        d = {}
        for p in ps:
            d.setdefault(p["name"], []).append(p)
        return d
    iv, cv = by_name(inst["plugins"]), by_name(cat["plugins"])

    def vers(ps):
        return ", ".join(sorted(str(p["version"]) for p in ps if p.get("version"))) or "no version"
    for n in sorted(set(iv) | set(cv)):
        if n not in cv:
            out.append(f"{n} is installed ({vers(iv[n])}) but not in the catalogue")
        elif n not in iv:
            out.append(f"{n} is in the catalogue ({vers(cv[n])}) but not installed")
        else:
            if vers(iv[n]) != vers(cv[n]):
                out.append(f"{n}: installed {vers(iv[n])}, catalogue {vers(cv[n])}")
            a = {t for p in iv[n] for t in p["tasks"]}
            b = {t for p in cv[n] for t in p["tasks"]}
            if a != b:
                out.append(f"{n}: tasks only installed {sorted(a - b)}, only in the catalogue {sorted(b - a)}")
    return out


def _cls_where(snap: Snapshot, cls: str) -> dict:
    return {s: (bool(snap.owner(s, cls)) if snap.available(s) else None) for s in SOURCES}


def _row(key: str, t: dict, ops: list[str], snap: Snapshot, via: str) -> dict:
    group = t.get("kestra_group")
    if not group and "kestra_group" not in t:
        # the plugin that ships the first Kestra task the map names for this system
        owner = next((o for c in t.get("kestra_tasks") or [] for s in SOURCES
                      for o in [snap.owner(s, c)] if o), None)
        group = owner.get("group") if owner else None
    words = t.get("kestra_search_words") or []
    inst, cat = _side(snap, "installed", group, words), _side(snap, "catalog", group, words)
    found = [p for side in (inst, cat) for p in side.get("plugins") or []]
    task_map = []
    unmatched = []
    for op in ops:
        m = next((x for x in t.get("op_tasks") or [] if re.search(x["match"], op)), None)
        if m and group:
            cls = f"{group}.{m['task']}"
            where = _cls_where(snap, cls)
            task_map.append({"op": op, "task": cls, "where": where})
            if not any(where.values()):
                unmatched.append(op)
        else:
            unmatched.append(op)
    reach = t.get("reach", "none")
    if key == "rest-host":
        status, unmatched = "http", []          # a generic REST client: HTTP by definition
    elif reach == "replace" or key == "vro-platform":
        status, unmatched = "replace", []
    elif found and (inst.get("found_by") == "group" or cat.get("found_by") == "group"):
        status = "partial" if unmatched and ops else "plugin"
    elif reach in ("http", "plugin") and (t.get("api") or key == "rest-host" or reach == "http"):
        status = "http"
    else:
        status = "new plugin"
    http_where = _cls_where(snap, HTTP_TASK)
    return {"key": key, "system": t.get("name") or key, "via": via, "status": status,
            "label": STATUS_LABEL[status], "kestra_group": group, "search_words": words,
            "installed": inst, "catalog": cat, "disagreements": _disagree(inst, cat),
            "operations": len(ops), "ops": list(ops), "task_map": task_map, "unmatched_ops": unmatched,
            "http_task": HTTP_TASK if status in ("http", "partial") else None, "http_task_where": http_where,
            "api": t.get("api"), "enterprise_only": any((p.get("license") or "") == "EE" for p in found)}


def coverage(targets: dict, ep_model: dict, pm: dict) -> dict:
    """The coverage table. targets: Assess target inventory; ep_model: entry points model."""
    raw = latest_snapshot()
    snap = Snapshot(raw)
    pmt = pm.get("targets") or {}
    rows = []
    for key, t in sorted(targets.items()):
        spec = dict(pmt.get(key) or {}, **{k: v for k, v in t.items() if v not in (None, [], "")})
        ops = [o["op"] for o in t.get("operations") or []] + list(t.get("action_only_operations") or [])
        rows.append(_row(key, spec, ops, snap, "workflow code"))
    # What the cloud templates create: only reached from Kestra once vRA is replaced.
    rtypes = {}
    for tpl in ep_model.get("cloud_templates") or []:
        for r in tpl.get("creates") or []:
            if (r.get("network_type") or "").lower() == "existing":
                continue                      # referenced, not created: nothing for a Kestra task to do
            rtypes.setdefault(r["type"], []).append(f"{tpl['name']}/{r['name']}")
    merged = {}
    for rtype, where in sorted(rtypes.items()):
        key = next((k for k, v in pmt.items() for p in v.get("vra_resource_types") or [] if rtype.startswith(p)), None)
        spec = pmt.get(key) or {"name": rtype}
        tasks = next((v for p, v in (spec.get("resource_tasks") or {}).items() if rtype == p), None)
        r = _row(key or rtype, spec, [], snap, "cloud template (if vRA is replaced)")
        r["resource_type"] = rtype
        r["created_by"] = where
        if tasks is not None:
            r["task_map"] = [{"op": f"create and delete {rtype}", "task": f"{spec.get('kestra_group')}.{x}",
                              "where": _cls_where(snap, f"{spec.get('kestra_group')}.{x}")} for x in tasks]
            if not tasks:
                r["unmatched_ops"] = [rtype]
        k = r["key"]
        if k in merged:
            m = merged[k]
            m["task_map"] += r["task_map"]
            m["unmatched_ops"] += r["unmatched_ops"]
            m["created_by"] += where
            m["resource_type"] += f", {rtype}"
        else:
            merged[k] = r
    for r in merged.values():
        if r["status"] == "plugin" and r["unmatched_ops"]:
            r["status"], r["label"] = "partial", STATUS_LABEL["partial"]
        rows.append(r)
    # vRA itself: the trigger source
    if ep_model.get("subscriptions") or ep_model.get("catalog_items") or ep_model.get("cloud_templates"):
        spec = pmt.get("vra") or {"name": "VMware vRA"}
        r = _row("vra", spec, [], snap, "entry points (starts the workflows)")
        r["status"], r["label"] = "trigger", STATUS_LABEL["trigger"]
        r["task_map"] = [{"op": "vRA calls Kestra (if vRA stays)", "task": "io.kestra.plugin.core.trigger.Webhook",
                          "where": _cls_where(snap, "io.kestra.plugin.core.trigger.Webhook")},
                         {"op": "catalog item (if vRA is replaced)", "task": "io.kestra.plugin.ee.apps.Execution",
                          "where": _cls_where(snap, "io.kestra.plugin.ee.apps.Execution")}]
        rows.append(r)
    # vRO state kept in configuration elements: the KV store replaces it
    for r in rows:
        if r["key"] == "vro-platform":
            r["task_map"] = [{"op": "state and settings in configuration elements", "task": c,
                              "where": _cls_where(snap, c)} for c in
                             ("io.kestra.plugin.core.kv.Set", "io.kestra.plugin.core.kv.Get", "io.kestra.plugin.core.kv.Delete")]
    meta = {"file": raw.get("file") if raw else None, "taken": raw.get("taken") if raw else None,
            "sources": {s: ({k: v for k, v in (raw["sources"].get(s) or {}).items() if k != "plugins"}
                            | {"plugins": len((raw["sources"].get(s) or {}).get("plugins") or [])})
                        if raw and s in raw["sources"] else {"available": False, "reason": "not in the snapshot"}
                        for s in SOURCES}}
    return {"snapshot": meta, "rows": rows}


def markdown(cov: dict) -> list[str]:
    """The coverage section for estate-report.md."""
    m = cov["snapshot"]
    L = ["## Plugin coverage", ""]
    if not m["file"]:
        return L + ["No plugin snapshot in kestra-reference/plugins/. Run `python tools/kestra_plugins.py` "
                    "(read-only) and re-run Assess.", ""]
    src = "; ".join(f"{s}: " + (f"{v.get('plugins')} plugins from {v.get('url')}"
                                + (f" (Kestra {v.get('kestra_version')} {v.get('edition')})" if v.get("kestra_version") else "")
                                if v.get("available") else f"unavailable ({v.get('reason', '')})")
                    for s, v in m["sources"].items())
    L += [f"Snapshot taken {m['taken']} (`{m['file']}`). {src}.", "",
          "| System | Touched through | Coverage | Kestra plugin (installed / catalogue) | Task types | Not covered by a task |",
          "|---|---|---|---|---|---|"]
    for r in cov["rows"]:
        def side(x):
            if not x.get("available"):
                return "not checked"
            return ", ".join(f"{p['name']} {p['version']}" if p.get("version") else
                             ("built in" if p["name"] == "core" else p["name"]) for p in x["plugins"]) or "none"
        tasks = ", ".join(sorted({t["task"].rsplit(".", 2)[-2] + "." + t["task"].rsplit(".", 1)[-1]
                                  if t["task"].count(".") > 3 else t["task"] for t in r["task_map"]}))
        L.append(f"| {r['system']} | {r['via']} | {r['label']} | {side(r['installed'])} / {side(r['catalog'])} | "
                 f"{tasks or '-'} | {'; '.join(r['unmatched_ops'][:6]) + (' ...' if len(r['unmatched_ops']) > 6 else '') or '-'} |")
    dis = [(r["system"], d) for r in cov["rows"] for d in r["disagreements"]]
    if dis:
        L += ["", "Where the instance and the catalogue disagree:", ""] + [f"- {s}: {d}" for s, d in dis]
    L.append("")
    return L
