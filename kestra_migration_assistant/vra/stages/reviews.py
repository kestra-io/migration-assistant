"""
Spec reviews on record: the `spec.<workflow>` items of your-input/answers.yaml.

An owner who has read a spec answers its item: `yes` when it is right, or what is wrong. The brief reads it on
every run, and a reviewed spec closes the "owner reviews the spec" entry criterion of its wave. The registry
keeps the fingerprint of the spec each review was written for (your_input's memo); when the spec is
regenerated after the review, the review is stale and the criterion opens again until the answer changes.
Before 2026-10-01 reviews lived in 3-brief/reviews.yaml; your_input.migrate() carries them over.
"""
from __future__ import annotations

import hashlib
import json
import re

from . import gates as G


def fingerprint(spec: dict | None) -> str | None:
    """A short, stable fingerprint of a spec's content (what the model wrote, not the prompt record)."""
    if not spec:
        return None
    body = {k: v for k, v in spec.items() if k != "context"}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:12]


def input_items(specs: dict[str, dict | None], wf: dict) -> list[dict]:
    """One review item per spec."""
    from . import your_input as Y
    out = []
    for u in sorted(specs, key=lambda u: wf[u]["short"]):
        s = specs[u]
        if not s:
            continue
        conf = s.get("confidence")
        it = Y.item(G.review_key(wf[u]["short"]), "review",
                    f"Read the spec of {wf[u]['name']} (confidence {conf}). Is it right?",
                    why=("medium confidence: its wave waits for this review" if conf == "medium" else
                         "the converter builds what the spec says; a wrong spec converts wrongly"),
                    unblocks=([f"the 'owner reviews the spec' criterion of {wf[u]['name']}'s wave"]
                              if conf == "medium" else []),
                    family="spec", link=f"report/workflows/{wf[u]['short']}.html",
                    fixed={"spec": fingerprint(s)},
                    question=f"Is the spec of {wf[u]['name']} right? (see its workflow page; answer yes, or say "
                             "what is wrong)")
        it["family_title"] = "Specs"
        it["workflow"] = wf[u]["name"]
        it["confidence"] = conf
        it["says"] = first_sentence(s.get("purpose") or "", wf[u]["name"])
        out.append(it)
    return out


def load(specs: dict[str, dict | None], wf: dict, loaded: dict | None = None) -> dict:
    """
    {"path", "exists", "by_uid": {uid: {state, reviewed_by, date, comment, why}}, "warnings"}.
    state: "reviewed" (closes the gate), "stale" (reviewed, then the spec changed), "open".
    """
    from . import your_input as Y
    loaded = loaded if loaded is not None else Y.load()
    out = {"path": loaded["path"], "exists": loaded["exists"], "by_uid": {}, "warnings": []}
    for u, s in specs.items():
        key = G.review_key(wf[u]["short"])
        if key not in loaded["entries"] and Y.via_group(key, loaded) is None:
            continue
        st = Y.state(key, loaded, {"group": "review", "fixed": {"spec": fingerprint(s)}})
        if not s and st["state"] != "open":
            st = {**st, "state": "open", "why": "there is no spec to review"}
        ans = st["answer"] or ""
        out["by_uid"][u] = {"state": st["state"], "reviewed_by": st["by"], "date": st["date"],
                            "comment": st["note"] or (ans if ans and ans.lower() not in Y.YES else None),
                            "why": st["why"]}
    return out


def first_sentence(text: str, name: str = "") -> str:
    """What a spec says a workflow does, in one sentence, to follow its name: "adds one user to ...", or, when the
    spec starts with what the workflow is, "is an hourly capacity check ..."."""
    t = re.split(r"(?<=[.!?])\s+(?=[A-Z])", str(text or "").strip(), maxsplit=1)[0].strip().rstrip(".")
    if name and t.lower().startswith(name.lower() + " "):
        t = t[len(name) + 1:]                          # "Extend VM Disk is a linear workflow" -> "is a linear workflow"
    if not t:
        return ""
    low = re.sub(r"\W+$", "", t.split()[0].lower())
    if t[:1].isupper() and not t[1:2].isupper():
        t = t[:1].lower() + t[1:]
    if low in ("is", "does", "runs", "was"):
        return t
    if low in ("a", "an", "the", "one"):
        return "is " + t
    if low.endswith("ed"):                              # "Subscribed to an event, this workflow ...": up to the comma
        return "is " + t.split(",", 1)[0]
    if VERB.fullmatch(low) and low not in NOT_VERBS:
        return t
    return ("is an " if re.match(r"[aeiou]|hour|honest", t, re.I) else "is a ") + t


# A sentence that says what the workflow does starts with a verb: "Adds", "Provisions", "Removes".
VERB = re.compile(r"[a-z]+(?<!s)s|[a-z]+(?:ches|shes|sses|xes|zes)")
NOT_VERBS = {"this", "its", "his", "tens", "days", "vms", "settings", "operations", "sales", "news", "series",
             "status", "various"}
