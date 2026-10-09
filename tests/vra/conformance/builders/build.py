"""
Writes the `builders/` conformance estate: body builders on top of a client, and REST paths that only their
callers make concrete. Every name, ID and value in it is invented.

Run from the repository root: `python tests/vra/conformance/builders/build.py`. The files it writes are committed;
re-run it after changing it.

The estate: one generic REST helper (sendRequest). A Nuage VSD client, createVsdObject(endpoint, type, body),
sends `POST /nuage/api/v6/{type}`; an Infoblox client (addIpamRecord) sends one fixed path. On top of the VSD
client: buildVsdDomain calls only the client (type "domains"); buildVsdZone also renders a template read from
a resource element (renderTemplate, which reaches no system) and passes "zones"; buildVsdObject forwards its
own `kind` parameter, which the workflow passes as "subnets"; setupNetwork calls both systems' clients (an
orchestrator, not a builder) and passes "enterprises".
"""
from __future__ import annotations

import os

HERE = os.path.dirname(os.path.abspath(__file__))
VRO = os.path.join(HERE, "vro")

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


def write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def action(module: str, name: str, n: int, params: list[tuple[str, str]], script: str, result: str = "Any") -> None:
    aid = f"a4000000-0000-4000-8000-{n:012d}"
    ps = "".join(f'<param n="{p}" t="{t}"/>' for p, t in params)
    base = os.path.join(VRO, "ScriptModule", *module.split("."), name)
    write(base + ".xml", f'<?xml version="1.0" encoding="UTF-8"?>\n<dunes-script-module name="{name}" '
          f'result-type="{result}" api-version="6.0.0" id="{aid}" version="1.0.0" allowed-operations="vef">'
          f'<description/>{ps}<script encoded="false"><![CDATA[{script.strip()}]]></script></dunes-script-module>\n')
    write(base + ".element_info.xml", INFO.format(cat=module, name=name, type="ScriptModule", id=aid))


def workflow(name: str, n: int, script: str) -> None:
    wid = f"e4000000-0000-4000-8000-{n:012d}"
    base = os.path.join(VRO, "Workflow", "Example", "Net", name)
    write(base + ".xml", f"""<?xml version="1.0" encoding="UTF-8"?>
<workflow xmlns="http://vmware.com/vco/workflow" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://vmware.com/vco/workflow http://vmware.com/vco/workflow/Workflow-v4.xsd" root-name="item1" object-name="workflow:name=generic" id="{wid}" version="1.0.0" api-version="6.0.0" allowed-operations="vef" restartMode="1" resumeFromFailedMode="0">
  <display-name><![CDATA[{name}]]></display-name>
  <position y="50.0" x="100.0"></position>
  <input>
    <param name="siteName" type="string">
      <description><![CDATA[site]]></description>
    </param>
  </input>
  <workflow-item name="item0" type="end" end-mode="0">
    <position y="45.0" x="384.5"></position>
  </workflow-item>
  <workflow-item name="item1" out-name="item0" type="task">
    <display-name><![CDATA[{name}]]></display-name>
    <script encoded="false"><![CDATA[{script.strip()}]]></script>
    <in-binding>
      <bind name="siteName" type="string" export-name="siteName"></bind>
    </in-binding>
    <out-binding></out-binding>
    <position y="55.0" x="204.5"></position>
  </workflow-item>
  <presentation>
    <p-param name="siteName">
      <desc><![CDATA[siteName]]></desc>
    </p-param>
  </presentation>
</workflow>
""")
    write(base + ".element_info.xml", INFO.format(cat="Example.Net", name=name, type="Workflow", id=wid))


def resource(cat: str, name: str, n: int, text: str) -> None:
    rid = f"r4000000-0000-4000-8000-{n:012d}"
    base = os.path.join(VRO, "ResourceElement", *cat.split("."), name)
    write(base, text)
    write(base + ".element_info.xml", INFO.format(cat=cat, name=name, type="ResourceElement", id=rid))


def build_vro() -> None:
    write(os.path.join(VRO, "dunes-meta-inf.xml"), """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<!DOCTYPE properties SYSTEM "http://java.sun.com/dtd/properties.dtd">
<properties>
<comment></comment>
<entry key="pkg-name">com.example.netbuild</entry>
<entry key="used-plugins"></entry>
<entry key="pkg-id">e4000000000040008000000000000000</entry>
</properties>
""")
    M = "com.example.netbuild"
    action(M, "sendRequest", 1, [("endpoint", "Properties"), ("method", "string"), ("path", "string"), ("body", "Any")], """
var base = RESTHostManager.createHost("net");
base.url = "https://" + endpoint.get("host");
var host = RESTHostManager.createTransientHostFrom(base);
var request = host.createRequest(method, path, body ? JSON.stringify(body) : "");
request.contentType = "application/json";
var response = request.execute();
return { statusCode: response.statusCode, body: response.contentAsString };
""")
    # the clients
    action(M, "createVsdObject", 2, [("endpoint", "Properties"), ("type", "string"), ("body", "Any")], """
var net = System.getModule("com.example.netbuild");
return net.sendRequest(endpoint, "POST", "/nuage/api/v6/" + type, body);
""")
    action(M, "addIpamRecord", 3, [("endpoint", "Properties"), ("name", "string")], """
return System.getModule("com.example.netbuild").sendRequest(endpoint, "POST", "/wapi/v2.12/record:a", { name: name });
""")
    # a helper that reaches no system: renders a body from a resource element
    action(M, "renderTemplate", 4, [("name", "string"), ("value", "string")], """
var category = Server.getResourceElementCategoryWithPath("Example/Net/Templates");
var element = category.resourceElements[0];
var text = element.getContentAsMimeAttachment().content;
return JSON.parse(text.split("{{value}}").join(value));
""")
    # builders on top of the VSD client
    action(M, "buildVsdDomain", 5, [("endpoint", "Properties"), ("name", "string")], """
var body = { name: name, description: "domain " + name };
return System.getModule("com.example.netbuild").createVsdObject(endpoint, "domains", body);
""")
    action(M, "buildVsdZone", 6, [("endpoint", "Properties"), ("name", "string")], """
var net = System.getModule("com.example.netbuild");
var body = net.renderTemplate("zone.json", name);
return net.createVsdObject(endpoint, "zones", body);
""")
    action(M, "buildVsdObject", 7, [("endpoint", "Properties"), ("kind", "string"), ("name", "string")], """
var net = System.getModule("com.example.netbuild");
return net.createVsdObject(endpoint, kind, { name: name });
""")
    # an orchestrator over two systems' clients: not a builder
    action(M, "setupNetwork", 8, [("endpoint", "Properties"), ("name", "string")], """
var net = System.getModule("com.example.netbuild");
net.createVsdObject(endpoint, "enterprises", { name: name });
return net.addIpamRecord(endpoint, name);
""")

    workflow("Net - Build Site", 1, """
var net = System.getModule("com.example.netbuild");
var ep = new Properties();
ep.put("host", "sdn.example.test");
net.buildVsdDomain(ep, siteName);
net.buildVsdZone(ep, siteName);
net.buildVsdObject(ep, "subnets", siteName);
net.setupNetwork(ep, siteName);
""")
    resource("Example.Net.Templates", "zone.json", 1, '{"name": "{{value}}", "kind": "zone"}\n')


if __name__ == "__main__":
    build_vro()
    os.makedirs(os.path.join(HERE, "specs"), exist_ok=True)
    write(os.path.join(HERE, "specs", ".gitkeep"), "")
    print("wrote", VRO)
