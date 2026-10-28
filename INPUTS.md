# Inputs: what the customer sends, and where it goes

This tool reads one estate at a time from one **export folder**. An **estate** is one customer's whole
vRO/vRA scope, named after its export folder, and one estate is one run
(`kestra-migrate vra run <export folder>`). This page covers what to put in that folder,
how an administrator produces it, and what has to come separately because no export contains it.

Every step that describes how the VMware product behaves is marked **(to confirm with a vRO expert)**.
Those steps come from the formats we have read, public documentation and public vRO
libraries, not from operating a customer's vRO. Menu names in particular differ between vRO 7.x,
8.x and VCF 9 Orchestrator.

## 1. The primary input: a `.package` export

A vRO package is a signed ZIP that holds workflows, actions, configuration elements and resource
elements, with a folder path for each. It is what vRO itself produces for moving content between
servers, so it is the format this tool documents first and tests on (`tests/vra/conformance/pkgdiff-*.package`).

How an administrator produces one **(to confirm with a vRO expert)**:

1. In the Orchestrator client, open **Assets > Packages** (vRO 8.x and VCF 9; in the vRO 7.x Java
   client, the **Packages** view in Design mode).
2. **Create a new package**, name it after the estate (for example `com.acme.migration.estate`).
3. Add content in either of two ways:
   - **Whole folders**: add each workflow folder and each action module in scope. Folder contents,
     including sub-folders, go in with it.
   - **Individual workflows**: add them one by one. When asked, **include dependencies**, so the
     actions, configuration elements, resource elements and nested workflows they reference come too.
     A nested workflow left out appears in the brief as a call "not in the export".
4. **Export** the package. Leave the default export options. Configuration element values can stay in
   the export: their numbers, booleans and netmasks reach the model, and every text value is dropped before
   any later step reads the export (section 6).
5. One package per vRO cluster is ideal. Several packages from the same estate are fine: put them all
   in the estate folder and they are read together.

What the tool reads from a package: `elements/<id>/data` (UTF-16BE XML), `info` (type and id),
`categories` (folder path), input forms, nested resource ZIPs, and `dunes-meta-inf` (vRO version and
declared plugins). The signatures and certificates are not needed.

## 2. Also accepted: a Build Tools (BTVA) source tree

Teams that keep vRO content in git with VMware Build Tools for Aria (BTVA, formerly vRBT) have a
source tree instead of a package: `Workflow/<Folder>/<Name>.xml` with a sibling
`<Name>.element_info.xml`, `ScriptModule/...` for actions, and usually `META-INF/dunes-meta-inf.xml`.
Most public vRO libraries on GitHub are trees of this kind.

Put the tree in the estate folder as it is in the repository, either as the whole folder
(`acme/src/main/resources/...` trimmed to the folder holding `Workflow/`) or one tree per
sub-folder. **(To confirm with a vRO expert:** whether the customer's BTVA version writes the same
`element_info` layout; the TypeScript `vro/src` form of BTVA projects is **not** read, only the XML
it compiles to.**)**

A tree built from source control usually records no vRO version and no build host, so Preflight
reports the version as unknown.

## 3. Accepted but experimental: single `.workflow` files

vRO can export one workflow at a time as a `.workflow` file (a ZIP of `workflow-info`,
`workflow-content` and optional input forms). The tool reads it, and **Preflight marks every such
input "experimental"**, because that reader has never run on a real export (none was among the inputs it was built on).
Prefer a package. If single files are all you have, send them and check their counts by hand.

## 4. Entry points: a vRA content export (`.vra-ng`), or `entry-points.yaml`

A vRO package does not say what starts its workflows. If the estate runs under vRA 8, Aria
Automation or VCF Automation, the customer can export that side with VMware Build Tools for
Aria (a `vra-ng` project) and put the `.vra-ng` next to the package. A Build Tools vRA project folder (with its `content.yaml`, optionally under
`src/main/resources/`) is read the same way. With the same ZIP guards and safe JSON/YAML loading as
everything else, the tool reads:

- `content.yaml`, and whether each item it lists is present;
- catalog items, their content source and request form fields (from `forms/<item>.json`, or a
  separate `forms/<item>__FormData.json`), and the cloud template each deploys;
- cloud templates (`blueprints/<name>/content.yaml`): inputs, and the resources they create;
- content sources, entitlements, property groups and policies;
- event broker subscriptions: event topic, condition, blocking, and the vRO workflow they run;
- day-2 resource actions, custom resource actions and catalog vRO workflows, which also run workflows.

Folders it does not know are listed in `readiness.md`, not dropped. Every workflow reference is
matched to the package by vRO workflow ID, and any that does not resolve is reported.

Without a vRA export, the customer gives the same facts by hand in
`<export folder>/kestra-assessment/your-input/entry-points.yaml`. Preflight writes this file only in that case, as a
template with one commented example in the same schema, and never overwrites it once it has an entry.
`answers.yaml` then has one open question, Q2, that points to it.

With a vRA export, there is no such file. Q2 is asked in `answers.yaml` itself, for what no export holds (vRO
scheduled tasks and policies, an ITSM, scripts calling the vRO REST API). Name them, or answer `none`.

A filled-in `entry-points.yaml` that a person adds is always read. When both exist, they are merged, and a
conflict keeps the export's value and is reported. Q2 is "partly answered" while there are entry points on
record, and answered once the file says `complete: true` (or Q2 is answered in `your-input/answers.yaml`).

## 5. What is not in any export, and has to come separately

A package holds the workflows. It does not hold who calls them or how often they run. Without these,
the brief can only say which workflows nothing *in the export* calls, not which are unused.

| Missing from the export | Why it matters | How to get it **(to confirm with a vRO expert)** | Preflight question |
|---|---|---|---|
| **Run history**, twelve months: workflow ID, start time, result | The retire rule ("no executions in twelve months") cannot be tested without it | vRO workflow tokens (the Orchestrator REST API `/vco/api/workflows/{id}/executions`, or the vRO database), or vRA deployment and request history. A CSV is enough | Q7 |
| **vRA catalog items** and the workflows behind them | These are entry points: a workflow a catalog item starts is used even if nothing in vRO calls it | The `.vra-ng` content export (section 4), or `entry-points.yaml` | Q2 |
| **Event broker subscriptions** | Also entry points (for example a workflow that runs after every VM is provisioned). They feed the trigger plan (the vRA flow page): what starts each workflow, whether vRA waits for it, and how that maps in Kestra. They never decide the workflow's outcome, which is judged on its logic | The `.vra-ng` content export (section 4), or `entry-points.yaml` | Q2 |
| **Whether vRA stays** as the front end or is replaced | Kept, each subscription calls a Kestra webhook; replaced, catalog items become Kestra Apps and cloud templates flows | A decision, not an export | Q9 |
| **Scheduled tasks and policies in vRO** | Entry points started by time or by a policy | vRO scheduled workflows and policies list, named in Q2 (or in `entry-points.yaml`, `other_triggers`, without a vRA export) | Q2 |
| **External callers**: ITSM (ServiceNow), scripts calling the vRO REST API | Same | A list from the owning teams, named in Q2 (or in `entry-points.yaml`, `other_triggers`, without a vRA export) | Q2 |
| **Installed plugins and versions** per node | A plugin object type with no Kestra route blocks every workflow that uses it | Orchestrator control centre > plugins, per node | Q8 |
| **A non-production instance per target** | A wave that writes to a target cannot be verified without one | From the owning teams | Q3 |

Answers go in `<export folder>/kestra-assessment/your-input/answers.yaml`, the one file a person fills in. By default it is
the only file in `your-input/`. It looks like this:

```yaml
# Answer what's open, then run: kestra-migrate vra run acme

estate:
  - id: estate.config
    question: 36 workflows call modules named getEndpoint and getDatacenterSettings, which pick a configuration element at run time (under Infra/Endpoints and Infra/Datacenters). Where should those settings live in Kestra?
    options:
      - Kestra's KV store, one entry per element, read by the same name
      - namespace variables, one set per environment
      - the system they come from today, which the flows call
    suggested: Kestra's KV store, one entry per element, read by the same name
    answer: 1
    closes:
      - question.create-dns-record.endpoint-identity
      - question.provision-linux-vm-dr-site.endpoint-identity
  - id: assume.trust-store
    assumed: Kestra checks each system's certificate against the CAs in its trust store. The CAs vRO imported go there once, in the Kestra setup, not in each flow.
    answer:
    closes:
      - tls.f5-bigip

open:
  - id: suggested
    question: 'Accept every suggested answer below? (yes; to change one, add answer: under it)'
    answer:
    each:
      - id: tls.f5-bigip
        question: Do the calls to F5 BIG-IP check its certificate? (vRO's do not)
        suggested: check it (the CA goes in the Kestra setup)
      - id: lifecycle.l1
        question: Do the 4 workflows of lifecycle group L1 convert and switch over together?
        suggested: together
  - id: defects.lb-create-load-balancer
    question: '3 possible defects in LB - Create Load Balancer: keep them all, fix them all, or decide each? (they are listed on report/workflows/lb-create-load-balancer.html)'
    answer:

answered:
  - id: q1-scope
    question: Is this export the whole vRO estate?
    answer: yes, the whole estate, from one vRO node

signed_for: full plan
```

`estate` comes first: the questions about the whole estate (at most eight, built from what the code shows: what
starts the workflows, where settings and secrets come from, which systems have a non-production instance, calls to
other vRO clusters, workflows that look out of scope, a module many workflows call), and the vRO mechanics the tool
assumes instead of asking (certificates and the trust store, host verification, retries and counters, REST hosts made
in code). An estate answer is one of its `options`, its number, `yes` for the suggested one, or your own words. It
applies to every workflow: `closes` lists the items it settled, and those are not asked again. While an estate
question is open, `settles` lists what its suggested answer would settle, and those are not asked one by one either.
Change an answer and what it closed is asked again. An assumption holds while its `answer` is empty (or `yes`); any
other answer overrides it, and what it settled is asked again. You can fill this section in before the first run.

`open` lists what is still unanswered, with what a wave waits on first, and `answered` lists what already has an
answer. Each entry has an `id`, one plain
`question`, a `suggested` value when the code points to one, and an `answer`. Write `yes` to take the
suggested value; any other answer is the answer itself. A remark goes in the answer too. `signed_for` is
`wave 1` or `full plan`, and stays empty until you sign.

A question that repeats is asked once: a possible defect or a question about an action several workflows share, or
the same one on near-duplicate workflows, is one entry `same.<defect or question>.<what>.<kind>`, with its items
under `each:`. In an interactive run, the planning after each round can also merge questions into one `same.<what>`
entry, close a question with an answer already given (listed under that answer's `closes`), reword one, or add one
(`added.<what>`); the IDs still say what a question is about, never its place.

What is settled together is one entry, with its items under `each:`. `suggested` holds every value the code
suggests: `yes` takes them all, and any one can still be given its own `answer:`. `defects.<workflow>` takes
`keep all`, `fix all` or `decide each`; after `decide each` the next run lists that workflow's possible defects
under it, each to answer `keep` or `fix`. `specs` takes `yes`, or names the workflow whose spec is wrong and
says what. `endpoints` (where a converted flow can run before production), `questions` (the specs' open
questions) and `about-the-estate` (what the export cannot say) gather items answered one by one. A group's
answer counts for the items it showed when it was given; an item found later is asked on its own entry.

Every item's `id` says what it is about, never its place in a list: a possible defect's is its workflow, the
action it concerns and its kind (`defect.lb-add-machine-to-pool.add-avi-pool-server.ip-version`), so a run
that words or orders its findings differently keeps each answer on its item. The tool rewrites the file on
every run: it moves an entry to `answered` once it has an answer, keeps every answer exactly as written, and
never drops an id. An answer whose item a run no longer finds moves to a third list, `no_longer_asked`, with
the question it was given for, and lands on nothing else. A file that does not parse is never rewritten; the
version before it is kept in `data/answers.yaml.previous`. An `answers.yaml` written by an earlier version is
moved to this format on the next run, every answer and the signature carried over, and the old file is kept
in `data/`.

The questions are asked from what is on record, and asked again after Assess: with a vRA export read, Q2
asks only for what an export cannot hold (vRO scheduled tasks and policies, the ITSM, REST callers); Q3
names the systems the tool found; Q7 names any run-history files in the folder; Q8 suggests the vRO plugins
the code uses, for you to answer `yes` or correct. Q9 takes keep, replace, or replace vRA with a bridge that
forwards vRA's requests to Kestra until users move to the new front end. A `.vra-ng` in the estate folder is read (section 4). Other files (a run-history CSV) can go there
too; Preflight lists them as "other files" and they are not parsed yet.

Later, the brief asks in the same `answers.yaml` for the decisions its waves wait for: a client route for a
system with no Kestra plugin, whether Enterprise Edition is available, a timeout policy for each blocking step,
how to get a resource no Kestra task creates, and keep or fix for each suspected defect. Each is a plain
question with the choices in it, and a suggested value where the tool has a recommendation. After
`decide each`, a suspected defect reads like this: "The Deployment Registry is read before the teardown gate,
so an event that should be ignored can still fail the run. Keep the current behavior, or fix it?" (answer
`keep` or `fix`). The specs' open questions, the spec review and the signature are there too.

A question that a decision already asks is asked only once, in that decision. A spec question about
certificate checks goes in the certificate decision, and one about what a failure does goes in the timeout and
failure decision of the blocking subscription that runs the workflow. Every wave entry criterion names the id
that closes it.

### Optional: correct what the tool found (`corrections.yaml`)

This step is for advanced use, and most estates never need it. If the tool got something wrong (a domain's
name, which domain a cluster belongs to, how a target is reached, a wrapper flag, a workflow's outcome),
add `<export folder>/kestra-assessment/your-input/corrections.yaml`. The tool never creates it, but reads it on every run if it's
there. Leave a section empty to change nothing:

```yaml
# Kestra Migration Assistant for vRA: corrections (optional). Read on every run; leave a section empty to change nothing.
#
# Only for what the tool found wrong. Answers to its questions go in answers.yaml, next to this file.
# Keys are the short names the pages show (or the workflow IDs in data/3-brief/triage.csv).

domains:
  rename: {}        # domain key -> new name, e.g. "avi-controller:provisioning": "Load balancer build"
  move: {}          # cluster id -> domain key, e.g. C12: "avi-controller:operations"

targets:
  reach: {}         # target key -> plugin | http | none, e.g. avi-controller: plugin

wrappers:
  not_wrapper: []   # workflows flagged as wrappers that are not
  wrapper: []       # workflows that are wrappers the rule missed

# One entry per workflow or action. The outcome must be one of these nine: Retire, Near-duplicate, Wrapper,
# Plugin-covered library, Convert, Needs owner input, Needs a model, Manual, Rebuild
#   outcomes:
#     lb-create-load-balancer:
#       outcome: Retire
#       reason: "unused, confirmed by the platform team on 2026-10-01"
outcomes: {}
```

## 6. Before anything leaves the customer's network: sanitize

Every run sanitizes first. `kestra-migrate vra run acme` writes a redacted copy to
`acme/kestra-assessment/data/0-preflight/sanitized/` and a `redaction-report.md` to review. In the copy:

- hostnames, IPs, URLs, emails, account names and anything credential-shaped are replaced with stable
  placeholders
- configuration element values are dropped, except numbers, booleans and netmasks
- SecureString values are dropped
- signer and owner identities are stripped

A `.vra-ng` is sanitized too. Besides the same text rules, owners, subscribers and principals are redacted as
accounts, the tenant (`orgId`) as an identity, and network names, constraint tags and cloud account tags as
NETWORK. IDs, event topics, conditions and names are kept, so entry points still resolve. The export's own
module and category names (`com.acme.net`) are code, not hosts, and are kept as written, as are workflow,
action, element and file names.

The structure is unchanged, so every later stage (Preflight, Assess, Understand, Brief and the pages) reads the
copy and never the export. No prompt carries a value the copy replaced, and the pages show placeholders such as
`<NETWORK-01>` where the export had a value. The customer can run this themselves and send only the sanitized
copy.

## 7. Where the customer puts it

One folder per estate, anywhere on the machine. The folder's name is the estate's name.

```
acme/                            the export folder
  acme-prod.package              one or more packages ...
  acme-dev-extras.package
  btva-repo/                     ... and/or BTVA trees ...
  Some Workflow.workflow         ... and/or single workflow files (experimental), at the top level
  acme-vra.vra-ng                a vRA content export, read for entry points (section 4)
  run-history-2025.csv           other files are listed by Preflight, not parsed
  kestra-assessment/             written by the tool, and never read as part of the export
```

Then:

```bash
kestra-migrate vra run acme      # sanitize, preflight, assess, understand, brief, pages
```

Everything the tool writes goes to `acme/kestra-assessment/`, or to the folder `--out` names if you can't
write to the export folder. Open `acme/kestra-assessment/index.html`, the brief. Answer in
`acme/kestra-assessment/your-input/answers.yaml` and run the same command again. Only what changed is redone,
and model replies already paid for come from the cache in `kestra-assessment/data/model-cache/`.

These commands upload nothing. The only outbound traffic goes to the configured model, and only for the model
calls the brief lists (domain names, walkthrough text, and the specs, which `run` writes unless `--no-model` is
given). Every piece of export text in those calls is marked as untrusted data.
