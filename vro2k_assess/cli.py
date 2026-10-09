"""
The vro2k_assess command line: every stage up to and including the brief.

One estate is one run. Every stage needs --estate, reads only sources/<estate>/
and writes only stages/<estate>/.

    python plugin/scripts/vro.py run        --estate avi-sample   # everything below, then the pages: the one command
    python plugin/scripts/vro.py estates                          # list sources/<estate>/ folders
    python plugin/scripts/vro.py use-sample avi                   # samples/avi -> sources/avi-sample/
    python plugin/scripts/vro.py preflight  --estate avi-sample   # Stage 0: export checks, answers.md
    python plugin/scripts/vro.py sanitize   --estate avi-sample   # Stage 0: redacted copy + redaction-report.md
    python plugin/scripts/vro.py assess     --estate avi-sample   # Stage 1: code-only assessment
    python plugin/scripts/vro.py understand --estate avi-sample   # specs: reuse cached, generate missing (model)
    python plugin/scripts/vro.py brief      --estate avi-sample   # Stage 2: triage, waves, BRIEF.md
    python plugin/scripts/vro.py visuals    --estate avi-sample   # the pages: stages/<estate>/index.html, report/
    python plugin/scripts/vro.py status     --estate avi-sample   # where each stage is; rebuilds index.html
    python plugin/scripts/vro.py all        --estate avi-sample   # run without understand (never calls the spec model)
    python plugin/scripts/vro.py check-intake <path>              # reject a received export that carries secrets

Run it as `python -m vro2k_assess <command>`, or through `plugin/scripts/vro.py`, which adds
the commands of any extension package installed next to this one. This module never
imports an extension; `main(extensions=...)` is handed them by the caller. An extension
is a module with `register(subparsers) -> {command: handler}`, and a handler takes the
parsed arguments and returns an exit code. The Claude Code commands in plugin/commands/
call exactly the commands above.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vro2k_assess.stages import common as C  # noqa: E402

STAGES = ["preflight", "sanitize", "assess", "understand", "brief", "visuals", "status", "all", "run"]
COMMANDS = frozenset(["estates", "use-sample", "check-intake", *STAGES])


def use_sample(name: str, force: bool) -> int:
    src = os.path.join(C.SAMPLES_ROOT, name)
    if not os.path.isdir(src):
        have = ", ".join(sorted(n for n in os.listdir(C.SAMPLES_ROOT) if not n.startswith("."))) or "none"
        print(f"no sample {name!r} in samples/ (have: {have})", file=sys.stderr)
        return 2
    dst = os.path.join(C.SOURCES_ROOT, f"{name}-sample")
    if os.path.exists(dst):
        if not force:
            print(f"{C.rel(dst)}/ already exists; pass --force to replace it", file=sys.stderr)
            return 1
        shutil.rmtree(dst)
    shutil.copytree(src, dst, symlinks=False, ignore=lambda d, names: [n for n in names if d == src and n in SAMPLE_EXTRAS])
    seeded = seed_sample(src, os.path.join(C.STAGES_ROOT, f"{name}-sample"))
    print(f"[use-sample] copied {C.rel(src)}/ to {C.rel(dst)}/"
          + (f"; seeded stages/{name}-sample/ with {seeded} saved file(s) (specs, model-written names)" if seeded else "")
          + f"; run with --estate {name}-sample")
    return 0


# What a sample carries besides its export. Neither is copied to sources/: the export is the only input.
# model-output/ is what the model wrote for the sample once (specs, cluster summaries, domain names,
# walkthrough text), laid out like stages/<estate>/, so a run on the sample needs no billed call;
# WALKTHROUGH.md is a hand-written tour of the sample's results.
SAMPLE_EXTRAS = ("model-output", "WALKTHROUGH.md")


def seed_sample(src: str, stages_dir: str) -> int:
    """Copy a sample's saved model output and walkthrough into its stage folder, never over a file there."""
    n = 0
    pairs = []
    mo = os.path.join(src, "model-output")
    for d, _, files in os.walk(mo):
        for f in files:
            # model-output/ is laid out like the stage folders (2-understand/ ...), which now sit in data/
            pairs.append((os.path.join(d, f), os.path.join(stages_dir, "data", os.path.relpath(os.path.join(d, f), mo))))
    if os.path.exists(os.path.join(src, "WALKTHROUGH.md")):
        pairs.append((os.path.join(src, "WALKTHROUGH.md"), os.path.join(stages_dir, "WALKTHROUGH.md")))
    for a, b in pairs:
        if not os.path.exists(b):
            os.makedirs(os.path.dirname(b), exist_ok=True)
            shutil.copy2(a, b)
            n += 1
    return n


def check_intake(path: str) -> int:
    from vro2k_assess import intake, safety
    try:
        r = intake.check(path)
    except FileNotFoundError:
        print(f"check-intake: no such file or folder: {path}", file=sys.stderr)
        return 2
    except (safety.UnsafeInput, zipfile.BadZipFile) as e:
        print(f"check-intake: REJECTED: {path}: unsafe to open: {e}", file=sys.stderr)
        return 2
    print(intake.report(r))
    return 0 if r["accepted"] else 1


def run_stage(s: str) -> str:
    """Run one stage and print what it did; return a short summary of what it found (for `run`)."""
    if s == "preflight":
        from vro2k_assess.stages import preflight
        r = preflight.run()
        ep = r["export"]["entry_points"]
        res = ep["resolution"] or {"resolved": 0, "unresolved": []}
        q2 = next(q["status"] for q in r["answers"]["items"] if q["id"] == "Q2")
        print(f"[preflight] {r['estate']}: {r['scope']['workflows_in_export']} workflows in export; "
              f"{r['export']['vra_exports_read']} vRA export(s); "
              f"{len(ep['model']['subscriptions'])} subscriptions, {len(ep['model']['catalog_items'])} catalog items, "
              f"{res['resolved']} workflow references resolved, {len(res['unresolved'])} unresolved; "
              f"Q2 {q2}; {len(r['answers']['open'])} of {len(r['answers']['items'])} questions open; "
              f"wrote {C.rel(C.PRE)}/")
        return (f"{_n(r['scope']['workflows_in_export'], 'workflow')}, "
                f"{_n(len(ep['model']['subscriptions']), 'subscription')}, "
                f"{_n(len(ep['model']['catalog_items']), 'catalog item')}; {len(r['answers']['open'])} of "
                f"{len(r['answers']['items'])} questions open")
    elif s == "sanitize":
        from vro2k_assess import sanitize
        r = sanitize.run()
        print(f"[sanitize] {r['estate']}: {r['files_touched']} of {r['files_seen']} files changed, "
              f"{sum(r['counts'].values())} redactions, {len(r['unsure'])} to review; "
              f"wrote {C.rel(r['out_dir'])}/ and redaction-report.md")
        return (f"{r['files_touched']} of {r['files_seen']} files changed, {sum(r['counts'].values())} redactions, "
                f"{len(r['unsure'])} to review")
    elif s == "assess":
        from vro2k_assess.stages import assess
        r = assess.run()
        print(f"[assess] {r['estate']}: {r['counts']['workflows']} workflows, {r['counts']['actions']} actions, "
              f"{r['counts']['clusters']} clusters, {r['counts']['domains']} domains, "
              f"{r['counts']['targets']} targets, {r['counts']['entry_points']} entry points "
              f"({r['counts']['entry_point_workflows']} workflows started by one, "
              f"{r['counts']['entry_points_unresolved']} unresolved); wrote {C.rel(C.ASSESS)}/")
        # Preflight's questions are asked again from what Assess found (systems, plugins in the code)
        from vro2k_assess.stages import preflight
        q = preflight.refresh_questions()
        if q["refreshed"]:
            print(f"[preflight] questions rewritten from what Assess found ({', '.join(q['found'])}); answers kept")
        for x in q["contradictions"]:
            print(f"  question contradicts the findings: {x}")
        c = r["counts"]
        return (f"{_n(c['workflows'], 'workflow')}, {_n(c['actions'], 'action')}, {_n(c['targets'], 'target')}, "
                f"{_n(c['entry_points'], 'entry point')}")
    elif s == "understand":
        from vro2k_assess import understand
        r = understand.run_estate()
        kept = [k for k, v in r["errors"].items() if "since --no-model" in str(v)]
        have = len([f for f in os.listdir(C.SPEC_DIR) if f.endswith(".json") and f not in
                    ("PROVENANCE.json", "cluster-summaries.json")]) if os.path.isdir(C.SPEC_DIR) else 0
        print(f"[understand] {C.ESTATE}: {have} specs on record, {r['generated']} generated this run, "
              + (f"{len(kept)} stale and kept (--no-model), " if kept else "")
              + f"{len(r['errors']) - len(kept)} failed; "
              f"{r['summaries_generated']} cluster summaries generated; new model spend ${r['cost_usd']:.4f} over {r['billed_calls']} billed call(s)")
        for k, v in sorted(r.get("stale", {}).items()):
            if k not in r["errors"]:
                print(f"  regenerated: {k}: {v}")
        for k, v in r["errors"].items():
            print(f"  {'kept, stale' if 'since --no-model' in str(v) else 'failed'}: {k}: {v}")
        failed = len(r["errors"]) - len(kept)
        return (f"{have} specs, {r['generated']} written this run"
                + (f", {len(kept)} stale and kept" if kept else "") + (f", {failed} failed" if failed else ""))
    elif s == "brief":
        from vro2k_assess.stages import brief
        r = brief.run()
        print(f"[brief] {r['estate']}: {r['counts']}; recommendation: {r['recommendation']['verdict']}")
        waves = r["plan"]["waves"]
        return (f"{sum(r['counts'].values())} workflows triaged, {len(waves)} wave{'s' if len(waves) != 1 else ''}, "
                f"recommendation {r['recommendation']['verdict']}")
    elif s == "visuals":
        from vro2k_assess.stages import visuals
        r = visuals.run()
        print(f"[visuals] wrote {len(r)} pages: open {C.rel(C.INDEX)} (the rest are in {C.rel(C.PAGES)}/)")
        return f"{len(r)} pages written"
    elif s == "status":
        from vro2k_assess.stages import status
        print(status.run())
    elif s == "migrate-input":
        # an estate written before answers.yaml: carry its answers over, once the stages have said what they ask
        from vro2k_assess.stages import corrections, your_input
        your_input.set_renames(_old_keys())
        done = corrections.migrate() + your_input.migrate()
        if done:
            print(f"[migrate] carried what a person wrote into {C.rel(C.INPUT)}/ (the old files are kept, renamed "
                  "*.migrated, and read by nothing):")
            for d in done:
                print(f"  - {d}")
            from vro2k_assess.stages import brief
            brief.run()
        return f"{len(done)} item{'s' if len(done) != 1 else ''} carried over" if done else "nothing to carry over"
    elif s == "summary":
        from vro2k_assess.stages import status
        line = status.headline()
        print(line)
        return line
    return ""


def _old_keys() -> dict[str, str]:
    """Keys an estate used before 2026-10-01 (built on the workflow's full ID) -> today's (its short name)."""
    import json
    from vro2k_assess.stages import gates as G
    p = os.path.join(C.ASSESS, "assess.json")
    if not os.path.exists(p):
        return {}
    wf = json.load(open(p))["workflows"]
    out = {}
    for u, w in wf.items():
        out[u] = G.review_key(w["short"])
        for i in range(1, 40):
            out[f"defect.{G.key_part(u)}.{i}"] = f"defect.{w['short']}.{i}"
    return out


def main(argv: list[str] | None = None, extensions: tuple = ()) -> int:
    ap = argparse.ArgumentParser(prog="vro")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("estates", help="list the estates in sources/")
    us = sub.add_parser("use-sample", help="copy samples/<name>/ to sources/<name>-sample/ and seed its saved model output")
    us.add_argument("name")
    us.add_argument("--force", action="store_true", help="replace an existing sources/<name>-sample/")
    ci = sub.add_parser("check-intake", help="exit 1 with a masked report if a received export carries secrets")
    ci.add_argument("path", help="a .package, .workflow, ZIP or folder as received")
    for s in STAGES:
        p = sub.add_parser(s, help=("sanitize, Preflight, Assess, Understand (billed only for what changed) and Brief, "
                                    "then the pages; run it again after answering" if s == "run" else None))
        p.add_argument("--estate", required=True, help="the folder under sources/ to run on")
        p.add_argument("--no-model", action="store_true",
                       help="never call a model; use cached replies or fall back to code-made labels")
    handlers = {}
    for ext in extensions:
        for name, run in ext.register(sub).items():
            if name in COMMANDS or name in handlers:
                raise ValueError(f"extension {ext.__name__} redefines command {name!r}")
            handlers[name] = run
    args = ap.parse_args(argv)

    if args.cmd in handlers:
        return handlers[args.cmd](args)

    if args.cmd == "estates":
        print("\n".join(C.list_estates()) or "(none: put an export in sources/<estate>/ or run use-sample)")
        return 0
    if args.cmd == "use-sample":
        return use_sample(args.name, args.force)
    if args.cmd == "check-intake":
        return check_intake(args.path)

    if args.no_model:
        os.environ["VRO_NO_MODEL"] = "1"
    try:
        C.configure(args.estate)
    except C.EstateError as e:
        print(f"vro: {e}", file=sys.stderr)
        return 2
    old = C.old_layout_message(C.STAGES)
    if old:
        print(f"vro: {old}", file=sys.stderr)
        return 2
    moved = C.migrate_layout(C.STAGES)
    if moved:
        print(f"[migrate] {C.rel(C.STAGES)}/ was written with the 2c layout; it now has index.html, your-input/, "
              "report/ and data/:")
        for m in moved:
            print(f"  - {m}")
        old_ep = os.path.join(C.PRE, "entry-points.yaml")
        if os.path.exists(old_ep) and not os.path.exists(C.ENTRY_POINTS):
            C.ensure(C.INPUT)
            os.replace(old_ep, C.ENTRY_POINTS)
            print(f"  - moved entry-points.yaml to {C.rel(C.ENTRY_POINTS)}")
    if args.cmd == "run":
        stages = ["sanitize", "preflight", "assess", "understand", "brief", "migrate-input", "visuals", "summary"]
    elif args.cmd == "all":
        stages = ["sanitize", "preflight", "assess", "brief", "migrate-input", "visuals", "status"]
    else:
        stages = [args.cmd]
    if args.cmd == "run":
        return _run_with_progress(stages)
    for s in stages:
        run_stage(s)
    return 0


def _n(k: int, word: str) -> str:
    return f"{k} {word}{'' if k == 1 else 's'}"


RUN_NAMES = {"sanitize": "Sanitize", "preflight": "Preflight", "assess": "Assess", "understand": "Understand",
             "brief": "Brief", "migrate-input": "Your input", "visuals": "Pages", "summary": "Status"}


def _calls_since(n: int) -> tuple[int, dict]:
    """The model call log's length now, and what was logged after its first n lines: billed, cached, cost."""
    import json
    out = {"billed": 0, "cached": 0, "cost": 0.0}
    path = C.STAGE_CALL_LOG
    if not path or not os.path.exists(path):
        return 0, out
    with open(path) as f:
        lines = f.readlines()
    for line in lines[n:]:
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("event"):
            continue
        out["cached" if r.get("cached") else "billed"] += 1
        out["cost"] += r.get("cost_usd") or 0.0
    return len(lines), out


def _model_words(m: dict) -> str:
    if not m["billed"] and not m["cached"]:
        return "no model calls"
    if not m["billed"]:
        return f"{m['cached']} model call{'s' if m['cached'] != 1 else ''}, all from cache"
    return (f"{m['billed']} billed model call{'s' if m['billed'] != 1 else ''} (${m['cost']:.2f})"
            + (f", {m['cached']} from cache" if m["cached"] else ""))


STARTED_AFTER = 1.0          # seconds: a stage still running by then says it started


def _run_with_progress(stages: list[str]) -> int:
    """
    `run`: one line per stage as it goes (its name, what it found, its model calls, how long it took), the
    stage's own details indented under it, and a last line with the total time, whether the model results
    came from cache, and the page to open. A stage still running after STARTED_AFTER seconds first prints a
    line saying it started, and a long stage prints what it is doing as it goes (Understand: one line per spec
    written, with its time); a stage that finishes at once stays one line.
    """
    import contextlib
    import io
    import threading
    import time
    t_all = time.monotonic()
    total = {"billed": 0, "cached": 0, "cost": 0.0}
    width = max(len(v) for v in RUN_NAMES.values())
    out = sys.stdout
    lock = threading.Lock()
    for s in stages:
        before, _ = _calls_since(0)
        t0 = time.monotonic()
        buf = io.StringIO()
        name = f"{RUN_NAMES[s]:<{width}}"
        started = {"said": False}

        def say(msg: str | None = None, name=name, started=started) -> None:
            with lock:
                if not started["said"]:
                    started["said"] = True
                    print(f"{name}  started", file=out, flush=True)
                if msg:
                    print(f"{name}  {msg}", file=out, flush=True)

        timer = threading.Timer(STARTED_AFTER, say)
        timer.daemon = True
        if s != "summary":
            timer.start()
        C.set_progress(say)
        try:
            with contextlib.redirect_stdout(buf):
                if s == "brief":
                    # understand wrote or regenerated specs: Assess reads them (spec systems), so bring it up to date
                    from vro2k_assess.stages import assess
                    assess.load()
                found = run_stage(s)
        finally:
            timer.cancel()
            C.set_progress(None)
            with lock:
                started["said"] = True          # a timer firing now prints nothing after the stage's line
        _, m = _calls_since(before)
        for k in total:
            total[k] += m[k]
        if s == "summary":
            print(f"{RUN_NAMES[s]:<{width}}  {found}")
            continue
        details = [x.strip() for x in buf.getvalue().splitlines()
                   if x.strip() and not x.startswith(f"[{s}]") and x.strip() != found]
        print(f"{RUN_NAMES[s]:<{width}}  {found or 'done'}; {_model_words(m)}; {time.monotonic() - t0:.1f}s",
              flush=True)
        for x in details:
            print("    " + x)
    cache = ("model results all from cache" if not total["billed"] and total["cached"] else
             "no model calls" if not total["billed"] else
             f"{total['billed']} billed model call{'s' if total['billed'] != 1 else ''}, ${total['cost']:.2f}")
    print(f"Done in {time.monotonic() - t_all:.1f}s; {cache}. Open {C.rel(C.INDEX)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
