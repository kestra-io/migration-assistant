"""
The estate patterns (1.2.0): each one, in a small tree of its own (tests/vra/conformance/patterns/, built by its
build.py), gets the right outcome with no model. Missing code is listed with how to add it and never asked about,
estate answers settle questions estate-wide, and vRO mechanics are assumed instead of asked.
"""
from __future__ import annotations

import json
import os
import shutil

import pytest

CONFORMANCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conformance", "patterns")


@pytest.fixture(scope="module")
def run_pattern(tmp_path_factory):
    built: dict[str, dict] = {}

    def get(label: str) -> dict:
        if label in built:
            return built[label]
        from kestra_migration_assistant.vra.stages import assess, brief, interview, preflight
        from kestra_migration_assistant.vra.stages import common as C
        here = os.path.join(CONFORMANCE, label)
        tmp = tmp_path_factory.mktemp(label)
        src, stages = tmp / "sources" / label, tmp / "stages" / label
        shutil.copytree(os.path.join(here, "vro"), src / label)
        understand = stages / C.LAYOUT["understand"]
        understand.mkdir(parents=True)
        for f in sorted(os.listdir(os.path.join(here, "specs"))):
            shutil.copy(os.path.join(here, "specs", f), understand / f)
        old = os.environ.get("VRO_NO_MODEL")
        os.environ["VRO_NO_MODEL"] = "1"
        try:
            C.configure(label, source_dir=str(src), stages_dir=str(stages))
            preflight.run()
            assess.run()
            interview.register()
            brief.run()
        finally:
            if old is None:
                os.environ.pop("VRO_NO_MODEL", None)
        built[label] = {"stages": str(stages), "label": label, "src": str(src)}
        return built[label]
    return get


def _use(r: dict):
    """Point the stages at one fixture's folders (each test may run after another fixture was configured)."""
    from kestra_migration_assistant.vra.stages import common as C
    C.configure(r["label"], source_dir=r["src"], stages_dir=r["stages"])


def _read(r: dict, *p: str) -> dict:
    return json.load(open(os.path.join(r["stages"], "data", *p)))


def _answer_and_rebrief(r: dict, key: str, answer: str) -> dict:
    from kestra_migration_assistant.vra.stages import brief
    from kestra_migration_assistant.vra.stages import your_input as Y
    _use(r)
    Y.set_answer(key, answer)
    os.environ["VRO_NO_MODEL"] = "1"
    try:
        return brief.run()
    finally:
        os.environ.pop("VRO_NO_MODEL", None)


def _file(r: dict) -> dict:
    from kestra_migration_assistant.vra.stages import your_input as Y
    return Y._yaml(open(os.path.join(r["stages"], "your-input", "answers.yaml"), encoding="utf-8").read())


def test_a_runtime_configuration_module_is_one_estate_question(run_pattern):
    r = run_pattern("runtimeconfig")
    pat = _read(r, "1-assess", "patterns.json")
    rc = pat["runtime_config"]
    assert [x["name"] for x in rc] == ["getRuntimeConfig"] and len(rc[0]["workflows"]) == 6
    assert rc[0]["by"] == "a path built at run time" and rc[0]["categories"] == ["Settings/"]
    est = {e["id"]: e for e in _file(r)["estate"]}
    q = est["estate.config"]
    assert "6 workflows call a module named getRuntimeConfig" in q["question"]
    assert q["options"][0] == q["suggested"]
    # the six "which vCenter" questions (grouped by their words into fewer items) wait on it, not asked one by one
    from kestra_migration_assistant.vra.stages import your_input as Y
    _use(r)
    asked = [it["key"] for it in Y.items() if it.get("family") == "question"]
    assert asked and sorted(q["settles"]) == sorted(asked) and all(k.endswith("endpoint-identity") for k in asked)
    b = _read(r, "3-brief", "brief.json")
    open_ids = {x["id"] for x in b["input"]["shown"]}
    assert not any(k in open_ids for k in q["settles"])
    # the category it opens is not in the package: listed with how to add it, never asked
    adds = [a for a in b["package_additions"] if a["kind"] == "configuration elements"]
    assert adds and "Settings/" in adds[0]["what"] and "Add the folder" in adds[0]["how"]
    assert not [k for k in open_ids if k.startswith("missing.")]


def test_answering_the_estate_question_settles_them_everywhere_and_a_changed_answer_reopens_them(run_pattern):
    r = run_pattern("runtimeconfig")
    b = _answer_and_rebrief(r, "estate.config", "1")
    est = {e["id"]: e for e in _file(r)["estate"]}
    closed = est["estate.config"]["closes"]
    assert closed and not est["estate.config"].get("settles")
    assert b["questions"]["closed"] >= len(closed)
    assert not set(closed) & {m for x in b["input"]["shown"] for m in x["members"]}
    from kestra_migration_assistant.vra.stages import your_input as Y
    st = Y.state(closed[0])
    assert st["state"] == "answered" and "namespace variables" in st["answer"]
    # an own answer the planner cannot match to an option still settles them (the rule reads any answer) ...
    _answer_and_rebrief(r, "estate.config", "")
    est = {e["id"]: e for e in _file(r)["estate"]}
    assert not est["estate.config"].get("closes") and est["estate.config"]["settles"] == closed
    assert Y.state(closed[0])["state"] == "open"


def test_vault_lookups_through_a_module_are_one_question_about_secrets(run_pattern):
    r = run_pattern("vault")
    pat = _read(r, "1-assess", "patterns.json")
    assert [v["name"] for v in pat["vault"]] == ["readSecret"] and len(pat["vault"][0]["workflows"]) == 5
    est = {e["id"]: e for e in _file(r)["estate"]}
    q = est["estate.secrets"]
    assert "5 workflows read their secrets from HashiCorp Vault today, through readSecret" in q["question"]
    assert "Vault" in q["suggested"] and q["settles"] and all(k.endswith(".credentials") for k in q["settles"])
    _answer_and_rebrief(r, "estate.secrets", "yes")
    est = {e["id"]: e for e in _file(r)["estate"]}
    assert est["estate.secrets"]["closes"] == q["settles"]


def test_rest_in_code_and_its_mechanics_are_assumed_not_asked(run_pattern):
    r = run_pattern("restcode")
    pat = _read(r, "1-assess", "patterns.json")
    assert len(pat["rest_in_code"]["workflows"]) == 3
    assert len(pat["mechanics"]["host_verification"]) == 3 and len(pat["mechanics"]["retries"]) == 3
    est = {e["id"]: e for e in _file(r)["estate"]}
    assert {"assume.rest-hosts", "assume.retries", "assume.trust-store"} <= set(est)
    # the retry question is settled by the assumption, listed under it with the answer it takes
    rt = [m["id"] for m in est["assume.retries"].get("each") or []]
    assert all(m["assumed"].startswith("the task's retry setting") for m in est["assume.retries"]["each"])
    _use(r)
    from kestra_migration_assistant.vra.stages import your_input as Y
    assert rt and sorted(rt) == sorted(it["key"] for it in Y.items() if it.get("family") == "question")
    # certificate and host checks turned off stay off, as today: assumed, never asked, and a security note in the brief
    assert "estate.host-verification" not in est
    hc = est["assume.host-checks"]
    ms = [m["id"] for m in hc.get("each") or []]
    assert len(ms) == 3 and all(".tls" in k for k in ms)
    assert all(m["assumed"].startswith("keep: the checks stay off") for m in hc["each"])
    b = _read(r, "3-brief", "brief.json")
    from kestra_migration_assistant.vra.stages import front as F
    notes = F.note_lines(b)
    assert notes and notes[0].startswith("**Security: certificate or host checks turned off** in three workflows")
    assert not set(ms) & {m for x in b["input"]["shown"] for m in x["members"]}
    # answered to check them, the conversion fixes each one instead
    b = _answer_and_rebrief(r, "assume.host-checks", "check them")
    assert all(Y.state(k)["answer"].startswith("fix: the flow verifies the certificate") for k in ms)
    assert not set(ms) & {m for x in b["input"]["shown"] for m in x["members"]}
    assert not any("host checks turned off" in n for n in F.note_lines(b))


def test_a_call_to_another_cluster_lists_what_to_add_and_asks_how_to_reach_it(run_pattern):
    r = run_pattern("multinode")
    pat = _read(r, "1-assess", "patterns.json")
    assert [x["name"] for x in pat["remote"]] == ["Remote - Rebuild Cache On Cluster B"]
    b = _read(r, "3-brief", "brief.json")
    adds = [a for a in b["package_additions"] if a["kind"] == "remote workflow"]
    assert len(adds) == 1 and adds[0]["called_by"] == ["Remote - Rebuild Cache On Cluster B"]
    assert "other vRO cluster" in adds[0]["what"] and "make a package" in adds[0]["how"]
    est = {e["id"]: e for e in _file(r)["estate"]}
    assert "estate.remote" in est
    assert not [x for x in b["input"]["shown"] if x["id"].startswith("missing.")]


def test_a_module_missing_three_calls_deep_is_listed_with_how_to_add_it(run_pattern):
    r = run_pattern("nested")
    pat = _read(r, "1-assess", "patterns.json")
    assert pat["nesting"]["deepest"] >= 3
    assert pat["nesting"]["missing"] == ["com.example.legacy/deepHelper"]
    b = _read(r, "3-brief", "brief.json")
    adds = {a["what"]: a for a in b["package_additions"]}
    a = adds["action com.example.legacy/deepHelper"]
    assert a["called_by"] == ["com.example.layer3/inner"] and "com.example.legacy" in a["how"]
    page = open(os.path.join(r["stages"], "report", "BRIEF.md"), encoding="utf-8").read() if os.path.exists(
        os.path.join(r["stages"], "report", "BRIEF.md")) else ""
    assert "deepHelper" in page


def test_a_vro_only_estate_is_asked_once_who_starts_its_workflows(run_pattern):
    r = run_pattern("vault")                     # no vRA export in this tree
    est = {e["id"]: e for e in _file(r)["estate"]}
    q = est["estate.started-by"]
    assert q["question"].startswith("There is no vRA export. Who starts these")
    assert q["options"][0] == "an external platform, through the vRO API" and "q2-external-callers" in q["settles"]
    before = _read(r, "3-brief", "brief.json")
    assert before["retire_candidates"]           # nothing in the export starts these workflows
    b = _answer_and_rebrief(r, "estate.started-by", "1")
    from kestra_migration_assistant.vra.stages import your_input as Y
    assert Y.state("q2-external-callers")["answer"] == "an external platform, through the vRO API"
    assert b["started_outside"] == {"answer": "an external platform, through the vRO API", "all": True}
    assert b["retire_candidates"] == []          # any of them may be started through the API: history decides
    from kestra_migration_assistant.vra.stages import visuals as V
    _use(r)
    a = _read(r, "1-assess", "assess.json")
    page = V.workflow_page(next(iter(a["workflows"])), a, b)
    assert "an external platform, through the vRO API (your answer about the estate)" in page


def test_out_of_scope_names_are_old_copies_and_tests_not_environments():
    from kestra_migration_assistant.vra.stages.patterns import SCOPE_NAME
    assert all(SCOPE_NAME.search(n) for n in ("OLD_Resize VM Disk", "test_REST_call", "Copy of Provision Linux VM",
                                              "Create AD User (vRO 7)", "Cleanup - deprecated"))
    assert not any(SCOPE_NAME.search(n) for n in ("Register VM in CMDB - Test", "Register VM in CMDB - Production",
                                                  "Rotate Old Keys", "Provision Linux VM - DR-Site"))
