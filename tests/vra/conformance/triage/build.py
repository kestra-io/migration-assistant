"""
Writes the `triage/` conformance input: one workflow per triage rule that no other conformance input reaches,
each with the smallest XML that exercises it. Every name, ID and value in it is invented.

Run from the repository root: `python tests/vra/conformance/triage/build.py`. The files it writes are committed;
re-run it after changing it.

The workflows (category Example/Lab): a wrapper that is one link to another workflow (Lab - Create Tenant); a
dispatcher named "Process ... Notification" that switches on an action type to one workflow per value; a
workflow whose only job is a vRO configuration element (Lab - Forget Controller); one that calls an action with
VRA in its name and whose spec names vRA as the consumer (Lab - Create Pool); OGNL form logic calling an action
(Lab - Change Password); a low-confidence spec (Lab - Create Admin); an SSH command with SecureString inputs, a
Path input and attribute, a `condition` item and a root that is not the first item in the file (Lab - Run
Remote Command); and a call to an action that is not in the export (Lab - Enable Override). The specs are in
specs/, written here too.
"""
from __future__ import annotations

import json
import os
from collections.abc import Sequence

HERE = os.path.dirname(os.path.abspath(__file__))
VRO = os.path.join(HERE, "vro")
SPECS = os.path.join(HERE, "specs")
MODULE = "com.example.lab"

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


def wid(n: int) -> str:
    return f"e5000000-0000-4000-8000-{n:012d}"


def action(name: str, n: int, params: list[tuple[str, str]], script: str, result: str = "Any") -> None:
    aid = f"a5000000-0000-4000-8000-{n:012d}"
    ps = "".join(f'<param n="{p}" t="{t}"/>' for p, t in params)
    base = os.path.join(VRO, "ScriptModule", *MODULE.split("."), name)
    write(base + ".xml", f'<?xml version="1.0" encoding="UTF-8"?>\n<dunes-script-module name="{name}" '
          f'result-type="{result}" api-version="6.0.0" id="{aid}" version="1.0.0" allowed-operations="vef">'
          f'<description/>{ps}<script encoded="false"><![CDATA[{script.strip()}]]></script></dunes-script-module>\n')
    write(base + ".element_info.xml", INFO.format(cat=MODULE, name=name, type="ScriptModule", id=aid))


def params(ps: Sequence[tuple[str, str]]) -> str:
    return "".join(f'\n    <param name="{p}" type="{t}"></param>' for p, t in ps)


Params = Sequence[tuple[str, str]]


def task(name: str, out: str, script: str, binds: Params = (), outs: Params = ()) -> str:
    ib = "".join(f'<bind name="{p}" type="{t}" export-name="{p}"></bind>' for p, t in binds)
    ob = "".join(f'<bind name="{p}" type="{t}" export-name="{p}"></bind>' for p, t in outs)
    return f"""
  <workflow-item name="{name}" out-name="{out}" type="task">
    <display-name><![CDATA[{name}]]></display-name>
    <script encoded="false"><![CDATA[{script.strip()}]]></script>
    <in-binding>{ib}</in-binding>
    <out-binding>{ob}</out-binding>
    <position y="55.0" x="204.5"></position>
  </workflow-item>"""


def end(name: str = "item0") -> str:
    return f"""
  <workflow-item name="{name}" type="end" end-mode="0">
    <position y="45.0" x="384.5"></position>
  </workflow-item>"""


def link(name: str, out: str, callee: int, display: str) -> str:
    return f"""
  <workflow-item name="{name}" out-name="{out}" type="link" linked-workflow-id="{wid(callee)}">
    <display-name><![CDATA[{display}]]></display-name>
    <in-binding><bind name="tenantName" type="string" export-name="tenantName"></bind></in-binding>
    <out-binding></out-binding>
    <position y="55.0" x="204.5"></position>
  </workflow-item>"""


def workflow(name: str, n: int, items: str, *, root: str = "item1", inputs: Params = (), outputs: Params = (),
             attribs: Params = (), presentation: str = "") -> None:
    at = "".join(f'\n  <attrib name="{p}" type="{t}" read-only="false"></attrib>' for p, t in attribs)
    base = os.path.join(VRO, "Workflow", "Example", "Lab", name)
    write(base + ".xml", f"""<?xml version="1.0" encoding="UTF-8"?>
<workflow xmlns="http://vmware.com/vco/workflow" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://vmware.com/vco/workflow http://vmware.com/vco/workflow/Workflow-v4.xsd" root-name="{root}" object-name="workflow:name=generic" id="{wid(n)}" version="1.0.0" api-version="6.0.0" allowed-operations="vef" restartMode="1" resumeFromFailedMode="0">
  <display-name><![CDATA[{name}]]></display-name>
  <position y="50.0" x="100.0"></position>
  <input>{params(inputs)}
  </input>
  <output>{params(outputs)}
  </output>{at}{items}
  <presentation>{presentation}
  </presentation>
</workflow>
""")
    write(base + ".element_info.xml", INFO.format(cat="Example.Lab", name=name, type="Workflow", id=wid(n)))


def spec(name: str, purpose: str, confidence: str) -> None:
    uid = "triage-" + name.replace(" ", "-")
    write(os.path.join(SPECS, uid + ".json"), json.dumps(
        {"workflow_uid": uid, "purpose": purpose, "confidence": confidence, "open_questions": [],
         "external_systems": []}, indent=2) + "\n")


TENANT = [("tenantName", "string")]


def build() -> None:
    write(os.path.join(VRO, "dunes-meta-inf.xml"), """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<!DOCTYPE properties SYSTEM "http://java.sun.com/dtd/properties.dtd">
<properties>
<comment></comment>
<entry key="pkg-name">com.example.lab</entry>
<entry key="used-plugins"></entry>
<entry key="pkg-id">e5000000000040008000000000000000</entry>
</properties>
""")
    action("createVRAPoolObj", 1, [("poolName", "string"), ("members", "Array/string")], """
var pool = { name: poolName, members: [] };
for (var i = 0; i < members.length; i++) {
    pool.members.push({ ip: members[i], port: 80 });
}
return pool;
""")
    action("listControllers", 2, [], """
return ["lab-ctl-a", "lab-ctl-b"];
""", result="Array/string")

    # two callees for the wrapper and the dispatcher; no spec, so Manual, which no test reads
    workflow("Lab - Find or Create Tenant", 1, end() + task("item1", "item0", """
var tenant = { name: tenantName, created: false };
if (!tenant.name) {
    throw "no tenant name";
}
tenant.created = true;
System.log("tenant " + tenant.name);
""", TENANT), inputs=TENANT)
    workflow("Lab - Delete Tenant", 2, end() + task("item1", "item0", """
if (!tenantName) {
    throw "no tenant name";
}
System.log("deleting tenant " + tenantName);
""", TENANT), inputs=TENANT)

    # Wrapper: one link to a workflow in the export, then the end, no script
    workflow("Lab - Create Tenant", 3, end() + link("item1", "item0", 1, "Lab - Find or Create Tenant"), inputs=TENANT)
    spec("Lab - Create Tenant", "Creates a tenant: a one-line delegating call to Lab - Find or Create Tenant.", "high")

    # Rebuild: a dispatcher by name, a switch on the action type to one workflow per value
    workflow("Process Tenant Notification", 4, end() + """
  <workflow-item name="item1" type="switch">
    <display-name><![CDATA[byActionType]]></display-name>
    <script encoded="false"><![CDATA[// Generated by the system, cannot be edited
if (actionType === "create") {
  return "item2";
} else if (actionType === "delete") {
  return "item3";
}]]></script>
    <in-binding><bind name="actionType" type="string" export-name="actionType"></bind></in-binding>
    <condition name="actionType" type="string" comparator="0" label="item2">create</condition>
    <condition name="actionType" type="string" comparator="0" label="item3">delete</condition>
    <position y="55.0" x="104.5"></position>
  </workflow-item>""" + link("item2", "item0", 3, "Lab - Create Tenant")
             + link("item3", "item0", 2, "Lab - Delete Tenant"),
             inputs=[("actionType", "string"), ("tenantName", "string")])
    spec("Process Tenant Notification", "Handles a tenant notification from the lab portal: runs Lab - Create "
         "Tenant or Lab - Delete Tenant depending on the action type.", "high")

    # Rebuild: its only operation writes vRO's own state (a configuration element)
    workflow("Lab - Forget Controller", 5, end() + task("item1", "item0", """
var category = Server.getConfigurationElementCategoryWithPath("Example/Lab");
var registry = category.configurationElements[0];
registry.removeAttributeWithKey(controller);
""", [("controller", "string")]), inputs=[("controller", "string")])
    spec("Lab - Forget Controller", "Removes a controller from the registry of controllers vRO keeps in a "
         "configuration element.", "high")

    # Rebuild: calls an action with VRA in its name; the spec names vRA as the consumer
    workflow("Lab - Create Pool", 6, end() + task("item1", "item0", """
if (!members || members.length == 0) {
    throw "a pool needs at least one member";
}
var name = poolName.toLowerCase().replace(/ /g, "-");
var pool = System.getModule("com.example.lab").createVRAPoolObj(name, members);
System.log("pool " + pool.name + " with " + pool.members.length + " members");
""", [("poolName", "string"), ("members", "Array/string")]),
             inputs=[("poolName", "string"), ("members", "Array/string")])
    spec("Lab - Create Pool", "Builds a load balancer pool object, for consumption by vRealize Automation (vRA) "
         "as a day-1 action.", "high")

    # Manual: the input form's logic is OGNL calling an action
    workflow("Lab - Change Password", 7, end() + task("item1", "item0", """
if (!newPassword || newPassword.length < 12) {
    throw "the new password is too short";
}
System.log("password changed on " + controller);
""", [("controller", "string"), ("newPassword", "SecureString")]),
             inputs=[("controller", "string"), ("newPassword", "SecureString")], presentation="""
    <p-param name="controller">
      <desc><![CDATA[Controller]]></desc>
      <p-qual kind="ognl" name="linkedEnumeration" type="Array/string"><![CDATA[GetAction("com.example.lab","listControllers").call()]]></p-qual>
    </p-param>""")
    spec("Lab - Change Password", "Changes the admin password on one lab controller, picked from a list.", "high")

    # Manual: the spec could not be read with confidence
    workflow("Lab - Create Admin", 8, end() + task("item1", "item0", """
var admin = { tenant: tenantName, role: "admin" };
for (var i = 0; i < 3; i++) {
    System.log("step " + i + " for " + admin.tenant);
}
""", TENANT), inputs=TENANT)
    spec("Lab - Create Admin", "Creates a tenant administrator; most of the steps it stands for are not "
         "visible in the export.", "low")

    # Manual: a Path input and attribute (a file on the vRO appliance). Also the parser's signature case:
    # root item6 is not the first item in the file; item6 > item9 > item3 (a condition) > item2 or item4 >
    # item5 > item8.
    ssh_in = [("hostName", "string"), ("port", "number"), ("cmd", "string"), ("username", "string"),
              ("passwordAuthentication", "boolean"), ("password", "SecureString"), ("path", "Path"),
              ("passphrase", "SecureString")]
    host = [("hostName", "string"), ("username", "string"), ("port", "number")]
    ran = [("output", "string"), ("error", "string"), ("exitCode", "number")]
    with_password = task("item2", "item5", """
var session = new SSHSession(hostName, username, port);
session.connectWithPassword(password);
session.executeCommand(cmd, true);
output = session.getOutput();
error = session.getError();
exitCode = session.exitCode;
session.disconnect();
""", host + [("password", "SecureString"), ("cmd", "string")], ran)
    condition = """
  <workflow-item name="item3" out-name="item2" alt-out-name="item4" type="condition" comparator="0">
    <display-name><![CDATA[Password authentication?]]></display-name>
    <in-binding><bind name="passwordAuthentication" type="boolean" export-name="passwordAuthentication"></bind></in-binding>
    <condition name="passwordAuthentication" type="boolean" comparator="0">true</condition>
    <position y="55.0" x="304.5"></position>
  </workflow-item>"""
    with_key = task("item4", "item5", """
var keyPath = path ? path : defaultKeyPairPath;
var session = new SSHSession(hostName, username, port);
session.connectWithPasswordOrIdentity(false, passphrase, keyPath);
session.executeCommand(cmd, true);
output = session.getOutput();
error = session.getError();
exitCode = session.exitCode;
session.disconnect();
""", host + [("passphrase", "SecureString"), ("path", "Path"), ("defaultKeyPairPath", "Path"), ("cmd", "string")], ran)
    results = [("result", "number"), ("errorText", "string"), ("outputText", "string")]
    collect = task("item5", "item8", """
result = exitCode;
errorText = error;
outputText = output;
""", ran, results)
    check = task("item6", "item9", """
if (!hostName) {
    throw "no host name";
}
""", [("hostName", "string")])
    log = task("item9", "item3", """
logText = "running on " + hostName + ": " + cmd;
System.log(logText);
""", [("hostName", "string"), ("cmd", "string")], [("logText", "string")])
    # in the file: item2, item3, item4, item5, item6, item8, item9
    workflow("Lab - Run Remote Command", 9, with_password + condition + with_key + collect + check + end("item8") + log,
             root="item6", inputs=ssh_in, outputs=results,
             attribs=ran + [("logText", "string"), ("defaultKeyPairPath", "Path")])
    spec("Lab - Run Remote Command", "Runs one command on a host over SSH, with a password or a key pair, and "
         "returns its exit code and output.", "high")

    # Needs owner input: calls an action that is not in the export; nothing else stops it
    workflow("Lab - Enable Override", 10, end() + task("item1", "item0", """
if (!segment) {
    throw "no segment";
}
var task = { name: "override " + segment, state: "queued" };
System.getModule("com.example.vendor").waitTaskEnd(task, 5);
System.log("override enabled on " + segment);
""", [("segment", "string")]), inputs=[("segment", "string")])
    spec("Lab - Enable Override", "Enables an override on one lab segment and waits for the task to finish.", "high")


if __name__ == "__main__":
    build()
