"""Phase 3c: the estate report an architect reads before committing to a migration."""
from __future__ import annotations

import json
import os
from collections import Counter

from .cluster import KNOWN_PLUGIN_PREFIXES


def write_estate_report(out_dir: str, label: str, path: str) -> str:
    res = json.load(open(os.path.join(out_dir, "assess", f"clusters-{label}.json")))
    idx = json.load(open(os.path.join(out_dir, "index.json")))
    models = {}
    for u in res["colours"]:
        models[u] = json.load(open(os.path.join(out_dir, "model", f"{u}.json")))

    colours = res["colours"]
    totals = Counter(c["band"] for c in colours.values())

    plugin_use: Counter[str] = Counter()
    unmapped_use: Counter[str] = Counter()
    for m in models.values():
        for t in m.get("plugin_types", []):
            plugin_use[t] += 1
            if t.split(":")[0] not in KNOWN_PLUGIN_PREFIXES:
                unmapped_use[t] += 1

    action_use: Counter[str] = Counter()
    for m in models.values():
        for a in m.get("action_calls", []):
            key = a["module"] + (f".{a['action']}" if a.get("action") else "")
            action_use[key] += 1

    cg = idx["call_graph"]
    # The call graph is keyed on wid, the models dict on uid. Comparing the two
    # directly made every workflow look like an orphan.
    wids_in_scope = {m["wid"] for m in models.values()}
    called = {e["to"] for e in cg["edges"] if e["from"] in wids_in_scope}
    orphans = sorted(u for u, m in models.items() if m["wid"] not in called)

    sdk_use: Counter[str] = Counter()
    for m in models.values():
        for c in m.get("sdk_calls", []):
            sdk_use[c] += 1

    L: list[str] = []
    A = L.append
    A(f"# Estate report: {label} set")
    A("")
    A(f"{len(models)} workflows, read straight from the source packages and XML. "
      "Nothing here involved a model; it is all static analysis, so it can be run "
      "on a customer estate before any migration decision is taken.")
    A("")

    A("## Readiness")
    A("")
    A("| Band | Count | Meaning |")
    A("|---|---:|---|")
    A(f"| green | {totals.get('green',0)} | Linear task and end items only, no plugin object types, no SDK calls beyond logging. Convert and move on. |")
    A(f"| amber | {totals.get('amber',0)} | Converts, but a person has to make choices: control flow, error handling, mapped plugin types, nested calls, or more than 40 lines of JavaScript. |")
    A(f"| red | {totals.get('red',0)} | At least one hard blocker. These need a decision before conversion, not after. |")
    A(f"| **total** | **{len(models)}** | |")
    A("")

    A("### What makes a workflow red")
    A("")
    A("Any one of these is enough:")
    A("")
    A("| Blocker | Workflows |")
    A("|---|---:|")
    import re as _re
    reason_counts: Counter[str] = Counter()
    for c in colours.values():
        for r in c["red_reasons"]:
            head = r.split(":")[0].split(";")[0].split("(")[0].strip()
            head = _re.sub(r"^\d+\s+", "", head)          # drop the leading count
            head = _re.sub(r"\bitem\b$", "items", head)
            reason_counts[head] += 1
    for r, n in reason_counts.most_common():
        A(f"| {r} | {n} |")
    A("")

    # Which rule fired, per workflow, and whether one rule dominates.
    RULES = [
        ("cycle in the item graph", "cycle"),
        ("OGNL presentation qualifier", "ognl"),
        ("user-interaction item", "user-interaction"),
        ("binary resource element dependency", "binary-resource"),
        ("file-typed parameter with no Kestra equivalent", "file-typed-param"),
        ("plugin type with no known Kestra mapping", "unmapped-plugin"),
    ]
    reds = [u for u in sorted(colours) if colours[u]["band"] == "red"]
    fired = {}
    for u in reds:
        hits = []
        for needle, key in RULES:
            if any(needle in r for r in colours[u]["red_reasons"]):
                hits.append(key)
        fired[u] = hits or ["(unclassified)"]

    only: Counter[str] = Counter()
    any_of: Counter[str] = Counter()
    for hits in fired.values():
        for h in hits:
            any_of[h] += 1
        if len(hits) == 1:
            only[hits[0]] += 1

    A("### Which rule made each workflow red")
    A("")
    A("A workflow can trip more than one rule. \"Sole reason\" counts the ones "
      "where removing that single rule would turn the workflow amber.")
    A("")
    A("| Rule | Workflows tripping it | Sole reason |")
    A("|---|---:|---:|")
    for _needle, key in RULES:
        if any_of.get(key):
            A(f"| {key} | {any_of[key]} | {only.get(key, 0)} |")
    if any_of.get("(unclassified)"):
        A(f"| (unclassified) | {any_of['(unclassified)']} | {only.get('(unclassified)', 0)} |")
    A(f"| **red workflows** | **{len(reds)}** | |")
    A("")

    if reds:
        top_key, top_n = any_of.most_common(1)[0]
        share = top_n / len(reds)
        if share >= 0.5:
            A(f"**One rule dominates.** `{top_key}` accounts for {top_n} of "
              f"{len(reds)} red workflows, and is the sole reason for "
              f"{only.get(top_key, 0)} of them. Read that as one decision rather "
              f"than {top_n}: if a route to those systems exists, or the customer "
              f"accepts rebuilding those integrations, most of this red list moves. "
              f"The remaining {len(reds) - only.get(top_key, 0)} are red for reasons "
              f"that survive that decision.")
            A("")
            if top_key == "unmapped-plugin":
                A("The unmapped types here are vendor plugin objects (VSPK, Avi, "
                  "vCloud). Nothing in Kestra speaks those protocols natively, so "
                  "each becomes an HTTP or SDK integration a person has to build "
                  "and own. That is a scoping conversation, not a conversion "
                  "defect, and it is the single largest thing standing between "
                  "this corpus and a migration.")
                A("")

    A("### The red list")
    A("")
    A("| Workflow | Source | Why |")
    A("|---|---|---|")
    for u in sorted(colours):
        if colours[u]["band"] != "red":
            continue
        A(f"| `{u}` | {models[u]['source']} | {'; '.join(colours[u]['red_reasons'])} |")
    A("")

    A("## Clusters")
    A("")
    A(f"{res['n_clusters']} clusters, {res['n_singletons']} of them singletons. "
      f"Single-linkage over the weighted fingerprint, cut at {res['threshold']}.")
    A("")
    A("| Cluster | Size | Sources | Colour mix | First of a kind |")
    A("|---|---:|---|---|---|")
    for c in res["clusters"]:
        mix = ", ".join(f"{k} {v}" for k, v in sorted(c["colour_mix"].items()))
        A(f"| {c['cluster_id']} | {c['size']} | {', '.join(c['sources'])} | {mix} | `{c['first_of_a_kind']}` |")
    A("")

    A("## Plugins in use")
    A("")
    A("Every vRO plugin object type that appears in a signature or a binding. "
      "A type with no Kestra route is a blocker, not a detail.")
    A("")
    A("| Plugin object type | Workflows | Kestra route |")
    A("|---|---:|---|")
    for t, n in plugin_use.most_common():
        prefix = t.split(":")[0]
        route = KNOWN_PLUGIN_PREFIXES.get(prefix, "**none known**")
        A(f"| `{t}` | {n} | {route} |")
    if not plugin_use:
        A("| (none) | | |")
    A("")

    A("## vRO SDK surface")
    A("")
    A("What the JavaScript actually calls. This is the work that cannot be moved, "
      "only rewritten.")
    A("")
    A("| SDK call | Workflows |")
    A("|---|---:|")
    for c, n in sdk_use.most_common(25):
        A(f"| `{c}` | {n} |")
    A("")

    A("## Most-called actions")
    A("")
    if action_use:
        A("| Action | Called from |")
        A("|---|---:|")
        for a, n in action_use.most_common(20):
            A(f"| `{a}` | {n} |")
    else:
        A("No action calls resolved in this set.")
    A("")

    A("## Call graph")
    A("")
    A(f"- Resolved nested or async calls between workflows in the corpus: {len(cg['edges'])}")
    A(f"- Calls whose target is not in the corpus: {len(cg['unresolved'])}")
    A(f"- Workflows nothing else calls (entry points or dead content): {len(orphans)}")
    A("")
    if cg["unresolved"]:
        A("Unresolved targets matter: each is a workflow the estate depends on that "
          "was not in the package. A migration that converts only what was exported "
          "will break at these points.")
        A("")
        A("| Caller | Item | Mode | Target workflow ID |")
        A("|---|---|---|---|")
        seen = set()
        for r in cg["unresolved"]:
            if r["from"] not in {models[u]["wid"] for u in models}:
                continue
            k = (r["from"], r["workflow_id"])
            if k in seen:
                continue
            seen.add(k)
            A(f"| `{r['from']}` | {r['item']} | {r['mode']} | `{r['workflow_id']}` |")
        A("")

    A("## Orphans")
    A("")
    A("Nothing in this corpus calls these. In a real estate that means either an "
      "entry point a person or a schedule starts, or content nobody runs any more. "
      "Telling the two apart is a conversation, not an analysis.")
    A("")
    for u in orphans:
        A(f"- `{u}` ({models[u]['source']}, {colours[u]['band']})")
    A("")

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(L))
    return path
