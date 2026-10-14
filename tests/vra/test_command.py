"""
`kestra-migrate vra run <export folder>`: where it reads, where it writes, and what a second run keeps.

- an export folder whose name has spaces, given relative to the current folder;
- Windows-style paths (drive letters, backslashes, case-insensitive names), with ntpath standing in for os.path;
- a read-only export folder: refused without --out, assessed with it, and left untouched;
- a run over an existing output: the tool never reads its own output, re-uses the model cache, keeps answers;
- no output file names a folder of this machine (the export's absolute path, or the home folder).
"""
from __future__ import annotations

import json
import ntpath
import os
import shutil
import stat

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HELPERS = os.path.join(ROOT, "tests", "vra", "conformance", "helpers")


def _export(folder) -> str:
    """The helpers conformance estate as an export folder: a raw vRO tree and a vRA content tree."""
    shutil.copytree(os.path.join(HELPERS, "vro"), os.path.join(folder, "vro"))
    shutil.copytree(os.path.join(HELPERS, "vra"), os.path.join(folder, "helpers-vra"))
    return str(folder)


def _main(*args) -> int:
    from kestra_migration_assistant import cli
    return cli.main(["vra", *args])


def _tree(top) -> dict[str, bytes]:
    out = {}
    for dp, _d, files in os.walk(top):
        for f in files:
            p = os.path.join(dp, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, top)] = fh.read()
    return out


@pytest.fixture(autouse=True)
def _no_model(monkeypatch):
    monkeypatch.setenv("VRO_NO_MODEL", "1")


# ------------------------------------------------------------------ names and paths

def test_no_output_file_names_a_folder_of_this_machine(tmp_path):
    """The output folder gets zipped and shared: no file in it, the parse cache included, may carry the export
    folder's absolute path or the user's home folder. Run on a raw tree, a vRA export and a .package."""
    export = _export(tmp_path / "export")
    shutil.copy(os.path.join(ROOT, "tests", "vra", "conformance", "pkgdiff-v1.0.package"), export)
    assert _main("run", export, "--no-model") == 0
    leaks = {os.path.realpath(export), os.path.abspath(export), os.path.expanduser("~")}
    found = [(rel, p) for rel, body in _tree(os.path.join(export, "kestra-assessment")).items()
             for p in leaks if p.encode() in body or p.replace(os.sep, "\\\\").encode() in body]
    assert any(r.startswith(os.path.join("data", "1-assess", "work")) for r in _tree(os.path.join(export, "kestra-assessment")))
    assert not found, found


def test_an_export_folder_with_spaces_given_relative(tmp_path, monkeypatch, capsys):
    export = _export(tmp_path / "Customer exports" / "LB use case 2026")
    monkeypatch.chdir(tmp_path / "Customer exports")
    assert _main("run", "LB use case 2026", "--no-model") == 0
    out = os.path.join(export, "kestra-assessment")
    assert os.path.exists(os.path.join(out, "index.html"))
    assert os.path.exists(os.path.join(out, "your-input", "answers.yaml"))
    brief = json.load(open(os.path.join(out, "data", "3-brief", "brief.json")))
    assert brief["estate"] == "LB-use-case-2026"                 # named after the folder
    # the last line points at the page, relative to where the command was run
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last.endswith(os.path.join("LB use case 2026", "kestra-assessment", "index.html")), last
    # paths recorded in the results are relative to the folder the export sits in, never absolute
    assess = open(os.path.join(out, "data", "1-assess", "assess.json"), encoding="utf-8").read()
    assert str(tmp_path) not in assess and "LB use case 2026/vro" in assess


def test_the_estate_name_comes_from_the_folder_name():
    from kestra_migration_assistant.vra.stages import common as C
    assert C.estate_name("/x/lb-usecase") == "lb-usecase"
    assert C.estate_name("/x/lb-usecase/") == "lb-usecase"
    assert C.estate_name("/x/My Estate (prod)") == "My-Estate-prod"
    assert C.estate_name("/x/...") == "estate"
    assert C.ESTATE_NAME.match(C.estate_name("/x/" + "a" * 200))


def test_windows_style_paths(monkeypatch):
    """Drive letters, backslashes and case-insensitive names, with ntpath standing in for os.path."""
    from kestra_migration_assistant.vra import outdir
    from kestra_migration_assistant.vra.stages import common as C
    monkeypatch.setattr(os, "path", ntpath)
    assert C.estate_name("C:\\Users\\ops\\Exports\\LB use case\\") == "LB-use-case"
    monkeypatch.setattr(C, "BASE", "C:\\Users\\ops\\Exports")
    # a path under the export's folder is recorded with forward slashes
    assert C.rel("C:\\Users\\ops\\Exports\\LB\\kestra-assessment\\index.html") == "LB/kestra-assessment/index.html"
    # an output folder on another drive (--out D:\...) is shown as given, not as a broken relative path
    assert C.rel("D:\\out\\index.html") == "D:\\out\\index.html"
    # the output folder is skipped whatever its case, and an --out folder inside the export too
    assert outdir.skipped("C:\\Exports\\LB", "Kestra-Assessment") is False      # the default name is exact
    outdir.skip("C:\\Exports\\LB\\Results")
    assert outdir.skipped("c:\\exports\\lb", "RESULTS")
    from kestra_migration_assistant.vra import cli
    assert cli.shown("D:\\out\\index.html").endswith("index.html")


# ------------------------------------------------------------------ a read-only export

@pytest.fixture
def read_only_export(tmp_path):
    export = _export(tmp_path / "ro export")
    modes = []
    for dp, _dirs, files in os.walk(export, topdown=False):
        for f in files:
            p = os.path.join(dp, f)
            os.chmod(p, stat.S_IRUSR | stat.S_IRGRP)
        os.chmod(dp, stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP)
        modes.append(dp)
    yield export
    for dp in reversed(modes):
        os.chmod(dp, stat.S_IRWXU)
    for dp, _d, files in os.walk(export):
        for f in files:
            os.chmod(os.path.join(dp, f), stat.S_IRUSR | stat.S_IWUSR)


def test_a_read_only_export_is_refused_without_out(read_only_export, capsys):
    if os.access(read_only_export, os.W_OK):
        pytest.skip("running as a user who can write anywhere")
    assert _main("run", read_only_export, "--no-model") == 2
    err = capsys.readouterr().err
    assert "--out" in err and "Traceback" not in err


def test_a_read_only_export_is_assessed_with_out_and_left_untouched(read_only_export, tmp_path):
    before = _tree(read_only_export)
    out = tmp_path / "results elsewhere"
    assert _main("run", read_only_export, "--out", str(out), "--no-model") == 0
    assert (out / "index.html").exists() and (out / "your-input" / "answers.yaml").exists()
    assert (out / "data" / "1-assess" / "assess.json").exists()
    assert _tree(read_only_export) == before
    assert _main("status", read_only_export, "--out", str(out)) == 0


# ------------------------------------------------------------------ running again

def test_a_second_run_reads_the_same_export_not_its_own_output(tmp_path):
    """The output folder holds a sanitized copy of the export; the next run must not read it as input."""
    export = _export(tmp_path / "estate")
    assert _main("run", export, "--no-model") == 0
    out = os.path.join(export, "kestra-assessment")
    assert os.path.isdir(os.path.join(out, "data", "0-preflight", "sanitized"))
    first = json.load(open(os.path.join(out, "data", "1-assess", "assess.json")))
    assert _main("run", export, "--no-model") == 0
    second = json.load(open(os.path.join(out, "data", "1-assess", "assess.json")))
    assert sorted(second["workflows"]) == sorted(first["workflows"])
    assert second["counts"] == first["counts"]
    pre = json.load(open(os.path.join(out, "data", "0-preflight", "preflight.json")))
    read = [x["path"] for x in pre["export"]["sources"]] + [x["path"] for x in pre["export"].get("vra_exports", [])]
    assert read and not [p for p in read if "kestra-assessment" in p], read


def test_an_export_that_is_itself_a_tree_never_reads_its_output(tmp_path):
    """An export folder that is a Build Tools tree (Workflow/ at the top) is one input: the walk prunes the output."""
    export = tmp_path / "tree export"
    shutil.copytree(os.path.join(HELPERS, "vro"), export)
    assert _main("run", str(export), "--no-model") == 0
    a = json.load(open(export / "kestra-assessment" / "data" / "1-assess" / "assess.json"))
    assert _main("run", str(export), "--no-model") == 0
    b = json.load(open(export / "kestra-assessment" / "data" / "1-assess" / "assess.json"))
    assert a["counts"] == b["counts"] and sorted(a["workflows"]) == sorted(b["workflows"])


def _minimal(schema: dict):
    """The smallest value that passes model.validate for `schema`: what a stand-in model answers."""
    t = schema.get("type")
    if t == "object":
        props = schema.get("properties", {})
        return {k: _minimal(props.get(k, {"type": "string"})) for k in schema.get("required", [])}
    if t == "array":
        return [_minimal(schema.get("items", {"type": "string"})) for _ in range(schema.get("minItems", 0))]
    if t == "string":
        return (schema.get("enum") or ["a stand-in answer, long enough for any minimum length".ljust(
            schema.get("minLength", 0), ".")])[0]
    if t in ("number", "integer"):
        return 0
    if t == "boolean":
        return False
    return None


def test_a_second_run_reuses_the_model_cache_and_keeps_every_answer(tmp_path, monkeypatch):
    from kestra_migration_assistant.vra import model as M
    from kestra_migration_assistant.vra.stages import common as C
    from kestra_migration_assistant.vra.stages import your_input as Y
    monkeypatch.delenv("VRO_NO_MODEL")
    # A provider of its own, so the run never depends on what this machine has set up (a Claude Code login, a key,
    # a config file): with nothing found, it would go ahead without a model and never reach the stand-in
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("KESTRA_MIGRATE_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "placeholder")
    calls = []

    class StandIn(M.Provider):
        def invoke(self, prompt, schema):
            calls.append(1)
            return {"result": json.dumps(_minimal(schema)), "is_error": False, "total_cost_usd": 0.0}

    monkeypatch.setattr(M, "provider_for", lambda cfg, s=None: StandIn(cfg))
    export = _export(tmp_path / "estate")
    assert _main("run", export) == 0
    first = len(calls)
    assert first > 0, "the first run asks the model"
    out = os.path.join(export, "kestra-assessment")
    assert os.listdir(os.path.join(out, "data", "model-cache"))
    log_path = os.path.join(out, "data", "model-calls.jsonl")
    logged = len(open(log_path).readlines())
    # a person answers something and signs
    import yaml
    path = os.path.join(out, "your-input", "answers.yaml")
    doc = yaml.safe_load(open(path))
    item = (doc.get("open") or doc.get("answered"))[0]
    Y.set_answer(item["id"], "kept as written, 42")
    # the next run calls no model: every reply comes from the cache in the output folder
    assert _main("run", export) == 0
    assert len(calls) == first
    again = [json.loads(x) for x in open(log_path).readlines()[logged:]]
    again = [r for r in again if not r.get("event")]
    assert again and all(r.get("cached") for r in again), [(r["purpose"], r.get("cached")) for r in again]
    flat = Y.flat(yaml.safe_load(open(path)))
    assert flat[item["id"]]["answer"] == "kept as written, 42"
    assert C.STAGE_CACHE == os.path.join(out, "data", "model-cache")


def test_a_persons_words_survive_two_runs_byte_for_byte(tmp_path):
    """`yes`, `no`, `on`, `off`, `y` and `42` are what the person typed, not YAML 1.1 booleans or a number: the tool
    reads them as text and writes them back exactly, unquoted."""
    import re

    import yaml
    export = _export(tmp_path / "estate")
    assert _main("run", export, "--no-model") == 0
    path = os.path.join(export, "kestra-assessment", "your-input", "answers.yaml")
    text = open(path, encoding="utf-8").read()
    words = ["yes", "no", "on", "off", "y", "42"]
    typed: dict[str, str] = {}
    lines, cur = text.split("\n"), None
    for i, ln in enumerate(lines):
        m = re.match(r"^\s*- id: (\S+)", ln)
        cur = m.group(1) if m else cur
        m = re.match(r"^(\s*)answer:\s*$", ln)
        if m and cur and len(typed) < len(words):
            typed[cur] = words[len(typed)]
            lines[i] = f"{m.group(1)}answer: {typed[cur]}"
    assert len(typed) == len(words)
    open(path, "w", encoding="utf-8").write("\n".join(lines))
    for _ in range(2):
        assert _main("run", export, "--no-model") == 0
    after = open(path, encoding="utf-8").read()
    entries = re.split(r"\n(?=\s*- id: )", after)
    for k, w in typed.items():
        e = next(x for x in entries if re.match(rf"\s*- id: {re.escape(k)}\n", x))
        assert re.search(rf"\n\s*answer: {re.escape(w)}(\n|$)", e), (k, w, e)
    assert not re.search(r"answer: (true|false|'yes'|'no'|'on'|'off'|'y'|'42')\s*$", after, re.M)
    assert yaml.safe_load(after)                       # still YAML any reader parses
