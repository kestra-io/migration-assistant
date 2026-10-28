"""
Sanitize: every planted secret is gone, and the structure every later stage needs is unchanged.

The fixture tests/vra/conformance/sanitize/planted-secrets/ holds invented values only.
"""
from __future__ import annotations

import os
import re
import sys
import zipfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from kestra_migration_assistant.vra import sanitize as S
from kestra_migration_assistant.vra.parse import formats, graph, workflow

CONFORMANCE = os.path.join(ROOT, "tests", "vra", "conformance")
PLANTED = os.path.join(CONFORMANCE, "sanitize", "planted-secrets")
PACKAGE = os.path.join(CONFORMANCE, "pkgdiff-v1.0.package")

# Every value planted in the fixture, by the category it must be redacted as.
PLANTED_VALUES = {
    "HOST": ["vc01.acme-internal.corp", "vro01.acme-internal.corp", "avi-ctrl.lab.example.net"],
    "IP": ["10.20.30.40", "192.168.100.7"],
    "EMAIL": ["ops-team@acme.example.com"],
    "URL": ["https://avi-ctrl.lab.example.net/api/pool",
            # webhooks, shaped so they cannot pass for real ones; Sanitize records the system, never the value
            "https://hooks.slack.com/services/PLANTED/FAKE/not-a-real-hook",
            "https://acme.webhook.office.com/webhookb2/planted-not-a-real-hook",
            "hooks.slack.com", "webhook.office.com", "not-a-real-hook"],
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
    assert rep["dropped_values"] == 5          # 2 SecureString (1 attrib, 1 config) + 3 other config values
    assert rep["files_touched"] == 4


def test_placeholders_are_stable_across_files(planted):
    _rep, files, _ = planted
    wf = files["planted-secrets/Workflow/Planted/Deploy Things.xml"]
    # vc01 appears in the description, an attribute value and a presentation default: one placeholder
    import re
    phs = re.findall(r"<HOST-\d+>", wf)
    vc01 = [p for p in phs if p != re.search(r'"https://(<HOST-\d+>)/api/pool\?name="', wf).group(1)]
    assert len(vc01) == 3 and len(set(vc01)) == 1          # the Avi controller's host is the other one
    # the IP in the script header and in dunes-meta-inf's build host are the same placeholder
    ip_script = re.search(r'"X-Backup", "(<IP-\d+>)"', wf).group(1)
    assert ip_script.strip("<>") in files["planted-secrets/META-INF/dunes-meta-inf.xml"]


def test_secure_and_config_values_dropped_names_and_types_kept(planted):
    _rep, files, _ = planted
    cfg = files["planted-secrets/ConfigurationElement/Planted/Settings.xml"]
    for name, typ in (("endpoint", "string"), ("serviceUser", "string"), ("servicePassword", "SecureString")):
        assert f'<att name="{name}" type="{typ}"' in cfg
    assert cfg.count("<![CDATA[__NULL__]]>") == 5
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
    for _cat, vs in PLANTED_VALUES.items():
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


@pytest.mark.parametrize("tree", ["canvas", "aria-xml", "events", "helpers", "builders", "review", "triage"])
def test_conformance_trees_parse_to_the_same_structure_after_sanitizing(tmp_path, tree):
    """Every raw XML tree in tests/vra/conformance/: the vRO tree of each synthetic estate, and the public ones."""
    here = os.path.join(CONFORMANCE, tree)
    src = os.path.join(here, "vro") if os.path.isdir(os.path.join(here, "vro")) else here
    S.sanitize_inputs({tree: src}, str(tmp_path / "s"))
    a, _ = formats.read_raw_tree(src)
    b, _ = formats.read_raw_tree(str(tmp_path / "s" / os.path.basename(src)))
    wa = {e.name: e for e in a if e.kind == "Workflow"}
    wb = {e.name: e for e in b if e.kind == "Workflow"}
    assert set(wa) == set(wb)
    for name in wa:
        ma, mb = workflow.parse_workflow(wa[name]), workflow.parse_workflow(wb[name])
        ma["graph"], mb["graph"] = graph.shape(ma), graph.shape(mb)
        assert _structure(ma) == _structure(mb), name


# Configuration element values that can't name a host or hold a secret are kept: numbers and booleans by their
# attribute's type, and netmasks. Every string value is still dropped, and every SecureString value.
def _config_values(tmp_path, atts: list[tuple[str, str, str]]) -> tuple[dict, dict[str, str]]:
    src = tmp_path / "export" / "ConfigurationElement" / "Test"
    src.mkdir(parents=True)
    body = "".join(f'    <att name="{n}" type="{t}" read-only="false">\n'
                   f'      <value encoded="n"><![CDATA[{v}]]></value>\n    </att>\n' for n, t, v in atts)
    (src / "Defaults.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<config-element id="0badc0de-0000-4000-8000-000000000001" '
        f'version="1.0.0">\n  <display-name><![CDATA[Defaults]]></display-name>\n  <atts>\n{body}  </atts>\n'
        "</config-element>\n")
    rep = S.sanitize_inputs({"export": str(tmp_path / "export")}, str(tmp_path / "s"))
    out = (tmp_path / "s" / "export" / "ConfigurationElement" / "Test" / "Defaults.xml").read_text()
    import re
    values = dict(re.findall(r'<att name="([^"]+)"[^>]*>\s*<value encoded="n"><!\[CDATA\[(.*?)\]\]>', out))
    return rep, values


def test_config_numbers_are_kept(tmp_path):
    rep, v = _config_values(tmp_path, [("monitorInterval", "number", "5"), ("monitorTimeout", "number", "16.0"),
                                       ("offset", "number", "-1"), ("big", "number", "1.0E9"),
                                       ("portAsText", "string", "8443"), ("notANumber", "number", "avi.lab.example")])
    assert (v["monitorInterval"], v["monitorTimeout"], v["offset"], v["big"]) == ("5", "16.0", "-1", "1.0E9")
    assert v["portAsText"] == v["notANumber"] == "__NULL__"
    assert rep["kept_values"] == 4 and rep["dropped_values"] == 2


def test_config_booleans_are_kept(tmp_path):
    rep, v = _config_values(tmp_path, [("enabled", "boolean", "true"), ("strict", "boolean", "false"),
                                       ("flagAsText", "string", "true"), ("odd", "boolean", "yes-please")])
    assert (v["enabled"], v["strict"]) == ("true", "false")
    assert v["flagAsText"] == v["odd"] == "__NULL__"
    assert rep["kept_values"] == 2 and rep["dropped_values"] == 2


def test_config_netmasks_are_kept(tmp_path):
    rep, v = _config_values(tmp_path, [("mask", "string", "255.255.255.0"), ("hostMask", "string", "255.255.255.255"),
                                       ("prefix", "string", "/24"), ("subnet", "string", "10.20.0.0/24"),
                                       ("notAPrefix", "string", "/33"), ("secretMask", "SecureString", "255.255.0.0"),
                                       ("secretNumber", "SecureString", "123456")])
    assert (v["mask"], v["hostMask"], v["prefix"]) == ("255.255.255.0", "255.255.255.255", "/24")
    assert v["subnet"] == v["notAPrefix"] == v["secretMask"] == v["secretNumber"] == "__NULL__"
    assert rep["kept_values"] == 3 and rep["dropped_values"] == 4


def test_a_netmask_in_text_is_kept_and_an_address_is_not():
    r = S.Redactor()
    out = r.text('{"mask": "255.255.255.255", "address": "10.20.30.40", "broadcast": "255.255.255.0"}')
    assert '"mask": "255.255.255.255"' in out and '"broadcast": "255.255.255.0"' in out
    assert "10.20.30.40" not in out and "<IP-01>" in out


def test_webhook_systems_recorded_never_their_values(planted, tmp_path):
    """A Teams or Slack webhook is known only by its host, which the copy replaces. Sanitize records the system
    against the placeholder and against the configuration attribute whose value it dropped; no value is written."""
    import json
    rep, files, _ = planted
    seen = rep["systems_seen"]
    assert seen.get("slackWebhookUrl") == "slack"
    assert sorted(v for k, v in seen.items() if k.startswith("<URL-")) == ["ms-teams", "slack"]
    wf = files["planted-secrets/Workflow/Planted/Deploy Things.xml"]
    teams_ph = next(k for k, v in seen.items() if v == "ms-teams")
    assert f'var teams = "{teams_ph}";' in wf
    out = json.dumps({k: v for k, v in rep.items() if k != "_masked"})
    for value in ("hooks.slack.com", "webhook.office.com", "not-a-real-hook", "PLANTED/FAKE"):
        assert value not in out


def test_well_known_public_values_are_kept_and_a_documented_api_path_keeps_its_path():
    """A value that identifies nothing (a schema URL, a vendor's documented API path) is kept; the customer's host
    in front of an API path is still replaced, and a webhook URL, whose path is its secret, is replaced whole."""
    from kestra_migration_assistant.vra.sanitize import Redactor
    r = Redactor()
    out = r.text('card["@context"] = "https://schema.org/extensions"; '
                 'u = "https://snow.corp.example.com/api/now/table/sc_req_item?sysparm_limit=1"; '
                 'f = "https://bigip01.corp.example.com/mgmt/tm/ltm/pool"; '
                 'h = "https://hooks.slack.com' '/services/T00000000/B00000000/abcdefghijklmnopqrstuvwx"; '
                 'o = "https://intranet.corp.example.com/team/acme-payments/runbook"')
    assert '"https://schema.org/extensions"' in out
    assert re.search(r'"https://<HOST-\d+>/api/now/table/sc_req_item\?sysparm_limit=1"', out), out
    assert re.search(r'"https://<HOST-\d+>/mgmt/tm/ltm/pool"', out), out
    assert "corp.example.com" not in out and "hooks.slack.com" not in out and "acme-payments" not in out
    assert out.count("<URL-") == 2                    # the webhook and the page with a name in its path
