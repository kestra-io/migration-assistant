"""
The interactive run: `kestra-migrate vra run` typed in a terminal asks its questions there, instead of only writing
them to answers.yaml.

    Inventory   the code is read (Sanitize, Preflight, Assess), no model
    Interview   five to eight questions about the whole estate, built from what the code shows, no model
    Analysis    Understand and Brief, with the estate answers in the model's prompts
    Rounds      the questions left, three to five at a time, the one that settles the most first; after each
                round the rest is re-planned (with a model, one call; without, the fixed rules)

Every question has the same shape: numbered choices, the first one what the code or the model suggests (Enter
takes it), the last one always the person's own words. `s` leaves a question for someone else, `q` saves and
stops asking. Each answer is written to answers.yaml as soon as it is given (the file is written whole or not at
all), so nothing answered is ever lost: stopping, a crash or Ctrl-C keeps every answer given so far. The same file,
run again with --batch or with no terminal, gives the same brief.

Nothing here runs unless the person typed the command in a terminal: a program calling `vra.cli.main`, a run with
no terminal attached and `--batch` never wait for input.
"""
from __future__ import annotations

import sys
import textwrap
from typing import TextIO

from .stages import common as C

WIDTH = 100


class Quit(Exception):
    """The person asked to save and stop asking."""


class Terminal:
    """Questions and answers in a plain terminal: numbers typed, no arrow keys."""

    def __init__(self, inp: TextIO | None = None, out: TextIO | None = None):
        self.inp = inp or sys.stdin
        self.out = out or sys.stdout

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
        """One question: the answer as text, None to skip it. Raises Quit for save and quit."""
        from .stages.planner import NONE_OF_THESE
        opts = list(q["options"]) + [NONE_OF_THESE]
        self.say()
        head = where + (f" (settles {q['settles']} questions)" if q.get("settles", 1) > 1 else "")
        self.say(head)
        self.say(q["question"], "  ")
        for i, o in enumerate(opts, 1):
            mark = " (suggested)" if q.get("default") and o == q["default"] else ""
            self.say(f"{i}. {o}{mark}", "    ")
        enter = " Enter for 1," if q.get("default") else ""
        while True:
            a = self.read(f"  Choose 1-{len(opts)},{enter} s to skip, q to save and quit: ").lower()
            if a == "" and q.get("default"):
                return str(q["default"])
            if a in ("q", "quit"):
                raise Quit()
            if a in ("s", "skip"):
                return None
            if a.isdigit() and 1 <= int(a) <= len(opts):
                if int(a) < len(opts):
                    return opts[int(a) - 1]
                while True:
                    own = self.read("  Your answer: ")
                    if own:
                        return own
            self.say(f"Type a number from 1 to {len(opts)}, s or q.", "  ")


def ask_round(term: Terminal, qs: list[dict], title: str, skipped: set[str]) -> list[dict]:
    """Ask one round. Writes each answer at once and says what it settled. Returns the answers given."""
    from .stages import planner
    from .stages import your_input as Y
    given = []
    term.say()
    term.say(title)
    for i, q in enumerate(qs, 1):
        before = planner.states()
        a = term.pose(q, f"Question {i} of {len(qs)}")
        if a is None:
            skipped.add(q["id"])
            term.say("Skipped: it stays open in answers.yaml for someone else to answer.", "  ")
            continue
        planner.record(q, a)
        n = planner.settled_by(before, planner.states())
        given.append({"id": q["id"], "question": q["question"], "answer": a})
        left = Y.count()
        if n > 1:
            term.say(f"Saved. This answer settled {n} questions. {planner.summary_line(left)}.", "  ")
        else:
            term.say(f"Saved. {planner.summary_line(left)}.", "  ")
    return given


def interview(term: Terminal, use_model: bool = True) -> bool:
    """Step 2: the open estate questions, all in one go. False when the person chose to stop."""
    from .stages import planner
    qs = [q for q in planner.questions() if q.get("estate") and (use_model or not q.get("needs_model"))]
    if not qs:
        return True
    term.say()
    term.say(f"About the estate: {len(qs)} question{'s' if len(qs) != 1 else ''}. Each answer applies to every "
             "workflow and settles many questions at once. Your answers are saved under `estate:` in "
             f"{C.rel(C.ANSWERS)} as you go.")
    try:
        ask_round(term, qs, "", set())
    except Quit:
        term.say()
        term.say("Saved. The run goes on with what you answered; run it again to answer the rest.")
        return False
    return True


def rounds(term: Terminal, use_model: bool, rerun_brief) -> dict:
    """Step 4: the remaining questions in rounds, re-planned after each. `rerun_brief` brings the brief up to date
    with the answers (new decisions can follow from them). Returns {"rounds", "answered", "stopped"}."""
    from .stages import planner
    from .stages import your_input as Y
    skipped: set[str] = set()
    done = {"rounds": 0, "answered": 0, "stopped": False, "left_out": 0}
    while True:
        qs = [q for q in planner.questions() if not q.get("estate") and q["id"] not in skipped]
        if not use_model:
            need = [q for q in qs if q.get("needs_model")]
            if need and not done["left_out"]:
                done["left_out"] = len(need)
                term.say()
                term.say(f"Left out: {len(need)} question{'s' if len(need) != 1 else ''} that need a model to read "
                         "the code. They stay in answers.yaml, marked as such; run with a model to get a suggestion.")
            qs = [q for q in qs if not q.get("needs_model")]
        if not qs:
            break
        batch = planner.rounds(qs)[0]
        n = done["rounds"] + 1
        c = Y.count()
        try:
            given = ask_round(term, batch, f"Round {n}: {len(batch)} of {c['open']} open question"
                                           f"{'s' if c['open'] != 1 else ''}, the ones that settle the most first.",
                              skipped)
        except Quit:
            done["stopped"] = True
            break
        done["rounds"] = n
        done["answered"] += len(given)
        if given:
            r = planner.replan(given, use_model)
            rerun_brief()
            said = [f"{r[k]} {w}" for k, w in (("closed", "closed"), ("merged", "merged into one"),
                                                 ("reworded", "reworded"), ("added", "added")) if r.get(k)]
            if r.get("model") and said:
                term.say(f"Re-planned with your answers: {', '.join(said)}.", "  ")
            elif r.get("error"):
                term.say(f"The re-planning call failed ({r['error'][:200]}); the next round uses the fixed rules.",
                         "  ")
    rerun_brief()                       # the brief, and its counts, follow every answer given
    c = Y.count()
    term.say()
    if done["stopped"]:
        term.say(f"Saved and stopped. {planner.summary_line(c)}. Run the same command again to go on.")
    elif c["open"]:
        term.say(f"Nothing more to ask now. {planner.summary_line(c)}: they were skipped or need a model, and stay "
                 f"in {C.rel(C.ANSWERS)}.")
    else:
        term.say("Every question is answered. Sign the plan in answers.yaml (signed_for) when it reads right.")
    return done
