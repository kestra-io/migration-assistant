"""
The interactive run: `kestra-migrate vra run` typed in a terminal asks its questions there, instead of only writing
them to answers.yaml.

    Model       which model the run uses, changeable, remembered for the estate
    Inventory   the code is read (Sanitize, Preflight, Assess), no model; then the first screen: what was read, what
                is missing, that the assessment only reads, and how to stop
    Interview   the few questions about the whole estate that the analysis reads, no model
    Analysis    Understand, asked for before anything is billed, with one progress line
    Brief       written at once: every question that has a suggestion is assumed, written in answers.yaml as assumed
                and listed in the brief; then a short summary
    Sharpen     an offer, no by default, to answer at most OFFER_AT_MOST questions that have no suggestion, those that
                change the brief most first; then one re-planning call (with a model) and the brief again
Run again on an estate that has a brief (and nothing to ask before the analysis), it redoes what changed and goes
straight to the summary and the offer.

Every question has the same shape: numbered choices, the suggestion first (Enter takes it), the last one always the
person's own words, and a question whose choices are not exclusive takes several numbers ("2, 4"). `s` leaves a
question for someone else, `q` saves and stops asking. Each answer is written to answers.yaml as soon as it is given
(the file is written whole or not at all), so nothing answered is ever lost: stopping, a crash or Ctrl+C keeps every
answer given so far. The same file, run again with --batch or with no terminal, gives the same brief.

The terminal only talks about the questions it offered: no counts kept behind the scenes, no rounds.

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
            # a path or a name is never split: no break at its hyphens, and a long one keeps a line of its own
            print(textwrap.fill(para, WIDTH, initial_indent=indent, subsequent_indent=indent, break_on_hyphens=False,
                                break_long_words=False) if para else "", file=self.out, flush=True)

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
        one = len(adds) == 1
        term.say(f"Not in the package: the workflows call {_n(len(adds), 'piece')} of code that "
                 f"{'isn' if one else 'aren'}'t in it. You can carry on: the analysis runs without "
                 f"{'it' if one else 'them'}, but the workflows that call {'it' if one else 'them'} get less certain "
                 f"results. To include {'it' if one else 'them'}, add {'it' if one else 'them'} to the package and run "
                 "again.")
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


# ------------------------------------------------------------------ the brief, then sharpening it

OFFER_AT_MOST = 5                       # questions offered in one go
SECONDS_EACH = 25                       # about how long one question takes to answer


def has_brief() -> bool:
    """The estate has a brief from an earlier run."""
    return os.path.exists(os.path.join(C.BRIEF, "brief.json"))


def _brief() -> dict:
    return C.read_json(os.path.join(C.BRIEF, "brief.json"))


def summary(term: Terminal, path: str) -> None:
    """The brief in a few lines: the recommendation, the waves, how many workflows convert, and where to open it."""
    b = _brief()
    rec = b.get("recommendation") or {}
    verdict = str(rec.get("verdict") or "").strip()
    why = re.sub(r"^\w[\w ]*\.\s+", "", str(rec.get("produced_by") or ""), count=1)
    first = re.split(r"(?<=\.)\s+", why, maxsplit=1)[0] if why else ""
    c = b.get("counts") or {}
    total = sum(c.values())
    converts = total - sum(c.get(o, 0) for o in ("Retire", "Rebuild", "Manual", "Needs a model"))
    term.say()
    term.say(f"The brief recommends: {verdict}." + (f" {first}" if first and verdict != "needs a model" else ""))
    if verdict != "needs a model" and f"{converts} workflows convert" not in first:
        term.say(f"{converts} of {_n(total, 'workflow')} convert to Kestra flows.", "  ")
    waves = (b.get("plan") or {}).get("waves") or []
    if waves:
        term.say(f"{_n(len(waves), 'wave')}:", "  ")
        for w in waves:
            n = w.get("workflows")
            if w.get("kind") == "vra-replacement":
                ts = [f"'{t}'" for t in w.get("cloud_templates") or []]
                term.say(f"Wave {w['wave']}: replaces vRA for {', '.join(ts) or 'its cloud templates'} with a Kestra App "
                         "and a provisioning flow", "    ")
                continue
            term.say(f"Wave {w['wave']}: {_n(len(n) if isinstance(n, list) else int(n or w.get('n') or 0), 'workflow')}, "
                     f"{_wave_words(w)}", "    ")
    term.say(f"Open {path}", "  ")


def _wave_words(w: dict) -> str:
    """What a wave does, from its name ("writes: A, B, C"): the systems it writes to or reads."""
    name = str(w.get("name") or "")
    how, _, what = name.partition(": ")
    systems = [x.strip() for x in what.split(",") if x.strip()] if what else []
    if not systems:
        return name
    shown = ", ".join(systems[:3]) + (f" and {len(systems) - 3} more" if len(systems) > 3 else "")
    return f"{'reads' if how.startswith('read') else 'writes to'} {shown}"


def assumptions_count() -> int:
    """The assumptions the brief lists: those in force."""
    return sum(1 for a in _brief().get("assumptions") or [] if a.get("holds"))


def to_offer(use_model: bool) -> list[dict]:
    """The questions that can sharpen the brief: those with no suggestion (every other one is assumed), the ones
    that change the brief most first."""
    from .stages import planner
    qs = [q for q in planner.questions() if not q.get("estate") and (use_model or not q.get("needs_model"))]
    return planner.ranked(qs)


def _minutes_words(k: int) -> str:
    m = round(k * SECONDS_EACH / 60)
    return "about a minute" if m <= 1 else f"about {m} minutes"


def offer(term: Terminal, use_model: bool) -> list[dict]:
    """Say the brief is ready and how many assumptions it makes, and offer the few questions that sharpen it. The
    default is no. Returns the questions to ask (none when the person says no, or nothing is left to offer)."""
    n = assumptions_count()
    made = f"It makes {_n(n, 'assumption')}, listed in it." if n else "It makes no assumptions."
    qs = to_offer(use_model)[:OFFER_AT_MOST]
    term.say()
    if not qs:
        term.say(f"The brief is ready. {made} To change one, write your answer under it in {C.rel(C.ANSWERS)} and "
                 "run the same command again.")
        return []
    k = len(qs)
    some = f"up to {k} questions" if k == OFFER_AT_MOST else _n(k, "question")
    term.say(f"The brief is ready. {made} To make it sharper, you can answer {some} now ({_minutes_words(k)}), or "
             f"later in {C.rel(C.ANSWERS)}.")
    try:
        a = term.read("Answer them now? (y/N) ").strip().lower()
    except Quit:
        return []
    return qs if a in ("y", "yes") else []


def sharpen(term: Terminal, use_model: bool, qs: list[dict], rewrite) -> dict:
    """Ask the questions offered, q to stop at any point; then re-plan what is left (with a model, one call) and
    write the brief again. Returns {"asked", "answered", "stopped"}."""
    from .stages import planner
    done = {"asked": 0, "answered": 0, "stopped": False}
    given = []
    for i, q in enumerate(qs, 1):
        try:
            a = term.pose(_strip_name(q), f"Question {i} of {len(qs)}")
        except Quit:
            done["stopped"] = True
            break
        done["asked"] += 1
        if a is None:
            term.say(SKIPPED, "  ")
            continue
        planner.record(q, a)
        done["answered"] += 1
        given.append({"id": q["id"], "question": q["question"], "answer": a})
    term.say()
    if not given:
        term.say("Nothing answered: the brief stays as it is.")
        return done
    term.say("Updating the brief with your answers…")
    r = planner.replan(given, use_model)
    if r.get("error"):
        C.progress(f"the re-planning call failed: {r['error'][:200]}")
    rewrite()
    return done


def _strip_name(q: dict) -> dict:
    """The question as the terminal asks it: without the workflow's name (the header says which workflows it is
    about), and without its choices in brackets when they are listed under it."""
    text = re.sub(r"^Asked of \d+ workflows \([^)]*\), one answer for all: ", "", q["question"])
    text = re.sub(r"^The same possible defect in \d+ workflows \([^)]*\): ", "", text)
    for w in q.get("about") or []:
        if text.startswith(w + ": "):
            text = text[len(w) + 2:]
            text = text[:1].upper() + text[1:]
    m = re.search(r"\s*\(([^()]*)\)\s*$", text)
    if m and q.get("options") and " or " in m.group(1) and all(
            o.split(":")[0].strip().lower() in m.group(1).lower() for o in q["options"][:2]):
        text = text[:m.start()]
    return {**q, "question": text}
