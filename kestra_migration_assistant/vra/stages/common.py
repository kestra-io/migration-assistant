"""
Shared paths and helpers for the Preflight, Assess and Brief stages.

The stages share the parser, call graph, fingerprints and clusters
(`kestra_migration_assistant.vra.parse`, `kestra_migration_assistant.vra.cluster`), and point the model adapter's
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
import time

from kestra_migration_assistant.vra import outdir

HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGE = os.path.dirname(HERE)                  # kestra_migration_assistant/vra/, as installed
PLUGIN_MAP = os.path.join(PACKAGE, "data", "plugin-map.yaml")
ASSETS = os.path.join(PACKAGE, "assets")

# ------------------------------------------------------------------ estates
#
# The export folder is the only input, and one export folder is one estate, named after the folder.
# Every stage is configured for exactly one estate before it runs (`configure_export`, or `configure`),
# reads only that folder, and writes only under its output folder: `<export folder>/kestra-assessment/`
# by default, or the folder `--out` names. Every reader of the export skips the output folder (outdir.py).
# Bundled assets (the plugin map, the plugin snapshot, the page assets) come from inside the installed
# package; nothing is resolved against a repository.

PRODUCT = "Kestra Migration Assistant for vRA"           # the name everywhere a customer sees it


def run_facts(date: str | None = None) -> dict:
    """What every page's footer says about the run: the tool's version, the model that wrote the specs, the date of
    the brief, and the date of the plugin catalog snapshot the coverage was checked against."""
    from kestra_migration_assistant import __version__
    models: set[str] = set()
    prov = os.path.join(SPEC_DIR, "PROVENANCE.json") if SPEC_DIR else ""
    if prov and os.path.exists(prov):
        for part in ("specs", "cluster_summaries"):
            models |= {str(x["model"]) for x in (read_json(prov).get(part) or {}).values() if x.get("model")}
    brief = os.path.join(BRIEF, "brief.json") if BRIEF else ""
    date = date or (read_json(brief).get("generated") if brief and os.path.exists(brief) else None) or now()
    from .coverage import latest_snapshot
    snap = latest_snapshot()
    return {"version": __version__, "model": ", ".join(sorted(models)) or "no model", "date": str(date)[:10],
            "snapshot": str((snap or {}).get("taken") or "")[:10] or "none bundled"}


def run_command() -> str:
    """The command that runs this estate again, as typed from the folder the export folder sits in."""
    if not SOURCE_DIR:
        return "kestra-migrate vra run <export folder>"
    cmd = f"kestra-migrate vra run {_arg(rel(SOURCE_DIR))}"
    if STAGES and os.path.abspath(STAGES) != os.path.join(SOURCE_DIR, outdir.OUTPUT_DIRNAME):
        cmd += f" --out {_arg(rel(STAGES))}"
    return cmd


def _arg(s: str) -> str:
    """A path as one shell argument: quoted when it has a space or a character a shell reads."""
    return f'"{s}"' if re.search(r"[^A-Za-z0-9._/:@%+=,~-]", s) else s


ESTATE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
INPUT_SUFFIXES = (".package", ".workflow", ".zip", ".xml")
TREE_MARKERS = ("Workflow", "workflows", "ScriptModule", "actions", "ConfigurationElement",
                "ResourceElement", "PolicyTemplate", "META-INF", "dunes-meta-inf.xml")

# Set by configure(). None until then, so a stage run without an estate fails loudly. Typed as what
# configure() sets: no stage reads them before it.
ESTATE: str = None  # type: ignore[assignment]
SOURCE_DIR: str = None  # type: ignore[assignment]
STAGES: str = None  # type: ignore[assignment]
PRE: str = None  # type: ignore[assignment]
ASSESS: str = None  # type: ignore[assignment]
UNDERSTAND: str = None  # type: ignore[assignment]
BRIEF: str = None  # type: ignore[assignment]
PAGES: str = None  # type: ignore[assignment]
INDEX: str = None  # type: ignore[assignment]
WORK: str = None  # type: ignore[assignment]
# The folder the export folder sits in: paths written into the results and shown on the pages are relative
# to it (`lb-usecase/kestra-assessment/your-input/answers.yaml`), so they read the same from any machine.
BASE: str | None = None
# Model replies are content-addressed; the cache lives in the output folder, so a re-run reuses it.
STAGE_CACHE: str = None  # type: ignore[assignment]
SPEC_DIR: str = None  # type: ignore[assignment]
CLUSTER_SUMMARIES: str = None  # type: ignore[assignment]
STAGE_CALL_LOG: str = None  # type: ignore[assignment]
ENTRY_POINTS: str = None  # type: ignore[assignment]
INPUT: str = None  # type: ignore[assignment]
ANSWERS: str = None  # type: ignore[assignment]
CORRECTIONS: str = None  # type: ignore[assignment]
BRIEF_MD: str = None  # type: ignore[assignment]
DATA: str = None  # type: ignore[assignment]
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
    where = rel(stages_dir)
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
            + "\n".join(steps) + "\nthen run `kestra-migrate vra run` again.")


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
    from kestra_migration_assistant.vra.parse import vra
    names = sorted(n for n in os.listdir(source_dir) if not n.startswith(".") and not outdir.skipped(source_dir, n))
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
    from kestra_migration_assistant.vra.parse import vra
    out = {}
    for n in sorted(os.listdir(source_dir)):
        p = os.path.join(source_dir, n)
        if n.startswith(".") or os.path.islink(p) or outdir.skipped(source_dir, n):
            continue
        if vra.is_vra_input(p):
            out[slug(os.path.splitext(n)[0] if os.path.isfile(p) else n)] = p
    return out


def estate_name(export_dir: str) -> str:
    """The estate's name, from the export folder's name: what the pages and the results call it."""
    name = slug(os.path.basename(os.path.normpath(os.path.abspath(export_dir))))[:64].lstrip("._-")
    return name or "estate"


def configure_export(export_dir: str, out_dir: str | None = None) -> None:
    """
    Point every stage at one export folder: the estate is named after the folder, and everything the tool
    writes goes to `out_dir`, by default `<export folder>/kestra-assessment/`.
    """
    src = os.path.abspath(export_dir)
    if not os.path.isdir(src):
        raise EstateError(f"no such folder: {export_dir}. Pass the folder that holds the export "
                          "(a .package, a Build Tools source tree or .workflow files, and the vRA export)")
    out = os.path.abspath(out_dir) if out_dir else os.path.join(src, outdir.OUTPUT_DIRNAME)
    configure(estate_name(src), source_dir=src, stages_dir=out)


def configure(estate: str, *, source_dir: str | None = None, stages_dir: str | None = None,
              spec_dir: str | None = None, inputs: dict[str, str] | None = None,
              vra_inputs: dict[str, str] | None = None) -> None:
    """Point every stage at one estate: its export folder and its output folder. The only way paths below are set."""
    global ESTATE, SOURCE_DIR, STAGES, PRE, ASSESS, UNDERSTAND, BRIEF, PAGES, INDEX, WORK
    global SPEC_DIR, CLUSTER_SUMMARIES, STAGE_CALL_LOG, INPUTS, IGNORED, VRA_INPUTS, ENTRY_POINTS
    global INPUT, ANSWERS, CORRECTIONS, BRIEF_MD, DATA, BASE, STAGE_CACHE
    if not estate or not ESTATE_NAME.match(estate):
        raise EstateError(f"estate name {estate!r}: use letters, digits, '.', '_' or '-', starting with a "
                          "letter or digit")
    if not source_dir or not stages_dir:
        raise EstateError("configure needs the export folder and the output folder (use configure_export)")
    src = os.path.abspath(source_dir)
    if not os.path.isdir(src):
        raise EstateError(f"no such folder: {source_dir}")
    ESTATE, SOURCE_DIR = estate, src
    BASE = os.path.dirname(src)
    STAGES = os.path.abspath(stages_dir)
    outdir.skip(STAGES)
    PRE = os.path.join(STAGES, LAYOUT["preflight"])
    ASSESS = os.path.join(STAGES, LAYOUT["assess"])
    UNDERSTAND = os.path.join(STAGES, LAYOUT["understand"])  # written by Understand
    BRIEF = os.path.join(STAGES, LAYOUT["brief"])
    PAGES = os.path.join(STAGES, LAYOUT["pages"])            # the detail pages; INDEX (the brief) is the way in
    INDEX = os.path.join(STAGES, "index.html")
    BRIEF_MD = os.path.join(PAGES, "BRIEF.md")                # the brief as a downloadable copy
    DATA = os.path.join(STAGES, "data")
    WORK = os.path.join(ASSESS, "work")                     # re-parsed model files
    SPEC_DIR = spec_dir or UNDERSTAND
    CLUSTER_SUMMARIES = os.path.join(UNDERSTAND, "cluster-summaries.json")
    STAGE_CALL_LOG = os.path.join(DATA, "model-calls.jsonl")
    STAGE_CACHE = os.path.join(DATA, "model-cache")
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
        raise EstateError(f"{rel(src)}/ holds no export: expected a .package, a BTVA "
                          "source tree, or a .workflow file (see INPUTS.md)")


def require_estate() -> None:
    if ESTATE is None:
        raise EstateError("no estate configured: pass the export folder")


def package_rel(path: str) -> str:
    """A file of the installed package as `kestra_migration_assistant/vra/...`, never as a path through the home
    folder or the site-packages of this machine."""
    top = os.path.dirname(os.path.dirname(PACKAGE))          # the folder that holds kestra_migration_assistant/
    return os.path.relpath(path, top).replace(os.path.sep, "/")


def rel(path: str) -> str:
    """
    A path as a reader should see it, and as the results record it: relative to the folder the export folder
    sits in, with forward slashes, so it reads the same on every machine. Before an estate is configured, or for
    a path on another drive, the path as given.
    """
    if BASE is None:
        return path
    try:
        return os.path.relpath(path, BASE).replace(os.path.sep, "/")
    except ValueError:                      # Windows: a path on another drive than the export
        return path


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


# A long stage reports what it is doing while it runs (`run` sets the reporter; nothing else does).
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
    from kestra_migration_assistant.vra import model as M
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
