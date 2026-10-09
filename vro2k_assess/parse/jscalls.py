"""
Action calls in a vRO script, direct or through a module variable.

vRO scripts reach an action in two ways:

    System.getModule("com.acme.net").reservePort(a, b);        // direct
    var net = System.getModule("com.acme.net");                // through a variable
    net.reservePort(a, b);

The second form is the common one in hand-written code. `module_calls` finds both. It reads the
script as text, in order: a variable is bound to a module when it is assigned `System.getModule("m")`
(with `var`, `let`, `const` or a plain assignment; `x = System.getModule("m").f()` binds x to what
f returns, not to the module), copied when it is assigned another bound variable,
and unbound when it is assigned anything else. A call `x.name(` counts only while `x` is bound.
Comments and string literals are blanked first, so text inside them is never read as code.

It is a text reading, not a JavaScript parser: it does not know about function scopes, so a parameter
that shadows a module variable in an inner function is still read as the module. That errs towards
finding a call, which the call graph then checks against the actions in the export.
"""
from __future__ import annotations

import re

_GET_MODULE = re.compile(r'System\s*\.\s*getModule\s*\(\s*(["\'])([^"\'\n]+)\1\s*\)')
_IDENT = r'[A-Za-z_$][\w$]*'
# `x = ...` but not `==`, `=>`, `!=`, `<=`, `>=`, nor a property `a.x = ...`
_ASSIGN = re.compile(r'(?<![\w$.])(' + _IDENT + r')\s*=(?![=>])')
_CALL = re.compile(r'(?<![\w$.])(' + _IDENT + r')\s*\.\s*(' + _IDENT + r')\s*\(')
_ALIAS_RHS = re.compile(r'\s*(' + _IDENT + r')\s*(?:;|\n|$)')


def mask(src: str) -> str:
    """The script with comments and string contents replaced by spaces, same length and line breaks."""
    out = list(src)
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
        elif c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
        elif c in "\"'`":
            j = i + 1
            while j < n and src[j] != c:
                if src[j] == "\\":
                    j += 1
                elif src[j] == "\n" and c != "`":
                    break
                j += 1
            for k in range(i + 1, min(j, n)):
                if out[k] != "\n":
                    out[k] = " "
            i = j + 1
        else:
            i += 1
    return "".join(out)


def module_calls(src: str | None) -> list[tuple[str, str]]:
    """Every (module, action) the script calls, in order of first call, each once."""
    if not src or "getModule" not in src:
        return []
    masked = mask(src)
    # getModule(...) spans that are code (not inside a comment or a string)
    modules = {}                                  # start of `System` -> (end, module)
    for m in _GET_MODULE.finditer(src):
        if masked[m.start()] == "S":
            modules[m.start()] = (m.end(), m.group(2).strip())

    events = []                                   # (pos, kind, payload)
    for m in _ASSIGN.finditer(masked):
        rhs_at = m.end()
        rest = masked[rhs_at:]
        stripped = len(rest) - len(rest.lstrip())
        gm = modules.get(rhs_at + stripped)
        if gm and masked[gm[0]:].lstrip().startswith("."):
            # `x = System.getModule("m").f()`: x holds what f returns, not the module
            events.append((m.start(), "unbind", (m.group(1), None)))
            continue
        if gm:
            events.append((m.start(), "bind", (m.group(1), gm[1])))
            continue
        al = _ALIAS_RHS.match(rest)
        if al and al.group(1) not in ("null", "undefined", "true", "false", "this"):
            events.append((m.start(), "alias", (m.group(1), al.group(1))))
        else:
            events.append((m.start(), "unbind", (m.group(1), None)))
    for m in _CALL.finditer(masked):
        events.append((m.start(), "call", (m.group(1), m.group(2))))
    # direct calls: System.getModule("m").f(
    for start, (end, mod) in modules.items():
        d = re.match(r'\s*\.\s*(' + _IDENT + r')\s*\(', masked[end:])
        if d:
            events.append((start, "direct", (mod, d.group(1))))

    bound: dict[str, str] = {}
    out: list[tuple[str, str]] = []
    for _, kind, (a, b) in sorted(events, key=lambda e: e[0]):
        if kind == "bind":
            bound[a] = b
        elif kind == "alias":
            if b in bound:
                bound[a] = bound[b]
            else:
                bound.pop(a, None)
        elif kind == "unbind":
            bound.pop(a, None)
        else:
            mod, act = (bound.get(a), b) if kind == "call" else (a, b)
            if mod and (mod, act) not in out:
                out.append((mod, act))
    return out


_ELEMENT_CAT = re.compile(r'\bget(Configuration|Resource)ElementCategoryWithPath\s*\(\s*([^)]*?)\s*\)')
_STR = re.compile(r'^(["\'])([^"\'\n]*)\1$')


def element_paths(src: str | None) -> list[tuple[str, str]]:
    """(kind, category path) for every configuration or resource element category this script opens with
    `Server.get{Configuration,Resource}ElementCategoryWithPath(...)`: a string literal, or a variable this
    script assigns one literal to. Paths are dotted, as the parse keeps category paths."""
    src = src or ""
    out = []
    for m in _ELEMENT_CAT.finditer(src):
        kind = "ConfigurationElement" if m.group(1) == "Configuration" else "ResourceElement"
        arg = m.group(2)
        lit = _STR.match(arg)
        path = lit.group(2) if lit else None
        if path is None and re.fullmatch(_IDENT, arg):
            a = re.search(r'(?<![\w$.])' + re.escape(arg) + r'\s*=\s*(["\'])([^"\'\n]*)\1', src)
            path = a.group(2) if a else None
        if path:
            k = (kind, path.replace("/", ".").strip("."))
            if k not in out:
                out.append(k)
    return out
