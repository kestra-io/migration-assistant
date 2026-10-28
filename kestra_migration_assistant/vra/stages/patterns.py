"""
Patterns an estate repeats, found by reading the code, with no model: what the estate interview asks about, what
the brief lists as missing from the package, and what the tool assumes instead of asking.

Read from the parse cache of the sanitized copy (data/1-assess/work/) and assess.json, written to
data/1-assess/patterns.json after Assess:

- `runtime_config`: shared modules that pick a configuration element at run time, by a value they are given
  (`e.name === name`) or a path they build, instead of reading one element the workflow binds. The workflows that
  reach each one, and the categories it opens.
- `vault`: code that reads secrets from HashiCorp Vault (a `X-Vault-Token` header, a `/v1/secret/` or
  `/v1/kv/` path, or a module or action named for Vault), and the workflows that reach it.
- `rest_in_code`: REST hosts made in a script (`RESTHostManager.createTransientHostFrom`, `createHost`,
  `new RESTHost`) instead of hosts added to the HTTP-REST plugin's inventory: the plugin is installed, not
  configured. Split into workflows whose own scriptable tasks do it, and shared modules that do it.
- `remote`: calls to workflows on another vRO cluster through the multi-node plugin (`VCO:` objects, a remote
  workflow's `execute`), which no export of this cluster holds **(to confirm with a vRO expert: the scripting
  names differ between vRO 7.x and 8.x)**.
- `nesting`: how deep modules call modules, and the modules called at any depth that the export does not have.
- `scope`: workflows whose name or folder says they are old, copies or tests.
- `shared`: the modules the most workflows reach, with what their code calls.
- `missing`: everything to add to the package, each with what calls it and how to add it.
- `mechanics`: the vRO mechanics the code uses that have a Kestra equivalent the tool assumes (certificates,
  host verification, retries and counters), with where.
"""
from __future__ import annotations

import os
import re
from collections import defaultdict

from ..parse import jscalls
from . import common as C

CATEGORY_CALL = re.compile(r"getConfigurationElementCategoryWithPath\s*\(\s*([^)]*)\)")
ELEMENT_BY_VALUE = re.compile(r"\.name\s*===?\s*([A-Za-z_$][\w$.]*)\s*\)|\b([A-Za-z_$][\w$.]*)\s*===?\s*[\w$.]+\.name\b")
VAULT = re.compile(r"X-Vault-Token|/v1/(?:secret|kv|auth)/|\bvault\b", re.I)
REST_IN_CODE = re.compile(r"RESTHostManager\s*\.\s*(?:createTransientHostFrom|createHost)\s*\(|new\s+RESTHost\s*\(")
REMOTE = re.compile(r"VCOHostManager|VCOConnectionManager|getRemoteWorkflow|executeRemote\w*\s*\(|"
                    r"\bVCO\s*\.\s*\w+|remoteWorkflow\w*\s*\.\s*execute\s*\(")
REMOTE_ID = re.compile(r"id='[^',]*,([0-9a-fA-F-]{36})'")
TRUST = re.compile(r"\bimportCertificate|SSLCertificateManager|trustStore|\.importCertificate\w*\s*\(|"
                   r"KeystoreManager", re.I)
HOST_VERIFICATION = re.compile(r"hostVerification\s*=")
RETRY = re.compile(r"\b(?:retr(?:y|ies)|attempts?|maxTries|tries)\b\s*(?:[<>]=?|\+\+|\+=|-=|=)", re.I)
SCOPE_NAME = re.compile(r"^(?:old|test|tmp|temp|copy of|deprecated|backup|bak)(?:[\s_-]|$)|[\s_-](?:old|deprecated|"
                        r"backup|copy|test)$|\(copy\)|\bvro[\s_-]?7\b", re.I)
SCOPE_FOLDER = {"test", "tests", "old", "archive", "archived", "deprecated", "sandbox", "backup", "trash", "tmp"}


def _literal(arg: str) -> str | None:
    """The string a call's argument is, when it is one literal; None when it is built at run time."""
    m = re.fullmatch(r"\s*([\"'])([^\"']*)\1\s*", arg)
    return m.group(2) if m else None


def _prefix(arg: str) -> str:
    """The literal start of a path built at run time: `"Config/" + region` -> `Config/`."""
    m = re.match(r"\s*([\"'])([^\"']*)\1", arg)
    return m.group(2) if m else ""


def _scripts(model: dict) -> list[str]:
    return [str(it.get("script") or "") for it in model.get("items") or [] if it.get("script")]


def find(assess: dict | None = None, idx: dict | None = None, models: dict | None = None) -> dict:
    """The patterns of the configured estate (or of the given assess.json, parse index and workflow models)."""
    if assess is None:
        assess = C.read_json(os.path.join(C.ASSESS, "assess.json"))
    if idx is None:
        p = os.path.join(C.WORK, "index.json")
        idx = C.read_json(p) if os.path.exists(p) else {"other_elements": []}
    wf = assess["workflows"]
    if models is None:
        models = {}
        for u in wf:
            p = os.path.join(C.WORK, "model", f"{u}.json")
            if os.path.exists(p):
                models[u] = C.read_json(p)
    acts = assess.get("actions") or []
    script_of = {(e["category_path"], e["name"]): str(e.get("script") or "")
                 for e in idx.get("other_elements") or [] if e.get("kind") in ("ScriptModule", "ScriptingAction")}
    by_key = {(a["module"], a["name"]): a for a in acts}
    by_uid = {a["uid"]: a for a in acts}
    names_of = {u: w["name"] for u, w in wf.items()}

    # which workflows reach each action, through any chain of actions
    reach: dict[tuple, set] = defaultdict(set)
    for a in acts:
        reach[(a["module"], a["name"])] |= set(a.get("called_by_workflows") or [])
    for _ in range(12):
        changed = False
        for a in acts:
            mine = reach[(a["module"], a["name"])]
            for c in a.get("called_by_actions") or []:
                ca = by_uid.get(c) or next((x for x in acts if x["key"] == c), None)
                if ca is None:
                    continue
                theirs = reach[(ca["module"], ca["name"])]
                if not theirs <= mine:
                    mine |= theirs
                    changed = True
        if not changed:
            break

    def users(key: tuple) -> list[str]:
        return sorted(reach.get(key) or [], key=lambda u: names_of.get(u, u))

    def label(key: tuple) -> str:
        return f"{key[0]}/{key[1]}"

    configured = {e["category_path"] for e in idx.get("other_elements") or [] if e.get("kind") == "ConfigurationElement"}

    runtime, vault, rest_mods, trust, hostver, retry = [], [], [], [], [], []
    for key, src in sorted(script_of.items()):
        code = jscalls.mask(src)
        calls = [m.group(1) for m in CATEGORY_CALL.finditer(src)]
        by_value = [x for m in ELEMENT_BY_VALUE.finditer(code) for x in m.groups() if x]
        built = [c for c in calls if _literal(c) is None]
        if calls and (built or by_value):
            cats = sorted({_literal(c) or _prefix(c) for c in calls} - {""})
            runtime.append({"module": label(key), "name": key[1], "workflows": users(key), "categories": cats,
                            "by": "a path built at run time" if built else "a value it is given",
                            "in_package": [c for c in cats if any(p == c.replace("/", ".") or p.startswith(
                                c.replace("/", ".")) for p in configured)]})
        if VAULT.search(src) or "vault" in key[0].lower() or "vault" in key[1].lower():
            vault.append({"module": label(key), "name": key[1], "workflows": users(key)})
        if REST_IN_CODE.search(code):
            rest_mods.append({"module": label(key), "name": key[1], "workflows": users(key)})
        if TRUST.search(code):
            trust.append(label(key))
        if HOST_VERIFICATION.search(code):
            hostver.append(label(key))
        if RETRY.search(code):
            retry.append(label(key))

    rest_wf, remote, scope = [], [], []
    for u, w in sorted(wf.items(), key=lambda kv: kv[1]["name"]):
        m = models.get(u) or {}
        srcs = _scripts(m)
        code = "\n".join(jscalls.mask(s) for s in srcs)
        if REST_IN_CODE.search(code):
            rest_wf.append(u)
        if HOST_VERIFICATION.search(code):
            hostver.append(w["name"])
        if TRUST.search(code):
            trust.append(w["name"])
        if RETRY.search(code):
            retry.append(w["name"])
        typed = [a for a in (m.get("attributes") or []) + (m.get("inputs") or [])
                 if str(a.get("type") or "").startswith("VCO:")]
        if typed or REMOTE.search(code):
            ids = sorted({x for a in typed for x in REMOTE_ID.findall(str(a.get("value") or ""))})
            remote.append({"workflow": u, "name": w["name"], "through": sorted({a["name"] for a in typed}),
                           "remote_ids": ids})
        folders = [p.lower() for p in re.split(r"[./]", str(w.get("category_path") or "")) if p]
        if SCOPE_NAME.search(w["name"]) or SCOPE_FOLDER & set(folders):
            scope.append({"workflow": u, "name": w["name"], "short": w["short"],
                          "why": "its folder" if SCOPE_FOLDER & set(folders) else "its name"})
    for a in acts:
        if any(str(t).startswith("VCO:") for t in (a.get("params") or {}).values()):
            remote.append({"workflow": None, "name": label((a["module"], a["name"])), "through": ["a VCO: parameter"],
                           "remote_ids": [], "workflows": users((a["module"], a["name"]))})

    # modules called at any depth that the export does not have, and how deep the chains go
    depth: dict[tuple, int] = {}

    def deep(key: tuple, seen: tuple = ()) -> int:
        if key in depth:
            return depth[key]
        if key in seen or key not in script_of:
            return 0
        kids = [(mo, nm) for mo, nm in jscalls.module_calls(script_of[key])]
        d = 1 + max((deep(k, (*seen, key)) for k in kids), default=0)
        depth[key] = d
        return d

    missing_mods: dict[tuple, set] = defaultdict(set)
    for key, src in script_of.items():
        deep(key)
        for mo, nm in jscalls.module_calls(src):
            if (mo, nm) not in script_of and (mo, nm) not in by_key:
                missing_mods[(mo, nm)].add(label(key))
    deepest = max(depth.values(), default=0)

    shared: list[dict] = []
    for a in sorted(acts, key=lambda a: (-len(reach[(a["module"], a["name"])]), a["key"])):
        n = len(reach[(a["module"], a["name"])])
        if n < max(5, len(wf) // 5) or len(shared) >= 5:
            continue
        shared.append({"module": label((a["module"], a["name"])), "name": a["name"], "workflows": users(
            (a["module"], a["name"])), "targets": sorted({str((assess.get("targets") or {}).get(t, {}).get("name") or t)
                                                         for t in a.get("targets") or []}),
            "ops": sorted({o.get("op") for o in a.get("all_ops") or a.get("ops") or [] if o.get("op")})[:6]})

    # what to add to the package, and how
    missing = []
    for x in assess.get("unexported_calls") or []:
        who = names_of.get(x["caller"], x["caller"])
        if x.get("mode") == "action":
            mo, _, nm = str(x["workflow_id"]).rpartition("/")
            missing_mods.pop((mo, nm), None)
            missing.append({"key": f"action:{x['workflow_id']}", "kind": "action", "what": f"action {x['workflow_id']}",
                            "called_by": [who], "how": _how_action(mo)})
        else:
            missing.append({"key": f"workflow:{x['workflow_id']}", "kind": "workflow",
                            "what": f"workflow {x.get('display_name') or x['workflow_id']} (ID {x['workflow_id']})",
                            "called_by": [who], "how": "Add this workflow to the package, with its dependencies, and "
                                                       "put the package in the export folder"})
    for (mo, nm), callers in sorted(missing_mods.items()):
        missing.append({"key": f"action:{mo}/{nm}", "kind": "action", "what": f"action {mo}/{nm}",
                        "called_by": sorted(callers), "how": _how_action(mo)})
    for r in remote:
        whom = [r["name"]] if r.get("workflow") else (
            [names_of.get(u, u) for u in r.get("workflows") or []] or [r["name"]])
        what = ("workflow " + ", ".join(r["remote_ids"]) if r["remote_ids"] else
                "the workflow" + (f" that {', '.join(r['through'])} points to" if r["through"] else " it calls")) \
            + " on the other vRO cluster, and the actions it calls"
        missing.append({"key": f"remote:{r['name']}", "kind": "remote workflow", "what": what, "called_by": whom,
                        "how": "On the vRO cluster that runs it, make a package of that workflow with its dependencies "
                               "(actions, configuration elements, nested workflows) and put it in this export folder. "
                               "Its calls are then read like any other"})
    for rc in runtime:
        for cat in rc["categories"]:
            if cat in rc["in_package"]:
                continue
            missing.append({"key": f"config:{cat}", "kind": "configuration elements",
                            "what": f"the configuration elements under {cat or 'the category'} {rc['name']} opens",
                            "called_by": [rc["module"]],
                            "how": f"Add the folder {cat} of configuration elements to the package (their text values "
                                   "are dropped when the export is sanitized; their names and types are what counts)"})
    missing.sort(key=lambda m: (m["kind"], m["key"]))

    return {"runtime_config": runtime, "vault": vault,
            "rest_in_code": {"workflows": [{"workflow": u, "name": names_of[u]} for u in rest_wf],
                             "modules": rest_mods},
            "remote": remote, "nesting": {"deepest": deepest, "missing": sorted(f"{m}/{n}" for m, n in missing_mods)},
            "scope": scope, "shared": shared, "missing": missing,
            "mechanics": {"trust_store": sorted(set(trust)), "host_verification": sorted(set(hostver)),
                          "retries": sorted(set(retry))},
            "counts": {"workflows": len(wf)}}


def _how_action(module: str) -> str:
    if module.startswith(("com.vmware.library.", "com.vmware.")):
        return (f"It comes from a VMware library module ({module}) installed with vRO or a plugin: add that module "
                "to the package, or name the plugin and its version in answers.yaml (Q8)")
    return f"Add the action module {module} to the package (in the vRO client, add it to the package's actions)"


def run() -> dict:
    r = find()
    C.write_json(os.path.join(C.ASSESS, "patterns.json"), r)
    return r


def load() -> dict:
    p = os.path.join(C.ASSESS, "patterns.json")
    return C.read_json(p) if os.path.exists(p) else {}
