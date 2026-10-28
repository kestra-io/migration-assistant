"""
The interactive run: `kestra-migrate vra run` typed in a terminal asks its questions there, instead of only writing
them to answers.yaml.

    Model       which model the run uses, changeable, remembered for the estate
    Inventory   the code is read (Sanitize, Preflight, Assess), no model; then the first screen: what was read, what
                is missing, that the assessment only reads, and how to stop
    Interview   the few questions about the whole estate that the analysis reads, no model
    Analysis    Understand and Brief, asked for before anything is billed, with one progress line
    Questions   what only the person can settle for the plan; every few answers the rest is re-planned (with a model,
                one call; without, the fixed rules)

Every question has the same shape: numbered choices, the suggestion first (Enter takes it), the last one always the
person's own words, and a question whose choices are not exclusive takes several numbers ("2, 4"). `s` leaves a
question for someone else, `q` saves and stops asking. Each answer is written to answers.yaml as soon as it is given
(the file is written whole or not at all), so nothing answered is ever lost: stopping, a crash or Ctrl+C keeps every
answer given so far. The same file, run again with --batch or with no terminal, gives the same brief.

The terminal only talks about the questions it said it would ask: no counts kept behind the scenes, no rounds.

Nothing here runs unless the person typed the command in a terminal: a program calling `vra.cli.main`, a run with
no terminal attached and `--batch` never wait for input.
"""
from __future__ import annotations

import os
import re
import sys
import textwrap
from typing import TextIO

from .stages import common as C

WIDTH = 100
OWN = "None of these, enter my own answer"
NO_OWN = "no, enter my own answer"
SKIPPED = "Skipped: it stays open in answers.yaml for someone else to answer."
STOP_LINE = "Press Ctrl+C to stop at any time. Finished work is kept."


class Quit(Exception):
    """The person asked to save and stop asking."""


class Terminal:
    """Questions and answers in a plain terminal: numbers typed, no arrow keys."""

    def __init__(self, inp: TextIO | None = None, out: TextIO | None = None):
        self.inp = inp or sys.stdin
        self.out = out or sys.stdout
        self.stopped = False

    def say(self, text: str = "", indent: str = "") -> None:
        if not text:
            print("", file=self.out, flush=True)
            return
        for para in text.split("\n"):
            print(textwrap.fill(para, WIDTH, initial_indent=indent, subsequent_indent=indent) if para else "",
                  file=self.out, flush=True)

    def read(self, prompt: str) -> str:
        print(prompt, end="", file=self.out, flush=True)
        line = self.inp.readline()
        if not line:                                     # end of input: save and stop
            raise Quit()
        return line.strip()

    def pose(self, q: dict, where: str) -> str | None:
        """One question: the answer as text (several choices joined by "; "), None to skip it. Raises Quit."""
        opts = list(q.get("options") or [])
        if opts and NO_OWN not in opts:
            opts.append(OWN)
        self.say()
        about = q.get("about") or []
        head = where + (f", about {about[0]}" if len(about) == 1 else f", affects {len(about)} workflows"
                        if len(about) > 1 else "")
        self.say(head)
        self.say(q["question"], "  ")
        for line in q.get("list") or []:
            self.say(f"- {line[0]}", "    ")
            if line[1]:
                self.say(f"Suggested: {line[1]}", "      ")
        if not opts:
            a = self.read("  Your answer (Enter to skip, q to save and quit): ")
            if a.lower() in ("q", "quit"):
                raise Quit()
            return a or None
        for i, o in enumerate(opts, 1):
            mark = " (suggested)" if q.get("default") and o == q["default"] else ""
            self.say(f"{i}. {o}{mark}", "    ")
        n = len(opts)
        enter = " Enter for 1," if q.get("default") else ""
        how = (f"  Choose one or more, like 2, 4;{enter} s to skip, q to save and quit: " if q.get("several")
               else f"  Choose 1-{n},{enter} s to skip, q to save and quit: ")
        while True:
            a = self.read(how).lower()
            if a == "" and q.get("default"):
                return str(q["default"])
            if a in ("q", "quit"):
                raise Quit()
            if a in ("s", "skip"):
                return None
            nums = [int(x) for x in re.split(r"[,\s]+", a) if x.isdigit()] if re.fullmatch(r"[\d,\s]+", a) else []
            if nums and all(1 <= x <= n for x in nums) and (len(nums) == 1 or q.get("several")):
                picked = [opts[x - 1] for x in dict.fromkeys(nums)]
                if any(p in (OWN, NO_OWN) for p in picked):
                    while True:
                        own = self.read("  Your answer: ")
                        if own:
                            return own
                return "; ".join(picked)
            self.say(f"Type {'one or more numbers' if q.get('several') else 'a number'} from 1 to {n}, s or q.", "  ")


# ------------------------------------------------------------------ the model, first

def remembered_model() -> dict:
    """The model chosen in the terminal for this estate: {"provider", "model"}, or {}."""
    p = os.path.join(C.DATA, "model.json")
    try:
        d = C.read_json(p) if os.path.exists(p) else {}
    except (OSError, ValueError):
        return {}
    return {k: d[k] for k in ("provider", "model") if isinstance(d.get(k), str)}


def remember_model(provider: str, model: str) -> None:
    C.write_json(os.path.join(C.DATA, "model.json"), {"provider": provider, "model": model})


def model_choices(s) -> list[dict]:
    """The providers and models this machine can use, the current one first: {"provider", "model", "label"}."""
    import shutil

    from kestra_migration_assistant import config as K

    from . import model as M
    out: list[dict] = []
    base = M.PRICES.get(s.model or "") or M.PRICES[K.DEFAULT_MODEL]

    def cost(m: str) -> str:
        p = M.PRICES.get(m)
        if not p or p == base:
            return ""
        r = p[1] / base[1]
        if r < 1:
            k = round(1 / r)
            return f": about {'half' if k == 2 else f'1/{k}'} the cost of {M.model_name(s.model or K.DEFAULT_MODEL)}"
        return f": about {round(r)} times the cost of {M.model_name(s.model or K.DEFAULT_MODEL)}"

    def add(provider: str, model: str, how: str) -> None:
        if not any(c["provider"] == provider and c["model"] == model for c in out):
            out.append({"provider": provider, "model": model,
                        "label": f"{M.model_name(model)} through {how}" + cost(model)})
    how = {"claude-code": "your Claude Code login", "anthropic": "an Anthropic API key"}
    if s.provider:
        add(s.provider, s.model, how.get(s.provider) or s.base_url or s.provider)
    claude = s.provider == "claude-code" or (bool(shutil.which("claude")) and not K.in_container())
    keyed = s.provider == "anthropic" or bool(os.environ.get("ANTHROPIC_API_KEY"))
    for provider, ok in (("claude-code", claude), ("anthropic", keyed)):
        if ok:
            for m in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"):
                add(provider, m, how[provider])
    return out


def choose_model(term: Terminal, s):
    """Say which model the run uses; Enter keeps it, c shows the others with their cost and changes it (remembered
    for this estate). Returns the settings to use."""
    from . import model as M
    a = term.read(f"Using {s.describe()}. Press Enter to continue, or c to change: ").lower()
    if a not in ("c", "change"):
        return s
    opts = model_choices(s)
    if len(opts) < 2:
        term.say("No other model is set up on this machine. `kestra-migrate init` says how to add one.", "  ")
        return s
    q = {"question": "Which model should the assessment use?", "options": [o["label"] for o in opts],
         "default": opts[0]["label"]}
    while True:
        try:
            pick = term.pose(q, "The model")
        except Quit:
            return s
        if pick in q["options"]:
            break
        term.say("Choose one of the numbers.", "  ")
    o = opts[q["options"].index(pick)]
    import dataclasses

    from kestra_migration_assistant import config as K
    if o["provider"] != s.provider:
        try:
            s = K.resolve({"provider": o["provider"], "model": o["model"]})
        except K.ConfigError as e:
            term.say(f"That one cannot be used: {e}", "  ")
            return s
    else:
        s = dataclasses.replace(s, model=o["model"])
    M.configure(s)
    remember_model(o["provider"], o["model"])
    term.say(f"Using {s.describe()} for this estate from now on.", "  ")
    return s


# ------------------------------------------------------------------ the first screen

def _n(k: int, one: str, many: str | None = None) -> str:
    return f"{k} {one if k == 1 else (many or one + 's')}"


def first_screen(term: Terminal, use_model: bool, n_questions: int) -> None:
    """What was read, what is missing, that the assessment only reads, how to stop, and what comes next."""
    from .stages import patterns
    pf = os.path.join(C.PRE, "preflight.json")
    exp = (C.read_json(pf).get("export") if os.path.exists(pf) else None) or {}
    term.say()
    term.say("Read:")
    words = {"Workflow": ("workflow", None), "ScriptModule": ("action", None),
             "ConfigurationElement": ("configuration element", None), "ResourceElement": ("resource element", None)}
    for src in exp.get("sources") or []:
        c = src.get("element_counts") or {}
        parts = [_n(c[k], *w) for k, w in words.items() if c.get(k)]
        term.say(f"- {os.path.basename(src.get('path') or src.get('source') or '')}: " + (", ".join(parts) or "nothing "
                 "this tool reads"), "  ")
    for x in exp.get("vra_exports") or []:
        c = x.get("counts") or {}
        parts = [f"{c[k]} {k if c[k] != 1 else k[:-1]}" for k in ("catalog items", "subscriptions", "cloud templates")
                 if c.get(k)]
        term.say(f"- {os.path.basename(x.get('path') or x.get('label') or '')}: " + (", ".join(parts) or "nothing this "
                 "tool reads"), "  ")
    pat = patterns.load() if os.path.exists(os.path.join(C.ASSESS, "patterns.json")) else {}
    adds = pat.get("missing") or []
    if adds:
        term.say()
        term.say(f"Missing: the workflows call {_n(len(adds), 'piece')} of code that {'is' if len(adds) == 1 else 'are'} "
                 "not in the package. Add them and run again:")
        for a in adds:
            by = a.get("called_by") or []
            term.say(f"- {a['what']}, called by {', '.join(by[:2])}{f' and {len(by) - 2} more' if len(by) > 2 else ''}. "
                     f"{a.get('how', '').rstrip('.')}.", "  ")
    term.say()
    if use_model:
        term.say("The assessment only reads these files: it never connects to or runs anything on your systems. Only "
                 "the model receives a copy, sanitized here first (hosts, addresses, accounts and secrets replaced).")
    else:
        term.say("The assessment only reads these files: it never connects to or runs anything on your systems, and "
                 "this run sends nothing anywhere.")
    term.say(STOP_LINE)
    term.say()
    if n_questions:
        term.say(f"Before the analysis, {_n(n_questions, 'question')} about your estate.")


# ------------------------------------------------------------------ the interview

def interview_questions(use_model: bool) -> list[dict]:
    from .stages import planner
    return [q for q in planner.questions() if q.get("estate") and q.get("phase") == "interview"
            and (use_model or not q.get("needs_model"))]


def interview(term: Terminal, use_model: bool, qs: list[dict], reanalysis: str | None = None) -> bool:
    """The estate questions the analysis reads, all in one go. False when the person chose to stop."""
    from .stages import planner
    if reanalysis and qs:
        term.say(reanalysis)
    try:
        for i, q in enumerate(qs, 1):
            a = term.pose(q, f"Question {i} of {len(qs)}")
            if a is None:
                term.say(SKIPPED, "  ")
                continue
            planner.record(q, a)
    except Quit:
        term.say()
        term.say("Saved. The run goes on with what you answered; run it again to answer the rest.")
        return False
    return True


# ------------------------------------------------------------------ after the analysis

def _plan_questions(use_model: bool, skipped: set[str]) -> list[dict]:
    from .stages import planner
    qs = [q for q in planner.questions() if q.get("phase") != "interview" and q["id"] not in skipped]
    return [q for q in qs if use_model or not q.get("needs_model")]


def _strip_name(q: dict) -> dict:
    """The question as the terminal asks it: without the workflow's name (the header says which workflows it is
    about), and without its choices in brackets when they are listed under it."""
    text = re.sub(r"^Asked of \d+ workflows \([^)]*\), one answer for all: ", "", q["question"])
    for w in q.get("about") or []:
        if text.startswith(w + ": "):
            text = text[len(w) + 2:]
            text = text[:1].upper() + text[1:]
    m = re.search(r"\s*\(([^()]*)\)\s*$", text)
    if m and q.get("options") and " or " in m.group(1) and all(
            o.split(":")[0].strip().lower() in m.group(1).lower() for o in q["options"][:2]):
        text = text[:m.start()]
    return {**q, "question": text}


def _blanket(q: dict, defects_left: int) -> dict:
    """A possible-defect question offers to keep every one still asked, when there is more than one."""
    if not q.get("defect") or defects_left < 2:
        return q
    return {**q, "options": [*q["options"], "keep this and every other possible defect asked here"]}


def after_analysis(term: Terminal, use_model: bool, rerun_brief) -> dict:
    """The questions only the person can settle for the plan. Says how many first, and only ever talks about those;
    an answer that settles others (a blanket answer, an estate answer) takes them off the list at once. Every
    REPLAN_EVERY answers, with a model, the rest is re-planned. Returns {"asked", "answered", "stopped"}."""
    from .stages import planner
    from .stages import your_input as Y
    skipped: set[str] = set()
    told = {q["id"] for q in _plan_questions(use_model, skipped)}
    done = {"asked": 0, "answered": 0, "stopped": False}
    term.say()
    if not told:
        term.say("Nothing to ask after the analysis.")
        return done
    term.say(f"After the analysis, {_n(len(told), 'question')}.")
    since, given = 0, []
    while True:
        qs = [q for q in _plan_questions(use_model, skipped) if q["id"] in told]
        if not qs:
            break
        q = qs[0]
        total = done["asked"] + len(qs)
        defects_left = sum(1 for x in qs if x.get("defect"))
        try:
            a = term.pose(_blanket(_strip_name(q), defects_left), f"Question {done['asked'] + 1} of {total}")
        except Quit:
            done["stopped"] = True
            break
        done["asked"] += 1
        if a is None:
            skipped.add(q["id"])
            term.say(SKIPPED, "  ")
            continue
        if a == "keep this and every other possible defect asked here":
            for x in qs:
                if x.get("defect"):
                    planner.record(x, planner.KEEP_ALL if x.get("group") else "keep it, as today")
        else:
            planner.record(q, a)
        if q["id"] == "suggested" and planner.value_of(a) == "decide each":
            told |= {x["id"] for x in _plan_questions(use_model, skipped)} - {x["id"] for x in qs}
        done["answered"] += 1
        given.append({"id": q["id"], "question": q["question"], "answer": a})
        since += 1
        if use_model and since >= planner.REPLAN_EVERY and len(qs) > 1:
            term.say()
            term.say("Updating the remaining questions…")
            r = planner.replan(given, use_model)
            rerun_brief()
            given, since = [], 0
            told |= {gid for gid in (Y.registry().get("_merges") or {})
                     if set(Y.registry()["_merges"][gid].get("members") or []) <= _members_of(told)}
            if r.get("error"):
                C.progress(f"the re-planning call failed: {r['error'][:200]}")
    rerun_brief()                       # the brief, and its counts, follow every answer given
    term.say()
    if done["stopped"]:
        term.say("Saved and stopped. Run the same command again to go on.")
    elif skipped:
        term.say(f"Nothing more to ask now. What you skipped stays in {C.rel(C.ANSWERS)}.")
    else:
        term.say("That was the last question. Sign the plan in answers.yaml (signed_for) when it reads right.")
    return done


def _members_of(ids: set[str]) -> set[str]:
    """The items behind question ids (a group's members, or the item itself)."""
    from .stages import your_input as Y
    gs = Y.load()["groups"]
    out: set[str] = set()
    for i in ids:
        out |= set(gs.get(i) or [i])
    return out
