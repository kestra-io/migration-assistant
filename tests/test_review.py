"""
The fixes from the review of 2026-10-01, each checked as a general
rule on synthetic content: tests/conformance/review/ (build.py writes it; every name in it is invented).

The estate: three workflows on one deployment's life (create a DNS zone, register each host, delete the zone,
the delete also run as the failed-create cleanup), sharing a zone registry kept in a configuration element;
Infoblox (a Kestra plugin) and Nuage VSD (none) behind one generic REST helper with basic auth and certificate
checks off; settings and endpoints in configuration elements, a request body in a resource element; payload
parsers and an event-type decision; a cloud template with machines counted from an input on an existing network
with static IPs.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from kestra_migration_assistant.vra.stages import common as C

REVIEW = os.path.join(ROOT, "tests", "conformance", "review")
CREATE, REGISTER, DELETE = "review-Dns---Create-Zone", "review-Dns---Register-Host", "review-Dns---Delete-Zone"


def _configure(tmp, spec_dir=None):
    C.configure("review", source_dir=REVIEW, stages_dir=str(tmp), spec_dir=spec_dir or os.path.join(REVIEW, "specs"),
                inputs={"review": os.path.join(REVIEW, "vro")}, vra_inputs={"review-vra": os.path.join(REVIEW, "vra")})


def _answer(qid, text):
    from conftest import answer
    answer(qid, text)


def _run(tmp, q9):
    from kestra_migration_assistant.vra.stages import assess as A
    from kestra_migration_assistant.vra.stages import brief as B
    from kestra_migration_assistant.vra.stages import preflight as P
    _configure(tmp)
    P.run()
    if q9:
        _answer("Q9", q9)
    a = A.run()
    b = B.run()
    return {"assess": a, "brief": b, "md": open(C.BRIEF_MD).read(),
            "oq": open(os.path.join(C.BRIEF, "open-questions.md")).read(),
            "inv": open(os.path.join(C.ASSESS, "target-inventory.md")).read(), "tmp": str(tmp)}


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    old = os.environ.get("VRO_NO_MODEL")
    os.environ["VRO_NO_MODEL"] = "1"
    try:
        out = {"replace": _run(tmp_path_factory.mktemp("review-replace"), "replace"),
               "bridge": _run(tmp_path_factory.mktemp("review-bridge"), "replace vRA, with a bridge until then"),
               "keep": _run(tmp_path_factory.mktemp("review-keep"), "keep")}
    finally:
        if old is None:
            os.environ.pop("VRO_NO_MODEL", None)
    return out


@pytest.fixture
def estate(runs):
    _configure(runs["replace"]["tmp"])
    old = os.environ.get("VRO_NO_MODEL")
    os.environ["VRO_NO_MODEL"] = "1"
    yield runs["replace"]
    if old is None:
        os.environ.pop("VRO_NO_MODEL", None)


def _act(r, name):
    return next(t for t in r["brief"]["action_triage"].values() if t["name"] == name)


def _criteria(r, wave_kind=None):
    for w in r["brief"]["plan"]["waves"]:
        if wave_kind is None or (w.get("kind") or "workflows") == wave_kind:
            yield from ((w, e) for e in w["entry_criteria"])


def _decisions(r):
    return {e["closes"]["key"]: e for _, e in _criteria(r) if e["closes"]["kind"] == "decision"}


def _prompts(r, spec_dir=None):
    """Every spec prompt as understand would send it (no model call), and what each carries."""
    from kestra_migration_assistant.vra import understand as U
    _configure(r["tmp"], spec_dir)
    uids = sorted(r["assess"]["workflows"])
    texts = {}
    orig = U.spec_prompt

    def capture(m, *a, **k):
        t = orig(m, *a, **k)
        texts[m["name"]] = t
        return t
    U.spec_prompt = capture
    try:
        res = U.run(C.WORK, uids, spec_dir=spec_dir or os.path.join(REVIEW, "specs"), call_tree=True,
                    entry_points=U._entry_points_by_uid(), dry=True, **U._shared_state())
    finally:
        U.spec_prompt = orig
    return texts, res


# ------------------------------------------------------------ 1. elements in the spec prompt

def test_1_a_spec_prompt_shows_the_elements_its_code_opens(estate):
    texts, res = _prompts(estate)
    p = texts["Dns - Create Zone"]
    assert "ELEMENTS THIS WORKFLOW READS" in p
    ctx = res["contexts"][CREATE]
    assert "Example/Dns/Settings/Defaults" in ctx["elements"]
    assert "Example/Dns/Templates/zone-body.json" in ctx["elements"]
    assert "Example/Dns/State/Zone Registry" in ctx["elements"]
    # names and types always, values where the export has them, never a secret's
    assert '"name": "suffix"' in p and '"value": "zones.example.test"' in p
    assert '"name": "password"' in p and '"secret": true' in p
    # a text resource's content is shown
    assert '{{zone}}' in p and '"mime": "application/json"' in p


def test_1_values_only_when_present_and_text_capped():
    from kestra_migration_assistant.vra import understand as U
    els = [{"kind": "ConfigurationElement", "category_path": "X.Y", "name": "E", "attributes": [
        {"name": "a", "type": "string", "value": None, "is_null_sentinel": True, "is_secure": False},
        {"name": "b", "type": "string", "value": "v", "is_null_sentinel": False, "is_secure": False},
        {"name": "c", "type": "SecureString", "value": "s3cret", "is_null_sentinel": False, "is_secure": True}]},
        {"kind": "ResourceElement", "category_path": "X.Z", "name": "big.txt", "mime": "text/plain",
         "binary": False, "text": "x" * 100, "text_cut": True},
        {"kind": "ResourceElement", "category_path": "X.Z", "name": "pic.png", "mime": "image/png", "binary": True}]
    got = U.elements_for(['Server.getConfigurationElementCategoryWithPath("X/Y");'
                          'var p = "X/Z"; Server.getResourceElementCategoryWithPath(p);'], els)
    attrs = {a["name"]: a for a in got[0]["attributes"]}
    assert "value" not in attrs["a"]                      # vRO's null, or a sanitized copy: no value shown
    assert attrs["b"]["value"] == "v"
    assert attrs["c"] == {"name": "c", "type": "SecureString", "secret": True}
    big, pic = got[1], got[2]
    assert big["text_cut"] and len(big["text"]) == 100 and pic["binary"] and "text" not in pic
    # the budget for all text resources
    over = U.elements_for(['Server.getResourceElementCategoryWithPath("X/Z")'], els, budget=10)
    assert next(e for e in over if e["name"] == "big.txt")["text_left_out"]


def test_1_a_raw_tree_resource_element_keeps_its_text_and_mime(estate):
    idx = json.load(open(os.path.join(C.WORK, "index.json")))
    r = next(e for e in idx["other_elements"] if e["kind"] == "ResourceElement")
    assert r["name"] == "zone-body.json" and r["mime"] == "application/json" and "{{zone}}" in r["text"]


def test_1_the_change_makes_specs_generated_before_it_stale(estate, tmp_path):
    from kestra_migration_assistant.vra import understand as U
    spec_dir = tmp_path / "specs"
    spec_dir.mkdir()
    for u in (CREATE, REGISTER, DELETE):
        (spec_dir / f"{u}.json").write_text(open(os.path.join(REVIEW, "specs", f"{u}.json")).read())
    old_context = "call tree (budget 60000, per action 6000) + entry points"
    (spec_dir / "PROVENANCE.json").write_text(json.dumps({"specs": {
        u: {"source": "generated by vro.py understand", "context": old_context} for u in (CREATE, REGISTER, DELETE)},
        "cluster_summaries": {}}))
    _configure(estate["tmp"], str(spec_dir))
    stale = U.stale_specs()
    assert set(stale) == {CREATE, REGISTER, DELETE} and all("shape" in w for w in stale.values())
    assert "elements" in U.CONTEXT_KEYS and "shares_state_with" in U.CONTEXT_KEYS


# ------------------------------------------------------------ 2. the shared state's writer first

def test_2_writers_are_specified_before_the_readers_and_shown_to_them(estate):
    texts, res = _prompts(estate)
    order = res["order"]
    assert order.index(REGISTER) > order.index(CREATE) and order.index(REGISTER) > order.index(DELETE)
    reader = texts["Dns - Register Host"]
    assert "WORKFLOWS SHARING STATE WITH THIS ONE" in reader
    assert "Creates a DNS zone in Infoblox" in reader and "Example/Dns/State, Zone Registry" in reader
    # two writers of the same state: a circle, each named to the other without a spec
    assert res["contexts"][CREATE]["shares_state_with"] == ["Dns - Register Host (name only)",
                                                           "Dns - Delete Zone (name only)"]
    assert res["contexts"][DELETE]["shares_state_with"][0] == "Dns - Create Zone (name only)"


def test_2_the_estate_schema_asks_for_defects_and_state_records():
    from kestra_migration_assistant.vra import understand as U
    req = U.ESTATE_SPEC_SCHEMA["required"]
    assert "suspected_defects" in req and "state_records" in req
    assert "suspected_defects" not in U.SPEC_SCHEMA["properties"]   # the closed benchmark's schema is unchanged


# ------------------------------------------------------------ 3. grouped questions, suspected defects

def test_3_a_question_asked_for_several_workflows_of_one_group_is_listed_once(estate):
    oq = estate["brief"]["open_questions"]
    assert oq["asked"] == 4 and oq["grouped"] == 2
    retry = next(g for g in oq["groups"] if g["theme"] == "error semantics")
    assert sorted(retry["workflows"]) == sorted([CREATE, REGISTER, DELETE])
    assert "Applies to: Dns - Create Zone" in estate["oq"] and "also asked for Dns - Register Host" in estate["oq"]


def test_3_suspected_defects_are_their_own_list_and_a_decision_each(estate):
    d = estate["brief"]["suspected_defects"]
    assert [(x["workflow"], x["n"]) for x in d] == [(CREATE, 1), (DELETE, 1), (DELETE, 2)]
    assert "possible defects** to confirm or fix" in estate["md"] and "## Suspected defects" in estate["oq"]
    dec = _decisions(estate)
    k = d[1]["decision"]
    assert k in dec and dec[k]["closes"]["options"] == ["keep", "fix"]
    # in answers.yaml, one plain question with the choice in it
    q = dec[k]["closes"]["plain"]
    assert q.startswith("Possible defect in ") and q.endswith(". Keep the current behavior, or fix it?")
    assert "(" not in q


# ------------------------------------------------------------ 4. stale specs reported by status and brief

def test_4_status_and_brief_report_a_stale_spec(estate, tmp_path):
    from kestra_migration_assistant.vra.stages import brief as B
    from kestra_migration_assistant.vra.stages import status as S
    spec_dir = tmp_path / "specs"
    spec_dir.mkdir()
    for u in (CREATE, REGISTER, DELETE):
        (spec_dir / f"{u}.json").write_text(open(os.path.join(REVIEW, "specs", f"{u}.json")).read())
    (spec_dir / "PROVENANCE.json").write_text(json.dumps({"specs": {
        CREATE: {"source": "generated by vro.py understand", "context": "an older prompt"}}, "cluster_summaries": {}}))
    _configure(estate["tmp"], str(spec_dir))
    st = S.collect()
    assert any(f"{CREATE}.json is stale" in x for x in st["stale"])
    assert next(s for s in st["stages"] if s["key"] == "understand")["state"] == "stale"
    b = B.run()
    assert set(b["stale_specs"]) == {CREATE}
    assert any("written from different code" in n for n in b["triage"][CREATE]["notes"])
    assert "other code or another prompt than today's" in open(C.BRIEF_MD).read()


# ------------------------------------------------------------ 5, 6, 7. action triage

def test_5_vro_state_actions_are_rebuild(estate):
    for name in ("getSettings", "getEndpoint", "zoneRegistry", "readZoneState", "saveZoneState", "dropZoneState",
                 "renderBody"):
        t = _act(estate, name)
        assert t["outcome"] == "Rebuild" and "vRO's own state" in t["reason"], (name, t)
    assert "KV store" in _act(estate, "saveZoneState")["reason"]
    assert "namespace file" in _act(estate, "renderBody")["reason"]


def test_5_event_payload_actions_are_rebuild_when_vra_goes_and_convert_when_it_stays(runs):
    for mode in ("replace", "bridge"):
        for name in ("readRequestEvent", "readHostEvent", "shouldDropZone"):
            t = _act(runs[mode], name)
            assert t["outcome"] == "Rebuild" and "typed inputs" in t["reason"], (mode, name, t)
    assert "eventType" in _act(runs["replace"], "readRequestEvent")["reason"]
    assert "DESTROY_DEPLOYMENT" in _act(runs["replace"], "shouldDropZone")["reason"]
    for name in ("readRequestEvent", "readHostEvent", "shouldDropZone"):
        assert _act(runs["keep"], name)["outcome"] == "Convert"
    # plain logic with no system still follows its callers
    assert _act(runs["replace"], "checkZoneName")["outcome"] == "Convert"


def test_6_builders_on_a_client_are_part_of_that_client(estate):
    for name in ("buildNuageDomain", "buildNuageZone"):
        t = _act(estate, name)
        assert t["outcome"] == "Rebuild" and "Nuage" in t["reason"], (name, t)
    a = next(x for x in estate["assess"]["actions"] if x["name"] == "buildNuageDomain")
    assert a["library_for"] == ["nuage-vsd"] and a["client_through"] == ["createNuageObject"]


def test_7_a_client_keeps_its_plugin_outcome_over_a_near_duplicate(estate):
    t = _act(estate, "removeHostRecord")
    assert t["outcome"] == "Plugin-covered library", t
    assert any(x.startswith("Near-duplicate: same normalised code as deleteZone") for x in t["also"])


# ------------------------------------------------------------ 8. target inventory

def test_8_a_system_found_by_rest_signature_has_no_vro_plugin_caveats(estate):
    nu = estate["inv"].split("(`nuage-vsd`)")[1].split("\n## ")[0]
    assert "Found by: REST signature" in nu and "vRO plugin: none used here" in nu
    t = estate["assess"]["targets"]["nuage-vsd"]
    assert all(d.startswith("REST signature") for d in t["detected_by"])


def test_8_operations_count_workflows_not_call_sites(estate):
    ib = estate["assess"]["targets"]["infoblox"]
    ops = {o["op"]: o["workflows"] for o in ib["operations"]}
    # Delete Zone calls DELETE on the registry ref twice (two clients, one path shape) but is one workflow
    assert max(ops.values()) <= len(ib["workflows"])


def test_8_vro_platform_shows_the_reach_its_operations_have(estate):
    from kestra_migration_assistant.vra.stages.assess_report import shown_reach
    t = estate["assess"]["targets"]["vro-platform"]
    assert shown_reach(t) == "replace" and "| vRO platform services | replace |" in estate["inv"]


# ------------------------------------------------------------ 9. the vRA event interface

def test_9_with_vra_replaced_flows_take_typed_inputs_and_run_in_non_production_until_then(estate):
    w1 = estate["brief"]["plan"]["waves"][0]
    dec = _decisions(estate)
    typed = dec["typed-inputs.wave-1"]
    assert "Dns - Create Zone: deploymentId, eventType, status" in typed["criterion"]
    assert "Dns - Register Host: addresses, deploymentId, resourceNames" in typed["criterion"]
    assert any("non-production only" in e for e in w1["exit_criteria"])
    assert not any("webhook while both run" in e for e in w1["exit_criteria"])


def test_9_with_a_bridge_the_flows_run_behind_vra_until_the_replacement(runs):
    w1 = runs["bridge"]["brief"]["plan"]["waves"][0]
    assert any("through a bridge" in e for e in w1["exit_criteria"])
    assert any(k.startswith("timeout.") and "webhook" not in k for k in _decisions(runs["bridge"]))
    assert runs["bridge"]["brief"]["trigger_plan"]["front_end"] == "bridge"
    assert any(w.get("kind") == "vra-replacement" for w in runs["bridge"]["brief"]["plan"]["waves"])


def test_9_with_vra_kept_the_exit_criterion_is_the_webhook(runs):
    w1 = runs["keep"]["brief"]["plan"]["waves"][0]
    assert any("webhook while both run" in e for e in w1["exit_criteria"])
    assert "typed-inputs.wave-1" not in _decisions(runs["keep"])


# ------------------------------------------------------------ 10. pilot

def test_10_the_pilot_of_a_lifecycle_group_is_its_state_writer(estate):
    w1 = estate["brief"]["plan"]["waves"][0]
    assert w1["pilot"] == CREATE and w1["pilot_with"] == [DELETE]


# ------------------------------------------------------------ 11. what vRA did implicitly

def test_11_an_existing_network_is_a_mapping_question_and_static_ips_day2_and_the_record_are_asked(estate):
    dec = _decisions(estate)
    assert not any(k.startswith("gap.") for k in dec)
    m = next(v for k, v in dec.items() if k.startswith("mappings."))
    assert "`host_net`" in m["closes"]["question"] and "net:example" in m["closes"]["question"]
    assert any(k.startswith("ip-allocation.") for k in dec)
    assert dec[next(k for k in dec if k.startswith("deployment-record."))]["closes"]["recommended"].startswith("the KV")
    day2 = dec[next(k for k in dec if k.startswith("day2-scale."))]
    assert "`hostCount`" in day2["criterion"] and "Dns - register host" in day2["criterion"]


def test_11_the_reader_keeps_the_property_values_the_plan_needs(estate):
    tpl = estate["assess"]["entry_points"]["model"]["cloud_templates"][0]
    by = {r["name"]: r for r in tpl["creates"]}
    assert by["host_net"]["network_type"] == "existing" and by["host_net"]["constraint_tags"] == ["net:example"]
    assert by["host"]["ip_assignment"] == ["static"]


# ------------------------------------------------------------ 12. decisions

def test_12_non_blocking_steps_get_a_failure_policy_and_the_delete_path_its_own_wording(estate):
    dec = _decisions(estate)
    nb = [v for k, v in dec.items() if k.startswith("failure.")]
    assert nb and all(v["closes"]["recommended"] == "log and continue" for v in nb)
    assert any("deletion" in v["criterion"] for v in nb)
    blocking = [v for k, v in dec.items() if k.startswith("timeout.")]
    assert blocking and not any("stopped the deletion" in v["criterion"] and "undoes the steps" in v["criterion"]
                                for v in blocking)


def test_12_every_target_asks_its_endpoint_and_the_state_auth_and_tls_are_decisions(estate):
    dec = _decisions(estate)
    assert {"endpoint.infoblox", "endpoint.nuage-vsd"} <= set(dec)
    assert "host, username and password" in dec["endpoint.infoblox"]["closes"]["why"]
    st = dec["state-store.example-dns-state"]
    assert st["closes"]["recommended"] == "the KV store" and "Dns - Create Zone" in st["criterion"]
    assert "basic authentication" in dec["auth.nuage-vsd"]["criterion"]
    assert "hostVerification = false" in dec["tls.nuage-vsd"]["criterion"]


def test_12_the_failed_create_cleanup_asks_how_a_canceled_create_runs_it(estate):
    dec = _decisions(estate)
    c = next(v for k, v in dec.items() if k.startswith("cancel."))
    assert "afterExecution" in c["criterion"] and "Dns - clean up failed create" in c["criterion"]


# ------------------------------------------------------------ 14. personas

def test_14_a_persona_with_no_steps_is_not_written_up(estate):
    personas = [p["persona"] for p in estate["brief"]["walkthroughs"]["paths"]]
    assert "the operator" not in personas
    assert all(p["nodes"] for p in estate["brief"]["walkthroughs"]["paths"])


# ------------------------------------------------------------ also noticed: model IDs

def test_every_model_id_in_models_yaml_is_a_known_model():
    import yaml

    from kestra_migration_assistant.vra import model as M
    cfg = yaml.safe_load(open(os.path.join(ROOT, "kestra_migration_assistant", "vra", "data", "models.yaml")))
    for tier in ("reasoning", "drafting"):
        if cfg[tier]["provider"] == "anthropic":
            assert cfg[tier]["model"] in M.PRICES, (tier, cfg[tier]["model"])


# ------------------------------------------------------------ 13. preflight asks nothing the exports answered

def test_13_preflight_after_assess_asks_nothing_the_exports_answered(estate):
    from kestra_migration_assistant.vra.stages import preflight as P
    q = P.refresh_questions()
    assert q["contradictions"] == []
    items = {i["id"]: i for i in P.load_answers()["items"]}
    assert ".vra-ng" not in items["Q2"]["question"]
    assert all(s in items["Q3"]["question"] for s in ("Infoblox", "Nuage", "vCenter"))
    assert "HTTP-REST" in items["Q8"]["question"] and items["Q8"]["open"]
    assert "bridge" in items["Q9"]["question"]


def test_13_rewriting_the_questions_keeps_the_answers_file_time(estate):
    from kestra_migration_assistant.vra.stages import preflight as P
    path = C.ANSWERS
    txt = open(path).read()
    assert "    question: For each system" in txt
    open(path, "w").write(txt.replace("    question: For each system", "    question: (old wording) For each system", 1))
    os.utime(path, (1_000_000_000, 1_000_000_000))
    P.refresh_questions()
    assert "(old wording)" not in open(path).read() and os.path.getmtime(path) == 1_000_000_000


def test_a_defect_question_keeps_names_and_lowers_the_rest():
    from kestra_migration_assistant.vra.stages.waves import defect_question as D
    end = ". Keep the current behavior, or fix it?"
    assert D("Create", "The registry is read before the teardown check (item1: x).") == \
        "Possible defect in Create: the registry is read before the teardown check" + end
    assert D("W", "A REST failure aborts the loop") == "Possible defect in W: a REST failure aborts the loop" + end
    for kept in ("Avi servers are sent as V4, so on Avi it fails", "HTTPS on Avi has no SSL", "F5 names split badly",
                 "IPv6 members break the split"):
        assert D("W", kept) == f"Possible defect in W: {kept}" + end
    # a system's name stays a name even when the sentence uses it only once
    assert D("W", "Avi servers are always sent as V4", {"Avi", "F5"}) == "Possible defect in W: Avi servers are always sent as V4" + end
    assert D("W", "Avi servers are always sent as V4").startswith("Possible defect in W: avi")
