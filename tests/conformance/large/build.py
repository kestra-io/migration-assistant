"""
Writes a synthetic estate of any size, for the front page's length test (tests/test_front.py). Every name, ID
and value in it is invented; nothing is committed but this file: the test builds the estate in a temporary
folder.

`build(out, n)` writes `out/vro/` (a raw vRO tree) and `out/specs/` (one spec per workflow). The estate: one
generic REST helper; for each of seven systems found by REST signature (Nuage VSD, Avi, F5 BIG-IP, vCloud
Director, vCenter, ServiceNow, Infoblox), four client actions on it; `n` workflows in six areas, each calling
two or three clients of one or two systems, some keeping state in a configuration element, a few with long
scripts. Each spec has open questions (some blocking) and some have suspected defects, as real specs do.
"""
from __future__ import annotations

import json
import os

INFO = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<!DOCTYPE properties SYSTEM "http://java.sun.com/dtd/properties.dtd">
<properties>
<comment></comment>
<entry key="categoryPath">{cat}</entry>
<entry key="name">{name}</entry>
<entry key="type">{type}</entry>
<entry key="id">{id}</entry>
</properties>
"""

SYSTEMS = [("Vsd", "/nuage/api/v6/"), ("Alb", "/api/virtualservice/"), ("Big", "/mgmt/tm/ltm/"),
           ("Vcd", "/cloudapi/1.0.0/"), ("Vc", "/api/vcenter/"), ("Snow", "/api/now/table/"),
           ("Ipam", "/wapi/v2.12/")]
AREAS = ["Network", "Balancer", "Tenant", "Compute", "Ticket", "Address"]
WORDS = ["retry", "timeout", "rollback", "paging", "quota", "ordering", "naming", "tagging", "locale", "rounding",
         "caching", "batching", "logging", "cleanup", "ownership", "versioning", "tenancy", "audit", "pagination",
         "encoding", "throttling", "parsing", "defaults", "limits", "sorting", "fallback", "renewal", "expiry"]
THEMES = ["error semantics", "data shape", "correctness", "external contract", "performance", "idempotency"]


def _w(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def build(out: str, n: int = 60) -> dict:
    vro = os.path.join(out, "vro")
    specs = os.path.join(out, "specs")
    _w(os.path.join(vro, "dunes-meta-inf.xml"), INFO.replace("{cat}", "").replace("{name}", "com.example.large")
       .replace("{type}", "package").replace("{id}", "f4000000000040008000000000000000"))
    k = 0

    def action(module, name, params, script):
        nonlocal k
        k += 1
        aid = f"a4000000-0000-4000-8000-{k:012d}"
        ps = "".join(f'<param n="{p}" t="string"/>' for p in params)
        base = os.path.join(vro, "ScriptModule", *module.split("."), name)
        _w(base + ".xml", f'<?xml version="1.0" encoding="UTF-8"?>\n<dunes-script-module name="{name}" result-type="Any" '
           f'api-version="6.0.0" id="{aid}" version="1.0.0" allowed-operations="vef"><description/>{ps}'
           f'<script encoded="false"><![CDATA[{script.strip()}]]></script></dunes-script-module>\n')
        _w(base + ".element_info.xml", INFO.format(cat=module, name=name, type="ScriptModule", id=aid))

    M = "com.example.large"
    action(M, "callApi", ["method", "path", "body"], """
var host = RESTHostManager.createTransientHostFrom(RESTHostManager.createHost("api"));
var request = host.createRequest(method, path, body ? JSON.stringify(body) : "");
var response = request.execute();
return { statusCode: response.statusCode, body: response.contentAsString };
""")
    clients = {}
    for s, prefix in SYSTEMS:
        clients[s] = []
        for i, verb in enumerate(("POST", "GET", "PATCH", "DELETE")):
            name = f"{verb.lower()}{s}Object{i}"
            action(M, name, ["id"], f'return System.getModule("{M}").callApi("{verb}", "{prefix}object{i}/" + id, null);')
            clients[s].append(name)
    # state kept between runs, read and written by some workflows
    cid = "c4000000-0000-4000-8000-000000000001"
    base = os.path.join(vro, "ConfigurationElement", "Example", "Large", "State", "Registry")
    _w(base + ".xml", f'<?xml version="1.0" encoding="UTF-8"?>\n<config-element id="{cid}" version="1.0.0">\n'
       '  <display-name><![CDATA[Registry]]></display-name>\n  <atts>\n'
       '    <att name="records" type="string" read-only="false"></att>\n  </atts>\n</config-element>\n')
    _w(base + ".element_info.xml", INFO.format(cat="Example.Large.State", name="Registry", type="ConfigurationElement", id=cid))

    names = []
    for i in range(n):
        area = AREAS[i % len(AREAS)]
        s1, s2 = SYSTEMS[i % len(SYSTEMS)][0], SYSTEMS[(i * 3 + 1) % len(SYSTEMS)][0]
        calls = [clients[s1][i % 4], clients[s1][(i + 1) % 4]] + ([clients[s2][i % 4]] if i % 3 == 0 else [])
        body = "\n".join(f'var r{j} = System.getModule("{M}").{c}("x{i}-{j}");\nif (r{j}.statusCode >= 400) {{ throw "failed {c}"; }}'
                         for j, c in enumerate(calls))
        if i % 5 == 0:
            body += ('\nvar cat = Server.getConfigurationElementCategoryWithPath("Example/Large/State");\n'
                     'var el = cat.configurationElements[0];\nel.setAttributeWithKey("records", "x' + str(i) + '");')
        if i % 7 == 0:
            body += "\n" + "\n".join(f"var step{j} = r0.body + \"{j}\";" for j in range(40))
        name = f"{area} - Task {i:02d}"
        names.append(name)
        wid = f"e4000000-0000-4000-8000-{i:012d}"
        wb = os.path.join(vro, "Workflow", "Example", "Large", area, name)
        _w(wb + ".xml", f"""<?xml version="1.0" encoding="UTF-8"?>
<workflow xmlns="http://vmware.com/vco/workflow" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" root-name="item1" object-name="workflow:name=generic" id="{wid}" version="1.0.0" api-version="6.0.0" allowed-operations="vef" restartMode="1" resumeFromFailedMode="0">
  <display-name><![CDATA[{name}]]></display-name>
  <position y="50.0" x="100.0"></position>
  <input><param name="id" type="string"></param></input>
  <workflow-item name="item0" type="end" end-mode="0"><position y="45.0" x="384.5"></position></workflow-item>
  <workflow-item name="item1" out-name="item0" type="task">
    <display-name><![CDATA[{name}]]></display-name>
    <script encoded="false"><![CDATA[{body}]]></script>
    <in-binding><bind name="id" type="string" export-name="id"></bind></in-binding>
    <out-binding></out-binding>
    <position y="55.0" x="204.5"></position>
  </workflow-item>
  <presentation><p-param name="id"><desc><![CDATA[id]]></desc></p-param></presentation>
</workflow>
""")
        _w(wb + ".element_info.xml", INFO.format(cat=f"Example.Large.{area}", name=name, type="Workflow", id=wid))
    return {"vro": vro, "specs": specs, "names": names}


def write_specs(specs_dir: str, uids: dict[str, str]) -> None:
    """One spec per workflow uid (uid -> name): three open questions each, a blocking one for every fourth,
    a suspected defect for every third, confidence medium for every fifth."""
    os.makedirs(specs_dir, exist_ok=True)
    for i, (u, name) in enumerate(sorted(uids.items())):
        qs = [{"question": f"{WORDS[(i * 3 + j) % len(WORDS)]} {WORDS[(i * 7 + j * 5 + 1) % len(WORDS)]} "
                           f"{WORDS[(i * 11 + j * 3 + 2) % len(WORDS)]} in step {j} of {name} ({i}.{j})?",
               "why_it_matters": "the converter builds one behaviour",
               "theme": THEMES[(i + j) % 6] if (i % 4 == 0 or THEMES[(i + j) % 6] not in
                                                ("correctness", "external contract")) else "data shape"}
              for j in range(3)]
        spec = {"purpose": f"{name}: calls the systems it is wired to and records the result.", "inputs": [],
                "outputs": [], "external_systems": [], "decisions": [], "side_effects": [], "assumptions": [],
                "open_questions": qs,
                "suspected_defects": ([{"what": f"{name} logs success before checking the status code",
                                        "evidence": "the log line comes before the check",
                                        "question": "Should the log follow the check?"}] if i % 3 == 0 else []),
                "state_records": [], "confidence": "medium" if i % 5 == 0 else "high",
                "confidence_reason": "synthetic spec for the front page length test"}
        with open(os.path.join(specs_dir, f"{u}.json"), "w") as f:
            json.dump(spec, f, indent=1)
