"""
The target inventory: how an operation on a typed variable is named, and that the inventory's rows come out in the
same order on every run. Checked on synthetic scripts and on the review conformance estate (invented names).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from kestra_migration_assistant.vra.stages import targets as T

REVIEW = os.path.join(ROOT, "tests", "vra", "conformance", "review")


def test_an_operation_is_named_after_the_type_as_written():
    """A plugin type (`VC:VirtualMachine`) and a constructor (`new SSHSession`) both name the operation with the
    type exactly as the script or binding wrote it."""
    T.load_plugin_map()
    typed = T.extract_ops("vm.sdkConnection.powerOnVM_Task(null);", {"vm": "VC:VirtualMachine"})
    assert [o["op"] for o in typed] == ["VC:VirtualMachine.sdkConnection.powerOnVM_Task"]
    built = T.extract_ops("var s = new SSHSession(host, user);\ns.executeCommand(cmd, true);", {})
    assert [(o["target"], o["op"]) for o in built] == [("ssh-host", "SSHSession.executeCommand")]


ASSESS = """
import json, os, sys
sys.path.insert(0, {root!r})
os.environ["VRO_NO_MODEL"] = "1"
from kestra_migration_assistant.vra.stages import common as C
from kestra_migration_assistant.vra.stages import assess, preflight
C.configure("review", source_dir={review!r}, stages_dir=sys.argv[1], spec_dir=os.path.join({review!r}, "specs"),
            inputs={{"review": os.path.join({review!r}, "vro")}},
            vra_inputs={{"review-vra": os.path.join({review!r}, "vra")}})
preflight.run()
r = assess.run()
print(json.dumps({{k: [o["op"] for o in t["operations"]] for k, t in r["targets"].items()}}))
"""


def test_the_inventory_rows_are_in_the_same_order_whatever_the_hash_seed(tmp_path):
    """Operations used by the same number of workflows come out by name, not in the process's set order: two runs
    with different string hashes write the same inventory."""
    code = ASSESS.format(root=ROOT, review=REVIEW)
    runs = []
    for seed in ("1", "2", "3"):
        r = subprocess.run([sys.executable, "-c", code, str(tmp_path / seed)], capture_output=True, text=True,
                           env={**os.environ, "PYTHONHASHSEED": seed})
        assert r.returncode == 0, r.stderr[-2000:]
        runs.append(json.loads(r.stdout.strip().splitlines()[-1]))
    assert runs[0] == runs[1] == runs[2]
    assert sum(len(ops) for ops in runs[0].values()) >= 10, "the estate needs targets with tied operations"
    report = json.load(open(tmp_path / "1" / "data" / "1-assess" / "assess.json"))
    for t in report["targets"].values():
        keys = [(-o["workflows"], o["op"]) for o in t["operations"]]
        assert keys == sorted(keys), t["key"]
