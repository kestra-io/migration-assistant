"""
Nothing is asked twice (brief.cover_questions).

On the review estate (tests/conformance/review/, invented names: a REST client with basic auth and certificate
checks off, shared state in a configuration element, blocking subscriptions), its specs are copied and given
the questions real specs ask: whether to keep TLS verification off, which credentials to use, production or
lab endpoints, and whether two runs for one deployment can race on the registry. Each is answered by a decision
the plan already asks, so it is not asked again: it leaves the spec's open questions, the decision names it,
the workflow page and the open questions page point to the decision, and a concurrency question on shared state
no longer holds its workflow in Needs owner input.
"""
from __future__ import annotations

import json
import os
import shutil
import sys

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from kestra_migration_assistant.vra.stages import common as C  # noqa: E402

REVIEW = os.path.join(ROOT, "tests", "conformance", "review")
CREATE = "review-Dns---Create-Zone"
EXTRA = [
    {"question": "Should the converted flow keep certificate verification off (hostVerification = false) for the "
                 "Infoblox calls?", "theme": "external contract", "why_it_matters": "security"},
    {"question": "Which credentials does the flow use: the basic auth from the endpoint element, or a service "
                 "account?", "theme": "external contract", "why_it_matters": "secrets"},
    {"question": "Are the endpoint values in the element production or lab?", "theme": "scope",
     "why_it_matters": "where the pilot runs"},
    {"question": "Can two CREATE events for the same deployment run concurrently and overwrite each other's "
                 "registry entry?", "theme": "concurrency", "why_it_matters": "lost updates"},
]
KEEP = "Which TTL should new zones get in production?"


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("ask-once")
    specs = tmp / "specs"
    shutil.copytree(os.path.join(REVIEW, "specs"), specs)
    p = specs / f"{CREATE}.json"
    s = json.load(open(p))
    s["open_questions"] += EXTRA
    json.dump(s, open(p, "w"), indent=1)
    old = os.environ.get("VRO_NO_MODEL")
    os.environ["VRO_NO_MODEL"] = "1"
    try:
        C.configure("review", source_dir=REVIEW, stages_dir=str(tmp / "stages"), spec_dir=str(specs),
                    inputs={"review": os.path.join(REVIEW, "vro")},
                    vra_inputs={"review-vra": os.path.join(REVIEW, "vra")})
        from kestra_migration_assistant.vra.stages import assess as A, brief as B, preflight as P, visuals as V
        P.run()
        a = A.run()
        b = B.run()
        V.run()
        yield {"assess": a, "brief": b, "answers": yaml.safe_load(open(C.ANSWERS)), "text": open(C.ANSWERS).read(),
               "oq": open(os.path.join(C.BRIEF, "open-questions.md")).read(),
               "page": open(os.path.join(C.PAGES, "workflows", a["workflows"][CREATE]["short"] + ".html")).read()}
    finally:
        if old is None:
            os.environ.pop("VRO_NO_MODEL", None)


def _covered(run):
    return {c["question"]: c for c in run["brief"]["open_questions"]["covered"].get(CREATE, [])}


def test_each_question_a_decision_asks_is_covered_by_that_decision(run):
    cov = {}
    for c in run["brief"]["open_questions"]["covered"].get(CREATE, []):
        cov.setdefault(c["question"], set()).add(c["key"])
    tls, cred, ep, race = (q["question"] for q in EXTRA)
    assert any(k.startswith("tls.") for k in cov[tls])
    assert cov[cred] & {k for k in cov[cred] if k.startswith(("auth.", "endpoint."))}
    assert all(k.startswith("endpoint.") for k in cov[ep])
    assert any(k.startswith("state-store.") for k in cov[race])
    assert KEEP not in cov                       # a question no decision asks stays a question


def test_a_covered_question_is_asked_once_in_answers_yaml(run):
    from kestra_migration_assistant.vra.stages import your_input as Y
    flat = Y.flat(run["answers"])
    # each spec-question item's question: the TTL question is one, none of the covered ones is
    asked = {k: e["question"] for k, e in flat.items() if k.startswith("question.")}
    assert any("TTL" in t for t in asked.values())
    assert not any(q["question"][:40] in t for q in EXTRA for t in asked.values())
    # the deciding items carry the questions they also answer (the brief keeps the cover; answers.yaml asks once)
    assert any("keep certificate verification off" in q["question"]
               for cs in run["brief"]["open_questions"]["covered"].values() for q in cs)


def test_a_concurrency_question_on_shared_state_closes_with_the_state_decision(run):
    tri = run["brief"]["triage"][CREATE]
    assert tri["outcome"] != "Needs owner input", tri["reason"]
    keys = {e["closes"]["key"] for w in run["brief"]["plan"]["waves"] for e in w["entry_criteria"]
            if (e.get("closes") or {}).get("kind") == "decision"}
    assert not any(k.startswith(f"question.") for k in keys)
    st = next(k for k in keys if k.startswith("state-store."))
    d = run["brief"]["decisions"]["asked"][st]
    assert any("concurrently" in q for q in d["also_answers"])


def test_the_spec_points_to_the_decision(run):
    assert "Asked once, by a decision" in run["page"] and "tls." in run["page"]
    assert "## Asked once" in run["oq"] and "Decided in `tls." in run["oq"]
    assert sum(1 for g in run["brief"]["open_questions"]["groups"] if CREATE in g["workflows"]
               and any(q["question"] in g["question"] for q in EXTRA)) == 0
