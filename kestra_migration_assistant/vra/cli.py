"""
`kestra-migrate vra`: the assessment of a vRA / vRO estate, every stage up to and including the brief.

One export folder is one estate, named after the folder. Every stage reads only that folder and writes only
to its output folder, `<export folder>/kestra-assessment/` unless `--out` names another; the readers of the
export skip the output folder, so the tool never reads its own output.

    kestra-migrate vra run <export folder> [--out <folder>] [--no-model]   # every stage, then the pages
    kestra-migrate vra status <export folder> [--out <folder>]             # where each stage is
    kestra-migrate vra check-intake <path>                                  # reject an export that carries secrets

For development, each stage also runs on its own (hidden from --help): preflight, sanitize, assess,
understand, brief, visuals, and all (run without understand).

This module never imports an extension; `main(extensions=...)` is handed them by the caller. An extension
is a module with `register(subparsers) -> {command: handler}`, and a handler takes the parsed arguments
and returns an exit code.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import zipfile

from kestra_migration_assistant.vra import outdir
from kestra_migration_assistant.vra.stages import common as C

COMMANDS_SHOWN = ["run", "status", "check-intake"]
STAGES = ["preflight", "sanitize", "assess", "interview", "understand", "brief", "visuals", "status", "all", "run"]
COMMANDS = frozenset(["check-intake", *STAGES])


def check_intake(path: str) -> int:
    from kestra_migration_assistant.vra import intake, safety
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
        from kestra_migration_assistant.vra.stages import preflight
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
        from kestra_migration_assistant.vra import sanitize
        r = sanitize.run()
        print(f"[sanitize] {r['estate']}: {r['files_touched']} of {r['files_seen']} files changed, "
              f"{sum(r['counts'].values())} redactions, {len(r['unsure'])} to review; "
              f"wrote {C.rel(r['out_dir'])}/ and redaction-report.md")
        return (f"{r['files_touched']} of {r['files_seen']} files changed, {sum(r['counts'].values())} redactions, "
                f"{len(r['unsure'])} to review")
    elif s == "assess":
        from kestra_migration_assistant.vra.stages import assess
        r = assess.run()
        print(f"[assess] {r['estate']}: {r['counts']['workflows']} workflows, {r['counts']['actions']} actions, "
              f"{r['counts']['clusters']} clusters, {r['counts']['domains']} domains, "
              f"{r['counts']['targets']} targets, {r['counts']['entry_points']} entry points "
              f"({r['counts']['entry_point_workflows']} workflows started by one, "
              f"{r['counts']['entry_points_unresolved']} unresolved); wrote {C.rel(C.ASSESS)}/")
        # Preflight's questions are asked again from what Assess found (systems, plugins in the code)
        from kestra_migration_assistant.vra.stages import preflight
        q = preflight.refresh_questions()
        if q["refreshed"]:
            print(f"[preflight] questions rewritten from what Assess found ({', '.join(q['found'])}); answers kept")
        for x in q["contradictions"]:
            print(f"  question contradicts the findings: {x}")
        c = r["counts"]
        return (f"{_n(c['workflows'], 'workflow')}, {_n(c['actions'], 'action')}, {_n(c['targets'], 'target')}, "
                f"{_n(c['entry_points'], 'entry point')}")
    elif s == "interview":
        from kestra_migration_assistant.vra.stages import interview
        from kestra_migration_assistant.vra.stages import your_input as Y
        pat = interview.register()
        loaded = Y.load()
        est = [it for it in Y.items() if it.get("family") == "estate"]
        n_open = sum(1 for it in est if interview.answer_of(it, loaded["entries"].get(it["key"])) is None)
        n_as = sum(1 for it in Y.items() if it.get("family") == "assume")
        miss = len(pat.get("missing") or [])
        print(f"[interview] {C.ESTATE}: {len(est)} estate questions ({n_open} open), {n_as} assumptions, "
              f"{miss} missing pieces to add to the package")
        return (f"{_n(len(est), 'estate question')}, {n_open} open; {_n(n_as, 'assumption')}"
                + (f"; {_n(miss, 'piece')} of code to add to the package" if miss else ""))
    elif s == "understand":
        from kestra_migration_assistant.vra import understand
        r = understand.run_estate()
        kept = [k for k, v in r["errors"].items() if "since --no-model" in str(v)]
        unread = [k for k, v in r["errors"].items() if str(v).startswith("needs a model")]
        have = len([f for f in os.listdir(C.SPEC_DIR) if f.endswith(".json") and f not in
                    ("PROVENANCE.json", "cluster-summaries.json")]) if os.path.isdir(C.SPEC_DIR) else 0
        print(f"[understand] {C.ESTATE}: {have} specs on record, {r['generated']} generated this run, "
              + (f"{len(kept)} stale and kept (--no-model), " if kept else "")
              + (f"{len(unread)} need a model, " if unread else "")
              + f"{len(r['errors']) - len(kept) - len(unread)} failed; "
              f"{r['summaries_generated']} cluster summaries generated; new model spend ${r['cost_usd']:.4f} over {r['billed_calls']} billed call(s)")
        for k, v in sorted(r.get("stale", {}).items()):
            if k not in r["errors"]:
                print(f"  regenerated: {k}: {v}")
        for k, v in r["errors"].items():
            if k not in unread:
                print(f"  {'kept, stale' if 'since --no-model' in str(v) else 'failed'}: {k}: {v}")
        failed = len(r["errors"]) - len(kept) - len(unread)
        if unread and not have:
            return f"no specs: what each of the {len(unread)} workflows does needs a model"
        return (f"{have} specs, {r['generated']} written this run"
                + (f", {len(kept)} stale and kept" if kept else "")
                + (f", {len(unread)} need a model" if unread else "") + (f", {failed} failed" if failed else ""))
    elif s == "brief":
        from kestra_migration_assistant.vra.stages import brief
        r = brief.run()
        print(f"[brief] {r['estate']}: {r['counts']}; recommendation: {r['recommendation']['verdict']}")
        waves = r["plan"]["waves"]
        return (f"{sum(r['counts'].values())} workflows triaged, {len(waves)} wave{'s' if len(waves) != 1 else ''}, "
                f"recommendation {r['recommendation']['verdict']}")
    elif s == "visuals":
        from kestra_migration_assistant.vra.stages import visuals
        pages = visuals.run()
        print(f"[visuals] wrote {len(pages)} pages: open {C.rel(C.INDEX)} (the rest are in {C.rel(C.PAGES)}/)")
        return f"{len(pages)} pages written"
    elif s == "status":
        from kestra_migration_assistant.vra.stages import status
        print(status.run())
    elif s == "migrate-input":
        # an estate written before answers.yaml: carry its answers over, once the stages have said what they ask
        from kestra_migration_assistant.vra.stages import corrections, your_input
        your_input.set_renames(_old_keys())
        done = corrections.migrate() + your_input.migrate()
        if done:
            print(f"[migrate] carried what a person wrote into {C.rel(C.INPUT)}/ (the old files are kept, renamed "
                  "*.migrated, and read by nothing):")
            for d in done:
                print(f"  - {d}")
            from kestra_migration_assistant.vra.stages import brief
            brief.run()
        return f"{len(done)} item{'s' if len(done) != 1 else ''} carried over" if done else "nothing to carry over"
    elif s == "summary":
        from kestra_migration_assistant.vra.stages import status
        line = status.headline()
        print(line)
        return line
    return ""


def _old_keys() -> dict[str, str]:
    """Keys an estate used before 2026-10-01 (built on the workflow's full ID) -> today's (its short name)."""
    import json

    from kestra_migration_assistant.vra.stages import gates as G
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


HELP = {
    "run": "assess an export folder: Sanitize, Preflight, Assess, Understand (billed only for what changed) and "
           "Brief, then the pages. Run it again after answering: it reuses the model cache and keeps every answer",
    "status": "show where each stage of an export folder's assessment is, and what to do next",
    "check-intake": "exit 1 with a masked report if a received export carries anything secret-shaped",
}


def _export_args(p: argparse.ArgumentParser, model: bool = True) -> None:
    p.add_argument("export", metavar="EXPORT_FOLDER",
                   help="the folder that holds the export: a .package, a Build Tools source tree or .workflow "
                        "files, and the vRA export if there is one. The estate is named after it")
    p.add_argument("--out", metavar="FOLDER",
                   help=f"where to write everything (default: EXPORT_FOLDER/{outdir.OUTPUT_DIRNAME}/)")
    if model:
        p.add_argument("--no-model", action="store_true",
                       help="never call a model; use cached replies, and say what needs a model")
        p.add_argument("--provider", choices=["claude-code", "anthropic", "openai-compatible"],
                       help="how to connect to the model (default: the config file, else your Claude Code login, "
                            "else ANTHROPIC_API_KEY)")
        p.add_argument("--model", help="the model every call uses (default: the config file, else claude-opus-5-5)")
        p.add_argument("--base-url", help="openai-compatible only: the server's address, ending in /v1")
        p.add_argument("--yes", action="store_true",
                       help="go ahead without asking when the cost estimate is above max_cost_usd (default $20)")


def main(argv: list[str] | None = None, extensions: tuple = (), prog: str = "kestra-migrate vra") -> int:
    from kestra_migration_assistant import config as K
    note = K.CONTAINER_HELP if K.in_container() else None
    ap = argparse.ArgumentParser(
        prog=prog, description="Kestra Migration Assistant for vRA: assess a vRA / vRO estate for migration to Kestra, "
                               "from its export, on this machine.", epilog=note)
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="{" + ",".join(COMMANDS_SHOWN) + "}")
    _export_args(sub.add_parser("run", help=HELP["run"], description=HELP["run"], epilog=note))
    _export_args(sub.add_parser("status", help=HELP["status"], description=HELP["status"]), model=False)
    ci = sub.add_parser("check-intake", help=HELP["check-intake"], description=HELP["check-intake"])
    ci.add_argument("path", help="a .package, .workflow, ZIP or folder as received")
    # For development: each stage on its own. Not listed in --help (a parser added without
    # help= has no line in the command list).
    for s in STAGES:
        if s not in ("run", "status"):
            _export_args(sub.add_parser(s))
    handlers = {}
    for ext in extensions:
        for name, run in ext.register(sub).items():
            if name in COMMANDS or name in handlers:
                raise ValueError(f"extension {ext.__name__} redefines command {name!r}")
            handlers[name] = run
    args = ap.parse_args(argv)

    if args.cmd in handlers:
        return handlers[args.cmd](args)

    if args.cmd == "check-intake":
        return check_intake(args.path)

    if getattr(args, "no_model", False):
        os.environ["VRO_NO_MODEL"] = "1"
    elif hasattr(args, "no_model") and args.cmd in ("run", "understand", "brief", "all", "assess"):
        if not set_up_model(args, prog):
            return 2
    try:
        C.configure_export(args.export, args.out)
    except C.EstateError as e:
        print(f"{prog}: {e}", file=sys.stderr)
        return 2
    old = C.old_layout_message(C.STAGES)
    if old:
        print(f"{prog}: {old}", file=sys.stderr)
        return 2
    try:
        C.ensure(C.STAGES)
    except OSError as e:
        print(f"{prog}: cannot write the results to {C.STAGES}: {e.strerror}. Pass --out with a folder you can "
              "write to", file=sys.stderr)
        return 2
    moved = C.migrate_layout(C.STAGES)
    if moved:
        print(f"[migrate] {C.rel(C.STAGES)}/ was written with an earlier version's layout; it now has index.html, your-input/, "
              "report/ and data/:")
        for m in moved:
            print(f"  - {m}")
        old_ep = os.path.join(C.PRE, "entry-points.yaml")
        if os.path.exists(old_ep) and not os.path.exists(C.ENTRY_POINTS):
            C.ensure(C.INPUT)
            os.replace(old_ep, C.ENTRY_POINTS)
            print(f"  - moved entry-points.yaml to {C.rel(C.ENTRY_POINTS)}")
    if args.cmd == "run":
        stages = ["sanitize", "preflight", "assess", "interview", "understand", "brief", "migrate-input", "visuals",
                  "summary"]
    elif args.cmd == "all":
        stages = ["sanitize", "preflight", "assess", "interview", "brief", "migrate-input", "visuals", "status"]
    else:
        stages = [args.cmd]
    from kestra_migration_assistant.vra import model
    if model.SETTINGS is not None and model.SETTINGS.provider and not os.environ.get("VRO_NO_MODEL"):
        model.GATE = cost_gate(yes=bool(getattr(args, "yes", False)))
    try:
        if args.cmd == "run":
            return _run_with_progress(stages)
        for s in stages:
            run_stage(s)
        return 0
    except model.CostNotApproved as e:
        print(str(e), file=sys.stderr)
        return 3
    finally:
        model.GATE = None


def cost_gate(yes: bool = False, confirm=None, out=None):
    """The estimate before the run's first billed call (model.Gate): said on the terminal, and above
    max_cost_usd asked in it. With no terminal to ask in, a run above it stops unless --yes is given."""
    from kestra_migration_assistant.vra import model
    from kestra_migration_assistant.vra.stages import estimate
    out = out or sys.stdout
    width = max(len(v) for v in RUN_NAMES.values())

    def say(msg: str) -> None:
        print(f"{'Cost':<{width}}  {msg}", file=out, flush=True)

    def ask_terminal(question: str) -> bool:
        print(f"{'Cost':<{width}}  {question}", end="", file=out, flush=True)
        return sys.stdin.readline().strip().lower() in ("y", "yes")

    if confirm is None and sys.stdin is not None and sys.stdin.isatty():
        confirm = ask_terminal
    return model.Gate(estimate.planned, say, yes=yes, confirm=confirm)


def set_up_model(args: argparse.Namespace, prog: str) -> bool:
    """Resolve which model this run calls (flags, environment, config file, defaults) and say so. With none set
    up, the run goes ahead without a model and says how to add one. False on a configuration error."""
    from kestra_migration_assistant import config as K
    from kestra_migration_assistant.vra import model
    try:
        s = K.resolve({"provider": args.provider, "model": args.model, "base_url": args.base_url})
    except K.ConfigError as e:
        print(f"{prog}: {e}", file=sys.stderr)
        return False
    if s.provider == "claude-code" and not shutil.which("claude"):
        print(f"{prog}: provider claude-code ({s.source.get('provider')}) needs the `claude` command, and it is not "
              "installed. Install Claude Code, or choose another provider (--provider, or the config file: "
              f"{s.path})", file=sys.stderr)
        return False
    model.configure(s)
    if not s.provider:
        os.environ["VRO_NO_MODEL"] = "1"
        print(s.why)
    else:
        print(f"Model: {s.describe()}")
    return True


def shown(path: str) -> str:
    """A path for the terminal: relative to the current folder when it is under it, else absolute."""
    a = os.path.abspath(path)
    try:
        r = os.path.relpath(a)
    except ValueError:                      # Windows: another drive
        return a
    return a if r.startswith("..") else r


def _n(k: int, word: str) -> str:
    return f"{k} {word}{'' if k == 1 else 's'}"


RUN_NAMES = {"sanitize": "Sanitize", "preflight": "Preflight", "assess": "Assess", "interview": "Estate",
             "understand": "Understand",
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
                    from kestra_migration_assistant.vra.stages import assess
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
    print(f"Done in {time.monotonic() - t_all:.1f}s; {cache}. Open {shown(C.INDEX)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
