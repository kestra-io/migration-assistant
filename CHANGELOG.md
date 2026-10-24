# Changelog

Every release of the Kestra Migration Assistant for vRA. Versions follow [semantic versioning](https://semver.org).

## 1.1.0 (2026-10-24)

Fixes found by running the assistant on a 46-workflow test estate, five more systems named, and one change to how
a workflow's outcome is decided. Re-running 1.1.0 on a folder that 1.0.0 assessed keeps every answer you gave.

### Systems the assistant now names

The assistant now recognizes these systems in vRO code, so the brief maps them to their Kestra plugins instead of
generic HTTP requests or nothing at all:

- **Ansible Tower / AWX** job templates and **Terraform Cloud / Enterprise** runs, called through the HTTP-REST
  plugin. Kestra's Ansible and Terraform plugins run playbooks and the terraform CLI themselves. Neither launches a
  Tower job template or queues a run through the Terraform Enterprise API. The brief says so and gives the two
  options: run the playbook or `terraform apply` in Kestra, or keep Tower or Terraform Enterprise and call its API
  with an HTTP task.
- **Microsoft Teams** and **Slack** incoming webhooks, mapped to `TeamsIncomingWebhook` and `SlackIncomingWebhook`.
  Only a webhook's host identifies it, and the sanitized copy replaces every host. So when Sanitize replaces a host
  it recognizes, it records which system it was (for example `slackWebhookUrl: slack`) in the redaction report.
  It never records the value. A Teams MessageCard or Adaptive Card body is recognized too.
- **Active Directory** through the vRO Active Directory plugin (`AD:` objects and `ActiveDirectory.search...`),
  mapped to the LDAP plugin's Search, Add, Modify and Delete tasks. `setAttribute` on an AD object now counts as a
  write to the directory.

### Fixes

- The brief's opening sentence read "This estate provisions an add user to AD group, ..." when catalog items are
  named for what they do. Such an estate now opens with "Users request 16 Service Broker catalog items, such as
  ...".
- Each piece of code missing from the export showed as a bullet reading only "Missing." in "What we need from
  you". The pieces are now one line that names each one and what calls it. Its question in `answers.yaml` no longer
  points to a `sources/` folder that doesn't exist.
- The plan cut workflow names short ("Create is the first of a kind", "LB - Create"). Names are now given in full.
- "Client actions for five other systems" counted only some of the systems in that row. It now counts all of them.
- The Translation page named no system for a client that only calls unnamed REST hosts ("hand-written client for ,
  which Kestra has a plugin for ()"). It now reads "hand-written HTTP client", and each call becomes an HTTP task.
- Plugin coverage showed every plugin system except F5 BIG-IP as "Kestra plugin, some operations over HTTP". A
  system now shows that only when one of its operations really goes over HTTP.
- `kestra-migrate vra status` asked you to confirm retire candidates that `corrections.yaml` had already settled.
  It now counts only the ones still open.
- No page listed the retire candidates before an owner answered. "What we need from you" now names them, with how
  to say what starts one or retire it, and each one's workflow page says it's a candidate.
- A `pom.xml` at the top of the export folder (a Build Tools project folder), or another `.xml` file that isn't an
  exported workflow, stopped the run with `BadZipFile`. Project files are now skipped. Another `.xml` file is reported
  and not read.
- In a source tree without `element_info.xml` files, an action module whose name contains a folder name like
  `actions` (`...loadbalancer.actions.common`) got the wrong module. It now gets the right one, so calls to it
  resolve.
- What you read says "recommendation" instead of "verdict": the pages, `BRIEF.md` and the README. The readiness
  report's table is now "Readiness per stage".

### Behavior changes you will notice

- **A workflow that calls code missing from the export is Needs owner input, even when the model rated its spec
  low.** In 1.0.0 it was Manual. The model rates a spec low when key logic sits in code it wasn't given. Once you
  supply that code, the spec can be written again. So the workflow now waits on that answer and gets a place in a
  wave, and its page keeps the model's reason for the low rating. A workflow that needs a hand rewrite for another
  reason (form logic, user interaction, a cycle, a file path, a system with no way to reach it) is still Manual.
- **New questions for the newly named systems.** `answers.yaml` asks for a non-production instance of each new
  system, and whether their read and write labels are right. Answers you gave about "REST hosts" move to
  `no_longer_asked` when those calls now belong to a named system. Nothing is lost, and your other answers stay
  where they are.
- **Waves can regroup.** The waves are built from the systems each workflow writes to. Naming Active Directory,
  Teams, Slack, Tower and Terraform Enterprise can move workflows between waves, and change the order of waves of
  similar size.
- **A workflow that nothing calls or starts is no longer picked as a wave's first of a kind** while there's
  another choice, because it may be retired.
- **The recommendation now says how many workflows need a conversion of their own**, when near-duplicates or
  wrappers make the total larger: "28 of the 39 need a conversion of their own: seven are near-duplicates ...".
- **The first run after upgrading sanitizes the export again**, because 1.0.0's redaction report has no record of
  the systems it saw. Model replies stay cached. A run can still make a few new model calls, because the prompt
  that names the estate's areas lists the newly named systems. The cost estimate shows them before the first one.

## 1.0.0 (2026-10-22)

The first release. It assesses a VMware vRA / vRO estate from its export, on your own machine.

- `kestra-migrate vra run <export folder>` reads a vRO `.package`, a Build Tools source tree or `.workflow` files,
  and a vRA content export, and writes everything to `kestra-assessment/` inside the export folder: the brief
  (`index.html`, and `report/BRIEF.md`), the detail pages, and `your-input/answers.yaml`.
- Code parses the export, behind guards for hostile XML, ZIP, JSON and YAML. The first step writes a sanitized
  copy (host names, IPs, URLs, emails, accounts and credential-shaped values replaced by placeholders;
  configuration element and SecureString values dropped, except a configuration element's numbers, booleans and
  netmasks). Every later step, including the prompts and the pages, reads only that copy. Workflow, action, module, element and file names are kept as written.
  `kestra-migrate vra check-intake` rejects an export that carries anything secret-shaped.
- What each workflow does and what starts it, the call graph, the systems it touches and the Kestra plugins that
  cover them (from a plugin catalog snapshot bundled with the release, its date on every page), and one of eight
  outcomes per workflow: convert, wrapper, near-duplicate, plugin-covered library, needs owner input, manual,
  rebuild or retire, or "needs a model" when a workflow's verdict needs one.
- A migration plan in waves, each starting with its first of a kind, and a recommendation: how many elements
  convert Direct, how many need a Redesign, and what the rest wait on.
- One file to answer, `answers.yaml`, with what is settled together grouped, and a suggested answer where the code
  points to one. Run again after answering. Only what changed is redone, every answer is kept as typed, and the
  plan is signed in the same file.
- One model for every call, with three ways to connect: your Claude Code login, an Anthropic API key, or an
  OpenAI-compatible endpoint (a model you host, or Azure OpenAI and Amazon Bedrock through a proxy). Claude Opus 5.5
  is the default. `kestra-migrate init` writes a commented config file in your home folder, never the export.
- Without a model, the brief still shows everything the code finds, and says what a model would add.
- A cost estimate before the first billed call. Above $20 (`max_cost_usd`), the run asks before going on. Replies
  are cached, so a rerun pays only for what is new.
- Nothing leaves the machine except the calls to the configured model: no telemetry, no update check.
- Installs from GitHub with `uv tool install git+https://github.com/kestra-io/migration-assistant@main` (or pipx,
  or pip, with the same URL; git is needed), on Python 3.10 or later, and updates with
  `uv tool upgrade kestra-migration-assistant`. To stay on this version, install `@v1.0.0` instead. A container
  image for linux/amd64 and linux/arm64, `ghcr.io/kestra-io/migration-assistant`, for an Anthropic API key, an
  OpenAI-compatible endpoint or no model. Each release carries SHA-256 checksums and CycloneDX SBOMs for the package
  and the image.
