"""
Phase 3b: clustering, and the green / amber / red score.

Method: single-linkage agglomerative clustering over the pairwise similarity
from fingerprint.py, cut at THRESHOLD, implemented as union-find.

Why this method
---------------
The requirement is that a person can read why two workflows landed together.
Single-linkage gives exactly that: two workflows are in the same cluster if and
only if there is a chain of pairs each at or above the threshold, so every
membership is justified by a specific pair with a specific per-feature
breakdown. k-means or an embedding would give tighter groups and no explanation.
The cost of single-linkage is chaining, where a bridge workflow pulls two
families together; the report lists the bridging pair for every cluster of more
than two so that chaining is visible rather than hidden.

Every workflow lands in exactly one cluster. Singletons are expected and fine.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict

from .fingerprint import THRESHOLD, WEIGHTS, load_fingerprints, similarity


class _UF:
    def __init__(self, items):
        self.p = {i: i for i in items}

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def cluster(fps: dict[str, dict], threshold: float = THRESHOLD):
    uids = sorted(fps)
    uf = _UF(uids)
    pairs = []
    for i, a in enumerate(uids):
        for b in uids[i + 1:]:
            score, per = similarity(fps[a], fps[b])
            if score >= threshold:
                uf.union(a, b)
                pairs.append({"a": a, "b": b, "score": round(score, 3),
                              "per_feature": {k: round(v, 3) for k, v in per.items()}})
    groups = defaultdict(list)
    for u in uids:
        groups[uf.find(u)].append(u)
    return groups, pairs


# ---------------------------------------------------------------- colour score

# Plugin prefixes with a known Kestra route. Anything else is a red blocker,
# because there is no way to talk to that system from a converted flow.
KNOWN_PLUGIN_PREFIXES = {
    "VC": "io.kestra.plugin.ee.vmware / HTTP",
    "SSH": "io.kestra.plugin.fs.ssh",
    "REST": "io.kestra.plugin.core.http",
    "SOAP": "io.kestra.plugin.core.http",
    "HTTP": "io.kestra.plugin.core.http",
    "SQL": "io.kestra.plugin.jdbc",
    "PowerShell": "io.kestra.plugin.scripts.powershell",
    "Mail": "io.kestra.plugin.notifications",
    "SNMP": "io.kestra.plugin.core.http",
}

AMBER_SCRIPT_LINES = 40
AMBER_DEPTH = 3

# vRO built-in types that are file-shaped: they mean a path on the orchestrator
# appliance, which Kestra has no equivalent for.
FILEISH_TYPES = {"Path", "MimeAttachment", "Image", "Attachment"}


def colour(model: dict, other_elements: list[dict]) -> dict:
    g = model.get("graph", {})
    red: list[str] = []
    amber: list[str] = []

    # --- hard blockers -------------------------------------------------
    if g.get("cycle"):
        red.append("cycle in the item graph; a Kestra flow is a DAG")
    n_ognl = len(model.get("presentation", {}).get("ognl", []))
    if n_ognl:
        red.append(f"{n_ognl} OGNL presentation qualifier(s); no Kestra equivalent")
    n_input = model.get("item_type_counts", {}).get("input", 0)
    if n_input:
        red.append(f"{n_input} user-interaction item(s); needs Pause or an App, "
                   "and the assignee and timeout semantics restated")

    binary_names = {e["name"] for e in other_elements
                    if e.get("kind") == "ResourceElement" and e.get("binary")}
    used_binary = sorted(
        n for n in binary_names
        if any(n and n in (s.get("source") or "") for s in model.get("scripts", [])))
    if used_binary:
        red.append("binary resource element dependency: " + ", ".join(used_binary))

    fileish = sorted({
        p["type"] for p in model.get("inputs", []) + model.get("attributes", [])
        if p["type"] in FILEISH_TYPES})
    if fileish:
        red.append("file-typed parameter with no Kestra equivalent: " + ", ".join(fileish))

    unmapped = sorted({
        t for t in model.get("plugin_types", [])
        if t.split(":")[0] not in KNOWN_PLUGIN_PREFIXES})
    if unmapped:
        red.append("plugin type with no known Kestra mapping: " + ", ".join(unmapped[:6]))

    # --- amber signals -------------------------------------------------
    mapped = sorted({t for t in model.get("plugin_types", []) if t not in unmapped})
    if mapped:
        amber.append(f"{len(mapped)} mapped plugin type(s)")
    sdk = [c for c in model.get("sdk_calls", [])
           if not c.startswith(("System.log", "System.debug", "System.warn",
                                "System.error", "Server.log", "Server.error"))]
    if sdk:
        amber.append(f"{len(sdk)} non-logging SDK call(s)")
    if model.get("total_script_lines", 0) > AMBER_SCRIPT_LINES:
        amber.append(f"{model['total_script_lines']} lines of JavaScript")
    if model.get("nested_calls"):
        amber.append(f"{len(model['nested_calls'])} nested or async workflow call(s)")
    if g.get("depth", 0) > AMBER_DEPTH:
        amber.append(f"path depth {g['depth']}")
    if model.get("error_handler_items") or model.get("throw_end_items") or model.get("default_error_handler"):
        amber.append("error handling present")
    ctrl = sum(model.get("item_type_counts", {}).get(t, 0) for t in
               ("switch", "condition", "custom-condition", "foreach",
                "waiting-timer", "decision-activity"))
    if ctrl:
        amber.append(f"{ctrl} control-flow item(s)")
    if model.get("graph", {}).get("unreachable_items"):
        amber.append(f"{len(g['unreachable_items'])} item(s) unreachable from root")

    band = "red" if red else ("amber" if amber else "green")
    return {"band": band, "red_reasons": red, "amber_reasons": amber,
            "unmapped_plugin_types": unmapped, "mapped_plugin_types": mapped}


# ---------------------------------------------------------------- driver

def run(out_dir: str, uids: list[str], label: str = "dev") -> dict:
    model_dir = os.path.join(out_dir, "model")
    idx = json.load(open(os.path.join(out_dir, "index.json")))
    others = idx["other_elements"]

    fps = load_fingerprints(model_dir, uids)
    groups, pairs = cluster(fps)

    models = {u: json.load(open(os.path.join(model_dir, f"{u}.json"))) for u in uids}
    colours = {u: colour(models[u], others) for u in uids}


    clusters = []
    for n, (_root, members) in enumerate(sorted(groups.items(),
                                                key=lambda kv: (-len(kv[1]), kv[0])), 1):
        members = sorted(members)
        # first of a kind: most items, then most script lines, then name, so the
        # richest member is the one converted by hand-quality prompting first.
        fok = max(members, key=lambda u: (
            models[u]["graph"]["items"], models[u]["total_script_lines"], u))
        internal = [p for p in pairs if p["a"] in members and p["b"] in members]
        internal.sort(key=lambda p: -p["score"])
        clusters.append({
            "cluster_id": f"C{n:02d}",
            "members": members,
            "size": len(members),
            "first_of_a_kind": fok,
            "shape_sigs": sorted({fps[u]["shape_sig"] for u in members}),
            "sources": sorted({fps[u]["source"] for u in members}),
            "plugins": sorted({p for u in members for p in fps[u]["plugins"]}),
            "colour_mix": _counts(colours[u]["band"] for u in members),
            "strongest_pair": internal[0] if internal else None,
            "weakest_internal_pair": internal[-1] if internal else None,
            "all_internal_pairs": internal,
        })

    result = {
        "label": label,
        "threshold": THRESHOLD,
        "weights": WEIGHTS,
        "method": "single-linkage agglomerative over weighted feature similarity, "
                  "implemented as union-find, cut at the threshold",
        "n_workflows": len(uids),
        "n_clusters": len(clusters),
        "n_singletons": sum(1 for c in clusters if c["size"] == 1),
        "clusters": clusters,
        "colours": colours,
        "colour_totals": _counts(colours[u]["band"] for u in uids),
        "fingerprints": fps,
    }
    os.makedirs(os.path.join(out_dir, "assess"), exist_ok=True)
    with open(os.path.join(out_dir, "assess", f"clusters-{label}.json"), "w") as f:
        json.dump(result, f, indent=1)
    return result


def _counts(seq):
    out = {}
    for x in seq:
        out[x] = out.get(x, 0) + 1
    return out
