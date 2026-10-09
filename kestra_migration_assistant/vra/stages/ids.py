"""
Stable IDs for what a person answers about one workflow: its possible defects and its spec questions.

An answer in your-input/answers.yaml is kept under its item's ID, so the ID has to name what the item is about,
not where it fell in a list: a re-run whose model words its findings differently, or lists them in another order,
must find each answer under the same ID. An ID is built from three things the code can read:

    defect.<workflow>.<element>.<kind>      defect.lb-add-machine-to-pool.add-avi-pool-server.ip-version
    question.<workflow>.<element>.<kind>    question.lb-delete-load-balancer.error-semantics

- the workflow's short name;
- the element it concerns: the first action of the export named in what the model wrote (its `what`, else its
  evidence), left out when it names none. Only names the export has count, so a model cannot invent one;
- the kind of issue, from a fixed list of the defects the spec prompt asks for (a check in the wrong order, a
  cleanup that removes what the run did not create, one value for every item, ...), matched in the `what`, then
  the evidence, then the question. A spec question that matches none takes its theme.

Two items of one workflow that come out the same are told apart by their text, in sorted order (`-2`, `-3`), so
the order the model listed them in never changes an ID. An item that changes kind or element between runs gets a
new ID: its old answer is then listed as no longer asked, never attached to something else.
"""
from __future__ import annotations

import re

# (kind, pattern), the first match wins: more specific kinds first.
KINDS: list[tuple[str, str]] = [
    ("tls", r"certificate|hostverification|\btls\b|\bssl\b|trust[- ]all"),
    ("name-collision", r"collision|collide|first \d+ (hex )?char|truncat"),
    ("misleading-log", r"\blogs?\b[^.]*(even when|although|regardless)|success log|log says|log line|"
                       r"logs? '|logged as"),
    ("deletes-unowned", r"did not create|didn't create|not created by|whether or not|created outside|"
                        r"pre-?existing|may not have created|belongs to"),
    ("race", r"atomic|parallel|concurren|\brace\b|simultaneous"),
    ("check-order", r"\b(read|opened|enforced|runs?|happens|checked|lookup)\b[^.]*\bbefore\b|"
                    r"before the \w+ (check|gate|decision|test)"),
    ("filter-mismatch", r"filter|case-sensitive|case-insensitive|any case|exact string"),
    ("ip-version", r"ipv6|ipv4|\bv4\b|\bv6\b|ip type|ip\.type|address type"),
    ("unverified-reuse", r"reuse|never compared|not compared|adopt"),
    ("same-value-for-all", r"same (port|value)|every (machine|item|server|member) gets|one value for|"
                           r"not each \w+'s own|instead of (its|each \w+'s) own"),
    ("masked-error", r"mask|swallow|tolerat|silently|ignor"),
    ("timing", r"timeout|interval|\bwait"),
    ("missing-validation", r"not validated|no validation|without (checking|validating)|not detected|unchecked"),
    ("parse-failure", r"malformed|cannot be parsed|\bparse\b|parsing|invalid json"),
    ("unused-value", r"never used|computed but|unused|never read"),
]


def slug(s: str) -> str:
    """camelCase and spaces to kebab case: addAviPoolServer -> add-avi-pool-server."""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", str(s or ""))
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def action_names(assess: dict) -> list[str]:
    """The names of the export's actions, longest first (so a name inside a longer one never wins)."""
    return sorted({str(a["name"]) for a in assess.get("actions") or [] if a.get("name")}, key=lambda n: (-len(n), n))


def kind_of(*texts: str | None) -> str | None:
    """The first kind whose pattern matches, trying each text in turn."""
    for t in texts:
        low = str(t or "").lower()
        for k, rx in KINDS:
            if re.search(rx, low):
                return k
    return None


def element_of(names: list[str], *texts: str | None) -> str | None:
    """The first action named in the first text that names one."""
    for t in texts:
        t = str(t or "")
        hits = []
        for n in names:
            m = re.search(r"(?<![A-Za-z0-9_])" + re.escape(n) + r"(?![A-Za-z0-9_])", t)
            if m:
                hits.append((m.start(), n))
        if hits:
            return min(hits)[1]
    return None


def _id(family: str, short: str, element: str | None, kind: str) -> str:
    return ".".join([family, short] + ([slug(element)] if element else []) + [kind])


def unique(ids: list[str], texts: list[str]) -> list[str]:
    """Tell apart IDs that came out the same, by the sorted order of their texts, not their position."""
    out = list(ids)
    for k in set(ids):
        same = [i for i, x in enumerate(ids) if x == k]
        if len(same) > 1:
            for n, i in enumerate(sorted(same, key=lambda i: texts[i]), 1):
                if n > 1:
                    out[i] = f"{k}-{n}"
    return out


def defect_ids(short: str, defects: list[dict], names: list[str]) -> list[str]:
    """One ID per suspected defect of one workflow, in the order given."""
    ids = [_id("defect", short, element_of(names, d.get("what"), d.get("evidence")),
               kind_of(d.get("what"), d.get("evidence"), d.get("question")) or "other") for d in defects]
    return unique(ids, [str(d.get("what") or "") for d in defects])


def question_id(short: str, question: str, why: str | None, theme: str | None, names: list[str]) -> str:
    """The ID of one spec question (before telling apart two that come out the same)."""
    kind = kind_of(question) or slug(theme or "") or kind_of(why) or "other"
    return _id("question", short, element_of(names, question, why), kind)


def defect_words(key: str) -> str:
    """A defect's ID in a few words, for a page: `defect.x.add-avi-pool-server.ip-version` -> `ip version, in
    addAviPoolServer`-like text (the element as its slug)."""
    parts = key.split(".")
    if len(parts) < 3:
        return key
    kind = re.sub(r"-\d+$", "", parts[-1]).replace("-", " ")
    return kind + (f" ({parts[2]})" if len(parts) > 3 else "")
