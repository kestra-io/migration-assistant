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

Two items of one workflow that come out the same are told apart by what each one cites and the others don't
(`_<what>`): another action of the export, else a name from the code (`state.memberPort`, `receive_timeout`), else
the words that gave it its kind, else a short hash of its text. Never its place in the list or in a sorted order, so
neither the order the model listed them in nor its wording of the others moves an answer. An item that changes kind,
element or what it cites between runs gets a new ID: its old answer is then listed as no longer asked, never
attached to something else.
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


# A name from the code: dotted (state.memberPort), snake case (receive_timeout), camel case (memberPort), a call
# (track()), or anything in backquotes.
CODE = re.compile(r"`([^`\s]{2,60})`|(?<![\w.])([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+|[a-z][a-z0-9]*_[a-z0-9_]+|"
                  r"[a-z][a-z0-9]*[A-Z][\w$]*|[A-Za-z_$][\w$]*(?=\())")


def cited(texts: tuple[str, ...], names: list[str]) -> list[list[str]]:
    """What one item cites, as slugs in tiers (actions of the export, names from the code, the words that gave it its
    kind), each in the order it appears in the texts."""
    tiers: list[list[str]] = [[], [], []]
    for t in texts:
        hits = sorted((m.start(), n) for n in names
                      for m in re.finditer(r"(?<![A-Za-z0-9_])" + re.escape(n) + r"(?![A-Za-z0-9_])", t))
        tiers[0] += [slug(n) for _, n in hits]
        tiers[1] += [slug(m.group(1) or m.group(2)) for m in CODE.finditer(t)]
        low = t.lower()
        tiers[2] += [slug(m.group(0)) for _, rx in KINDS for m in re.finditer(rx, low)]
    return [list(dict.fromkeys(x for x in tier if x)) for tier in tiers]


def unique(ids: list[str], texts: list, names: list[str] | None = None) -> list[str]:
    """Tell apart IDs that came out the same by what each item cites and the others in its set don't, never by its
    position. `texts[i]` is the item's text, or a tuple of its texts in the order they are searched."""
    out = list(ids)
    for k in set(ids):
        same = [i for i, x in enumerate(ids) if x == k]
        if len(same) < 2:
            continue
        own = {i: cited(texts[i] if isinstance(texts[i], tuple) else (texts[i],), names or []) for i in same}
        for i in same:
            others = {c for j in same if j != i for tier in own[j] for c in tier}
            mark = next((c for tier in own[i] for c in tier if c not in others and c not in k.split(".")), None)
            out[i] = f"{k}_{mark or _short_hash(texts[i])}"
    return out


def _short_hash(text) -> str:
    import hashlib
    words = " ".join(text if isinstance(text, tuple) else (text,))
    return "h" + hashlib.sha256(" ".join(words.lower().split()).encode()).hexdigest()[:6]


def kind_part(key: str) -> str:
    """The kind an ID ends with, without what tells it apart: `defect.x.y.tls_state-ref` -> `tls`."""
    return re.sub(r"-\d+$", "", key.rsplit(".", 1)[-1].split("_", 1)[0])


def defect_ids(short: str, defects: list[dict], names: list[str]) -> list[str]:
    """One ID per suspected defect of one workflow, in the order given."""
    ids = [_id("defect", short, element_of(names, d.get("what"), d.get("evidence")),
               kind_of(d.get("what"), d.get("evidence"), d.get("question")) or "other") for d in defects]
    return unique(ids, [(str(d.get("what") or ""), str(d.get("evidence") or "")) for d in defects], names)


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
    kind = kind_part(key).replace("-", " ")
    return kind + (f" ({parts[2]})" if len(parts) > 3 else "")


# A page never lists these IDs to a customer: answers.yaml asks possible defects per workflow
# (`defects.<workflow>`) and spec questions in one group (`questions`), each with its own words.
COUNTED = {"defect": ("possible defect", "possible defects", "decided", "to decide"),
           "question": ("question from reading the workflows", "questions from reading the workflows", "answered",
                        "to answer")}


def counted(family: str, n: int, done: int) -> str:
    """A wave's possible defects or spec questions in one line: '15 possible defects, decided', '4 of 15 possible
    defects still to decide'."""
    one, many, ok, todo = COUNTED[family]
    return f"{n} {one if n == 1 else many}, {ok}" if done == n else f"{n - done} of {n} {one if n == 1 else many} still {todo}"


def entry_key(key: str) -> str:
    """The answers.yaml entry a customer answers an item under: `defect.<workflow>.…` -> `defects.<workflow>`,
    `question.…` -> `questions`; any other key is its own entry."""
    fam, _, rest = key.partition(".")
    if fam == "defect" and rest:
        return "defects." + rest.split(".")[0]
    if fam == "question" and rest:
        return "questions"
    return key
