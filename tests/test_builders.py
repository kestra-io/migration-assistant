"""
Body builders on top of a client, and REST paths made concrete by their callers. Checked on synthetic content
(tests/conformance/builders/, built by its build.py); every name in it is invented.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vro2k_assess.stages import common as C  # noqa: E402

BUILDERS = os.path.join(ROOT, "tests", "conformance", "builders")


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("builders-stages")
    old = os.environ.get("VRO_NO_MODEL")
    os.environ["VRO_NO_MODEL"] = "1"
    try:
        C.configure("builders", source_dir=BUILDERS, stages_dir=str(tmp), spec_dir=os.path.join(BUILDERS, "specs"),
                    inputs={"builders": os.path.join(BUILDERS, "vro")}, vra_inputs={})
        from vro2k_assess.stages import assess as A, brief as B, preflight as P
        P.run()
        a = A.run()
        b = B.run()
        inv = open(os.path.join(C.ASSESS, "target-inventory.md")).read()
    finally:
        if old is None:
            os.environ.pop("VRO_NO_MODEL", None)
    acts = {x["name"]: x for x in a["actions"]}
    tri = {t["name"]: t for t in b["action_triage"].values()}
    return {"assess": a, "acts": acts, "tri": tri, "inventory": inv}


def test_a_builder_is_part_of_its_client(run):
    a, t = run["acts"], run["tri"]
    assert a["buildVsdDomain"]["library_for"] == a["createVsdObject"]["library_for"] == ["nuage-vsd"]
    assert a["buildVsdDomain"]["client_through"] == ["createVsdObject"]
    assert t["buildVsdDomain"]["outcome"] == t["createVsdObject"]["outcome"] == "Rebuild"
    assert "part of the client it builds on (createVsdObject)" in t["buildVsdDomain"]["reason"]


def test_a_builder_that_also_renders_a_template_stays_part_of_the_client(run):
    a, t = run["acts"], run["tri"]
    z = a["buildVsdZone"]
    assert z["library_for"] == ["nuage-vsd"]
    assert z["client_through"] == ["createVsdObject"]
    assert z["builder_helpers"] == ["renderTemplate"]
    assert t["buildVsdZone"]["outcome"] == t["buildVsdDomain"]["outcome"]
    assert "renderTemplate" in t["buildVsdZone"]["reason"]
    # the renderer is judged on its own code (vRO's own state), not as the client
    assert not a["renderTemplate"]["library_for"]


def test_an_orchestrator_over_two_systems_is_not_a_builder(run):
    s = run["acts"]["setupNetwork"]
    assert not s["library_for"] and not s.get("client_through")
    assert run["tri"]["setupNetwork"]["by_caller"]


def test_a_client_path_takes_the_literals_its_callers_pass(run):
    c = run["acts"]["createVsdObject"]
    assert [o["op"] for o in c["ops"]] == ["POST /nuage/api/v6/{type}"]
    # two builders with literals, one through a forwarded parameter (the workflow passes "subnets"), one
    # from the orchestrator; no call site leaves the placeholder
    assert c["ops_resolved"] == ["POST /nuage/api/v6/domains", "POST /nuage/api/v6/enterprises",
                                 "POST /nuage/api/v6/subnets", "POST /nuage/api/v6/zones"]


def test_target_operations_and_workflow_ops_are_concrete(run):
    t = run["assess"]["targets"]["nuage-vsd"]
    ops = {o["op"] for o in t["operations"]} | set(t["action_only_operations"])
    assert ops == {"POST /nuage/api/v6/domains", "POST /nuage/api/v6/enterprises", "POST /nuage/api/v6/subnets",
                   "POST /nuage/api/v6/zones"}
    w = next(iter(run["assess"]["workflows"].values()))
    assert {o["op"] for o in w["ops"] if o["target"] == "nuage-vsd"} == ops
    rows = [ln for ln in run["inventory"].splitlines() if ln.startswith("| `POST /nuage")]
    assert len(rows) == 4 and not any("{type}" in ln for ln in rows)


def test_an_unknown_argument_keeps_its_placeholder():
    from vro2k_assess.stages import targets as T
    b = T.bind_args(["endpoint", "type"], ["ep", "someCall()"], {}, "")
    assert b == {}
    assert T.bind_op({"kind": "rest", "op": "POST /x/{type}"}, b) == "POST /x/{type}"
    b = T.bind_args(["endpoint", "type"], ["ep", "kind"], {"kind": "pool"}, "")
    assert T.bind_op({"kind": "rest", "op": "POST /x/{type}/{id}"}, b) == "POST /x/pool/{id}"
    b = T.bind_args(["type"], ["t"], {}, 'var t = "vsvip";\nf(t);')
    assert b == {"type": "vsvip"}
