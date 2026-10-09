"""
Event-driven estates, checked on synthetic content (tests/vra/conformance/events/, CONFORMANCE.md).

The estate: three vRO workflows started by vRA subscriptions, IPAM calls through a generic REST helper
action, state kept in a configuration element, one dispatcher that switches on the event type, a
catalog item and a cloud template. Nothing here names a real estate.

Covers: action calls through a module variable (parse/jscalls.py); systems reached only through an
action; REST helpers named by vendor signature; the domain name built from content; triage on logic,
not on the trigger; trigger notes, lifecycle groups, the trigger plan for both front-end answers;
waves that keep a lifecycle group together; the brief's wording about triggers and history; plugin
coverage from a snapshot.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from kestra_migration_assistant.vra.parse.jscalls import mask, module_calls
from kestra_migration_assistant.vra.stages import common as C
from kestra_migration_assistant.vra.stages import triggers as TG

EVENTS = os.path.join(ROOT, "tests", "vra", "conformance", "events")
REG, UNREG, ROUTER = "events-Net---Register-Host", "events-Net---Unregister-Host", "events-Net---Event-Router"


# ------------------------------------------------------------------ jscalls

def test_direct_call():
    assert module_calls('System.getModule("a.b").doIt(1);') == [("a.b", "doIt")]


def test_call_through_a_variable_var_let_const():
    src = ('var a = System.getModule("m.a");\nlet b = System.getModule("m.b");\nconst c = System.getModule("m.c");\n'
           'a.one(); b.two(); c.three();')
    assert module_calls(src) == [("m.a", "one"), ("m.b", "two"), ("m.c", "three")]


def test_reassignment_changes_the_module_from_that_point():
    src = ('var x = System.getModule("m.v1");\nx.first();\nx = System.getModule("m.v2");\nx.second();\n'
           'x = somethingElse();\nx.third();')
    assert module_calls(src) == [("m.v1", "first"), ("m.v2", "second")]


def test_alias_and_several_modules_in_one_declaration():
    src = 'var a = System.getModule("m.a"), b = System.getModule("m.b");\nvar c = a;\nc.viaAlias();\nb.other();'
    assert module_calls(src) == [("m.a", "viaAlias"), ("m.b", "other")]


def test_comments_and_strings_are_not_calls():
    src = ('var a = System.getModule("m.a");\n// a.inComment();\n/* a.inBlock(); */\n'
           'var s = "a.inString()";\na.real();')
    assert module_calls(src) == [("m.a", "real")]
    assert len(mask(src)) == len(src)


def test_call_split_over_lines_and_inside_a_closure():
    src = ('var a = System.getModule("m.a");\nvar undo = [];\nundo.push(function () { return a.rollback(1); });\n'
           'System.getModule("m.b")\n    .later(2);')
    assert module_calls(src) == [("m.a", "rollback"), ("m.b", "later")]


def test_a_variable_holding_what_an_action_returns_is_not_the_module():
    src = ('var element = System.getModule("m.c").getRegistryElement();\n'
           'var v = element.getAttributeWithKey("k");\nelement.setAttributeWithKey("k", 1);')
    assert module_calls(src) == [("m.c", "getRegistryElement")]


def test_no_module_no_calls():
    assert module_calls("var a = {}; a.b();") == []
    assert module_calls(None) == []


# ------------------------------------------------------------------ the estate, run through the stages

def _configure(tmp):
    C.configure("events", source_dir=EVENTS, stages_dir=str(tmp), spec_dir=os.path.join(EVENTS, "specs"),
                inputs={"events": os.path.join(EVENTS, "vro")},
                vra_inputs={"events-vra": os.path.join(EVENTS, "vra")})


@pytest.fixture(scope="module")
def estate_run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("events-stages")
    old = os.environ.get("VRO_NO_MODEL")
    os.environ["VRO_NO_MODEL"] = "1"
    try:
        _configure(tmp)
        from kestra_migration_assistant.vra.stages import assess as A
        from kestra_migration_assistant.vra.stages import brief as B
        from kestra_migration_assistant.vra.stages import preflight as P
        P.run()
        a = A.run()
        b = B.run()
        brief_md = open(C.BRIEF_MD).read()
        report = open(os.path.join(C.ASSESS, "estate-report.md")).read()
    finally:
        if old is None:
            os.environ.pop("VRO_NO_MODEL", None)
    return {"assess": a, "brief": b, "md": brief_md, "report": report, "tmp": str(tmp)}


@pytest.fixture
def estate(estate_run):
    """The events estate's results, with the stage paths pointing at it again (other tests re-point them)."""
    _configure(estate_run["tmp"])
    old = os.environ.get("VRO_NO_MODEL")
    os.environ["VRO_NO_MODEL"] = "1"
    yield estate_run
    if old is None:
        os.environ.pop("VRO_NO_MODEL", None)


def _calls(a, uid):
    return {(c["module"], c["action"]) for c in a["workflows"][uid]["called_actions"]}


def test_workflow_to_action_edges_through_variables(estate):
    a = estate["assess"]
    assert _calls(a, REG) == {("com.example.ipam", "removeRecord"), ("com.example.ipam.v2", "addRecord"),
                              ("com.example.state", "saveHostState")}
    assert ("com.example.ipam", "notCalledInComment") not in _calls(a, REG)
    assert _calls(a, UNREG) == {("com.example.state", "readHostState"), ("com.example.ipam", "removeRecord"),
                                ("com.example.state", "dropHostState")}
    acts = {x["module"] + "/" + x["name"]: x for x in a["actions"]}
    assert REG in acts["com.example.ipam.v2/addRecord"]["called_by_workflows"]
    assert REG not in acts["com.example.ipam/addRecord"]["called_by_workflows"]
    # action to action through a variable, including an alias of an alias (v2/addRecord)
    assert acts["com.example.ipam/callApi"]["called_by_actions"]


def test_a_system_reached_only_through_an_action_shows_on_the_workflow(estate):
    a = estate["assess"]
    for u in (REG, UNREG):
        assert "infoblox" in a["workflows"][u]["targets"], a["workflows"][u]["targets"]
        ops = {o["op"] for o in a["workflows"][u]["ops"] if o["target"] == "infoblox"}
        assert all(o.get("via") for o in a["workflows"][u]["ops"] if o["target"] == "infoblox")
    assert "POST /wapi/v2.12/record:a" in {o["op"] for o in a["workflows"][REG]["ops"]}
    # the path built by a path-building action, with its literal argument substituted
    assert "DELETE /wapi/v2.12/record:a" in ops


def test_the_rest_helper_is_not_a_system_of_its_own(estate):
    a = estate["assess"]
    helper = next(x for x in a["actions"] if x["name"] == "callApi")
    assert helper["rest_helper"] and helper["targets"] == []
    assert "rest-host" not in a["targets"]
    t = estate["brief"]["action_triage"][helper["key"]]
    assert t["outcome"] == "Plugin-covered library" and "generic REST client" in t["reason"]


def test_state_in_a_configuration_element(estate):
    w = estate["assess"]["workflows"]
    assert w[REG]["state"]["writes"] == ["Example/HostState"]
    assert w[UNREG]["state"]["writes"] == ["Example/HostState"]
    assert w[ROUTER]["state"] == {"reads": ["Example/HostState"], "writes": []}


def test_domain_name_is_built_from_the_content(estate):
    d = estate["assess"]["domains"]["domains"]
    assert len(d) == 1
    assert d[0]["name_source"] == "code fallback"
    assert "fixture" not in d[0]["name"].lower()
    assert d[0]["name"] == "Host workflows (Infoblox)"


def test_dispatcher_on_event_type_is_rebuild(estate):
    t = estate["brief"]["triage"][ROUTER]
    assert estate["assess"]["workflows"][ROUTER]["event_branches"] == ["CREATE_DEPLOYMENT", "DESTROY_DEPLOYMENT",
                                                                       "UPDATE_DEPLOYMENT"]
    assert t["outcome"] == "Rebuild" and "event dispatcher" in t["reason"]


def test_started_by_a_subscription_is_not_rebuild(estate):
    """Its spec names vRA, which alone used to make it Rebuild; a subscription explains the mention."""
    t = estate["brief"]["triage"][REG]
    assert "vRA" in json.load(open(os.path.join(EVENTS, "specs", f"{REG}.json")))["purpose"]
    assert t["outcome"] == "Convert", t
    assert not any("Rebuild" in x for x in t["also"])


def test_trigger_notes(estate):
    tri = estate["brief"]["triage"]
    assert any(n.startswith("blocking subscription 'Net - register on provision'") and "timeout 5 min" in n
               for n in tri[REG]["trigger_notes"])
    assert any(n.startswith("run by 2 subscriptions") for n in tri[UNREG]["trigger_notes"])
    assert not any("blocking" in n for n in tri[ROUTER]["trigger_notes"])
    assert "lifecycle group L1" in tri[REG]["trigger_tags"]


def test_lifecycle_group(estate):
    g = estate["assess"]["lifecycle_groups"]
    assert len(g) == 1
    assert set(g[0]["members"]) == {REG, UNREG, ROUTER}
    assert g[0]["shared_state"] == ["Example/HostState"]
    assert g[0]["writers"] == sorted([REG, UNREG])
    assert g[0]["phases"] == ["request", "machine", "removal"]


def test_a_lifecycle_group_moves_together(estate):
    """The dispatcher is Rebuild, so the rest of its lifecycle group waits for it."""
    plan = estate["brief"]["plan"]
    blocked = {b["uid"]: b for b in plan["blocked"]}
    assert set(blocked) == {REG, UNREG}
    assert all("lifecycle group L1" in " ".join(b["blocked_on"]) for b in blocked.values())
    assert plan["waves"] == []


def test_waves_keep_a_lifecycle_group_in_one_wave_and_gate_blocking_subscriptions():
    from kestra_migration_assistant.vra.stages import waves as W
    wf = {}
    for u, c, subs in (("x-create", "C1", [("create", "deployment.request.pre", True, 15)]),
                       ("x-add", "C2", [("add", "compute.provision.post", True, 10)]),
                       ("x-other", "C3", [])):
        wf[u] = {"uid": u, "cluster": c, "nested": [], "ops": [], "script_lines": 5, "writes": 0,
                 "domain": "D1", "entry_points": [{"kind": "subscription", "name": n, "event_topic": t,
                                                   "blocking": b, "timeout_minutes": m} for n, t, b, m in subs]}
    assess = {"workflows": wf, "targets": {},
              "lifecycle_groups": [{"id": "L1", "members": ["x-create", "x-add"]}]}
    tri = {u: {"outcome": "Convert", "evidence": {}} for u in wf}
    ans = {"open": ["Q9"], "items": []}
    plan = W.plan(assess, tri, ans)
    where = {u: w["wave"] for w in plan["waves"] for u in w["workflows"]}
    assert where["x-create"] == where["x-add"]
    crit = [e["criterion"] for w in plan["waves"] for e in w["entry_criteria"]]
    assert any("timeout and failure policy" in c and "'create'" in c and "15 min" in c for c in crit)
    assert any("lifecycle group L1 converts whole" in c for c in crit)
    assert any("trigger plan" in c for c in crit)


def test_trigger_plan_rows_and_both_answers(estate):
    model = estate["assess"]["entry_points"]["model"]
    both = TG.plan(model, None)
    assert both["front_end"] == TG.BOTH
    kinds = [r["kind"] for r in both["rows"]]
    assert kinds.count("subscription") == 4 and "catalog item" in kinds and "cloud template" in kinds
    rows = {r["name"]: r for r in both["rows"]}
    reg = rows["Net - register on provision"]
    assert reg["cloud_template"] == "Web Host" and reg["blocking"] and reg["timeout_minutes"] == 5
    assert "wait: true" in reg["keep"] and "Webhook" in reg["keep"]
    assert "per-machine loop, right after the create-VM task" in reg["replace"]
    assert "`errors` branch" in rows["Net - clean up failed request"]["replace"]
    assert "Kestra App" in rows["Web Host"]["replace"] and "hostCount" in rows["Web Host"]["replace"] \
        if rows["Web Host"]["kind"] == "catalog item" else True
    cat = next(r for r in both["rows"] if r["kind"] == "catalog item")
    assert TG.APP in cat["replace"] and "project, hostCount, register" in cat["replace"]
    assert TG.front_end("We will replace vRA with Kestra") == TG.REPLACE
    assert TG.front_end("keep vRA as the portal") == TG.KEEP
    assert TG.front_end("") == TG.BOTH


def test_lifecycle_order():
    assert TG.position("deployment.request.pre", 'eventType == "CREATE_DEPLOYMENT"')[0] < \
        TG.position("compute.provision.post", "")[0] < \
        TG.position("deployment.request.post", 'eventType == "CREATE_DEPLOYMENT"')[0] < \
        TG.position("compute.removal.pre", "")[0] < \
        TG.position("deployment.request.post", 'eventType == "DESTROY_DEPLOYMENT"')[0]
    assert TG.position("something.else", "")[1] == "other"


def test_trigger_plan_on_the_vra_flow_page_and_wording(estate):
    from kestra_migration_assistant.vra.stages import visuals as V
    page = V.vra_view(estate["assess"], estate["brief"])
    assert "<h2>In Kestra</h2>" in page
    assert "If vRA stays" in page and "If vRA is replaced" in page          # Q9 open: both
    assert "Deployment lifecycle groups" in page
    md = estate["md"]
    assert "no trigger export" not in md and "Untestable here" not in md
    assert "four event subscriptions run" in md and "Service Broker catalog item" in md
    assert "OPTIONS.md" not in md and "internal/" not in md


def test_brief_shows_one_plan_when_q9_is_answered(estate):
    from kestra_migration_assistant.vra.stages import brief as B
    path = C.ANSWERS
    txt = open(path).read()
    try:
        from conftest import answer
        answer("Q9", "replace")
        B.run()
        from kestra_migration_assistant.vra.stages import assess as A
        from kestra_migration_assistant.vra.stages import visuals as V
        page = V.vra_view(A.load(), C.read_json(os.path.join(C.BRIEF, "brief.json")))
        assert "If vRA is replaced" in page and "If vRA stays" not in page
        assert "vRA is replaced (preflight Q9)" in page
    finally:
        open(path, "w").write(txt)
        B.run()


def test_q9_not_applicable_without_vra(tmp_path):
    from kestra_migration_assistant.vra.stages import preflight as P
    C.configure("conformance", source_dir=os.path.join(ROOT, "tests", "vra", "conformance"), stages_dir=str(tmp_path),
                inputs={"aria-xml": os.path.join(ROOT, "tests", "vra", "conformance", "aria-xml")}, vra_inputs={})
    q9 = next(q for q in P.load_answers()["items"] if q["id"] == "Q9")
    assert q9["status"] == "not applicable" and not q9["open"]


def test_stage_ignores_a_name_tag():
    from kestra_migration_assistant.vra.stages import assess as A
    assert A._stage_of("LB - Create Load Balancer") == "provisioning"
    assert A._stage_of("[NET] Add Route") == "configuration"
    assert A._stage_of("Create VM") == "provisioning"


# ------------------------------------------------------------------ plugin coverage

def _snapshot(folder, installed_version="1.0.0", catalog_version="1.1.0"):
    def plugin(v):
        return {"name": "plugin-ee-infoblox", "title": "Infoblox", "group": "io.kestra.plugin.ee.infoblox",
                "version": v, "license": "EE", "apps": [], "triggers": [],
                "tasks": [{"cls": "io.kestra.plugin.ee.infoblox.ipam.address.AllocateNextAvailable", "title": ""}]}
    core = {"name": "core", "title": "Core", "group": "io.kestra.plugin.core", "version": None, "license": None,
            "tasks": [{"cls": c, "title": ""} for c in ("io.kestra.plugin.core.http.Request",
                                                        "io.kestra.plugin.core.kv.Set", "io.kestra.plugin.core.kv.Get",
                                                        "io.kestra.plugin.core.kv.Delete")],
            "triggers": [{"cls": "io.kestra.plugin.core.trigger.Webhook", "title": ""}], "apps": []}
    snap = {"taken": "2026-01-02T03:04:05Z", "sources": {
        "installed": {"available": True, "url": "http://kestra.invalid/api/v1/plugins", "kestra_version": "9.9",
                      "edition": "EE", "plugins": [plugin(installed_version), core]},
        "catalog": {"available": True, "url": "https://catalog.invalid", "plugins": [plugin(catalog_version), core]}}}
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "kestra-plugins-2026-01-02.json"), "w") as f:
        json.dump(snap, f)


def test_plugin_coverage_from_a_snapshot(estate, tmp_path, monkeypatch):
    from kestra_migration_assistant.vra.stages import coverage as CV
    from kestra_migration_assistant.vra.stages import targets as T
    _snapshot(str(tmp_path))
    monkeypatch.setattr(CV, "SNAPSHOT_DIR", str(tmp_path))
    a = estate["assess"]
    cov = CV.coverage(a["targets"], a["entry_points"]["model"], T.load_plugin_map())
    rows = {r["key"]: r for r in cov["rows"]}
    assert cov["snapshot"]["taken"] == "2026-01-02T03:04:05Z"
    ib = rows["infoblox"]
    assert ib["installed"]["plugins"][0]["version"] == "1.0.0" and ib["catalog"]["plugins"][0]["version"] == "1.1.0"
    assert any("installed 1.0.0, catalogue 1.1.0" in d for d in ib["disagreements"])
    assert ib["status"] == "partial" and "POST /wapi/v2.12/record:a" in ib["unmatched_ops"]
    assert rows["vro-platform"]["status"] == "replace"
    assert rows["vra"]["status"] == "trigger"
    vc = rows["vcenter"]                                  # no VMware plugin in this snapshot: HTTP
    assert vc["via"].startswith("cloud template") and vc["status"] == "http" and vc["installed"]["plugins"] == []
    assert vc["unmatched_ops"] == [] and vc["created_by"] == ["Web Host/host"]
    md = "\n".join(CV.markdown(cov))
    assert "Snapshot taken 2026-01-02T03:04:05Z" in md and "Where the instance and the catalogue disagree" in md


def test_plugin_coverage_without_a_snapshot(estate, tmp_path, monkeypatch):
    from kestra_migration_assistant.vra.stages import coverage as CV
    from kestra_migration_assistant.vra.stages import targets as T
    monkeypatch.setattr(CV, "SNAPSHOT_DIR", str(tmp_path))
    a = estate["assess"]
    cov = CV.coverage(a["targets"], a["entry_points"]["model"], T.load_plugin_map())
    assert cov["snapshot"]["file"] is None
    assert "No plugin snapshot" in "\n".join(CV.markdown(cov))


def test_estate_report_has_coverage_and_the_visuals_write_both_views(estate):
    assert "## Plugin coverage" in estate["report"]
    from kestra_migration_assistant.vra.stages import visuals as V
    written = V.run()
    names = {os.path.basename(p) for p in written}
    assert {"vra.html", "plugins.html"} <= names
    vra = open(os.path.join(C.PAGES, "vra.html")).read()
    assert "Lifecycle events, in order" in vra and "Net - register on provision" in vra
    assert vra.index("Each machine, after create") < vra.index("Request finished") < vra.index("Each machine, before removal")
    assert "Plugin coverage" in open(os.path.join(C.PAGES, "plugins.html")).read()


def test_q9_a_negated_bridge_is_no_bridge():
    assert TG.front_end("replace: both vRA and vRO are replaced by Kestra, no bridge") == TG.REPLACE
    assert TG.front_end("replace it, without a bridge") == TG.REPLACE
    assert TG.front_end("replace vRA, with a bridge until then") == TG.BRIDGE
    assert TG.front_end("keep") == TG.KEEP
