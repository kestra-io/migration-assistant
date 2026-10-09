"""
vRA exports and entry points: the reader, resolution, the hand-written template and merge,
sanitize coverage and the ZIP guards, on the synthetic fixture in tests/conformance/vra/.

The fixture (sample/, zipped to sample.vra-ng by build.py) is laid out like the public
vmware-samples/build-tools-for-vmware-aria-samples `vra/` project and holds one catalog item with a
form, one cloud template and two subscriptions. "Sample - Install on provision" runs the
aria-xml conformance workflow Install (vRO ID 3617107975829378234967908345654834235);
"Sample - Missing workflow" names a workflow that is in no package. Every name, address and account
in it is invented.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import zipfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from kestra_migration_assistant.vra import entry_points as EP
from kestra_migration_assistant.vra import intake, safety
from kestra_migration_assistant.vra import sanitize as S
from kestra_migration_assistant.vra.parse import vra

VRA_DIR = os.path.join(ROOT, "tests", "conformance", "vra")
TREE = os.path.join(VRA_DIR, "sample")
VRA_NG = os.path.join(VRA_DIR, "sample.vra-ng")
ARIA_XML = os.path.join(ROOT, "tests", "conformance", "aria-xml")
INSTALL_ID = "3617107975829378234967908345654834235"
MISSING_ID = "8F80808080808080808080808080808080808080011756098733430000Deadbeef"
WORKFLOWS = {INSTALL_ID: {"name": "Install", "uid": "aria-xml-Install"}}

# Invented values planted in the fixture, by the category sanitize must redact them as.
PLANTED = {
    "HOST": ["ns1.sample-internal.corp"],
    "IP": ["10.99.88.77"],
    "EMAIL": ["cloud-team@sample.example.com"],
    "USER": ["jdoe-sample", "vro-gateway-Sample0001", "alice.sample"],
    "IDENTITY": ["0a1b2c3d-sample-org"],
    "NETWORK": ["net:sample-dmz", "SAMPLE-DMZ-VLAN42", "site:sample-east"],
}
# What the stages need from the export, which sanitize must leave byte for byte.
KEPT = [INSTALL_ID, MISSING_ID, "compute.provision.post", "deployment.request.post",
        'event.data.customProperties.install == \\"true\\"', "Sample - Install on provision",
        "Cloud.vSphere.Machine", "${input.serverCount}", "${resource.web_net.id}", "sub-sample-install",
        "99999999-8888-7777-6666-555555555555"]


def _zip(entries, path, symlink=None):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries:
            z.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, "/etc/passwd")
    return path


def _tree_entries(tree=TREE):
    out = []
    for dp, _d, fs in os.walk(tree):
        for f in fs:
            p = os.path.join(dp, f)
            out.append((os.path.relpath(p, tree).replace(os.sep, "/"), open(p, "rb").read()))
    return sorted(out)


# ------------------------------------------------------------------ the fixture itself

def test_committed_vra_ng_matches_its_tree():
    with zipfile.ZipFile(VRA_NG) as z:
        committed = sorted((n, z.read(n)) for n in z.namelist())
    assert committed == _tree_entries(), "run: python tests/conformance/vra/build.py"


# ------------------------------------------------------------------ reader

@pytest.fixture(scope="module")
def read():
    return vra.read_vra(VRA_NG)


def test_reader_counts_every_kind(read):
    assert vra.counts(read) == {"cloud templates": 1, "catalog items": 1, "content sources": 1, "entitlements": 1,
                                "subscriptions": 2, "custom resources": 0, "resource actions": 0,
                                "catalog workflows": 0, "property groups": 1, "policies": 1}
    assert read["format"] == "vra-ng" and read["files"] == 11
    assert read["warnings"] == [] and read["unknown"] == [] and read["unread"] == []
    assert read["listed_but_missing"] == [] and read["present_but_unlisted"] == []


def test_reader_reads_subscriptions(read):
    s = {x["name"]: x for x in read["subscriptions"]}
    a = s["Sample - Install on provision"]
    assert (a["event_topic"], a["blocking"], a["runnable_type"], a["runnable_id"], a["timeout_minutes"]) == \
        ("compute.provision.post", True, "extensibility.vro", INSTALL_ID, 5)
    assert a["condition"] == 'event.data.customProperties.install == "true"' and a["projects"] == ["Sample Project"]
    assert s["Sample - Missing workflow"]["blocking"] is False and s["Sample - Missing workflow"]["projects"] == []


def test_reader_reads_the_catalog_item_form_embedded_as_a_string(read):
    c = read["catalog_items"][0]
    assert (c["name"], c["content_source"], c["form"]["present"], c["form"]["source_type"]) == \
        ("Sample Web Server", "Sample Templates", True, "com.vmw.blueprint")
    f = {x["id"]: x for x in c["form_fields"]}
    assert [x["id"] for x in c["form_fields"]] == ["project", "serverCount", "dnsServer"]
    assert f["serverCount"]["type"] == "integer" and f["serverCount"]["required"] and f["serverCount"]["default"] == 1
    assert f["project"]["values"] == "scriptAction: projects" and f["project"]["page"] == "General"


def test_reader_reads_the_cloud_template(read):
    b = read["blueprints"][0]
    assert b["name"] == "Sample Web Server" and [i["name"] for i in b["inputs"]] == ["serverCount", "dnsServer"]
    r = {x["name"]: x for x in b["resources"]}
    assert r["web"]["type"] == "Cloud.vSphere.Machine" and r["web"]["depends_on"] == ["web_net"]
    assert r["web"]["uses_inputs"] == ["dnsServer", "serverCount"]
    assert r["web_net"]["type"] == "Cloud.vSphere.Network"


def test_reader_reads_the_other_kinds(read):
    assert read["entitlements"][0]["project"] == "Sample Project"
    assert read["content_sources"][0]["type_id"] == "com.vmw.blueprint"
    assert read["property_groups"][0]["properties"] == ["ntpServer"]
    assert (read["policies"][0]["kind"], read["policies"][0]["type_id"]) == ("approval", "com.vmware.policy.approval")
    assert read["content_yaml"]["region_mapping"]


def test_a_folder_and_a_build_tools_source_tree_read_the_same(tmp_path, read):
    proj = tmp_path / "proj"
    shutil.copytree(TREE, proj / "src" / "main" / "resources")
    shutil.move(str(proj / "src" / "main" / "resources" / "content.yaml"), str(proj / "content.yaml"))
    (proj / "pom.xml").write_text("<project/>")
    for path in (TREE, str(proj)):
        assert vra.is_vra_input(path)
        r = vra.read_vra(path)
        assert r["format"] == "vra-tree" and r["unknown"] == [] and r["warnings"] == []
        assert EP.from_vra(r, "x")["subscriptions"] == EP.from_vra(read, "x")["subscriptions"]


def test_form_data_in_its_own_file_as_this_customer_export_has_it(tmp_path):
    t = tmp_path / "t"
    shutil.copytree(TREE, t)
    hdr = t / "catalog-items" / "forms" / "Sample Templates__Sample Web Server.json"
    d = json.loads(hdr.read_text())
    form, d["form"] = d["form"], ""
    hdr.write_text(json.dumps(d))
    (t / "catalog-items" / "forms" / "Sample Templates__Sample Web Server__FormData.json").write_text(form)
    c = vra.read_vra(str(t))["catalog_items"][0]
    assert [x["id"] for x in c["form_fields"]] == ["project", "serverCount", "dnsServer"]


def test_unknown_folders_and_files_are_reported_not_dropped(tmp_path):
    t = tmp_path / "t"
    shutil.copytree(TREE, t)
    (t / "extras").mkdir()
    (t / "extras" / "a.json").write_text("{}")
    (t / "extras" / "b.txt").write_text("x")
    (t / "notes.txt").write_text("x")
    r = vra.read_vra(str(t))
    assert {"path": "extras/", "files": 2} in r["unknown"] and {"path": "notes.txt", "files": 1} in r["unknown"]


def test_listed_but_missing_and_malformed_files_are_warnings(tmp_path):
    t = tmp_path / "t"
    shutil.copytree(TREE, t)
    os.remove(t / "subscriptions" / "Sample - Missing workflow.json")
    (t / "subscriptions" / "Broken.json").write_text("{not json")
    r = vra.read_vra(str(t))
    assert "subscription: Sample - Missing workflow" in r["listed_but_missing"]
    assert "subscription: Broken" in r["present_but_unlisted"]
    assert any("Broken.json: does not parse" in w for w in r["warnings"])


# ------------------------------------------------------------------ untrusted JSON and YAML

def test_yaml_aliases_are_refused():
    bomb = "a: &a [x, x]\nb: &b [*a, *a]\nc: [*b, *b]\n"
    with pytest.raises(safety.UnsafeInput, match="aliases"):
        safety.load_yaml(bomb)


def test_yaml_custom_tags_and_deep_nesting_are_refused():
    import yaml
    with pytest.raises(yaml.YAMLError):
        safety.load_yaml("a: !!python/object/apply:os.system ['true']\n")
    with pytest.raises(safety.UnsafeInput, match="deeper"):
        safety.load_yaml("[" * 100 + "]" * 100)


def test_json_limits():
    with pytest.raises(safety.UnsafeInput):
        safety.load_json("[" * 100 + "]" * 100)
    with pytest.raises(ValueError):
        safety.load_json('{"a": NaN}')
    with pytest.raises(safety.UnsafeInput, match="exceeds"):
        safety.load_json(b" " * (safety.MAX_DOC_BYTES + 1))


def test_an_alias_bomb_in_a_blueprint_is_a_warning_not_a_crash(tmp_path):
    t = tmp_path / "t"
    shutil.copytree(TREE, t)
    (t / "blueprints" / "Sample Web Server" / "content.yaml").write_text("a: &a [x]\nb: [*a, *a]\n")
    r = vra.read_vra(str(t))
    assert any("aliases are refused" in w for w in r["warnings"])


# ------------------------------------------------------------------ ZIP guards

@pytest.mark.parametrize("name", ["../evil.json", "subscriptions/../../evil.json", "/etc/evil.json", "C:/evil.json"])
def test_hostile_entry_names_are_refused(tmp_path, name):
    p = _zip(_tree_entries() + [(name, "{}")], str(tmp_path / "evil.vra-ng"))
    with pytest.raises(safety.UnsafeInput):
        vra.read_vra(p)


def test_symlink_entry_is_refused(tmp_path):
    p = _zip(_tree_entries(), str(tmp_path / "evil.vra-ng"), symlink="subscriptions/link.json")
    with pytest.raises(safety.UnsafeInput, match="symlink"):
        vra.read_vra(p)


def test_zip_bomb_is_refused(tmp_path):
    p = _zip(_tree_entries() + [("subscriptions/bomb.json", b"0" * (4 * 1024 * 1024))], str(tmp_path / "bomb.vra-ng"))
    with pytest.raises(safety.UnsafeInput, match="zip bomb"):
        vra.read_vra(p)


def test_too_many_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(safety, "MAX_ZIP_ENTRIES", 5)
    with pytest.raises(safety.UnsafeInput, match="entries"):
        vra.read_vra(VRA_NG)


def test_a_hostile_export_is_reported_by_collect_not_raised(tmp_path):
    p = _zip([("../evil.json", "{}")], str(tmp_path / "evil.vra-ng"))
    ep = EP.collect({"evil": p}, str(tmp_path / "none.yaml"))
    assert ep["exports"][0]["error"].startswith("UnsafeInput") and EP.total(ep["model"]) == 0


def test_not_a_zip_is_reported(tmp_path):
    p = tmp_path / "junk.vra-ng"
    p.write_bytes(b"not a zip")
    ep = EP.collect({"junk": str(p)}, str(tmp_path / "none.yaml"))
    assert "BadZipFile" in ep["exports"][0]["error"]


# ------------------------------------------------------------------ normalize and resolve

@pytest.fixture()
def model(read):
    m = EP.from_vra(read, "sample")
    res = EP.resolve(m, WORKFLOWS)
    return m, res


def test_every_subscription_resolves_or_is_reported(model):
    m, res = model
    s = {x["name"]: x for x in m["subscriptions"]}
    ok = s["Sample - Install on provision"]
    assert (ok["resolved"], ok["resolved_by"], ok["workflow"], ok["workflow_uid"]) == (True, "id", "Install", "aria-xml-Install")
    bad = s["Sample - Missing workflow"]
    assert bad["resolved"] is False and MISSING_ID in bad["why"]
    assert res["resolved"] == 1 and [u["name"] for u in res["unresolved"]] == ["Sample - Missing workflow"]


def test_catalog_item_leads_to_its_cloud_template_and_what_it_creates(model):
    m, res = model
    c = m["catalog_items"][0]
    assert (c["cloud_template"], c["source_type"], c["projects"], c["resolved"]) == \
        ("Sample Web Server", "com.vmw.blueprint", ["Sample Project"], None)
    assert [f["id"] for f in c["form_fields"]] == ["project", "serverCount", "dnsServer"]
    t = m["cloud_templates"][0]
    assert [(x["name"], x["type"]) for x in t["creates"]] == [("web", "Cloud.vSphere.Machine"),
                                                              ("web_net", "Cloud.vSphere.Network")]
    assert res["no_workflow"] == 1


def test_links_for_the_call_graph(model):
    m, _ = model
    edges = {(tuple(e["from"]), tuple(e["to"])) for e in EP.links(m)}
    assert (("catalog item", "Sample Web Server"), ("cloud template", "Sample Web Server")) in edges
    assert (("event topic", "compute.provision.post"), ("workflow", "aria-xml-Install")) in edges
    assert (("event topic", "deployment.request.post"), ("missing workflow", MISSING_ID)) in edges
    assert EP.per_workflow(m)[INSTALL_ID][0]["event_topic"] == "compute.provision.post"


def test_an_abx_subscription_runs_no_workflow_and_is_not_unresolved():
    r = {"subscriptions": [{"name": "abx", "id": "", "description": "", "type": "RUNNABLE", "event_topic": "t",
                            "condition": "", "blocking": False, "disabled": False, "priority": 10,
                            "timeout_minutes": 0, "runnable_type": "extensibility.abx", "runnable_id": "abx-1",
                            "contextual": False, "broadcast": False, "projects": [], "file": "s/abx.json"}],
         "catalog_items": [], "content_sources": [], "blueprints": [], "entitlements": [],
         "resource_actions": [], "custom_resources": [], "catalog_workflows": []}
    m = EP.from_vra(r, "x")
    assert EP.resolve(m, WORKFLOWS)["unresolved"] == [] and m["subscriptions"][0]["resolved"] is None


# ------------------------------------------------------------------ the hand-written template and the merge

def test_template_is_the_same_schema_and_parses_empty(tmp_path):
    p = tmp_path / "entry-points.yaml"
    p.write_text(EP.template())
    data, warnings = EP.load_manual(str(p))
    assert warnings == [] and EP.total(data) == 0 and data["complete"] is False
    assert not EP.is_filled(str(p))
    # the commented example, uncommented, is valid under the same schema
    body = "\n".join(l[2:] for l in EP.template().splitlines() if l.startswith("# ") and not l.startswith("# Example")
                     and (l.startswith("#   ") or l[2:3].isalpha() and l.rstrip().endswith(":")))
    ex = tmp_path / "ex.yaml"
    ex.write_text(body)
    data, warnings = EP.load_manual(str(ex))
    assert warnings == [], warnings
    assert [len(data[k]) for k in EP.KINDS] == [1, 1, 1, 1, 1]
    for k in EP.KINDS:
        assert set(data[k][0]) - {"origin"} <= set(EP.FIELDS[k])


def test_manual_merge_fills_gaps_adds_entries_and_reports_conflicts(tmp_path, read):
    p = tmp_path / "entry-points.yaml"
    p.write_text("""
complete: true
subscriptions:
  - name: Sample - Install on provision
    event_topic: compute.provision.pre          # disagrees with the export
    notes: owned by the platform team
other_triggers:
  - name: Nightly install check
    kind: vro scheduled task
    workflow_name: Install
  - name: Old REST caller
    kind: rest api
    workflow_id: 00000000-dead-0000-0000-000000000000
""")
    manual, warnings = EP.load_manual(str(p))
    assert warnings == []
    m, conflicts = EP.merge([EP.from_vra(read, "sample"), manual])
    res = EP.resolve(m, WORKFLOWS)
    s = {x["name"]: x for x in m["subscriptions"]}["Sample - Install on provision"]
    assert s["event_topic"] == "compute.provision.post" and s["notes"] == "owned by the platform team"
    assert s["origins"] == ["vra:sample!subscriptions/Sample - Install on provision.json", "manual"]
    assert conflicts == [{"kind": "subscriptions", "name": "Sample - Install on provision", "field": "event_topic",
                          "kept": "compute.provision.post",
                          "kept_from": "vra:sample!subscriptions/Sample - Install on provision.json",
                          "other": "compute.provision.pre", "other_from": "manual"}]
    t = {x["name"]: x for x in m["other_triggers"]}
    assert (t["Nightly install check"]["resolved"], t["Nightly install check"]["resolved_by"]) == (True, "name")
    assert t["Old REST caller"]["resolved"] is False
    assert sorted(u["name"] for u in res["unresolved"]) == ["Old REST caller", "Sample - Missing workflow"]
    assert m["complete"] is True


def test_manual_file_problems_are_warnings(tmp_path):
    p = tmp_path / "entry-points.yaml"
    p.write_text("subscriptions:\n  - event_topic: x\n  - name: a\n    colour: red\nwidgets: []\ncatalog_items: {}\n")
    data, warnings = EP.load_manual(str(p))
    assert len(data["subscriptions"]) == 1
    assert any("needs at least a name" in w for w in warnings)
    assert any("unknown field(s)" in w and "colour" in w for w in warnings)
    assert any("unknown section 'widgets'" in w for w in warnings)
    assert any("catalog_items: expected a list" in w for w in warnings)
    p.write_text("subscriptions: [\n")
    data, warnings = EP.load_manual(str(p))
    assert EP.total(data) == 0 and "does not parse" in warnings[0]


# ------------------------------------------------------------------ stages on a temporary estate

@pytest.fixture()
def estate(tmp_path, monkeypatch):
    """aria-xml (holds Install) plus the fixture .vra-ng, as a customer would drop them in sources/."""
    from kestra_migration_assistant.vra.stages import common as C
    monkeypatch.setenv("VRO_NO_MODEL", "1")
    src = tmp_path / "sources" / "vra-test"
    src.mkdir(parents=True)
    shutil.copytree(ARIA_XML, src / "aria-xml")
    shutil.copy(VRA_NG, src / "sample.vra-ng")
    C.configure("vra-test", source_dir=str(src), stages_dir=str(tmp_path / "stages" / "vra-test"))
    return C


def test_estate_inputs_route_the_vra_export(estate):
    C = estate
    assert list(C.INPUTS) == ["aria-xml"] and list(C.VRA_INPUTS) == ["sample"] and C.IGNORED == []


def test_preflight_reports_entry_points_and_marks_q2(estate):
    from kestra_migration_assistant.vra.stages import preflight
    C = estate
    r = preflight.run()
    # a vRA export: no entry-points.yaml in your-input/ (Q2 is answered in answers.yaml)
    assert not os.path.exists(C.ENTRY_POINTS) and not r["entry_points_template_written_this_run"]
    ep = r["export"]["entry_points"]
    assert ep["resolution"]["resolved"] == 1 and len(ep["resolution"]["unresolved"]) == 1
    q2 = next(q for q in r["answers"]["items"] if q["id"] == "Q2")
    assert q2["status"] == "partly answered" and q2["open"]
    assert r["export"]["subscriptions_present"] and r["export"]["catalog_items_present"]
    md = open(os.path.join(C.PRE, "readiness.md")).read()
    assert "PARTLY ANSWERED" in md and "### Not resolved" in md and "Sample - Missing workflow" in md
    # the export was read, so Q2 asks only for what it cannot hold
    assert "entry-points.yaml" not in q2["question"] and ".vra-ng" not in q2["question"]
    assert "none" in q2["question"]
    assert "was read" in q2["found"]
    assert os.path.exists(os.path.join(C.PRE, "entry-points.json"))


def test_preflight_never_overwrites_a_filled_in_template(estate):
    from kestra_migration_assistant.vra.stages import preflight
    C = estate
    preflight.run()
    filled = EP.template().replace("other_triggers: []",
                                   "other_triggers:\n  - name: cron\n    kind: other\n    workflow_name: Install")
    filled = filled.replace("complete: false", "complete: true")
    open(C.ENTRY_POINTS, "w").write(filled)
    r = preflight.run()
    assert open(C.ENTRY_POINTS).read() == filled and not r["entry_points_template_written_this_run"]
    q2 = next(q for q in r["answers"]["items"] if q["id"] == "Q2")
    assert q2["status"] == "answered" and not q2["open"]
    # a file a person wrote from scratch, with no marker, is never touched either
    open(C.ENTRY_POINTS, "w").write("# mine\n")
    preflight.run()
    assert open(C.ENTRY_POINTS).read() == "# mine\n"


def test_assess_adds_entry_points_to_the_call_graph_and_retire_check(estate):
    from kestra_migration_assistant.vra.stages import assess, preflight, triage
    preflight.run()
    a = assess.run()
    w = a["workflows"]["aria-xml-Install"]
    assert w["called_by"] == [] and w["entry_points"][0]["name"] == "Sample - Install on provision"
    assert triage.retire_candidate(w, None) is None
    assert triage.retire_candidate({**w, "entry_points": []}, None) is not None
    c = a["counts"]
    assert (c["entry_points"], c["entry_point_workflows"], c["entry_points_unresolved"]) == (4, 1, 1)
    edges = a["call_graph"]["entry_edges"]
    assert any(tuple(e["from"]) == ("catalog item", "Sample Web Server")
               and tuple(e["to"]) == ("cloud template", "Sample Web Server") for e in edges)
    assert any(tuple(e["to"]) == ("workflow", "aria-xml-Install") for e in edges)


def test_call_graph_page_draws_entry_points(estate):
    from kestra_migration_assistant.vra.stages import assess, preflight, visuals
    preflight.run()
    a = assess.run()
    src = visuals.entry_points_src(a)
    assert "catalog item<br/>Sample Web Server" in src and "cloud template<br/>Sample Web Server" in src
    assert "event topic<br/>compute.provision.post" in src and "not in the package" in src
    assert 'href "workflows/aria-xml-Install.html"' in src and src.count("click ") == 1


# ------------------------------------------------------------------ sanitize

@pytest.fixture(scope="module")
def sanitized(tmp_path_factory):
    out = tmp_path_factory.mktemp("s")
    rep = S.sanitize_inputs({}, str(out / "s"), {"sample": VRA_NG})
    path = str(out / "s" / "sample.vra-ng")
    with zipfile.ZipFile(path) as z:
        files = {n: z.read(n).decode() for n in z.namelist()}
    return rep, files, path


@pytest.mark.parametrize("cat,value", [(c, v) for c, vs in PLANTED.items() for v in vs])
def test_every_planted_vra_value_is_gone(sanitized, cat, value):
    _rep, files, _ = sanitized
    with zipfile.ZipFile(VRA_NG) as z:
        assert any(value in z.read(n).decode() for n in z.namelist()), "the fixture should hold it"
    leaks = [n for n, t in files.items() if value in t]
    assert not leaks, f"{cat} value {value!r} survives in {leaks}"


def test_sanitize_counts_the_vra_categories(sanitized):
    rep, _, _ = sanitized
    for cat in PLANTED:
        assert rep["counts"][cat] >= 1, cat


@pytest.mark.parametrize("value", KEPT)
def test_ids_topics_and_conditions_are_kept(sanitized, value):
    _rep, files, _ = sanitized
    assert any(value in t for t in files.values()), value


def test_sanitized_export_gives_the_same_entry_points(sanitized, read):
    _rep, _files, path = sanitized
    r = vra.read_vra(path)
    assert r["warnings"] == []
    before, after = EP.from_vra(read, "x"), EP.from_vra(r, "x")
    EP.resolve(before, WORKFLOWS)
    EP.resolve(after, WORKFLOWS)
    strip = lambda m: [{k: v for k, v in s.items() if k not in ("description",)} for s in m["subscriptions"]]
    assert strip(before) == strip(after)
    assert [c["cloud_template"] for c in after["catalog_items"]] == ["Sample Web Server"]
    # the same resources; a constraint tag is a network name, which sanitize redacts (NETWORK)
    drop = lambda m: [{k: v for k, v in x.items() if k != "constraint_tags"} for x in m["cloud_templates"][0]["creates"]]
    assert drop(after) == drop(before)
    tags = [x.get("constraint_tags") for x in after["cloud_templates"][0]["creates"] if x.get("constraint_tags")]
    assert tags and all(t.startswith("<NETWORK-") for ts in tags for t in ts)


def test_check_intake_accepts_the_fixture_and_its_long_vcf_style_id(sanitized):
    _rep, _files, path = sanitized
    for p in (VRA_NG, path):
        r = intake.check(p)
        assert r["accepted"], r["findings"]
    assert S.looks_like_key(MISSING_ID), "the ID is key-shaped; only the ID-key exemption lets it through"
