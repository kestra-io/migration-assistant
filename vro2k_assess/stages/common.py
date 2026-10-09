"""
Shared paths and helpers for the Preflight, Assess and Brief stages.

The stages share the parser, call graph, fingerprints and clusters
(`vro2k_assess.parse`, `vro2k_assess.cluster`), and point the model adapter's
cache and call log at the estate's output folder at runtime.

Every stage reads the previous stage's files from disk and writes its own.
Human edits live in files a person owns (`answers.md`, `overrides.yaml`,
the brief's approval block) and are re-read on every run.
"""
from __future__ import annotations

import csv
import html
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGE = os.path.dirname(HERE)                  # vro2k_assess/
ROOT = os.environ.get("VRO2K_ROOT") or os.path.dirname(PACKAGE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ------------------------------------------------------------------ estates
#
# `sources/<estate>/` is the only input. One estate is one run: every stage is
# configured for exactly one estate before it runs (`configure`), reads only
# that estate's folder, and writes only under `stages/<estate>/`. `samples/`
# holds the public samples and is never read; `use-sample` copies one into
# `sources/` first.

SOURCES_ROOT = os.path.join(ROOT, "sources")
SAMPLES_ROOT = os.path.join(ROOT, "samples")
STAGES_ROOT = os.environ.get("VRO_STAGES_DIR") or os.path.join(ROOT, "stages")
PLUGIN_MAP = os.path.join(PACKAGE, "data", "plugin-map.yaml")
ASSETS = os.path.join(PACKAGE, "assets")

# Model replies are content-addressed, so one cache serves every estate; each
# estate keeps its own call log.
STAGE_CACHE = os.path.join(STAGES_ROOT, ".model-cache")

ESTATE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
INPUT_SUFFIXES = (".package", ".workflow", ".zip", ".xml")
TREE_MARKERS = ("Workflow", "workflows", "ScriptModule", "actions", "ConfigurationElement",
                "ResourceElement", "PolicyTemplate", "META-INF", "dunes-meta-inf.xml")

# Set by configure(). None until then, so a stage run without an estate fails loudly.
ESTATE = SOURCE_DIR = STAGES = PRE = ASSESS = UNDERSTAND = BRIEF = PAGES = INDEX = WORK = None
SPEC_DIR = CLUSTER_SUMMARIES = STAGE_CALL_LOG = ENTRY_POINTS = INPUT = ANSWERS = CORRECTIONS = BRIEF_MD = DATA = None
INPUTS: dict[str, str] = {}
VRA_INPUTS: dict[str, str] = {}
IGNORED: list[str] = []


# The estate layout (session 2e, 2026-10-01):
#
#     stages/<estate>/index.html     start here: the brief
#     stages/<estate>/your-input/    what a person writes: answers.yaml, corrections.yaml, entry-points.yaml
#     stages/<estate>/report/        the detail pages, and BRIEF.md as a downloadable copy
#     stages/<estate>/data/          the stages' working files, never edited by a person
#
# data/ keeps one folder per stage, numbered as the stages run.
LAYOUT = {"preflight": "data/0-preflight", "assess": "data/1-assess", "understand": "data/2-understand",
          "brief": "data/3-brief", "pages": "report", "input": "your-input"}
# Folders of the layout before 2026-09-30 (session 2c), old name -> new name. None: regenerated, not moved.
OLD_LAYOUT = {"specs": "2-understand", "2-brief": "3-brief", "visuals": None}
# The layout of sessions 2c and 2d: the stage folders at the top, the pages in pages/. `migrate_layout` moves
# them into data/ and report/ itself (nothing a person wrote is lost: their files are carried into
# your-input/ by your_input.migrate()).
LAYOUT_2C = {"0-preflight": "data/0-preflight", "1-assess": "data/1-assess", "2-understand": "data/2-understand",
             "3-brief": "data/3-brief", "pages": None}


class EstateError(ValueError):
    pass


def old_layout(stages_dir: str) -> list[str]:
    """The folders of the oldest layout (before 2c) still in an estate's stage folder."""
    if not os.path.isdir(stages_dir):
        return []
    return [n for n in OLD_LAYOUT if os.path.isdir(os.path.join(stages_dir, n))]


def old_layout_message(stages_dir: str) -> str | None:
    """
    What to do about an estate written with the oldest layout, or None. Those folders hold a person's files
    and paid-for specs under names this tool no longer knows, so it says how to move them, and stops.
    """
    old = old_layout(stages_dir)
    if not old:
        return None
    where = os.path.relpath(stages_dir, ROOT)
    steps = []
    for n in old:
        new = OLD_LAYOUT[n]
        if new is None:
            steps.append(f"  rm -r {where}/{n}          # the old pages; `run` writes report/ and index.html")
        elif os.path.exists(os.path.join(stages_dir, new)):
            steps.append(f"  # both {n}/ and {new}/ exist: keep the files you edited from {n}/, then remove it")
        else:
            steps.append(f"  mv {where}/{n} {where}/{new}")
    return (f"{where}/ was written with an older layout ({', '.join(n + '/' for n in old)}). Move the folders "
            "to the names of the 2c layout first, and the next run carries them into data/ and your-input/:\n"
            + "\n".join(steps) + f"\nthen re-run `vro.py run --estate {os.path.basename(stages_dir)}`.")


def layout_2c(stages_dir: str) -> list[str]:
    """The folders of the 2c/2d layout (stage folders at the top, pages/) in an estate's stage folder."""
    if not os.path.isdir(stages_dir):
        return []
    return [n for n in LAYOUT_2C if os.path.isdir(os.path.join(stages_dir, n))]


def migrate_layout(stages_dir: str) -> list[str]:
    """
    Move a 2c/2d estate into today's layout: each stage folder into data/, and pages/ removed (the pages are
    regenerated into report/). A folder whose new place already exists is left where it is and named in the
    result. Returns what was done, one line each, for the run to print.
    """
    import shutil
    done = []
    for n in layout_2c(stages_dir):
        src, new = os.path.join(stages_dir, n), LAYOUT_2C[n]
        if new is None:
            shutil.rmtree(src)
            done.append(f"removed {n}/ (the old pages; regenerated in report/)")
            continue
        dst = os.path.join(stages_dir, new)
        if os.path.exists(dst):
            done.append(f"left {n}/ in place: {new}/ exists too (keep what you need from {n}/, then remove it)")
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        os.replace(src, dst)
        done.append(f"moved {n}/ to {new}/")
    log = os.path.join(stages_dir, "model-calls.jsonl")
    if done and os.path.exists(log) and not os.path.exists(os.path.join(stages_dir, "data", "model-calls.jsonl")):
        os.replace(log, os.path.join(stages_dir, "data", "model-calls.jsonl"))
        done.append("moved model-calls.jsonl to data/")
    return done


def estate_inputs(source_dir: str) -> tuple[dict[str, str], list[str]]:
    """
    The inputs in one estate folder, label -> path, and the files ignored.

    If the folder is itself a BTVA / raw XML tree (it holds Workflow/, META-INF/ ...), it is one
    input labelled with the folder name. Otherwise every child is one input: a directory (a tree),
    a .package, a single .workflow export, a ZIP, or one workflow .xml. The label prefixes every
    workflow's stable ID. A vRA export (`.vra-ng`, or a Build Tools vRA folder) is neither: it is read
    by vra_inputs_of(). Anything else (a README, a SOURCE.md, run history) is listed as ignored.
    """
    from vro2k_assess.parse import vra
    names = sorted(n for n in os.listdir(source_dir) if not n.startswith("."))
    if any(n in TREE_MARKERS for n in names):
        return {slug(os.path.basename(os.path.normpath(source_dir))): source_dir}, []
    inputs, ignored = {}, []
    for n in names:
        p = os.path.join(source_dir, n)
        if os.path.islink(p):
            ignored.append(n + " (symlink, not followed)")
        elif vra.is_vra_input(p):
            continue                                        # a vRA export: see vra_inputs_of()
        elif os.path.isdir(p):
            inputs[slug(n)] = p
        elif n.lower().endswith(INPUT_SUFFIXES):
            inputs[slug(os.path.splitext(n)[0])] = p
        else:
            ignored.append(n)
    return inputs, ignored


def vra_inputs_of(source_dir: str) -> dict[str, str]:
    """
    The vRA / Aria Automation exports in one estate folder, label -> path: each `.vra-ng` file, and each
    folder laid out as a Build Tools vRA project (content.yaml plus blueprints/, subscriptions/ ...).
    They are read for entry points only, never as vRO inputs.
    """
    from vro2k_assess.parse import vra
    out = {}
    for n in sorted(os.listdir(source_dir)):
        p = os.path.join(source_dir, n)
        if n.startswith(".") or os.path.islink(p):
            continue
        if vra.is_vra_input(p):
            out[slug(os.path.splitext(n)[0] if os.path.isfile(p) else n)] = p
    return out


def list_estates() -> list[str]:
    if not os.path.isdir(SOURCES_ROOT):
        return []
    return sorted(n for n in os.listdir(SOURCES_ROOT)
                  if os.path.isdir(os.path.join(SOURCES_ROOT, n)) and ESTATE_NAME.match(n))


def configure(estate: str, *, source_dir: str | None = None, stages_dir: str | None = None,
              spec_dir: str | None = None, inputs: dict[str, str] | None = None,
              vra_inputs: dict[str, str] | None = None) -> None:
    """Point every stage at one estate. The only way paths below are set."""
    global ESTATE, SOURCE_DIR, STAGES, PRE, ASSESS, UNDERSTAND, BRIEF, PAGES, INDEX, WORK
    global SPEC_DIR, CLUSTER_SUMMARIES, STAGE_CALL_LOG, INPUTS, IGNORED, VRA_INPUTS, ENTRY_POINTS
    global INPUT, ANSWERS, CORRECTIONS, BRIEF_MD, DATA
    if not estate or not ESTATE_NAME.match(estate):
        raise EstateError(f"estate name {estate!r}: use letters, digits, '.', '_' or '-', starting with a "
                          "letter or digit")
    src = source_dir or os.path.join(SOURCES_ROOT, estate)
    if not os.path.isdir(src):
        have = ", ".join(list_estates()) or "none"
        raise EstateError(f"no estate folder {os.path.relpath(src, ROOT)}/ (estates in sources/: {have}). "
                          "Put the export in sources/<estate>/, or run `vro.py use-sample <name>`.")
    ESTATE, SOURCE_DIR = estate, src
    STAGES = stages_dir or os.path.join(STAGES_ROOT, estate)
    PRE = os.path.join(STAGES, LAYOUT["preflight"])
    ASSESS = os.path.join(STAGES, LAYOUT["assess"])
    UNDERSTAND = os.path.join(STAGES, LAYOUT["understand"])  # written by `vro.py understand`
    BRIEF = os.path.join(STAGES, LAYOUT["brief"])
    PAGES = os.path.join(STAGES, LAYOUT["pages"])            # the detail pages; INDEX (the brief) is the way in
    INDEX = os.path.join(STAGES, "index.html")
    BRIEF_MD = os.path.join(PAGES, "BRIEF.md")                # the brief as a downloadable copy
    DATA = os.path.join(STAGES, "data")
    WORK = os.path.join(ASSESS, "work")                     # re-parsed model files
    SPEC_DIR = spec_dir or UNDERSTAND
    CLUSTER_SUMMARIES = os.path.join(UNDERSTAND, "cluster-summaries.json")
    STAGE_CALL_LOG = os.path.join(DATA, "model-calls.jsonl")
    # what a person writes: everything they answer, optional corrections, and the entry points (preflight Q2)
    INPUT = os.path.join(STAGES, LAYOUT["input"])
    ANSWERS = os.path.join(INPUT, "answers.yaml")
    CORRECTIONS = os.path.join(INPUT, "corrections.yaml")
    ENTRY_POINTS = os.path.join(INPUT, "entry-points.yaml")
    if inputs is None:
        INPUTS, IGNORED = estate_inputs(src)
    else:
        INPUTS, IGNORED = dict(inputs), []
    if vra_inputs is not None:
        VRA_INPUTS = dict(vra_inputs)
    else:
        VRA_INPUTS = vra_inputs_of(src) if inputs is None else {}
    if not INPUTS:
        raise EstateError(f"{os.path.relpath(src, ROOT)}/ holds no export: expected a .package, a BTVA "
                          "source tree, or a .workflow file (see INPUTS.md)")


def require_estate() -> None:
    if ESTATE is None:
        raise EstateError("no estate configured: pass --estate <name>")


def rel(path: str) -> str:
    """A path as a reader should see it: relative to the repository."""
    return os.path.relpath(path, ROOT)


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def ensure(*dirs: str) -> None:
    for d in dirs:
        os.makedirs(d, exist_ok=True)


def read_json(path: str):
    with open(path) as f:
        return json.load(f)


def write_json(path: str, obj) -> str:
    ensure(os.path.dirname(path))
    with open(path, "w") as f:
        json.dump(obj, f, indent=1, sort_keys=False, default=str)
        f.write("\n")
    return path


def write_text(path: str, text: str) -> str:
    ensure(os.path.dirname(path))
    with open(path, "w") as f:
        f.write(text if text.endswith("\n") else text + "\n")
    return path


def write_csv(path: str, rows: list[dict], fields: list[str]) -> str:
    ensure(os.path.dirname(path))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


def load_yaml(path: str):
    import yaml
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return yaml.safe_load(f)


def dump_yaml(obj) -> str:
    import yaml
    return yaml.safe_dump(obj, sort_keys=False, allow_unicode=True, width=100)


def spec(uid: str) -> dict | None:
    p = os.path.join(SPEC_DIR, f"{uid}.json")
    return read_json(p) if os.path.exists(p) else None


def md_table(headers: list[str], rows: list[list], align: list[str] | None = None) -> str:
    def cell(v):
        s = "" if v is None else str(v)
        return s.replace("|", "\\|").replace("\n", " ")
    align = align or ["---"] * len(headers)
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(align) + "|"]
    out += ["| " + " | ".join(cell(v) for v in r) + " |" for r in rows]
    return "\n".join(out)


def esc(s) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def no_em_dashes(text: str) -> str:
    """House rule for anything a person reads: no em or en dashes."""
    text = re.sub(r"\s*\u2014\s*", ", ", text)
    text = re.sub(r",\s*,", ",", text)
    return text.replace("\u2013", "-")


def slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-")


def short_names(names: dict[str, str]) -> dict[str, str]:
    """uid -> a short, readable key from the display name (`lb-create-load-balancer`), unique in the estate.
    What a person reads and types: answers.yaml keys, page names, corrections.yaml keys."""
    out, used = {}, set()
    for u in sorted(names):
        base = slug((names[u] or u).lower().replace(" ", "-")).strip("-.").lower() or "workflow"
        base = re.sub(r"-{2,}", "-", base)
        k, i = base, 2
        while k in used:
            k, i = f"{base}-{i}", i + 1
        used.add(k)
        out[u] = k
    return out


# A long stage reports what it is doing while it runs (`vro.py run` sets the reporter; nothing else does).
_PROGRESS = None


def set_progress(fn) -> None:
    global _PROGRESS
    _PROGRESS = fn


def progress(msg: str) -> None:
    """One line on what a long stage is doing now (a spec written, with its time). Silent outside `run`."""
    if _PROGRESS:
        _PROGRESS(msg)


def mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


# ------------------------------------------------------------------ model

def model():
    """
    The model adapter (cached, logged), pointed at the cache and the call log
    of the configured estate.
    """
    from vro2k_assess import model as M
    ensure(STAGE_CACHE)
    M.CACHE_DIR = STAGE_CACHE
    M.LOG_PATH = STAGE_CALL_LOG
    return M


def call_summary() -> dict:
    """What the new stages spent, read back from their own call log."""
    if not os.path.exists(STAGE_CALL_LOG):
        return {"calls": 0, "billed": 0, "cached": 0, "cost_usd": 0.0, "purposes": {}}
    calls = billed = cached = 0
    cost = 0.0
    purposes: dict[str, int] = {}
    with open(STAGE_CALL_LOG) as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("event"):
                continue
            calls += 1
            if r.get("cached"):
                cached += 1
            else:
                billed += 1
            cost += r.get("cost_usd") or 0.0
            purposes[r.get("purpose", "?")] = purposes.get(r.get("purpose", "?"), 0) + 1
    return {"calls": calls, "billed": billed, "cached": cached,
            "cost_usd": round(cost, 4), "purposes": purposes}
