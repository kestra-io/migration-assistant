"""
Sanitize (part of Preflight): a redacted copy of one estate's export, for the customer to review
before anything leaves their network.

    kestra-migrate vra run <export folder>     (sanitize runs first)
      -> kestra-assessment/data/0-preflight/sanitized/<input>/...   the redacted copy, same layout
      -> kestra-assessment/data/0-preflight/redaction-report.md      counts, files touched, what to check
      -> kestra-assessment/data/0-preflight/redaction-report.json

What is redacted, everywhere text can carry it (scripts, attribute values, configuration element
values, presentation defaults, element_info and dunes-meta-inf entries, input forms):
hostnames and FQDNs, IP addresses, URLs, email addresses, user and service account names, and
anything that looks like a credential, token or key. Each distinct value gets one stable placeholder
per category (`<HOST-01>`, `<IP-02>` ...), so a host named in five scripts is `<HOST-01>` in all five
and repeated references still line up.

Dropped entirely, names and types kept: every configuration element value that could name a host or hold a
secret, and every value of a SecureString attribute or parameter (replaced by vRO's null sentinel `__NULL__`).
Kept, because they can't identify anything: a configuration element value that is a number or a boolean (by its
attribute's type), or a netmask (255.x.x.x, or a prefix like /24). Every string value is still dropped. In text,
a 255.x.x.x address is a netmask, not a host's address, and is kept too.

Stripped: signature-owner, pkg-owner and pkg-signer identities (`<IDENTITY-01>`), and a package's
`signatures/` and `certificates/` entries (they name the signer, and the signatures no longer match
the redacted content anyway). The sanitized package is for analysis, not for importing into vRO.

Kept intact: the XML structure, item graphs, bindings, names, IDs, types and the code's logic, so
every later stage runs on the sanitized copy, and only on it (stages/common.py sanitized_inputs). Markup
(tag and attribute names, IDs, `out-name`, `script-module`, namespaces) is never edited; only text content,
CDATA and comments are. The export's own module and category names (`com.acme.net`, and every module a
script names in System.getModule) read like host names but are code: they are kept as written, so IDs and
calls come out as from the export itself.

Placeholders are written as `<HOST-01>` inside CDATA, comments, JSON and plain text, and as
`&lt;HOST-01&gt;` in XML text nodes, so the XML still parses and reads back as `<HOST-01>`.

A vRA export (`.vra-ng`, or a Build Tools vRA folder) is sanitized the same way, entry by entry,
plus a pass over the keys its JSON and YAML use for what the text rules can't recognise: accounts
(`ownerId`, `subscriberId`, `createdBy`, principals, `USER:` approvers) as USER, the tenant
(`orgId`) as IDENTITY, and networks (network and subnet names, constraint tags, cloud account
tags, CIDRs) as NETWORK. Values that are `${...}` expressions are code and are kept. IDs
(`id`, `runnableId`, `workflowId`, `formId` ...) are kept byte for byte, since entry points
resolve by them; so are names, event topics, conditions, input names and resource types.

The report never prints a redacted value, only its category, placeholder and a masked preview.
No mapping from placeholder to value is written anywhere.
"""
from __future__ import annotations

import os
import re
import shutil
import zipfile
from collections import Counter, defaultdict
from collections.abc import Callable

from . import outdir, safety

CATEGORIES = ["HOST", "IP", "URL", "EMAIL", "USER", "SECRET", "IDENTITY", "NETWORK"]

# Public schema and namespace hosts that appear in every vRO file. Not customer data; kept.
PUBLIC_HOSTS = {"www.w3.org", "w3.org", "java.sun.com", "vmware.com", "www.vmware.com", "www.apache.org",
                "apache.org", "schemas.xmlsoap.org", "schemas.microsoft.com", "json-schema.org"}
TLDS = ("com|net|org|io|local|lan|corp|internal|intra|intranet|int|localdomain|home|arpa|edu|gov|mil|"
        "eu|uk|de|fr|nl|us|ca|au|br|jp|ch|se|no|dk|fi|es|it|be|at|pl|pt|ie|cz|cloud")

URL_RE = re.compile(r"\b(?:https?|ftps?|ldaps?|sftp|ssh|wss?)://[^\s\"'<>`\]\[]+", re.I)
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
IPV4_RE = re.compile(r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\d.])")
IPV6_RE = re.compile(r"(?<![:\w])(?:[0-9a-fA-F]{1,4}:){4,7}[0-9a-fA-F]{1,4}(?![:\w])")
HOST_RE = re.compile(r"(?<![\w.@-])((?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+(?:" + TLDS + r"))"
                     r"(?![\w-]|\.[A-Za-z])", re.I)
NOT_HOST_FIRST = {"java", "javax", "sun", "jdk", "system", "server", "workflow", "this", "window", "document"}
# DOMAIN\user, and svc-/svc_ accounts
DOMAIN_USER_RE = re.compile(r"\b[A-Z][A-Z0-9-]{1,15}\\{1,2}[A-Za-z][\w.$-]{1,63}\b")
SVC_RE = re.compile(r"\b(?:svc|srv|sa)[-_.][A-Za-z0-9][\w.-]{1,40}\b", re.I)
# key = "value" / key: 'value' / setUsername("value") for user-ish and secret-ish keys
KV = r"""(?i)(["']?\b(?:{keys})\b["']?\s*[:=]\s*|\bset(?:{keys})\s*\(\s*)(["'])([^"'\\\n]{{2,200}})\2"""
USER_KEYS = r"user(?:name|_name|id)?|login|account|principal|svcaccount|service_account|admin_user|owner"
SECRET_KEYS = (r"pass(?:word|wd|phrase)?|pwd|secret|client_secret|token|access_token|refresh_token|api_?key|"
               r"apikey|auth(?:orization)?|private_?key|credentials?|bearer")
USER_KV_RE = re.compile(KV.format(keys=USER_KEYS))
SECRET_KV_RE = re.compile(KV.format(keys=SECRET_KEYS))
TOKEN_RES = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}\b"),               # JWT
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                                  # AWS access key id
    re.compile(r"\b(?:sk|pk|rk)[-_](?:live|test|ant|proj)[-_][A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{32,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"(?i)\b(?:Bearer|Basic)\s+([A-Za-z0-9+/_.=-]{16,})"),
]
# A long mixed-case/digit string literal is probably a key. Hex digests and UUIDs are not.
HIGH_ENTROPY_RE = re.compile(r"""(["'])([A-Za-z0-9+/_=-]{32,})\1""")
UUID_RE = re.compile(r"^[0-9a-fA-F-]{32,36}$")
# Hostname-like single labels (vc-prod-01, esx42): too risky to redact blind, so reported as unsure.
SHORT_HOST_RE = re.compile(r"""(["'])([a-z][a-z0-9]*(?:-[a-z0-9]+)*\d[a-z0-9-]*)\1""")
IDENTITY_KEYS = ("signature-owner", "pkg-owner", "pkg-signer", "owner", "created-by", "author")

SECURE_RE = re.compile(r"<(att|attrib|param)\b[^>]*\btype=\"SecureString\"[^>]*>.*?</\1>", re.S)
# Configuration element values that can't name a host or hold a secret, kept: a number or a boolean by the
# attribute's type, and a netmask whatever its type. 255.x.x.x is never a host's address.
CONFIG_ATT_RE = re.compile(r"<att\b[^>]*>.*?</att>", re.S)
KEPT_VALUES = {
    "number": re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"),
    "boolean": re.compile(r"true|false"),
}
NETMASK_RE = re.compile(r"255(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}|/(?:3[0-2]|[12]?\d)")


def looks_like_key(v: str) -> bool:
    """A HIGH_ENTROPY_RE literal that is probably a key: mixed case and digits, not a UUID, digest or word."""
    if UUID_RE.match(v) or v.isdigit() or re.fullmatch(r"[0-9a-f]+", v) or re.fullmatch(r"[A-Za-z_]+", v):
        return False
    return bool(re.search(r"[A-Z]", v) and re.search(r"[a-z]", v) and re.search(r"\d", v))


XML_TOKENS = re.compile(r"<!\[CDATA\[(.*?)\]\]>|<!--(.*?)-->|<[^>]*>", re.S)


class Redactor:
    def __init__(self, names: set[str] | frozenset[str] = frozenset()) -> None:
        self.names = {n.lower() for n in names}       # the export's module and category names: code, kept
        self.kept_names: Counter = Counter()
        self.maps: dict[str, dict[str, str]] = {c: {} for c in CATEGORIES}
        self.counts: Counter = Counter()
        self.per_file: dict[str, Counter] = defaultdict(Counter)
        self.unsure: list[dict] = []
        self.kept_public: Counter = Counter()
        self.file = ""

    # ---------------------------------------------------------------- placeholders
    def ph(self, cat: str, value: str) -> str:
        m = self.maps[cat]
        if value not in m:
            m[value] = f"<{cat}-{len(m) + 1:02d}>"
        self.counts[cat] += 1
        self.per_file[self.file][cat] += 1
        return m[value]

    def _placeholder_spans(self, s: str) -> list[tuple[int, int]]:
        return [m.span() for m in re.finditer(r"<(?:%s)-\d{2,}>" % "|".join(CATEGORIES), s)]

    # ---------------------------------------------------------------- one run of text
    def text(self, s: str) -> str:
        """Redact a run of raw text (script, CDATA, comment, JSON, plain value)."""
        if not s:
            return s
        s = TOKEN_RES[0].sub(lambda m: self.ph("SECRET", m.group(0)), s)
        for rx in TOKEN_RES[1:]:
            s = rx.sub(lambda m: self._secret_sub(m), s)
        s = SECRET_KV_RE.sub(lambda m: m.group(1) + m.group(2) + self._keep_ph(m.group(3), "SECRET") + m.group(2), s)
        s = URL_RE.sub(self._url, s)
        s = EMAIL_RE.sub(lambda m: self.ph("EMAIL", m.group(0)), s)
        s = USER_KV_RE.sub(lambda m: m.group(1) + m.group(2) + self._keep_ph(m.group(3), "USER") + m.group(2), s)
        s = DOMAIN_USER_RE.sub(lambda m: self.ph("USER", m.group(0)), s)
        s = SVC_RE.sub(lambda m: self.ph("USER", m.group(0)), s)
        s = IPV4_RE.sub(lambda m: m.group(0) if m.group(0).startswith("255.") else self.ph("IP", m.group(0)), s)
        s = IPV6_RE.sub(lambda m: self.ph("IP", m.group(0)), s)
        s = HOST_RE.sub(self._host, s)
        s = HIGH_ENTROPY_RE.sub(self._entropy, s)
        for m in SHORT_HOST_RE.finditer(s):
            v = m.group(2)
            if re.match(r"^(item|param|attr|arg|var|v|x|id|wf|step|node|case|c|t|n|p)\d+$", v):
                continue                              # vRO item names and loop counters
            self.unsure.append({"file": self.file, "category": "HOST?", "preview": v,
                                "why": "single-label name with a digit, could be a host (not redacted)"})
        return s

    def _keep_ph(self, value: str, cat: str) -> str:
        if value.startswith("<") and value.endswith(">") or value == "__NULL__":
            return value
        return self.ph(cat, value)

    def _secret_sub(self, m: re.Match) -> str:
        if m.lastindex:                                  # Bearer/Basic: keep the scheme word
            return m.group(0)[: m.start(1) - m.start(0)] + self.ph("SECRET", m.group(1))
        return self.ph("SECRET", m.group(0))

    def _url(self, m: re.Match) -> str:
        u = m.group(0).rstrip(".,;)")
        tail = m.group(0)[len(u):]
        host = re.sub(r"^[a-z]+://", "", u, flags=re.I).split("/")[0].split(":")[0].split("@")[-1].lower()
        if host in PUBLIC_HOSTS:
            self.kept_public[host] += 1
            return m.group(0)
        return self.ph("URL", u) + tail

    def _host(self, m: re.Match) -> str:
        h = m.group(1)
        first = h.split(".")[0].lower()
        if h.lower() in PUBLIC_HOSTS:
            self.kept_public[h.lower()] += 1
            return h
        if first in NOT_HOST_FIRST or re.fullmatch(r"[\d.]+", h):
            return h
        if h.lower() in self.names:                   # a module or category of the export (com.acme.net)
            self.kept_names[h] += 1
            return h
        # Code, not a host: a camelCase label (newProperty.local) or used as an lvalue or call target.
        if any(re.search(r"[a-z][A-Z]", lab) for lab in h.split(".")) or \
                re.match(r"\s*(=(?!=)|\(|\[)", m.string[m.end():m.end() + 4]):
            return h
        return self.ph("HOST", h)

    def _entropy(self, m: re.Match) -> str:
        v = m.group(2)
        if not looks_like_key(v):
            return m.group(0)
        return m.group(1) + self.ph("SECRET", v) + m.group(1)

    # ---------------------------------------------------------------- XML-aware
    def xml(self, s: str) -> str:
        """Redact an XML document's text, CDATA and comments; leave markup alone, then drop secret values."""
        s = self._identities(s)
        out, pos = [], 0
        for m in XML_TOKENS.finditer(s):
            out.append(self._xml_text(s[pos:m.start()]))
            if m.group(1) is not None:
                out.append("<![CDATA[" + self.text(m.group(1)).replace("]]>", "]]]]><![CDATA[>") + "]]>")
            elif m.group(2) is not None:
                out.append("<!--" + self.text(m.group(2)).replace("--", "- -") + "-->")
            else:
                out.append(m.group(0))
            pos = m.end()
        out.append(self._xml_text(s[pos:]))
        s = "".join(out)
        return self._drop_secure_values(s)

    def _xml_text(self, t: str) -> str:
        if not t.strip():
            return t
        r = self.text(t)
        # a placeholder in a text node must be escaped to keep the XML well formed
        return re.sub(r"<((?:%s)-\d{2,})>" % "|".join(CATEGORIES), r"&lt;\1&gt;", r)

    VALUE_RE = re.compile(r"(<value\b[^>]*>)(.*?)(</value>)", re.S)

    def _null_values(self, block: str) -> str:
        def one(m):
            inner = m.group(2)
            if re.fullmatch(r"\s*(<!\[CDATA\[__NULL__\]\]>|__NULL__)?\s*", inner):
                return m.group(0)
            self.counts["DROPPED"] += 1
            self.per_file[self.file]["DROPPED"] += 1
            return m.group(1) + "<![CDATA[__NULL__]]>" + m.group(3)
        return self.VALUE_RE.sub(one, block)

    def _drop_secure_values(self, s: str) -> str:
        if "<config-element" in s[:2000]:
            return re.sub(r"<atts>.*?</atts>", lambda m: CONFIG_ATT_RE.sub(self._config_att, m.group(0)), s,
                          flags=re.S)
        return SECURE_RE.sub(lambda m: self._null_values(m.group(0)), s)

    def _config_att(self, m: re.Match) -> str:
        """One configuration element attribute: its value kept if it can't identify anything, else dropped."""
        att = m.group(0)
        typ = re.match(r"<att\b[^>]*\btype=\"([^\"]*)\"", att)
        value = self.VALUE_RE.search(att)
        if typ and value:
            v = re.sub(r"^\s*<!\[CDATA\[(.*)\]\]>\s*$", r"\1", value.group(2), flags=re.S).strip()
            rx = KEPT_VALUES.get(typ.group(1))
            if rx and rx.fullmatch(v) or typ.group(1) != "SecureString" and NETMASK_RE.fullmatch(v):
                self.counts["KEPT"] += 1
                return att
        return self._null_values(att)

    def _identities(self, s: str) -> str:
        def one(m):
            if not m.group(2).strip() or m.group(2).startswith("&lt;"):
                return m.group(0)
            return m.group(1) + "&lt;" + self.ph("IDENTITY", m.group(2).strip())[1:-1] + "&gt;" + m.group(3)
        keys = "|".join(re.escape(k) for k in IDENTITY_KEYS)
        return re.sub(r'(<entry key="(?:%s)">)([^<]*)(</entry>)' % keys, one, s)


# ------------------------------------------------------------------ vRA export keys

VRA_USER_KEYS = ("ownerId", "owner", "createdBy", "updatedBy", "requestedBy", "subscriberId", "userName",
                 "username", "user", "referenceId", "principalId", "serviceAccount", "cloudAccount",
                 "cloudAccountName", "account", "accountName")
VRA_IDENTITY_KEYS = ("orgId", "organizationId", "tenantId", "tenant", "orgName")
VRA_NETWORK_KEYS = ("tag", "tags", "network", "networkName", "networks", "networkProfile", "subnet", "subnetName",
                    "segment", "portGroup", "cidr", "gateway", "domain", "dnsSearchDomains", "dnsServers",
                    "export-tag", "import-tags", "zone", "region", "regionName")
# Kept exactly: entry points resolve by these.
VRA_ID_KEYS = ("id", "runnableId", "workflowId", "formId", "sourceId", "blueprintId", "iconId", "projectId",
               "sourceProjectId", "eventTopicId", "typeId", "sourceType", "resourceType", "externalType")
VRA_SUFFIXES = (".json", ".yaml", ".yml")


def _vra_keyed(r: Redactor, text: str, yaml_like: bool) -> str:
    """Redact values of account, tenant and network keys in a vRA JSON or YAML file, as text."""
    groups = [(VRA_USER_KEYS, "USER"), (VRA_IDENTITY_KEYS, "IDENTITY"), (VRA_NETWORK_KEYS, "NETWORK")]

    def val(v: str, cat: str) -> str:
        t = v.strip()
        if not t or t.startswith(("${", "<")) or t in ("null", "true", "false", "[]", "{}", "~", "__NULL__") \
                or re.fullmatch(r"-?\d+(\.\d+)?", t):
            return v
        return v.replace(t, r.ph(cat, t))

    for keys, cat in groups:
        k = "|".join(re.escape(x) for x in keys)
        # JSON: "key": "value"  and  "key": ["a", "b"]
        text = re.sub(r'("(?:%s)"\s*:\s*")([^"\\]*)(")' % k, lambda m: m.group(1) + val(m.group(2), cat) + m.group(3), text)
        text = re.sub(r'("(?:%s)"\s*:\s*\[)([^\]]*)(\])' % k,
                      lambda m: m.group(1) + re.sub(r'"([^"\\]*)"', lambda n: '"' + val(n.group(1), cat) + '"',
                                                    m.group(2)) + m.group(3), text)
        if not yaml_like:
            continue
        lines, block = text.split("\n"), None
        for i, line in enumerate(lines):
            if block is not None:
                m = re.match(r"^(\s*-\s+)(['\"]?)([^'\"#\n]*?)\2(\s*(?:#.*)?)$", line)
                if m and re.match(r"^[\w.-]+\s*:(\s|$)", m.group(3)):
                    m = None                                   # a list of mappings: keys are checked below
                if m and len(m.group(1)) - len(m.group(1).lstrip()) > block:
                    lines[i] = m.group(1) + m.group(2) + val(m.group(3), cat) + m.group(2) + m.group(4)
                    continue
                if line.strip():
                    block = None
            m = re.match(r"^(\s*(?:-\s+)?(?:%s)\s*:)(\s*)(.*)$" % k, line)
            if not m:
                continue
            head, sp, rest = m.groups()
            if not rest.strip() or rest.strip().startswith("#"):
                block = len(head) - len(head.lstrip())             # a block list may follow
                continue
            if rest.lstrip().startswith("["):
                rest = re.sub(r"(['\"]?)([^,\[\]'\"]+)\1", lambda n: n.group(1) + val(n.group(2), cat) + n.group(1)
                              if n.group(2).strip() else n.group(0), rest)
            else:
                mm = re.match(r"^(['\"]?)(.*?)\1(\s*(?:#.*)?)$", rest)
                if mm:
                    rest = mm.group(1) + val(mm.group(2), cat) + mm.group(1) + mm.group(3)
            lines[i] = head + sp + rest
        text = "\n".join(lines)
    # approvers and principals written as "USER:name" / "GROUP:name"
    text = re.sub(r'(["\s\[,-])((?:USER|GROUP):)([^"\s,\]]+)',
                  lambda m: m.group(1) + m.group(2) + (m.group(3) if m.group(3).startswith("<") else r.ph("USER", m.group(3))),
                  text)
    return text


def _vra_text(r: Redactor, text: str, name: str) -> str:
    """A vRA JSON or YAML file: keyed pass, then the text rules, with ID values held back from both."""
    held: list[str] = []

    def hold(m):
        held.append(m.group(2))
        return m.group(1) + f"VROKEEPID{len(held) - 1:06d}" + m.group(3)
    k = "|".join(re.escape(x) for x in VRA_ID_KEYS)
    text = re.sub(r'("(?:%s)"\s*:\s*")([^"\\]+)(")' % k, hold, text)
    text = re.sub(r"(^\s*(?:-\s+)?(?:%s)\s*:\s*['\"]?)([^'\"\s#][^'\"#\n]*?)(['\"]?\s*(?:#.*)?$)" % k, hold, text, flags=re.M)
    text = _vra_keyed(r, text, name.lower().endswith((".yaml", ".yml")))
    text = r.text(text)
    return re.sub(r"VROKEEPID(\d{6})", lambda m: held[int(m.group(1))], text)


# ------------------------------------------------------------------ files

TEXT_SUFFIXES = (".xml", ".json", ".properties", ".txt", ".js", ".ts", ".yaml", ".yml", ".csv", ".md")


def _is_xml(text: str) -> bool:
    return text.lstrip("﻿ \r\n\t").startswith("<")


def _sanitize_bytes(r: Redactor, raw: bytes, name: str, vra: bool = False) -> tuple[bytes, bool]:
    """Returns (new bytes, was text). Keeps the original encoding and BOM."""
    from .parse.formats import decode_bytes
    if raw[:2] == b"\xfe\xff":
        enc, bom = "utf-16-be", b"\xfe\xff"
    elif raw[:2] == b"\xff\xfe":
        enc, bom = "utf-16-le", b"\xff\xfe"
    elif raw[:3] == b"\xef\xbb\xbf":
        enc, bom = "utf-8", b"\xef\xbb\xbf"
    else:
        enc, bom = "utf-8", b""
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw, False
    text = decode_bytes(raw)
    if "\x00" in text:
        return raw, False
    if vra and name.lower().endswith(VRA_SUFFIXES):
        new = _vra_text(r, text, name)
    else:
        new = r.xml(text) if _is_xml(text) else r.text(text)
    return bom + new.encode(enc), True


def _sanitize_tree(r: Redactor, src: str, dst: str, rel_label: str, touched: list, skipped: list,
                   vra: bool = False) -> int:
    n = 0
    for dp, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if not d.startswith(".") and not os.path.islink(os.path.join(dp, d))
                   and not outdir.skipped(dp, d)]
        for fn in sorted(files):
            if fn.startswith("."):
                continue
            p = os.path.join(dp, fn)
            rel = os.path.relpath(p, src)
            out = os.path.join(dst, rel)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            if os.path.islink(p):
                skipped.append(f"{rel_label}/{rel} (symlink, not copied)")
                continue
            n += 1
            raw = safety.read_file(p)
            r.file = f"{rel_label}/{rel}"
            if fn.lower().endswith(TEXT_SUFFIXES) or not fn.count("."):
                new, was_text = _sanitize_bytes(r, raw, fn, vra)
            else:
                new, was_text = raw, False
            if not was_text:
                skipped.append(f"{rel_label}/{rel} (binary, copied unread)")
            with open(out, "wb") as f:
                f.write(new)
            if new != raw:
                touched.append(r.file)
    return n


def _sanitize_zip(r: Redactor, src: str | bytes, dst: str | None, rel_label: str, touched: list, skipped: list,
                  depth: int = 0, vra: bool = False) -> tuple[bytes, int]:
    """Rebuild a ZIP (package, .workflow export, or a nested resource ZIP) entry by entry."""
    import io
    buf = io.BytesIO()
    n = 0
    with safety.open_zip(src if depth == 0 else src, label=rel_label) as z, \
            zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as w:
        for info in z.infolist():
            name = info.filename
            if info.is_dir():
                continue
            if name.startswith(("signatures/", "certificates/")):
                r.counts["STRIPPED_ENTRY"] += 1
                r.per_file[f"{rel_label}!{name.split('/')[0]}/"]["STRIPPED_ENTRY"] += 1
                continue
            n += 1
            raw = safety.read_member(z, name)
            r.file = f"{rel_label}!{name}"
            if raw[:2] == b"PK" and depth < safety.MAX_NESTED_ZIP_DEPTH:
                new, _ = _sanitize_zip(r, raw, None, r.file, touched, skipped, depth + 1)
            else:
                new, was_text = _sanitize_bytes(r, raw, name, vra)
                if not was_text:
                    skipped.append(f"{r.file} (binary, copied unread)")
            if new != raw:
                touched.append(r.file)
            w.writestr(zipfile.ZipInfo(name, date_time=info.date_time), new, zipfile.ZIP_DEFLATED)
    data = buf.getvalue()
    if dst:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as f:
            f.write(data)
    return data, n


GET_MODULE_RE = re.compile(r"""\bSystem\s*\.\s*getModule\s*\(\s*(["'])([\w.-]+)\1""")


def code_names(inputs: dict[str, str]) -> set[str]:
    """
    The export's own module and category names, which read like host names (com.acme.net) but are code: every
    element's category path, and every module a script names in System.getModule("..."). Read before anything is
    redacted, so the copy keeps them as written and IDs, calls and verdicts come out the same as from the export.
    An input that cannot be read adds nothing (sanitize reports it).
    """
    from .parse import formats
    names: set[str] = set()
    for path in inputs.values():
        try:
            elements, _ = formats.read_any(path)
        except Exception:
            continue
        for e in elements:
            if e.category_path:
                names.add(e.category_path)
            names.update(m.group(2) for m in GET_MODULE_RE.finditer(e.xml or ""))
    return names


def sanitize_inputs(inputs: dict[str, str], out_dir: str, vra_inputs: dict[str, str] | None = None,
                    dest: Callable[[str], str] | None = None) -> dict:
    """
    Sanitize each input (label -> path) into out_dir, keeping names, and each vRA export in
    `vra_inputs` the same way plus its keyed pass. `dest` says where an input's copy goes (the stages'
    common.copy_of); by default, out_dir/<its name>. Returns the report data.
    """
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)
    r = Redactor(code_names(inputs))
    touched: list[str] = []
    skipped: list[str] = []
    unreadable: dict[str, str] = {}
    signed: list[str] = []
    seen = 0
    vra_paths = set((vra_inputs or {}).values())
    for label, path in {**inputs, **(vra_inputs or {})}.items():
        name = os.path.basename(os.path.normpath(path))
        to = dest(path) if dest else os.path.join(out_dir, name)
        is_vra = path in vra_paths
        try:
            seen += _sanitize_input(r, path, to, name, touched, skipped, is_vra)
            if f"{name}!signatures/" in r.per_file:     # the copy drops them: say the export had them
                signed.append(label)
        except (safety.UnsafeInput, zipfile.BadZipFile, OSError) as exc:
            # no copy for it, so no stage reads it; Preflight reports it as unreadable
            unreadable[label] = f"{type(exc).__name__}: {exc}"
            if os.path.isdir(to):
                shutil.rmtree(to)
            elif os.path.exists(to):
                os.remove(to)
    # dedupe unsure by (file, preview)
    seen_u, unsure = set(), []
    for u in r.unsure:
        k = (u["file"], u["preview"])
        if k not in seen_u:
            seen_u.add(k)
            unsure.append(u)
    return {"counts": {c: r.counts.get(c, 0) for c in CATEGORIES},
            "distinct": {c: len(r.maps[c]) for c in CATEGORIES},
            "dropped_values": r.counts.get("DROPPED", 0),
            "kept_values": r.counts.get("KEPT", 0),
            "stripped_entries": r.counts.get("STRIPPED_ENTRY", 0),
            "files_seen": seen, "files_touched": len(sorted(set(touched))),
            "touched": sorted(set(touched)),
            "per_file": {f: dict(c) for f, c in sorted(r.per_file.items()) if c},
            "unsure": unsure, "skipped": skipped, "kept_public": dict(r.kept_public),
            "kept_names": dict(sorted(r.kept_names.items())), "unreadable": unreadable, "signed": signed,
            "placeholders": {c: sorted(r.maps[c].values()) for c in CATEGORIES},
            "_masked": {c: {ph: _mask(v) for v, ph in r.maps[c].items()} for c in CATEGORIES}}


def _sanitize_input(r: Redactor, path: str, to: str, name: str, touched: list, skipped: list, is_vra: bool) -> int:
    """Sanitize one input (a tree, a ZIP or one file) to `to`; returns the number of files read."""
    if os.path.isdir(path):
        return _sanitize_tree(r, path, to, name, touched, skipped, vra=is_vra)
    if safety.is_zip(path):
        return _sanitize_zip(r, path, to, name, touched, skipped, vra=is_vra)[1]
    r.file = name
    raw = safety.read_file(path)
    new, _ = _sanitize_bytes(r, raw, name)
    os.makedirs(os.path.dirname(to), exist_ok=True)
    with open(to, "wb") as f:
        f.write(new)
    if new != raw:
        touched.append(name)
    return 1


def _mask(v: str) -> str:
    v = v.strip()
    return (v[:2] + "…" if len(v) > 4 else "…") + f" ({len(v)} chars)"


# ------------------------------------------------------------------ stage

def run() -> dict:
    from .stages import common as C
    C.require_estate()
    out_dir = C.sanitized_dir()
    rep = sanitize_inputs(C.INPUTS, out_dir, C.VRA_INPUTS, dest=C.copy_of)
    rep.update({"estate": C.ESTATE, "generated": C.now(), "out_dir": out_dir, "source_dir": C.rel(C.SOURCE_DIR)})
    C.write_json(os.path.join(C.PRE, "redaction-report.json"), {k: v for k, v in rep.items() if k != "out_dir"})
    C.write_text(os.path.join(C.PRE, "redaction-report.md"), C.no_em_dashes(report_md(rep, C)))
    return rep


def report_md(r: dict, C) -> str:
    L = [f"# Redaction report ({r['estate']})", "",
         f"Generated {r['generated']} by `kestra-migrate vra run` (sanitize) from `{r['source_dir']}/`. The sanitized copy is in "
         f"`{C.rel(r['out_dir'])}/`, laid out like the original.", "",
         "**Review this before anything leaves your network.** Open a few files from the copy and check that "
         "nothing identifying is left, especially the items under \"Not sure\" below, which were deliberately "
         "left in place. No value that was redacted appears in this report, and no mapping back to the original "
         "values is written anywhere.", "",
         "## What was changed", "",
         C.md_table(["Category", "Occurrences replaced", "Distinct values", "Placeholders"],
                    [[c, r["counts"][c], r["distinct"][c],
                      ", ".join(r["placeholders"][c][:6]) + (" ..." if len(r["placeholders"][c]) > 6 else "")]
                     for c in CATEGORIES]), "",
         f"- Configuration element and SecureString values dropped (set to `__NULL__`): **{r['dropped_values']}**",
         "- Configuration element values kept because they can't identify anything (numbers, booleans, netmasks): "
         f"**{r.get('kept_values', 0)}**",
         f"- Package `signatures/` and `certificates/` entries removed: **{r['stripped_entries']}**. A sanitized "
         "package is for analysis only; it will not import into vRO.",
         f"- Files read: {r['files_seen']}; files changed: {r['files_touched']}.", "",
         "NETWORK covers what a vRA export names networks by: network and subnet names, constraint and cloud "
         "account tags, CIDRs. USER also covers vRA owners, subscribers and principals; IDENTITY the tenant (orgId).", "",
         "Public namespace and schema hosts were kept, because every vRO file has them and they identify no one: "
         + (", ".join(f"{h} ({n})" for h, n in sorted(r["kept_public"].items())) or "none") + ".", "",
         "The export's own module and category names were kept as written, though they read like host names, "
         "because they are code: the workflows' IDs and calls are built from them. "
         + ("Kept: " + ", ".join(f"`{n}` ({k})" for n, k in r.get("kept_names", {}).items()) + "."
            if r.get("kept_names") else "None of them read like a host name in this export."), "",
         "What stays the same: XML structure, item graphs, bindings, workflow, action, module, element and item "
         "names, IDs, types, and the scripts' logic. Markup is never edited. Every stage after this one "
         "(Preflight, Assess, Understand, Brief and the pages) reads this copy, never the export, so this copy "
         "is all the model is sent.", "",
         "## Placeholders and what they replaced (masked)", ""]
    rows = [[ph, c, mv] for c in CATEGORIES for ph, mv in sorted(r["_masked"][c].items())]
    L += [C.md_table(["Placeholder", "Category", "Original (masked)"], rows) if rows else "None.", ""]
    L += ["## Not sure (left in place)", "",
          "Single-label names with a digit (for example `vc-prod-01`) may be hostnames, and may equally be object "
          "names. They were not redacted. If any is a host, redact it by hand or add it to the export's "
          "owner notes before sharing.", ""]
    if r["unsure"]:
        L += [C.md_table(["File", "Value", "Why"], [[u["file"], f"`{u['preview']}`", u["why"]] for u in r["unsure"]]), ""]
    else:
        L += ["Nothing.", ""]
    if r.get("unreadable"):
        L += ["## Not readable", "", "These inputs could not be opened safely. They have no sanitized copy, so no "
              "stage reads them; Preflight lists them.", ""] + [f"- `{k}`: {v}" for k, v in r["unreadable"].items()] + [""]
    if r["skipped"]:
        L += ["## Not inspected", "", "Binary files and symlinks are copied unread (or not copied). Check them by "
              "hand if they could hold anything identifying.", ""] + [f"- `{s}`" for s in r["skipped"]] + [""]
    L += ["## Files touched", "", C.md_table(["File", "Changes"],
          [[f, ", ".join(f"{k} {v}" for k, v in sorted(c.items()))] for f, c in r["per_file"].items()]), ""]
    return "\n".join(L)
