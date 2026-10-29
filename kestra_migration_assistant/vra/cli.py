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
import time
import zipfile
from typing import Any

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
        p.add_argument("--batch", action="store_true",
                       help="ask nothing in the terminal: read the answers in answers.yaml and write the questions "
                            "left there (the default when no terminal is attached)")


def is_terminal() -> bool:
    """A person at a terminal: both standard input and output are one."""
    return bool(sys.stdin and sys.stdin.isatty() and sys.stdout and sys.stdout.isatty())


def main(argv: list[str] | None = None, extensions: tuple = (), prog: str = "kestra-migrate vra",
         interactive: bool = False, exit_now: bool = False) -> int:
    """The `kestra-migrate vra` command line. `interactive`: the command was typed by a person (the console script
    says so); only then, and only in a terminal and without --batch, does `run` ask its questions there. A program
    calling this function never waits for input. `exit_now`: on Ctrl+C, end the process at once once everything
    finished is written (the console script; a program calling this function gets 130 back instead)."""
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

    asking = bool(interactive and args.cmd == "run" and not getattr(args, "batch", False) and is_terminal())
    if asking:
        from kestra_migration_assistant import banner
        banner.show()
    from kestra_migration_assistant.vra import model
    model.STOP.clear()
    model.LIMIT["message"] = None
    if asking:                          # the model chosen for this estate is kept in its output folder
        try:
            C.configure_export(args.export, args.out)
        except C.EstateError as e:
            print(f"{prog}: {e}", file=sys.stderr)
            return 2
    if getattr(args, "no_model", False):
        os.environ["VRO_NO_MODEL"] = "1"
    elif hasattr(args, "no_model") and args.cmd in ("run", "understand", "brief", "all", "assess"):
        if not set_up_model(args, prog, asking=asking):
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
    term = None
    if asking:
        from kestra_migration_assistant.vra import interactive as I
        term = I.Terminal()
    if model.SETTINGS is not None and model.SETTINGS.provider and not os.environ.get("VRO_NO_MODEL"):
        model.GATE = cost_gate(yes=bool(getattr(args, "yes", False)), always=asking)
    try:
        if asking:
            return _run_interactive(term)
        if args.cmd == "run":
            return _run_with_progress(stages)
        for s in stages:
            run_stage(s)
        return 0
    except model.CostNotApproved as e:
        PROGRESS.clear()
        print(str(e), file=sys.stderr)
        return 3
    except (KeyboardInterrupt, model.Stopped):
        model.stop()                    # the calls in flight end now; nothing new starts
        PROGRESS.stop()
        limit = model.LIMIT["message"]  # the model's limit ran out (any thread may have raised first)
        print(f"\n{limit or stopped_message()}", flush=True)
        code = LIMIT_EXIT if limit else 130
        if exit_now:
            sys.stderr.flush()
            os._exit(code)              # a call still in flight (an HTTP request) would hold the process otherwise
        return code
    finally:
        model.GATE = None


LIMIT_EXIT = 75                 # EX_TEMPFAIL: the model's limit ran out; the same command works once it resets


def cost_gate(yes: bool = False, confirm=None, out=None, always: bool = False):
    """The estimate before the run's first billed call (model.Gate): said on the terminal, and above
    max_cost_usd asked in it. With no terminal to ask in, a run above it stops unless --yes is given. `always`
    (the interactive run): asked whatever the estimate, in plain words, Enter to go on."""
    from kestra_migration_assistant.vra import model
    from kestra_migration_assistant.vra.stages import estimate
    out = out or sys.stdout
    width = max(len(v) for v in RUN_NAMES.values())

    def say(msg: str) -> None:
        if always:
            return                                  # the question says it
        PROGRESS.clear()
        print(f"{'Cost':<{width}}  {msg}", file=out, flush=True)

    def ask_terminal(question: str) -> bool:
        PROGRESS.clear()
        if always:
            from kestra_migration_assistant.vra import interactive
            print(f"\n{question}", end="", file=out, flush=True)
            t0 = time.monotonic()
            try:
                return sys.stdin.readline().strip().lower() in ("", "y", "yes")
            finally:
                interactive.WAITED["seconds"] += time.monotonic() - t0
        print(f"{'Cost':<{width}}  {question}", end="", file=out, flush=True)
        return sys.stdin.readline().strip().lower() in ("y", "yes")

    if confirm is None and sys.stdin is not None and (always or sys.stdin.isatty()):
        confirm = ask_terminal
    return model.Gate(estimate.planned, say, yes=yes, confirm=confirm, always=always)


def set_up_model(args: argparse.Namespace, prog: str, asking: bool = False) -> bool:
    """Resolve which model this run calls (flags, environment, config file, defaults) and say so. With none set
    up, the run goes ahead without a model and says how to add one. False on a configuration error. `asking` (the
    interactive run): a model chosen in the terminal for this estate comes after the flags, and the terminal says
    which model it is and lets the person change it, instead of a line here."""
    from kestra_migration_assistant import config as K
    from kestra_migration_assistant.vra import model
    flags = {"provider": args.provider, "model": args.model, "base_url": args.base_url}
    if asking and not args.provider and not args.model:
        from kestra_migration_assistant.vra import interactive
        flags.update(interactive.remembered_model())
    try:
        s = K.resolve(flags)
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
    elif not asking:
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


class ProgressLine:
    """One line that says which step a long run is on and how far it is ("Analyzing 46 workflows: 19 done, 4m 2s"),
    rewritten in place on a terminal, with the time redrawn every second so the line never looks stuck; elsewhere
    written once at the start and once at the end. A step that starts inside another (sorting, inside the brief)
    gives the line back to it when it ends. What each step did in detail goes to the run's log file instead (RunLog)."""

    WHAT = {"workflows": "Analyzing {total} workflows: {done} done",
            "groups": "Summarizing {total} groups: {done} done",
            "questions": "Sorting what the analysis found: {done} of {total} parts done",
            "brief": "Preparing the brief"}
    EVERY = 1.0                      # seconds between two redraws of the time

    def __init__(self) -> None:
        import threading
        self.out: Any = None
        self.active = False
        self.lock = threading.RLock()
        self.cur: dict | None = None             # {"what", "text", "t0"}
        self.outer: list[dict] = []              # the steps this one started inside
        self.beat: Any = None

    def _tty(self) -> bool:
        return bool(getattr(self.out or sys.stdout, "isatty", lambda: False)())

    def _draw(self, end: str = "") -> None:
        assert self.cur is not None
        import time
        t = _minutes(time.monotonic() - self.cur["t0"])
        print(f"\r{self.cur['text']}, {t}{end}\033[K" if self._tty() else f"{self.cur['text']}, {t}{end}",
              end="" if not end else "\n", file=self.out or sys.stdout, flush=True)

    def tick(self, what: str, done: int, total: int) -> None:
        import time
        with self.lock:
            if self.cur is None or self.cur["what"] != what:
                if self.cur is not None and self.active:
                    self.outer.append(self.cur)
                self.cur = {"what": what, "t0": time.monotonic()}
            self.cur["text"] = self.WHAT.get(what, what + ": {done} of {total} done").format(done=done, total=total)
            if not self._tty():
                if done == 0:
                    print(f"{self.cur['text']}.", file=self.out or sys.stdout, flush=True)
                elif done >= total:
                    self._draw(".")
                if done >= total:
                    self.cur = self.outer.pop() if self.outer else None
                return
            if done < total:
                self.active = True
                self._draw()
                self._keep_alive()
                return
            self._draw(".")
            self.active = False
            self.cur = self.outer.pop() if self.outer else None
            if self.cur is not None:              # back to the step this one ran inside
                self.active = True
                self._draw()

    def _keep_alive(self) -> None:
        import threading
        if self.beat is not None and self.beat.is_alive():
            return

        def beat() -> None:
            import time
            while True:
                time.sleep(self.EVERY)
                with self.lock:
                    if not self.active or self.cur is None:
                        return
                    self._draw()
        self.beat = threading.Thread(target=beat, name="progress-line", daemon=True)
        self.beat.start()

    def clear(self) -> None:
        """End the line in progress, so what is printed next starts on a line of its own (the next tick draws it
        again)."""
        with self.lock:
            if self.active:
                print("", file=self.out or sys.stdout, flush=True)
                self.active = False

    def stop(self) -> None:
        """The run stopped: end the line and forget every step."""
        with self.lock:
            self.clear()
            self.cur, self.outer = None, []


PROGRESS = ProgressLine()


class RunLog:
    """data/run.log: what each step did in detail (each spec written, what was regenerated and why, each group
    summary). The terminal shows one line per step instead."""

    def __init__(self) -> None:
        import datetime
        C.ensure(C.DATA)
        self.path = os.path.join(C.DATA, "run.log")
        self.f = open(self.path, "a", encoding="utf-8")
        self.f.write(f"\n== {datetime.datetime.now().isoformat(timespec='seconds')} {C.run_command()}\n")
        self.f.flush()

    def write(self, text: str) -> int:
        self.f.write(text)
        self.f.flush()
        return len(text)

    def flush(self) -> None:
        self.f.flush()

    def line(self, text: str) -> None:
        self.write(text.rstrip("\n") + "\n")

    def close(self) -> None:
        self.f.close()


ANALYZING = {"on": False}


def stopped_message() -> str:
    """What Ctrl+C kept: the workflows analyzed so far when it stopped the analysis, else the answers."""
    import json
    idx = os.path.join(C.WORK, "index.json") if C.WORK else ""
    if ANALYZING["on"] and idx and os.path.exists(idx):
        uids = [w["uid"] for w in json.load(open(idx))["workflows"]]
        have = sum(1 for u in uids if os.path.exists(os.path.join(C.SPEC_DIR, f"{u}.json")))
        return f"Stopped. {have} of {len(uids)} workflows analyzed and kept. Run again to continue."
    return f"Stopped. Every answer given so far is saved in {C.rel(C.ANSWERS)}. Run again to continue."


def _run_stage_logged(s: str, log: RunLog) -> str:
    """One stage, with everything it prints going to the run's log."""
    import contextlib
    C.set_progress(log.line)
    try:
        with contextlib.redirect_stdout(log):
            log.line(f"[{s}] started")
            found = run_stage(s)
            log.line(f"[{s}] {found}")
            return found
    finally:
        C.set_progress(None)


def _minutes(sec: float) -> str:
    return f"{int(sec // 60)}m {int(sec % 60)}s" if sec >= 60 else f"{sec:.0f}s"


def _write_brief(log: RunLog) -> None:
    """The brief and its pages, one progress line ("Preparing the brief"): Assess first when the specs changed under
    it, then the brief, what an earlier version's files carry over, and the pages."""
    import contextlib

    from kestra_migration_assistant.vra.stages import assess
    C.tick("brief", 0, 1)
    try:
        with contextlib.redirect_stdout(log):
            assess.load()
        for s in ("brief", "migrate-input", "visuals"):
            _run_stage_logged(s, log)
    finally:
        C.tick("brief", 1, 1)
        PROGRESS.clear()


def _run_interactive(term) -> int:
    """`run` typed in a terminal. The first time: the model, the export read, the first screen, the interview, the
    analysis (asked for before anything is billed, one progress line), then the brief at once, with every question
    that has a suggestion assumed and listed in it. It ends with a short summary, the brief's link, an offer to open
    it, and one line: run the same command again to sharpen it. Run again on an estate that has a brief and nothing
    to ask before the analysis, it re-runs only what changed, asks the few questions that sharpen the brief most (q
    stops), writes it again and ends the same way. "Done in" is the tool's own working time, not the time spent
    answering. What each step did in detail is in data/run.log."""
    from kestra_migration_assistant.vra import interactive as I
    from kestra_migration_assistant.vra import model
    from kestra_migration_assistant.vra.stages import assess, estimate
    from kestra_migration_assistant.vra.stages import your_input as Y
    t_all = time.monotonic()
    I.WAITED["seconds"] = 0.0
    use_model = not os.environ.get("VRO_NO_MODEL")
    log = RunLog()
    calls_before, _ = _calls_since(0)
    PROGRESS.out = term.out
    C.set_tick(PROGRESS.tick)
    try:
        if use_model and model.SETTINGS is not None:
            I.choose_model(term, model.SETTINGS)
        elif not use_model:
            term.say("No model: this run reads the code only, and says what needs a model.")
        assess.NAME_DOMAINS = False                 # nothing is billed before the person says so
        try:
            for s in ("sanitize", "preflight", "assess", "interview"):
                _run_stage_logged(s, log)
        finally:
            assess.NAME_DOMAINS = True
        qs = I.interview_questions(use_model)
        again = I.has_brief() and not qs            # run again: no first screen, the questions that sharpen it
        if not again:
            I.first_screen(term, use_model, len(qs))
            warn = None
            if qs and use_model and Y._specs_on_record():
                est = estimate.reanalysis()
                warn = ("If your answers differ from those the last analysis read, every workflow is analyzed again: "
                        "up to " + model.estimate_words(est, model.settings()).split(". With")[0]
                        + ". You are asked before anything is billed.")
            if not I.interview(term, use_model, qs, warn):
                term.stopped = True
            term.say()
        # the analysis: what changed is analyzed again, the rest comes from the cache
        t0, w0 = time.monotonic(), I.WAITED["seconds"]
        ANALYZING["on"] = True
        with __import__("contextlib").redirect_stdout(log):
            assess.run() if use_model else None     # the domain names, now that billing is agreed
        _run_stage_logged("understand", log)
        ANALYZING["on"] = False
        PROGRESS.clear()
        _write_brief(log)
        worked = time.monotonic() - t0 - (I.WAITED["seconds"] - w0)
        if not again and worked > 60:
            term.say(f"Analysis and brief done in {_minutes(worked)}.")
        if again:
            asked = I.to_ask(term, use_model)
            if asked:
                I.sharpen(term, use_model, asked, lambda: _write_brief(log))
        I.summary(term)
        _, m = _calls_since(calls_before)
        cost = "under $0.01" if m["cost"] < 0.005 else f"${m['cost']:.2f}"
        spent = (f" {m['billed']} billed model call{'s' if m['billed'] != 1 else ''}, {cost}." if m["billed"]
                 else "")
        term.say()
        term.say(f"Done in {_minutes(max(0.0, time.monotonic() - t_all - I.WAITED['seconds']))}.{spent}")
        I.finish(term, C.INDEX)
        return 0
    finally:
        C.set_tick(None)
        log.close()


def _run_with_progress(stages: list[str]) -> int:
    """
    `run`: one line per stage as it goes (its name, what it found, its model calls, how long it took), and a last
    line with the total time, whether the model results came from cache, and the page to open. A stage still running
    after STARTED_AFTER seconds first prints a line saying it started, and a long one shows one progress line ("Analyzing
    46 workflows: 19 done."). What each stage did in detail (each spec written, what was regenerated and why) goes to
    data/run.log.
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
    log = RunLog()
    PROGRESS.out = out
    C.set_tick(PROGRESS.tick)
    try:
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
                        PROGRESS.clear()
                        print(f"{name}  started", file=out, flush=True)
                    if msg:
                        log.line(f"{name}  {msg}")

            timer = threading.Timer(STARTED_AFTER, say)
            timer.daemon = True
            if s != "summary":
                timer.start()
            C.set_progress(say)
            if s == "understand":
                ANALYZING["on"] = True
            try:
                with contextlib.redirect_stdout(buf):
                    if s == "brief":
                        # understand wrote or regenerated specs: Assess reads them (spec systems), so bring it up to date
                        from kestra_migration_assistant.vra.stages import assess
                        assess.load()
                    found = run_stage(s)
                ANALYZING["on"] = False         # still on when Ctrl+C stopped it: the message says how far it got
            finally:
                timer.cancel()
                C.set_progress(None)
                with lock:
                    started["said"] = True          # a timer firing now prints nothing after the stage's line
                log.write(buf.getvalue())
            PROGRESS.clear()
            _, m = _calls_since(before)
            for k in total:
                total[k] += m[k]
            if s == "summary":
                print(f"{RUN_NAMES[s]:<{width}}  {found}")
                continue
            print(f"{RUN_NAMES[s]:<{width}}  {found or 'done'}; {_model_words(m)}; {time.monotonic() - t0:.1f}s",
                  flush=True)
    finally:
        C.set_tick(None)
        log.close()
    cache = ("model results all from cache" if not total["billed"] and total["cached"] else
             "no model calls" if not total["billed"] else
             f"{total['billed']} billed model call{'s' if total['billed'] != 1 else ''}, ${total['cost']:.2f}")
    print(f"Done in {time.monotonic() - t_all:.1f}s; {cache}. Open {shown(C.INDEX)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
