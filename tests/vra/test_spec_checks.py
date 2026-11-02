"""
A spec is checked only for a workflow that is migrated: one that is retired, or left out of the migration by its
owner, gets no spec question in answers.yaml or in the terminal.
"""
from __future__ import annotations

import io
import os
import shutil
import sys

CONFORMANCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conformance")


def _export(tmp) -> str:
    folder = tmp / "review"
    shutil.copytree(os.path.join(CONFORMANCE, "review", "vro"), folder / "review")
    shutil.copytree(os.path.join(CONFORMANCE, "review", "vra"), folder / "review-vra")
    d = folder / "kestra-assessment" / "data" / "2-understand"
    d.mkdir(parents=True)
    for f in os.listdir(os.path.join(CONFORMANCE, "review", "specs")):
        shutil.copy(os.path.join(CONFORMANCE, "review", "specs", f), d / f)
    return str(folder)


def test_no_spec_check_on_a_workflow_that_is_retired_or_left_out(tmp_path, monkeypatch):
    """A spec is checked only for a workflow that is migrated: one retired in corrections.yaml (or left out by the
    owner, which the brief also reads as Retire) gets no spec question, in the file or in the terminal."""
    import json

    import yaml

    from kestra_migration_assistant.vra import cli, interactive
    from kestra_migration_assistant.vra.stages import your_input as Y
    export = _export(tmp_path)
    monkeypatch.setenv("VRO_NO_MODEL", "1")
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert cli.main(["run", export, "--no-model", "--batch"]) == 0
    brief = json.load(open(os.path.join(export, "kestra-assessment", "data", "3-brief", "brief.json")))
    shorts = {v["short"] for v in brief["names"].values()}
    checked = {it["key"] for it in Y.items() if it["key"].startswith("spec.")}
    assert checked and checked <= {f"spec.{s}" for s in shorts}
    gone = sorted(checked)[0][len("spec."):]
    with open(os.path.join(export, "kestra-assessment", "your-input", "corrections.yaml"), "w") as f:
        yaml.safe_dump({"outcomes": {gone: {"outcome": "Retire", "reason": "no longer used"}}}, f)
    assert cli.main(["run", export, "--no-model", "--batch"]) == 0
    brief = json.load(open(os.path.join(export, "kestra-assessment", "data", "3-brief", "brief.json")))
    uid = next(u for u, v in brief["names"].items() if v["short"] == gone)
    assert brief["triage"][uid]["outcome"] == "Retire"
    now = {it["key"] for it in Y.items() if it["key"].startswith("spec.")}
    assert f"spec.{gone}" not in now and now == checked - {f"spec.{gone}"}
    assert f"spec.{gone}" not in open(os.path.join(export, "kestra-assessment", "your-input", "answers.yaml")).read()
    monkeypatch.setattr(interactive, "has_brief", lambda: True)
    assert not [q for q in interactive.to_offer(False) if q["id"] == f"spec.{gone}"]
