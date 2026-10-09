"""
Actions judged by what they reach and what uses them, spec prompts that carry the whole call tree, spec
reviews on record, and the replacement of vRA planned as waves. Checked on synthetic content
(tests/conformance/helpers/, CONFORMANCE.md); every name in it is invented.

The estate: a REST helper (callApi) used for a system with a Kestra plugin (Infoblox, /wapi/v) and one
without (Nuage VSD, /nuage/api/); a path-free helper used only by the plugin-covered client (recordName);
a response check used by both clients (checkResponse); a three-deep chain of logic (levelOne > levelTwo >
levelThree); an action only a Manual workflow uses (onlyManual) and one nothing uses (orphan). vRA: one
cloud template creating a vSphere machine and a vSphere network, its catalog item, and one blocking
subscription that runs Web - Provision Host.
"""
from __future__ import annotations

import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vro2k_assess.stages import common as C  # noqa: E402

HELPERS = os.path.join(ROOT, "tests", "conformance", "helpers")
PROV, APPROVE = "helpers-Web---Provision-Host", "helpers-Web---Approve-Host"


def _configure(tmp, spec_dir=None):
    C.configure("helpers", source_dir=HELPERS, stages_dir=str(tmp), spec_dir=spec_dir or os.path.join(HELPERS, "specs"),
                inputs={"helpers": os.path.join(HELPERS, "vro")},
                vra_inputs={"helpers-vra": os.path.join(HELPERS, "vra")})


def _answer_q9(text):
    from conftest import answer
    answer("Q9", text)


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("helpers-stages")
    old = os.environ.get("VRO_NO_MODEL")
    os.environ["VRO_NO_MODEL"] = "1"
    try:
        _configure(tmp)
        from vro2k_assess.stages import assess as A, brief as B, preflight as P
        P.run()
        _answer_q9("replace: Kestra takes over the catalog")
        a = A.run()
        b = B.run()
        md = open(C.BRIEF_MD).read()
    finally:
        if old is None:
            os.environ.pop("VRO_NO_MODEL", None)
    return {"assess": a, "brief": b, "md": md, "tmp": str(tmp)}


@pytest.fixture
def estate(run):
    _configure(run["tmp"])
    old = os.environ.get("VRO_NO_MODEL")
    os.environ["VRO_NO_MODEL"] = "1"
    yield run
    if old is None:
        os.environ.pop("VRO_NO_MODEL", None)


def _act(estate, name):
    return next(t for t in estate["brief"]["action_triage"].values() if t["name"] == name)


# ------------------------------------------------------------------ actions

def test_a_client_for_a_plugin_system_is_plugin_covered(estate):
    t = _act(estate, "addDnsRecord")
    assert t["outcome"] == "Plugin-covered library" and "Infoblox" in t["reason"]


def test_a_client_for_a_system_with_no_plugin_is_rebuild(estate):
    t = _act(estate, "addSubnet")
    assert t["outcome"] == "Rebuild" and "Nuage" in t["reason"]


def test_a_helper_shared_by_a_covered_and_an_uncovered_system_stays_and_says_which_uses_go(estate):
    t = _act(estate, "callApi")
    assert t["outcome"] == "Rebuild", t
    uses = {u["system"]: u for u in t["uses"]}
    assert uses["infoblox"]["covered"] and not uses["nuage-vsd"]["covered"]
    assert uses["infoblox"]["callers"] == ["addDnsRecord"] and uses["nuage-vsd"]["callers"] == ["addSubnet"]
    assert "replaces its uses for Infoblox" in t["reason"] and "Nuage" in t["reason"]
    # BRIEF.md lists it under the row of the client it still needs code for; the translation page says why
    assert "callApi" in estate["md"]


def test_every_system_reached_counts_not_only_the_first():
    """An action whose own code calls a plugin system but which reaches a second one through another action."""
    from vro2k_assess.stages import triage as TR
    targets = {"ssh-host": {"reach": "plugin", "name": "SSH", "kestra_tasks": ["io.kestra.plugin.fs.ssh.Command"]},
               "avi-controller": {"reach": "http", "name": "Avi", "kestra_tasks": ["io.kestra.plugin.core.http.Request"]}}
    a = {"uid": "a", "key": "k", "name": "a", "wrapper": False, "library_for": ["ssh-host"], "ops": [{"op": "x"}],
         "calls_actions": [], "called": True,
         "all_ops": [{"op": "x", "target": "ssh-host"}, {"op": "GET /api/pool", "target": "avi-controller"}]}
    t = TR.triage_action(a, targets, None)
    assert t["outcome"] == "Rebuild" and "Avi" in t["reason"] and "SSH" in t["reason"]


def test_a_helper_used_only_by_a_plugin_covered_client_goes_with_it(estate):
    t = _act(estate, "recordName")
    assert t["outcome"] == "Plugin-covered library" and "addDnsRecord" in t["reason"]


def test_a_helper_used_by_a_rebuilt_and_a_replaced_client_is_rebuilt_and_says_so(estate):
    t = _act(estate, "checkResponse")
    assert t["outcome"] == "Rebuild" and "addSubnet" in t["reason"] and "addDnsRecord goes with the plugin" in t["reason"]


def test_an_action_with_no_blocker_of_its_own_converts_with_its_workflow(estate):
    for name in ("levelOne", "levelTwo", "levelThree"):
        t = _act(estate, name)
        assert t["outcome"] == "Convert", (name, t)
        assert "Web - Provision Host" in t["reason"]
    assert "through levelTwo" in _act(estate, "levelThree")["reason"]


def test_an_action_only_a_manual_workflow_uses_is_manual_and_names_the_blocker(estate):
    assert estate["brief"]["triage"][APPROVE]["outcome"] == "Manual"
    t = _act(estate, "onlyManual")
    assert t["outcome"] == "Manual" and "Web - Approve Host" in t["reason"] and "no spec" in t["reason"]


def test_an_action_nothing_calls_waits_for_an_owner_not_manual(estate):
    t = _act(estate, "orphan")
    assert t["outcome"] == "Needs owner input" and "nothing in the export calls it" in t["reason"]


def test_no_action_is_manual_without_a_named_blocker(estate):
    for t in estate["brief"]["action_triage"].values():
        if t["outcome"] == "Manual":
            assert t["reason"] and "no spec:" not in t["reason"]


def test_waves_carry_their_actions(estate):
    w1 = next(w for w in estate["brief"]["plan"]["waves"] if PROV in w["workflows"])
    assert "levelThree" in w1["actions"]["Convert"] and "addDnsRecord" in w1["actions"]["Plugin-covered library"]
    assert "callApi" in w1["actions"]["Rebuild"]


def test_no_internal_word_in_customer_facing_text(estate):
    csv = open(os.path.join(C.BRIEF, "triage.csv")).read()
    report = open(os.path.join(C.ASSESS, "estate-report.md")).read()
    for text in (estate["md"], csv, report):
        assert not re.search(r"\bprototype\b", text, re.I)


# ------------------------------------------------------------------ the spec prompt: the whole call tree

def _abm(estate):
    from vro2k_assess import understand as U
    idx = json.load(open(os.path.join(C.WORK, "index.json")))
    return U.actions_index(idx), json.load(open(os.path.join(C.WORK, "model", f"{PROV}.json")))


def test_the_spec_prompt_carries_actions_the_workflow_reaches_through_other_actions(estate):
    from vro2k_assess import understand as U
    abm, m = _abm(estate)
    inc, omitted, cut = U.action_tree(m, abm)
    got = {a["action"]: a for a in inc}
    assert {"levelOne", "levelTwo", "levelThree", "addDnsRecord", "addSubnet", "callApi", "recordName",
            "checkResponse"} <= set(got)
    assert got["levelOne"]["depth"] == 1 and got["levelThree"]["depth"] == 3
    assert got["levelThree"]["called_from"] == "com.example.util/levelTwo"
    assert "onlyManual" not in got and "orphan" not in got
    assert not omitted and not cut
    prompt = U.spec_prompt(m, [], inc, omitted, estate["assess"]["workflows"][PROV]["entry_points"])
    assert "REACHES, directly or through other actions" in prompt and "levelThree" in prompt
    assert "WHAT STARTS THIS WORKFLOW" in prompt and "compute.provision.post" in prompt


def test_the_budget_leaves_actions_out_and_says_which(estate):
    from vro2k_assess import understand as U
    abm, m = _abm(estate)
    inc, omitted, _ = U.action_tree(m, abm, budget=400)
    assert inc and omitted
    assert all("size budget" in o["why"] for o in omitted)
    prompt = U.spec_prompt(m, [], inc, omitted)
    assert "ACTIONS LEFT OUT" in prompt and omitted[0]["action"] in prompt


def test_the_spec_records_what_its_prompt_left_out(estate, tmp_path, monkeypatch):
    from vro2k_assess import model as M, understand as U
    monkeypatch.setattr(U, "ACTION_BUDGET", 400)
    seen = {}

    def fake_ask(purpose, uid, prompt, schema, **kw):
        seen[uid] = prompt
        return {"purpose": "x" * 40, "confidence": "medium", "open_questions": []}
    monkeypatch.setattr(M, "ask", fake_ask)
    monkeypatch.setattr(M, "run_parallel", lambda todo, work: ({u: work(u) for u in todo}, {}))
    out = tmp_path / "specs"
    said = []
    C.set_progress(said.append)
    try:
        U.run(C.WORK, [PROV], spec_dir=str(out), call_tree=True)
    finally:
        C.set_progress(None)
    # `vro.py run` shows each spec as it is written, with its time
    assert said[0] == "writing 1 spec, up to 1 at a time"
    assert re.fullmatch(r"wrote spec 1 of 1: .+ \(\d+\.\ds\)", said[1]), said
    spec = json.load(open(out / f"{PROV}.json"))
    assert spec["context"]["actions_omitted"] and spec["context"]["action_depth"] >= 1
    assert "ACTIONS LEFT OUT" in seen[PROV]
    # without the call tree, the prompt is the one the closed benchmark uses: direct calls only
    U.run(C.WORK, [PROV], spec_dir=str(tmp_path / "plain"), call_tree=False)
    assert "levelThree" not in seen[PROV] and "context" not in json.load(open(tmp_path / "plain" / f"{PROV}.json"))


# ------------------------------------------------------------------ spec reviews on record

def test_every_spec_is_a_review_item_and_an_answer_closes_its_gate(estate):
    import yaml
    from vro2k_assess.stages import brief as B, your_input as Y
    doc = yaml.safe_load(open(C.ANSWERS))
    short = estate["assess"]["workflows"][PROV]["short"]
    key = f"spec.{short}"
    assert Y.entry_of(doc, key) is not None
    assert Y.entry_of(doc, f"spec.{estate['assess']['workflows'][APPROVE]['short']}") is None      # Approve has no spec
    assert Y.entry_of(doc, key)["answer"] is None and "workflow page" in Y.entry_of(doc, key)["question"]
    crit = [e for w in estate["brief"]["plan"]["waves"] for e in w["entry_criteria"]
            if e["criterion"].startswith("Owner reviews the spec")]
    assert crit and all(e["status"] == "open" for e in crit)
    before = open(C.ANSWERS).read()
    try:
        Y.set_answer(key, "yes, the purpose is right")
        b = B.run()
        assert Y.entry_of(yaml.safe_load(open(C.ANSWERS)), key)["answer"] == "yes, the purpose is right"     # kept
        crit = [e for w in b["plan"]["waves"] for e in w["entry_criteria"]
                if e["criterion"].startswith("Owner reviews the spec")]
        assert crit[0]["status"] == "done" and "the purpose is right" in crit[0]["note"]
        md = open(C.BRIEF_MD).read()
        assert re.search(r"- met: Owner reviews the spec for " + re.escape(estate["assess"]["workflows"][PROV]["name"]), md)
        assert b["reviews"]["reviewed"] == [PROV]
    finally:
        open(C.ANSWERS, "w").write(before)
        B.run()


def test_a_review_goes_stale_when_the_spec_changes():
    from vro2k_assess.stages import reviews as RV, your_input as Y
    spec = {"purpose": "a" * 40, "confidence": "medium"}
    wf = {"u": {"name": "U", "short": "u"}}
    loaded = {"entries": {"spec.u": {"answer": "yes"}}, "path": "x", "exists": True, "warnings": [],
              "memo": {"reviewed": {"spec.u": {"spec": RV.fingerprint(spec), "answer": "yes"}}}}
    assert RV.load({"u": spec}, wf, loaded)["by_uid"]["u"]["state"] == "reviewed"
    changed = dict(spec, purpose="b" * 40)
    got = RV.load({"u": changed}, wf, loaded)["by_uid"]["u"]
    assert got["state"] == "stale" and "regenerated" in got["why"]
    # a changed answer is a new review, of the spec as it is now
    loaded["entries"]["spec.u"]["answer"] = "yes, read again"
    assert RV.load({"u": changed}, wf, loaded)["by_uid"]["u"]["state"] == "reviewed"
    loaded["entries"]["spec.u"]["answer"] = "yes"
    # the prompt record in a spec is not part of what was reviewed
    assert RV.load({"u": dict(spec, context={"actions_included": 3})}, wf, loaded)["by_uid"]["u"]["state"] == "reviewed"
    # an empty answer is no review
    loaded["entries"]["spec.u"]["answer"] = ""
    assert RV.load({"u": spec}, wf, loaded)["by_uid"]["u"]["state"] == "open"
    assert Y.is_open("don't know") and not Y.is_open("yes")


# ------------------------------------------------------------------ replacing vRA, planned as work

def _replacement(estate):
    return [w for w in estate["brief"]["plan"]["waves"] if w.get("kind") == "vra-replacement"]


def test_replacing_vra_is_a_wave_after_the_workflows_it_runs(estate):
    rw = _replacement(estate)
    assert len(rw) == 1
    w = rw[0]
    w1 = next(x for x in estate["brief"]["plan"]["waves"] if PROV in x["workflows"])
    assert w["depends_on"] == [w1["wave"]] and w["wave"] > w1["wave"]
    kinds = {(x["kind"], x["name"]) for x in w["scope"]}
    assert ("App", "App Host") in kinds and ("flow", "app-host") in kinds
    assert ("lifecycle step", "Web - provision host") in kinds
    app = next(x for x in w["scope"] if x["kind"] == "App")
    assert app["kestra"] == "io.kestra.plugin.ee.apps.Execution" and "project, hostCount" in app["detail"]


def test_the_template_resources_get_tasks_from_the_coverage_view_and_an_existing_network_is_no_gap(estate):
    w = _replacement(estate)[0]
    res = {x["name"]: x for x in w["scope"] if x["kind"] == "resource"}
    machine = res["host (Cloud.vSphere.Machine)"]
    assert "vcenter.CreateVm" in machine["kestra"] and not machine.get("gap")
    # `networkType: existing`: vRA picked the network, nothing creates it; which one it is is a mapping question
    net = res["host_net (Cloud.vSphere.Network)"]
    assert not net["gap"] and net["kestra"] == "an existing network, referenced by name"
    assert w["gaps"] == []
    crit = [e for e in w["entry_criteria"]]
    assert not any(e["criterion"].startswith("No Kestra task creates") for e in crit)
    m = next(e for e in crit if e["closes"].get("key", "").startswith("mappings."))
    assert "network" in m["closes"]["question"] and "`host_net`" in m["closes"]["question"]


def test_the_replacement_wave_has_entry_criteria_and_takes_the_blocking_policy(estate):
    plan = estate["brief"]["plan"]
    w = _replacement(estate)[0]
    crit = [e["criterion"] for e in w["entry_criteria"]]
    assert any("Wave 1 has met its exit criteria" in c for c in crit)
    assert any("non-production VMware vCenter" in c for c in crit)
    assert any("Enterprise Edition" in c and "apps.Execution" in c for c in crit)
    assert any("Step 'Web - provision host'" in c and "5 min" in c for c in crit)
    assert any("deployments vRA already made" in c for c in crit)
    assert any("image" in c and "flavor" in c for c in crit)
    w1 = next(x for x in plan["waves"] if PROV in x["workflows"])
    assert not any("timeout and failure policy is decided" in e["criterion"] for e in w1["entry_criteria"])
    md = estate["md"]
    assert "**Wave 2, replacing vRA.**" in md and "### Wave 2 (" in md


def test_a_signed_approval_closes_the_approval_gate_of_the_waves_it_covers(estate):
    from vro2k_assess.stages import brief as B, your_input as Y
    path = C.ANSWERS
    txt = open(path).read()
    try:
        Y.set_signature("wave-1")
        b = B.run()
        gate = {w["wave"]: next(e for e in w["entry_criteria"] if e["criterion"].startswith("The signature covers"))
                for w in b["plan"]["waves"]}
        assert gate[1]["status"] == "done" and gate[1]["note"] == "signed for Wave 1"
        assert all(g["status"] == "open" for n, g in gate.items() if n > 1)
    finally:
        open(path, "w").write(txt)
        B.run()


def test_no_replacement_wave_while_q9_is_open_or_says_keep(estate):
    from vro2k_assess.stages import brief as B
    path = C.ANSWERS
    txt = open(path).read()
    try:
        _answer_q9("keep vRA as the portal")
        b = B.run()
        assert not [w for w in b["plan"]["waves"] if w.get("kind") == "vra-replacement"]
        assert any("timeout and failure policy is decided" in e["criterion"]
                   for w in b["plan"]["waves"] for e in w["entry_criteria"])
    finally:
        open(path, "w").write(txt)
        B.run()


def test_replacement_waves_render_on_the_waves_page(estate):
    from vro2k_assess.stages import visuals as V
    html = V.waves(estate["assess"], estate["brief"])
    assert "Wave 2, replacing vRA" in html and "First of a kind: one deployment ordered from the App" in html
    assert "pilot" not in html.lower()


def test_a_spec_whose_input_changed_is_stale(tmp_path, monkeypatch):
    """A generated spec is regenerated when the prompt it would get today differs from the one it came from."""
    import json
    import shutil
    from vro2k_assess import understand as U
    monkeypatch.setenv("VRO_NO_MODEL", "1")
    spec_dir = tmp_path / "2-understand"
    shutil.copytree(os.path.join(HELPERS, "specs"), spec_dir)
    _configure(tmp_path / "stages", spec_dir=str(spec_dir))
    from vro2k_assess.stages import assess as A, preflight as P
    P.run()
    A.run()
    idx = json.load(open(os.path.join(C.WORK, "index.json")))
    uids = sorted(w["uid"] for w in idx["workflows"])
    have = [u for u in uids if (spec_dir / f"{u}.json").exists()]
    assert have
    probe = U.run(C.WORK, uids, spec_dir=str(spec_dir), call_tree=True, entry_points=U._entry_points_by_uid(), dry=True)
    u = have[0]
    spec = json.load(open(spec_dir / f"{u}.json"))
    # written from today's prompt: not stale
    spec["context"] = dict(probe["contexts"][u])
    json.dump(spec, open(spec_dir / f"{u}.json", "w"))
    assert U.input_changes(uids, {u}) == {}
    # written when the call tree listed an action it no longer does: stale, and says what changed
    spec["context"] = {k: v for k, v in probe["contexts"][u].items() if k != "prompt_sha"}
    spec["context"]["actions_omitted"] = ["m/getAttributeWithKey (not in the export)"]
    json.dump(spec, open(spec_dir / f"{u}.json", "w"))
    why = U.input_changes(uids, {u})[u]
    assert "actions_omitted" in why and "getAttributeWithKey" in why
    # with a fingerprint, a different prompt is stale
    spec["context"] = dict(probe["contexts"][u], prompt_sha="0" * 16)
    json.dump(spec, open(spec_dir / f"{u}.json", "w"))
    assert u in U.input_changes(uids, {u})
