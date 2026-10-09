"""
Sanitize: every planted secret is gone, and the structure every later stage needs is unchanged.

The fixture tests/conformance/sanitize/planted-secrets/ holds invented values only.
"""
from __future__ import annotations

import os
import sys
import zipfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from kestra_migration_assistant.vra import sanitize as S  # noqa: E402
from kestra_migration_assistant.vra.parse import formats, graph, workflow  # noqa: E402

PLANTED = os.path.join(ROOT, "tests", "conformance", "sanitize", "planted-secrets")
PACKAGE = os.path.join(ROOT, "tests", "conformance", "pkgdiff-v1.0.package")

# Every value planted in the fixture, by the category it must be redacted as.
PLANTED_VALUES = {
    "HOST": ["vc01.acme-internal.corp", "vro01.acme-internal.corp", "avi-ctrl.lab.example.net"],
    "IP": ["10.20.30.40", "192.168.100.7"],
    "EMAIL": ["ops-team@acme.example.com"],
    "URL": ["https://avi-ctrl.lab.example.net/api/pool"],
    "USER": ["svc-vro-deploy", "jane.doe"],
    "SECRET": ["Hunter2!Winter", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ2cm8ifQ.c2lnbmF0dXJlc2lnbmF0dXJl",
               "AbCdEf0123456789XyZabcdEF0123"],
    "DROPPED": ["S3cr3tValue!9", "Pl@ntedPassw0rd"],
    "IDENTITY": ["O=Acme,OU=Platform"],
}


@pytest.fixture(scope="module")
def planted(tmp_path_factory):
    out = tmp_path_factory.mktemp("sanitized")
    rep = S.sanitize_inputs({"planted-secrets": PLANTED}, str(out / "s"))
    files = {}
    for dp, _d, fs in os.walk(out / "s"):
        for f in fs:
            p = os.path.join(dp, f)
            files[os.path.relpath(p, out / "s")] = open(p, encoding="utf-8").read()
    return rep, files, str(out / "s" / "planted-secrets")


@pytest.mark.parametrize("cat,value", [(c, v) for c, vs in PLANTED_VALUES.items() for v in vs])
def test_every_planted_value_is_gone(planted, cat, value):
    _rep, files, _ = planted
    leaks = [f for f, t in files.items() if value in t]
    assert not leaks, f"{cat} value {value!r} survives in {leaks}"


def test_counts_per_category(planted):
    rep, _, _ = planted
    for cat in ("HOST", "IP", "URL", "EMAIL", "USER", "SECRET", "IDENTITY"):
        assert rep["counts"][cat] >= 1, cat
    assert rep["dropped_values"] == 4          # 2 SecureString (1 attrib, 1 config) + 2 other config values
    assert rep["files_touched"] == 4


def test_placeholders_are_stable_across_files(planted):
    _rep, files, _ = planted
    wf = files["planted-secrets/Workflow/Planted/Deploy Things.xml"]
    # vc01 appears in the description, an attribute value and a presentation default: one placeholder
    import re
    phs = re.findall(r"<HOST-\d+>", wf)
    assert len(phs) == 3 and len(set(phs)) == 1
    # the IP in the script header and in dunes-meta-inf's build host are the same placeholder
    ip_script = re.search(r'"X-Backup", "(<IP-\d+>)"', wf).group(1)
    assert ip_script.strip("<>") in files["planted-secrets/META-INF/dunes-meta-inf.xml"]


def test_secure_and_config_values_dropped_names_and_types_kept(planted):
    _rep, files, _ = planted
    cfg = files["planted-secrets/ConfigurationElement/Planted/Settings.xml"]
    for name, typ in (("endpoint", "string"), ("serviceUser", "string"), ("servicePassword", "SecureString")):
        assert f'<att name="{name}" type="{typ}"' in cfg
    assert cfg.count("<![CDATA[__NULL__]]>") == 4
    wf = files["planted-secrets/Workflow/Planted/Deploy Things.xml"]
    assert '<attrib name="apiSecret" type="SecureString" read-only="false">\n\t\t<value encoded="n"><![CDATA[__NULL__]]>' in wf


def test_identity_stripped(planted):
    _rep, files, _ = planted
    meta = files["planted-secrets/META-INF/dunes-meta-inf.xml"]
    assert '<entry key="pkg-owner">&lt;IDENTITY-01&gt;</entry>' in meta


def _model(tree, name):
    els, _ = formats.read_raw_tree(tree)
    m = workflow.parse_workflow(next(e for e in els if e.kind == "Workflow" and e.name == name))
    m["graph"] = graph.shape(m)
    return m


def _structure(m):
    """Everything a later stage relies on, minus script text and values."""
    return {
        "root": m["root_item"], "id": m["id"], "name": m["name"],
        "items": [(i["name"], i["type"], sorted(i["attributes"].items()), i.get("in_bindings"), i.get("out_bindings"),
                   i.get("conditions")) for i in m["items"]],
        "edges": m["edges"], "inputs": m["inputs"], "outputs": m["outputs"],
        "attributes": [(a["name"], a["type"]) for a in m["attributes"]],
        "graph": m["graph"], "handler": m.get("default_error_handler"),
    }


def test_graph_and_bindings_unchanged(planted):
    _rep, _files, out = planted
    before, after = _model(PLANTED, "Deploy Things"), _model(out, "Deploy Things")
    assert not after.get("parse_error")
    assert _structure(before) == _structure(after)
    # the script still has its logic; only literals changed
    s = next(i for i in after["items"] if i["name"] == "item1")["script"]
    assert 'new RESTRequest("POST", url)' in s and "req.execute()" in s


def test_unsure_names_are_reported_not_redacted(planted):
    rep, files, _ = planted
    assert any(u["preview"] == "esx-host-42" for u in rep["unsure"])
    assert "esx-host-42" in files["planted-secrets/Workflow/Planted/Deploy Things.xml"]


def test_report_never_prints_a_redacted_value(planted, tmp_path):
    rep, _, _ = planted
    from kestra_migration_assistant.vra.stages import common as C
    rep = {**rep, "estate": "planted", "generated": "now", "out_dir": str(tmp_path), "source_dir": "x"}
    md = S.report_md(rep, C)
    for cat, vs in PLANTED_VALUES.items():
        for v in vs:
            assert v not in md, v


def test_package_is_rebuilt_readable_and_unsigned(tmp_path):
    rep = S.sanitize_inputs({"pkgdiff-v1.0": PACKAGE}, str(tmp_path / "s"))
    out = tmp_path / "s" / "pkgdiff-v1.0.package"
    names = zipfile.ZipFile(out).namelist()
    assert not [n for n in names if n.startswith(("signatures/", "certificates/"))]
    assert rep["stripped_entries"] > 0 and rep["counts"]["IDENTITY"] >= 1
    before, _ = formats.read_package(PACKAGE)
    after, _ = formats.read_package(str(out))
    assert sorted((e.kind, e.name, e.category_path) for e in before) == \
        sorted((e.kind, e.name, e.category_path) for e in after)
    raw = zipfile.ZipFile(out).read(next(n for n in names if n.endswith("/data") and "elements/" in n))
    assert raw[:2] == b"\xfe\xff", "UTF-16BE with its BOM, as vRO wrote it"
    for n in names:
        if n.endswith("/info"):
            assert "lab-os-vraapp" not in zipfile.ZipFile(out).read(n).decode("utf-8", "replace")


@pytest.mark.parametrize("sample,tree", [("avi", "alb"), ("nuage", "vspk")])
def test_samples_parse_to_the_same_structure_after_sanitizing(tmp_path, sample, tree):
    src = os.path.join(ROOT, "samples", sample, tree)
    S.sanitize_inputs({tree: src}, str(tmp_path / "s"))
    a, _ = formats.read_raw_tree(src)
    b, _ = formats.read_raw_tree(str(tmp_path / "s" / tree))
    wa = {e.name: e for e in a if e.kind == "Workflow"}
    wb = {e.name: e for e in b if e.kind == "Workflow"}
    assert set(wa) == set(wb)
    for name in wa:
        ma, mb = workflow.parse_workflow(wa[name]), workflow.parse_workflow(wb[name])
        ma["graph"], mb["graph"] = graph.shape(ma), graph.shape(mb)
        assert _structure(ma) == _structure(mb), name
