"""
Target inventory: every external system the estate touches, and how.

Evidence, in order of strength:

1. Plugin object types in signatures and bindings (`VSPK:Session`, `Avi:VirtualService`).
2. Calls in scripts on variables whose type is known, either from a binding
   or parameter, or from `new X()` (`new VSPKUser()`, `new AviRunTimeInfo()`).
3. The Avi plugin client, whose calls carry the REST object as a string
   (`add("pool", ...)`, `setUrl("cluster/runtime")` then `callAviAPI`).
4. Nested calls to workflows that are not in the export, attributed to a
   target by the types of the parameters passed to them.
5. Actions a workflow calls: an action's operations count for its callers.

Read or write is judged from the operation's verb only. It is marked for
owner confirmation wherever it is used to order waves.

Nothing here calls a model. The cached specs are read for one thing only:
the `external_systems` list, which is shown next to the code evidence so a
reviewer can see where the two disagree.
"""
from __future__ import annotations

import re

from . import common as C

CALL_RE = re.compile(r"\b([A-Za-z_$][\w$]*)((?:\s*\.\s*[A-Za-z_$][\w$]*)*)\s*\.\s*([A-Za-z_$][\w$]*)\s*\(")
NEW_RE = re.compile(r"\b([A-Za-z_$][\w$]*)\s*=\s*new\s+([A-Za-z_$][\w$]*)\s*\(")
PARAM_RE = re.compile(r'<param\s+n="([^"]*)"\s+t="([^"]*)"')
STR_ARG = re.compile(r"""^\s*\(\s*(?:["']([^"']*)["'])""")

# Constructor or singleton name prefix -> target key.
CTOR_TARGET = [
    (re.compile(r"^VSPK"), "nuage-vsd"),
    (re.compile(r"^Avi"), "avi-controller"),
    (re.compile(r"^Vcl"), "vcloud-director"),
    (re.compile(r"^Vc"), "vcenter"),
    (re.compile(r"^SSHSession$"), "ssh-host"),
    (re.compile(r"^(RESTHost|RESTRequest|HTTPRestHost)"), "rest-host"),
    (re.compile(r"^(EmailMessage|MailClient)"), "mail"),
]
TYPE_PREFIX_TARGET: dict[str, str] = {}      # filled from plugin-map.yaml

AVI_CLIENT_VAR = re.compile(r"avi.*client|aviplugin", re.I)
AVI_METHOD_VERB = {
    "add": "POST", "addObject": "POST", "update": "PUT", "updateObject": "PUT",
    "delete": "DELETE", "deleteObject": "DELETE", "deleteObjectByName": "DELETE",
    "getObjectByName": "GET", "getObjectByUUID": "GET", "getObjectDataByName": "GET",
    "getObjectDataByUUID": "GET", "getAllData": "GET", "get": "GET",
}
AVI_SKIP = {"getCred", "getWorkflowId", "setAviVroClient", "getAviVroClient",
            "getResponseBody", "getHttpStatuseCode", "setHttpMethod", "setUrl",
            "setRequestBody", "toString"}
# Avi paths the plugin sends as POST that only read (inventory queries through the controller).
AVI_READ_POSTS = re.compile(r"^(nsxt/|vcenter/contentlibraries|.*verify/login)")

READ_HEAD = re.compile(r"^(get|find|fetch|list|read|search|lookup|query|verify|enumerate|to[A-Z]|convert|is|has)", re.I)
WRITE_HEAD = re.compile(r"^(create|add|delete|remove|update|edit|set|assign|attach|detach|reconfigure|execute|"
                        r"save|resync|commit|schedule|scale|change|import|connect|disconnect|run|post|put|write)", re.I)
PLATFORM_NOT_OPS = {"log", "debug", "warn", "error", "getModule", "sleep", "getObjectId", "getDate"}


def load_plugin_map() -> dict:
    pm = C.load_yaml(C.PLUGIN_MAP) or {}
    TYPE_PREFIX_TARGET.clear()
    for key, t in (pm.get("targets") or {}).items():
        for p in t.get("vro_type_prefixes") or []:
            TYPE_PREFIX_TARGET[p] = key
    load_rest_signatures(pm)
    return pm


def target_for_type(t: str) -> str | None:
    if not t or ":" not in t:
        return None
    return TYPE_PREFIX_TARGET.get(t.split(":", 1)[0])


def target_for_ctor(name: str) -> str | None:
    for rx, key in CTOR_TARGET:
        if rx.search(name):
            return key
    return None


def mode_of(op: str) -> str:
    """read, write, or unknown, from the verb in the operation name."""
    m = re.match(r"^(GET|POST|PUT|PATCH|DELETE) (.*)$", op)
    if m:
        if m.group(1) == "GET" or (m.group(1) == "POST" and AVI_READ_POSTS.match(m.group(2))):
            return "read"
        return "write"
    first = op.split()[0] if op.split() else ""
    if " " in op and "." not in first:
        head = first                                   # "Find VM in Me"
    else:
        head = op.split("(")[0].split(".")[-1]         # "VSPK:Group.assignUsers"
    if READ_HEAD.match(head):
        return "read"
    if WRITE_HEAD.match(head):
        return "write"
    return "unknown"


def _obj_from_var(var: str) -> str:
    v = re.sub(r"(Object|Obj)$", "", var)
    return v.lower() or var


def extract_ops(script: str, var_types: dict[str, str]) -> list[dict]:
    """Operations against targets in one script."""
    ops: list[dict] = []
    types = dict(var_types)
    for var, ctor in NEW_RE.findall(script or ""):
        types.setdefault(var, ctor)

    # `workflowRuntime.getAviVroClient().addObject(...)`: the call after the getter
    # has no receiver name, so give it one.
    script = re.sub(r"\b\w+\s*\.\s*getAviVroClient\s*\(\s*\)", "aviVroClient", script or "")
    pending_method: dict[str, str] = {}
    pending_url: dict[str, str] = {}
    for line in (script or "").splitlines():
        s = line.strip()
        if s.startswith("//"):
            continue
        for m in re.finditer(r'(\w+)\.setHttpMethod\(\s*["\'](\w+)["\']', s):
            pending_method[m.group(1)] = m.group(2).upper()
        for m in re.finditer(r'(\w+)\.setUrl\(\s*["\']([^"\']+)["\']\s*(\+?)', s):
            pending_url[m.group(1)] = m.group(2) + ("<id>" if m.group(3) else "")
        for m in CALL_RE.finditer(s):
            var, path, meth = m.group(1), re.sub(r"\s+", "", m.group(2) or ""), m.group(3)
            rest = s[m.end() - 1:]
            arg = STR_ARG.match(rest)
            arg0 = arg.group(1) if arg else None

            # the vRO platform itself
            if var in ("System", "Server", "Workflow"):
                if meth in PLATFORM_NOT_OPS:
                    continue
                if var == "Server" and meth.startswith("find"):
                    ops.append({"target": "vro-platform", "op": f"Server.{meth}({arg0 or '?'})",
                                "kind": "inventory lookup"})
                elif var == "Server" and meth.startswith("getConfigurationElement"):
                    ops.append({"target": "vro-platform", "op": f"read configuration element {arg0 or '?'}",
                                "kind": "configuration element"})
                elif var == "Server" and meth.startswith("getResourceElement"):
                    ops.append({"target": "vro-platform", "op": f"read resource element {arg0 or '?'}",
                                "kind": "resource element"})
                elif var == "Server" and meth == "getWorkflowWithId":
                    ops.append({"target": "vro-platform", "op": "Server.getWorkflowWithId", "kind": "workflow lookup"})
                continue
            if var in ("AviVroPlugin",):
                ops.append({"target": "vro-platform", "op": f"AviVroPlugin.{meth}",
                            "kind": "plugin endpoint registration (Avi)"})
                continue
            if var == "VcPlugin":
                ops.append({"target": "vcenter", "op": f"VcPlugin.{meth}", "kind": "sdk"})
                continue
            if meth in ("execute", "schedule") and types.get(var) == "Workflow" or \
                    (var.lower().startswith("workflow") and meth in ("execute", "schedule")):
                ops.append({"target": "vro-platform", "op": f"Workflow.{meth}", "kind": "run another workflow"})
                continue

            # Avi plugin client: the object type travels as a string argument
            is_avi_client = AVI_CLIENT_VAR.search(var) or "getAviVroClient()" in (path + ".") or \
                (var == "workflowRuntime" and ".getAviVroClient" in path)
            if is_avi_client and meth not in AVI_SKIP:
                if meth == "callAviAPI":
                    info = re.search(r"\(\s*(\w+)", rest)
                    iv = info.group(1) if info else ""
                    verb = pending_method.get(iv, "?")
                    url = pending_url.get(iv, "?")
                    ops.append({"target": "avi-controller", "op": f"{verb} {url}", "kind": "rest"})
                elif meth == "callAction":
                    vm = re.search(r'\(\s*(?:["\']([^"\']+)["\']|(\w+))\s*,\s*["\'](\w+)["\']', rest)
                    path_s = (vm.group(1) or f"<{vm.group(2)}>") if vm else "?"
                    ops.append({"target": "avi-controller",
                                "op": f"{(vm.group(3).upper() if vm else '?')} {path_s}", "kind": "rest"})
                elif meth == "executeWorkflow":
                    ops.append({"target": "avi-controller", "op": "commit batched changes (executeWorkflow)",
                                "kind": "plugin batch"})
                elif meth in AVI_METHOD_VERB:
                    obj = arg0
                    if obj is None:
                        am = re.match(r"\(\s*(\w+)", rest)
                        obj = _obj_from_var(am.group(1)) if am else "?"
                    ops.append({"target": "avi-controller", "op": f"{AVI_METHOD_VERB[meth]} {obj}",
                                "kind": "rest"})
                continue

            # typed variables. Setters, and getters with no arguments, read or build a
            # local object; they are not calls to the remote system.
            if re.match(r"^set[A-Z_]|^add\w*Item$", meth) or \
                    (re.match(r"^(get|is|has)[A-Z]", meth) and re.match(r"\(\s*\)", rest)):
                continue
            t = types.get(var)
            tgt = target_for_type(t) if t and ":" in (t or "") else (target_for_ctor(t) if t else None)
            if tgt:
                assert t is not None
                op = f"{t}{path}.{meth}" if path else f"{t}.{meth}"
                ops.append({"target": tgt, "op": op, "kind": "sdk"})
        # state kept in a configuration element: setAttributeWithKey / removeAttributeWithKey
        for m in re.finditer(r"\.\s*(setAttributeWithKey|removeAttributeWithKey)\s*\(", s):
            ops.append({"target": "vro-platform", "op": f"write configuration element ({m.group(1)})",
                        "kind": "configuration element"})
        # reading an element's content: an attribute of a configuration element, a resource element's payload
        if re.search(r"\.\s*getAttributeWithKey\s*\(", s):
            ops.append({"target": "vro-platform", "op": "get configuration element attribute (getAttributeWithKey)",
                        "kind": "configuration element"})
        if re.search(r"\.\s*getContentAsMimeAttachment\s*\(", s):
            ops.append({"target": "vro-platform", "op": "get resource element content (getContentAsMimeAttachment)",
                        "kind": "resource element"})
    ops += rest_client_ops(script)
    # de-duplicate, keep order
    seen, out = set(), []
    for o in ops:
        k = (o["target"], o["op"])
        if k not in seen:
            seen.add(k)
            o["mode"] = mode_of(o["op"])
            out.append(o)
    return out


def workflow_var_types(m: dict) -> dict[str, str]:
    vt = {}
    for p in m.get("inputs", []) + m.get("outputs", []) + m.get("attributes", []):
        vt[p["name"]] = p["type"]
    for i in m.get("items", []):
        for b in i.get("in_bindings", []) + i.get("out_bindings", []):
            vt.setdefault(b["name"], b["type"])
    return vt


def action_params(xml: str) -> dict[str, str]:
    return {n: t for n, t in PARAM_RE.findall(xml or "")}


def reach_for(op: dict, pm: dict) -> dict:
    """How Kestra reaches this one operation: target reach, unless a gap matches."""
    t = (pm.get("targets") or {}).get(op["target"]) or {}
    reach = t.get("reach", "none")
    note = None
    for g in t.get("gaps") or []:
        if re.search(g["match"], op["op"]):
            reach, note = g["reach"], g.get("note")
            break
    return {"reach": reach, "confirmed": bool(t.get("confirmed")), "gap_note": note}


# ------------------------------------------------------------------ REST through a helper action
#
# Hand-written estates often reach every system through one action that builds a REST request from
# its parameters (`invokeRest(endpoint, method, path, body)`). That action says nothing about which
# system it talks to; its callers do, in the method and path they pass. So:
#
#   1. an action whose script sends an HTTP request with a method and path it does not fix itself is
#      a REST helper (`is_rest_helper`);
#   2. every call to a helper becomes an operation on the caller, `<METHOD> <path template>`, read from
#      the call's arguments (`rest_call_ops`), with a path variable or a path-building action expanded
#      one level;
#   3. the system is named from what the caller's code says: path prefixes and header names that a
#      vendor's REST API uses, listed per target in plugin-map.yaml (`rest_signatures`). A caller that
#      matches no target, or more than one, stays on the generic `rest-host`.

HTTP_VERBS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
REST_CLIENT = re.compile(r"\bRESTHostManager\b|\.createTransientHostFrom\s*\(|\bnew\s+RESTHost\b|"
                         r"\bnew\s+HTTPRestHost\b")
REST_REQUEST = re.compile(r"\.createRequest\s*\(\s*([^,()]+?)\s*,\s*([^,()]+?)\s*[,)]")
LITERAL = re.compile(r"""^\s*(["'])(.*)\1\s*$""", re.S)
STRING_LIT = re.compile(r"""(["'])((?:\\.|(?!\1)[^\\\n])*)\1""")
REST_SIGNATURES: dict[str, dict] = {}        # target -> {"paths": [re], "headers": [re]}, from plugin-map


def load_rest_signatures(pm: dict) -> None:
    REST_SIGNATURES.clear()
    for key, t in (pm.get("targets") or {}).items():
        sig = t.get("rest_signatures") or {}
        if sig:
            REST_SIGNATURES[key] = {k: [re.compile(p) for p in sig.get(k) or []] for k in ("paths", "headers")}


def _literal(expr: str) -> str | None:
    m = LITERAL.match(expr or "")
    return m.group(2) if m and m.group(1) not in m.group(2) else None


def rest_client_ops(script: str) -> list[dict]:
    """An HTTP request built in this script: one generic op, or `<METHOD> <path>` when both are literal."""
    if not script or not REST_CLIENT.search(script):
        return []
    for m in REST_REQUEST.finditer(script):
        verb, path = _literal(m.group(1)), _literal(m.group(2))
        if verb and verb.upper() in HTTP_VERBS and path is not None:
            return [{"target": "rest-host", "op": f"{verb.upper()} {path}", "kind": "rest"}]
    return [{"target": "rest-host", "op": "HTTP request (method and path from the caller)",
             "kind": "rest client", "generic": True}]


def is_rest_helper(action: dict) -> bool:
    return any(o.get("generic") for o in action.get("ops") or [])


def _split_args(text: str, masked: str, open_at: int) -> tuple[list[str], int]:
    """Top-level arguments of the call whose '(' is at open_at; the raw text of each, and the end."""
    depth, start, args = 0, open_at + 1, []
    i = open_at
    while i < len(masked):
        c = masked[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                args.append(text[start:i])
                return [a.strip() for a in args if a.strip() or len(args) > 1], i
        elif c == "," and depth == 1:
            args.append(text[start:i])
            start = i + 1
        i += 1
    return [a.strip() for a in args], len(masked)


def _split_plus(expr: str) -> list[str]:
    from kestra_migration_assistant.vra.parse.jscalls import mask
    m = mask(expr)
    out, depth, start = [], 0, 0
    for i, c in enumerate(m):
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "+" and depth == 0:
            out.append(expr[start:i])
            start = i + 1
    out.append(expr[start:])
    return [o.strip() for o in out]


def _first_assignment(script: str, name: str) -> str | None:
    m = re.search(r"(?:\bvar|\blet|\bconst)\s+" + re.escape(name) + r"\s*=\s*([^;]+)", script or "")
    return m.group(1).strip() if m else None


def path_template(expr: str, script: str, builders: dict[str, dict], depth: int = 0) -> str:
    """A readable path from a JavaScript string expression: literals kept, anything else {name}."""
    parts = []
    for op in _split_plus(expr):
        lit = _literal(op)
        if lit is not None:
            parts.append(lit)
            continue
        ident = re.fullmatch(r"[A-Za-z_$][\w$]*", op)
        if ident:
            rhs = _first_assignment(script, op) if depth < 2 else None
            parts.append(path_template(rhs, script, builders, depth + 1) if rhs else "{" + op + "}")
            continue
        op = re.sub(r"^System\s*\.\s*getModule\s*\(\s*([\"'])[^\"']*\1\s*\)", "module", op)
        strop = re.fullmatch(r"([A-Za-z_$][\w$]*)\s*\.\s*(?:toLowerCase|toUpperCase|trim|toString)\s*\(\s*\)", op)
        if strop:
            parts.append("{" + strop.group(1) + "}")
            continue
        call = re.fullmatch(r"[\w$.]+?\s*\.\s*([A-Za-z_$][\w$]*)\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)", op, re.S)
        if call and call.group(1) in builders and depth < 2:
            b = builders[call.group(1)]
            from kestra_migration_assistant.vra.parse.jscalls import mask
            inner = "(" + call.group(2) + ")"
            args, _ = _split_args(inner, mask(inner), 0)
            first = next((ln for ln in (b["script"] or "").splitlines() if ln.strip()), "")
            m = re.match(r"\s*(?:var|let|const)\s+[\w$]+\s*=\s*([^;]+);?\s*$|\s*return\s+([^;]+);?\s*$", first)
            if m:
                body = m.group(1) or m.group(2)
                for p, a in zip(list(b["params"]), args, strict=False):
                    a_val = path_template(a, script, builders, depth + 1) if a else "{" + p + "}"
                    body = re.sub(r"(?<![\w$.\"'])" + re.escape(p) + r"(?![\w$\"'])",
                                  lambda _m, v=a_val: '"' + v + '"', body)
                parts.append(path_template(body, b["script"], builders, depth + 1))
                continue
        if call:
            parts.append("{" + call.group(1) + "}")
            continue
        wrap = re.fullmatch(r"(?:String|encodeURIComponent|encodeURI)\s*\(\s*([\w$.]+).*\)", op, re.S)
        parts.append("{" + (wrap.group(1) if wrap else re.sub(r"\W+", "", op.split(".")[-1]) or "…") + "}")
    path = "".join(parts).split("?")[0]
    segs = []
    for s in path.split("/"):
        if "{" in s and not re.fullmatch(r"\{[\w$.]+\}", s):
            s = "{…}"
        segs.append(s)
    return "/".join(segs)


def call_args(script: str, name: str) -> list[list[str]]:
    """The arguments of every call `<x>.name(...)` in this script (code only, not comments or strings)."""
    if not script or name not in script:
        return []
    from kestra_migration_assistant.vra.parse.jscalls import mask
    masked = mask(script)
    return [_split_args(script, masked, m.end() - 1)[0]
            for m in re.finditer(r"\.\s*" + re.escape(name) + r"\s*\(", masked)]


BOUND_VALUE = re.compile(r"[\w.:~-]+")


def bind_args(params, args: list[str], outer: dict[str, str] | None, script: str = "") -> dict[str, str]:
    """
    A callee's parameters bound at one call site: a literal argument, a local variable assigned a literal in
    the caller's script, or the caller's own parameter where that is bound further up. Anything else stays
    unbound, and its placeholder stays in the path.
    """
    outer = outer or {}
    out = {}
    for p, a in zip(list(params or []), args, strict=False):
        a = (a or "").strip()
        v = _literal(a)
        if v is None and re.fullmatch(r"[A-Za-z_$][\w$]*", a):
            if a in outer:
                v = outer[a]
            else:
                rhs = _first_assignment(script, a)
                v = _literal(rhs) if rhs else None
        if v is not None and BOUND_VALUE.fullmatch(v):
            out[p] = v
    return out


def bind_op(op: dict, binding: dict[str, str] | None) -> str:
    """A REST operation's `<METHOD> <path>` with each `{param}` bound at the call site filled in."""
    if not binding or op.get("kind") != "rest":
        return op["op"]
    return re.sub(r"\{([A-Za-z_$][\w$]*)\}", lambda m: binding.get(m.group(1), m.group(0)), op["op"])


def rest_call_ops(script: str, helpers: dict[str, dict], builders: dict[str, dict]) -> list[dict]:
    """`<METHOD> <path>` for every call this script makes to a REST helper action."""
    if not script or not helpers:
        return []
    from kestra_migration_assistant.vra.parse.jscalls import mask, module_calls
    called = {act for _, act in module_calls(script)}
    masked = mask(script)
    ops = []
    for name in sorted(called & set(helpers)):
        h = helpers[name]
        pnames = list(h["params"])
        mi = next((i for i, p in enumerate(pnames) if re.search(r"method|verb", p, re.I)), None)
        pi = next((i for i, p in enumerate(pnames) if re.search(r"path|uri|url|resource", p, re.I)), None)
        for m in re.finditer(r"\.\s*" + re.escape(name) + r"\s*\(", masked):
            args, _ = _split_args(script, masked, m.end() - 1)
            verb = _literal(args[mi]) if mi is not None and mi < len(args) else None
            if verb is None:
                verb = next((_literal(a) for a in args if (_literal(a) or "").upper() in HTTP_VERBS), None)
            verb = (verb or "?").upper()
            if pi is not None and pi < len(args):
                path = path_template(args[pi], script, builders)
            else:
                path = "{path}"
            ops.append({"target": "rest-host", "op": f"{verb} {path}", "kind": "rest", "helper": name})
    return ops


def name_rest_target(evidence: list[str]) -> tuple[str, list[str]]:
    """The one target whose REST signatures match the evidence strings, else rest-host."""
    hits = {}
    for key, sig in REST_SIGNATURES.items():
        why = [f"path {s!r}" for s in evidence for rx in sig["paths"] if rx.search(s)] + \
              [f"header {s!r}" for s in evidence for rx in sig["headers"] if rx.search(s)]
        if why:
            hits[key] = sorted(set(why))[:3]
    if len(hits) == 1:
        (k, why), = hits.items()
        return k, why
    return "rest-host", ([f"matches {', '.join(sorted(hits))}"] if hits else [])


def string_literals(script: str) -> list[str]:
    from kestra_migration_assistant.vra.parse.jscalls import mask
    masked = mask(script or "")
    return [m.group(2) for m in STRING_LIT.finditer(script or "") if masked[m.start()] in "\"'"]
