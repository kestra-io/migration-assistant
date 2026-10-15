"""
The calls a run will still make, for the cost estimate before its first billed call (model.Gate).

A run asks in this order: Assess names the domains, Understand writes a spec per workflow with none on disk or a
stale one (its prompt would differ today, as after the export changed) and then a summary per group of workflows, Assess names the domains again from those specs, and Brief writes the
walkthroughs. A spec's prompt is built here exactly as Understand builds it (its dry run), so a spec whose reply
is cached costs nothing; the calls whose prompts depend on replies not in yet are priced by their expected
size. The current call is always one of them.
"""
from __future__ import annotations

import json
import os

from . import common as C

# Expected prompt and schema size, in characters, of the calls whose prompts wait on other replies; measured
# on the load balancer use case.
DOMAIN_NAMES_CHARS = 2400
WALKTHROUGHS_CHARS = 2900
SUMMARY_CHARS = 1000                # a group summary: this, and SUMMARY_PER_MEMBER for each workflow in it
SUMMARY_PER_MEMBER = 1500
# what a reader's prompt carries for each writer whose spec is not written yet when the dry run builds it
SHARED_SPEC_CHARS = 3000
# what a caller's prompt carries for each callee whose spec is written in the same run (a whole spec)
CALLEE_SPEC_CHARS = 15000


def _missing_specs() -> list[str]:
    """The specs Understand will write: those with none on disk, and the stale ones it writes again."""
    from kestra_migration_assistant.vra import understand as U
    idx = os.path.join(C.WORK, "index.json")
    if not os.path.exists(idx):
        return []
    uids = sorted(w["uid"] for w in json.load(open(idx))["workflows"])
    stale = U.stale_specs(uids)
    return [u for u in uids if not os.path.exists(os.path.join(C.SPEC_DIR, f"{u}.json")) or u in stale]


def _spec_calls(missing: list[str]) -> list[dict]:
    """Each spec to write, with the prompt Understand would send now (its dry run, with the specs it writes again
    left out, as Understand moves them aside first)."""
    from kestra_migration_assistant.vra import understand as U
    idx = json.load(open(os.path.join(C.WORK, "index.json")))
    uids = sorted(w["uid"] for w in idx["workflows"])
    dry = U.run(C.WORK, uids, spec_dir=C.SPEC_DIR, call_tree=True, entry_points=U._entry_points_by_uid(), dry=True,
                regenerate=set(missing), **U._shared_state())
    uid_of = {w["wid"]: w["uid"] for w in idx["workflows"]}
    callees: dict[str, set[str]] = {u: set() for u in uids}
    for e in idx["call_graph"]["edges"]:
        if e["from"] in uid_of and e["to"] in uid_of:
            callees[uid_of[e["from"]]].add(uid_of[e["to"]])
    out = []
    for u in missing:
        ctx = dry["contexts"].get(u) or {}
        waiting = sum(1 for r in ctx.get("shares_state_with") or [] if r.endswith("(name only)"))
        written_before = len(callees.get(u, set()) & set(missing))
        if waiting or written_before:
            # a spec it will carry is not written yet: the real prompt is longer, and not cached
            out.append({"purpose": "understand", "chars": len(dry["prompts"][u]) + len(json.dumps(dry["schema"]))
                        + waiting * SHARED_SPEC_CHARS + written_before * CALLEE_SPEC_CHARS})
        else:
            out.append({"purpose": "understand", "prompt": dry["prompts"][u], "schema": dry["schema"]})
    return out


def _summary_calls(written: set[str]) -> list[dict]:
    """A summary for each group with a spec written this run."""
    p = os.path.join(C.WORK, "assess", "clusters-estate.json")
    groups = json.load(open(p))["clusters"] if os.path.exists(p) else [{"members": sorted(written)}]
    return [{"purpose": "cluster-summary", "chars": SUMMARY_CHARS + SUMMARY_PER_MEMBER * len(g["members"])}
            for g in groups if set(g["members"]) & written]


def planned(current: dict) -> list[dict]:
    """The calls this run will make from `current` (the first one not in the cache) on."""
    missing = _missing_specs()
    step = current["purpose"]
    calls: list[dict] = [] if step == "understand" else [current]
    if missing and step in ("stages-domain-names", "understand"):
        calls += _spec_calls(missing)
        calls += _summary_calls(set(missing))
        calls.append({"purpose": "stages-domain-names", "chars": DOMAIN_NAMES_CHARS})   # again, from the specs
    elif step == "cluster-summary":
        calls.append({"purpose": "stages-domain-names", "chars": DOMAIN_NAMES_CHARS})
    if step != "stages-walkthroughs":
        calls.append({"purpose": "stages-walkthroughs", "chars": WALKTHROUGHS_CHARS})
    return calls
