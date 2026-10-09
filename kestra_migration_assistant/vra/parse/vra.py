"""
Reader for a vRA 8 / Aria Automation / VCF Automation content export, as VMware Build Tools
writes it: a `.vra-ng` ZIP, or the same layout unpacked in a folder (optionally under
`src/main/resources/`).

Layout, from the public samples (vmware-samples/build-tools-for-vmware-aria-samples, `vra/`
and `vcfa-all-apps/`) and the exports seen so far:

  content.yaml                          which items of each kind the package holds
  blueprints/<name>/content.yaml        a cloud template (inputs, resources)
  blueprints/<name>/details.json        its id, name, description
  blueprints/<name>/versions.json       released versions (optional)
  blueprints/<name>/<name>__FormData.json   a custom request form on the template (VCF 9)
  catalog-items/<source>__<name>.json   a catalog item: sourceName, name, formId
  catalog-items/forms/<source>__<name>.json            its form header; `form` may embed the form as a JSON string
  catalog-items/forms/<source>__<name>__FormData.json  or the form body sits here
  content-sources/<name>.json           typeId com.vmw.blueprint, com.vmw.vro.workflow, ...
  entitlements/<name>.yaml              catalog entitlement (older Build Tools)
  subscriptions/<name>.json             event broker subscription: eventTopicId, criteria, blocking, runnableId
  custom-resources/<name>/details.json  XaaS resource type; mainActions and additionalActions run vRO workflows
  resource-actions/<name>/details.json  day-2 action; runnableItem is a vRO workflow
  workflows/<name>.json                 a vRO workflow published to the catalog (spec.workflowId)
  property-groups/<name>.json           input or constant property groups
  policies/<kind>/<name>.json           content-sharing, approval, day2-actions, lease, iaas-resource ...
  scenarios/, flavor-mappings/, image-mappings/, storage-profiles/, region-mappings/

Everything in the file is untrusted. The ZIP goes through `safety.open_zip`, every entry is read
with `safety.read_member`, and JSON and YAML go through `safety.load_json` / `safety.load_yaml`.
A file that does not parse is a warning, never a crash. Folders and files this reader does not
know are listed in `unknown`, with their file counts, rather than dropped.
"""
from __future__ import annotations

import os
import posixpath
import re
from collections import defaultdict

from .. import outdir, safety

VRA_SUFFIXES = (".vra-ng",)

KNOWN_DIRS = ("blueprints", "catalog-items", "content-sources", "entitlements", "subscriptions",
              "custom-resources", "resource-actions", "workflows", "property-groups", "policies",
              "scenarios", "flavor-mappings", "image-mappings", "storage-profiles", "region-mappings")
# Files Build Tools puts next to the content in a source tree. Not content; not reported as unknown.
PROJECT_FILES = ("pom.xml", "README.md", "LICENSE", "release.sh", "package.json")
PROJECT_DIRS = ("license_data", "target", "META-INF")

# content.yaml key -> folder
CONTENT_KEYS = {"blueprint": "blueprints", "subscription": "subscriptions", "catalog-item": "catalog-items",
                "content-source": "content-sources", "catalog-entitlement": "entitlements",
                "custom-resource": "custom-resources", "resource-action": "resource-actions",
                "property-group": "property-groups", "workflow": "workflows", "policy": "policies",
                "scenario": "scenarios", "flavor-mapping": "flavor-mappings", "image-mapping": "image-mappings",
                "storage-profile": "storage-profiles", "region-mapping": "region-mappings"}

MAX_FILES = 5_000


def is_vra_tree(path: str) -> bool:
    """A folder holding a vRA export: content.yaml at its root or under src/main/resources/."""
    if not os.path.isdir(path):
        return False
    for base in ("", os.path.join("src", "main", "resources")):
        d = os.path.join(path, base)
        if os.path.isfile(os.path.join(path, "content.yaml")) and \
                any(os.path.isdir(os.path.join(d, k)) for k in KNOWN_DIRS):
            return True
    return False


def is_vra_input(path: str) -> bool:
    return path.lower().endswith(VRA_SUFFIXES) or is_vra_tree(path)


# ------------------------------------------------------------------ file access

def _files(path: str) -> tuple[dict[str, bytes], list[str]]:
    """Every file in the export, posix relative path -> bytes, and warnings."""
    out, warnings = {}, []
    if os.path.isdir(path):
        for dp, dirs, files in os.walk(path):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not os.path.islink(os.path.join(dp, d))
                             and not outdir.skipped(dp, d))
            for fn in sorted(files):
                if fn.startswith("."):
                    continue
                p = os.path.join(dp, fn)
                rel = os.path.relpath(p, path).replace(os.sep, "/")
                try:
                    out[rel] = safety.read_file(p)
                except safety.UnsafeInput as e:
                    warnings.append(f"{rel}: not read: {e}")
                if len(out) > MAX_FILES:
                    raise safety.UnsafeInput(f"{path}: more than {MAX_FILES} files")
        return out, warnings
    with safety.open_zip(path) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            out[info.filename.replace("\\", "/")] = safety.read_member(z, info.filename)
    return out, warnings


def _strip_root(files: dict[str, bytes]) -> dict[str, bytes]:
    """Drop a `src/main/resources/` prefix (a Build Tools source tree), keeping content.yaml."""
    pre = "src/main/resources/"
    if any(k.startswith(pre) for k in files):
        return {(k[len(pre):] if k.startswith(pre) else k): v for k, v in files.items()}
    return files


# ------------------------------------------------------------------ helpers

def _s(v) -> str:
    return "" if v is None else str(v)


def _stem(name: str) -> str:
    return re.sub(r"\.(json|ya?ml)$", "", posixpath.basename(name), flags=re.I)


def _bool(v) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.strip().lower() in ("true", "false"):
        return v.strip().lower() == "true"
    return None


class _Doc:
    """Parses one member at a time, collecting warnings instead of raising."""

    def __init__(self, files: dict[str, bytes]):
        self.files = files
        self.warnings: list[str] = []
        self.used: set[str] = set()

    def get(self, name: str, kind: str = "auto"):
        if name not in self.files:
            return None
        self.used.add(name)
        raw = self.files[name]
        kind = kind if kind != "auto" else ("yaml" if name.lower().endswith((".yaml", ".yml")) else "json")
        try:
            return safety.load_yaml(raw, name) if kind == "yaml" else safety.load_json(raw, name)
        except safety.UnsafeInput as e:
            self.warnings.append(f"{name}: refused: {e}")
        except Exception as e:
            self.warnings.append(f"{name}: does not parse as {kind.upper()}: {type(e).__name__}: {str(e)[:160]}")
        return None

    def mapping(self, name: str, kind: str = "auto") -> dict | None:
        v = self.get(name, kind)
        if v is not None and not isinstance(v, dict):
            self.warnings.append(f"{name}: expected a mapping, found {type(v).__name__}")
            return None
        return v


# ------------------------------------------------------------------ one kind at a time

def _form_fields(form: dict | None) -> list[dict]:
    """Fields in layout order from a request form ({layout, schema}), with what each one asks for."""
    if not isinstance(form, dict):
        return []
    schema = form.get("schema") if isinstance(form.get("schema"), dict) else {}
    order, display, page_of = [], {}, {}
    layout = form.get("layout") if isinstance(form.get("layout"), dict) else {}
    for page in layout.get("pages") or []:
        if not isinstance(page, dict):
            continue
        for sec in page.get("sections") or []:
            for f in (sec.get("fields") or []) if isinstance(sec, dict) else []:
                if isinstance(f, dict) and f.get("id") and f["id"] not in order:
                    order.append(_s(f["id"]))
                    display[f["id"]] = _s(f.get("display"))
                    page_of[f["id"]] = _s(page.get("title") or page.get("id"))
    order += [k for k in schema if k not in order]
    out = []
    for fid in order:
        sc = schema.get(fid) if isinstance(schema.get(fid), dict) else {}
        typ = sc.get("type") if isinstance(sc.get("type"), dict) else {}
        cons = sc.get("constraints") if isinstance(sc.get("constraints"), dict) else {}
        vl = sc.get("valueList")
        values = None
        if isinstance(vl, list):
            values = [_s(x.get("value")) if isinstance(x, dict) else _s(x) for x in vl]
        elif isinstance(vl, dict):
            values = f"{_s(vl.get('type'))}: {_s(vl.get('id'))}"
        default = sc.get("default")
        out.append({"id": fid, "label": _s(sc.get("label")), "type": _s(typ.get("dataType")),
                    "multiple": bool(typ.get("isMultiple")), "required": bool(cons.get("required")),
                    "default": default if isinstance(default, (str, int, float, bool)) or default is None
                    else "(computed)",
                    "values": values, "display": display.get(fid, ""), "page": page_of.get(fid, ""),
                    "in_layout": fid in display})
    return out


# Machine properties vRA's own schema defines (Cloud.Machine, Cloud.vSphere.Machine, and the common
# cloud-agnostic ones). Anything else on a machine reaches subscriptions as a custom property.
MACHINE_SCHEMA = {
    "count", "image", "imageRef", "flavor", "flavorRef", "name", "networks", "constraints", "storage", "tags",
    "attachedDisks", "cpuCount", "totalMemoryMB", "coreCount", "cloudConfig", "cloudConfigSettings",
    "remoteAccess", "folderName", "resourceGroupName", "resourcePool", "customizationSpec", "description",
    "snapshotLimit", "imageDiskConstraints", "storagePolicy", "powerState", "ovfProperties", "machineName",
    "customizeGuestOs", "newName", "cloneStrategy", "provisioningType", "hostName", "zone", "region",
    "customProperties", "sshKey", "username", "password", "dnsServers", "timeZone", "vmToolsVersion",
}


def _plan_values(props: dict) -> dict:
    """The few property values the replacement plan needs, kept as values (everything else is kept by name):
    a network's `networkType`, each machine network's IP `assignment`, and the placement constraint tags.
    An input reference (`${input.x}`) is kept as written."""
    out = {}
    if isinstance(props.get("networkType"), str):
        out["network_type"] = _s(props["networkType"])
    nets = props.get("networks") if isinstance(props.get("networks"), list) else []
    assign = sorted({_s(n.get("assignment")) for n in nets if isinstance(n, dict) and isinstance(n.get("assignment"), str)})
    if assign:
        out["ip_assignment"] = assign
    cons = props.get("constraints") if isinstance(props.get("constraints"), list) else []
    tags = [_s(c.get("tag")) for c in cons if isinstance(c, dict) and isinstance(c.get("tag"), str)]
    if tags:
        out["constraint_tags"] = tags
    return out


def _blueprint(doc: _Doc, name: str, files: list[str]) -> dict:
    base = f"blueprints/{name}/"
    det = doc.mapping(base + "details.json") or {}
    body = doc.mapping(base + "content.yaml", "yaml") or {}
    inputs = []
    raw_in = body.get("inputs") if isinstance(body.get("inputs"), dict) else {}
    for k, v in raw_in.items():
        v = v if isinstance(v, dict) else {}
        values = v.get("enum")
        if values is None and isinstance(v.get("oneOf"), list):
            values = [x.get("const") for x in v["oneOf"] if isinstance(x, dict)]
        inputs.append({"name": _s(k), "type": _s(v.get("type")), "title": _s(v.get("title")),
                       "default": v.get("default") if isinstance(v.get("default"), (str, int, float, bool)) else None,
                       "values": [_s(x) for x in values] if isinstance(values, list) else None})
    resources = []
    raw_res = body.get("resources") if isinstance(body.get("resources"), dict) else {}
    for k, v in raw_res.items():
        v = v if isinstance(v, dict) else {}
        props = v.get("properties") if isinstance(v.get("properties"), dict) else {}
        text = repr(props)
        cp = props.get("customProperties") if isinstance(props.get("customProperties"), dict) else {}
        if not cp and v.get("type", "").endswith(".Machine"):
            # vRA passes a machine property its schema does not know as a custom property
            cp = {p: 1 for p in props if _s(p) not in MACHINE_SCHEMA}
        count = props.get("count")
        resources.append({"name": _s(k), "type": _s(v.get("type")),
                          "properties": sorted(_s(p) for p in props),
                          **_plan_values(props),
                          "count": _s(count) if isinstance(count, (str, int)) and not isinstance(count, bool) else None,
                          "custom_properties": sorted(_s(p) for p in cp),
                          "uses_inputs": sorted(set(re.findall(r"input\.(\w+)", text))),
                          "depends_on": sorted(set(re.findall(r"resource\.(\w+)", text)))})
    versions = doc.get(base + "versions.json") if base + "versions.json" in doc.files else None
    form_files = [f for f in files if f.endswith("__FormData.json")]
    form = doc.mapping(form_files[0]) if form_files else None
    for f in files:
        if f.endswith("styles.css"):
            doc.used.add(f)
    return {"name": _s(det.get("name")) or name, "folder": name, "id": _s(det.get("id")),
            "description": _s(det.get("description")), "format_version": body.get("formatVersion"),
            "inputs": inputs, "resources": resources,
            "versions": len(versions) if isinstance(versions, list) else None,
            "form_fields": _form_fields(form), "file": base + "content.yaml"}


def _catalog_item(doc: _Doc, fname: str) -> dict:
    stem = _stem(fname)
    d = doc.mapping(fname) or {}
    src, _, nm = stem.partition("__")
    form_hdr = doc.mapping(f"catalog-items/forms/{stem}.json")
    form = None
    if isinstance(form_hdr, dict) and isinstance(form_hdr.get("form"), str) and form_hdr["form"].strip():
        try:
            form = safety.load_json(form_hdr["form"], f"catalog-items/forms/{stem}.json: form")
        except Exception as e:
            doc.warnings.append(f"catalog-items/forms/{stem}.json: embedded form does not parse: {str(e)[:160]}")
    if form is None:
        form = doc.mapping(f"catalog-items/forms/{stem}__FormData.json")
    return {"name": _s(d.get("name")) or nm or stem, "content_source": _s(d.get("sourceName")) or src,
            "id": _s(d.get("id")), "description": _s(d.get("description")), "form_id": _s(d.get("formId")),
            "form": {"present": form_hdr is not None or form is not None,
                     "type": _s((form_hdr or {}).get("type")), "status": _s((form_hdr or {}).get("status")),
                     "source_type": _s((form_hdr or {}).get("sourceType"))},
            "form_fields": _form_fields(form), "file": fname}


def _subscription(doc: _Doc, fname: str) -> dict:
    d = doc.mapping(fname) or {}
    cons = d.get("constraints") if isinstance(d.get("constraints"), dict) else {}
    projects = cons.get("projectNames") or ([cons["projectId"]] if cons.get("projectId") else [])
    return {"name": _s(d.get("name")) or _stem(fname), "id": _s(d.get("id")),
            "description": _s(d.get("description")), "type": _s(d.get("type")),
            "event_topic": _s(d.get("eventTopicId")), "condition": _s(d.get("criteria")),
            "blocking": _bool(d.get("blocking")), "disabled": _bool(d.get("disabled")),
            "priority": d.get("priority") if isinstance(d.get("priority"), int) else None,
            "timeout_minutes": d.get("timeout") if isinstance(d.get("timeout"), int) else None,
            "runnable_type": _s(d.get("runnableType")), "runnable_id": _s(d.get("runnableId")),
            "contextual": _bool(d.get("contextual")), "broadcast": _bool(d.get("broadcast")),
            "projects": [_s(p) for p in projects] if isinstance(projects, list) else [], "file": fname}


def _runnable(r) -> dict | None:
    if not isinstance(r, dict):
        return None
    return {"id": _s(r.get("id")), "name": _s(r.get("name")), "type": _s(r.get("type")),
            "inputs": [_s(p.get("name")) for p in r.get("inputParameters") or [] if isinstance(p, dict)]}


def _custom_resource(doc: _Doc, name: str) -> dict:
    d = doc.mapping(f"custom-resources/{name}/details.json") or {}
    actions = []
    main = d.get("mainActions") if isinstance(d.get("mainActions"), dict) else {}
    for k, v in main.items():
        r = _runnable(v)
        if r:
            actions.append({"action": _s(k), **r})
    for v in d.get("additionalActions") or []:
        if isinstance(v, dict):
            r = _runnable(v.get("runnableItem") or v)
            if r:
                actions.append({"action": _s(v.get("displayName") or v.get("name")), **r})
    return {"name": _s(d.get("displayName")) or name, "resource_type": _s(d.get("resourceType")),
            "external_type": _s(d.get("externalType")), "actions": actions,
            "file": f"custom-resources/{name}/details.json"}


def _resource_action(doc: _Doc, name: str) -> dict:
    d = doc.mapping(f"resource-actions/{name}/details.json") or {}
    return {"name": _s(d.get("displayName") or d.get("name")) or name, "resource_type": _s(d.get("resourceType")),
            "provider": _s(d.get("providerName")), "runnable": _runnable(d.get("runnableItem")),
            "file": f"resource-actions/{name}/details.json"}


# ------------------------------------------------------------------ the export

def read_vra(path: str) -> dict:
    """Read one vRA export. Raises safety.UnsafeInput or zipfile.BadZipFile only for the container."""
    files, warnings = _files(path)
    files = _strip_root(files)
    doc = _Doc(files)
    doc.warnings += warnings
    by_dir = defaultdict(list)
    for n in sorted(files):
        by_dir[n.split("/", 1)[0] if "/" in n else ""].append(n)

    content = doc.mapping("content.yaml", "yaml")
    if "content.yaml" not in files:
        doc.warnings.append("no content.yaml: the export does not say what it was meant to hold")

    blueprints = []
    bp_files = defaultdict(list)
    for n in by_dir.get("blueprints", []):
        parts = n.split("/")
        if len(parts) >= 3:
            bp_files[parts[1]].append(n)
    for name in sorted(bp_files):
        blueprints.append(_blueprint(doc, name, bp_files[name]))

    catalog = [_catalog_item(doc, n) for n in by_dir.get("catalog-items", [])
               if n.count("/") == 1 and n.lower().endswith(".json")]
    sources = []
    for n in by_dir.get("content-sources", []):
        d = doc.mapping(n) or {}
        sources.append({"name": _s(d.get("name")) or _stem(n), "type_id": _s(d.get("typeId")),
                        "description": _s(d.get("description")), "id": _s(d.get("id")), "file": n})
    entitlements = []
    for n in by_dir.get("entitlements", []):
        d = doc.mapping(n) or {}
        entitlements.append({"name": _s(d.get("name")) or _stem(n), "type": _s(d.get("type")),
                             "source_type": _s(d.get("sourceType")),
                             "project": _s(d.get("projectName") or d.get("projectId")), "file": n})
    subs = [_subscription(doc, n) for n in by_dir.get("subscriptions", []) if n.lower().endswith(".json")]
    cres = sorted({n.split("/")[1] for n in by_dir.get("custom-resources", []) if n.count("/") >= 2})
    custom = [_custom_resource(doc, n) for n in cres]
    ras = sorted({n.split("/")[1] for n in by_dir.get("resource-actions", []) if n.count("/") >= 2})
    actions = [_resource_action(doc, n) for n in ras]
    for n in by_dir.get("resource-actions", []) + by_dir.get("custom-resources", []):
        if not n.endswith("details.json"):
            doc.used.add(n)                       # forms and styles of a day-2 action: counted, not modelled
    wfs = []
    for n in by_dir.get("workflows", []):
        d = doc.mapping(n) or {}
        spec = d.get("spec") if isinstance(d.get("spec"), dict) else {}
        wfs.append({"name": _s(d.get("name")) or _stem(n), "type_id": _s(d.get("typeId")),
                    "workflow_id": _s(spec.get("workflowId")), "description": _s(d.get("description")),
                    "file": n})
    pgroups = []
    for n in by_dir.get("property-groups", []):
        d = doc.mapping(n) or {}
        props = d.get("properties") if isinstance(d.get("properties"), dict) else {}
        pgroups.append({"name": _s(d.get("name")) or _stem(n), "type": _s(d.get("type")),
                        "properties": sorted(_s(k) for k in props), "file": n})
    policies = []
    for n in by_dir.get("policies", []):
        parts = n.split("/")
        d = doc.mapping(n) or {}
        policies.append({"kind": parts[1] if len(parts) > 2 else "", "name": _s(d.get("name")) or _stem(n),
                         "type_id": _s(d.get("typeId")), "enforcement": _s(d.get("enforcementType")), "file": n})
    other_kinds = {}
    for k in ("scenarios", "flavor-mappings", "image-mappings", "storage-profiles", "region-mappings"):
        if by_dir.get(k):
            for n in by_dir[k]:
                doc.get(n) if n.lower().endswith((".json", ".yaml", ".yml")) else doc.used.add(n)
            other_kinds[k] = len(by_dir[k])

    unknown = []
    for d, names in sorted(by_dir.items()):
        if d == "":
            for n in names:
                if n != "content.yaml" and n not in PROJECT_FILES:
                    unknown.append({"path": n, "files": 1})
        elif d not in KNOWN_DIRS and d not in PROJECT_DIRS:
            unknown.append({"path": d + "/", "files": len(names)})
    unread = sorted(n for n in files if n not in doc.used and n != "content.yaml"
                    and n.split("/", 1)[0] in KNOWN_DIRS)

    listed = _listed(content)
    present = {"blueprint": [b["folder"] for b in blueprints], "subscription": [_stem(s["file"]) for s in subs],
               "catalog-item": [_stem(c["file"]) for c in catalog],
               "content-source": [_stem(s["file"]) for s in sources],
               "catalog-entitlement": [_stem(e["file"]) for e in entitlements],
               "custom-resource": cres, "resource-action": ras, "workflow": [_stem(w["file"]) for w in wfs],
               "property-group": [_stem(p["file"]) for p in pgroups],
               "policy": [f"{p['kind']}/{_stem(p['file'])}" for p in policies]}
    missing, unlisted = [], []
    for k, names in listed.items():
        if names is None or k not in present:
            continue
        for n in names:
            if n not in present[k]:
                missing.append(f"{k}: {n}")
    for k, names in present.items():
        if k in listed and listed[k] is not None:
            unlisted += [f"{k}: {n}" for n in names if n not in listed[k]]
    for m in missing:
        doc.warnings.append(f"content.yaml lists {m}, which is not in the export")

    return {"path": path, "format": "vra-tree" if os.path.isdir(path) else "vra-ng", "files": len(files),
            "content_yaml": {"present": content is not None, "listed": listed,
                             "region_mapping": bool(isinstance(content, dict) and content.get("region-mapping"))},
            "blueprints": blueprints, "catalog_items": catalog, "content_sources": sources,
            "entitlements": entitlements, "subscriptions": subs, "custom_resources": custom,
            "resource_actions": actions, "catalog_workflows": wfs, "property_groups": pgroups,
            "policies": policies, "other_kinds": other_kinds,
            "unknown": unknown, "unread": unread, "listed_but_missing": missing,
            "present_but_unlisted": unlisted, "warnings": doc.warnings}


def _listed(content) -> dict:
    """content.yaml as kind -> list of names (None: the key says 'all existing', or is absent)."""
    out = {}
    if not isinstance(content, dict):
        return out
    for k, v in content.items():
        if k == "policy" and isinstance(v, dict):
            out["policy"] = [f"{sub}/{n}" for sub, ns in v.items() for n in (ns or []) if isinstance(ns, list)]
        elif isinstance(v, list):
            out[_s(k)] = [_s(x) for x in v]
        elif v is None:
            out[_s(k)] = None
    return out


def counts(r: dict) -> dict:
    """Items per kind, for a report line."""
    return {"cloud templates": len(r["blueprints"]), "catalog items": len(r["catalog_items"]),
            "content sources": len(r["content_sources"]), "entitlements": len(r["entitlements"]),
            "subscriptions": len(r["subscriptions"]), "custom resources": len(r["custom_resources"]),
            "resource actions": len(r["resource_actions"]), "catalog workflows": len(r["catalog_workflows"]),
            "property groups": len(r["property_groups"]), "policies": len(r["policies"]),
            **r["other_kinds"]}
