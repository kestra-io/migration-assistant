"""
Entry criteria a person can close, and the item that closes each one.

Every wave entry criterion carries `closes`: the file a person edits and the key in it. Since 2026-10-01 that
file is your-input/answers.yaml for everything but an outcome correction (your_input.py):

- answer    a preflight question (Q3, Q4, Q9): an answer closes it.
- approval  the signature (`signed_for:`): one whose scope covers the wave closes it.
- review    a spec review (`spec.<workflow>`): an answer closes it, until the spec is regenerated.
- decision  a choice rather than an answer or a review (a client route, the edition, a timeout policy, how to
            handle a gap, whether a lifecycle group converts whole, a suspected defect, a spec question that
            holds a workflow): an answer closes it.
- outcome   your-input/corrections.yaml, one workflow: the criterion closes when that workflow gets a flow,
            which a new outcome there (or the answer that unblocks it) brings.
- gate      an earlier wave's exit (`wave-exit.<n>`): the migration closes it, when that wave is migrated and
            verified. Nobody answers it during the assessment, so it is not in answers.yaml, is not an open item,
            and the status line reads it as the order the waves run in.

One decision can close several criteria (the edition, asked by every wave that needs it). A decision can also
stop a wave: an option listed under `blocks` (Enterprise Edition "not available") marks the criterion blocked,
not met, and the brief says so. A decision with a recommendation is a `confirm` item, the recommendation its
`suggested` value.
"""
from __future__ import annotations

from . import common as C

OPEN, DONE, BLOCKED = "open", "done", "blocked"


# ------------------------------------------------------------------ what closes a criterion

ANSWERS_FILE = "your-input/answers.yaml"
CORRECTIONS_FILE = "your-input/corrections.yaml"


def by_answer(q: str) -> dict:
    from .preflight import answer_key
    return {"kind": "answer", "file": ANSWERS_FILE, "field": answer_key(q), "key": q}


def by_approval() -> dict:
    return {"kind": "approval", "file": ANSWERS_FILE, "field": "signed_for", "key": "approval"}


def by_review(uid: str, short: str | None = None) -> dict:
    return {"kind": "review", "file": ANSWERS_FILE, "field": review_key(short or uid), "key": uid}


def review_key(short: str) -> str:
    return f"spec.{short}"


def by_outcome(uid: str, short: str | None = None) -> dict:
    return {"kind": "outcome", "file": CORRECTIONS_FILE,
            "field": f"outcomes.{short or uid} (or the answer that unblocks it)", "key": uid}


def by_decision(key: str, question: str, options: list[str] | None = None, recommended: str | None = None,
                why: str | None = None, blocks: list[str] | None = None, plain: str | None = None) -> dict:
    """A decision in answers.yaml. `question` is the full one the pages show; `plain` the one-line question
    answers.yaml shows, when the full one does not shorten well by itself (your_input.question_of)."""
    return {"kind": "decision", "file": ANSWERS_FILE, "field": key, "key": key, "question": question,
            "options": options or [], "recommended": recommended, "why": why, "blocks": blocks or [],
            "plain": plain}


def by_gate(wave: int) -> dict:
    """An earlier wave's exit: a gate the migration closes, not a question (no file a person edits)."""
    return {"kind": "gate", "file": None, "field": f"wave-exit.{wave}", "key": f"wave-exit.{wave}", "wave": wave,
            "when": gate_when(wave)}


def gate_when(wave: int) -> str:
    return f"closes when Wave {wave} is migrated and verified"


def is_gate(e: dict) -> bool:
    return (e.get("closes") or {}).get("kind") == "gate"


def key_part(s: str) -> str:
    """A readable piece of a decision key: lower case, words joined by '-'."""
    import re
    return re.sub(r"-{2,}", "-", C.slug(str(s).lower().replace(" ", "-"))).strip("-.") or "x"


# ------------------------------------------------------------------ decisions as answers.yaml items

# How the decisions are grouped in answers.yaml and on the front page: key prefix -> title.
FAMILIES = {
    "edition": "Kestra edition", "endpoint": "Endpoints and credentials", "client-route": "Client routes",
    "auth": "Authentication", "tls": "Certificate checks", "writes": "Read and write classification",
    "reads-only": "Read and write classification", "lifecycle": "Lifecycle groups", "state-store": "State",
    "timeout": "Blocking steps: timeouts and failure", "failure": "Blocking steps: timeouts and failure",
    "cancel": "Blocking steps: timeouts and failure", "typed-inputs": "Typed inputs", "defect": "Suspected defects",
    "question": "Spec questions", "missing": "Missing from the export", "mappings": "vRA replacement",
    "gap": "vRA replacement", "app": "vRA replacement", "existing-deployments": "vRA replacement",
    "ip-allocation": "vRA replacement", "deployment-record": "vRA replacement", "day2-scale": "vRA replacement",
}


def family(key: str) -> str:
    return key.split(".", 1)[0]


def family_title(key: str) -> str:
    return FAMILIES.get(family(key), family(key).replace("-", " ").capitalize())


def entries(plan: dict) -> dict[str, dict]:
    """Every decision the plan asks for: key -> {question, options, recommended, why, blocks, waves, closes}."""
    out: dict[str, dict] = {}
    for w in plan["waves"]:
        for e in w["entry_criteria"]:
            c = e.get("closes") or {}
            if c.get("kind") != "decision":
                continue
            d = out.setdefault(c["key"], {"question": c["question"], "options": list(c.get("options") or []),
                                          "recommended": c.get("recommended"), "why": c.get("why"),
                                          "plain": c.get("plain"),
                                          "blocks": list(c.get("blocks") or []), "waves": [], "closes": [],
                                          "also_answers": list(c.get("also_answers") or [])})
            if w["wave"] not in d["waves"]:
                d["waves"].append(w["wave"])
            d["closes"].append(f"Wave {w['wave']}: {e['criterion']}")
            for x in c.get("also_answers") or []:
                if x not in d["also_answers"]:
                    d["also_answers"].append(x)
    return out


def suggestion(d: dict) -> str | None:
    """What the code suggests for a decision: its recommendation, or 'confirmed' where that is an option."""
    if d.get("recommended"):
        return d["recommended"]
    if d.get("options") and d["options"][0] == "confirmed":
        return "confirmed"
    return None


def input_items(asked: dict[str, dict]) -> list[dict]:
    """The decisions as answers.yaml items: confirm where the code suggests a value, else answer."""
    from . import your_input as Y
    out = []
    for k, d in sorted(asked.items(), key=lambda kv: (list(FAMILIES).index(family(kv[0])) if family(kv[0]) in FAMILIES
                                                      else len(FAMILIES), kv[0])):
        sug = suggestion(d)
        ask = d["question"]
        if d.get("also_answers"):
            aa = d["also_answers"]
            ask += (" This also answers the specs' question" + ("s" if len(aa) > 1 else "") + ": "
                    + "; ".join(f"\"{q[:200]}{'...' if len(q) > 200 else ''}\"" for q in aa[:2])
                    + (f"; and {len(aa) - 2} more (open-questions page, Asked once)" if len(aa) > 2 else "") + ".")
        it = Y.item(k, "confirm" if sug else "answer", ask, why=d.get("why"), unblocks=d["closes"],
                    options=d["options"], suggested=sug, family=family(k), blocks=d.get("blocks"),
                    question=d.get("plain"))
        it["family_title"] = family_title(k)
        out.append(it)
    return out


def load(asked: dict[str, dict], loaded: dict | None = None) -> dict:
    """
    {"path", "exists", "by_key": {key: {state, decision, decided_by, date, comment, why}}, "missing", "warnings"}.
    state: "decided" (closes its criteria), "blocked" (decided on an option that stops the wave), "open".
    """
    from . import your_input as Y
    loaded = loaded if loaded is not None else Y.load()
    out = {"path": loaded["path"], "exists": loaded["exists"], "by_key": {}, "missing": [],
           "warnings": list(loaded["warnings"])}
    for k, d in asked.items():
        sug = suggestion(d)
        st = Y.state(k, loaded, {"group": "confirm" if sug else "answer", "suggested": sug, "fixed": {}})
        rec = {"decision": st["answer"] if st["state"] == "answered" else None, "decided_by": st["by"],
               "date": st["date"], "comment": st["note"]}
        if st["state"] != "answered":
            rec.update(state="open", why=st["why"])
        else:
            blocks = [b.lower() for b in d.get("blocks") or []]
            rec.update(state="blocked" if st["answer"].lower() in blocks else "decided",
                       why=st["why"] + (f" ({rec['comment']})" if rec["comment"] else ""))
        out["by_key"][k] = rec
    return out


# ------------------------------------------------------------------ closing

def close(plan: dict, *, answers: dict, reviews: dict, decisions: dict, approval: dict) -> None:
    """Set each entry criterion's status (open, done, blocked) and note from the files that close it."""
    items = {q["id"]: q for q in answers.get("items") or []}
    for w in plan["waves"]:
        for e in w["entry_criteria"]:
            c = e.get("closes") or {}
            kind = c.get("kind")
            e.pop("note", None)
            e["status"] = OPEN
            if kind == "approval":
                covered = approval.get("signed") and (approval.get("scope") == "full" or
                                                      (approval.get("scope") == "wave-1" and w["wave"] == 1))
                if covered:
                    e.update(status=DONE, note="signed for " + ("the full plan" if approval["scope"] == "full"
                                                                 else "Wave 1"))
                elif approval.get("signed"):
                    e["note"] = f"the signature covers {approval['scope']} only"
            elif kind == "answer":
                q = items.get(c["key"]) or {}
                if q.get("status") == "answered":
                    ans = " ".join(str(q.get("answer") or "").split())
                    e.update(status=DONE, note=f"answered: \"{ans[:200]}{'...' if len(ans) > 200 else ''}\"")
                elif q.get("status") == "partly answered":
                    e["note"] = "partly answered"
            elif kind == "review":
                rv = (reviews.get("by_uid") or {}).get(c["key"]) or {}
                if rv.get("state") == "reviewed":
                    e.update(status=DONE, note=rv["why"] + (f": {rv['comment']}" if rv.get("comment") else ""))
                elif rv.get("state") == "stale":
                    e["note"] = rv["why"]
            elif kind == "decision":
                d = (decisions.get("by_key") or {}).get(c["key"]) or {}
                if d.get("state") == "decided":
                    e.update(status=DONE, note=d["why"])
                elif d.get("state") == "blocked":
                    e.update(status=BLOCKED, note=d["why"] + "; this stops the wave")
                elif d.get("state") == "open" and d.get("why") != "not answered":
                    e["note"] = d["why"]
            elif kind == "gate":
                e["note"] = c["when"]


def summary(plan: dict) -> dict:
    """Per wave: criteria by status, and the open ones grouped by the file that closes them."""
    out = []
    for w in plan["waves"]:
        st = {OPEN: 0, DONE: 0, BLOCKED: 0}
        by_file: dict[str, int] = {}
        for e in w["entry_criteria"]:
            st[e["status"]] = st.get(e["status"], 0) + 1
            if e["status"] != DONE and not is_gate(e):
                f = (e.get("closes") or {}).get("file", "?")
                by_file[f] = by_file.get(f, 0) + 1
        out.append({"wave": w["wave"], "status": st, "open_by_file": by_file})
    return {"waves": out}


def where(e: dict) -> str:
    """'`file` → `key`' for a criterion, as the brief shows it."""
    c = e.get("closes") or {}
    if not c:
        return ""
    if c.get("kind") == "gate":
        return c["when"]
    return f"`{c['file']}` → `{c['field']}`"
