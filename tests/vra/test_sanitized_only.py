"""
Every stage after Sanitize reads the sanitized copy, never the export: `kestra-migrate vra run` on the
planted-secrets fixture, with a fake model that records every prompt, and no planted value may reach a prompt or
any file in the output folder.

Also: a module named like a domain (com.acme.net, .io, .cloud) is code, not a host. It stays as written, so the
IDs, calls and verdicts from the sanitized copy are the ones the export itself gives.
"""
from __future__ import annotations

import json
import os
import shutil
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import webhooks as W
from test_sanitize import PLANTED_VALUES as _BY_CATEGORY

from kestra_migration_assistant.vra import model as M
from kestra_migration_assistant.vra import sanitize as S
from kestra_migration_assistant.vra.cli import main

PLANTED = W.filled(os.path.join(ROOT, "tests", "vra", "conformance", "sanitize", "planted-secrets"))
HELPERS = os.path.join(ROOT, "tests", "vra", "conformance", "helpers")

# Every value planted in the fixture (the password, the token and the key, each IP, each host name, the email,
# the URL, the accounts, the dropped configuration and SecureString values, the signer's identity), and the
# domain the host names share. Kept in test_sanitize.py, which the repository's secret scan allowlists.
PLANTED_VALUES = [v for vs in _BY_CATEGORY.values() for v in vs] + ["acme-internal.corp"]


def minimal(schema: dict, root: dict | None = None):
    """The smallest value `schema` accepts: what a model that says as little as it may would reply."""
    root = root or schema
    if "$ref" in schema:
        node = root
        for part in schema["$ref"].lstrip("#/").split("/"):
            node = node[part]
        return minimal(node, root)
    for k in ("anyOf", "oneOf"):
        if k in schema:
            return minimal(schema[k][0], root)
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    t = schema.get("type", "object")
    t = next((x for x in t if x != "null"), "null") if isinstance(t, list) else t
    if t == "object":
        return {k: minimal(v, root) for k, v in (schema.get("properties") or {}).items()
                if k in (schema.get("required") or [])}
    if t == "array":
        return [minimal(schema.get("items") or {}, root) for _ in range(schema.get("minItems", 0))]
    if t == "string":
        return "x" * max(1, schema.get("minLength", 1))
    if t in ("integer", "number"):
        return schema.get("minimum", 0)
    if t == "boolean":
        return False
    return None


class Recorder(M.Provider):
    prompts: list[str] = []

    def invoke(self, prompt, schema):
        Recorder.prompts.append(prompt)
        return {"result": json.dumps(minimal(schema)), "is_error": False, "total_cost_usd": 0.0, "duration_api_ms": 1}


def _run(export: str, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))      # no config file of this machine
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-key")
    monkeypatch.delenv("VRO_NO_MODEL", raising=False)
    monkeypatch.setattr(M, "provider_for", lambda cfg, s=None: Recorder(cfg, s))
    Recorder.prompts = []
    assert main(["run", export, "--provider", "anthropic", "--model", "recorder", "--yes"]) == 0
    os.environ.pop("VRO_NO_MODEL", None)


@pytest.mark.parametrize("layout", ["export folder is the tree", "tree inside the export folder"])
def test_no_planted_value_reaches_a_prompt_or_an_output_file(tmp_path, monkeypatch, capsys, layout):
    if layout == "export folder is the tree":
        export = tmp_path / "planted-secrets"
        shutil.copytree(PLANTED, export)
    else:
        export = tmp_path / "planted"
        shutil.copytree(PLANTED, export / "planted-secrets")
    _run(str(export), monkeypatch, tmp_path)
    capsys.readouterr()
    prompts = Recorder.prompts
    assert len(prompts) >= 2, "the run should have asked the model for specs and the brief"
    assert any("Deploy Things" in p for p in prompts)               # the workflow itself was sent, as code
    for value in PLANTED_VALUES:
        for p in prompts:
            assert value not in p, f"{value!r} reached a prompt"
    out = export / "kestra-assessment"
    pre = json.load(open(out / "data" / "0-preflight" / "preflight.json"))
    assert pre["export"]["sources"][0]["build_host_recorded"]          # a fact about the export, still known
    files = [os.path.join(dp, f) for dp, _d, fs in os.walk(out) for f in fs]
    assert len(files) > 20
    for path in files:
        data = open(path, "rb").read()
        for value in PLANTED_VALUES:
            for form in {value, value.replace("&", "&amp;"), json.dumps(value)[1:-1]}:
                assert form.encode() not in data, f"{value!r} in {os.path.relpath(path, out)}"


def test_no_stage_after_sanitize_opens_the_export(tmp_path, monkeypatch, capsys):
    """Every file the run opens after Sanitize is in the output folder (the sanitized copy among it)."""
    import builtins
    export = tmp_path / "planted-secrets"
    shutil.copytree(PLANTED, export)
    opened: list[tuple[str, str]] = []
    real_open, real_run = builtins.open, S.run
    stage = {"after": False}

    def spy_open(file, *a, **kw):
        if stage["after"] and isinstance(file, (str, os.PathLike)):
            opened.append(os.path.abspath(os.fspath(file)))
        return real_open(file, *a, **kw)

    def sanitize_then_watch():
        r = real_run()
        stage["after"] = True
        return r

    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(S, "run", sanitize_then_watch)
    _run(str(export), monkeypatch, tmp_path)
    capsys.readouterr()
    out = str(export / "kestra-assessment")
    from_export = [p for p in opened if p.startswith(str(export) + os.sep) and not p.startswith(out + os.sep)]
    assert opened and not from_export, from_export[:5]


# ------------------------------------------------------------ module names read like domains

def _domain_modules(tmp_path):
    """
    The helpers estate with action modules named like domains: com.example.net (as it is), com.example.util
    renamed com.example.io, and its onlyManual action moved to com.example.cloud (Web - Approve Host calls it).
    """
    src = tmp_path / "src"
    shutil.copytree(os.path.join(HELPERS, "vro"), src / "helpers")
    tree = src / "helpers"
    modules = tree / "ScriptModule" / "com" / "example"
    os.rename(modules / "util", modules / "io")
    (modules / "cloud").mkdir()
    for f in ("onlyManual.xml", "onlyManual.element_info.xml"):
        os.rename(modules / "io" / f, modules / "cloud" / f)

    def edit(path, old, new):
        text = open(path, encoding="utf-8").read()
        assert old in text, (path, old)
        open(path, "w", encoding="utf-8").write(text.replace(old, new))
    for f in os.listdir(modules / "io"):
        if f.endswith(".element_info.xml"):
            edit(modules / "io" / f, "com.example.util", "com.example.io")
    edit(modules / "cloud" / "onlyManual.element_info.xml", "com.example.util", "com.example.cloud")
    web = tree / "Workflow" / "Example" / "Web"
    edit(web / "Web - Provision Host.xml", 'getModule("com.example.util")', 'getModule("com.example.io")')
    edit(web / "Web - Approve Host.xml", 'getModule("com.example.util")', 'getModule("com.example.cloud")')
    return src


def test_module_names_like_domains_are_kept_and_nothing_moves(tmp_path):
    from kestra_migration_assistant.vra.parse import corpus
    src = _domain_modules(tmp_path)
    rep = S.sanitize_inputs({"helpers": str(src / "helpers")}, str(tmp_path / "copy"))
    assert {"com.example.net", "com.example.io", "com.example.cloud"} <= set(rep["kept_names"])
    for tld in ("net", "io", "cloud"):
        assert not any("HOST-" in open(os.path.join(dp, f), encoding="utf-8").read()
                       for dp, _d, fs in os.walk(tmp_path / "copy" / "helpers" / "ScriptModule" / "com" / "example" / tld)
                       for f in fs if f.endswith(".xml")), tld

    def parsed(path, work):
        idx = corpus.parse_corpus(str(tmp_path / work), {"helpers": path}, root=os.path.dirname(path))
        wfs = {w["wid"]: (w["name"], sorted(json.dumps(c, sort_keys=True) for c in w.get("action_calls") or []))
               for w in idx["workflows"]}
        return wfs, sorted(e.get("wid") or e.get("name") for e in idx.get("other_elements") or [])
    assert parsed(str(tmp_path / "copy" / "helpers"), "w2") == parsed(str(src / "helpers"), "w1")


def test_module_names_like_domains_keep_ids_calls_and_verdicts(tmp_path, monkeypatch):
    """The whole assessment from the sanitized copy matches one from the export itself, as before the fix."""
    from kestra_migration_assistant.vra.stages import assess, brief
    from kestra_migration_assistant.vra.stages import common as C
    src = _domain_modules(tmp_path)
    monkeypatch.setenv("VRO_NO_MODEL", "1")
    seen = {}
    for name, read_export in (("export", True), ("copy", False)):
        C.configure("helpers", source_dir=str(src), stages_dir=str(tmp_path / name))
        if read_export:     # what the stages saw before the fix: the export, as its own "copy"
            monkeypatch.setattr(C, "sanitized_inputs", lambda: (dict(C.INPUTS), dict(C.VRA_INPUTS)))
        else:
            monkeypatch.undo()
            monkeypatch.setenv("VRO_NO_MODEL", "1")
        a = assess.run()
        b = brief.run()
        acts = a["actions"].values() if isinstance(a["actions"], dict) else a["actions"]
        seen[name] = {
            "workflows": {u: (w["wid"], w["name"], w["targets"], json.dumps(w["called_actions"], sort_keys=True))
                          for u, w in a["workflows"].items()},
            "actions": sorted(x["uid"] for x in acts),
            "triage": json.dumps(b["triage"], sort_keys=True, default=str),
            "actions_triage": json.dumps(b["action_triage"], sort_keys=True, default=str),
            "waves": json.dumps(b["plan"]["waves"], sort_keys=True, default=str),
        }
    assert any("com.example.net" in u for u in seen["copy"]["actions"])
    assert seen["copy"] == seen["export"]


def test_preflight_says_a_package_is_signed_though_the_copy_drops_its_signatures(tmp_path, monkeypatch):
    from kestra_migration_assistant.vra.parse import formats
    from kestra_migration_assistant.vra.stages import common as C
    from kestra_migration_assistant.vra.stages import preflight
    package = os.path.join(ROOT, "tests", "vra", "conformance", "pkgdiff-v1.0.package")
    assert formats.read_any(package)[1]["signed"]
    export = tmp_path / "signed"
    export.mkdir()
    shutil.copy(package, export / "pkgdiff.package")
    C.configure_export(str(export))
    monkeypatch.setenv("VRO_NO_MODEL", "1")
    exp = preflight.check_export()
    copy = C.copy_of(str(export / "pkgdiff.package"))
    assert not formats.read_any(copy)[1]["signed"]                 # the copy has no signatures/ ...
    assert exp["sources"][0]["signed"] is True                      # ... and Preflight still says the export had
