# Changelog

Every release of the Kestra Migration Assistant for vRA. Versions follow [semantic versioning](https://semver.org).

## Unreleased

A brief from every run. After a few questions about the estate and the analysis, the brief is written straight
away, with every assumption it makes listed in it. Questions only make it sharper: they are offered, never needed.
Re-running on a folder that 1.1.0 assessed keeps every answer you gave.

### What's new

- **`kestra-migrate vra run` asks its questions in the terminal.** It first says which model it uses (Enter keeps it,
  `c` shows the others with their cost and changes it for this export), what it read in each file, what code the
  workflows call that is not in the package, that it only reads, and that Ctrl+C stops it. Then it asks the few
  questions about the whole estate that the analysis reads (what starts the workflows, where the converted flows
  read their settings and get their secrets, calls to other vRO clusters, what looks out of scope), each choice
  explained in vRO's terms. Before the analysis it says how many model calls it makes and what they cost, and asks.
  Then it writes the brief at once and shows a short summary: the recommendation, the waves, how many workflows
  convert, and where to open it. Last, it offers to ask up to five questions that would sharpen the brief, the ones
  that change it most first. The default is no. Each question has numbered choices, the suggestion first and your
  own words last; a question whose choices are not exclusive takes several numbers (`2, 4`); `s` skips a question,
  `q` saves and stops. Every answer is saved at once. Run it again, and it goes straight to the summary and the
  offer. `--batch` asks nothing, and a run with no terminal never asks.
- **The brief assumes what has a sensible answer.** Non-production: a simulated lab first, then a non-production
  instance where one exists. Certificate and host checks vRO turned off: kept as today, listed as a security note.
  Off limits: nothing. vRA: replaced by Kestra. What the code suggests: taken. Any other question with a suggestion
  takes it. The brief lists each assumption, and each answer taken from a suggestion, under "Assumptions", with how
  to change it in `answers.yaml`. Only a question with no sensible answer (your Kestra edition, whether run history
  can be exported) is left to answer, and none of them is needed to write the brief.
- **Notes to fix after the migration.** The brief lists, before anything else it asks, the security problems kept as
  today (certificate or host checks turned off, a credential written to a log or sent somewhere) and the possible
  defects that make a workflow fail every time.
- **A limit stops the run cleanly.** When your Claude plan's limit runs out, the run stops at once and says so,
  instead of waiting: finished work is kept, and the same command picks up after the limit resets. An API key's rate
  limit gets one short retry first.
- **Each step of a long run shows on its one progress line**, with the time it has taken so far ("Analyzing 46
  workflows: 0 done, 1m 20s"), then "Summarizing 19 groups" and "Preparing the brief".
- **Today's behavior is kept, and not asked about.** The converted flows do what the workflows do today, so one can
  be tested against the other. Possible defects are kept, with the ones that fail every time and the security
  problems listed as notes. A question the code answers today is answered so, vRA's timeouts and failure handling are kept, and a question
  about a value the sanitized copy hides is not asked. Each is written in `answers.yaml` with the answer it takes,
  and you can change it there. The brief lists every possible defect by what it does today (Appendix C).
- **Details of how a flow is built wait for its wave.** Which servers a flow targets, the shape of an input, and the
  review of a spec the model was less sure of are asked when the wave is converted (`waves:` in `answers.yaml`).
- **Ctrl+C stops at once,** during every wait, including the wait after a quota error. The model calls in flight
  end, none starts after them, and every spec already written is kept. The run says how far it got.
- **Faster analysis.** Eight model calls run at once instead of four, and the group summaries run in parallel too.
  Each call's time is the model writing its reply (about a minute on Opus and Haiku alike), not the tool.
- **One question per pattern.** A possible defect or a question about an action several workflows share, or the same
  one on near-duplicate workflows, is asked once for all of them.
- **What the tool assumes instead of asking.** The trust store, retries and counters, and REST hosts made in code
  have a Kestra equivalent. The brief lists each assumption under "Assumptions", and you can override any of them in
  `answers.yaml`.
- **What to add to the package.** When a workflow calls code the export does not hold (an action module at any
  depth, a workflow on another vRO cluster, the configuration elements a module reads at run time), the brief lists
  it under "Add to the package", with what calls it and how to add it. It is no longer a question.
- **vRO-only estates.** With no vRA export, the tool asks once what starts the workflows. If it's an external
  platform on the vRO API, or people in the vRO client, that applies to every workflow nothing in the export starts,
  and none is listed as a retire candidate from the call graph alone.

### Fixes

- Ctrl+C did not stop a run on macOS while the model calls waited (after a quota error, for up to half an hour): the
  run waited on them in a way the terminal's Ctrl+C does not reach. Every wait is now in short steps.
- A path in the terminal could be cut at a hyphen when a line wrapped (`your-` on one line, `input/answers.yaml` on
  the next). Paths and names are no longer split.
- The first screen named a question by its number ("(Q8)"). Questions are named in words there and on the pages.

- The brief could say 8 questions while `answers.yaml` held hundreds. The brief counted the file's entries, where a
  group of questions is one entry, while the file listed every question in it (83 spec questions in one entry on a
  46-workflow estate) and "decide each" opened every workflow's possible defects. The brief and the status line now
  count the empty answers in the file, so they always agree.
- An answered group carried into a fresh folder covered only the questions the first stage to run knew about. It now
  covers the questions the file lists under it, so the same `answers.yaml` gives the same brief anywhere.
- Every file the tool writes is written whole or not at all, so a run stopped half way never leaves `answers.yaml`
  cut short.

### Output changes

- `answers.yaml`: a new `estate` section at the top (estate questions with `options`, assumptions with `assumed`).
  Entries can carry `closes` (what an answer settled) and `settles` (what an open estate question's suggestion would
  settle). New IDs: `estate.<topic>`, `assume.<topic>`, `same.<defect or question>.<what>.<kind>` (a pattern asked
  once), and from re-planning `same.<what>` and `added.<what>`. A defect or question that is now part of a pattern
  keeps its own ID, under the pattern's entry. `missing.<code>` is no longer asked; an answer to it moves to
  `no_longer_asked`. A 1.1.0 file loads as before, and `your_input.load`, `items`, `state` and `signature` keep their
  signatures; `state` now also reports an item another answer settled as answered.
- `data/3-brief/brief.json`: new `questions` (the counts the file shows), `estate_answers`, `assumptions`,
  `package_additions` and `started_outside`. A wave's criterion for missing code closes by `package` (adding it), not
  by a decision.
- `answers.yaml` (also new in 1.2.0): a `waves` section, one entry per wave (`wave.<n>`, `wave.none`) with what is
  asked when it is converted under `each:`. Each assumption lists the items it settles under `each:`, each with
  `assumed:` (the answer it takes) and no answer line. New assumptions: `assume.defects`, `assume.behavior`,
  `assume.placeholders`, `assume.timeouts`, `assume.labels`, `assume.specs`, `assume.host-checks`,
  `assume.non-production` and `assume.suggested` (every other question with a suggestion, listed with the answer it
  takes). `estate.non-production` and `estate.host-verification` are no longer asked; a file that answers them keeps
  them, and its answer still counts. `estate.module.*` is no longer asked (an answer to it still settles its
  module's questions). Preflight's off limits question (`q4-off-limits`) suggests "nothing is off limits", and the vRA
  question (`q9-vra-front-end`) suggests "replace": both are assumed. Possible defects that fail every time or are a
  security problem are kept (`assume.defects`) instead of asked. A kept defect's answer reads `keep, as today`;
  a kept timeout's is its recommendation, with its minutes. An answer that takes several choices joins them with
  `; `.
- `data/3-brief/brief.json`: `questions.later` and `input.later` (what waits for a wave); each of
  `suspected_defects` has `today`, `asked`, `flag` (`security`, `fails` or null: listed as a note), `answer` and
  `assumed`. The `assume.suggested` entry of `assumptions` has `takes`: each answer taken, with its question.
- When vRA is replaced, the facts only its conversion needs (vRA's image and flavor mappings, who allocates a static
  IP, how a resource no Kestra task creates is made) wait for that wave; a canceled create, day 2 scaling and the
  deployments vRA already made get the suggestion that keeps today's behavior.
- `vra.model`: a plan limit or quota stops the run (`LimitReached`, a `Stopped`, with `LIMIT["message"]`) instead of
  waiting; set `model.WAIT_ON_LIMIT = True` to keep backing off and resuming, as 1.1.0 did. The command exits with 75
  when a limit stopped it.
- `data/run.log` (new): what each step did in detail. `data/model.json` (new): the model chosen in the terminal for
  this export. `data/input-items.json`: each defect and spec question has `sort`.
- The sanitized copy: `schema.org` and a few other public schema hosts are kept, and in a URL whose path is a
  vendor's documented API path only the host is replaced (`https://<HOST-03>/api/now/table/...`). Placeholders can be
  numbered differently from 1.1.0.
- `data/1-assess/patterns.json`: new, the patterns the estate questions are built from.
- The status line reads "The brief is ready. To start <waves>: answer N questions in answers.yaml" and counts
  answers, not entries.

### Behavior changes you will notice

- **A run in a terminal asks the estate questions.** To run it unattended, add `--batch`.
- **Fewer questions, and none needed after the analysis.** On the 46-workflow test estate, the terminal asks 4
  questions before the analysis, then writes the brief; 1.1.0 left 149 answers to fill in `answers.yaml`. The brief
  then offers at most five questions, which you can decline.
- **The run's own lines are shorter.** One line per step, and one progress line while the workflows are analyzed.
  What each step did in detail is in `data/run.log`.
- **Typed in a terminal, `run` asks before any billed call,** whatever the estimate, and also with `--yes`. Add
  `--batch` to run without asking.
- **Answering the estate questions writes the specs again, billed.** The answers are part of each workflow's prompt.
  The cost estimate shows it before the first call. A run with no estate answers sends the same prompts as 1.1.0,
  so its cached replies still count.

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
