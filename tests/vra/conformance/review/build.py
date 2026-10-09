"""
Writes the `review/` conformance estate: synthetic content for the fixes from the review of 2026-10-01.
Every name, ID and value in it is invented.

Run from the repository root: `python tests/vra/conformance/review/build.py`. The files it writes are committed;
re-run it after changing it.

The estate: three workflows started by vRA subscriptions on one deployment's life (create a DNS zone, register
each host, delete the zone), sharing a zone registry kept in a configuration element. They reach Infoblox
(a Kestra plugin) and Nuage VSD (no plugin) through one generic REST helper; the Nuage client is a pair of
generic object calls with builders on top. Settings, endpoints and the registry are configuration elements,
the request body a resource element. Two actions read vRA's event payload and one decides on its event type.
The vRA export: a cloud template with machines counted from an input on an existing network with static IPs.
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
VRO = os.path.join(HERE, "vro")
VRA = os.path.join(HERE, "vra")

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
    aid = f"a3000000-0000-4000-8000-{n:012d}"
    ps = "".join(f'<param n="{p}" t="{t}"/>' for p, t in params)
    base = os.path.join(VRO, "ScriptModule", *module.split("."), name)
    write(base + ".xml", f'<?xml version="1.0" encoding="UTF-8"?>\n<dunes-script-module name="{name}" '
          f'result-type="{result}" api-version="6.0.0" id="{aid}" version="1.0.0" allowed-operations="vef">'
          f'<description/>{ps}<script encoded="false"><![CDATA[{script.strip()}]]></script></dunes-script-module>\n')
    write(base + ".element_info.xml", INFO.format(cat=module, name=name, type="ScriptModule", id=aid))


def workflow(name: str, n: int, script: str) -> None:
    wid = f"e3000000-0000-4000-8000-{n:012d}"
    base = os.path.join(VRO, "Workflow", "Example", "Dns", name)
    write(base + ".xml", f"""<?xml version="1.0" encoding="UTF-8"?>
<workflow xmlns="http://vmware.com/vco/workflow" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://vmware.com/vco/workflow http://vmware.com/vco/workflow/Workflow-v4.xsd" root-name="item1" object-name="workflow:name=generic" id="{wid}" version="1.0.0" api-version="6.0.0" allowed-operations="vef" restartMode="1" resumeFromFailedMode="0">
  <display-name><![CDATA[{name}]]></display-name>
  <position y="50.0" x="100.0"></position>
  <input>
    <param name="inputProperties" type="Properties">
      <description><![CDATA[event payload]]></description>
    </param>
  </input>
  <workflow-item name="item0" type="end" end-mode="0">
    <position y="45.0" x="384.5"></position>
  </workflow-item>
  <workflow-item name="item1" out-name="item0" type="task">
    <display-name><![CDATA[{name}]]></display-name>
    <script encoded="false"><![CDATA[{script.strip()}]]></script>
    <in-binding>
      <bind name="inputProperties" type="Properties" export-name="inputProperties"></bind>
    </in-binding>
    <out-binding></out-binding>
    <position y="55.0" x="204.5"></position>
  </workflow-item>
  <presentation>
    <p-param name="inputProperties">
      <desc><![CDATA[inputProperties]]></desc>
    </p-param>
  </presentation>
</workflow>
""")
    write(base + ".element_info.xml", INFO.format(cat="Example.Dns", name=name, type="Workflow", id=wid))


def config(cat: str, name: str, n: int, atts: list[tuple[str, str, str | None]]) -> None:
    cid = f"c3000000-0000-4000-8000-{n:012d}"
    body = "".join(
        f'    <att name="{a}" type="{t}" read-only="false">\n'
        + (f'      <value encoded="n"><![CDATA[{v}]]></value>\n' if v is not None else "")
        + "    </att>\n" for a, t, v in atts)
    base = os.path.join(VRO, "ConfigurationElement", *cat.split("."), name)
    write(base + ".xml", f'<?xml version="1.0" encoding="UTF-8"?>\n<config-element id="{cid}" version="1.0.0">\n'
          f"  <display-name><![CDATA[{name}]]></display-name>\n  <atts>\n{body}  </atts>\n</config-element>\n")
    write(base + ".element_info.xml", INFO.format(cat=cat, name=name, type="ConfigurationElement", id=cid))


def resource(cat: str, name: str, n: int, text: str) -> None:
    rid = f"r3000000-0000-4000-8000-{n:012d}"
    base = os.path.join(VRO, "ResourceElement", *cat.split("."), name)
    write(base, text)
    write(base + ".element_info.xml", INFO.format(cat=cat, name=name, type="ResourceElement", id=rid))


def build_vro() -> None:
    write(os.path.join(VRO, "dunes-meta-inf.xml"), """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<!DOCTYPE properties SYSTEM "http://java.sun.com/dtd/properties.dtd">
<properties>
<comment></comment>
<entry key="pkg-name">com.example.dnszones</entry>
<entry key="used-plugins"></entry>
<entry key="pkg-id">e3000000000040008000000000000000</entry>
</properties>
""")
    D, U = "com.example.dns", "com.example.dnsutil"
    # the one generic REST helper: method and path come from the caller
    action(D, "callDns", 1, [("endpoint", "Properties"), ("method", "string"), ("path", "string"), ("body", "Any")], """
var base = RESTHostManager.createHost("dns");
base.url = "https://" + endpoint.get("host");
base.hostVerification = false;
var host = RESTHostManager.createTransientHostFrom(base);
var request = host.createRequest(method, path, body ? JSON.stringify(body) : "");
request.contentType = "application/json";
request.setHeader("Authorization", "Basic " + System.getModule("com.example.dns").basicToken(endpoint));
var response = request.execute();
return { statusCode: response.statusCode, body: response.contentAsString };
""")
    action(D, "basicToken", 21, [("endpoint", "Properties")], """
return CryptoEncoding.base64Encode(endpoint.get("username") + ":" + endpoint.get("password"));
""", "string")
    # Infoblox client: one action per object type
    action(D, "createZone", 2, [("endpoint", "Properties"), ("zone", "string")], """
return System.getModule("com.example.dns").callDns(endpoint, "POST", "/wapi/v2.12/zone_auth", { fqdn: zone });
""")
    action(D, "deleteZone", 3, [("endpoint", "Properties"), ("ref", "string")], """
return System.getModule("com.example.dns").callDns(endpoint, "DELETE", "/wapi/v2.12/zone_auth/" + ref);
""")
    action(D, "addHostRecord", 4, [("endpoint", "Properties"), ("name", "string"), ("ip", "string")], """
return System.getModule("com.example.dns").callDns(endpoint, "POST", "/wapi/v2.12/record:host",
    { name: name, ipv4addrs: [{ ipv4addr: ip }] });
""")
    # same normalised code as deleteZone, another Infoblox object
    action(D, "removeHostRecord", 5, [("endpoint", "Properties"), ("ref", "string")], """
return System.getModule("com.example.dns").callDns(endpoint, "DELETE", "/wapi/v2.12/record:host/" + ref);
""")
    # Nuage client: generic object calls (the object type is the caller's), and builders on top of them
    action(D, "createNuageObject", 6, [("endpoint", "Properties"), ("type", "string"), ("body", "Any")], """
var dns = System.getModule("com.example.dns");
return dns.callDns(endpoint, "POST", "/nuage/api/v6/" + type, body);
""")
    action(D, "deleteNuageObject", 7, [("endpoint", "Properties"), ("type", "string"), ("id", "string")], """
var dns = System.getModule("com.example.dns");
return dns.callDns(endpoint, "DELETE", "/nuage/api/v6/" + type + "/" + id);
""")
    action(D, "buildNuageDomain", 8, [("endpoint", "Properties"), ("name", "string"), ("template", "string")], """
var body = { name: name, templateID: template, description: "zone " + name };
return System.getModule("com.example.dns").createNuageObject(endpoint, "domains", body);
""")
    action(D, "buildNuageZone", 9, [("endpoint", "Properties"), ("domainId", "string"), ("name", "string")], """
var body = { name: name, parentID: domainId };
return System.getModule("com.example.dns").createNuageObject(endpoint, "zones", body);
""")
    # vRA's event payload
    action(U, "readRequestEvent", 10, [("inputProperties", "Properties")], """
var id = inputProperties.get("deploymentId");
if (!id) {
    throw "payload has no deploymentId";
}
var event = new Properties();
event.put("deploymentId", id);
event.put("eventType", inputProperties.get("eventType") || "");
event.put("status", inputProperties.get("status") || "");
return event;
""", "Properties")
    action(U, "readHostEvent", 11, [("inputProperties", "Properties")], """
var names = inputProperties.get("resourceNames") || [];
var addresses = inputProperties.get("addresses") || [];
var hosts = [];
for (var i = 0; i < names.length; i++) {
    var ips = addresses[i] || [];
    hosts.push({ name: names[i], ip: ips[0] });
}
return { deploymentId: inputProperties.get("deploymentId"), hosts: hosts };
""")
    action(U, "shouldDropZone", 12, [("eventType", "string"), ("status", "string")], """
var failed = status === "FAILED";
if (eventType === "DESTROY_DEPLOYMENT") {
    return true;
}
if (eventType === "CREATE_DEPLOYMENT") {
    return failed;
}
return false;
""", "boolean")
    # vRO's own state and settings
    action(U, "getSettings", 13, [], """
var category = Server.getConfigurationElementCategoryWithPath("Example/Dns/Settings");
var element = category.configurationElements[0];
var out = new Properties();
out.put("suffix", element.getAttributeWithKey("suffix").value);
out.put("ttl", element.getAttributeWithKey("ttl").value);
return out;
""", "Properties")
    action(U, "getEndpoint", 14, [("name", "string")], """
var category = Server.getConfigurationElementCategoryWithPath("Example/Dns/Endpoints");
for (var i = 0; i < category.configurationElements.length; i++) {
    var e = category.configurationElements[i];
    if (e.name === name) {
        var p = new Properties();
        p.put("host", e.getAttributeWithKey("host").value);
        p.put("username", e.getAttributeWithKey("username").value);
        p.put("password", e.getAttributeWithKey("password").value);
        return p;
    }
}
throw "no endpoint " + name;
""", "Properties")
    action(U, "zoneRegistry", 15, [], """
var category = Server.getConfigurationElementCategoryWithPath("Example/Dns/State");
return category.configurationElements[0];
""", "ConfigurationElement")
    action(U, "readZoneState", 16, [("deploymentId", "string")], """
var element = System.getModule("com.example.dnsutil").zoneRegistry();
var attribute = element.getAttributeWithKey(deploymentId);
return attribute ? JSON.parse(attribute.value) : null;
""")
    action(U, "saveZoneState", 17, [("deploymentId", "string"), ("record", "Any")], """
var element = System.getModule("com.example.dnsutil").zoneRegistry();
element.setAttributeWithKey(deploymentId, JSON.stringify(record), "string");
""", "void")
    action(U, "dropZoneState", 18, [("deploymentId", "string")], """
var element = System.getModule("com.example.dnsutil").zoneRegistry();
element.removeAttributeWithKey(deploymentId);
""", "void")
    action(U, "renderBody", 19, [("name", "string"), ("values", "Properties")], """
var category = Server.getResourceElementCategoryWithPath("Example/Dns/Templates");
var element = null;
for (var i = 0; i < category.resourceElements.length; i++) {
    if (category.resourceElements[i].name === name) {
        element = category.resourceElements[i];
    }
}
var text = element.getContentAsMimeAttachment().content;
for (var j = 0; j < values.keys.length; j++) {
    text = text.split("{{" + values.keys[j] + "}}").join(values.get(values.keys[j]));
}
return JSON.parse(text);
""")
    # plain logic, no system: converts with its callers
    action(U, "checkZoneName", 20, [("name", "string"), ("suffix", "string")], """
if (!name || name.length > 63) {
    throw "bad zone name " + name;
}
return name + "." + suffix;
""", "string")

    workflow("Dns - Create Zone", 1, """
var util = System.getModule("com.example.dnsutil");
var dns = System.getModule("com.example.dns");
var event = util.readRequestEvent(inputProperties);
if (event.get("eventType") !== "CREATE_DEPLOYMENT") {
    return;
}
var settings = util.getSettings();
var zone = util.checkZoneName(event.get("deploymentId"), settings.get("suffix"));
var ib = util.getEndpoint("Infoblox");
var nu = util.getEndpoint("Nuage");
var values = new Properties();
values.put("zone", zone);
var body = util.renderBody("zone-body.json", values);
var created = dns.createZone(ib, zone);
var domain = dns.buildNuageDomain(nu, zone, body.template);
dns.buildNuageZone(nu, domain.body, zone);
util.saveZoneState(event.get("deploymentId"), { zone: zone, ref: created.body, domain: domain.body });
""")
    workflow("Dns - Register Host", 2, """
var util = System.getModule("com.example.dnsutil");
var dns = System.getModule("com.example.dns");
var event = util.readHostEvent(inputProperties);
var state = util.readZoneState(event.deploymentId);
var ib = util.getEndpoint("Infoblox");
for (var i = 0; i < event.hosts.length; i++) {
    dns.addHostRecord(ib, event.hosts[i].name + "." + state.zone, event.hosts[i].ip);
}
""")
    workflow("Dns - Delete Zone", 3, """
var util = System.getModule("com.example.dnsutil");
var dns = System.getModule("com.example.dns");
var event = util.readRequestEvent(inputProperties);
if (!util.shouldDropZone(event.get("eventType"), event.get("status"))) {
    return;
}
var state = util.readZoneState(event.get("deploymentId"));
var ib = util.getEndpoint("Infoblox");
var nu = util.getEndpoint("Nuage");
dns.removeHostRecord(ib, state.ref);
dns.deleteZone(ib, state.ref);
dns.deleteNuageObject(nu, "domains", state.domain);
util.dropZoneState(event.get("deploymentId"));
""")

    config("Example.Dns.Settings", "Defaults", 1, [("suffix", "string", "zones.example.test"), ("ttl", "number", "300")])
    config("Example.Dns.Endpoints", "Infoblox", 2, [("host", "string", "ipam.example.test"), ("username", "string", "svc"),
                                                    ("password", "SecureString", "")])
    config("Example.Dns.Endpoints", "Nuage", 3, [("host", "string", "sdn.example.test"), ("username", "string", "svc"),
                                                 ("password", "SecureString", "")])
    config("Example.Dns.State", "Zone Registry", 4, [("_format", "string", '{"zone": "string", "ref": "string", '
                                                                            '"domain": "string"}')])
    resource("Example.Dns.Templates", "zone-body.json", 1, '{"name": "{{zone}}", "template": "tmpl-01"}\n')


def build_vra() -> None:
    write(os.path.join(VRA, "content.yaml"), """---
# Synthetic vRA export for vro2k's review tests. Every name in it is invented.
blueprint:
  - Zone Tier
subscription:
  - Dns - create zone
  - Dns - register host
  - Dns - delete zone
  - Dns - clean up failed create
catalog-item:
  - Example Dns__Zone Tier
content-source:
  - Example Dns
catalog-entitlement:
  - Example Dns
custom-resource: []
resource-action: []
""")
    write(os.path.join(VRA, "blueprints", "Zone Tier", "details.json"), json.dumps({
        "id": "b3000000-0000-4000-8000-000000000001", "name": "Zone Tier",
        "description": "Synthetic cloud template: hosts on an existing network with static IPs",
        "requestScopeOrg": False}, indent=2) + "\n")
    write(os.path.join(VRA, "blueprints", "Zone Tier", "content.yaml"), """formatVersion: 1
inputs:
  hostCount:
    type: integer
    title: Hosts
    default: 2
resources:
  host:
    type: Cloud.vSphere.Machine
    properties:
      count: ${input.hostCount}
      image: example-image
      flavor: small
      dnsEnabled: true
      networks:
        - network: ${resource.host_net.id}
          assignment: static
  host_net:
    type: Cloud.vSphere.Network
    properties:
      networkType: existing
      constraints:
        - tag: 'net:example'
""")
    write(os.path.join(VRA, "catalog-items", "Example Dns__Zone Tier.json"), json.dumps({
        "sourceId": "c3000000-0000-4000-8000-0000000000aa", "sourceName": "Example Dns", "name": "Zone Tier",
        "id": "d3000000-0000-4000-8000-000000000001", "formId": "f3000000-0000-4000-8000-000000000001",
        "description": "Synthetic catalog item"}, indent=2) + "\n")
    form = {"layout": {"pages": [{"id": "p1", "title": "General", "sections": [
        {"id": "s1", "fields": [{"id": "hostCount", "display": "integerField"}]}]}]},
        "schema": {"hostCount": {"label": "Hosts", "type": {"dataType": "integer", "isMultiple": False},
                                 "default": 2, "constraints": {"required": True}}},
        "options": {"externalValidations": []}}
    write(os.path.join(VRA, "catalog-items", "forms", "Example Dns__Zone Tier.json"), json.dumps({
        "id": "f3000000-0000-4000-8000-000000000001", "name": "Zone Tier", "form": json.dumps(form),
        "styles": None, "sourceType": "com.vmw.blueprint", "type": "requestForm", "status": "ON",
        "formFormat": "JSON"}, indent=2) + "\n")
    write(os.path.join(VRA, "content-sources", "Example Dns.json"), json.dumps({
        "id": "c3000000-0000-4000-8000-0000000000aa", "name": "Example Dns", "typeId": "com.vmw.blueprint",
        "description": "Synthetic content source"}, indent=2) + "\n")
    write(os.path.join(VRA, "entitlements", "Example Dns.yaml"), "name: Example Dns\ntype: CatalogSourceIdentifier\n"
          "sourceType: com.vmw.blueprint\nprojectName: Example Project\n")
    subs = [
        ("Dns - create zone", "deployment.request.pre", 1, True, 10, 'event.data.eventType == "CREATE_DEPLOYMENT"'),
        ("Dns - register host", "compute.provision.post", 2, True, 5, ""),
        ("Dns - delete zone", "deployment.request.post", 3, False, 0, 'event.data.eventType == "DESTROY_DEPLOYMENT"'),
        ("Dns - clean up failed create", "deployment.request.post", 3, False, 0,
         'event.data.eventType == "CREATE_DEPLOYMENT" && event.data.status == "FAILED"'),
    ]
    for i, (name, topic, wf, blocking, timeout, crit) in enumerate(subs, 1):
        write(os.path.join(VRA, "subscriptions", name + ".json"), json.dumps({
            "id": f"sub-example-3{i}", "type": "RUNNABLE", "name": name, "description": "Synthetic subscription",
            "disabled": False, "eventTopicId": topic, "runnableType": "extensibility.vro",
            "runnableId": f"e3000000-0000-4000-8000-{wf:012d}", "blocking": blocking, "timeout": timeout,
            "priority": 10, "criteria": crit, "contextual": False,
            "constraints": {"projectNames": ["Example Project"]}}, indent=2) + "\n")


if __name__ == "__main__":
    build_vro()
    build_vra()
    print("wrote", VRO, "and", VRA)
