"""
check-intake: the planted-secrets fixture is rejected with a masked report, and its sanitized copy is accepted.

The fixture tests/vra/conformance/sanitize/planted-secrets/ holds invented values only.
"""
from __future__ import annotations

import os
import subprocess
import sys
import zipfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from kestra_migration_assistant.vra import intake
from kestra_migration_assistant.vra import sanitize as S

PLANTED = os.path.join(ROOT, "tests", "vra", "conformance", "sanitize", "planted-secrets")

# The secret-shaped values in the fixture. Hosts, IPs, emails and users are not grounds for rejection.
SECRETS = {
    "Hunter2!Winter": "password, token or key assignment",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ2cm8ifQ.c2lnbmF0dXJlc2lnbmF0dXJl": "JWT",
    "AbCdEf0123456789XyZabcdEF0123": "bearer or basic token",
    "S3cr3tValue!9": "SecureString value",
    "Pl@ntedPassw0rd": "SecureString value",
}


def cli(path: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "kestra_migration_assistant", "vra", "check-intake", path],
                          capture_output=True, text=True, cwd=ROOT)


def zip_tree(src: str, dst: str) -> str:
    with zipfile.ZipFile(dst, "w") as z:
        for dp, _d, fs in os.walk(src):
            for f in fs:
                p = os.path.join(dp, f)
                z.write(p, os.path.relpath(p, src))
    return dst


@pytest.fixture(scope="module")
def sanitized(tmp_path_factory):
    out = tmp_path_factory.mktemp("sanitized")
    S.sanitize_inputs({"planted-secrets": PLANTED}, str(out))
    return str(out / "planted-secrets")


def test_planted_fixture_is_rejected_with_every_secret_found():
    r = intake.check(PLANTED)
    assert not r["accepted"]
    kinds = sorted(f["kind"] for f in r["findings"])
    assert kinds == sorted(SECRETS.values())
    assert r["files_seen"] == 4


def test_cli_rejects_with_exit_1_and_never_prints_a_value():
    p = cli(PLANTED)
    assert p.returncode == 1, p.stdout + p.stderr
    assert "REJECTED" in p.stdout
    for value in SECRETS:
        assert value not in p.stdout + p.stderr
        assert S._mask(value) in p.stdout


def test_sanitized_copy_is_accepted(sanitized):
    r = intake.check(sanitized)
    assert r["accepted"], r["findings"]
    p = cli(sanitized)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "ACCEPTED" in p.stdout


def test_package_form_rejected_then_accepted_after_sanitize(tmp_path):
    pkg = zip_tree(PLANTED, str(tmp_path / "planted.package"))
    assert cli(pkg).returncode == 1
    S.sanitize_inputs({"planted": pkg}, str(tmp_path / "s"))
    assert cli(str(tmp_path / "s" / "planted.package")).returncode == 0


def test_nested_zip_is_read(tmp_path):
    inner = zip_tree(PLANTED, str(tmp_path / "inner.zip"))
    outer = tmp_path / "outer.package"
    with zipfile.ZipFile(outer, "w") as z:
        z.write(inner, "resources/inner.zip")
    r = intake.check(str(outer))
    assert len(r["findings"]) == len(SECRETS)
    assert all(f["file"].startswith("outer.package!resources/inner.zip!") for f in r["findings"])


def test_placeholders_and_null_are_not_values():
    text = ('password = "<SECRET-01>"; token = "<user>"\n'
            '<attrib name="p" type="SecureString"><value encoded="n"><![CDATA[__NULL__]]></value></attrib>')
    assert intake.scan_text(text) == []


def test_missing_path_exits_2(tmp_path):
    assert cli(str(tmp_path / "nope")).returncode == 2


def test_unsafe_archive_exits_2(tmp_path):
    bad = tmp_path / "evil.package"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("../escape.xml", "<x/>")
    p = cli(str(bad))
    assert p.returncode == 2
    assert "unsafe to open" in p.stderr
