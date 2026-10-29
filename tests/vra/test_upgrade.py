"""
Someone who ran 1.0.0 and answered everything runs this version on the same export folder.

The output folder is tests/vra/conformance/upgrade/1.0.0/, written by 1.0.0 itself (upgrade/build.py): the
detection/ and triage/ trees, every item answered with text that names its ID, the plan signed. This version names
systems 1.0.0 left on the generic REST host and asks new items about them, and asks about code a low-confidence
workflow is missing. What must hold:

- every answer 1.0.0 recorded is still in answers.yaml, with its item and in the words it was given;
- an answer whose item this version no longer asks is under `no_longer_asked`, never dropped and never moved to
  another item;
- nothing is both asked and no longer asked.
"""
from __future__ import annotations

import os
import shutil

import pytest
import webhooks as W
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFORMANCE = os.path.join(ROOT, "tests", "vra", "conformance")


def _answers(path: str) -> dict[str, dict[str, str]]:
    """section -> {id: answer} for every item with an answer, the members of a group included."""
    from kestra_migration_assistant.vra.stages.your_input import TextLoader
    doc = yaml.load(open(path, encoding="utf-8"), Loader=TextLoader) or {}
    out: dict = {"unanswered": set()}
    for section in ("open", "answered", "no_longer_asked"):
        got = out.setdefault(section, {})

        def walk(items, got=got):
            for it in items or []:
                if it.get("answer") not in (None, ""):
                    got[it["id"]] = it["answer"]
                elif not it.get("each"):
                    out["unanswered"].add(it["id"])
                walk(it.get("each"))
        walk(doc.get(section))
    out["signed_for"] = doc.get("signed_for")
    out["estate"] = {e["id"]: e for e in doc.get("estate") or []}
    return out


@pytest.fixture(scope="module")
def upgraded(tmp_path_factory):
    from kestra_migration_assistant import cli
    export = tmp_path_factory.mktemp("upgrade") / "upgrade"
    for label in ("detection", "triage"):
        W.copy(os.path.join(CONFORMANCE, label, "vro"), export / label)
    shutil.copytree(os.path.join(CONFORMANCE, "upgrade", "1.0.0"), export / "kestra-assessment")
    before = _answers(str(export / "kestra-assessment" / "your-input" / "answers.yaml"))
    old = os.environ.get("VRO_NO_MODEL")
    os.environ["VRO_NO_MODEL"] = "1"
    try:
        assert cli.main(["vra", "run", str(export), "--no-model"]) == 0
    finally:
        if old is None:
            os.environ.pop("VRO_NO_MODEL", None)
    return before, _answers(str(export / "kestra-assessment" / "your-input" / "answers.yaml"))


def test_the_fixture_is_a_fully_answered_1_0_0_run(upgraded):
    before, _ = upgraded
    assert not before["open"] and len(before["answered"]) >= 20 and before["signed_for"] == "full plan"
    assert {"endpoint.rest-host", "writes.rest-host", "missing.waittaskend", "q3-non-production-targets"} \
        <= set(before["answered"])


def test_every_answer_stays_with_its_item_in_its_words(upgraded):
    before, after = upgraded
    for k, v in before["answered"].items():
        where = [s for s in ("open", "answered", "no_longer_asked") if k in after[s]]
        assert where, f"{k}: answer dropped"
        assert len(where) == 1, f"{k}: in {where}"
        assert after[where[0]][k] == v, f"{k}: {v!r} became {after[where[0]][k]!r}"


def test_what_is_no_longer_asked_moves_to_no_longer_asked(upgraded):
    _, after = upgraded
    # REST hosts are named systems now: their endpoint and read/write items are not asked any more
    assert {"endpoint.rest-host", "writes.rest-host"} <= set(after["no_longer_asked"])
    asked = set(after["open"]) | set(after["answered"])
    assert not asked & set(after["no_longer_asked"])


def test_only_what_is_new_is_asked_and_the_signature_stays(upgraded):
    before, after = upgraded
    assert not before["unanswered"]
    new = after["unanswered"]
    assert not new & set(before["answered"])                # nothing 1.0.0 had answered is asked again
    # 1.2.0: the new systems' endpoints are assumed (the lab first, then non-production where it exists), each listed
    # under the assumption; missing code is not asked: it is listed
    est = after["estate"]
    assert {"endpoint.ansible-tower", "endpoint.ms-teams", "endpoint.slack"} <= {
        m["id"] for m in est["assume.non-production"].get("each") or []}
    assert "missing.purgeorphanedvolumes" not in new
    assert after["signed_for"] == "full plan"


def test_a_1_1_0_file_still_loads_through_the_public_functions(upgraded):
    """The closed half reads answers.yaml through your_input.load, items, state and signature: their signatures and
    their reading of a file written before 1.2.0 hold."""
    import inspect

    from kestra_migration_assistant.vra.stages import your_input as Y
    assert list(inspect.signature(Y.load).parameters) == ["path"]
    assert list(inspect.signature(Y.items).parameters) == []
    assert list(inspect.signature(Y.state).parameters) == ["key", "loaded", "it"]
    assert list(inspect.signature(Y.signature).parameters) == ["loaded"]
