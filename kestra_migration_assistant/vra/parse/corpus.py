"""
Drive the parsers over the whole corpus and write one model file per workflow.

Stable IDs
----------
An ID is <source>/<name>, and where two workflows in one source share a display
name it is <source>/<name>#<last category segment>. The baseline evaluation's
results.csv keys on <source>/<name> alone, so the model also carries
`baseline_key`, which is what the head-to-head joins on.
"""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict

from . import formats, graph, workflow

RESOURCE_TEXT_MAX = 16_384       # a text resource element's payload kept for spec prompts, in characters


def safe_id(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-")


def _relative(path: str, root: str | None) -> str:
    """`path` relative to `root` with forward slashes, a container's inner part after '!' kept as is. As given
    when there is no root, or on another drive."""
    if not root or not path:
        return path
    outer, sep, inner = path.partition("!")
    try:
        outer = os.path.relpath(outer, root).replace(os.path.sep, "/")
    except ValueError:                      # Windows: another drive than the export
        return path
    return outer + sep + inner


def parse_corpus(out_dir: str, sources: dict[str, str], root: str | None = None) -> dict:
    """Parse every input in `sources` (label -> path). Labels prefix the stable IDs. Every path written is
    relative to `root` (the export folder), so the parse cache names no folder of this machine."""
    os.makedirs(os.path.join(out_dir, "model"), exist_ok=True)

    workflows: list[dict] = []
    other_elements: list[dict] = []
    package_summaries = {}
    failures = []

    for label, path in sources.items():
        try:
            elements, summary = formats.read_any(path)
        except Exception as exc:
            failures.append({"source": label, "path": _relative(path, root), "error": str(exc)})
            continue
        if "source_path" in summary:
            summary = {**summary, "source_path": _relative(summary["source_path"], root)}
        package_summaries[label] = summary

        # workflows
        wf_els = [e for e in elements if e.kind == "Workflow"]
        by_name = defaultdict(list)
        for e in wf_els:
            by_name[e.name].append(e)

        for e in wf_els:
            m = workflow.parse_workflow(e)
            m["source_path"] = _relative(m.get("source_path") or e.source_path, root)
            if m.get("parse_error"):
                failures.append({"source": label, "path": m["source_path"],
                                 "name": e.name, "error": m["parse_error"]})
                m["source"] = label
                workflows.append(m)
                continue

            base_key = f"{label}/{e.file_stem or e.name}"
            if len(by_name[e.name]) > 1:
                seg = (e.category_path or "").split(".")[-1] or "root"
                wid = f"{label}/{e.name}#{seg}"
            else:
                wid = base_key

            m["wid"] = wid
            m["uid"] = safe_id(wid)
            m["source"] = label
            m["file_stem"] = e.file_stem or e.name
            m["baseline_key"] = base_key
            m["baseline_ambiguous"] = sum(
                1 for o in wf_els if (o.file_stem or o.name) == (e.file_stem or e.name)) > 1
            m["graph"] = graph.shape(m)
            m["item_type_sequence"] = graph.item_type_sequence(m)
            m["root_path_items"] = graph.root_path_items(m)
            m["error_handler_path_items"] = graph.error_handler_path_items(m)

            # The single most important derived fact: which script the workflow
            # actually runs first, versus the one that comes first in the file.
            by_item = {i["name"]: i for i in m["items"]}
            first_on_path = next(
                (n for n in m["root_path_items"] if by_item.get(n, {}).get("type") == "task"),
                None)
            first_in_doc = next(
                (i["name"] for i in m["items"] if i["type"] == "task"), None)
            m["first_task_on_root_path"] = first_on_path
            m["first_task_in_doc_order"] = first_in_doc
            m["doc_order_would_mislead"] = bool(
                first_on_path and first_in_doc and first_on_path != first_in_doc)

            workflows.append(m)

        # non-workflow elements, needed for config/resource/action resolution
        for e in elements:
            if e.kind == "Workflow":
                continue
            rec = {
                "source": label, "kind": e.kind, "uuid": e.uuid, "name": e.name,
                "category_path": e.category_path, "source_path": _relative(e.source_path, root),
                "container_format": e.container_format,
                "bytes": len(e.raw) if e.raw else len(e.xml or ""),
                "warnings": e.warnings,
            }
            if e.kind == "ConfigurationElement" and e.xml:
                rec["attributes"] = _config_attrs(e.xml)
            if e.kind in ("ScriptModule", "ScriptingAction") and e.xml:
                rec["script"] = _module_script(e.xml)
            if e.kind == "ResourceElement":
                rec["binary"] = _looks_binary(e.raw)
                rec["mime"] = e.mime or ""
                if not rec["binary"] and e.raw:
                    # the text a spec prompt can show, capped: a template or a properties file, not a dump
                    text = formats.decode_bytes(e.raw)
                    rec["text"] = text[:RESOURCE_TEXT_MAX]
                    rec["text_cut"] = len(text) > RESOURCE_TEXT_MAX
            other_elements.append(rec)

    # cross-workflow call graph
    by_wfid = {w["id"]: w for w in workflows if w.get("id")}
    call_graph: dict[str, list[dict]] = {"nodes": [], "edges": [], "unresolved": []}
    for w in workflows:
        if not w.get("wid"):
            continue
        call_graph["nodes"].append({"wid": w["wid"], "id": w.get("id", ""),
                                    "name": w["name"], "source": w["source"]})
        for c in w.get("nested_calls", []):
            tgt = by_wfid.get(c["workflow_id"])
            if tgt and tgt.get("wid"):
                call_graph["edges"].append({
                    "from": w["wid"], "to": tgt["wid"], "mode": c["mode"],
                    "item": c["item"]})
            else:
                call_graph["unresolved"].append({
                    "from": w["wid"], "workflow_id": c["workflow_id"],
                    "mode": c["mode"], "item": c["item"],
                    "display_name": c.get("display_name")})

    indeg: defaultdict[str, int] = defaultdict(int)
    for ed in call_graph["edges"]:
        indeg[ed["to"]] += 1
    callers = defaultdict(list)
    for ed in call_graph["edges"]:
        callers[ed["to"]].append(ed["from"])

    for w in workflows:
        if w.get("wid"):
            w["called_by"] = sorted(set(callers.get(w["wid"], [])))
            w["is_orphan"] = indeg.get(w["wid"], 0) == 0

    # write
    for w in workflows:
        if not w.get("uid"):
            continue
        with open(os.path.join(out_dir, "model", f"{w['uid']}.json"), "w") as f:
            json.dump(w, f, indent=1)

    index = {
        "workflows": [
            {k: w.get(k) for k in (
                "wid", "uid", "id", "name", "source", "category_path",
                "container_format", "baseline_key", "baseline_ambiguous",
                "total_script_lines", "sdk_calls", "plugin_types", "has_ognl",
                "item_type_counts", "doc_order_would_mislead", "is_orphan")}
            for w in workflows if w.get("uid")
        ],
        "graph_summary": {w["uid"]: w["graph"] for w in workflows if w.get("uid")},
        "call_graph": call_graph,
        "other_elements": other_elements,
        "package_summaries": package_summaries,
        "failures": failures,
        "counts": {
            "workflows_parsed": len([w for w in workflows if w.get("uid")]),
            "parse_failures": len(failures),
            "other_elements": len(other_elements),
        },
    }
    with open(os.path.join(out_dir, "index.json"), "w") as f:
        json.dump(index, f, indent=1)
    return index


ATT_RE = re.compile(
    r'<att\s+name="([^"]*)"\s+type="([^"]*)"[^>]*>(.*?)</att>', re.S)
VALUE_RE = re.compile(r"<value[^>]*>(?:<!\[CDATA\[(.*?)\]\]>|([^<]*))</value>", re.S)


def _config_attrs(xml: str) -> list[dict]:
    out = []
    for m in ATT_RE.finditer(xml):
        name, typ, body = m.groups()
        vm = VALUE_RE.search(body)
        val = (vm.group(1) if vm and vm.group(1) is not None else
               (vm.group(2) if vm else None))
        out.append({
            "name": name,
            "type": typ,
            "is_secure": typ.lower() == "securestring",
            "value": None if val == workflow.NULL_SENTINEL else val,
            "is_null_sentinel": val == workflow.NULL_SENTINEL,
        })
    return out


SCRIPT_RE = re.compile(r"<script[^>]*>(?:<!\[CDATA\[(.*?)\]\]>|(.*?))</script>", re.S)


def _module_script(xml: str) -> str:
    m = SCRIPT_RE.search(xml)
    if not m:
        return ""
    return (m.group(1) if m.group(1) is not None else m.group(2) or "").strip()


def _looks_binary(raw: bytes) -> bool:
    if not raw:
        return False
    sample = raw[:4096]
    if b"\x00" in sample:
        return True
    printable = sum(1 for b in sample if 32 <= b < 127 or b in (9, 10, 13))
    return printable / len(sample) < 0.85
