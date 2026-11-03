"""
/vro-status: where each stage is, what is stale, and what a person still has to do.

Read-only over the stage outputs, like Anthropic's /modernize-status, with one
exception the brief asks for: it regenerates the overview dashboard
(stages/<estate>/index.html) and the rendered reports, so the pages always match the files.
"""
from __future__ import annotations

import os

from . import common as C


def _p(*parts):
    return os.path.join(*parts)


def headline(brief: dict | None = None) -> str:
    """The status line the front page leads with, and `run` ends with."""
    brief = brief or (C.read_json(_p(C.BRIEF, "brief.json")) if os.path.exists(_p(C.BRIEF, "brief.json")) else None)
    if not brief:
        return f"Not run yet: `{C.run_command()}`"
    st = status_of(brief)
    return st["line"]


def status_of(brief: dict) -> dict:
    """{"line", "blocking", "open", "blocks": [wave names], "after": {wave name: [earlier waves]}, "start": [wave
    names]}: what still waits on a person. An earlier wave's exit (a gate) is not an open item: a wave held
    only by gates starts once those waves are done, and the line says so."""
    inp = brief.get("input") or {}
    keys_open = {it["key"] for g in ("confirm", "answer", "review") for f in ((inp.get(g) or {}).get("families") or {}).values()
                 for it in f["open"]}
    # asked when its wave is converted (answers.yaml, `waves:`): holds the wave's start, not the brief
    later = set(inp.get("later") or [])
    blocks, blocking, after, start, package = [], set(), {}, [], set()
    for w in brief["plan"]["waves"]:
        name = f"Wave {w['wave']}"
        crit = [e for e in w["entry_criteria"] if e.get("status") != "done"
                and (e.get("closes") or {}).get("kind") != "gate"
                and (e.get("closes") or {}).get("field") not in later]
        gates = [e["closes"]["wave"] for e in w["entry_criteria"] if e.get("status") != "done"
                 and (e.get("closes") or {}).get("kind") == "gate"]
        ks = {(e.get("closes") or {}).get("field") for e in crit}
        package |= {(e.get("closes") or {}).get("what") for e in crit if (e.get("closes") or {}).get("kind") == "package"}
        if crit:
            blocks.append(name)
            blocking |= {k for k in ks if k in keys_open}
            blocking |= {f"(outcome) {k}" for k in ks if k and k.startswith("outcomes.")}
        elif gates:
            after[name] = [f"Wave {d}" for d in sorted(set(gates))]
        else:
            start.append(name)
    n_open = len(keys_open)
    # a person counts the empty answer lines of answers.yaml (your_input.count), never the items behind them
    entries = inp.get("shown")
    n_hold = len(blocking)
    if entries is not None:
        n_open = sum(x.get("lines", 1) for x in entries)
        n_hold = sum(x.get("lines", 1) for x in entries if set(x["members"]) & blocking or x["id"].startswith("estate."))
    add = (f" and add {len(package)} missing piece{'s' if len(package) != 1 else ''} of code to the package"
           if package else "")
    wave_time = (f" {len(later)} more {'is' if len(later) == 1 else 'are'} asked when {'its' if len(later) == 1 else 'their'} "
                 "wave is converted (`waves:` in answers.yaml)." if later else "")
    order = " ".join(f"{w} starts once {_and(ds)} {'is' if len(ds) == 1 else 'are'} done." for w, ds in after.items())
    if (brief.get("recommendation") or {}).get("verdict") == "needs a model":
        line = ("Next step: run it again with a model. This run had none, so what each workflow does, its possible "
                "defects, and whether to convert or rebuild it are not judged yet.")
    elif blocking and n_hold:
        k = n_hold
        line = (f"The brief is ready. To start {_and(blocks)}: answer {k} question{'s' if k != 1 else ''} in "
                f"answers.yaml{add}, then run `{C.run_command()}` again"
                + (f"; {n_open - k} more {'blocks' if n_open - k == 1 else 'block'} nothing" if n_open > k else "") + ".")
    elif package:
        line = (f"Next step: add {len(package)} missing piece{'s' if len(package) != 1 else ''} of code to the package "
                f"(the brief lists how), then run `{C.run_command()}` again. {'They hold' if len(package) != 1 else 'It holds'} "
                f"{_and(blocks)}" + (f"; {n_open} open question{'s block' if n_open != 1 else ' blocks'} nothing" if n_open else "")
                + ".")
    elif blocks:
        line = f"Next step: nothing from you yet. {_and(blocks)} cannot start until the earlier waves have run."
    elif not brief["plan"]["waves"]:
        line = "Next step: no wave can start yet. The waves page says what each workflow waits on."
    else:
        from kestra_migration_assistant.config import HELP_URL, NEXT_STEP
        line = ("The brief is ready" + (f": {_and(start)} can start." if start else ".")
                + (f" {order}" if order else "")
                + (f" {n_open} open question{'s block' if n_open != 1 else ' blocks'} nothing." if n_open else "")
                + wave_time + f" Next step: {NEXT_STEP} ({HELP_URL}).")
    if wave_time and wave_time not in line and not line.startswith("Next step: run it again with a model"):
        line += wave_time
    return {"line": line, "blocking": sorted(blocking), "open": n_open, "holding": n_hold, "package": sorted(package), "blocks": blocks, "after": after,
            "start": start, "order": order, "later": len(later)}


def _and(xs: list[str]) -> str:
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def collect() -> dict:
    from kestra_migration_assistant.config import HELP_URL, NEXT_STEP
    pre_json = _p(C.PRE, "preflight.json")
    answers = C.ANSWERS
    readiness = _p(C.PRE, "readiness.md")
    assess_json = _p(C.ASSESS, "assess.json")
    a_over = C.CORRECTIONS
    brief_json = _p(C.BRIEF, "brief.json")
    brief_md = C.BRIEF_MD
    b_over = C.CORRECTIONS
    visuals = [_p(C.PAGES, x) for x in ("architecture.html", "explorer.html", "call-graph.html", "waves.html")]

    stale = []

    def older(out, *ins):
        if not os.path.exists(out):
            return
        newer = [i for i in ins if os.path.exists(i) and C.mtime(i) > C.mtime(out) + 1]
        for i in newer:
            stale.append(f"{C.rel(out)} is older than {C.rel(i)}")

    # a generated spec whose prompt would differ today describes other code or another prompt (no model call)
    stale_spec = {}
    try:
        from kestra_migration_assistant.vra import understand
        stale_spec = understand.stale_specs()
    except (OSError, KeyError, ValueError):
        pass
    for u, why in sorted(stale_spec.items()):
        stale.append(f"{C.rel(C.SPEC_DIR)}/{u}.json is stale: {why}; `{C.run_command()}` with a model (billed) regenerates it")
    older(assess_json, a_over, C.PLUGIN_MAP, pre_json)
    older(brief_md, assess_json, b_over, answers)
    for v in visuals:
        older(v, brief_json, assess_json)

    from . import preflight
    ans = preflight.load_answers()
    brief = C.read_json(brief_json) if os.path.exists(brief_json) else None
    assess = C.read_json(assess_json) if os.path.exists(assess_json) else None

    def state(outs, waiting):
        if not all(os.path.exists(o) for o in outs):
            return "not started"
        if any(s.startswith(tuple(C.rel(o) for o in outs)) for s in stale):
            return "stale"
        return "waiting on a person" if waiting else "done"

    pre = C.read_json(pre_json) if os.path.exists(pre_json) else None
    stages = []
    n_open = len(ans["open"])
    n_inputs = len((pre or {}).get("export", {}).get("sources", [])) or len(C.INPUTS)
    stages.append({
        "key": "preflight", "title": "0. Preflight", "state": state([pre_json, readiness], n_open > 0),
        "did": [f"Read {n_inputs} export input(s) in {C.rel(C.SOURCE_DIR)}/: formats, element counts, vRO version",
                _entry_line(ans),
                f"Asked {len(ans['items'])} questions the export cannot answer, in your-input/answers.yaml"],
        "next": [f"Answer them in your-input/answers.yaml ({n_open} of {len(ans['items'])} open)",
                 "Say how many vRO nodes and what calls these workflows from outside"],
        "links": [("readiness", "../data/0-preflight/readiness.md")]})
    if assess:
        c = assess["counts"]
        did = [f"Parsed {c['workflows_in_export']} workflows and {c['actions']} actions; {c['clusters']} clusters",
               f"Inventoried {c['targets']} target systems and how Kestra reaches each",
               f"Flagged {c['wrappers']} wrappers, {c['libraries']} client actions, {c['unexported_calls']} calls to things not exported",
               f"Grouped clusters into {c['domains']} domains (code), named by the model (1 call)"]
    else:
        did = ["not run yet"]
    stages.append({
        "key": "assess", "title": "1. Assess", "state": state([assess_json], False),
        "did": did,
        "next": ["Check the target inventory's Kestra reach for "
                 + (", ".join(t["name"] for k, t in assess["targets"].items() if t["reach"] == "http") or "each target")
                 + (" (HTTP, no plugin)" if any(t["reach"] == "http" for t in assess["targets"].values()) else "")
                 if assess else "Check the target inventory's Kestra reach",
                 "If the grouping is wrong, rename or move domains in your-input/corrections.yaml (optional, see INPUTS.md)"],
        "links": [("estate report", "../data/1-assess/estate-report.md"),
                  ("targets", "../data/1-assess/target-inventory.md"), ("domains", "../data/1-assess/domains.md")]})
    if brief:
        bc = brief["counts"]
        did = [f"Triaged {sum(bc.values())} workflows and {sum(brief['action_counts'].values())} actions into one outcome each",
               f"Planned {len(brief['plan']['waves'])} waves; {len(brief['plan']['blocked'])} workflows blocked, each with a reason",
               f"Recommended: {brief['recommendation']['verdict']}",
               f"Wrote {len(brief['walkthroughs']['flows'])} walkthroughs (paths by code, words by the model, 1 call)"]
    else:
        did = ["not run yet"]
    specs = [f for f in os.listdir(C.SPEC_DIR) if f.endswith(".json") and f not in ("PROVENANCE.json", "cluster-summaries.json")] \
        if os.path.isdir(C.SPEC_DIR) else []
    n_wf = len((assess or {}).get("workflows") or {})
    stages.append({
        "key": "understand", "title": "2. Understand",
        "state": "not started" if not specs else ("stale" if stale_spec else
                                                  ("done" if not n_wf or len(specs) >= n_wf else "waiting on a person")),
        "did": [f"{len(specs)} spec(s) in {C.rel(C.SPEC_DIR)}/" + (f" for {n_wf} workflows" if n_wf else ""),
                "A spec says what a workflow is for, from its code and every action it calls (model-written)"],
        "next": ([f"Run `{C.run_command()}` with a model (billed): {len(stale_spec)} spec(s) are "
                  "stale (listed under Stale)"] if stale_spec else []) +
                [f"Run `{C.run_command()}` with a model (billed) for the workflows without a spec"
                 if n_wf and len(specs) < n_wf else "Review the specs (the review items in your-input/answers.yaml)"],
        "links": []})
    stages.append({
        "key": "brief", "title": "3. Brief", "state": state([brief_md, brief_json], bool(brief and status_of(brief)["blocking"])),
        "did": did,
        "next": ["Answer what is open in your-input/answers.yaml",
                 f"{NEXT_STEP[:1].upper()}{NEXT_STEP[1:]}: {HELP_URL}"],
        "links": [("brief", "BRIEF.md"), ("triage.csv", "../data/3-brief/triage.csv"),
                  ("open questions", "../data/3-brief/open-questions.md")]})
    stages.append({
        "key": "convert", "title": "4. Convert (not built)", "state": "not started",
        "did": ["Nothing. This stage is not part of this work."],
        "next": ["Would read each wave's entry criteria as gates"],
        "links": [("wave plan", "waves.html")]})

    todo = []
    if n_open:
        todo.append(f"Answer {n_open} preflight question(s) in {C.rel(C.ANSWERS)} ({', '.join(ans['open'])})")
    if brief:
        # a candidate corrections.yaml already settled (Retire, or another outcome) is not asked again
        tri = brief.get("triage") or {}
        unsettled = [x for x in brief["retire_candidates"] if not (tri.get(x["uid"]) or {}).get("overridden")]
        if unsettled:
            todo.append(f"Confirm or reject {len(unsettled)} retire candidates ({C.rel(C.CORRECTIONS)})")
        c = brief["counts"]
        if c.get("Needs owner input"):
            todo.append(f"Answer the questions holding {c['Needs owner input']} Needs owner input workflow(s) "
                        "(their wave's entry criteria, on the waves page)")
        if c.get("Needs a model"):
            todo.append(f"Run again with a model: what {c['Needs a model']} workflow(s) do, their possible defects "
                        "and whether to convert or rebuild them need one")
        if c.get("Manual"):
            todo.append(f"Plan the hand rewrite of {c['Manual']} Manual workflow(s) (the waves page lists them)")
        nb = sum(1 for b in brief["plan"]["blocked"])
        if nb:
            todo.append(f"{nb} workflow(s) wait on that Manual work (the waves page lists them)")
        by_file: dict[str, int] = {}
        for g in (brief.get("gates") or {}).get("waves") or []:
            for f, k in g["open_by_file"].items():
                by_file[f] = by_file.get(f, 0) + k
        if by_file:
            todo.append(f"Close {sum(by_file.values())} open entry criteria, in "
                        + ", ".join(f"{f} ({k})" for f, k in sorted(by_file.items()))
                        + " (index.html lists what is asked)")
    return {"generated": C.now(), "stages": stages, "stale": stale, "todo": todo,
            "model_calls": C.call_summary()}


def run() -> str:
    st = collect()
    try:
        from . import visuals
        assess, brief, pre = visuals._load()
        visuals._vendor()
        from . import pages
        m = pages.link_map()
        n = len(pages.render_reports(m))
        visuals._set_names(assess)
        visuals._write("index.html", visuals.index(assess, brief, pre), m)
        regen = f"regenerated {C.rel(C.INDEX)} and {n} rendered report page(s) in {C.rel(C.PAGES)}/reports/"
    except FileNotFoundError as e:
        regen = f"did not regenerate the dashboard: {e}"
    L = [f"vRO to Kestra: status of estate {C.ESTATE}", ""]
    for s in st["stages"]:
        L.append(f"  {s['title']:<26} {s['state']}")
    L += ["", "Stale:" if st["stale"] else "Stale: nothing"]
    L += [f"  - {x}" for x in st["stale"]]
    L += ["", "A person still needs to:"] + [f"  - {x}" for x in st["todo"]]
    mc = st["model_calls"]
    L += ["", f"New model calls by these stages: {mc['billed']} billed, {mc['cached']} cached, ${mc['cost_usd']}",
          "", regen]
    L += [f"Status: {headline()}", f"Next: answer in {C.rel(C.ANSWERS)}, then `{C.run_command()}` again"]
    return "\n".join(L)


def _entry_line(ans: dict) -> str:
    """What the estate has on record about triggers and run history, from Assess when it has run."""
    path = os.path.join(C.ASSESS, "assess.json")
    if not os.path.exists(path):
        return "Entry points and run history: read by Assess"
    from . import facts
    fx = facts.estate(C.read_json(path), ans)
    return fx["triggers"] + " " + fx["history_sentence"]
