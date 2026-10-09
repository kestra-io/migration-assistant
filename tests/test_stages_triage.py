"""
Triage rules, checked on real workflows.

Each expected outcome below was checked by hand against the workflow's XML
and its spec, on 2026-09-25 (re-checked 2026-09-27 after the move to estates).
The note on each case says what in the XML or spec decides it, so a reviewer
can re-check without running anything.

Where each case runs:
- `alb-*`: the avi-sample estate's Assess output (1-assess/assess.json) and its specs
  (samples/avi/model-output/2-understand/), built by the `sample_stages` fixture (tests/conftest.py).
- `vspk-*`: the nuage-sample estate, the same way.
- `canvas-*`, `pkgdiff-*`, `aria-xml-*`: the conformance inputs in tests/conformance/,
  assessed here in a temporary folder with the specs in tests/conformance/specs/.
  They are test inputs only: no brief, no waves.

The sample estates are built once per session from samples/ (stage output is not committed).
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from kestra_migration_assistant.vra.stages import common as C  # noqa: E402
from kestra_migration_assistant.vra.stages import triage as TR  # noqa: E402

CONFORMANCE = os.path.join(ROOT, "tests", "conformance")
ESTATE_OF = {"alb": "avi-sample", "vspk": "nuage-sample"}

# uid -> (expected outcome, what in the XML or spec decides it)
HAND_CHECKED = {
    "vspk-Create-Organization": (
        "Wrapper",
        "XML: two items, item0 is a link to 'Find or Create Enterprise for Organization', item1 an end. "
        "No script. Spec: 'a one-line delegating call'."),
    "vspk-Assign-User-to-Group": (
        "Wrapper",
        "XML: one scriptable task, three lines, the only call is group.assignUsers(session, users) on a "
        "VSPK:Group. No decisions."),
    "alb-Delete-Vs-with-dependent-objects-using-model": (
        "Wrapper",
        "XML: two action items, initVroWorkflow (Avi batch setup) and deleteObjectObj. Both scripts are "
        "'Auto generated script, cannot be modified'. One real call."),
    "canvas-Switch-Happy-Path": (
        "Convert",
        "XML: a switch on operationType to four tasks that only System.log. No targets. Spec confidence high."),
    "alb-Get-Leader-IP-and-Cluster-Version": (
        "Convert",
        "XML: one task, GET cluster/runtime through the Avi client, parses the leader IP and version. "
        "Avi is reachable over HTTP. Spec confidence high."),
    "pkgdiff-v1.1-WF_test3": (
        "Convert",
        "XML: one task, System.debug('Hello beautifull world'). The only OGNL qualifier is "
        "linkedEnumeration='__NULL__', which carries no form logic. Spec confidence high."),
    "pkgdiff-v1.0-WF_test2": (
        "Convert",
        "Was Near-duplicate on the combined corpus. Its cluster now also holds pkgdiff-v1.1-WF_test1, "
        "which the old split held out: 4 items to WF_test2's 3, so it is the first of a kind, and WF_test2 "
        "no longer has the same shape. Checked by hand 2026-09-27: v1.1-WF_test1 adds one empty task."),
    "alb-Reconfigure-Virtual-service": (
        "Near-duplicate",
        "XML: identical to 'Reconfigure Virtual service deprecated.xml' apart from the display name "
        "(diff of the two files with IDs removed: one line). The deprecated copy is the first of a kind."),
    "alb-Detach-Avi-Controller": (
        "Rebuild",
        "XML: one line, AviVroPlugin.removeVroClient(controller). It edits vRO's own Avi endpoint registry, "
        "which Kestra replaces with secrets."),
    "alb-Create-Virtual-service": (
        "Rebuild",
        "XML: calls com.vmware.avi/createVRAPoolObj. Spec: built 'for consumption by vRealize Automation "
        "(vRA) as a cloud agnostic load balancer day-1 action'."),
    "vspk-Process-Network-Notification": (
        "Rebuild",
        "XML: a switch on the actionType input to a link to Create Network or Delete Network. It is a "
        "vCloud Director notification handler."),
    "canvas-User-Interaction": (
        "Manual",
        "XML: three items of type input (user interaction)."),
    "canvas-Workflow-Test": (
        "Manual",
        "XML: the waiting-timer loops back through a custom-condition (a cycle), and item3 links to "
        "9e4503db-..., which is not in the export."),
    "alb-Change-password": (
        "Manual",
        "XML: presentation has an OGNL qualifier calling GetAction('com.vmware.avi','getVROControllerList')."),
    "vspk-Create-Enterprise-Admin": (
        "Manual",
        "Spec confidence low: three of its four nested calls go to VSPK plugin workflows not in the export."),
    "alb-Avi-SSH-Command": (
        "Manual",
        "XML: an attribute of type Path (a file on the vRO appliance). Otherwise high confidence."),
    "canvas-Async-Workflow-Test": (
        "Needs owner input",
        "XML: item1 launches 9e4503db-cbaa-435a-9fad-144409c08df0 asynchronously; nothing in the export has that ID. "
        "Nothing else blocks it, so an owner supplying the callee is all it needs."),
    "vspk-Enable-VLAN-Override": (
        "Needs owner input",
        "XML: calls action vim3WaitTaskEnd, which is not in the export. Spec confidence high; no form logic, "
        "no user interaction, no cycle."),
    "alb-VS-Creation-using-model": (
        "Convert",
        "Spec confidence medium, no hard blockers, Avi reachable over HTTP. Medium counts as Convert with an "
        "owner spec review as a wave gate."),
}


class Assessed:
    """One Assess output and the spec folder that goes with it."""

    def __init__(self, assess: dict, spec_dir: str):
        self.a, self.spec_dir = assess, spec_dir

    def spec(self, uid):
        p = os.path.join(self.spec_dir, f"{uid}.json")
        return json.load(open(p)) if os.path.exists(p) else None


@pytest.fixture(scope="module")
def conformance(tmp_path_factory):
    from kestra_migration_assistant.vra.stages import assess as A
    tmp = tmp_path_factory.mktemp("conformance-stages")
    inputs = {"canvas": os.path.join(CONFORMANCE, "canvas"), "aria-xml": os.path.join(CONFORMANCE, "aria-xml"),
              "pkgdiff-v1.0": os.path.join(CONFORMANCE, "pkgdiff-v1.0.package"),
              "pkgdiff-v1.1": os.path.join(CONFORMANCE, "pkgdiff-v1.1.package")}
    old = os.environ.get("VRO_NO_MODEL")
    os.environ["VRO_NO_MODEL"] = "1"
    try:
        C.configure("conformance", source_dir=CONFORMANCE, stages_dir=str(tmp),
                    spec_dir=os.path.join(CONFORMANCE, "specs"), inputs=inputs)
        r = A.run()
    finally:
        if old is None:
            os.environ.pop("VRO_NO_MODEL", None)
    return Assessed(r, os.path.join(CONFORMANCE, "specs"))


_SAMPLE_STAGES = None


def _estate(name):
    stages = _SAMPLE_STAGES(name)
    return Assessed(json.load(open(os.path.join(stages, "data", "1-assess", "assess.json"))), os.path.join(stages, "data", "2-understand"))


@pytest.fixture(scope="module", autouse=True)
def _samples(sample_stages):
    global _SAMPLE_STAGES
    _SAMPLE_STAGES = sample_stages


@pytest.fixture(scope="module")
def assessed(conformance):
    cache = {}

    def get(uid):
        name = ESTATE_OF.get(uid.split("-")[0])
        if name is None:
            return conformance
        if name not in cache:
            cache[name] = _estate(name)
        return cache[name]
    return get


def _triage(x: Assessed, uid):
    wf = x.a["workflows"]
    fok = {c["cluster_id"]: c["first_of_a_kind"] for c in x.a["clusters"]}
    w = wf[uid]
    return TR.triage_workflow(w, x.spec(uid), wf[fok[w["cluster"]]], uid == fok[w["cluster"]])


def test_at_least_ten_hand_checked():
    assert len(HAND_CHECKED) >= 10
    assert {o for o, _ in HAND_CHECKED.values()} >= {"Wrapper", "Convert", "Near-duplicate", "Rebuild", "Manual",
                                                    "Needs owner input"}


@pytest.mark.parametrize("uid", sorted(HAND_CHECKED))
def test_hand_checked_outcome(assessed, uid):
    expected, why = HAND_CHECKED[uid]
    got = _triage(assessed(uid), uid)
    assert got["outcome"] == expected, f"{uid}: expected {expected} ({why}); got {got['outcome']}: {got['reason']}"


@pytest.mark.parametrize("where", ["conformance", "avi-sample", "nuage-sample"])
def test_every_workflow_gets_exactly_one_known_outcome(conformance, where):
    x = conformance if where == "conformance" else _estate(where)
    for uid in x.a["workflows"]:
        t = _triage(x, uid)
        assert t["outcome"] in TR.OUTCOMES
        assert t["outcome"] != "Retire", "Retire is never assigned by code without run history"


# ---- rule order, on synthetic records -------------------------------------

def _base(**kw):
    w = {"uid": "x", "name": "Do Thing", "cluster": "C99", "shape_sig": "task>end", "graph_sig": {},
         "script_hashes": ["a"], "item_type_counts": {"task": 1, "end": 1}, "plugin_types": [], "io": [],
         "wrapper": False, "wrapper_reason": None, "n_calls": 0, "logic_lines": 5, "called_actions": [],
         "nested": [], "ops": [], "inputs": [], "ognl": 0, "user_interaction": 0, "cycle": False,
         "file_typed_params": [], "unreachable_ops": [], "targets": []}
    w.update(kw)
    return w


def test_near_duplicate_beats_wrapper():
    first = _base(uid="f")
    w = _base(uid="x", wrapper=True, wrapper_reason="one call")
    t = TR.triage_workflow(w, {"confidence": "high"}, first, is_first=False)
    assert t["outcome"] == "Near-duplicate"
    assert any(a.startswith("Wrapper") for a in t["also"])


def test_wrapper_beats_manual():
    w = _base(wrapper=True, wrapper_reason="one call", ognl=2)
    t = TR.triage_workflow(w, {"confidence": "low"}, None, is_first=True)
    assert t["outcome"] == "Wrapper"
    assert any(a.startswith("Manual") for a in t["also"])


def test_medium_confidence_without_blockers_is_convert_with_spec_review():
    t = TR.triage_workflow(_base(), {"confidence": "medium"}, None, is_first=True)
    assert t["outcome"] == "Convert" and t["evidence"]["spec_review"] is True
    assert "owner review of the spec" in t["reason"]
    h = TR.triage_workflow(_base(), {"confidence": "high"}, None, is_first=True)
    assert h["outcome"] == "Convert" and h["evidence"]["spec_review"] is False


def test_low_confidence_stays_manual():
    t = TR.triage_workflow(_base(), {"confidence": "low"}, None, is_first=True)
    assert t["outcome"] == "Manual" and "spec confidence low" in t["reason"]


def test_only_blocking_themes_need_owner_input():
    spec = {"confidence": "high", "open_questions": [{"question": "q", "theme": "error semantics"}]}
    assert TR.triage_workflow(_base(), spec, None, True)["outcome"] == "Convert"
    spec["open_questions"].append({"question": "q2", "theme": "correctness"})
    t = TR.triage_workflow(_base(), spec, None, True)
    assert t["outcome"] == "Needs owner input"
    assert t["evidence"]["blocking_questions"] == ["q2"]


def test_missing_callee_needs_owner_input():
    w = _base(nested=[{"in_export": False, "kind": "unknown workflow", "display_name": "asyncCall",
                       "workflow_id": "9e45"}])
    t = TR.triage_workflow(w, {"confidence": "high"}, None, True)
    assert t["outcome"] == "Needs owner input" and t["evidence"]["missing"] == ["asyncCall"]


def test_hand_rewrite_blocker_wins_over_owner_questions():
    """A cycle needs a rewrite whatever the answers, so the workflow is Manual, and the questions are kept."""
    w = _base(cycle=True, nested=[{"in_export": False, "kind": "unknown workflow", "display_name": "x",
                                   "workflow_id": "x"}])
    t = TR.triage_workflow(w, {"confidence": "high"}, None, True)
    assert t["outcome"] == "Manual"
    assert t["evidence"]["blockers"] and t["evidence"]["owner_questions"]


@pytest.mark.parametrize("kw", [{"ognl": 1}, {"user_interaction": 2}, {"cycle": True},
                                {"file_typed_params": ["Path"]}, {"unreachable_ops": ["X.do (mystery)"]}])
def test_each_hand_rewrite_blocker_is_manual(kw):
    assert TR.triage_workflow(_base(**kw), {"confidence": "high"}, None, True)["outcome"] == "Manual"


def test_unreachable_target_is_manual():
    w = _base(unreachable_ops=["X.do (mystery)"])
    assert TR.triage_workflow(w, {"confidence": "high"}, None, True)["outcome"] == "Manual"


def test_owner_override_wins_and_keeps_rule_reason():
    t = TR.triage_workflow(_base(), {"confidence": "high"}, None, True,
                           override={"outcome": "Retire", "reason": "unused"})
    assert t["outcome"] == "Retire" and t["overridden"] and "rules said Convert" in t["reason"]


def test_action_client_for_plugin_target_is_plugin_covered():
    targets = {"ssh-host": {"reach": "plugin", "name": "SSH hosts", "kestra_tasks": ["io.kestra.plugin.fs.ssh.Command"]}}
    a = {"uid": "a", "key": "k", "wrapper": False, "library_for": ["ssh-host"], "ops": [{"op": "x"}, {"op": "y"}],
         "calls_actions": [], "called": True}
    assert TR.triage_action(a, targets, None)["outcome"] == "Plugin-covered library"


def test_action_client_for_http_target_is_rebuild():
    targets = {"avi-controller": {"reach": "http", "name": "Avi", "kestra_tasks": ["io.kestra.plugin.core.http.Request"],
                                  "sdk_alternative": "avisdk on PyPI"}}
    a = {"uid": "a", "key": "k", "wrapper": False, "library_for": ["avi-controller"], "ops": [{"op": "x"}, {"op": "y"}],
         "calls_actions": [], "called": True}
    t = TR.triage_action(a, targets, None)
    assert t["outcome"] == "Rebuild" and "avisdk" in t["reason"]


def test_default_error_handler_is_a_conversion_note_not_an_outcome():
    w = _base(default_error_handler={"target": "item3", "throw_bind_name": "errorMessage"},
              error_handler_path=["item3", "item5"])
    t = TR.triage_workflow(w, {"confidence": "high"}, None, True)
    assert t["outcome"] == "Convert"
    assert t["notes"] and "item3 > item5" in t["notes"][0] and "errors" in t["notes"][0]


def test_error_handler_workflows_carry_the_note(conformance):
    for uid in ("canvas-Default-Error-Handler-Custom-Item", "canvas-Workflow-Test", "canvas-Default-Error-Handler-Happy"):
        assert _triage(conformance, uid)["notes"], uid


def test_conformance_inputs_are_all_parsed(conformance):
    """Every conformance workflow is assessed, including the ones the old held-out split hid."""
    assert conformance.a["counts"]["workflows"] == 29
    assert conformance.a["counts"]["parse_failures"] == 0
