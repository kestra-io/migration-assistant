# Inputs: what the customer sends, and where it goes

This tool reads one estate at a time from one **export folder**. An **estate** is one customer's whole
vRO/vRA scope, and it is named after its export folder; one estate is one run
(`kestra-migrate vra run <export folder>`). This page says what to put in that folder,
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
3. Add content. Two ways, either is fine:
   - **Whole folders**: add each workflow folder and each action module in scope. Folder contents,
     including sub-folders, go in with it.
   - **Individual workflows**: add them one by one. When asked, **include dependencies**, so the
     actions, configuration elements, resource elements and nested workflows they reference come too.
     A nested workflow left out appears in the brief as a call "not in the export".
4. **Export** the package. Leave the default export options; in particular keep configuration
   element values if the customer is comfortable sharing them (they can be redacted, see section 6),
   because without them some target endpoints are unknown.
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
input "experimental"**: that reader has never run on a real export (none was among the inputs it was built on). Prefer a
package; if single files are all that is available, send them and check their counts by hand.

## 4. Entry points: a vRA content export (`.vra-ng`), or `entry-points.yaml`

A vRO package does not say what starts its workflows. If the estate runs under vRA 8, Aria
Automation or VCF Automation, the customer can export that side with VMware Build Tools for
Aria (a `vra-ng` project) and put the `.vra-ng` next to the package. A Build Tools vRA project folder (with its `content.yaml`, optionally under
`src/main/resources/`) is read the same way. The tool reads, with the same ZIP guards and safe
JSON/YAML loading as everything else:

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
`<export folder>/kestra-assessment/your-input/entry-points.yaml`. Preflight writes it only then, as a template with one
commented example in the same schema, and never overwrites it once it has an entry; `answers.yaml` then has
one open question, Q2, that points to it. With a vRA export there is no such file: Q2 is asked in
`answers.yaml` itself, for what no export holds (vRO scheduled tasks and policies, an ITSM, scripts calling
the vRO REST API): name them, or answer `none`. A filled-in `entry-points.yaml` a person adds is always
read. When both exist they are merged, and a conflict keeps the export's value and is reported. Q2 is
"partly answered" while there are entry points on record, and answered once the file says
`complete: true` (or Q2 is answered in `your-input/answers.yaml`).

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

`open` lists what a wave waits on, first; `answered` lists the rest. Each entry has an `id`, one plain
`question`, a `suggested` value when the code points to one, and an `answer`. Write `yes` to take the
suggested value; any other answer is the answer itself. A remark goes in the answer too. `signed_for` is
`wave 1` or `full plan`, and stays empty until you sign.

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
version before it is kept in `data/answers.yaml.previous`. An `answers.yaml` in an older format is moved to
this one on the next run, every answer and the signature carried over, and the old file kept as
`data/answers.yaml.before-2g`.

The questions are asked from what is on record, and asked again after Assess: with a vRA export read, Q2
asks only for what an export cannot hold (vRO scheduled tasks and policies, the ITSM, REST callers); Q3
names the systems the tool found; Q7 names any run-history files in the folder; Q8 suggests the vRO plugins
the code uses, for you to answer `yes` or correct. Q9 takes keep, replace, or replace with a bridge until
then. A `.vra-ng` in the estate folder is read (section 4). Other files (a run-history CSV) can go there
too; Preflight lists them as "other files" and they are not parsed yet.

Later, the brief asks for the decisions its waves wait for (a client route for a system with no Kestra
plugin, whether Enterprise Edition is available, a timeout policy for each blocking step, how to get a
resource no Kestra task creates, keep or fix each suspected defect) in the same `answers.yaml`, each a plain
question with the choices in it, and with a suggested value where the tool has a recommendation. A
suspected defect, after `decide each`, reads like this: "The Deployment Registry is read before the teardown
gate, so an event that should be ignored can still fail the run. Keep the current behavior, or fix it?"
(answer `keep` or `fix`). The specs' open questions and the spec review are there too, and the signature. A
question a decision already asks is asked once, there: a spec question about certificate checks in the
certificate decision, one about what a failure does in the timeout and failure decision of the blocking
subscription that runs the workflow. Every wave entry criterion names the id that closes it.

### Optional: correct what the tool found (`corrections.yaml`)

This step is for advanced use, and most estates never need it. If the tool got something wrong (a domain's
name, which domain a cluster belongs to, how a target is reached, a wrapper flag, a workflow's outcome),
add `<export folder>/kestra-assessment/your-input/corrections.yaml`. The tool never creates it; it reads it on every run if it
is there. Leave a section empty to change nothing:

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

# One entry per workflow or action. The outcome must be one of: Retire, Near-duplicate, Wrapper,
# Plugin-covered library, Convert, Needs owner input, Manual, Rebuild
#   outcomes:
#     lb-create-load-balancer:
#       outcome: Retire
#       reason: "unused, confirmed by the platform team on 2026-10-01"
outcomes: {}
```

## 6. Before anything leaves the customer's network: sanitize

Every run sanitizes first: `kestra-migrate vra run acme` writes a redacted copy to
`acme/kestra-assessment/data/0-preflight/sanitized/` and a `redaction-report.md` to review:
hostnames, IPs, URLs, emails, account names and anything credential-shaped replaced with stable
placeholders; configuration element and SecureString values dropped; signer and owner identities
stripped. A `.vra-ng` is sanitized too: besides the same text rules, owners, subscribers and
principals are redacted as accounts, the tenant (`orgId`) as an identity, and network names, constraint
tags and cloud account tags as NETWORK; IDs, event topics, conditions and names are kept, so entry
points still resolve. The export's own module and category names (`com.acme.net`) are code, not hosts, and
are kept as written, as are workflow, action, element and file names. The structure is unchanged, so every
later stage (Preflight, Assess, Understand, Brief and the pages) reads the copy and never the export: no
prompt carries a value the copy replaced, and the pages show placeholders such as `<NETWORK-01>` where the
export had a value. The customer can run this themselves and send only the sanitized copy.

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

Everything the tool writes goes to `acme/kestra-assessment/` (or the folder `--out` names, for an export
folder you cannot write to); open `acme/kestra-assessment/index.html`, the brief. Answer in
`acme/kestra-assessment/your-input/answers.yaml` and run the same command again; only what changed is
re-done, and the model replies already paid for come from the cache in `kestra-assessment/data/model-cache/`.
Nothing is uploaded anywhere by these commands; the only outbound traffic is to the configured model, and
only for the model calls the brief lists (domain names, walkthrough text, and the specs, which `run` writes
unless `--no-model` is given), with every piece of export text marked as untrusted data.
