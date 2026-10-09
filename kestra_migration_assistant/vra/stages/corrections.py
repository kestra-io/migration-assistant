"""
your-input/corrections.yaml: optional corrections to what the tool found.

Answers go in answers.yaml. This file is for the rarer case where the tool got the estate wrong: a domain
named or grouped badly, a system reached another way than the plugin map says, a wrapper the rule missed, or
an outcome an owner overrules. Assess reads `domains`, `targets` and `wrappers`; the Brief reads `outcomes`.
The tool never creates it: a person adds it (INPUTS.md shows the shape, TEMPLATE below), and it is read on
every run. A blank template (what earlier versions wrote) holds nothing and is moved to data/.

Before 2026-10-01 the same corrections lived in 1-assess/overrides.yaml and 3-brief/overrides.yaml;
`migrate()` merges what they hold into this file once and renames them `overrides.yaml.migrated`.
"""
from __future__ import annotations

import os

import yaml

from . import common as C

TEMPLATE = """# Kestra Migration Assistant for vRA: corrections (optional). Read on every run; leave a section empty to change nothing.
#
# Only for what the tool found wrong. Answers to its questions go in answers.yaml, next to this file.
# Keys are the short names the pages show (or the workflow IDs in data/3-brief/triage.csv).

domains:
  rename: {}        # domain key -> new name, e.g. "avi-controller:provisioning": "Load balancer build"
  move: {}          # cluster id -> domain key, e.g. C12: "avi-controller:operations"

targets:
  reach: {}         # target key -> plugin | http | none, e.g. avi-controller: plugin

wrappers:
  not_wrapper: []   # workflows flagged as wrappers that are not
  wrapper: []       # workflows that are wrappers the rule missed

# One entry per workflow or action. The outcome must be one of: Retire, Near-duplicate, Wrapper,
# Plugin-covered library, Convert, Needs owner input, Needs a model, Manual, Rebuild
#   outcomes:
#     lb-create-load-balancer:
#       outcome: Retire
#       reason: "unused, confirmed by the platform team on 2026-10-01"
outcomes: {}
"""


def _blank(v) -> bool:
    if isinstance(v, dict):
        return all(_blank(x) for x in v.values())
    if isinstance(v, list):
        return all(_blank(x) for x in v)
    return v in (None, "")


def load() -> dict:
    """The corrections, or {} when there is no file."""
    if not os.path.exists(C.CORRECTIONS):
        return {}
    try:
        data = C.load_yaml(C.CORRECTIONS) or {}
    except yaml.YAMLError as e:
        print(f"[corrections] {C.rel(C.CORRECTIONS)} could not be read, so no correction is applied: {e}")
        return {}
    text = open(C.CORRECTIONS, encoding="utf-8").read()
    if text.startswith(TEMPLATE.split("\n", 1)[0]) and _blank(data):
        # the blank template an earlier version wrote: it holds nothing, so it moves out of your-input/
        C.ensure(C.DATA)
        os.replace(C.CORRECTIONS, os.path.join(C.DATA, "corrections.yaml.blank"))
        return {}
    return data


def outcomes(names: dict[str, str] | None = None) -> dict:
    """uid or action key -> override. A key may be a short name; `names` maps short name -> uid."""
    got = (load().get("outcomes") or {})
    names = names or {}
    return {names.get(k, k): v for k, v in got.items() if isinstance(v, dict)}


def migrate() -> list[str]:
    """Merge the old overrides.yaml files into corrections.yaml, once. Returns what was carried."""
    olds = [p for p in (os.path.join(C.ASSESS, "overrides.yaml"), os.path.join(C.BRIEF, "overrides.yaml"))
            if os.path.exists(p)]
    if not olds:
        return []
    cur = load()
    done = []
    for p in olds:
        old = C.load_yaml(p) or {}
        n = 0
        for sec in ("domains", "targets", "wrappers"):
            for sub, v in (old.get(sec) or {}).items():
                if v:
                    d = cur.setdefault(sec, {}) or {}
                    cur[sec] = d
                    if isinstance(v, dict):
                        d[sub] = {**(d.get(sub) or {}), **v}
                    else:
                        d[sub] = sorted(set(d.get(sub) or []) | set(v))
                    n += len(v)
        for k, v in (old.get("outcomes") or {}).items():
            cur.setdefault("outcomes", {})
            cur["outcomes"] = cur["outcomes"] or {}
            cur["outcomes"].setdefault(k, v)
            n += 1
        os.replace(p, p + ".migrated")
        done.append(f"{C.rel(p)}: {n} correction(s)")
    if any(not d.endswith(": 0 correction(s)") for d in done):
        C.write_text(C.CORRECTIONS, TEMPLATE.split("\ndomains:")[0] + "\n\n"
                     + yaml.safe_dump(cur, sort_keys=False, allow_unicode=True, width=100))
    return done
