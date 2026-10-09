"""
BRIEF.md, the brief to download: the front page's sections (front.document, so the two cannot disagree), then an
appendix with every element of the estate in its row and every wave's entry criteria, one line each.

The answers and the signature live in your-input/answers.yaml; this file is regenerated on every run.
"""
from __future__ import annotations

import re
from typing import cast

from . import front as FR


def render(r: dict, assess: dict, specs: dict, answers: dict, old_approval: dict) -> str:
    L = FR.markdown(assess, r)
    L += ["## Appendix A. Every element", "",
          "Each element of the estate, under the row of the table above that holds it. A star marks what is ready "
          "now.", ""]
    for x in FR.merged_rows(assess, r):
        its = [(i, row) for row in x["rows"] for i in row["items"]]
        seen = [i["name"] for i, _ in its]
        names = [i["name"] + (f" ({row['kind'].lower().rstrip('s')})" if seen.count(i["name"]) > 1 else "")
                 + ("*" if i.get("ready") else "") for i, row in its]
        L.append(f"- **{x['label']}** ({x['how']}, {x['count']}): " + ", ".join(names) + ".")
    L.append("")
    L += ["## Appendix B. Entry criteria per wave", "",
          "Met or open, and the item in `your-input/answers.yaml` that closes it.", ""]
    for w in r["plan"]["waves"]:
        crit = w["entry_criteria"]
        met = sum(1 for e in crit if e.get("status") == "done")
        L += [f"### Wave {w['wave']} ({met} of {len(crit)} met)", ""]
        defects: dict[tuple[str, str], list] = {}
        for e in crit:
            k = closes_key(e)
            st = "met" if e.get("status") == "done" else e.get("status") or "open"
            if k.startswith("defect.") and k.count(".") >= 2:
                defects.setdefault((st, k.split(".")[1]), []).append(k)
                continue
            c = e.get("closes") or {}
            L.append(f"- {st}: {criterion_label(e, assess)}"
                     + (f" ({c['when']})" if c.get("kind") == "gate" else f" (`{k}`)" if k else ""))
        wf = {w["short"]: w["name"] for w in assess["workflows"].values()}
        for (st, short), ks in defects.items():
            L.append(f"- {st}: {len(ks)} possible {'defect' if len(ks) == 1 else 'defects'} in {wf.get(short, short)} "
                     f"(`defect.{short}.*`)")
        L.append("")
    return "\n".join(L).rstrip() + "\n"


def closes_key(e: dict) -> str:
    c = e.get("closes") or {}
    k = str(c.get("field") or c.get("key") or "")
    return k if k and c.get("kind") != "approval" else ("signed_for" if c.get("kind") == "approval" else k)


def criterion_label(e: dict, assess: dict) -> str:
    """An entry criterion in a few words: system names short, no parenthesis, no explanation after a colon."""
    key = closes_key(e)
    fam = key.split(".", 1)[0]
    wf = {w["short"]: w for w in assess["workflows"].values()}
    if fam == "defect":
        parts = key.split(".")
        w = wf.get(parts[1]) if len(parts) > 2 else None
        return f"Possible defect {parts[-1]} in {w['name'] if w else parts[1]}"
    names = {t["key"] if "key" in t else k: t.get("short_name") or t["name"] for k, t in assess["targets"].items()}
    from .gates import key_part
    by_part = {key_part(k): v for k, v in names.items()}
    rest = key.split(".", 1)[1] if "." in key else ""
    q = re.findall(r"'([^']+)'", e["criterion"])
    template = {
        "endpoint": lambda: f"{by_part.get(rest, rest)}: the instance and its credentials are named",
        "timeout": lambda: f"Timeout and failure policy for '{q[0]}'" if q else None,
        "failure": lambda: f"Failure policy for '{q[0]}'" if q else None,
        "cancel": lambda: f"What a cancelled request undoes in '{q[0]}'" if q else None,
        "wave-exit": lambda: f"Wave {rest} has met its exit criteria",
        "mappings": lambda: "Image, size and placement mappings are restated for Kestra",
        "ip-allocation": lambda: "Who assigns the machines' IP addresses is decided",
        "day2-scale": lambda: "How scaling works on Day 2 is decided",
        "existing-deployments": lambda: "A plan for the deployments vRA already made",
        "deployment-record": lambda: "The flow records what belongs to each deployment",
        "app": lambda: f"The App for '{q[0]}' is agreed" if q else None,
    }.get(fam)
    if template and template():
        return cast(str, template())
    s = e["criterion"]
    for t in assess["targets"].values():
        if t.get("short_name"):
            s = s.replace(t["name"], t["short_name"])
    s = re.sub(r"VMware vCenter", "vCenter", s)
    s = re.sub(r"\s*\([^()]*\)", "", s)
    s = re.sub(r"\s*\([^()]*\)", "", s)
    head, sep, rest = s.partition(": ")
    if sep and len(head.split()) <= 4:          # "Avi: the instance ... is named", keep the system
        s = head + ": " + re.split(r"[:;]\s", rest)[0]
    else:
        s = re.split(r"[:;]\s", s)[0]
    s = s.replace("Suspected defect", "Possible defect").replace("pilot", "first of a kind")
    return s[:1].upper() + s[1:]
