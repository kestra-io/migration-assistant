"""
What this estate actually has, in words: entry points on record, catalog items, run history.

One place for the sentences the brief, the visuals and the status line use about triggers, catalog items
and run history, so none of them says "no trigger export" to an estate that sent one, or promises run
history nobody offered. Everything comes from Assess (the entry points model) and the preflight answers.
"""
from __future__ import annotations

import re

KIND_WORDS = {"subscriptions": ("event subscription", "event subscriptions"),
              "catalog_items": ("catalog item", "catalog items"),
              "cloud_templates": ("cloud template", "cloud templates"),
              "day2_actions": ("day-2 action", "day-2 actions"),
              "other_triggers": ("other trigger", "other triggers")}
NO_HISTORY = re.compile(r"\b(no|none|not available|unavailable|cannot|can't|don't have|do not have)\b", re.I)


def _n(k: str, n: int) -> str:
    one, many = KIND_WORDS[k]
    return f"{n} {one if n == 1 else many}"


def estate(assess: dict, answers: dict) -> dict:
    model = ((assess.get("entry_points") or {}).get("model")) or {}
    counts = {k: len(model.get(k) or []) for k in KIND_WORDS}
    starts = sum(counts[k] for k in ("subscriptions", "catalog_items", "day2_actions", "other_triggers"))
    q = {i["id"]: i for i in answers.get("items") or []}
    q2 = (q.get("Q2") or {}).get("status", "open")
    q7 = q.get("Q7") or {}
    if q7.get("status") != "answered":
        history = "unknown"
    elif NO_HISTORY.search(q7.get("answer") or ""):
        history = "none"
    else:
        history = "offered"
    wf = assess.get("workflows") or {}
    started = sum(1 for w in wf.values() if w.get("entry_points"))
    listed = ", ".join(_n(k, v) for k, v in counts.items() if v)

    if starts or counts["cloud_templates"]:
        triggers = (f"On record: {listed}, starting {started} of {len(wf)} workflows"
                    + ("" if q2 == "answered" else "; the list is not marked complete (preflight Q2), so a "
                       "schedule, policy or external caller may still be missing") + ".")
    else:
        triggers = ("No entry point is on record: no vRA export and nothing in entry-points.yaml (preflight Q2 "
                    "is open), so nothing says what starts these workflows.")
    catalog = (f"{_n('catalog_items', counts['catalog_items'])} on record" if counts["catalog_items"]
               else "no catalog item on record")
    hist = {"unknown": "No run history is on record, and preflight Q7 (can it be exported) is open.",
            "none": "No run history is available (preflight Q7).",
            "offered": "Run history was offered (preflight Q7) but this tool does not read it yet; it is not "
                       "used below."}[history]
    half = ("the 'no caller and no trigger' half can be checked against the entry points on record"
            if starts else "only the 'no caller' half can be checked, since no entry point is on record")
    retire_rule = ("No executions in twelve months, or no caller and no trigger. "
                   + ("Without run history, " if history != "offered" else "Run history is not read, so ")
                   + half + ".")
    return {"counts": counts, "starts": starts, "started_workflows": started, "q2": q2, "history": history,
            "vra": bool(starts or counts["cloud_templates"]) and any(
                counts[k] for k in ("subscriptions", "catalog_items", "cloud_templates", "day2_actions")),
            "triggers": triggers, "catalog": catalog, "history_sentence": hist, "retire_rule": retire_rule,
            "listed": listed}
