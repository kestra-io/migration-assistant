"""
Writes the `upgrade/` conformance input: what kestra-migrate 1.0.0 left in an export folder's kestra-assessment/
after a person answered every item it asked, for tests/vra/test_upgrade.py, which runs it through this version.

The export is the detection/ and triage/ vRO trees side by side (labels `detection` and `triage`, so the specs
match), with full specs standing in for the model's: high confidence, one suspected defect and one open question
each. 1.0.0 runs with --no-model, every open item gets an answer that names its ID, 1.0.0 runs again (the answers
move to `answered`), and the plan is signed. Only `your-input/` and `data/` are kept (no pages, no sanitized copy),
under `upgrade/1.0.0/`.

Needs 1.0.0, never this checkout: run from the repository root with

    uv tool run --from git+https://github.com/kestra-io/migration-assistant@v1.0.0 kestra-migrate --version
    python tests/vra/conformance/upgrade/build.py <path to a 1.0.0 kestra-migrate>

The files it writes are committed.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
CONFORMANCE = os.path.dirname(HERE)
sys.path.insert(0, os.path.dirname(CONFORMANCE))
import webhooks  # noqa: E402  (tests/vra/webhooks.py: the fixtures' webhook URLs, written in at run time)
OUT = os.path.join(HERE, "1.0.0")


def full_spec(path: str) -> dict:
    """A committed minimal spec, filled to the schema 1.0.0 reads."""
    s = json.load(open(path))
    name = s["workflow_uid"].split("-", 1)[1].replace("---", " - ").replace("-", " ")
    return {**s, "purpose": s["purpose"] + " Written for the upgrade fixture; every name in it is invented.",
            "confidence": "high" if s["confidence"] != "low" else "low",
            "confidence_reason": "The steps are all in the export." if s["confidence"] != "low"
            else s.get("confidence_reason", "Key behaviour sits behind code that is not in the export."),
            "inputs": [], "outputs": [], "external_systems": [], "decisions": [], "side_effects": [],
            "assumptions": [], "state_records": [],
            "open_questions": [{"question": f"What should {name} do when the target is unreachable?",
                                "why_it_matters": "The flow's error handling depends on it.", "theme": "error semantics"}],
            "suspected_defects": [{"what": f"{name} does not check the input before the call",
                                   "evidence": "the first script item", "question": "Keep it, or fix it?"}]}


def answer_all(path: str) -> int:
    doc = yaml.safe_load(open(path))
    n = 0

    def fill(items):
        nonlocal n
        for it in items or []:
            if "each" in it:
                fill(it["each"])
                it["answer"] = it.get("answer") or ("yes" if it["id"] == "suggested" else None)
            elif not it.get("answer"):
                it["answer"] = "yes" if "suggested" in it else f"answer for {it['id']}"
                n += 1
    fill(doc.get("open"))
    if n:
        # written only when something was filled: read back with YAML 1.1 rules, a plain `yes` the tool wrote
        # would become a boolean
        doc["signed_for"] = "full plan"
        with open(path, "w") as f:
            yaml.safe_dump(doc, f, sort_keys=False, allow_unicode=True, width=1000)
    return n


def run(binary: str, export: str) -> None:
    env = dict(os.environ, XDG_CONFIG_HOME=os.path.join(os.path.dirname(export), "xdg"))
    subprocess.run([binary, "vra", "run", export, "--no-model"], check=True, env=env, stdin=subprocess.DEVNULL)


def build(binary: str) -> None:
    v = subprocess.run([binary, "--version"], capture_output=True, text=True, check=True).stdout.strip()
    assert v == "kestra-migrate 1.0.0", f"needs 1.0.0, got {v!r}"
    with tempfile.TemporaryDirectory() as tmp:
        export = os.path.join(tmp, "upgrade")
        for label in ("detection", "triage"):
            webhooks.copy(os.path.join(CONFORMANCE, label, "vro"), os.path.join(export, label))
        specs = os.path.join(export, "kestra-assessment", "data", "2-understand")
        os.makedirs(specs)
        for label in ("detection", "triage"):
            d = os.path.join(CONFORMANCE, label, "specs")
            for f in sorted(os.listdir(d)):
                with open(os.path.join(specs, f), "w") as fh:
                    json.dump(full_spec(os.path.join(d, f)), fh, indent=2)
        run(binary, export)
        answers = os.path.join(export, "kestra-assessment", "your-input", "answers.yaml")
        answer_all(answers)
        run(binary, export)
        assert answer_all(answers) == 0, "1.0.0 asked something new on the second run"
        if os.path.isdir(OUT):
            shutil.rmtree(OUT)
        out = os.path.join(export, "kestra-assessment")
        shutil.copytree(os.path.join(out, "your-input"), os.path.join(OUT, "your-input"))
        shutil.copytree(os.path.join(out, "data"), os.path.join(OUT, "data"),
                        ignore=shutil.ignore_patterns("sanitized", "model-calls.jsonl"))


if __name__ == "__main__":
    build(sys.argv[1])
