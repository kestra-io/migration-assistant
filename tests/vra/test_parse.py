"""
Parser unit tests.

Every expected value below was read by hand out of the source XML before being
written here. Where a value is subtle the comment says where in the file it came
from.

Run: .venv/bin/python -m pytest tests -q
"""
from __future__ import annotations

import json
import os
import sys
import zipfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from kestra_migration_assistant.vra.parse import corpus, formats, graph, workflow

CONFORMANCE = os.path.join(ROOT, "tests", "vra", "conformance")
PACKAGE_V10 = os.path.join(CONFORMANCE, "pkgdiff-v1.0.package")
# Every conformance input with workflows, label -> path: the public ones and the synthetic estates' vRO trees.
INPUTS = {
    "pkgdiff-v1.0": PACKAGE_V10,
    "pkgdiff-v1.1": os.path.join(CONFORMANCE, "pkgdiff-v1.1.package"),
    "canvas": os.path.join(CONFORMANCE, "canvas"),
    "aria-xml": os.path.join(CONFORMANCE, "aria-xml"),
    "planted-secrets": os.path.join(CONFORMANCE, "sanitize", "planted-secrets"),
    **{name: os.path.join(CONFORMANCE, name, "vro") for name in ("events", "helpers", "builders", "review", "triage")},
}
_PARSED: dict = {}


def parsed_dir() -> str:
    """Parse every conformance input in INPUTS once."""
    if "dir" not in _PARSED:
        import tempfile
        d = tempfile.mkdtemp(prefix="vro2k-parse-")
        corpus.parse_corpus(d, INPUTS)
        _PARSED["dir"] = d
    return _PARSED["dir"]


def index() -> dict:
    with open(os.path.join(parsed_dir(), "index.json")) as f:
        return json.load(f)


def model(uid: str) -> dict:
    with open(os.path.join(parsed_dir(), "model", f"{uid}.json")) as f:
        return json.load(f)


# ---------------------------------------------------------------- decoding

def test_decode_utf16be_bom():
    # The exact shape found inside a .package: FE FF then UTF-16BE, with a
    # declaration that claims UTF-8.
    text = '<?xml version="1.0" encoding="UTF-8"?><workflow/>'
    raw = b"\xfe\xff" + text.encode("utf-16-be")
    assert formats.decode_bytes(raw) == text
    assert formats.read_xml_text(raw) == "<workflow/>"


def test_decode_utf16le_bom():
    raw = b"\xff\xfe" + "<a/>".encode("utf-16-le")
    assert formats.decode_bytes(raw) == "<a/>"


def test_decode_utf8_bom_and_plain():
    assert formats.decode_bytes(b"\xef\xbb\xbf<a/>") == "<a/>"
    assert formats.decode_bytes(b"<a/>") == "<a/>"


def test_strip_declaration_only_once():
    s = '<?xml version="1.0"?><a><?xml-stylesheet?></a>'
    assert formats.strip_xml_declaration(s) == "<a><?xml-stylesheet?></a>"


# ---------------------------------------------------------------- package

def test_package_element_inventory():
    """package_v1.0 holds 10 elements: verified against the tool run in the eval."""
    els, summary = formats.read_package(PACKAGE_V10)
    assert summary["format"] == "package"
    assert summary["signed"] is True
    counts = summary["element_counts"]
    assert counts["Workflow"] == 2
    assert counts["ConfigurationElement"] == 2
    assert counts["ResourceElement"] == 2
    assert counts["ScriptModule"] == 4
    assert len(els) == 10


def test_package_info_carries_no_name_or_category():
    """The name comes from inside data, the category from the categories file."""
    with zipfile.ZipFile(PACKAGE_V10) as z:
        info = next(n for n in z.namelist()
                    if n.startswith("elements/") and n.endswith("/info"))
        text = formats.decode_bytes(z.read(info))
    assert '<entry key="type">' in text
    assert '<entry key="id">' in text
    assert "categoryPath" not in text
    assert '<entry key="name">' not in text


def test_package_workflow_names_and_categories():
    els, _ = formats.read_package(PACKAGE_V10)
    wfs = {e.name: e for e in els if e.kind == "Workflow"}
    assert set(wfs) == {"WF_test1", "WF_test2"}
    # categories file for these elements reads tests / vro-package-diff
    assert wfs["WF_test1"].category_path == "tests.vro-package-diff"


def test_resource_element_nested_zip_and_binary():
    els, _ = formats.read_package(PACKAGE_V10)
    res = {e.name: e for e in els if e.kind == "ResourceElement"}
    assert "tropical-paradise.jpg" in res
    assert "note.xml" in res
    # The payload is the file itself, unwrapped from VSO-RESOURCE-INF/.
    assert res["tropical-paradise.jpg"].raw[:2] == b"\xff\xd8"  # JPEG SOI
    assert res["note.xml"].raw.lstrip()[:1] == b"<"


def test_config_element_securestring_and_null_sentinel():
    from kestra_migration_assistant.vra.parse.corpus import _config_attrs
    els, _ = formats.read_package(PACKAGE_V10)
    cfg = next(e for e in els if e.kind == "ConfigurationElement"
               and e.name == "test_configurationA")
    atts = {a["name"]: a for a in _config_attrs(cfg.xml)}
    # att0 string "Test value"; att1 Array/string, att2 VCO:RemoteServer and
    # att3 Array/vCACCAFE:Tenant are all __NULL__ in the source.
    assert atts["att0"]["value"] == "Test value"
    assert atts["att1"]["is_null_sentinel"] is True
    assert atts["att2"]["is_null_sentinel"] is True
    assert atts["att2"]["value"] is None, "__NULL__ must not survive as a string"
    assert all(not a["is_secure"] for a in atts.values())


# ---------------------------------------------------------------- workflows

def test_aria_install_minimal():
    m = model("aria-xml-Install")
    assert m["root_item"] == "item1"
    assert m["item_type_counts"] == {"task": 1, "end": 1}
    assert [(i["name"], i["type"]) for i in m["inputs"]] == [("json", "string")]
    assert m["outputs"] == []
    assert m["total_script_lines"] == 2
    assert m["sdk_calls"] == ["System.log"]
    assert m["graph"]["linear"] is True
    assert m["doc_order_would_mislead"] is False


def test_decision_happy_condition_arms():
    """custom-condition item1 points to item2 on true and item0 on false."""
    m = model("canvas-Decision-Happy")
    assert m["root_item"] == "item1"
    assert m["item_type_counts"] == {"custom-condition": 1, "task": 1, "end": 1}
    cond = next(i for i in m["items"] if i["type"] == "custom-condition")
    assert cond["attributes"].get("out-name")
    assert cond["attributes"].get("alt-out-name")
    kinds = {e["kind"] for e in m["edges"]}
    assert "next" in kinds and "alt" in kinds
    # One attribute, waitingTimer of type Date.
    assert [(a["name"], a["type"]) for a in m["attributes"]] == [("waitingTimer", "Date")]


def test_switch_happy_path_cases():
    """The switch's generated script maps case values onto target item names."""
    m = model("canvas-Switch-Happy-Path")
    assert m["item_type_counts"] == {"switch": 1, "task": 4, "end": 1}
    sw = next(i for i in m["items"] if i["type"] == "switch")
    assert len(sw["conditions"]) >= 2
    labels = {c["label"] for c in sw["conditions"]}
    assert all(l for l in labels), "every switch arm carries a target label"
    case_edges = [e for e in m["edges"] if e["kind"] == "case"]
    assert len(case_edges) == len(sw["conditions"])
    assert m["attributes"][0]["name"] == "operationType"


def test_user_interaction_items_and_signature():
    m = model("canvas-User-Interaction")
    assert m["item_type_counts"]["input"] == 3
    names = [i["name"] for i in m["inputs"]]
    assert names == ["security_assignees", "security_assignee_groups",
                     "security_group", "timeout_date"]
    assert [o["name"] for o in m["outputs"]] == ["userInteractionAnswer", "timeoutDate"]


def test_workflow_test_root_differs_from_document_order():
    """
    The case that made the baseline tool wrong: root-name is item6, but the
    first <workflow-item type="task"> in the file is item2.
    """
    m = model("canvas-Workflow-Test")
    assert m["root_item"] == "item6"
    assert m["first_task_in_doc_order"] == "item2"
    assert m["first_task_on_root_path"] == "item6"
    assert m["doc_order_would_mislead"] is True
    assert m["item_type_counts"] == {
        "task": 4, "end": 2, "custom-condition": 1, "link": 1, "waiting-timer": 1}
    # item3 is a link carrying linked-workflow-id, so it is a nested call.
    assert any(c["mode"] == "sync" for c in m["nested_calls"])


def test_ssh_command_full_signature():
    """
    Hand-checked against tests/vra/conformance/triage/vro/Workflow/Example/Lab/Lab - Run Remote Command.xml.
    root-name item6, but item2 is the first task in the file; item6 -> item9 -> item3 (condition) -> item2 /
    item4 -> item5 -> item8 end. Script lines: item2 7, item4 8, item5 3, item6 3, item9 2.
    """
    m = model("triage-Lab---Run-Remote-Command")
    assert m["root_item"] == "item6"
    assert m["first_task_in_doc_order"] == "item2"
    assert m["doc_order_would_mislead"] is True
    assert m["item_type_counts"] == {"task": 5, "condition": 1, "end": 1}

    ins = {i["name"]: i["type"] for i in m["inputs"]}
    assert ins["password"] == "SecureString"
    assert ins["passphrase"] == "SecureString"
    assert ins["passwordAuthentication"] == "boolean"
    assert ins["path"] == "Path"
    assert [o["name"] for o in m["outputs"]] == ["result", "errorText", "outputText"]

    attrs = {a["name"] for a in m["attributes"]}
    assert {"output", "error", "exitCode", "logText", "defaultKeyPairPath"} <= attrs
    assert "SSHSession" in " ".join(s["source"] for s in m["scripts"])
    assert m["total_script_lines"] == 23


def test_securestring_inputs_are_found_across_corpus():
    """
    The baseline tool mapped SecureString workflow inputs to STRING. The parser
    must surface them so the converter can make them SECRET.
    """
    found = []
    for w in index()["workflows"]:
        uid = w["uid"]
        m = model(uid)
        for i in m.get("inputs", []) + m.get("attributes", []):
            if i["type"] == "SecureString":
                found.append((uid, i["name"]))
    assert found, "SecureString inputs exist in the test inputs"
    # hand-checked: two inputs of the SSH command, one input and one attribute of the planted-secrets workflow
    assert {("triage-Lab---Run-Remote-Command", "password"), ("triage-Lab---Run-Remote-Command", "passphrase"),
            ("planted-secrets-Deploy-Things", "adminPassword"), ("planted-secrets-Deploy-Things", "apiSecret")} <= set(found)


# ---------------------------------------------------------------- graph

def test_root_path_excludes_unreachable_items():
    """
    canvas User Interaction.xml: item1 -> item2 -> item3 -> item4 -> item5 -> item0 over out-name. item6 is
    only item4's catch-name (an error edge, not a control edge), so it is not on the root path.
    """
    m = model("canvas-User-Interaction")
    reachable = set(m["root_path_items"])
    assert m["root_item"] in reachable
    assert reachable <= {i["name"] for i in m["items"]}
    assert reachable == {"item0", "item1", "item2", "item3", "item4", "item5"}
    assert "item6" in {i["name"] for i in m["items"]} and "item6" not in reachable
    assert m["graph"]["reachable_from_root"] == len(reachable)


def test_no_false_cycles_on_linear_workflow():
    assert graph.has_cycle(model("aria-xml-Install")) is False


def test_script_normalization_ignores_identifier_and_literal_names():
    a = workflow._norm_script('var host = "prod-01"; System.log("connecting " + host);')
    b = workflow._norm_script('var target = "dev-02"; System.log("connecting " + target);')
    assert a == b, "near-duplicate scripts must normalize to the same text"
    c = workflow._norm_script('for (var i=0;i<3;i++) { System.log(i); }')
    assert c != a


def test_script_normalization_strips_comments():
    a = workflow._norm_script("// a comment\nvar x = 1;")
    b = workflow._norm_script("/* block */ var x = 1;")
    assert a == b


# ---------------------------------------------------------------- corpus-wide

def test_every_workflow_parsed_without_error():
    idx = index()
    assert idx["counts"]["parse_failures"] == 0
    # canvas 23, aria-xml 1, pkgdiff 2 + 3, planted-secrets 1, events 3, helpers 2, builders 1, review 3, triage 10
    assert idx["counts"]["workflows_parsed"] == 49


def test_every_edge_target_exists_or_is_warned():
    idx = index()
    for w in idx["workflows"]:
        m = model(w["uid"])
        names = {i["name"] for i in m["items"]}
        for e in m["edges"]:
            if e["to"] not in names:
                assert any("has no such item" in x for x in m["warnings"]), \
                    f"{w['uid']} has a dangling edge with no warning"


# ---------------------------------------------------------------- default error handler

PSCOE = os.path.join(CONFORMANCE, "canvas", "workflows", "VMware", "PSCoE")


def _parse_canvas(name: str) -> dict:
    """Parse one canvas workflow straight from its XML, so the test checks the parser, not a stale model file."""
    elements, _ = formats.read_raw_tree(PSCOE)
    el = next(e for e in elements if e.file_stem == name)
    m = workflow.parse_workflow(el)
    m["graph"] = graph.shape(m)
    return m


# name -> (handler target, throw-bind-name, items reachable only through the handler)
# Read by hand from each file's <error-handler> element and the target's out-name chain.
ERROR_HANDLER_CASES = {
    "Default Error Handler Custom Item": ("item3", "errorMessage", ["item3", "item5"]),
    "Default Error Handler Happy": ("item0", "errorMessage", []),
    "Workflow Test": ("item8", "errorMessage", ["item8"]),
}


@pytest.mark.parametrize("name", sorted(ERROR_HANDLER_CASES))
def test_default_error_handler_is_parsed(name):
    target, bind, only_via_handler = ERROR_HANDLER_CASES[name]
    m = _parse_canvas(name)
    h = m["default_error_handler"]
    assert h == {"target": target, "throw_bind_name": bind}
    assert target in {i["name"] for i in m["items"]}
    assert not [w for w in m["warnings"] if "error-handler" in w]
    # The handler is not an item's catch-name, and not an edge with a source item.
    assert all(e["from"] in {i["name"] for i in m["items"]} for e in m["edges"])
    # Items on the handler's path run on an uncaught error, so they are not unreachable.
    path = m["graph"]["error_handler_path"]
    assert path[0] == target
    assert not set(path) & set(m["graph"]["unreachable_items"])
    assert sorted(set(path) - set(graph.root_path_items(m))) == only_via_handler


def test_custom_item_handler_path_follows_out_name():
    """processError (item3) runs, then ends at item5; before the fix both showed as unreachable."""
    m = _parse_canvas("Default Error Handler Custom Item")
    assert m["graph"]["error_handler_path"] == ["item3", "item5"]
    assert m["graph"]["unreachable_items"] == []


def test_workflow_without_error_handler_has_none():
    m = _parse_canvas("Decision Happy")
    assert m["default_error_handler"] is None
    assert m["graph"]["error_handler_path"] == []
