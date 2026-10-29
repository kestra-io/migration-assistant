"""
Writes the `detection/` conformance input: the systems that 1.0.0 did not name, each called the way vRO code calls
it, so Assess must name them from the sanitized copy. Every name, ID and value in it is invented, and the webhook
URLs are markers (`@WEBHOOK:<name>@`) that tests/vra/webhooks.py fills in at run time, so no file holds one whole.

Run from the repository root: `python tests/vra/conformance/detection/build.py`. The files it writes are committed;
re-run it after changing it.

- Ansible Tower and Terraform Enterprise, through a generic REST helper action (`callRest`): the paths their callers
  pass (`/api/v2/job_templates/.../launch/`, `/api/v2/runs`) name them.
- Teams and Slack incoming webhooks, as a script builds them: a transient REST host on a URL read from a
  configuration element (`getAttributeWithKey("teamsWebhookUrl")`), then `POST ""`. The copy has no URL left, so
  the host Sanitize saw names them; the Teams one also has a MessageCard body.
- Active Directory through the vRO AD plugin: `ActiveDirectory.searchExactMatch`, an `AD:OrganizationalUnit`
  parameter's `createUserGroup`, an `AD:UserGroup`'s `setAttribute` (a remote write) and its
  `distinguishedName.indexOf` (local string work, not an operation).

One workflow (category Example/Detect) calls each client action. The specs are in specs/, written here too.
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
VRO = os.path.join(HERE, "vro")
SPECS = os.path.join(HERE, "specs")
MODULE = "com.example.detect"

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


def action(name: str, n: int, params: list[tuple[str, str]], script: str, result: str = "Any") -> None:
    aid = f"a6000000-0000-4000-8000-{n:012d}"
    ps = "".join(f'<param n="{p}" t="{t}"/>' for p, t in params)
    base = os.path.join(VRO, "ScriptModule", *MODULE.split("."), name)
    write(base + ".xml", f'<?xml version="1.0" encoding="UTF-8"?>\n<dunes-script-module name="{name}" '
          f'result-type="{result}" api-version="6.0.0" id="{aid}" version="1.0.0" allowed-operations="vef">'
          f'<description/>{ps}<script encoded="false"><![CDATA[{script.strip()}]]></script></dunes-script-module>\n')
    write(base + ".element_info.xml", INFO.format(cat=MODULE, name=name, type="ScriptModule", id=aid))


def workflow(name: str, n: int, script: str, inputs: list[tuple[str, str]]) -> None:
    wid = f"e6000000-0000-4000-8000-{n:012d}"
    ins = "".join(f'\n    <param name="{p}" type="{t}"></param>' for p, t in inputs)
    binds = "".join(f'<bind name="{p}" type="{t}" export-name="{p}"></bind>' for p, t in inputs)
    base = os.path.join(VRO, "Workflow", "Example", "Detect", name)
    write(base + ".xml", f"""<?xml version="1.0" encoding="UTF-8"?>
<workflow xmlns="http://vmware.com/vco/workflow" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://vmware.com/vco/workflow http://vmware.com/vco/workflow/Workflow-v4.xsd" root-name="item1" object-name="workflow:name=generic" id="{wid}" version="1.0.0" api-version="6.0.0" allowed-operations="vef" restartMode="1" resumeFromFailedMode="0">
  <display-name><![CDATA[{name}]]></display-name>
  <position y="50.0" x="100.0"></position>
  <input>{ins}
  </input>
  <output>
  </output>
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
    write(base + ".element_info.xml", INFO.format(cat="Example.Detect", name=name, type="Workflow", id=wid))
    uid = "detection-" + name.replace(" ", "-")
    write(os.path.join(SPECS, uid + ".json"), json.dumps(
        {"workflow_uid": uid, "purpose": f"{name}, for the detection fixture.", "confidence": "high",
         "open_questions": [], "external_systems": []}, indent=2) + "\n")


def build() -> None:
    write(os.path.join(VRO, "dunes-meta-inf.xml"), """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<!DOCTYPE properties SYSTEM "http://java.sun.com/dtd/properties.dtd">
<properties>
<comment></comment>
<entry key="pkg-name">com.example.detect</entry>
<entry key="used-plugins"></entry>
<entry key="pkg-id">e6000000000040008000000000000000</entry>
</properties>
""")
    cid = "c6000000-0000-4000-8000-000000000001"
    write(os.path.join(VRO, "ConfigurationElement", "Example", "Endpoints.xml"),
          f"""<?xml version='1.0' encoding='UTF-8'?>
<config-element id="{cid}" version="0.0.1">
  <display-name><![CDATA[Endpoints]]></display-name>
  <atts>
    <att name="teamsWebhookUrl" type="string" read-only="false">
      <value encoded="n"><![CDATA[@WEBHOOK:teams-detect@]]></value>
    </att>
    <att name="slackWebhookUrl" type="string" read-only="false">
      <value encoded="n"><![CDATA[@WEBHOOK:slack-detect@]]></value>
    </att>
    <att name="towerUrl" type="string" read-only="false">
      <value encoded="n"><![CDATA[https://tower.example.net]]></value>
    </att>
  </atts>
</config-element>
""")
    write(os.path.join(VRO, "ConfigurationElement", "Example", "Endpoints.element_info.xml"),
          INFO.format(cat="Example", name="Endpoints", type="ConfigurationElement", id=cid))

    action("endpoint", 1, [("key", "string")], """
var category = Server.getConfigurationElementCategoryWithPath("Example");
var element = category.configurationElements[0];
return element.getAttributeWithKey(key).value;
""", result="string")
    action("callRest", 2, [("method", "string"), ("path", "string"), ("body", "string")], """
var prototype = RESTHostManager.createHost("detect");
prototype.url = System.getModule("com.example.detect").endpoint("towerUrl");
var host = RESTHostManager.createTransientHostFrom(prototype);
var request = host.createRequest(method, path, body);
request.contentType = "application/json";
return request.execute().contentAsString;
""", result="string")
    action("launchJobTemplate", 3, [("templateId", "string")], """
var job = JSON.parse(System.getModule("com.example.detect").callRest("POST", "/api/v2/job_templates/" + templateId + "/launch/", "{}"));
var state = JSON.parse(System.getModule("com.example.detect").callRest("GET", "/api/v2/jobs/" + job.id + "/", ""));
return state.status;
""", result="string")
    action("queueRun", 4, [("workspaceId", "string")], """
var run = { data: { type: "runs", relationships: { workspace: { data: { type: "workspaces", id: workspaceId } } } } };
var out = JSON.parse(System.getModule("com.example.detect").callRest("POST", "/api/v2/runs", JSON.stringify(run)));
return out.data.id;
""", result="string")
    action("postTeamsMessage", 5, [("title", "string"), ("text", "string")], """
var prototype = RESTHostManager.createHost("teams");
prototype.url = System.getModule("com.example.detect").endpoint("teamsWebhookUrl");
var host = RESTHostManager.createTransientHostFrom(prototype);
var card = { "@type": "MessageCard", summary: title, text: text };
var request = host.createRequest("POST", "", JSON.stringify(card));
request.contentType = "application/json";
request.execute();
""", result="void")
    action("postSlackMessage", 6, [("channel", "string"), ("text", "string")], """
var prototype = RESTHostManager.createHost("slack");
prototype.url = System.getModule("com.example.detect").endpoint("slackWebhookUrl");
var host = RESTHostManager.createTransientHostFrom(prototype);
var request = host.createRequest("POST", "", JSON.stringify({ channel: channel, text: text }));
request.contentType = "application/json";
request.execute();
""", result="void")
    action("createSecurityGroup", 7, [("ou", "AD:OrganizationalUnit"), ("group", "AD:UserGroup"),
                                       ("groupName", "string")], """
var existing = ActiveDirectory.searchExactMatch("UserGroup", groupName);
if (existing && existing.length > 0) {
    return existing[0];
}
ou.createUserGroup(groupName);
group.setAttribute("description", "created by vRO");
if (group.distinguishedName.indexOf("OU=Managed") < 0) {
    System.warn("group outside the managed OU");
}
return group;
""")

    workflow("Detect - Patch Linux", 1, """
var status = System.getModule("com.example.detect").launchJobTemplate(templateId);
if (status != "successful") {
    throw "patch run " + status;
}
""", [("templateId", "string")])
    workflow("Detect - Apply Segment", 2, """
var runId = System.getModule("com.example.detect").queueRun(workspaceId);
if (!runId) {
    throw "no run queued";
}
""", [("workspaceId", "string")])
    workflow("Detect - Notify", 3, """
if (!text) {
    throw "nothing to say";
}
System.getModule("com.example.detect").postTeamsMessage("Infra", text);
System.getModule("com.example.detect").postSlackMessage("#infra", text);
""", [("text", "string")])
    workflow("Detect - Create Group", 4, """
if (!groupName) {
    throw "no group name";
}
var g = System.getModule("com.example.detect").createSecurityGroup(ou, group, groupName);
System.log("group " + g.distinguishedName);
""", [("ou", "AD:OrganizationalUnit"), ("group", "AD:UserGroup"), ("groupName", "string")])


if __name__ == "__main__":
    build()
