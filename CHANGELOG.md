# Changelog

Every release of the Kestra Migration Assistant for vRA. Versions follow [semantic versioning](https://semver.org).

## 1.2.0 (2026-11-03)

Every run now ends with a brief. In a terminal, the tool asks a few questions about the estate, runs the analysis
and writes the brief straight away, with every assumption it makes listed in it. Questions only make the brief
sharper: run the same command again to be asked them, and none is needed. Re-running 1.2.0 on a folder that 1.1.0
assessed keeps every answer you gave.

### What's new

- **`kestra-migrate vra run` asks its questions in the terminal.** It starts with the KESTRA wordmark and the
  version (in Kestra's purple, fading to lavender, where the terminal has color). It says which model it uses (Enter
  keeps it, `c` lists the others with their cost and changes it for this export), what it read in each file, how
  many pieces of code the workflows call that aren't in the package, that it only reads, and that Ctrl+C stops it.
  Then it asks the few questions about the whole estate that the analysis reads: what starts the workflows, where
  the converted flows read their settings and get their secrets, whether workflows call other vRO clusters, and what
  looks out of scope (four on our 46-workflow test estate). Each choice says what a Kestra term means in vRO's terms.
- **It asks before it spends anything.** Before the analysis, it says how many model calls it makes and what they
  cost, and waits for Enter. In a terminal it asks whatever the estimate, also with `--yes`.
- **It stops at the brief.** It writes the brief at once and shows a short summary (the recommendation, the waves,
  how many workflows convert), the brief's `file://` link and `Open the brief now? (Y/n)`; Enter opens it in your
  browser. Run the same command again, and it asks up to five questions, the ones that change the brief most first,
  writes the brief again and ends the same way.
- **Questions look the same everywhere.** Numbered choices, the suggestion first (Enter takes it) and your own words
  last. A question whose choices aren't exclusive takes several numbers (`2, 4`). `s` skips a question, `q` saves
  and stops, and every answer is saved at once. `--batch` asks nothing, and a run with no terminal never asks.
- **The brief assumes what has a sensible answer.** The converted flows do what the workflows do today, so one can
  be tested against the other: possible defects are kept, certificate and host checks vRO turned off stay off, and
  vRA's timeouts and failure handling are kept. Flows are tested in a simulated lab first, then against a
  non-production instance where one exists. Nothing is off limits, Kestra replaces vRA, Kestra Enterprise Edition is
  available, and any other question with a suggestion takes it. The brief lists each assumption, and each answer
  taken from a suggestion, under "Assumptions", with how to change it in `answers.yaml`. Only a question with no
  sensible answer (who owns each area, the check of a spec) is left to you, and none is needed to write the brief.
- **Notes to fix after the migration.** The brief lists the security problems kept as today (certificate or host
  checks turned off, a credential written to a log or sent somewhere) and the possible defects that make a workflow
  fail every time.
- **Add to the package.** When a workflow calls code the export doesn't hold (an action module at any depth, a
  workflow on another vRO cluster, the configuration elements a module reads at run time), the brief lists it, with
  what calls it and how to add it. It's no longer a question.
- **Every possible defect, by what it does today,** in the brief's Appendix C and on each workflow's page.
- **A spec check quotes the brief.** "The brief says Add User to AD Group adds one user to one managed Active
  Directory group. Is that right?", one per workflow, only in a run after the brief exists, and never for a workflow
  that is retired or left out of the migration.
- **One question per pattern.** A possible defect or a question about an action several workflows share, or the
  same one on near-duplicate workflows, is asked once for all of them.
- **Details of how a flow is built wait for its wave.** Which servers a flow targets, the shape of an input, and the
  review of a spec the model was less sure of are asked when the wave is converted.
- **Three questions are no longer asked:** whether the export is the whole estate (the brief says it covers what is
  in the export), whether anyone tried to migrate it before, and whether twelve months of run history can be
  exported (the brief's next steps suggest checking each workflow's run history). An answer 1.1.0 kept for one stays
  in `answers.yaml`, under `no_longer_asked`.
- **vRO-only estates.** With no vRA export, the tool asks once what starts the workflows. If it's an external
  platform on the vRO API, or people in the vRO client, that applies to every workflow nothing in the export starts,
  and none is listed as a retire candidate from the call graph alone.
- **A limit stops the run cleanly.** When your Claude plan's limit runs out, the run stops at once and says so;
  finished work is kept, and the same command picks up after the limit resets. An API key's rate limit gets one
  short retry first.
- **One progress line** for each long step, with the time so far ("Analyzing 46 workflows: 0 done, 1m 20s"). What
  each step did in detail goes to `kestra-assessment/data/run.log`.
- **Faster analysis.** Eight model calls run at once instead of four, and the group summaries run in parallel too.
- **Tested on Linux, macOS and Windows.** Each release installs its wheel in a clean environment and runs it on
  Ubuntu with Python 3.10, macOS with Python 3.12 and Windows with Python 3.14, and is published only if all three
  pass.

### Fixes

- **End of input never spends.** Ctrl+D (or stdin closing) at the cost question counted as Enter, which goes on. It
  now stops before any billed call and says "Stopped before anything was billed." The same at a question whose
  answer leads to a model call. At the model question it crashed the run; there, as at every other question, it now
  saves and stops asking, like `q`.
- **Windows:** the tool wrote its files as UTF-8 but read them back in the system's code page, so `answers.yaml`
  with an accent or a dash could come back garbled. Every file is now read and written as UTF-8, and output to a
  pipe never stops on a character the code page lacks.
- A folder answered with 1.1.0 stopped on its first interactive run with 1.2.0 when a "keep them all" for a
  workflow's possible defects had nothing left to ask. That answer now stays as it was.
- In the container, the run ended with a `file:///work/...` link that only exists inside the container. It now
  names the brief's path in the folder you mounted.
- Ctrl+C didn't stop a run on macOS while model calls waited (after a quota error, for up to half an hour). Every
  wait is now in short steps, so it stops at once; the calls in flight end, and every spec already written is kept.
- The brief could say 8 questions while `answers.yaml` held hundreds: it counted the file's entries, where a group
  of questions is one entry. The brief and the status line now count the empty answers in the file, so they agree.
- An answered group carried into a fresh folder covered only the questions the first stage to run knew about. It
  now covers the questions the file lists under it, so the same `answers.yaml` gives the same brief anywhere.
- Every file the tool writes is written whole or not at all, so a run stopped half way never leaves `answers.yaml`
  cut short.
- A path in the terminal could be cut at a hyphen when a line wrapped. Paths and names are no longer split.
- The first screen named a question by its number ("(Q8)"). Questions are named in words.

### Output changes

What a program reading the tool's output, or a person editing `answers.yaml`, needs to know.

**Signing is removed.** Migrations are agreed with Kestra directly, so the assessment has no signature.
`answers.yaml` has no `signed_for` field (a 1.1.0 file that has one still loads; the value is ignored and not
written back), `your_input.signature` and `your_input.set_signature` are gone, and no wave has an entry criterion
about a signature. `brief.json` has no `approval` or `input.sign`. Where the brief said to sign, it now says the
next step is to talk to Kestra about a proof of concept or the migration, with the help link.

**`answers.yaml`, new sections and fields:**

- `estate` (at the top): the estate questions (`estate.<topic>`) with their `options`, and the assumptions
  (`assume.<topic>`) with `assumed`. An answer can carry `closes` (what it settled), and an open estate question
  `settles` (what its suggestion would settle). An answer that takes several choices joins them with `; `.
- `waves`: one entry per wave (`wave.<n>`, `wave.none`), with what's asked when it's converted under `each:`.
- New IDs: `same.<defect or question>.<what>.<kind>` (a pattern asked once) and, from re-planning, `same.<what>`
  and `added.<what>`. A defect or question in a pattern keeps its own ID, under the pattern's entry.
- `missing.<code>` is no longer asked (it's in "Add to the package"); an answer to it moves to `no_longer_asked`,
  as do answers to the three questions no longer asked (`q1-scope`, `q5-prior-attempts`, `q7-execution-history`).
- A 1.1.0 file loads as before, and `your_input.load`, `items` and `state` keep their signatures; `state` also
  reports an item another answer settled as answered.

**The assumptions.** Each lists the items it settles under `each:`, each with `assumed:` (the answer it takes) and
no answer line; write `answer:` under one to override it. They are `assume.defects` (every possible defect is kept;
the answer reads `keep, as today`), `assume.behavior`, `assume.placeholders`, `assume.timeouts` (a kept timeout's
answer is its recommendation, with its minutes), `assume.labels`, `assume.specs`, `assume.host-checks`,
`assume.non-production` and `assume.suggested` (every other question with a suggestion, with the answer it takes).
The off limits question (`q4-off-limits`) is assumed "nothing is off limits", and the vRA question
(`q9-vra-front-end`) "replace", so a brief now plans the vRA replacement waves by default; the facts only their
conversion needs (image and flavor mappings, who allocates a static IP, how a resource no Kestra task creates is
made) wait for that wave. `estate.non-production`, `estate.host-verification` and `estate.module.*` are no longer
asked; a file that answers them keeps its answers, and they still count.

**The brief's new sections:** "Notes to fix after the migration", "Add to the package", "Assumptions", "Your
answers about the estate" and "Next steps", and in `BRIEF.md`, Appendix C, every possible defect by what it does
today. The status line reads "The brief is ready. To start <waves>: answer N questions in answers.yaml".

**`data/3-brief/brief.json`:** new `questions` (the counts the file shows, with `later` for what waits for a wave),
`input.later`, `estate_answers`, `assumptions` (the `assume.suggested` entry has `takes`: each answer taken, with
its question), `package_additions` and `started_outside`. Each of `suspected_defects` has `today`, `asked`, `flag`
(`security`, `fails`, or null), `answer` and `assumed`. A wave's criterion for missing code closes by `package`
(adding the code), not by a decision.

**Public values the sanitizer no longer redacts.** `schema.org`, `purl.org`, `schemas.openxmlformats.org`,
`xmlns.com` (each with or without `www.`) join the public schema hosts already kept (`www.w3.org`,
`json-schema.org`, `schemas.microsoft.com` and the like). In a URL to your own server whose path is a vendor's
documented API path (ServiceNow, F5, Infoblox, AWX, Avi, vCenter, Vault, Nuage, vRA), only the host is replaced:
`https://<HOST-03>/api/now/table/...`. A webhook URL is still replaced whole. The placeholders in the sanitized copy
can therefore be numbered differently from 1.1.0, and a model cache made with 1.1.0 misses the prompts that held
those URLs.

**Other files:** `data/run.log` (new, what each step did), `data/model.json` (new, the model chosen with `c` in the
terminal for this export), `data/1-assess/patterns.json` (new, the patterns the estate questions are built from),
and `data/input-items.json` (each defect and spec question has `sort`).

**`vra.model`:** a plan limit or quota stops the run (`LimitReached`, a `Stopped`, with `LIMIT["message"]`) instead
of waiting; set `model.WAIT_ON_LIMIT = True` to keep backing off and resuming, as 1.1.0 did. The command exits with
75 when a limit stopped it. `model.pause` and `model.wait_for` are new. `model.Gate`'s `confirm` may return None
(the input ended), which stops the run like a no.

### Behavior changes you will notice

- **A run in a terminal asks the estate questions.** To run it unattended, add `--batch`.
- **Fewer questions, and none needed.** On the 46-workflow test estate, the terminal asks 4 questions before the
  analysis, then writes the brief; 1.1.0 left 149 answers to fill in `answers.yaml`.
- **Typed in a terminal, `run` asks before any billed call,** whatever the estimate and also with `--yes`. Add
  `--batch` to run without asking.
- **Answering the estate questions writes the specs again, billed,** because the answers are part of each
  workflow's prompt. The cost estimate shows it before the first call. A run with no estate answers sends the same
  prompts as 1.1.0, so its cached replies still count.

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

- Ctrl+C did not stop a run on macOS while the model calls waited (after a quota error, for up to half an hour): the
  run waited on them in a way the terminal's Ctrl+C does not reach. Every wait is now in short steps.
- A path in the terminal could be cut at a hyphen when a line wrapped (`your-` on one line, `input/answers.yaml` on
  the next). Paths and names are no longer split.
- The first screen named a question by its number ("(Q8)"). Questions are named in words there and on the pages.

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
