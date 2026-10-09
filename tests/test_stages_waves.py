"""
Wave planning: the grouping rule, the budget, blocked work and gates.

Synthetic estates check the rule itself; the last tests read the two sample estates' briefs.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from kestra_migration_assistant.vra.stages import waves as W  # noqa: E402

TARGETS = {t: {"name": t, "reach": "http"} for t in ("avi", "nuage", "vcd", "vc", "t4", "t5", "t6", "t7", "t8", "t9")}
ANSWERS = {"open": ["Q3", "Q4"]}


def _wf(uid, cluster, ops=(), nested=(), domain="D1", lines=5):
    return {"uid": uid, "cluster": cluster, "domain": domain, "script_lines": lines,
            "ops": [{"target": t, "mode": m, "op": f"{m} x"} for t, m in ops],
            "writes": sum(1 for _, m in ops if m == "write"),
            "nested": [{"target_uid": v} for v in nested]}


def _tri(outcome="Convert", **ev):
    return {"outcome": outcome, "evidence": ev}


def _plan(wfs, tri):
    return W.plan({"workflows": {w["uid"]: w for w in wfs}, "targets": TARGETS}, tri, ANSWERS)


@pytest.mark.parametrize("n,expected", [(8, 1), (16, 2), (32, 3), (33, 4), (64, 4), (65, 5),
                                        (1025, 9), (2048, 9), (2049, 10), (4096, 10), (10**6, 12)])
def test_budget(n, expected):
    assert W.wave_budget(n) == expected


def test_same_tier_and_primary_target_share_a_wave(monkeypatch):
    monkeypatch.setattr(W, "wave_budget", lambda n: 12)   # grouping only; the budget is tested above
    wfs = [_wf("a", "C1", [("avi", "write")]), _wf("b", "C2", [("avi", "write")]),
           _wf("c", "C3", [("nuage", "write")]), _wf("d", "C4")]
    p = _plan(wfs, {w["uid"]: _tri() for w in wfs})
    by = {u: w["wave"] for w in p["waves"] for u in w["workflows"]}
    assert by["a"] == by["b"] != by["c"]
    assert p["waves"][0]["workflows"] == ["d"], "no external target goes first"


def test_callee_wave_comes_first_even_when_riskier(monkeypatch):
    monkeypatch.setattr(W, "wave_budget", lambda n: 12)
    wfs = [_wf("reader", "C1", [("vcd", "read")], nested=["writer"]), _wf("writer", "C2", [("nuage", "write")])]
    p = _plan(wfs, {w["uid"]: _tri() for w in wfs})
    order = [u for w in p["waves"] for u in w["workflows"]]
    assert order.index("writer") < order.index("reader")
    reader_wave = next(w for w in p["waves"] if "reader" in w["workflows"])
    writer_wave = next(w for w in p["waves"] if "writer" in w["workflows"])
    assert reader_wave["depends_on"] == [writer_wave["wave"]]


def test_manual_is_not_waved_and_blocks_its_callers():
    wfs = [_wf("m", "C1"), _wf("caller", "C2", nested=["m"]), _wf("free", "C3")]
    tri = {"m": _tri("Manual"), "caller": _tri(), "free": _tri()}
    p = _plan(wfs, tri)
    waved = {u for w in p["waves"] for u in w["workflows"]}
    assert waved == {"free"}
    assert [b["uid"] for b in p["blocked"]] == ["caller"]
    assert "Manual work" in p["blocked"][0]["blocked_on"][0]
    assert p["not_waved"]["Manual"] == ["m"]


def test_owner_input_is_waved_with_its_questions_as_gates():
    wfs = [_wf("q", "C1", [("avi", "read")])]
    tri = {"q": _tri("Needs owner input", blocking_questions=["Is it atomic?"], missing=["asyncCall"])}
    crit = [c["criterion"] for c in _plan(wfs, tri)["waves"][0]["entry_criteria"]]
    assert any("Is it atomic?" in c for c in crit)
    assert any("asyncCall" in c for c in crit)


def test_medium_confidence_convert_gets_a_spec_review_gate():
    wfs = [_wf("m", "C1")]
    crit = [c["criterion"] for c in _plan(wfs, {"m": _tri(spec_review=True)})["waves"][0]["entry_criteria"]]
    assert "Owner reviews the spec for m (spec confidence medium)" in crit


def test_a_few_thousand_workflows_give_about_ten_waves():
    """3,000 workflows over 300 clusters, 10 targets, 3 tiers, 20 domains: 30 natural groups, budget 10."""
    wfs, tri = [], {}
    ts = list(TARGETS)
    for i in range(3000):
        c = i // 10
        t = ts[c % len(ts)]
        tier = c % 3
        ops = [] if tier == 0 else [(t, "read" if tier == 1 else "write")]
        wfs.append(_wf(f"w{i}", f"C{c:03d}", ops, domain=f"D{c % 20}"))
        tri[f"w{i}"] = _tri()
    p = _plan(wfs, tri)
    assert p["grouping"]["budget"] == 10
    assert len(p["waves"]) == 10
    assert sum(w["n"] for w in p["waves"]) == 3000
    # risk rises monotonically: no external target, then reads, then writes
    ranks = [{"no external target": 0, "read-only": 1, "writes": 2}[w["risk"]] for w in p["waves"]]
    assert ranks == sorted(ranks)


# ---- the real briefs, one per sample estate ---------------------------------

@pytest.fixture(params=["avi-sample", "nuage-sample"])
def brief(request, sample_stages):
    return json.load(open(os.path.join(sample_stages(request.param), "data", "3-brief", "brief.json")))


def test_real_brief_stays_within_the_wave_budget(brief):
    g = brief["plan"]["grouping"]
    assert g["budget"] == W.wave_budget(g["waved"])
    assert (1 if g["waved"] else 0) <= len(brief["plan"]["waves"]) <= g["budget"]


def test_real_brief_waves_hold_no_manual_work(brief):
    for w in brief["plan"]["waves"]:
        for u in w["workflows"]:
            assert brief["triage"][u]["outcome"] in W.WAVED, u


def test_real_brief_every_waved_or_blocked_workflow_is_accounted_for(brief):
    waved = [u for w in brief["plan"]["waves"] for u in w["workflows"]]
    blocked = [b["uid"] for b in brief["plan"]["blocked"]]
    assert len(waved) == len(set(waved))
    want = {u for u, t in brief["triage"].items() if t["outcome"] in W.WAVED}
    assert set(waved) | set(blocked) == want
