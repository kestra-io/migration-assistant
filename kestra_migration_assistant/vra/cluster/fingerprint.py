"""
Phase 3a: a fingerprint per workflow.

Six features, each chosen because it changes what the conversion has to do:

1. shape_sig    The item-type sequence along the root path, run-length encoded.
                Two workflows with the same shape need the same Kestra control
                flow, whatever their scripts say.
2. graph_sig    Branches, joins, cycle, depth, linear. Shape alone does not
                distinguish a two-arm condition from a five-arm switch.
3. plugin_types The vRO plugin object types in the signature and bindings. These
                decide which Kestra plugin, or whether a conversion is possible
                at all.
4. sdk_calls    The vRO SDK surface used in scripts. Decides how much of the
                JavaScript survives a rewrite.
5. io_sig       Input and output names with types. Two workflows with the same
                signature are usually variants of one operation.
6. script_set   Shingles of normalized script text (identifiers, literals and
                whitespace removed). Catches copy-paste families, which is what
                a console-authored estate is mostly made of.

Similarity is a weighted sum of per-feature similarities, all in 0..1, so a pair
score can be read back as "these two matched on shape and scripts but not on
plugin types". That readability is the reason for not using an embedding.
"""
from __future__ import annotations

import json
import os
import re

WEIGHTS = {
    "shape": 0.30,     # the control flow that must be rebuilt
    "graph": 0.10,     # branching and depth
    "plugins": 0.15,   # which target plugin
    "sdk": 0.10,       # how much script survives
    "io": 0.15,        # the operation's signature
    "scripts": 0.20,   # copy-paste family
}

# A pair at or above this joins the same cluster. Chosen by sweeping the dev set
# and reading the resulting groups; see report section 3 for the sweep table.
THRESHOLD = 0.62


def _rle(seq: list[str]) -> str:
    out, prev, n = [], None, 0
    for x in seq:
        if x == prev:
            n += 1
        else:
            if prev is not None:
                out.append(f"{prev}x{n}" if n > 1 else prev)
            prev, n = x, 1
    if prev is not None:
        out.append(f"{prev}x{n}" if n > 1 else prev)
    return ">".join(out)


def _norm_type(t: str) -> str:
    """Collapse a plugin object type to its plugin, so VC:VirtualMachine and
    VC:Datastore count as the same integration concern."""
    m = re.match(r"^([A-Za-z][A-Za-z0-9_]*):", t or "")
    return m.group(1) if m else t


def fingerprint(model: dict) -> dict:
    g = model.get("graph", {})
    shape_seq = model.get("item_type_sequence") or []
    io_in = [f"{i['name']}:{i['type']}" for i in model.get("inputs", [])]
    io_out = [f"{o['name']}:{o['type']}" for o in model.get("outputs", [])]

    shingles: set[str] = set()
    for _item, sh in (model.get("script_shingles") or {}).items():
        shingles.update(sh)

    return {
        "uid": model["uid"],
        "shape_sig": _rle(shape_seq),
        "shape_multiset": sorted(model.get("item_type_counts", {}).items()),
        "graph_sig": {
            "branches": g.get("branches", 0),
            "joins": g.get("joins", 0),
            "cycle": bool(g.get("cycle")),
            "depth": g.get("depth", 0),
            "linear": bool(g.get("linear")),
        },
        "plugins": sorted({_norm_type(t) for t in model.get("plugin_types", [])}),
        "plugin_types_full": sorted(model.get("plugin_types", [])),
        "sdk": sorted({c.split(".")[0] for c in model.get("sdk_calls", [])}),
        "sdk_full": sorted(model.get("sdk_calls", [])),
        "io_in": sorted(io_in),
        "io_out": sorted(io_out),
        "script_shingles": sorted(shingles),
        "script_lines": model.get("total_script_lines", 0),
        "nested_calls": len(model.get("nested_calls", [])),
        "source": model.get("source"),
        "name": model.get("name"),
    }


# ------------------------------------------------------------------ similarity

def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _seq_sim(a: str, b: str) -> float:
    """Token-level similarity of two run-length-encoded shape signatures."""
    at, bt = a.split(">"), b.split(">")
    if not at and not bt:
        return 1.0
    if a == b:
        return 1.0
    # longest common subsequence over the token lists
    n, m = len(at), len(bt)
    if n == 0 or m == 0:
        return 0.0
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n):
        for j in range(m):
            dp[i + 1][j + 1] = (dp[i][j] + 1) if at[i] == bt[j] else max(dp[i][j + 1], dp[i + 1][j])
    return 2 * dp[n][m] / (n + m)


def _graph_sim(a: dict, b: dict) -> float:
    parts = []
    parts.append(1.0 if a["cycle"] == b["cycle"] else 0.0)
    parts.append(1.0 if a["linear"] == b["linear"] else 0.0)
    for k in ("branches", "joins", "depth"):
        hi = max(a[k], b[k])
        parts.append(1.0 if hi == 0 else 1.0 - abs(a[k] - b[k]) / hi)
    return sum(parts) / len(parts)


def similarity(fa: dict, fb: dict) -> tuple[float, dict]:
    """Return (score, per-feature contributions) so a pair can be explained."""
    per = {
        "shape": _seq_sim(fa["shape_sig"], fb["shape_sig"]),
        "graph": _graph_sim(fa["graph_sig"], fb["graph_sig"]),
        "plugins": _jaccard(set(fa["plugins"]), set(fb["plugins"])),
        "sdk": _jaccard(set(fa["sdk"]), set(fb["sdk"])),
        "io": _jaccard(set(fa["io_in"]) | set(fa["io_out"]),
                       set(fb["io_in"]) | set(fb["io_out"])),
        "scripts": _jaccard(set(fa["script_shingles"]), set(fb["script_shingles"])),
    }
    score = sum(WEIGHTS[k] * v for k, v in per.items())
    return score, per


def load_fingerprints(model_dir: str, uids: list[str]) -> dict[str, dict]:
    out = {}
    for uid in uids:
        with open(os.path.join(model_dir, f"{uid}.json")) as f:
            out[uid] = fingerprint(json.load(f))
    return out
