"""
Writes the `patterns/` conformance inputs: one small tree per estate pattern the estate interview, the package
additions and the assumptions are built from. Not estates: each holds only the snippets its test needs. Every name,
ID and value in them is invented.

Run from the repository root: `python tests/vra/conformance/patterns/build.py`. The files it writes are committed;
re-run it after changing it.

- `runtimeconfig/`: a shared module (`getRuntimeConfig`) picks a configuration element at run time, by a path built
  from the region and the cluster it is given. The category it opens is not in the package. Six workflows call it,
  and their specs each ask which host they use.
- `vault/`: secrets read from HashiCorp Vault through a shared module (`readSecret`, an `X-Vault-Token` header on a
  `/v1/secret/` path). Five workflows call it; their specs ask where the credentials come from.
- `restcode/`: REST calls made in workflows' own scriptable tasks, on hosts made in code (the HTTP-REST plugin
  installed, its inventory not configured), with host verification off and a retry counter. The specs name the
  certificate check as a possible defect and ask how many retries a call gets.
- `multinode/`: a workflow calls a workflow on another vRO cluster through the multi-node plugin (a `VCO:Workflow`
  attribute and its `execute`), which no export of this cluster holds.
- `nested/`: a workflow calls a module that calls a module that calls a module that calls one the export does not
  have (`com.example.legacy/deepHelper`).
"""
from __future__ import annotations

import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))

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

META = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<!DOCTYPE properties SYSTEM "http://java.sun.com/dtd/properties.dtd">
<properties>
<comment></comment>
<entry key="pkg-name">com.example.{label}</entry>
<entry key="used-plugins"></entry>
<entry key="pkg-id">e7000000000040008000000000000000</entry>
</properties>
"""


def write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class Tree:
    def __init__(self, label: str, n: int):
        self.label, self.n = label, n
        self.vro = os.path.join(HERE, label, "vro")
        self.specs = os.path.join(HERE, label, "specs")
        shutil.rmtree(os.path.join(HERE, label), ignore_errors=True)
        write(os.path.join(self.vro, "dunes-meta-inf.xml"), META.format(label=label))

    def action(self, module: str, name: str, k: int, params: list[tuple[str, str]], script: str,
               result: str = "Any") -> None:
        aid = f"a7{self.n:06d}-0000-4000-8000-{k:012d}"
        ps = "".join(f'<param n="{p}" t="{t}"/>' for p, t in params)
        base = os.path.join(self.vro, "ScriptModule", *module.split("."), name)
        write(base + ".xml", f'<?xml version="1.0" encoding="UTF-8"?>\n<dunes-script-module name="{name}" '
              f'result-type="{result}" api-version="6.0.0" id="{aid}" version="1.0.0" allowed-operations="vef">'
              f'<description/>{ps}<script encoded="false"><![CDATA[{script.strip()}]]></script></dunes-script-module>\n')
        write(base + ".element_info.xml", INFO.format(cat=module, name=name, type="ScriptModule", id=aid))

    def workflow(self, name: str, k: int, script: str, inputs: list[tuple[str, str]], spec: dict,
                 attribs: list[tuple[str, str, str]] | None = None) -> None:
        wid = f"e7{self.n:06d}-0000-4000-8000-{k:012d}"
        ins = "".join(f'\n    <param name="{p}" type="{t}"></param>' for p, t in inputs)
        atts = "".join(f'\n  <attrib name="{a}" type="{t}" read-only="false"><value encoded="n"><![CDATA[{v}]]>'
                       f'</value></attrib>' for a, t, v in attribs or [])
        binds = "".join(f'<bind name="{p}" type="{t}" export-name="{p}"></bind>'
                        for p, t in inputs + [(a, t) for a, t, _v in attribs or []])
        base = os.path.join(self.vro, "Workflow", "Example", self.label.capitalize(), name)
        write(base + ".xml", f"""<?xml version="1.0" encoding="UTF-8"?>
<workflow xmlns="http://vmware.com/vco/workflow" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://vmware.com/vco/workflow http://vmware.com/vco/workflow/Workflow-v4.xsd" root-name="item1" object-name="workflow:name=generic" id="{wid}" version="1.0.0" api-version="6.0.0" allowed-operations="vef" restartMode="1" resumeFromFailedMode="0">
  <display-name><![CDATA[{name}]]></display-name>
  <position y="50.0" x="100.0"></position>
  <input>{ins}
  </input>
  <output>
  </output>{atts}
  <workflow-item name="item0" type="end" end-mode="0">
    <position y="45.0" x="384.5"></position>
  </workflow-item>
  <workflow-item name="item1" out-name="item0" type="task">
    <display-name><![CDATA[Run]]></display-name>
    <script encoded="false"><![CDATA[{script.strip()}]]></script>
    <in-binding>{binds}</in-binding>
    <out-binding></out-binding>
    <position y="55.0" x="204.5"></position>
  </workflow-item>
  <presentation>
  </presentation>
</workflow>
""")
        write(base + ".element_info.xml", INFO.format(cat=f"Example.{self.label.capitalize()}", name=name,
                                                      type="Workflow", id=wid))
        uid = f"{self.label}-" + name.replace(" ", "-")
        body = {"workflow_uid": uid, "purpose": f"{name}, for the {self.label} pattern fixture.", "confidence": "high",
                "open_questions": [], "suspected_defects": [], "external_systems": []}
        body.update(spec)
        write(os.path.join(self.specs, uid + ".json"), json.dumps(body, indent=2) + "\n")


def runtimeconfig() -> None:
    t = Tree("runtimeconfig", 1)
    t.action("com.example.config", "getRuntimeConfig", 1, [("region", "string"), ("cluster", "string"),
                                                          ("key", "string")], """
var category = Server.getConfigurationElementCategoryWithPath("Settings/" + region);
if (!category) {
    throw "no settings for region " + region;
}
for each (var e in category.configurationElements) {
    if (e.name === cluster) {
        return e.getAttributeWithKey(key).value;
    }
}
throw "no settings for cluster " + cluster;
""", result="string")
    for k, verb in enumerate(["Power On", "Power Off", "Snapshot", "Rename", "Tag", "Report"], 1):
        t.workflow(f"Config - {verb} VM", k, f"""
var host = System.getModule("com.example.config").getRuntimeConfig(region, cluster, "vcenterHost");
System.log("{verb} on " + host + ": " + vmName);
""", [("region", "string"), ("cluster", "string"), ("vmName", "string")],
            {"open_questions": [{"question": f"Which vCenter does {verb} VM use in each region and cluster?",
                                 "why_it_matters": "the converted flow needs the host", "theme": "endpoint identity"}]})


def vault() -> None:
    t = Tree("vault", 2)
    t.action("com.example.secrets", "readSecret", 1, [("path", "string"), ("token", "SecureString")], """
var host = RESTHostManager.createTransientHostFrom(RESTHostManager.createHost("secrets"));
var request = host.createRequest("GET", "/v1/secret/data/" + path, null);
request.setHeader("X-Vault-Token", token);
return JSON.parse(request.execute().contentAsString).data.data;
""", result="Properties")
    for k, what in enumerate(["Database", "Directory", "Backup", "Monitoring", "Storage"], 1):
        t.workflow(f"Vault - Rotate {what} Account", k, f"""
var secret = System.getModule("com.example.secrets").readSecret("{what.lower()}/service", token);
System.log("rotating " + secret.username);
""", [("token", "SecureString")],
            {"open_questions": [{"question": f"Where does the converted flow get the {what.lower()} service "
                                             "account password?",
                                 "why_it_matters": "credentials", "theme": "credentials"}]})


def restcode() -> None:
    t = Tree("restcode", 3)
    for k, what in enumerate(["Tickets", "Inventory", "Billing"], 1):
        t.workflow(f"Rest - Sync {what}", k, f"""
var prototype = RESTHostManager.createHost("{what.lower()}");
prototype.url = baseUrl;
prototype.hostVerification = false;
var host = RESTHostManager.createTransientHostFrom(prototype);
var response = null;
for (var attempt = 0; attempt < 3; attempt++) {{
    response = host.createRequest("GET", "/api/{what.lower()}", null).execute();
    if (response.statusCode == 200) {{
        break;
    }}
}}
System.log(response.contentAsString);
""", [("baseUrl", "string")],
            {"suspected_defects": [{"what": "TLS certificate verification is off (hostVerification = false)",
                                    "evidence": "prototype.hostVerification = false",
                                    "question": "Should the converted flow check the certificate?"}],
             "open_questions": [{"question": f"How many retries should the {what.lower()} call get, and how long "
                                             "between attempts?",
                                 "why_it_matters": "error handling", "theme": "error semantics"}]})


def multinode() -> None:
    t = Tree("multinode", 4)
    t.workflow("Remote - Rebuild Cache On Cluster B", 1, """
var props = new Properties();
props.put("cacheName", cacheName);
var token = remoteWorkflow.execute(props);
System.log("remote run " + token.id);
""", [("cacheName", "string")], {},
        attribs=[("remoteWorkflow", "VCO:Workflow",
                  "dunes://service.dunes.ch/CustomSDKObject?id='cluster-b,0f0e0d0c-0b0a-4908-8706-050403020100'"
                  "&dunesName='VCO:Workflow'")])
    t.workflow("Remote - Local Step", 2, """
System.log("local only: " + cacheName);
""", [("cacheName", "string")], {})


def nested() -> None:
    t = Tree("nested", 5)
    t.action("com.example.layer1", "outer", 1, [("x", "string")], """
return System.getModule("com.example.layer2").middle(x);
""", result="string")
    t.action("com.example.layer2", "middle", 2, [("x", "string")], """
return System.getModule("com.example.layer3").inner(x);
""", result="string")
    t.action("com.example.layer3", "inner", 3, [("x", "string")], """
return System.getModule("com.example.legacy").deepHelper(x);
""", result="string")
    t.workflow("Nested - Run", 1, """
System.log(System.getModule("com.example.layer1").outer(name));
""", [("name", "string")], {})


def build() -> None:
    for f in (runtimeconfig, vault, restcode, multinode, nested):
        f()


if __name__ == "__main__":
    build()
