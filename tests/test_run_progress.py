"""`vro.py run` prints one line per stage as it goes, and a last line with the total, the cache and the page."""
from __future__ import annotations

import os
import re
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_run_prints_a_line_per_stage_and_a_last_line(tmp_path, monkeypatch, capsys):
    from vro2k_assess import cli
    from vro2k_assess.stages import common as C
    helpers = os.path.join(ROOT, "tests", "conformance", "helpers")
    src = tmp_path / "sources" / "est"
    shutil.copytree(os.path.join(helpers, "vro"), src / "vro")
    shutil.copytree(os.path.join(helpers, "vra"), src / "helpers-vra")
    monkeypatch.setattr(C, "SOURCES_ROOT", str(tmp_path / "sources"))
    monkeypatch.setattr(C, "STAGES_ROOT", str(tmp_path / "stages"))
    monkeypatch.setenv("VRO_NO_MODEL", "1")
    assert cli.main(["run", "--estate", "est", "--no-model"]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    tops = [x for x in lines if not x.startswith(" ")]
    for name in ("Sanitize", "Preflight", "Assess", "Understand", "Brief", "Your input", "Pages", "Status"):
        assert any(x.startswith(name + " ") for x in tops), (name, out)
    # a stage line: what it found, its model calls and its time
    assess = next(x for x in tops if x.startswith("Assess "))
    assert re.search(r"\d+ workflows?, \d+ actions?, \d+ targets?", assess), assess
    assert re.search(r"(no model calls|from cache|billed model call)", assess) and re.search(r"\d+\.\ds$", assess)
    last = tops[-1]
    assert last.startswith("Done in ") and "index.html" in last, last
    assert re.search(r"(model results all from cache|no model calls|billed model call)", last)
    assert "—" not in out


def test_a_long_stage_says_it_started_and_what_it_is_doing(monkeypatch, capsys):
    import time
    from vro2k_assess import cli
    from vro2k_assess.stages import common as C

    def stage(s):
        if s == "understand":
            time.sleep(0.15)
            C.progress("wrote spec 1 of 2: A (0.1s)")
            C.progress("wrote spec 2 of 2: B (0.1s)")
            return "2 specs, 2 written this run"
        return "done at once"
    monkeypatch.setattr(cli, "run_stage", stage)
    monkeypatch.setattr(cli, "STARTED_AFTER", 0.05)
    monkeypatch.setattr(cli, "_calls_since", lambda n: (0, {"billed": 0, "cached": 0, "cost": 0.0}))
    monkeypatch.setattr(C, "INDEX", "index.html")
    cli._run_with_progress(["preflight", "understand", "summary"])
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("Preflight   done at once;")                 # instant: one line
    assert lines[1] == "Understand  started"
    assert lines[2] == "Understand  wrote spec 1 of 2: A (0.1s)" and lines[3] == "Understand  wrote spec 2 of 2: B (0.1s)"
    assert lines[4].startswith("Understand  2 specs, 2 written this run;")
    assert C._PROGRESS is None                                                 # nothing reports outside run
