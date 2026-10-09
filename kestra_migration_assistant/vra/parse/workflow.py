"""
Turn a vRO workflow XML into the normalized model the rest of the pipeline uses.

The model is deliberately complete. Anything the model does not record is
something a later phase cannot convert, so the parser records everything it can
see and flags what it cannot, rather than dropping it.
"""
from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from typing import Any

from .. import safety
from .jscalls import module_calls

NS = {"w": "http://vmware.com/vco/workflow"}

# vRO globals and plugin singletons that appear in scriptable tasks. Used to
# report SDK surface, and later to decide whether a script can survive a rewrite.
SDK_RE = re.compile(
    r"\b(System|Server|VcPlugin|Workflow|Task|ActionResult|LockingSystem|Command|"
    r"EmailMessage|VROES|RESTHostManager|SOAPHostManager|ConfigurationElement|"
    r"ResourceElement|DynamicWrapper|VcSdkConnection|SSHSession|Properties|"
    r"ldap|ActiveDirectory|AD_ObjectFactory|SQLBrowser|HTTPRestHost|Config|"
    r"WorkflowToken|SecureStringUtil|Base64|XPath|Date)\b\s*\.\s*(\w+)"
)

# A type prefixed `Foo:` is a plugin object type, except a few built-in generics.
PLUGIN_TYPE_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_]*):(.+)$")
NOT_PLUGIN_PREFIXES = {"Array", "CompositeType"}

# vRO's null sentinel. Not a string, and must never be carried over as one.
NULL_SENTINEL = "__NULL__"


def _txt(el) -> str:
    if el is None:
        return ""
    return "".join(el.itertext()).strip()


def _find(parent, tag):
    r = parent.find(f"w:{tag}", NS)
    return r if r is not None else parent.find(tag)


def _findall(parent, tag):
    r = parent.findall(f"w:{tag}", NS)
    return r if r else parent.findall(tag)


def _script_of(item):
    s = _find(item, "script")
    if s is None:
        return None
    return s.text or ""


def _norm_script(src: str) -> str:
    """
    Normalize a script for near-duplicate detection: strip comments, collapse
    whitespace, and replace identifiers and literals with placeholders so that
    two scripts differing only in variable and endpoint names hash the same.
    """
    s = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    s = re.sub(r"//[^\n]*", " ", s)
    s = re.sub(r'"(?:[^"\\]|\\.)*"', '"S"', s)
    s = re.sub(r"'(?:[^'\\]|\\.)*'", "'S'", s)
    s = re.sub(r"\b\d+(?:\.\d+)?\b", "N", s)
    kw = {
        "var", "function", "return", "if", "else", "for", "while", "do", "switch",
        "case", "break", "continue", "throw", "try", "catch", "finally", "new",
        "typeof", "instanceof", "in", "of", "null", "true", "false", "undefined",
        "this", "delete", "void",
    }
    s = re.sub(r"\b[A-Za-z_$][A-Za-z0-9_$]*\b",
               lambda m: m.group(0) if m.group(0) in kw else "ID", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _shingles(s: str, k: int = 5) -> list[str]:
    toks = s.split()
    if len(toks) < k:
        return [" ".join(toks)] if toks else []
    return [" ".join(toks[i:i + k]) for i in range(len(toks) - k + 1)]


def _param_list(container):
    out = []
    if container is None:
        return out
    for p in _findall(container, "param"):
        d = {
            "name": p.attrib.get("name", ""),
            "type": p.attrib.get("type", ""),
        }
        desc = _find(p, "description")
        if desc is not None:
            d["description"] = _txt(desc)
        out.append(d)
    return out


def _bindings(item, which):
    out = []
    b = _find(item, f"{which}-binding")
    if b is None:
        return out
    for bind in _findall(b, "bind"):
        out.append({
            "name": bind.attrib.get("name", ""),
            "type": bind.attrib.get("type", ""),
            "export_name": bind.attrib.get("export-name", ""),
            "explanation": bind.attrib.get("explanation", ""),
        })
    return out


def _plugin_type(t: str):
    m = PLUGIN_TYPE_RE.match(t or "")
    if not m:
        return None
    if m.group(1) in NOT_PLUGIN_PREFIXES:
        # Array/VC:VirtualMachine still carries a plugin type inside it.
        inner = m.group(2) if m.group(1) == "CompositeType" else t.split("/", 1)[-1]
        mi = PLUGIN_TYPE_RE.match(inner)
        return inner if mi and mi.group(1) not in NOT_PLUGIN_PREFIXES else None
    return t


def parse_workflow(element) -> dict:
    """element is a formats.Element of kind Workflow."""
    warnings: list[str] = list(element.warnings)
    try:
        root = safety.parse_xml(element.xml)
        for sc in root.iter():
            if sc.tag.split("}")[-1] == "script":
                safety.check_script(sc.text, element.name or element.source_path)
    except (ET.ParseError, safety.UnsafeInput) as exc:
        return {
            "id": "", "name": element.name, "parse_error": str(exc),
            "source_path": element.source_path, "warnings": warnings + [f"parse error: {exc}"],
        }

    tag = root.tag.split("}")[-1]
    if tag != "workflow":
        return {
            "id": "", "name": element.name,
            "parse_error": f"root element is <{tag}>, not <workflow>",
            "source_path": element.source_path,
            "warnings": warnings + [f"root element is <{tag}>"],
        }

    wf_id = root.attrib.get("id", "") or element.uuid
    root_name = root.attrib.get("root-name", "")

    # ---- identity -------------------------------------------------------
    display = _txt(_find(root, "display-name")) or element.name
    description = _txt(_find(root, "description"))

    # ---- signature ------------------------------------------------------
    inputs = _param_list(_find(root, "input"))
    outputs = _param_list(_find(root, "output"))
    attributes = []
    for a in _findall(root, "attrib"):
        v = _find(a, "value")
        raw_val = _txt(v) if v is not None else None
        attributes.append({
            "name": a.attrib.get("name", ""),
            "type": a.attrib.get("type", ""),
            "read_only": a.attrib.get("read-only") == "true",
            "value": None if raw_val == NULL_SENTINEL else raw_val,
            "is_null_sentinel": raw_val == NULL_SENTINEL,
            "description": _txt(_find(a, "description")) or None,
        })

    # ---- items and edges ------------------------------------------------
    items = []
    edges = []
    scripts = []
    sdk_calls: set[str] = set()
    nested_calls = []
    action_calls = []
    config_refs = []

    for it in _findall(root, "workflow-item"):
        name = it.attrib.get("name", "")
        itype = it.attrib.get("type", "?")
        rec = {
            "name": name,
            "type": itype,
            "display_name": _txt(_find(it, "display-name")) or None,
            "attributes": dict(it.attrib),
            "in_bindings": _bindings(it, "in"),
            "out_bindings": _bindings(it, "out"),
            "script": None,
            "script_lines": 0,
            "conditions": [],
            "references": [],
        }

        src = _script_of(it)
        if src is not None and src.strip():
            lines = [l for l in src.splitlines() if l.strip() and not l.strip().startswith("//")]
            norm = _norm_script(src)
            rec["script"] = src
            rec["script_lines"] = len(lines)
            rec["script_norm_hash"] = hashlib.sha256(norm.encode()).hexdigest()[:16]
            scripts.append({
                "item": name, "lines": len(lines), "source": src,
                "norm": norm, "shingles": _shingles(norm),
            })
            for m in SDK_RE.finditer(src):
                sdk_calls.add(f"{m.group(1)}.{m.group(2)}")
            for m in re.finditer(
                r'getConfigurationElementCategoryWithPath\(\s*["\']([^"\']+)["\']', src):
                config_refs.append({"item": name, "path": m.group(1)})
            for mod, act in module_calls(src):
                action_calls.append({"item": name, "module": mod, "action": act})

        # switch and condition arms
        for c in _findall(it, "condition"):
            rec["conditions"].append({
                "name": c.attrib.get("name", ""),
                "type": c.attrib.get("type", ""),
                "comparator": c.attrib.get("comparator", ""),
                "label": c.attrib.get("label", ""),
                "value": _txt(c),
                "is_null_check": _txt(c) == NULL_SENTINEL,
            })
        for r in _findall(it, "reference"):
            rec["references"].append({
                "type": r.attrib.get("type", ""), "id": r.attrib.get("id", "")})

        # nested and async workflow calls
        lw = it.attrib.get("linked-workflow-id")
        aw = it.attrib.get("launched-workflow-id")
        sm = it.attrib.get("script-module")
        if lw:
            nested_calls.append({"item": name, "workflow_id": lw, "mode": "sync",
                                 "display_name": rec["display_name"]})
        if aw:
            nested_calls.append({"item": name, "workflow_id": aw, "mode": "async",
                                 "display_name": rec["display_name"]})
        if sm:
            action_calls.append({"item": name, "module": sm, "action": None})
        for r in rec["references"]:
            if r["type"] == "Workflow" and r["id"]:
                nested_calls.append({"item": name, "workflow_id": r["id"],
                                     "mode": "foreach", "display_name": rec["display_name"]})

        items.append(rec)

        # edges
        for attr, kind in (("out-name", "next"), ("alt-out-name", "alt"),
                           ("catch-name", "error")):
            tgt = it.attrib.get(attr)
            if tgt:
                edges.append({"from": name, "to": tgt, "kind": kind, "label": None})
        for c in rec["conditions"]:
            if c["label"]:
                edges.append({"from": name, "to": c["label"], "kind": "case",
                              "label": c["value"] or "(default)"})

    # ---- default error handler -----------------------------------------
    # <error-handler name="itemN" throw-bind-name="x"> sits beside the items, not
    # inside one. Any error that no item's catch-name catches goes to itemN, with
    # the error message bound to x. It is the workflow-wide catch, so it is kept
    # apart from `edges` (it has no source item) and from `error_handler_items`
    # (which lists items carrying a catch-name).
    default_error_handlers = []
    for eh in _findall(root, "error-handler"):
        default_error_handlers.append({
            "target": eh.attrib.get("name", ""),
            "throw_bind_name": eh.attrib.get("throw-bind-name") or None,
        })

    item_names = {i["name"] for i in items}
    for h in default_error_handlers:
        if h["target"] not in item_names:
            warnings.append(f"error-handler -> {h['target']} has no such item")
    if len(default_error_handlers) > 1:
        warnings.append(f"{len(default_error_handlers)} error-handler elements; vRO allows one")
    for e in edges:
        if e["to"] not in item_names:
            warnings.append(f"edge {e['from']} -> {e['to']} ({e['kind']}) has no such item")
    if root_name and root_name not in item_names:
        warnings.append(f"root-name '{root_name}' is not an item in this workflow")

    # ---- presentation ---------------------------------------------------
    pres = _find(root, "presentation")
    presentation: dict[str, Any] = {"params": [], "ognl": [], "raw_present": pres is not None}
    if pres is not None:
        for p in _findall(pres, "p-param"):
            pd = {"name": p.attrib.get("name", ""),
                  "desc": _txt(_find(p, "desc")) or None, "quals": []}
            for q in _findall(p, "p-qual"):
                qd = {"kind": q.attrib.get("kind", ""),
                      "name": q.attrib.get("name", ""),
                      "value": _txt(q)}
                pd["quals"].append(qd)
                if qd["kind"] == "ognl":
                    presentation["ognl"].append(
                        {"param": pd["name"], "qual": qd["name"], "expr": qd["value"]})
            presentation["params"].append(pd)

    # ---- plugin object types across the whole document ------------------
    plugin_types: set[str] = set()
    for t in [p["type"] for p in inputs] + [p["type"] for p in outputs] + \
             [a["type"] for a in attributes]:
        pt = _plugin_type(t)
        if pt:
            plugin_types.add(pt)
    for it in items:
        for b in it["in_bindings"] + it["out_bindings"]:
            pt = _plugin_type(b.get("type", ""))
            if pt:
                plugin_types.add(pt)

    # ---- error handling -------------------------------------------------
    error_handlers = [i["name"] for i in items
                      if i["attributes"].get("catch-name")]
    throw_ends = [i["name"] for i in items
                  if i["type"] == "end" and i["attributes"].get("end-mode") not in (None, "0")]

    return {
        "id": wf_id,
        "name": display,
        "description": description,
        "category_path": element.category_path,
        "source_path": element.source_path,
        "container_format": element.container_format,
        "root_item": root_name,
        "restart_mode": root.attrib.get("restartMode"),
        "api_version": root.attrib.get("api-version"),
        "version": root.attrib.get("version"),
        "inputs": inputs,
        "outputs": outputs,
        "attributes": attributes,
        "items": items,
        "edges": edges,
        "item_type_counts": _counts(i["type"] for i in items),
        "scripts": [{k: v for k, v in s.items() if k != "shingles"} for s in scripts],
        "script_shingles": {s["item"]: s["shingles"] for s in scripts},
        "total_script_lines": sum(s["lines"] for s in scripts),
        "sdk_calls": sorted(sdk_calls),
        "plugin_types": sorted(plugin_types),
        "nested_calls": nested_calls,
        "action_calls": action_calls,
        "config_refs": config_refs,
        "presentation": presentation,
        "has_ognl": len(presentation["ognl"]) > 0,
        "error_handler_items": error_handlers,
        "default_error_handler": default_error_handlers[0] if default_error_handlers else None,
        "throw_end_items": throw_ends,
        "input_form_present": element.input_form is not None,
        "warnings": warnings,
    }


def _counts(seq):
    out = {}
    for x in seq:
        out[x] = out.get(x, 0) + 1
    return out
