"""
Intake check: refuse an export that still carries secrets, before it is stored or read by any stage.

    vro.py check-intake <path>     # a .package, a .workflow, a ZIP, a BTVA tree or any folder
      exit 0   nothing secret-shaped found: accepted
      exit 1   something secret-shaped found: rejected, with a masked report
      exit 2   the path is missing or unsafe to open (ZIP bomb, traversal, symlink, over the size limits)

It runs the sanitizer's own detectors, read-only: private keys, JWTs, cloud and API key shapes, bearer
and basic tokens, password/token/key assignments with a literal value, long mixed-case-and-digit
literals, and any SecureString attribute or parameter whose value is not vRO's null sentinel
`__NULL__`. Hostnames, IPs, emails and user names are not grounds for rejection: they are what
`vro.py sanitize` redacts, and an export without them is not required.

A sanitized copy passes, because every value the sanitizer finds is a placeholder (`<SECRET-01>`) or
`__NULL__` afterwards. The report prints the file, line, kind and a masked preview, never the value.
"""
from __future__ import annotations

import os
import re

from . import safety
from . import sanitize as S
from .parse.formats import decode_bytes

TOKEN_KINDS = ["private key", "JWT", "AWS access key id", "API key", "API key", "GitHub token", "Slack token",
               "bearer or basic token"]
assert len(TOKEN_KINDS) == len(S.TOKEN_RES)
# Not a value: a placeholder the sanitizer wrote or leaves alone (<SECRET-01>, <user>), the same in an XML
# text node (&lt;SECRET-01&gt;), or vRO's null sentinel.
PLACEHOLDER = re.compile(r"^(?:<[^<>]*>|&lt;(?:%s)-\d{2,}&gt;|__NULL__)$" % "|".join(S.CATEGORIES))
# A vRA export's IDs (a VCF runnableId is 65 mixed-case hex characters) are not keys.
ID_VALUE = re.compile(r'"(?:%s)"\s*:\s*$' % "|".join(re.escape(k) for k in S.VRA_ID_KEYS))
NULL_VALUE = re.compile(r"^\s*(?:<!\[CDATA\[(?:__NULL__)?\]\]>|__NULL__)?\s*$")


def scan_text(text: str) -> list[tuple[str, int, str]]:
    """(kind, line, value) for every secret-shaped value in one file's text."""
    found: dict[tuple[int, str], str] = {}

    def add(kind: str, pos: int, value: str) -> None:
        v = value.strip()
        if not v or PLACEHOLDER.match(v):
            return
        key = (text.count("\n", 0, pos) + 1, v)
        found.setdefault(key, kind)

    for kind, rx in zip(TOKEN_KINDS, S.TOKEN_RES):
        for m in rx.finditer(text):
            add(kind, m.start(), m.group(m.lastindex or 0))
    for m in S.SECRET_KV_RE.finditer(text):
        add("password, token or key assignment", m.start(3), m.group(3))
    for m in S.HIGH_ENTROPY_RE.finditer(text):
        if S.looks_like_key(m.group(2)) and not ID_VALUE.search(text[max(0, m.start() - 40):m.start()]):
            add("high-entropy literal", m.start(2), m.group(2))
    if S._is_xml(text):
        for m in S.SECURE_RE.finditer(text):
            for v in S.Redactor.VALUE_RE.finditer(m.group(0)):
                if not NULL_VALUE.match(v.group(2)):
                    inner = re.sub(r"^\s*<!\[CDATA\[|\]\]>\s*$", "", v.group(2))
                    add("SecureString value", m.start() + v.start(2), inner)
    return [(kind, line, v) for (line, v), kind in sorted(found.items())]


def _scan_bytes(raw: bytes, label: str, out: list, skipped: list) -> None:
    if raw[:2] not in (b"\xfe\xff", b"\xff\xfe"):
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            skipped.append(f"{label} (binary, not read)")
            return
    text = decode_bytes(raw)
    if "\x00" in text:
        skipped.append(f"{label} (binary, not read)")
        return
    for kind, line, v in scan_text(text):
        out.append({"file": label, "line": line, "kind": kind, "preview": S._mask(v)})


def _scan_zip(src, label: str, out: list, skipped: list, depth: int = 0) -> int:
    n = 0
    with safety.open_zip(src, label=label) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            n += 1
            raw = safety.read_member(z, info.filename)
            member = f"{label}!{info.filename}"
            if raw[:2] == b"PK" and depth < safety.MAX_NESTED_ZIP_DEPTH:
                n += _scan_zip(raw, member, out, skipped, depth + 1)
            else:
                _scan_bytes(raw, member, out, skipped)
    return n


def check(path: str) -> dict:
    """Scan a file or folder. Raises FileNotFoundError, or safety.UnsafeInput for an unsafe archive."""
    path = os.path.normpath(path)
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    findings: list[dict] = []
    skipped: list[str] = []
    seen = 0
    name = os.path.basename(path)
    if os.path.isdir(path):
        for dp, dirs, files in os.walk(path):
            dirs[:] = sorted(d for d in dirs if not os.path.islink(os.path.join(dp, d)))
            for fn in sorted(files):
                p = os.path.join(dp, fn)
                rel = os.path.join(name, os.path.relpath(p, path))
                if os.path.islink(p):
                    skipped.append(f"{rel} (symlink, not followed)")
                    continue
                seen += 1
                if safety.is_zip(p):
                    seen += _scan_zip(p, rel, findings, skipped)
                else:
                    _scan_bytes(safety.read_file(p), rel, findings, skipped)
    elif safety.is_zip(path):
        seen = _scan_zip(path, name, findings, skipped)
    else:
        seen = 1
        _scan_bytes(safety.read_file(path), name, findings, skipped)
    return {"path": path, "files_seen": seen, "findings": findings, "skipped": skipped,
            "accepted": not findings}


def report(r: dict) -> str:
    verdict = "ACCEPTED" if r["accepted"] else "REJECTED"
    L = [f"check-intake: {verdict}: {r['path']}",
         f"{r['files_seen']} file(s) read, {len(r['findings'])} secret-shaped value(s) found."]
    if r["findings"]:
        L += ["", "Values are masked; none is printed in full."]
        L += [f"  {f['file']}:{f['line']}: {f['kind']}: {f['preview']}" for f in r["findings"]]
        L += ["", "Ask the sender to remove these values, or run `vro.py sanitize` on the export and send the "
              "sanitized copy (it replaces every one with a placeholder or __NULL__)."]
    if r["skipped"]:
        L += ["", "Not read (check these by hand):"] + [f"  {s}" for s in r["skipped"]]
    return "\n".join(L)
