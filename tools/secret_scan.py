#!/usr/bin/env python3
"""
Secret scanning of this repository: every git-tracked file, anything staged, or the whole history.

    python tools/secret_scan.py            # exit 1 and list findings if anything looks like a secret
    python tools/secret_scan.py --staged   # only what is staged (for a pre-commit hook)
    python tools/secret_scan.py --history  # every blob in every commit of every ref, under every path it had

Uses the sanitizer's detectors (private keys, JWTs, cloud and API key shapes, bearer tokens,
password/token assignments with a literal value, .env-style assignments) and prints each finding
masked, never the value. Files that plant fake secrets on purpose are listed in ALLOW.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from kestra_migration_assistant.vra import safety  # noqa: E402
from kestra_migration_assistant.vra import sanitize as S  # noqa: E402

# Fixtures that hold invented secrets on purpose, and the code that defines the patterns.
ALLOW = (
    "tests/conformance/sanitize/planted-secrets/",
    "tests/test_sanitize.py",
    "tests/test_intake.py",
    "tests/test_security.py",
    "tests/test_models.py",
    "kestra_migration_assistant/vra/sanitize.py",
    "tools/secret_scan.py",
)
ENV_ASSIGN = re.compile(r"(?m)^\s*(?:export\s+)?([A-Z][A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY|APIKEY|PRIVATE_KEY))\s*=\s*(\S{6,})")
# Not a secret: a placeholder, a template or Kestra expression, a vRO type name, or a plain word
# (a repository secret has digits or symbols; a word like "claude-code" is a setting).
PLACEHOLDER = re.compile(r"^(<[^>]*>|\$\{?[A-Za-z_.]+\}?|\{\{.*\}\}|x{3,}|\*{3,}|\.\.\.|__NULL__|"
                         r"SecureString|string|boolean|number|Array/.*|[A-Za-z]+:[A-Za-z]+|[A-Za-z][A-Za-z_-]*)$")


def not_a_credential(value: str) -> bool:
    """
    A literal after a secret-named key that cannot be one: no digit, and either a space in it (prose, like
    `"auth": "how the flows sign in"`, or code the pattern ran into, like `token: ".concat(wfToken.id, "`)
    or no letter at all (punctuation, like mermaid's `token:"->>-"`). Only the repository scan applies this;
    the sanitizer and the intake check stay strict, since an export is not this repository's own code.
    """
    return not re.search(r"\d", value) and (bool(re.search(r"\s", value)) or not re.search(r"[A-Za-z]", value))


def tracked(staged: bool) -> list[str]:
    cmd = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM", "-z"] if staged else ["git", "ls-files", "-z"]
    out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [p for p in out.split("\0") if p]           # NUL-separated: paths may contain spaces


def scan_text(path: str, text: str) -> list[tuple[str, int, str]]:
    found = []

    def add(kind, m, value):
        v = value.strip()
        if PLACEHOLDER.match(v):
            return
        line = text.count("\n", 0, m.start()) + 1
        found.append((kind, line, (v[:3] + "…" if len(v) > 6 else "…") + f" ({len(v)} chars)"))

    for rx in S.TOKEN_RES:
        for m in rx.finditer(text):
            add("token or key", m, m.group(m.lastindex or 0))
    for m in S.SECRET_KV_RE.finditer(text):
        if not not_a_credential(m.group(3)):
            add("secret assignment", m, m.group(3))
    for m in ENV_ASSIGN.finditer(text):
        add("env assignment " + m.group(1), m, m.group(2))
    return found


def scan_blob(label: str, raw: bytes, stats: dict, depth: int = 0) -> list[str]:
    """Scan one file's bytes. Nothing is skipped: a ZIP (a .package) is opened behind the ZIP guards
    and every entry scanned, nested two deep; any other binary is scanned as Latin-1, which is enough
    for key, token and JWT shapes."""
    out = []
    if raw[:2] == b"PK" and depth <= safety.MAX_NESTED_ZIP_DEPTH:
        try:
            with safety.open_zip(raw, label=label) as z:
                stats["zips"] += 1
                for info in z.infolist():
                    if not info.is_dir():
                        out += scan_blob(f"{label}!{info.filename}", safety.read_member(z, info.filename),
                                         stats, depth + 1)
                return out
        except (zipfile.BadZipFile, safety.UnsafeInput) as e:
            stats["unreadable"].append(f"{label} ({e})")
            return out
    if b"\x00" in raw[:8192]:
        stats["binary"] += 1
        text = raw.decode("latin-1")
    else:
        stats["text"] += 1
        text = raw.decode("utf-8", "replace")
    for kind, line, preview in scan_text(label, text):
        out.append(f"{label}:{line}: {kind}: {preview}")
    return out


def new_stats() -> dict:
    return {"text": 0, "binary": 0, "zips": 0, "allowed": 0, "unreadable": []}


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def history() -> tuple[list[str], dict]:
    """Every blob in every commit of every ref, each under every path it was committed at."""
    commits = git("rev-list", "--all").split()
    paths: dict[str, set[str]] = {}
    for c in commits:
        for entry in git("ls-tree", "-r", "-z", c).split("\0"):
            if not entry:
                continue
            meta, path = entry.split("\t", 1)
            _mode, typ, sha = meta.split()
            if typ == "blob":
                paths.setdefault(sha, set()).add(path)
    st = new_stats()
    st.update(commits=len(commits), blobs=len(paths))
    findings = []
    for sha, names in sorted(paths.items()):
        names_s = sorted(names)
        if all(n.startswith(ALLOW) for n in names_s):
            st["allowed"] += 1
            continue
        raw = subprocess.run(["git", "cat-file", "blob", sha], cwd=ROOT, capture_output=True, check=True).stdout
        findings += [f"{sha[:10]} {f}" + (f" (also at {len(names_s) - 1} other path(s))" if len(names_s) > 1 else "")
                     for f in scan_blob(names_s[0], raw, st)]
    return findings, st


def main() -> int:
    if "--history" in sys.argv:
        findings, st = history()
        for f in findings:
            print(f)
        print(f"secret scan (history): {st['commits']} commits, {st['blobs']} distinct blobs; "
              f"{st['allowed']} allowlisted, the rest scanned ({st['text']} text, {st['binary']} binary as Latin-1, "
              f"{st['zips']} ZIPs opened); {len(st['unreadable'])} unreadable; {len(findings)} finding(s)",
              file=sys.stderr)
        for u in st["unreadable"]:
            print(f"  unreadable: {u}", file=sys.stderr)
        return 1 if findings or st["unreadable"] else 0
    staged = "--staged" in sys.argv
    findings, st = [], new_stats()
    files = tracked(staged)
    for rel in files:
        if rel.startswith(ALLOW):
            st["allowed"] += 1
            continue
        p = os.path.join(ROOT, rel)
        if not os.path.isfile(p):
            continue
        findings += scan_blob(rel, open(p, "rb").read(), st)
    for f in findings:
        print(f)
    print(f"secret scan: {len(files)} files, {st['allowed']} allowlisted, the rest scanned ({st['text']} text, "
          f"{st['binary']} binary as Latin-1, {st['zips']} ZIPs opened); {len(st['unreadable'])} unreadable; "
          f"{len(findings)} finding(s)", file=sys.stderr)
    for u in st["unreadable"]:
        print(f"  unreadable: {u}", file=sys.stderr)
    return 1 if findings or st["unreadable"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
