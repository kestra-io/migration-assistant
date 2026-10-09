# Conformance inputs

Test inputs only. Each file is here because it exercises one construct, one container format or
one encoding trap that the parser, triage or sanitizer must get right. They are **not estates**: no
brief, no waves, no recommendation is ever made from them, and they never sit in `sources/`.

Used by `tests/test_parse.py` (parser, straight from the files), `tests/test_stages_triage.py`
(Assess run on them in a temporary folder, with the specs in `specs/`), `tests/test_sanitize.py`
and `tests/test_security.py`.

Moved here from `corpus/sources/` on 2026-09-27. Licences of the upstream repositories are in
`licences/`.

## Where they come from

| Path | Upstream | Licence | Format |
|---|---|---|---|
| `canvas/` | [vmware/build-tools-for-vmware-aria](https://github.com/vmware/build-tools-for-vmware-aria), `typescript/vrotsc/e2e/expect/canvas-items/`, commit `4ca2f6a` | BSD-2-Clause | BTVA raw XML tree (`workflows/<Cat>/<Name>.xml` + `.element_info.xml`) |
| `aria-xml/` | [vmware-samples/build-tools-for-vmware-aria-samples](https://github.com/vmware-samples/build-tools-for-vmware-aria-samples), `xml/src/main/resources/`, commit `57e4d5b` | BSD-2-Clause | raw XML tree with a top-level `dunes-meta-inf.xml` |
| `pkgdiff-v1.0.package`, `pkgdiff-v1.1.package` | [lrivallain/vro-package-diff](https://github.com/lrivallain/vro-package-diff), `tests/data/package_v1.0.package`, `package_v1.1.package`, commit `04de43b` | MIT | signed vRO `.package` (ZIP) |
| `specs/` | model-written specs for 22 of the conformance workflows, saved 2026-09-24 | this repository | JSON, model-written |
| `sanitize/planted-secrets/` | written for this repository, 2026-09-27 | this repository | raw XML tree with known fake secrets |
| `vra/sample/`, `vra/sample.vra-ng` | written for this repository, 2026-09-30, laid out like [vmware-samples/build-tools-for-vmware-aria-samples](https://github.com/vmware-samples/build-tools-for-vmware-aria-samples) `vra/` and `vcfa-all-apps/` (layout only; no file copied) | this repository | vRA content export: the tree, and the same zipped by `vra/build.py` |
| `events/` | written for this repository, 2026-09-30 | this repository | a raw XML tree (`vro/`), a vRA content tree (`vra/`) and three specs (`specs/`): an event-driven estate |
| `helpers/` | written for this repository, 2026-09-30 | this repository | a raw XML tree (`vro/`), a vRA content tree (`vra/`) and one spec (`specs/`): shared helpers and the replacement of vRA |
| `builders/` | written for this repository, 2026-10-01, by `builders/build.py` | this repository | a raw XML tree (`vro/`): body builders on top of a client, and REST paths their callers make concrete |
| `large/` | written for this repository, 2026-10-01: only `large/build.py` is committed; the tests build the estate in a temporary folder | this repository | a raw XML tree and specs of any size (60 workflows for the front page test) |
| `security/` | written for this repository, 2026-09-27 (built by the tests at run time where a file would be hostile to keep on disk) | this repository | hostile XML and ZIP inputs |

The canvas files are **synthetic**: vrotsc's end-to-end expected outputs, each built to exercise one
vRO construct. They are the best public check that a construct is read correctly, and a poor guide to
what a customer's workflows look like.

## What each file covers

### Canvas fixtures (`canvas/workflows/VMware/PSCoE/`)

| Workflow | Items | Construct it exercises |
|---|---|---|
| Action Happy Path | 2 task, end | task calling an action (`script-module`), auto-generated script |
| Action Edge | task, end | action call with an unusual binding |
| Async Workflow Test | 4 task, end | `launched-workflow-id`: asynchronous call to a workflow **not in the export**; root is not the first task in the file |
| Scheduled Workflow Test | 4 task, end | scheduled start of another workflow; root not first in the file |
| Complex | 2 task, custom-condition, waiting-timer, end | a **cycle** (timer loops back through a condition); root not first |
| Decision Happy, Decision Edge, Decision Edge 2 | custom-condition + tasks | `custom-condition`: `out-name` true, `alt-out-name` false; converging branches |
| Default Error Handler Happy | task, end | `<error-handler>` pointing at an item already on the path |
| Default Error Handler Custom Item | 3 task, 2 end | `<error-handler>` whose target item is only reachable through the handler |
| Root Default First | 2 task, end | `root-name` is the first item (the easy case) |
| Root When Set | 2 task, end | `root-name` is **not** the first item in the file (document order misleads) |
| Switch Happy Path, All Cases, Boolean Cases, String Cases | switch + 3 to 5 tasks | switch arms from `<condition>` labels; boolean and string case values; comparator codes (0 known, 1 to 6 unknown) |
| Switch Edge Cases | 3 switch, 12 task | nested switches, default arms |
| User Interaction | 3 task, 3 input | `input` items (user interaction) with three separate `.form.json` input forms |
| Waiting Timer Happy, Waiting Timer Edge | waiting-timer, task | `waiting-timer` items |
| Workflow End Happy | custom-condition, 2 end | `end-mode="0"`, a normal end |
| Workflow End Exception | custom-condition, 2 end | `end-mode="1"`, an end that throws |
| Workflow Test | 4 task, custom-condition, link, waiting-timer, 2 end | everything at once: cycle, `<error-handler>`, a `link` to a workflow not in the export, root not first |

### Build Tools sample (`aria-xml/`)

| Workflow | Covers |
|---|---|
| Install | the smallest real workflow (one task, one end); a raw XML tree whose `dunes-meta-inf.xml` sits at the top instead of under `META-INF/` |

### Package pair (`pkgdiff-v1.0.package`, `pkgdiff-v1.1.package`)

Two versions of the same signed package: 2 and 3 workflows, 4 actions, 2 configuration elements,
2 resource elements and 1 certificate each.

| Trap | Where |
|---|---|
| `elements/<uuid>/data` is **UTF-16BE with an `FE FF` BOM** while its XML declaration says UTF-8 | every element |
| `info` holds only type, id and signature owner, never the name | every element |
| the `categories` file quotes `name` with **single quotes** | every element |
| a resource element's `data` is a **nested ZIP** (`VSO-RESOURCE-INF/`), one JPEG and one XML file | `tropical-paradise.jpg`, `note.xml` |
| a configuration element holds a `SecureString` and vRO's `__NULL__` sentinel | `test_configurationA` |
| an OGNL qualifier whose expression is `__NULL__` (no form logic) | every workflow |
| `end-mode="1"` | every workflow |
| `signature-owner` and `pkg-owner` identities (the sanitizer strips them) | `info`, `dunes-meta-inf` |
| near-identical workflows across versions (clustering, near-duplicate rule) | `WF_test1..3` |

### Planted secrets (`sanitize/planted-secrets/`)

A small raw XML tree with known fake hosts, IPs, URLs, emails, a service account, a hard-coded
password and a bearer token in scripts, attribute values, a configuration element (including a
SecureString) and a presentation default. `tests/test_sanitize.py` asserts every one is gone from
the sanitized copy and that the item graph, bindings and script structure are unchanged. Every
value in it is invented; none is a real host or credential.

### vRA export (`vra/`)

Synthetic, every name, address and account invented. `sample/` is the source; `sample.vra-ng` is built
from it by `python tests/conformance/vra/build.py`, deterministically, and `tests/test_vra.py` checks the
two match.

| Item | Covers |
|---|---|
| `catalog-items/Sample Templates__Sample Web Server.json` and `forms/` | a catalog item whose form is embedded as a JSON string (the public `vra/` sample's shape); three fields, one from a script action |
| `blueprints/Sample Web Server/` | a cloud template with two inputs and two resources (a machine and a network), `${input.*}` and `${resource.*}` references |
| `subscriptions/Sample - Install on provision.json` | a blocking `compute.provision.post` subscription whose `runnableId` is the aria-xml Install workflow's vRO ID: resolves |
| `subscriptions/Sample - Missing workflow.json` | a subscription naming a VCF-style 65-character workflow ID that is in no package: reported as unresolved |
| `content-sources/`, `entitlements/` (YAML), `property-groups/`, `policies/approval/`, `content.yaml` region mapping | the other kinds, and planted values for sanitize: a DNS host, an IP, an email, owners and a subscriber, an `orgId`, a network name, a constraint tag, cloud account tags, a `USER:` approver |


### Event-driven estate (`events/`)

Used by `tests/test_events.py`. Invented names only (`com.example.*`, "Web Host", "Example Project").

| Element | What it exercises |
|---|---|
| `Net - Register Host` | action calls through module variables, a reassignment to another module, an alias, a call in a comment and one in a string (not calls); a spec that names vRA (no longer Rebuild when a subscription starts it); started by a blocking `compute.provision.post` subscription |
| `Net - Unregister Host` | two modules in one `var` declaration; run by two subscriptions (blocking `compute.removal.pre`, and a failed-create `deployment.request.post`) |
| `Net - Event Router` | a `switch` on the event type with three cases: a dispatcher (Rebuild); reads the shared state |
| `com.example.ipam/callApi` | a generic REST helper (RESTHostManager, method and path from the caller) |
| `com.example.ipam/recordPath` | a path-building action, expanded at the call site |
| `com.example.ipam/addRecord`, `ipam.v2/addRecord` | call sites with literal paths matching the Infoblox signature; `let` and an alias of an alias |
| `com.example.state/*` | state in a configuration element (`Example/HostState`): read, write, remove; links the three workflows into one lifecycle group |
| `vra/` | one catalog item (three form fields), one cloud template (a machine with an input-bound custom property), four subscriptions |

### Shared helpers and replacing vRA (`helpers/`)

Two workflows and ten actions, every name invented. Used by `tests/test_helpers.py`.

| Element | What it is | Checks |
|---|---|---|
| `com.example.net/callApi` | a generic REST helper (method and path from the caller) | used for Infoblox (a Kestra plugin) and Nuage VSD (none): it is Rebuild, and the brief says which uses a plugin replaces |
| `addDnsRecord`, `addSubnet` | one client per system, both through `callApi` | Plugin-covered library (Infoblox), Rebuild (Nuage) |
| `recordName` | used only by `addDnsRecord` | goes with the plugin-covered client |
| `checkResponse` | used by both clients | Rebuild with `addSubnet`, its use in `addDnsRecord` goes with the plugin |
| `levelOne` > `levelTwo` > `levelThree` | a chain of logic, three deep | each converts with Web - Provision Host; the spec prompt carries all three (depth and caller) |
| `onlyManual` | used only by Web - Approve Host, which has no spec | Manual, naming its caller and the blocker |
| `orphan` | nothing calls it | Needs owner input, not Manual |
| Web - Provision Host | calls the chain and both clients; medium-confidence spec | Convert; its wave's spec review gate closes from its `spec.` item in `answers.yaml` |
| vRA `App Host` | a cloud template creating a `Cloud.vSphere.Machine` and a `Cloud.vSphere.Network`, its catalog item (2 fields), one blocking subscription (`compute.provision.post`, 5 min) | with Q9 = replace: a replacement wave after the workflow wave, the App from the form, vCenter tasks for the machine, a gap for the network, the blocking step's timeout and failure policy as a gate there |

### Body builders and concrete paths (`builders/`)

One workflow and eight actions, every name invented. Used by `tests/test_builders.py`.

| Element | What it is | Checks |
|---|---|---|
| `sendRequest` | a generic REST helper (method and path from the caller) | |
| `createVsdObject` | the Nuage VSD client, `POST /nuage/api/v6/` + its `type` parameter | `ops_resolved` holds the four types its callers pass, no placeholder; the target inventory lists the four |
| `addIpamRecord` | an Infoblox client with a fixed path | Plugin-covered library |
| `buildVsdDomain` | calls only the client, type `"domains"` | part of the client (`client_through`), Rebuild like it |
| `buildVsdZone` | calls the client (type `"zones"`) and `renderTemplate` | part of the client too; `builder_helpers` names the renderer |
| `buildVsdObject` | forwards its own `kind` to the client; the workflow passes `"subnets"` | the path is bound two levels up |
| `renderTemplate` | reads a resource element; reaches no system | judged on its own code (vRO's state) |
| `setupNetwork` | calls the clients of two systems (type `"enterprises"`) | an orchestrator, not a builder: judged by its callers |

### An estate of any size (`large/`)

`large/build.py` writes, at test time, `n` workflows in six areas over seven systems found by REST signature
(Nuage VSD, Avi, F5 BIG-IP, vCloud Director, vCenter, ServiceNow, Infoblox), four client actions per system
behind one generic REST helper, a configuration element some workflows write, and one spec per workflow with
three open questions (some blocking), a suspected defect for every third and medium confidence for every
fifth. Every name is invented. Used by `tests/test_front.py` and `tests/test_writing.py`.

| Built | Checks |
|---|---|
| 60 workflows | `index.html` leads with the status line, the summary, the number and the translation table; every list is capped (10 rows, 5 defects, 6 waves, 3 keys per group); it stays within 1,400 words; the number is the sum of the table and the recommendation reads it; no workflow ID on the page; the full lists are on `translation.html` and `workflows/index.html` |
| 8 workflows | the same page, and the 60-workflow page is at most 250 words longer |

### Tests on the review and helpers estates for the input file

- `tests/test_ask_once.py` (`review/`, its specs copied and given four more questions): a question on certificate
  checks, credentials, endpoints or a race on shared state is covered by the decision the plan already asks
  (`tls.`, `auth.` or `endpoint.`, `state-store.`), leaves the open questions, is named by that decision in
  `answers.yaml`, and points to it from the workflow page and the open questions page; a concurrency question no
  longer holds its workflow in Needs owner input.
- `tests/test_your_input.py` (`helpers/`): a `suggested` value follows the code until a person answers (`yes`
  takes it); an answered item moves to `answered`, and one no longer asked stays there, its question starting
  "No longer asked:"; a rewrite of the tool's text keeps the file's time; the signature is `signed_for:`
  `wave 1` or `full plan`; an old-format file is migrated with every answer and the signature; the
  recommendation counts only client code as client code.
