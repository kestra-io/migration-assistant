"""
Stage 0, Preflight: is this export ready to assess, and what can't it tell us?

Two halves, as in Anthropic's /modernize-preflight:

1. Checks the export answers by itself: formats, element counts, whether
   execution history is present, the source vRO version from dunes-meta-inf,
   the plugins it was built against, and what kinds of entry point
   (catalog items, subscriptions, schedules, policies) are in it.
2. Questions the export cannot answer. They go in `your-input/answers.yaml`
   (your_input.py), the one file a customer fills in, which keeps every answer
   when it is rewritten: on every later run it is read back, and each question
   with no answer is carried by Assess and Brief as an open item.
"""
from __future__ import annotations

import os
import re
from collections import Counter
from typing import Any

from .. import entry_points, outdir
from . import common as C

# Adapted from Anthropic's Check 0. Theirs asks about scope, local build and
# test, bespoke build infrastructure, prior attempts and what is off limits.
# For vRO the build questions do not apply (a workflow is not built), and the
# things that decide a migration are who calls the workflows and whether there
# is somewhere safe to run them.
QUESTIONS: list[dict[str, Any]] = [
    {"id": "Q1", "topic": "scope",
     "question": "Is this export the whole vRO estate? If not, what is missing, and how many vRO nodes or clusters does the estate run on?",
     "why": "Every count in Assess and Brief is only as complete as the export. A partial export hides callers and callees.",
     "read_by": ["assess: unexported calls", "brief: retire candidates, waves"],
     "options": ["yes, the whole estate, from one vRO cluster", "the whole estate, from several clusters: I will name them",
                 "only part of it: I will say what is missing"]},
    {"id": "Q2", "topic": "external callers",
     "question": "What starts these workflows from outside vRO? Two ways to answer, and they can be combined: put the vRA content export (a .vra-ng from Build Tools) in the export folder, and the tool reads its catalog items, cloud templates and event broker subscriptions; and list in your-input/entry-points.yaml whatever that export does not hold, or everything if there is no export: vRO scheduled tasks and policies, the ITSM (for example ServiceNow), and scripts calling the vRO REST API. Set complete: true there once the list is whole.",
     "why": "None of these are in a vRO package. Without them every workflow nobody calls in the export looks like an entry point or dead code, and the two cannot be told apart.",
     "read_by": ["brief: retire pass, entry points, rebuild outcome"]},
    {"id": "Q3", "topic": "non-production targets",
     "question": "For each target system (vCenter, NSX, Avi, Nuage VSD, vCloud Director, IPAM, ITSM), is there a non-production instance a converted flow can run against?",
     "why": "A wave that writes to a target cannot be verified without somewhere safe to write. Read-only waves can go first either way.",
     "read_by": ["brief: wave entry criteria"]},
    {"id": "Q4", "topic": "off limits",
     "question": "Is anything in this export off limits for this migration (owned by another team, frozen, vendor supplied, or due to be replaced)?",
     "why": "Off-limits workflows are dropped from the waves, and whatever calls them keeps a dependency on vRO.",
     "read_by": ["brief: waves, blocked list"],
     "options": ["nothing is off limits", "some of it is: I will name it"],
     "suggested": "nothing is off limits",
     "plain": "Is any of it off limits (another team's, frozen, vendor supplied, or being replaced)?"},
    {"id": "Q5", "topic": "prior attempts",
     "question": "Has anyone tried to migrate or rewrite any of this before? What happened?",
     "why": "A previous attempt usually found the hard part already. It belongs in the brief's risks.",
     "read_by": ["brief: risks"],
     "options": ["no earlier attempt", "yes: I will say what happened"],
     "plain": "Has anyone tried to migrate or rewrite any of it before? What happened?"},
    {"id": "Q6", "topic": "owners",
     "question": "Who owns each functional area (for example load balancing, network provisioning, tenant onboarding)? Name a person or team per area.",
     "why": "Open questions, retire candidates and wave approvals need someone to answer them. The brief lists open questions by area.",
     "read_by": ["brief: open questions, approval"],
     "plain": "Who owns each functional area? A person or team for each."},
    {"id": "Q7", "topic": "execution history",
     "question": "Can you export twelve months of workflow run history (vRO workflow tokens, or the vRA deployment history), with workflow ID, start time and result?",
     "why": "The retire rule is 'no executions in twelve months'. Without history it cannot be tested, only guessed from the call graph.",
     "read_by": ["brief: retire pass"],
     "options": ["yes, I will put it in the export folder", "no, there is no history to export"],
     "plain": "Can you export twelve months of run history: workflow, start time and result?"},
    {"id": "Q8", "topic": "plugin inventory",
     "question": "Which vRO plugins and versions are installed on each node, and are any custom or in-house plugins in use?",
     "why": "A plugin object type Kestra cannot reach blocks every workflow that uses it. The export only records plugins by name where dunes-meta-inf lists them.",
     "read_by": ["assess: target inventory"]},
    {"id": "Q9", "topic": "vRA front end",
     "question": "If vRA starts any of these workflows: will it stay as the front end, with its catalog items and subscriptions calling Kestra, or be replaced, with Kestra Apps and flows taking over its catalog items, cloud templates and subscriptions? Answer keep, replace, or replace with a bridge until then (vRA's subscriptions call Kestra through webhooks while the Apps and flows are built, then vRA is switched off).",
     "why": "The trigger plan differs: kept, every subscription calls a Kestra webhook (and a blocking one waits for the result); replaced, each catalog item becomes a Kestra App and each cloud template a flow whose steps are the old lifecycle events; replaced with a bridge, the converted flows run behind webhooks first and vRA goes once the replacement is ready. Unanswered, the brief shows both.",
     "read_by": ["brief: trigger plan, wave entry criteria"],
     "options": ["keep", "replace", "replace with a bridge until then"],
     "suggested": "replace",
     "plain": "Will vRA stay as the front end and call Kestra, or will Kestra replace it? (keep, replace, or replace "
              "with a bridge until then)"},
]

# ------------------------------------------------------------------ questions from what is on record

# The systems Q3 names when nothing is on record yet. Once Assess has found the estate's systems, Q3 names
# those instead, and naming one of these the tool did not find would contradict it (contradictions()).
GENERIC_SYSTEMS = ["vCenter", "NSX", "Avi", "Nuage VSD", "vCloud Director", "IPAM", "ITSM"]
FOUND = "Found by the tool:"
NOT_SYSTEMS = {"vro-platform", "rest-host", "vra"}


def facts(exp: dict | None = None, assess: dict | None = None) -> dict:
    """What the tool already knows that a question would otherwise ask: the export check (preflight.json,
    or `exp`) and, once Assess has run, its findings (assess.json, or `assess`). Every key is empty when the
    stage that finds it has not run."""
    if exp is None:
        p = os.path.join(C.PRE, "preflight.json")
        exp = (C.read_json(p).get("export") if os.path.exists(p) else None) or {}
    if assess is None:
        p = os.path.join(C.ASSESS, "assess.json")
        assess = C.read_json(p) if os.path.exists(p) else {}
    model = ((exp.get("entry_points") or {}).get("model") or {})
    read = [x for x in exp.get("vra_exports") or [] if not x.get("error")]
    vra = {"exports": [x.get("label") for x in read],
           "catalog_items": len(model.get("catalog_items") or []),
           "cloud_templates": len(model.get("cloud_templates") or []),
           "subscriptions": len(model.get("subscriptions") or []),
           "day2_actions": len(model.get("day2_actions") or [])} if read else None

    systems = []                              # (name, how it was found)
    ids = {}                                  # name -> the system's key (a target of assess.json)
    for k, t in sorted((assess.get("targets") or {}).items()):
        if k not in NOT_SYSTEMS:
            systems.append((t.get("name") or k, "the workflows' code"))
            ids[t.get("name") or k] = k
    have = {n for n, _ in systems}
    for r in (assess.get("plugin_coverage") or {}).get("rows") or []:
        if r.get("key") in NOT_SYSTEMS or not r.get("resource_type") or (r.get("system") or r["key"]) in have:
            continue
        systems.append((r.get("system") or r["key"], f"the cloud templates ({r['resource_type']})"))
        ids[r.get("system") or r["key"]] = r["key"]

    # vRO plugins the code uses: declared in dunes-meta-inf, plugin object types, the HTTP-REST plugin's
    # classes, and a plugin client's calls
    from . import targets as T
    pm = T.load_plugin_map() if assess else {"targets": {}}
    plugins: dict[str, set] = {}

    def add(name, why):
        plugins.setdefault(name, set()).add(why)
    for s in exp.get("sources") or []:
        for p in s.get("used_plugins") or []:
            add(p.strip(), "declared in dunes-meta-inf")
    wf = assess.get("workflows") or {}
    types = {t for w in wf.values() for t in w.get("plugin_types") or []}
    types |= {t for a in assess.get("actions") or [] for t in (a.get("params") or {}).values() if ":" in (t or "")}
    for t in sorted(types):
        tgt = T.target_for_type(t)
        vp = ((pm.get("targets") or {}).get(tgt) or {}).get("vro_plugin") if tgt else None
        add(vp or f"the plugin of type {t.split(':')[0]}", f"object type {t}")
    rest = sorted({a["name"] for a in assess.get("actions") or []
                   if any(o.get("kind") == "rest client" or o.get("target") == "rest-host" for o in a.get("ops") or [])})
    if rest:
        add("HTTP-REST plugin (built in)", "REST requests built in " + ", ".join(rest[:4])
            + (f" and {len(rest) - 4} more" if len(rest) > 4 else ""))
    for w in list(wf.values()) + list(assess.get("actions") or []):
        for o in w.get("ops") or []:
            k = o.get("kind") or ""
            tgt = "avi-controller" if "(Avi)" in k else o.get("target")
            if k in ("sdk", "plugin batch") or k.startswith("plugin endpoint"):
                vp = ((pm.get("targets") or {}).get(tgt) or {}).get("vro_plugin")
                if vp and not vp.startswith("("):
                    add(vp, f"calls such as {o.get('op')}")
    hist = (exp.get("execution_history") or {}).get("files") or []
    return {"vra": vra, "systems": systems, "system_ids": ids, "plugins": {k: sorted(v) for k, v in sorted(plugins.items())},
            "history_files": hist, "assessed": bool(assess)}


def _n(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def questions_for(f: dict) -> list[dict]:
    """QUESTIONS as asked of this estate: a question the export or Assess already answers in part asks only
    for the rest, says what was found (`found`), and never asks for something on record."""
    out = []
    for q in QUESTIONS:
        q = dict(q)
        if q["id"] == "Q2" and not f.get("vra"):
            q["plain"] = ("No vRA export was found. What starts these workflows from outside vRO? List it in "
                          "entry-points.yaml next to this file, or put the vRA export in the export folder and run "
                          "again.")
        if q["id"] == "Q2" and f.get("vra"):
            v = f["vra"]
            got = ", ".join(x for x in (_n(v["catalog_items"], "catalog item"), _n(v["cloud_templates"], "cloud template"),
                                        _n(v["subscriptions"], "event subscription"),
                                        _n(v["day2_actions"], "day-2 action") if v["day2_actions"] else "") if x)
            q["question"] = ("What else starts these workflows from outside vRO, that a vRA export cannot hold: vRO "
                             "scheduled tasks and policies, the ITSM (for example ServiceNow), scripts or systems "
                             "calling the vRO REST API? Name them in the answer, or answer none.")
            q["plain"] = ("Besides vRA, what starts these workflows (vRO schedules or policies, an ITSM, scripts on the "
                          "vRO REST API)? Or none.")
            q["found"] = (f"the vRA export ({', '.join(v['exports'])}) was read: {got}. Its catalog items and "
                          "subscriptions are on record.")
        if q["id"] == "Q3" and f.get("systems"):
            names = [n for n, _ in f["systems"]]
            q["question"] = ("For each system the tool found (" + ", ".join(names) + "), is there a "
                             "non-production instance a converted flow can run against? Name any other system the "
                             "workflows reach.")
            q["found"] = "; ".join(f"{n} (from {how})" for n, how in f["systems"]) + "."
            q["systems"] = [{"key": (f.get("system_ids") or {}).get(n, n), "name": n} for n in names]
        if q["id"] == "Q7" and f.get("history_files"):
            hs = f["history_files"]
            q["question"] = ("The estate folder holds files that look like run history. Do they cover twelve months "
                             "for every workflow, with workflow ID, start time and result? If not, export the rest.")
            q["found"] = f"{_n(len(hs), 'file')}: " + ", ".join(hs[:5]) + (" ..." if len(hs) > 5 else "") + "."
        if q["id"] == "Q8" and f.get("plugins"):
            q["question"] = ("The code uses these vRO plugins: " + ", ".join(f["plugins"]) + ". Is that the list? "
                             "Correct it if not, and add the installed version per node and any custom or in-house "
                             "plugin.")
            q["suggested"] = ", ".join(f["plugins"])
            q["found"] = "; ".join(f"{p} ({', '.join(w)})" for p, w in f["plugins"].items()) + "."
        out.append(q)
    return out


def contradictions(questions: list[dict], f: dict) -> list[str]:
    """Each place a question asks for something the tool already has. Empty when none does."""
    by = {q["id"]: q for q in questions}
    out = []
    q2 = (by.get("Q2") or {}).get("question", "")
    if f.get("vra") and re.search(r"\.vra-ng|content export|put the vRA", q2, re.I):
        out.append("Q2 asks for the vRA export, which was read: " + ", ".join(f["vra"]["exports"]))
    q3 = (by.get("Q3") or {}).get("question", "")
    if f.get("systems"):
        found = " ".join(n for n, _ in f["systems"]).lower()
        extra = [g for g in GENERIC_SYSTEMS if re.search(r"\b" + re.escape(g) + r"\b", q3)
                 and g.lower() not in found]
        if extra:
            out.append("Q3 lists systems the tool did not find (" + ", ".join(extra) + ") instead of those it did")
    if f.get("plugins") and not (by.get("Q8") or {}).get("found"):
        out.append("Q8 asks which plugins are used without saying which the code uses: " + ", ".join(f["plugins"]))
    if f.get("history_files") and not (by.get("Q7") or {}).get("found"):
        out.append("Q7 asks for run history while the estate folder holds files that look like it")
    return out


META_ENTRY = re.compile(r'<entry key="([^"]+)">([^<]*)</entry>|<(comment)>([^<]*)</comment>')


def _meta(text: str) -> dict:
    out = {}
    for k, v, ck, cv in META_ENTRY.findall(text):
        if ck:
            out["comment"] = cv
        else:
            out[k] = v
    return out


# ------------------------------------------------------------------ export

def _meta_from_package(path: str) -> dict:
    from kestra_migration_assistant.vra import safety
    from kestra_migration_assistant.vra.parse import formats
    with safety.open_zip(path) as z:
        for n in z.namelist():
            if n == "dunes-meta-inf":
                return _meta(formats.read_xml_text(safety.read_member(z, n)))
    return {}


def _meta_from_tree(path: str) -> tuple[dict, str | None]:
    for cand in ("META-INF/dunes-meta-inf.xml", "dunes-meta-inf.xml"):
        p = os.path.join(path, cand)
        if os.path.isfile(p):
            with open(p, encoding="utf-8", errors="replace") as f:
                return _meta(f.read()), cand
    return {}, None


WF_ID = re.compile(r'<(?:\w+:)?workflow\b[^>]*?\sid="([^"]+)"')
HISTORY_HINTS = re.compile(r"(token|execution|run-history|workflow-run|logs?)(/|\.json|\.csv|$)", re.I)
ENTRY_KINDS = {
    "PolicyTemplate": "policy",
    "Policy": "policy",
    "Task": "scheduled task",
    "ScheduledTask": "scheduled task",
}


def check_export() -> dict:
    from kestra_migration_assistant.vra import safety
    from kestra_migration_assistant.vra.parse import formats

    sources: list[dict[str, Any]] = []
    totals: Counter[str] = Counter()
    input_forms = 0
    history_files = []
    failures = []
    wf_ids: dict[str, dict] = {}
    inputs, _ = C.sanitized_inputs()
    sanitized = C.sanitize_report()
    unreadable = sanitized.get("unreadable") or {}
    for label, path in inputs.items():
        if label in unreadable:
            failures.append({"source": label, "path": C.rel(C.export_of(path)), "error": unreadable[label]})
            continue
        try:
            elements, summary = formats.read_any(path)
        except Exception as exc:
            failures.append({"source": label, "path": C.rel(C.export_of(path)), "error": f"{type(exc).__name__}: {exc}"})
            continue
        kinds = Counter(e.kind for e in elements)
        totals.update(kinds)
        for e in elements:
            if e.kind == "Workflow":
                m = WF_ID.search(e.xml[:8000]) if e.xml else None
                wf_ids[(m.group(1) if m else "") or e.uuid] = {"name": e.name, "source": label}
        input_forms += sum(1 for e in elements if e.input_form)
        if os.path.isdir(path):
            meta, meta_file = _meta_from_tree(path)
            for dp, dirs, files in os.walk(path):
                outdir.prune(dp, dirs)
                for fn in files:
                    rel = os.path.relpath(os.path.join(dp, fn), path)
                    if HISTORY_HINTS.search(rel) and not rel.endswith(".xml"):
                        history_files.append(f"{label}/{rel}")
        elif safety.is_zip(path):
            meta, meta_file = _meta_from_package(path), "dunes-meta-inf"
            with safety.open_zip(path) as z:
                history_files += [f"{label}!{n}" for n in z.namelist()
                                  if HISTORY_HINTS.search(n) and not n.startswith(("elements/", "signatures/"))]
        else:
            meta, meta_file = {}, None                      # one exported element (.xml): no package metadata

        version = meta.get("vso-version") or None
        plugins = [p for p in (meta.get("used-plugins") or "").split(",") if p.strip()]
        raw_name = meta.get("pkg-name") or ""
        sources.append({
            "source": label,
            "path": C.rel(C.export_of(path)),
            "format": summary.get("format"),
            # The single .workflow reader has never run on real data.
            "experimental": summary.get("format") == "workflow-export",
            "signed": True if label in (sanitized.get("signed") or []) else summary.get("signed"),
            "element_counts": dict(sorted(kinds.items())),
            "dunes_meta_inf": meta_file,
            "package_name": raw_name if "${" not in raw_name else None,
            "package_name_is_template": "${" in raw_name,
            "vro_version": version,
            "vro_build": meta.get("vso-build-nb") or None,
            "used_plugins": plugins,
            # host:port, read from the sanitized copy, where the host is a placeholder (&lt;IP-01&gt;:8281)
            "build_host_recorded": bool(re.match(r"^(?:[\w.-]+|&lt;[A-Z]+-\d+&gt;|<[A-Z]+-\d+>):\d+$",
                                                 (meta.get("comment") or "").strip())),
        })

    versions = sorted({s["vro_version"] for s in sources if s["vro_version"]})
    entry_elements = {k: v for k, v in totals.items() if k in ENTRY_KINDS}
    ep = entry_points.for_estate(wf_ids)
    vra_found = [x for x in ep["exports"] if not x.get("error")]
    return {
        "sources": sources,
        "failures": failures,
        "ignored_files": list(C.IGNORED),
        "totals": dict(sorted(totals.items())),
        "input_forms": input_forms,
        "vro_versions": versions,
        "sources_without_version": [s["source"] for s in sources if not s["vro_version"]],
        "execution_history": {"present": bool(history_files), "files": history_files},
        "entry_point_elements": entry_elements,
        # vRA content never ships in a vRO package; it comes from a .vra-ng or entry-points.yaml.
        "catalog_items_present": bool(ep["model"]["catalog_items"]),
        "subscriptions_present": bool(ep["model"]["subscriptions"]),
        "schedules_present": bool(entry_elements.get("Task") or entry_elements.get("ScheduledTask")
                                  or any("schedule" in (t.get("kind") or "") for t in ep["model"]["other_triggers"])),
        "vra_exports": [{k: v for k, v in x.items() if k != "raw"} | {"path": C.rel(x["path"])}
                        for x in ep["exports"]],
        "vra_exports_read": len(vra_found),
        "workflow_ids": len(wf_ids),
        "entry_points": entry_points.public(ep),
    }


# ------------------------------------------------------------------ answers

def _parse_answers(text: str) -> dict[str, str]:
    """Answer text per question id in an answers.md of the 2c/2d layout (your_input.migrate reads it)."""
    out: dict[str, str] = {}
    parts = re.split(r"^###\s+(Q\d+)\b.*$", text, flags=re.M)
    # parts = [preamble, id1, body1, id2, body2, ...]
    for i in range(1, len(parts) - 1, 2):
        qid, body = parts[i], parts[i + 1]
        m = re.search(r"^Answer:[ \t]*(.*)\Z", body, flags=re.M | re.S)
        ans = (m.group(1) if m else "").strip()
        ans = re.sub(r"<!--.*?-->", "", ans, flags=re.S).strip()
        out[qid] = ans
    return out


def is_open(answer: str | None) -> bool:
    from .your_input import is_open as _open
    return _open(answer)


def _q2_from_entry_points() -> str:
    """Q2 is partly answered by any entry point on record, and fully once entry-points.yaml says complete."""
    try:
        return entry_points.q2_status(entry_points.for_estate())
    except Exception:
        return "open"


def _q9_from_entry_points() -> str:
    """Q9 only applies when vRA starts something: with no vRA content on record it is not asked."""
    try:
        m = entry_points.for_estate()["model"]
    except Exception:
        return "open"
    vra = any(m.get(k) for k in ("subscriptions", "catalog_items", "cloud_templates", "day2_actions"))
    return "open" if vra else "not applicable"


def answer_key(qid: str) -> str:
    """A question's key in your-input/answers.yaml: `q3-non-production-targets`."""
    q = next((x for x in QUESTIONS if x["id"] == qid), None)
    return f"{qid.lower()}-{C.slug(q['topic'].lower().replace(' ', '-'))}" if q else qid.lower()


def load_answers(f: dict | None = None) -> dict:
    """The preflight answers, from your-input/answers.yaml. Used by every later stage. The questions are as
    asked of this estate (questions_for), from `f` or from the facts on disk."""
    from . import your_input as Y
    loaded = Y.load()
    items = []
    for q in questions_for(facts() if f is None else f):
        key = answer_key(q["id"])
        it: dict[str, Any] = {"group": "confirm" if q.get("suggested") else "answer", "suggested": q.get("suggested"), "fixed": {}}
        st = Y.state(key, loaded, it)
        a = st["answer"] or "" if st["state"] == "answered" else ""
        status = "answered" if st["state"] == "answered" else "open"
        if q["id"] == "Q2" and status == "open":
            status = _q2_from_entry_points()
        if q["id"] == "Q9" and status == "open":
            status = _q9_from_entry_points()
        items.append({**q, "key": key, "answer": a, "status": status, "by": st["by"], "date": st["date"],
                      "open": status not in ("answered", "not applicable")})
    return {"path": C.rel(C.ANSWERS), "exists": os.path.exists(C.ANSWERS),
            "items": items, "open": [i["id"] for i in items if i["open"]]}


def input_items(f: dict) -> list[dict]:
    """Preflight's questions as items of your-input/answers.yaml. A question that does not apply is not asked."""
    from . import your_input as Y
    out = []
    for q in questions_for(f):
        if q["id"] == "Q9" and _q9_from_entry_points() == "not applicable":
            continue
        out.append(Y.item(answer_key(q["id"]), "confirm" if q.get("suggested") else "answer",
                          f"{q['id']}. {q['question']}", title=f"{q['id']} {q['topic']}", why=q["why"],
                          unblocks=list(q.get("read_by") or []), suggested=q.get("suggested"),
                          found=q.get("found"), family="preflight", link="report/readiness.html",
                          question=q.get("plain"), systems=q.get("systems"), options=q.get("options")))
    for it in out:
        it["family_title"] = "Preflight: what the export cannot say"
    return out


def write_questions(f: dict) -> bool:
    """Register the questions as asked of this estate in your-input/answers.yaml (every answer kept).
    True when the file was created by this call."""
    from . import your_input as Y
    created = not os.path.exists(C.ANSWERS)
    Y.register("preflight", input_items(f))
    return created


def refresh_questions() -> dict:
    """After Assess: ask the questions again from what it found (the systems, the plugins the code uses), in
    answers.yaml, preflight.json and readiness.md. No export is read again. Returns what changed."""
    from . import your_input as Y
    pj = os.path.join(C.PRE, "preflight.json")
    if not os.path.exists(pj):
        return {"refreshed": False, "contradictions": []}
    r = C.read_json(pj)
    f = facts(r.get("export") or {})
    before = [it for it in Y.registry().get("preflight") or []]
    write_questions(f)
    r["answers"] = load_answers(f)
    r["question_contradictions"] = contradictions(r["answers"]["items"], f)
    C.write_json(pj, r)
    C.write_text(os.path.join(C.PRE, "readiness.md"), C.no_em_dashes(_readiness_md(r)))
    return {"refreshed": before != (Y.registry().get("preflight") or []), "contradictions": r["question_contradictions"],
            "found": [q["id"] for q in r["answers"]["items"] if q.get("found")]}


# ------------------------------------------------------------------ report

def write_entry_points(has_vra: bool) -> bool:
    """your-input/entry-points.yaml only when the estate has no vRA export: a template, rewritten only while it
    is untouched. With a vRA export an untouched template goes (Q2 is answered in answers.yaml); a file a person
    filled in is always read and never removed. True when the template was written."""
    path = C.ENTRY_POINTS
    if entry_points.is_filled(path):
        return False
    if has_vra:
        if os.path.exists(path):                # an untouched template (is_filled said so)
            os.remove(path)
        return False
    if os.path.exists(path) and open(path).read() == entry_points.template():
        return False
    C.write_text(path, entry_points.template())
    return True


def run() -> dict:
    C.ensure(C.PRE)
    exp = check_export()
    # The questions are written from what is on record: this export check and, once Assess has run, its
    # findings (refresh_questions runs again after Assess).
    f = facts(exp)
    ep_created = write_entry_points(bool(f.get("vra")))
    created = write_questions(f)
    ans = load_answers(f)
    wf_total = exp["totals"].get("Workflow", 0)

    checks = []

    def chk(name, status, found, fix=""):
        checks.append({"check": name, "status": status, "found": found, "fix": fix})

    fmts = Counter(s["format"] for s in exp["sources"])
    experimental = [s["source"] for s in exp["sources"] if s["experimental"]]
    chk("Formats", "gap" if exp["failures"] else "ready",
        ", ".join(f"{n} {k}" for k, n in sorted(fmts.items())) + f" across {len(exp['sources'])} input(s); "
        + (f"{len(exp['failures'])} could not be read: " + "; ".join(f"{f['source']} ({f['error']})" for f in exp["failures"])
           if exp["failures"] else "all read with 0 failures"),
        "Re-export the unreadable input (INPUTS.md)." if exp["failures"] else "")
    if experimental:
        chk("Single .workflow files", "experimental",
            f"{len(experimental)} input(s) are single-workflow exports ({', '.join(experimental)}). That reader has "
            "never run on real data, so check their counts by hand.",
            "Prefer a .package containing these workflows (INPUTS.md).")
    if exp["ignored_files"]:
        chk("Other files in the estate folder", "ignored",
            ", ".join(exp["ignored_files"]), "Not an export format, so not read. Run history goes to Q7.")
    chk("Element counts", "ready",
        ", ".join(f"{v} {k}" for k, v in exp["totals"].items()))
    chk("Execution history", "missing" if not exp["execution_history"]["present"] else "ready",
        "none in the export" if not exp["execution_history"]["present"] else
        f"{len(exp['execution_history']['files'])} file(s)",
        "Export run history (Q7). Until then the retire rule is untestable.")
    vtxt = ", ".join(exp["vro_versions"]) or "none"
    chk("Source vRO version", "gap" if exp["sources_without_version"] else "ready",
        f"{vtxt} (from dunes-meta-inf)" + (f"; no version recorded for {', '.join(exp['sources_without_version'])}"
                                           if exp["sources_without_version"] else ""),
        "Confirm the version per node (Q1, Q8). Sources built from source control carry no version.")
    plug = sorted({p for s in exp["sources"] for p in s["used_plugins"]})
    chk("Plugins declared", "gap",
        (", ".join(plug) or "none") + " declared in dunes-meta-inf; other plugins are only visible from object types in the XML",
        "Confirm installed plugins (Q8).")
    ep = exp["entry_points"]
    model, res = ep["model"], ep["resolution"] or {"resolved": 0, "unresolved": [], "no_workflow": 0}
    n_ep = entry_points.total(model)
    for x in exp["vra_exports"]:
        if x.get("error"):
            chk("vRA export", "gap", f"{x['label']} could not be read: {x['error']}",
                "Re-export it with Build Tools (INPUTS.md), or list its entry points in entry-points.yaml.")
        else:
            chk("vRA export", "ready" if not x["warnings"] else "check",
                f"{x['label']} ({x['format']}, {x['files']} files): "
                + (", ".join(f"{v} {k}" for k, v in x["counts"].items() if v) or "no content")
                + (f"; {len(x['warnings'])} warning(s)" if x["warnings"] else "")
                + (f"; {len(x['unknown'])} unknown folder(s) or file(s): "
                   + ", ".join(u["path"] for u in x["unknown"]) if x["unknown"] else ""),
                "See Entry points below." if x["warnings"] or x["unknown"] else "")
    if n_ep:
        chk("Entry points", "ready" if not res["unresolved"] and not ep["conflicts"] else "check",
            f"{len(model['subscriptions'])} subscription(s), {len(model['catalog_items'])} catalog item(s), "
            f"{len(model['cloud_templates'])} cloud template(s), {len(model['day2_actions'])} day-2 action(s), "
            f"{len(model['other_triggers'])} other trigger(s); {res['resolved']} workflow reference(s) resolve "
            f"to the package, {len(res['unresolved'])} do not"
            + (f"; {len(ep['conflicts'])} conflict(s) between the export and entry-points.yaml" if ep["conflicts"] else ""),
            ("Q2 is partly answered: add vRO scheduled tasks and policies, ITSM and REST callers to "
             "entry-points.yaml, and set complete: true when the list is whole." if not model["complete"] else "")
            + (" Check the unresolved references: the workflow may be missing from the package (Q1)."
               if res["unresolved"] else ""))
    else:
        chk("Entry points", "missing",
            "no vRA export and nothing in entry-points.yaml: no catalog items, subscriptions, scheduled tasks or policies",
            "Add the .vra-ng to the estate folder, or fill in entry-points.yaml (Q2). Until then, entry points "
            "are guessed from the call graph.")
    if ep["manual"]["warnings"]:
        chk("entry-points.yaml", "check", "; ".join(ep["manual"]["warnings"]), "Fix the file and re-run preflight.")
    chk("Input forms", "ready" if exp["input_forms"] else "gap",
        f"{exp['input_forms']} workflow(s) ship a separate input form; presentation is otherwise read from the workflow XML")
    rr = os.path.join(C.PRE, "redaction-report.json")
    if os.path.exists(rr):
        red = C.read_json(rr)
        chk("Sanitized copy for sharing", "ready" if not red.get("unsure") else "check",
            f"{sum(red['counts'].values())} values redacted, {red['dropped_values']} secret or configuration values "
            f"dropped, {len(red.get('unsure', []))} left in place as unsure; copy in data/0-preflight/sanitized/",
            "Review redaction-report.md before anything leaves the network.")
    else:
        chk("Sanitized copy for sharing", "gap", "not made yet",
            f"Run `{C.run_command()}`: it sanitizes first. Review redaction-report.md before sharing the export.")
    n_open = len(ans["open"])
    chk("Customer answers", "gap" if n_open else "ready",
        f"{len(QUESTIONS) - n_open} of {len(QUESTIONS)} answered in your-input/answers.yaml",
        f"Fill in {C.rel(C.ANSWERS)}.")

    verdicts = [
        ("/vro-assess", "Ready", "Needs only the export. Target reach for plugins not listed in dunes-meta-inf is read from object types."),
        ("/vro-brief", "Ready with gaps" if n_open else "Ready",
         f"{n_open} preflight question(s) open. The retire pass is untestable without history, and wave entry criteria keep the open items as gates."),
        ("convert (not built yet)", "Not ready",
         "Needs an approved brief, and a non-production instance for every target a wave writes to (Q3)."),
    ]

    result = {
        "generated": C.now(),
        "export": exp,
        "estate": C.ESTATE,
        "source_dir": C.rel(C.SOURCE_DIR),
        "scope": {"workflows_in_export": wf_total,
                  "note": "Assess and Brief work on every workflow and action in this estate."},
        "checks": checks,
        "verdicts": [{"command": a, "verdict": b, "why": c} for a, b, c in verdicts],
        "answers": ans,
        "question_contradictions": contradictions(ans["items"], f),
        "answers_created_this_run": created,
        "entry_points_template_written_this_run": ep_created,
    }
    C.write_json(os.path.join(C.PRE, "entry-points.json"), {"generated": result["generated"], **exp["entry_points"]})
    C.write_json(os.path.join(C.PRE, "preflight.json"), result)
    C.write_text(os.path.join(C.PRE, "readiness.md"), C.no_em_dashes(_readiness_md(result)))
    return result


def _readiness_md(r: dict) -> str:
    exp, ans = r["export"], r["answers"]
    L = [f"# Preflight: readiness ({r['estate']})", "",
         f"Generated {r['generated']} from `{r['source_dir']}/`. No model calls; this stage only reads files.", ""]

    L += ["## Customer answers", "",
          f"Read from `{ans['path']}`. The export cannot answer these. "
          f"{len(ans['open'])} of {len(ans['items'])} are open, and every later stage carries them as open items.", ""]
    for q in ans["items"]:
        state = {"open": "OPEN", "partly answered": "PARTLY ANSWERED",
                 "not applicable": "not applicable"}.get(q["status"], "answered")
        L += [f"**{q['id']}. {q['question']}** ({state})", ""]
        if q.get("found"):
            L += [f"{FOUND} {q['found']}", ""]
        if q["answer"] and not is_open(q["answer"]):
            L += [f"> {q['answer']}", ""]
        elif q["status"] == "partly answered":
            L += ["> From the entry points on record (see Entry points below). Not yet marked complete.", ""]
        elif q["status"] == "not applicable":
            L += ["> No vRA content on record, so nothing to keep or replace.", ""]
        else:
            L += ["> (no answer yet)", ""]
        L += [f"Why it matters: {q['why']} Read by: {'; '.join(q['read_by'])}.", ""]

    L += ["## Scope", "",
          f"The export holds {r['scope']['workflows_in_export']} workflows. {r['scope']['note']}", ""]
    if len({s["vro_version"] for s in exp["sources"] if s["vro_version"]}) > 1:
        L += ["The inputs record different vRO versions, which usually means more than one vRO node. "
              "Q1 asks the customer to say how many.", ""]

    L += ["## Checks", "",
          C.md_table(["Check", "Status", "Found", "What to do"],
                     [[c["check"], c["status"], c["found"], c["fix"]] for c in r["checks"]]), ""]

    L += ["## Sources", "",
          C.md_table(["Input", "Format", "Signed", "vRO version", "Plugins declared", "Elements"],
                     [[s["source"], s["format"] + (" (experimental)" if s["experimental"] else ""),
                       "yes" if s["signed"] else "no",
                       s["vro_version"] or "not recorded",
                       ", ".join(s["used_plugins"]) or "none",
                       ", ".join(f"{v} {k}" for k, v in s["element_counts"].items())]
                      for s in exp["sources"]]), "",
          f"{sum(1 for s in exp['sources'] if s['build_host_recorded'])} source(s) record a build host in "
          "dunes-meta-inf. The value is not repeated here.", ""]

    L += _entry_points_md(exp)
    L += ["## Readiness per stage", "",
          C.md_table(["Stage", "Readiness", "Why"],
                     [[v["command"], v["verdict"], v["why"]] for v in r["verdicts"]]), ""]

    L += ["## Most important gap", ""]
    if entry_points.total(exp["entry_points"]["model"]):
        L += ["No execution history (Q7). The entry points on record say what can start a workflow, not "
              "whether anything did in the last twelve months, so the retire rule is still untested.", ""]
    else:
        L += ["No execution history and no external entry points (Q2, Q7). Without them the brief cannot "
              "say which workflows are unused, only which ones nothing in the export calls.", ""]
    return "\n".join(L)


def _yn(v) -> str:
    return {True: "yes", False: "no"}.get(v, "")


def _entry_points_md(exp: dict) -> list[str]:
    ep = exp["entry_points"]
    model, res = ep["model"], ep["resolution"] or {"unresolved": []}
    L = ["## Entry points", "",
         f"What starts these workflows from outside vRO (Q2): read from {len(exp['vra_exports'])} vRA export(s) "
         f"and from `{ep['manual']['path']}` ({ep['manual']['entries']} hand-written entr"
         f"{'y' if ep['manual']['entries'] == 1 else 'ies'}). Workflow references are matched to the package by "
         f"vRO workflow ID. The normalized list is in `entry-points.json`.", ""]
    for x in exp["vra_exports"]:
        if x.get("error"):
            continue
        L += [f"### vRA export {x['label']}", "",
              C.md_table(["Kind", "Items"], [[k, v] for k, v in x["counts"].items()]), ""]
        if x["unknown"]:
            L += ["Folders and files this reader does not know, kept in the report rather than dropped:", "",
                  C.md_table(["Path", "Files"], [[u["path"], u["files"]] for u in x["unknown"]]), ""]
        if x["unread"]:
            L += ["Files in known folders that no reader used: " + ", ".join(f"`{u}`" for u in x["unread"]), ""]
        if x["warnings"]:
            L += ["Warnings:", ""] + [f"- {w}" for w in x["warnings"]] + [""]
        if x["present_but_unlisted"]:
            L += ["In the export but not listed in content.yaml: " + ", ".join(x["present_but_unlisted"]), ""]
    if model["subscriptions"]:
        L += ["### Event subscriptions", "",
              C.md_table(["Subscription", "Event topic", "Blocking", "Condition", "Runs workflow", "Resolves"],
                         [[s["name"], s.get("event_topic") or "", _yn(s.get("blocking")),
                           f"`{s['condition']}`" if s.get("condition") else "always",
                           s.get("workflow") or s.get("workflow_id") or s.get("workflow_name") or "",
                           "yes, by " + s["resolved_by"] if s.get("resolved") else
                           ("no: " + s.get("why", "") if s.get("resolved") is False else "")]
                          for s in model["subscriptions"]]), ""]
    if model["catalog_items"]:
        L += ["### Catalog items", "",
              C.md_table(["Catalog item", "Content source", "Deploys", "Form fields", "Entitled projects"],
                         [[c["name"], c.get("content_source") or "",
                           (f"cloud template {c['cloud_template']}" if c.get("cloud_template") else
                            (f"workflow {c.get('workflow') or c.get('workflow_id')}" if c.get("workflow_id") else
                             "not found in the export")),
                           len(c.get("form_fields") or []), ", ".join(c.get("projects") or [])]
                          for c in model["catalog_items"]]), ""]
    if model["cloud_templates"]:
        L += ["### Cloud templates", "",
              C.md_table(["Cloud template", "Inputs", "Creates"],
                         [[t["name"], len(t.get("inputs") or []),
                           ", ".join(f"{r.get('name')} ({r.get('type')})" for r in t.get("creates") or [])]
                          for t in model["cloud_templates"]]), ""]
    for k, title in (("day2_actions", "Day-2 actions"), ("other_triggers", "Other triggers")):
        if model[k]:
            L += [f"### {title}", "",
                  C.md_table(["Name", "Kind", "Runs workflow", "Resolves"],
                             [[a["name"], a.get("kind") or "", a.get("workflow") or a.get("workflow_id") or
                               a.get("workflow_name") or "", "yes" if a.get("resolved") else
                               ("no: " + a.get("why", "") if a.get("resolved") is False else "")]
                              for a in model[k]]), ""]
    if res["unresolved"]:
        L += ["### Not resolved", "",
              "These name a workflow that is not in the package. Either the package is partial (Q1), or the "
              "entry point is stale.", "",
              C.md_table(["Kind", "Entry point", "Workflow", "Why"],
                         [[u["kind"], u["name"], u.get("workflow_id") or u.get("workflow_name") or "", u.get("why", "")]
                          for u in res["unresolved"]]), ""]
    if ep["conflicts"]:
        L += ["### Conflicts between the export and entry-points.yaml", "",
              "The export's value is kept. Fix whichever side is wrong.", "",
              C.md_table(["Kind", "Entry point", "Field", "Kept", "From", "Other value", "From"],
                         [[c["kind"], c["name"], c["field"], c["kept"], c["kept_from"], c["other"], c["other_from"]]
                          for c in ep["conflicts"]]), ""]
    if ep["manual"]["warnings"]:
        L += ["### Problems in entry-points.yaml", ""] + [f"- {w}" for w in ep["manual"]["warnings"]] + [""]
    return L
