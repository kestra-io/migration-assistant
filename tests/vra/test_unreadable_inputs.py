"""
What the export folder holds besides the export, on the review/ conformance tree:

- the tool's own output is never read as input: the kestra-assessment/ folder, and a ZIP that holds one (the
  results zipped to share them and left in the export folder), at the top or deeper down;
- any other file the tool cannot read stops nothing: no traceback; it is listed, with why in plain words, under
  "Not in the package" on the first screen and under "Add to the package" in the brief, and the run goes on.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import zipfile

CONFORMANCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conformance")


def _export(tmp) -> str:
    folder = tmp / "review"
    shutil.copytree(os.path.join(CONFORMANCE, "review", "vro"), folder / "review")
    shutil.copytree(os.path.join(CONFORMANCE, "review", "vra"), folder / "review-vra")
    return str(folder)


def _zip_of_results(path) -> None:
    """kestra-assessment.zip as a person makes it on macOS: the folder, its files, and __MACOSX copies."""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("kestra-assessment/", "")
        z.writestr("kestra-assessment/index.html", "<html>a brief</html>")
        z.writestr("kestra-assessment/your-input/answers.yaml", "open: []\n")
        z.writestr("__MACOSX/kestra-assessment/._index.html", "x")


def _run(monkeypatch, export: str, typed: str | None = None) -> tuple[int, str]:
    from kestra_migration_assistant.vra import cli, interactive
    out = io.StringIO()
    monkeypatch.setenv("VRO_NO_MODEL", "1")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", out)
    if typed is not None:
        monkeypatch.setattr(cli, "is_terminal", lambda: True)
        monkeypatch.setattr(interactive, "can_open_browser", lambda: False)
        monkeypatch.setattr(sys, "stdin", io.StringIO(typed))
    code = cli.main(["run", export, "--no-model"], interactive=typed is not None)
    return code, out.getvalue()


def test_a_zip_of_the_tools_own_output_is_never_read(tmp_path, monkeypatch):
    """rc1: "ValueError: ZIP is neither a package nor a workflow export: .../0-preflight/sanitized/
    kestra-assessment.zip", with a traceback, on the first run."""
    export = _export(tmp_path)
    _zip_of_results(os.path.join(export, "kestra-assessment.zip"))
    _zip_of_results(os.path.join(export, "review", "old-results.zip"))         # deeper down, any name
    code, out = _run(monkeypatch, export)
    assert code == 0, out[-2000:]
    assert "Traceback" not in out
    copy = os.path.join(export, "kestra-assessment", "data", "0-preflight", "sanitized")
    copied = [f for _dp, _d, fs in os.walk(copy) for f in fs]
    assert "kestra-assessment.zip" not in copied and "old-results.zip" not in copied
    brief = json.load(open(os.path.join(export, "kestra-assessment", "data", "3-brief", "brief.json")))
    assert brief["unreadable"] == []                                    # skipped, like the output folder: not a fault


def test_a_file_that_cannot_be_read_is_listed_with_why_and_the_run_goes_on(tmp_path, monkeypatch):
    export = _export(tmp_path)
    with zipfile.ZipFile(os.path.join(export, "photos.zip"), "w") as z:
        z.writestr("holiday.txt", "not an export")
    with open(os.path.join(export, "broken.package"), "wb") as f:
        f.write(b"PK\x03\x04 cut short")
    code, out = _run(monkeypatch, export, typed="\n" * 20)
    assert code == 0, out[-2000:]
    assert "Traceback" not in out
    # the first screen
    assert "Not in the package: 2 files in the export folder could not be read" in out
    assert "- photos.zip: A ZIP that holds neither a vRO package nor a single workflow export." in out
    assert "- broken.package: Not a ZIP file" in out
    # the brief, under "Add to the package", with the same reasons and no path of the machine's
    brief = json.load(open(os.path.join(export, "kestra-assessment", "data", "3-brief", "brief.json")))
    assert sorted(u["file"] for u in brief["unreadable"]) == ["broken.package", "photos.zip"]
    md = open(os.path.join(export, "kestra-assessment", "report", "BRIEF.md"), encoding="utf-8").read()
    section = md.split("## Add to the package", 1)[1].split("\n## ", 1)[0]
    assert "`photos.zip`: A ZIP that holds neither" in section and "`broken.package`: Not a ZIP file" in section
    assert str(tmp_path) not in json.dumps(brief["unreadable"]) and str(tmp_path) not in section
    # and the workflows that could be read are assessed as usual
    assert brief["names"]


def test_plain_reasons_carry_no_type_and_no_path():
    from kestra_migration_assistant.vra.parse import formats
    assert formats.plain_reason("ValueError: unrecognised input: /Users/someone/exports/acme/x.bin") == \
        "Unrecognised input."
    assert formats.plain_reason(r"BadZipFile: File is not a zip file: C:\exports\acme\a.package") == \
        "File is not a zip file."
