"""
Wave plan: which workflows convert together, in which order, and what must be
true first.

What goes into waves
- Convert, Near-duplicate and Needs owner input work. Wrappers, plugin-covered
  libraries and rebuilds are not converted; Manual work is rewritten by hand;
  retire candidates wait for an owner.
- Anything that calls Manual work, directly or through a wrapper, is blocked
  until that work is rewritten, and so is a near-duplicate whose first of a kind
  is Manual or blocked. Blocked work is listed with what it waits for.

How workflows are grouped (the rule, in four steps)
1. Unit. A cluster is the unit: its members share a pattern, so the first of a
   kind pilots the rest. Clusters that call each other in a loop form one unit,
   and so do the clusters of one deployment lifecycle group (workflows started by
   one deployment's lifecycle events that share state one of them writes). A
   lifecycle group moves together: if one member is Manual, Rebuild or blocked,
   the others wait for it.
2. Key. Each unit gets a risk tier (no external target, read-only, writes; judged
   from operation names) and a primary target (the system its operations call
   most). Units with the same tier and primary target form one group: shared
   pattern inside, shared target across.
3. Budget. The number of waves is capped at ceil(log2(n / 4)) for n waved
   workflows, at least 1 and at most 12: 4 waves for 33 to 64 workflows, 9 for
   1,025 to 2,048, 10 for 2,049 to 4,096. Waves grow with the estate, but slowly,
   because each wave needs its own owner sign-off and pilot. While there are more
   groups than the budget, the smallest group is merged into the group that is
   closest: same tier first, then the most shared targets, then the most shared
   domains (the same pattern family), then the smallest.
4. Order. Callees before callers, then lower risk first. Groups that end up
   depending on each other in a loop are merged. Inside a wave, callees come
   first, then by cluster, near-duplicates after their first of a kind.

A wave only exists when every target it touches is reachable (plugin, http, or
replaced by design).

When preflight Q9 says vRA is replaced, what vRA did is work too: the Apps, the flows the cloud templates
become and their lifecycle steps are planned in replacement waves after the workflow waves they depend on
(stages/replace_vra.py), and each blocking subscription's timeout and failure policy is a gate there.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict

from . import gates as G
from . import ids as I

RISK_LABEL = {0: "no external target", 1: "read-only", 2: "writes"}
WAVED = ("Convert", "Near-duplicate", "Needs owner input")


def wave_budget(n: int) -> int:
    """ceil(log2(n / 4)), clamped to 1..12. 33-64 -> 4; 2,049-4,096 -> 10."""
    if n <= 8:
        return 1
    return max(1, min(12, math.ceil(math.log2(n / 4))))


def plan(assess: dict, triage: dict[str, dict], answers: dict, reviews: dict | None = None,
         act_tri: dict | None = None, trigger_plan: dict | None = None, specs: dict | None = None,
         question_keys: dict | None = None) -> dict:
    """reviews: reviews.load() (a reviewed spec closes its gate); act_tri: action triage (which actions
    each wave carries); trigger_plan: triggers.plan() (when vRA is replaced, its replacement is waved);
    specs: uid -> spec (each suspected defect is a preserve-or-fix decision); question_keys: (uid, question) ->
    the answers.yaml key of the grouped question (one key for a question several workflows ask)."""
    from . import triggers as TG
    reviewed = (reviews or {}).get("by_uid") or {}
    front = (trigger_plan or {}).get("front_end")
    replacing = front in TG.REPLACING and bool(
        ((assess.get("entry_points") or {}).get("model") or {}).get("cloud_templates"))
    wf = assess["workflows"]
    targets = assess["targets"]
    open_q = set(answers["open"])

    def callees(u, seen=None):
        """Exported dev callees, looking through wrappers."""
        seen = seen or set()
        out = set()
        for n in wf[u]["nested"]:
            v = n.get("target_uid")
            if not v or v not in wf or v in seen:
                continue
            seen.add(v)
            if triage[v]["outcome"] == "Wrapper":
                out |= callees(v, seen)
            out.add(v)
        return out

    # workflows started by one deployment's lifecycle events and sharing its state move together
    lifecycle_of = {u: g for g in assess.get("lifecycle_groups") or [] for u in g["members"]}

    # --- what is blocked, and on what -----------------------------------
    # held: work no wave can take yet, rewritten by hand or not judged without a model
    manual = {u for u, t in triage.items() if t["outcome"] in ("Manual", "Needs a model")}
    blocked: dict[str, list[str]] = {}
    changed = True
    while changed:
        changed = False
        for u, t in triage.items():
            if u in blocked or t["outcome"] not in WAVED:
                continue
            why = []
            if t["outcome"] == "Near-duplicate":
                f = t["evidence"]["first_of_a_kind"]
                if f in manual or f in blocked:
                    why.append(f"its first of a kind {f} is {triage[f]['outcome'] if f in manual else 'blocked'}")
            bad_m = sorted(v for v in callees(u) if v in manual)
            bad_b = sorted(v for v in callees(u) if v in blocked)
            hand = [v for v in bad_m if triage[v]["outcome"] == "Manual"]
            if hand:
                why.append(f"calls Manual work, rewritten by hand first: {', '.join(hand)}")
            if len(hand) < len(bad_m):
                why.append("calls work that needs a model to judge: "
                           + ", ".join(v for v in bad_m if v not in hand))
            if bad_b:
                why.append(f"calls blocked work: {', '.join(bad_b)}")
            g = lifecycle_of.get(u)
            if g:
                held = sorted(v for v in g["members"] if v != u and
                              (triage[v]["outcome"] in ("Manual", "Needs a model", "Rebuild") or v in blocked))
                if held:
                    why.append(f"its deployment lifecycle group {g['id']} moves together, and "
                               + ", ".join(f"{v} is {'blocked' if v in blocked else triage[v]['outcome']}" for v in held))
            if why:
                blocked[u] = why
                changed = True

    waved = sorted(u for u in wf if triage[u]["outcome"] in WAVED and u not in blocked)
    budget = wave_budget(len(waved))

    def ext_ops(us):
        return [o for u in us for o in wf[u]["ops"] if o["target"] != "vro-platform"]

    def tier(us):
        ops = ext_ops(us)
        return 0 if not ops else (2 if any(o["mode"] != "read" for o in ops) else 1)

    def tset(us):
        return frozenset(o["target"] for o in ext_ops(us))

    def primary(us):
        c = Counter(o["target"] for o in ext_ops(us))
        return min(c, key=lambda t: (-c[t], t)) if c else None

    # --- step 1: units are clusters, merged where they call each other in a loop
    by_cluster = defaultdict(list)
    for u in waved:
        by_cluster[wf[u]["cluster"]].append(u)
    cdeps = defaultdict(set)
    for u in waved:
        for v in callees(u):
            if v in by_cluster.get(wf[v]["cluster"], []) and wf[v]["cluster"] != wf[u]["cluster"]:
                cdeps[wf[u]["cluster"]].add(wf[v]["cluster"])
    # a lifecycle group is one unit: its clusters are tied both ways, so they fall into one component
    ldeps = defaultdict(set, {k: set(v) for k, v in cdeps.items()})
    for g in assess.get("lifecycle_groups") or []:
        cl = sorted({wf[u]["cluster"] for u in g["members"] if u in waved})
        for a in cl:
            for b in cl:
                if a != b:
                    ldeps[a].add(b)
    comp = _scc(list(by_cluster), ldeps)
    units = defaultdict(list)
    for c, g in comp.items():
        units[g].append(c)

    def members_of(clusters):
        return sorted(u for c in clusters for u in by_cluster[c])

    # --- step 2: group units by (tier, primary target)
    groups: dict[str, list[str]] = {}
    for cl in units.values():
        us = members_of(cl)
        key = f"{tier(us)}|{primary(us) or '-'}"
        groups.setdefault(key, []).extend(cl)
    natural = len(groups)

    def gdeps_of(gs):
        where = {c: k for k, cl in gs.items() for c in cl}
        d = defaultdict(set)
        for c, ds in cdeps.items():
            for x in ds:
                if where[c] != where[x]:
                    d[where[c]].add(where[x])
        return d

    def collapse_loops(gs):
        d = gdeps_of(gs)
        comp_g = _scc(list(gs), d)
        out = defaultdict(list)
        for k, cid in comp_g.items():
            out[cid].extend(gs[k])
        return dict(out)

    groups = collapse_loops(groups)

    # --- step 3: merge down to the budget
    merges = []
    while len(groups) > budget:
        def size(k):
            return len(members_of(groups[k]))
        small = min(groups, key=lambda k: (size(k), k))
        su = members_of(groups[small])

        def cost(k):
            ku = members_of(groups[k])
            a, b = tset(su), tset(ku)
            jac = len(a & b) / len(a | b) if (a | b) else 1.0
            da, db = {wf[u]["domain"] for u in su}, {wf[u]["domain"] for u in ku}
            dom = len(da & db) / len(da | db)
            return (abs(tier(su) - tier(ku)), -jac, -dom, size(k), k)
        into = min((k for k in groups if k != small), key=cost)
        merges.append({"merged": sorted(groups[small]), "into": sorted(groups[into])})
        groups[into] = groups[into] + groups.pop(small)
        groups = collapse_loops(groups)

    # --- step 4: order, callees first, then lower risk
    gd = gdeps_of(groups)
    order: list[str] = []
    done: set[str] = set()
    pending = set(groups)
    while pending:
        ready = [k for k in pending if gd[k] <= done] or list(pending)
        k = min(ready, key=lambda k: (tier(members_of(groups[k])), len(members_of(groups[k])), k))
        order.append(k)
        done.add(k)
        pending.discard(k)

    total_lines = sum(wf[u]["script_lines"] + 1 for u in waved) or 1
    waves: list[dict] = []
    wave_of: dict[str, int] = {}
    wave_of_uid: dict[str, int] = {}
    for n, k in enumerate(order, 1):
        clusters = sorted(groups[k])
        members = members_of(clusters)
        inside = set(members)

        def depth(u, seen=()):
            cs = [v for v in callees(u) if v in inside and v not in seen]
            return 0 if not cs else 1 + max(depth(v, seen + (u,)) for v in cs)
        members.sort(key=lambda u: (depth(u), wf[u]["cluster"], triage[u]["outcome"] == "Near-duplicate", u))
        wave_of[k] = n
        ts = sorted(tset(members))
        writes = sorted({o["target"] for o in ext_ops(members) if o["mode"] != "read"})
        reads_only = sorted(set(ts) - set(writes))
        dep_waves = sorted({wave_of[d] for d in gd[k]})
        spec_review = [u for u in members if (triage[u]["evidence"] or {}).get("spec_review")]
        owner = [u for u in members if triage[u]["outcome"] == "Needs owner input"]
        lines = sum(wf[u]["script_lines"] + 1 for u in members)
        share = lines / total_lines
        crit = [{"criterion": "The signature covers this wave", "source": "signature",
                 "closes": G.by_approval()}]
        for d in dep_waves:
            crit.append({"criterion": f"Wave {d} has met its exit criteria (it holds workflows this wave calls)",
                         "source": "waves", "closes": _wave_exit(d)})
        for t in writes:
            crit.append({"criterion": f"A non-production {targets[t]['name']} is named for this wave to write to",
                         "source": "preflight Q3", "closes": G.by_answer("Q3")})
        for t in ts:
            crit.append({"criterion": f"{targets[t]['name']}: the instance this wave's flows call is named (base URL "
                                      "or host) and its credentials are stored as Kestra secrets",
                         "source": "target inventory", "closes": _endpoint(t, targets[t])})
            if targets[t]["reach"] == "http":
                crit.append({"criterion": f"{targets[t]['name']}: the client route is chosen (HTTP tasks, the vendor SDK "
                                          "or a new plugin; compared on the plugin coverage page)",
                             "source": "target inventory", "closes": _client_route(t, targets[t], assess)})
                auth = targets[t].get("client_auth")
                if auth and targets[t].get("api_auth"):
                    crit.append({"criterion": f"{targets[t]['name']}: how flows authenticate is decided (the vRO client "
                                              f"uses {auth}; the API also offers {targets[t]['api_auth']})",
                                 "source": "target inventory",
                                 "closes": G.by_decision(
                                     f"auth.{G.key_part(t)}",
                                     f"The vRO client authenticates to {targets[t]['name']} with {auth}. The API also "
                                     f"offers {targets[t]['api_auth']}. Which do the Kestra flows use?",
                                     [f"{auth}, as today", "the other (say which)"],
                                     recommended=f"{auth}, as today",
                                     plain=f"How do the flows sign in to {targets[t].get('short_name') or targets[t]['name']}?",
                                     why="the client's own code; the plugin map's note on the API")})
            if targets[t].get("tls_off"):
                crit.append({"criterion": f"{targets[t]['name']}: certificate checking is decided (the vRO client turns "
                                          f"it off: {targets[t]['tls_off']})", "source": "target inventory",
                             "closes": G.by_decision(
                                 f"tls.{G.key_part(t)}",
                                 f"The vRO client calls {targets[t]['name']} without checking its certificate "
                                 f"({targets[t]['tls_off']}). Do the Kestra calls check it?",
                                 ["check it (the CA goes in the Kestra setup)", "do not check, as today"],
                                 recommended="check it (the CA goes in the Kestra setup)",
                                 plain=f"Do the calls to {targets[t].get('short_name') or targets[t]['name']} check its "
                                       "certificate? (vRO's do not)",
                                 why="carrying the check across off would keep a man-in-the-middle risk vRO had")})
            if targets[t].get("enterprise_only"):
                crit.append({"criterion": f"Kestra Enterprise Edition available ({targets[t]['kestra_tasks'][0].rsplit('.', 1)[0]} is EE)",
                             "source": "plugin map", "closes": edition()})
        if reads_only:
            names = [targets[t]["name"] for t in reads_only]
            crit.append({"criterion": "Owner confirms these calls only read: " + ", ".join(names)
                                      + " (judged from operation names)", "source": "target inventory",
                         "closes": G.by_decision(
                             "reads-only." + "+".join(G.key_part(t) for t in reads_only),
                             "Do these systems' calls only read, as judged from the operation names? "
                             + ", ".join(names) + " (the operations are on the target inventory page, report/reports/target-inventory.html)",
                             ["confirmed", "some calls write"], blocks=["some calls write"],
                             plain=f"Do the calls to {', '.join(names)} only read? (see the target inventory page)",
                             why="a wave that writes needs a non-production target first; if some calls write, "
                                 "say which in the answer and correct the target in your-input/corrections.yaml")})
        if writes:
            names = [targets[t]["name"] for t in writes]
            crit.append({"criterion": "Owner confirms the write classification for " + ", ".join(names),
                         "source": "target inventory",
                         "closes": G.by_decision(
                             "writes." + "+".join(G.key_part(t) for t in writes),
                             "Is the read and write classification of each operation right for " + ", ".join(names)
                             + "? (per operation on the target inventory page, judged from operation names)",
                             ["confirmed", "corrected"], blocks=["corrected"],
                             plain="Are the read and write labels on the target inventory page right?",
                             why="'corrected' holds the wave until the correction is in your-input/corrections.yaml "
                                 "and the brief is re-run; then set it to confirmed")})
        for u in owner:
            ev = triage[u]["evidence"]
            for q in ev.get("blocking_questions", []):
                crit.append({"criterion": f"Owner answers for {_nm(wf, u)}: {q}", "source": "spec",
                             "closes": G.by_decision((question_keys or {}).get((u, q))
                                                     or f"question.{_sh(wf, u)}.{_short_hash(q)}",
                                                     f"{wf[u].get('name') or u}: {q}",
                                                     why="a blocking question in the workflow's spec; the "
                                                         "decision is the answer")})
            for x in ev.get("missing", []):
                crit.append({"criterion": f"Owner supplies {x}, called by {_nm(wf, u)}, or says what it does (not in the export)",
                             "source": "unexported calls",
                             "closes": G.by_decision(f"missing.{G.key_part(x)}",
                                                     f"{x} is called by {wf[u].get('name') or u} but is not in the export. "
                                                     "Add its export to sources/<estate>/ and re-run, or say what it does.",
                                                     ["exported", "described in the answer", "not needed"])})
        for u in spec_review:
            crit.append({"criterion": f"Owner reviews the spec for {_nm(wf, u)} (spec confidence medium)",
                         "source": "spec review" if u in reviewed else "spec", "closes": G.by_review(u, _sh(wf, u))})
        subs = [(u, e) for u in members for e in wf[u].get("entry_points") or []
                if e.get("kind") == "subscription" and not e.get("disabled")]
        if subs:
            crit.append({"criterion": "The trigger plan for this wave's subscriptions is agreed: vRA calls Kestra's "
                                      "webhook, or vRA is replaced and each event becomes a step (preflight Q9)",
                         "source": "preflight Q9", "closes": G.by_answer("Q9")})
        for u, e in subs:
            # replaced: a step of the vRA replacement wave instead; with a bridge, the webhook in between too
            if e.get("blocking") and (not replacing or front == TG.BRIDGE):
                crit.append({"criterion": f"A timeout and failure policy is decided for blocking subscription "
                                          f"'{e['name']}' ({e.get('event_topic')}"
                                          + (f", vRA waits {e['timeout_minutes']} min" if e.get("timeout_minutes") else "")
                                          + f") that runs {_nm(wf, u)}", "source": "entry points",
                             "closes": timeout_policy(f"timeout.{G.key_part(e['name'])}", e['name'],
                                                      e.get("timeout_minutes"), webhook=True)})
        for gid in sorted({lifecycle_of[u]["id"] for u in members if u in lifecycle_of}):
            g = next(x for x in assess["lifecycle_groups"] if x["id"] == gid)
            crit.append({"criterion": f"Deployment lifecycle group {gid} converts whole in this wave ("
                                      + ", ".join(_nm(wf, v) for v in g["members"]) + "): no deployment is left half in vRO and half "
                                      "in Kestra", "source": "entry points",
                         "closes": G.by_decision(f"lifecycle.{G.key_part(gid)}",
                                                 f"Do the workflows of deployment lifecycle group {gid} ("
                                                 + ", ".join(wf[v].get("name") or v for v in g["members"])
                                                 + ") convert and switch over together?",
                                                 ["together", "split"], recommended="together", blocks=["split"],
                                                 plain=f"Do the {len(g['members'])} workflows of lifecycle group {gid} "
                                                       "convert and switch over together?",
                                                 why=(f"{g['why']}. " if g.get("why") else "")
                                                     + "Split, a deployment made in one system is changed or "
                                                       "deleted by the other")})
        for path in sorted({p for u in members for p in (wf[u].get("state") or {}).get("writes") or []}):
            writers = [wf[u]["name"] for u in members if path in ((wf[u].get("state") or {}).get("writes") or [])]
            readers = [wf[u]["name"] for u in members if path in ((wf[u].get("state") or {}).get("reads") or [])
                       and wf[u]["name"] not in writers]
            crit.append({"criterion": f"Where the state kept in vRO configuration element {path} lives in Kestra is "
                                      "decided (written by " + ", ".join(writers)
                                      + (", read by " + ", ".join(readers) if readers else "") + ")",
                         "source": "target inventory",
                         "closes": G.by_decision(
                             f"state-store.{G.key_part(path)}",
                             f"{path} keeps state between runs (written by {', '.join(writers)}). Where does it live in "
                             "Kestra, and what moves the records vRO holds today?",
                             ["the KV store", "an external database (say which)", "other (say how)"],
                             recommended="the KV store",
                             plain=f"Where does the state kept in {path} live in Kestra?",
                             why="a configuration element written at run time is state; Kestra's KV store keeps it "
                                 "per namespace, and the records already in vRO move once, before the switch")})
        if replacing:
            typed = {u: _typed_inputs(u, wf, assess) for u in members if TG.started_by_vra(wf[u])}
            typed = {u: k for u, k in typed.items() if k}
            if typed:
                listing = "; ".join(f"{_nm(wf, u)}: {', '.join(k)}" for u, k in sorted(typed.items()))
                crit.append({"criterion": "Each flow takes typed inputs instead of vRA's event payload (" + listing
                                          + "); the payload parsing is not converted",
                             "source": "preflight Q9",
                             "closes": G.by_decision(
                                 f"typed-inputs.wave-{n}",
                                 "With vRA replaced, which typed inputs does each flow take? Proposed from the payload "
                                 "keys the workflow and its actions read: " + listing + ".",
                                 ["as proposed", "changed (say how)"], recommended="as proposed",
                                 plain="Which typed inputs does each flow take instead of vRA's event payload? "
                                       "(see the proposal on the vRA flow page)",
                                 why="the replacement flow calls these flows with values, not with an event payload"
                                     + ("; until then the bridge maps the payload to them" if front == TG.BRIDGE else ""))})
        names = I.action_names(assess)
        tls = [c for e in crit if isinstance(c := e.get("closes"), dict) and str(c.get("key") or "").startswith("tls.")]
        for u in members:
            ds = ((specs or {}).get(u) or {}).get("suspected_defects") or []
            for key, d in zip(I.defect_ids(_sh(wf, u), ds, names), ds, strict=True):
                if tls and key.rsplit(".", 1)[-1] == "tls":
                    # certificate checks are already a decision of this wave: asked once, there
                    for c in tls:
                        c.setdefault("also_answers", []).append(f"{wf[u].get('name') or u}: {d['what']}")
                    continue
                crit.append({"criterion": f"Suspected defect in {wf[u].get('name') or u}: {d['what']}. Preserve it or "
                                          "fix it in the conversion", "source": "spec",
                             "closes": G.by_decision(
                                 key,
                                 f"{wf[u].get('name') or u}: {d['what']} ({d.get('evidence')}). {d.get('question')}",
                                 ["keep", "fix"],
                                 why="the converter builds one or the other; the spec found it in the code",
                                 plain=defect_question(_short_name(wf, u), d["what"], _names(assess)))})
        if "Q4" in open_q:
            crit.append({"criterion": "Preflight Q4 answered: none of these workflows is off limits",
                         "source": "preflight Q4", "closes": G.by_answer("Q4")})
        # Pilot: the safest member to learn from. Convert with a high-confidence spec first,
        # then the fewest writes, then nothing it calls inside the wave. In a lifecycle group the member with the
        # fewest writes is a reader, which needs the others' records to run: the group's state writer goes
        # first (the earliest in the deployment's life), on an empty target.
        group_writers = sorted({v for u in members for v in (lifecycle_of.get(u) or {}).get("writers") or []
                                if v in inside})
        pilot = min(group_writers or members,
                    key=lambda u: (_life_pos(wf[u]) if group_writers else 0, triage[u]["outcome"] != "Convert",
                                   bool(triage[u]["evidence"].get("spec_review")), wf[u]["writes"], depth(u), u))
        pilot_with = [v for v in group_writers if v != pilot]
        rk = tier(members)
        prim = primary(members)
        waves.append({
            "wave": n, "clusters": clusters, "workflows": members, "n": len(members),
            "name": RISK_LABEL[rk] + (f": {', '.join(targets[t]['name'] for t in ts)}" if ts else ""),
            "primary_target": prim,
            "owner_input": owner, "spec_review": spec_review,
            "targets": ts, "writes_to": writes, "reads_from": reads_only,
            "risk": RISK_LABEL[rk], "depends_on": dep_waves,
            "scale": "S" if share < 0.15 else ("M" if share < 0.35 else "L"),
            "script_lines": lines - len(members),
            "entry_criteria": crit,
            "exit_criteria": [
                "Every member has a Kestra flow that runs to the same end state as the vRO workflow, against "
                "the non-production target where it has one",
                "Tests generated from each spec pass; near-duplicates pass their first of a kind's tests with their own values",
                _caller_exit(replacing, front, members, wf),
            ],
            "pilot": pilot, "pilot_with": pilot_with,
            "actions": _wave_actions(act_tri, members, wave_of_uid),
        })
        for u in members:
            wave_of_uid[u] = n

    from . import replace_vra
    waves += replace_vra.plan(assess, triage, answers, trigger_plan, wave_of_uid, len(waves) + 1, blocked)

    blocked_list = [{"uid": u, "cluster": wf[u]["cluster"], "outcome": triage[u]["outcome"], "blocked_on": why}
                    for u, why in sorted(blocked.items())]
    return {"waves": waves, "blocked": blocked_list,
            "grouping": {"waved": len(waved), "budget": budget, "clusters": len(by_cluster),
                         "natural_groups": natural, "merges": merges},
            "not_waved": {o: sorted(u for u, t in triage.items() if t["outcome"] == o)
                          for o in ("Wrapper", "Manual", "Needs a model", "Rebuild", "Retire")},
            "rules": __doc__.strip()}


def _nm(wf: dict, u: str) -> str:
    return (wf.get(u) or {}).get("name") or u


def _sh(wf: dict, u: str) -> str:
    return (wf.get(u) or {}).get("short") or G.key_part(u)


def _life_pos(w: dict) -> int:
    """The earliest point in a deployment's life at which something starts the workflow."""
    from . import triggers as TG
    return min((TG.position(e.get("event_topic"), e.get("condition"))[0] for e in w.get("entry_points") or []
                if e.get("kind") == "subscription"), default=99)


def _caller_exit(replacing: bool, front: str | None, members: list[str], wf: dict) -> str:
    """What starts a wave's flows until something else does. With vRA replaced the flows wait for the
    replacement wave: they run in non-production only, or behind vRA through a bridge."""
    from . import triggers as TG
    if not replacing or not any(TG.started_by_vra(wf[u]) for u in members):
        return "Callers outside this wave still reach it (a Subflow, or vRO calling Kestra's webhook while both run)"
    if front == TG.BRIDGE:
        return ("Until the vRA replacement wave, vRA's subscriptions run each flow through a bridge that posts the "
                "event payload to its webhook and maps it to the flow's typed inputs; the vRO workflows are switched "
                "off one subscription at a time")
    return ("Until the vRA replacement wave, the flows run in non-production only, started with typed inputs by a "
            "test or by hand; vRA's subscriptions keep running the vRO workflows in production")


def _typed_inputs(u: str, wf: dict, assess: dict) -> list[str]:
    """The vRA payload keys a workflow and the actions it reaches read: what its typed inputs replace."""
    keys = set(wf[u].get("event_payload_keys") or [])
    for a in assess.get("actions") or []:
        if u in _reaching(a, assess):
            keys |= set(a.get("event_payload_keys") or [])
    return sorted(keys)


_REACH: dict = {}


def _reaching(a: dict, assess: dict) -> set:
    key = id(assess)
    if _REACH.get("key") != key:
        from .triage import reaching_workflows
        _REACH.clear()
        _REACH.update({"key": key, "map": reaching_workflows(assess.get("actions") or [])})
    return _REACH["map"].get(a["key"]) or set()


def _names(assess: dict) -> set[str]:
    """Words that are names in this estate: those of its systems (target names and short names)."""
    out = set()
    for t in (assess.get("targets") or {}).values():
        for n in (t.get("name") or "", t.get("short_name") or ""):
            out |= {w for w in re.findall(r"[A-Za-z][\w-]*", n) if w[:1].isupper()}
    return out


def _short_name(wf: dict, u: str) -> str:
    """A workflow's name without the tag every workflow in the estate shares ('LB - ')."""
    names = [w.get("name") or "" for w in wf.values()]
    n = wf[u].get("name") or u
    m = re.match(r"^(.{1,12}?\s[-:]\s)", n)
    return n[len(m.group(1)):] if m and len(names) > 1 and all(x.startswith(m.group(1)) for x in names) else n


def defect_question(name: str, what: str, names: set[str] | frozenset = frozenset()) -> str:
    """A suspected defect as one plain question, the choice in it. `names`: words that are names in this
    estate (its systems' names), kept as written."""
    what = re.sub(r"\s*\([^)]*\)", "", str(what)).strip().rstrip(".")
    first = what.split(" ", 1)[0]
    # lower case after the colon, unless the first word is a name: capitals inside it (HTTPS, F5, IPv6), or
    # written with a capital in the middle of the sentence too ("on Avi")
    proper = any(ch.isupper() or ch.isdigit() for ch in first[1:]) or re.search(r"[a-z,;:] " + re.escape(first) + r"\b", what)
    if first[:1].isupper() and not proper and first not in names and (len(first) > 1 or first == "A"):
        what = what[0].lower() + what[1:]
    return f"Possible defect in {name}: {what}. Keep the current behavior, or fix it?"


def _endpoint(key: str, t: dict) -> dict:
    return G.by_decision(f"endpoint.{G.key_part(key)}",
                         f"Which {t['name']} do the flows call: its base URL or host, and the names of the Kestra "
                         "secrets that hold its credentials? Write them in the answer.",
                         plain=f"{t.get('short_name') or t['name']}: the URL of a non-production instance, and the "
                               "Kestra secrets that hold its credentials?",
                         why="the vRO side kept them in its own settings; a Kestra flow names them"
                             + (" (the plugin's tasks take host, username and password)" if t.get("reach") == "plugin"
                                else ""))


def _wave_actions(act_tri: dict | None, members: list[str], wave_of_uid: dict) -> dict:
    """The actions a wave carries: those a member reaches that no earlier wave's workflow reaches, by outcome."""
    out: dict[str, list[str]] = {}
    inside = set(members)
    for k, t in sorted((act_tri or {}).items()):
        us = set(t.get("workflow_uids") or [])
        if us & inside and not any(u in wave_of_uid for u in us):
            out.setdefault(t["outcome"], []).append(t.get("name") or k)
    return out


def _short_hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:6]


def _wave_exit(n: int) -> dict:
    return G.by_gate(n)


def edition() -> dict:
    return G.by_decision("edition", "Is Kestra Enterprise Edition available for this migration?",
                         ["available", "not available"], blocks=["not available"],
                         why="the plugin map marks a task this plan needs as Enterprise Edition only")


def timeout_policy(key: str, name: str, minutes, *, webhook: bool = False, path: str | None = None) -> dict:
    """The decision for a blocking step or subscription: its timeout and what a failure does. `path`: create or
    delete, the flow path the step is on (its failure is handled differently on each)."""
    wait = f"{minutes} min" if minutes else "for it"
    keep = f"keep vRA's timeout ({minutes} min)" if minutes else "keep vRA's timeout"
    if webhook:
        then = "a failure stops the deployment, as today"
    elif path == "delete":
        then = "a failure stops the deletion and the deployment stays for a retry, as today"
    else:
        then = "a failure stops the deployment and runs the create path's cleanup"
    return G.by_decision(key, f"'{name}' is blocking: vRA waited {wait} and a failure stopped the "
                              + ("deletion" if path == "delete" else "deployment")
                              + ". What timeout does the Kestra step get, and what does a failure do?",
                         recommended=f"{keep}; {then}", why="the behaviour vRA has today, carried across unchanged",
                         plain=f"Timeout and failure handling for blocking step '{name}'?")


def failure_policy(key: str, name: str, path: str | None = None) -> dict:
    """The decision for a step vRA did not wait for: does its failure fail the flow now?"""
    return G.by_decision(key, f"'{name}' was not blocking: vRA did not wait for it, so its failure did not stop the "
                              + ("deletion" if path == "delete" else "deployment")
                              + ". In the Kestra flow, does a failure here fail the flow?",
                         ["log and continue", "fail the flow"], recommended="log and continue",
                         why="the behaviour vRA has today: the request finished whatever this step did")


# Above this many lines of hand-written client code for one system, a vendor SDK in a script task is the
# smaller rewrite (the options study's argument for Avi: the client logic reads better as Python than as
# templated HTTP bodies). Below it, HTTP tasks are.
SDK_LINES = 1000


def _client_route(key: str, t: dict, assess: dict) -> dict:
    """The client-route decision for a target with no plugin: HTTP tasks, or the SDK for a large client."""
    row = next((r for r in (assess.get("plugin_coverage") or {}).get("rows") or [] if r.get("key") == key), None)
    lines = sum(a.get("lines") or 0 for a in assess.get("actions") or [] if key in (a.get("library_for") or []))
    n = len((row or {}).get("ops") or []) or len(t.get("operations") or [])
    rec = why = None
    if t.get("sdk_alternative") and lines > SDK_LINES:
        rec = "sdk"
        why = (f"{lines} lines of hand-written client code for it read better as one Python module than as "
               f"templated HTTP bodies: {t['sdk_alternative']}")
    elif row and row.get("status") == "http":
        rec = "http"
        why = (f"the plugin coverage page plans io.kestra.plugin.core.http.Request for its {n} operation(s) and "
               f"there is no Kestra plugin; the hand-written client is {lines} lines, small enough for HTTP tasks")
    return G.by_decision(f"client-route.{G.key_part(key)}",
                         f"How do flows call {t['name']}: HTTP tasks, the vendor SDK in a script task, or a new "
                         "plugin? (The instance and its secrets are the endpoint decision.)",
                         ["http", "sdk", "new plugin"], recommended=rec, why=why,
                         plain=f"How do flows call {t.get('short_name') or t['name']}: http tasks, sdk in a script, "
                               "or a new plugin?")


def _scc(nodes, deps):
    """Tarjan. Returns node -> component id (the smallest node name in it)."""
    index, low, stack, on, comp = {}, {}, [], set(), {}
    counter = [0]

    def strong(v):
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on.add(v)
        for w in deps.get(v, ()):
            if w not in index:
                strong(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            members = []
            while True:
                w = stack.pop()
                on.discard(w)
                members.append(w)
                if w == v:
                    break
            cid = min(members)
            for m in members:
                comp[m] = cid

    for v in nodes:
        if v not in index:
            strong(v)
    return comp
