# Kestra Migration Assistant for vRA

Assess your VMware vRA and vRO environment before migrating workflows to [Kestra](https://kestra.io).

The Kestra Migration Assistant reads an export of your environment and writes a migration brief: a workflow inventory, a recommendation for each workflow, and a plan organized into waves. It runs on your machine and only reads the export. It never connects to your systems, and it doesn't perform the migration.

## Quickstart

**1. Install** with [uv](https://docs.astral.sh/uv/) (you also need [git](https://git-scm.com/downloads); uv installs Python if you don't have it):

```bash
uv tool install git+https://github.com/kestra-io/migration-assistant@main
```

If your terminal can't find `kestra-migrate` afterwards, run `uv tool update-shell` and open a new terminal.

**2. Put the export in a folder of its own and run the tool on it.** The folder's name becomes the estate's name.

```bash
kestra-migrate vra run ~/exports/acme
```

No export at hand? Download the [sample load balancer export](https://kestra.io/kestra-migration-assistant-sample-vra-load-balancer.zip) (a vRA catalog item and four vRO workflows), unzip it, and run the tool on the folder it creates.

**3. Read the brief.** The run ends by printing the brief's link and offering to open it in your browser. It's at `kestra-assessment/index.html` inside the export folder.

That's all you need for a first result. If you're logged in to Claude Code, the tool uses that login; otherwise it uses `ANTHROPIC_API_KEY` if it's set, and without either it runs with no model (see [Choose a model](#choose-a-model)). With a model, it tells you how many model calls the analysis takes and what they cost, and asks before it spends anything.

**Working with a sensitive export?** Run `kestra-migrate vra run ~/exports/acme --no-model` first, then check the redaction report and the sanitized copy before you use an external model. [What is sent to the model](#what-is-sent-to-the-model) says what to look for.

## Learn more

- **[The vRA migration guide](https://kestra.io/resources/migration/vra?utm_source=migration-assistant&utm_medium=readme)**: the full method this tool follows, from assessing your estate to switching vRA off.
- **[A sample migration brief](https://kestra.io/resources/migration/vra/assistant?utm_source=migration-assistant&utm_medium=readme)**: see what the tool produces before you run it.
- **Live walkthrough, with Q&A:** from the sample export to a converted flow tested in the VMware Migration Lab. [Europe](https://watch.getcontrast.io/register/kestra-leaving-vra-without-the-big-rewrite), Wednesday, November 4 at 2 PM CET. [Americas](https://watch.getcontrast.io/register/kestra-leaving-vra-without-the-big-rewrite-9061c391), Thursday, November 5 at 11 AM PT.
- **[Get help running it](https://kestra.io/resources/migration/vra/assistant?utm_source=migration-assistant&utm_medium=readme)**, or ask us to run it for you.

## What the assessment does

The tool works from a vRO `.package`, a Build Tools source tree, or individual `.workflow` files, and you can add a vRA export next to them. [INPUTS.md](https://github.com/kestra-io/migration-assistant/blob/main/INPUTS.md) explains each format and how an administrator produces it. The parser has safeguards for malformed and potentially hostile export files.

For each workflow, it finds what the workflow does, what starts it, which systems it calls, and which Kestra plugins may cover those calls. It then gives each workflow one of eight outcomes: convert, wrapper, near-duplicate, plugin-covered library, needs owner input, manual, rebuild, or retire. A workflow whose recommendation needs a model gets "needs a model" instead.

Deterministic code does the parsing, the inventory, the counts and the outcomes. A language model helps read a workflow's intent where its structure isn't enough. Without a model, the brief shows what the code can find and marks the workflows that still need a model.

Before any analysis, the tool writes a sanitized copy of the export, and every later stage reads only that copy. Prompts to the model are built from it too.

## Other ways to install

You can also use `pipx`, or `pip` in a virtual environment with Python 3.10 or later:

```bash
pipx install git+https://github.com/kestra-io/migration-assistant@main
pip install git+https://github.com/kestra-io/migration-assistant@main
```

For the Anthropic API provider, install the `anthropic` extra (the Claude Code and OpenAI-compatible providers don't need it):

```bash
uv tool install 'kestra-migration-assistant[anthropic] @ git+https://github.com/kestra-io/migration-assistant@main'
```

`uv tool upgrade kestra-migration-assistant` updates a uv installation to the latest version on `main` and keeps its extras. `kestra-migrate --version` shows the version you have. To pin a release, replace `@main` with its tag, such as `@v1.2.0`. The tags are on the [releases page](https://github.com/kestra-io/migration-assistant/releases).

## What a run looks like

In a terminal, the run starts with the KESTRA wordmark and the version, then:

1. **The model.** It says which model it uses. Press Enter to go on, or `c` to choose another (each shows its cost compared with the current one). The choice is kept for this export.
2. **What it read.** Each file and what's in it (workflows, actions, configuration and resource elements, catalog items, subscriptions), and how many pieces of code the workflows call that aren't in the package. You can carry on without them: the workflows that call them get less certain results, and the brief lists each one with how to add it. Press Ctrl+C at any time to stop; finished work is kept.
3. **About the estate.** Up to five questions the analysis reads, each asked only when the code shows it matters: what starts the workflows, where the converted flows read their settings, where they get their secrets, whether workflows call other vRO clusters, and what looks out of scope. On our 46-workflow test estate, that's four questions. Each choice says what a Kestra term means in vRO's terms.
4. **Analysis.** It says how many model calls this takes and what they cost, and asks before it spends anything. Then one line shows which step it's on, how far it is and how long it has taken. The detail goes to `kestra-assessment/data/run.log`.
5. **The brief.** It's written straight away, with every assumption it makes listed in it. The terminal shows a short summary (the recommendation and the waves), the brief's location as a `file://` link, and asks `Open the brief now? (Y/n)`. Enter opens it in your browser, and the run ends there.

To answer more questions and sharpen the brief, run the same command again. Your answers are kept. After redoing whatever changed, it asks up to five questions, the ones that change the brief most, writes the brief again, and ends the same way. "Done in" counts the tool's working time, not the time you spend answering.

Each question has numbered choices. The first is what the code or the model suggests, and Enter takes it. The last is your own answer. When the choices aren't exclusive, type several numbers, like `2, 4`. Type `s` to leave a question for someone else, or `q` to save and stop. Every answer is saved in `answers.yaml` as soon as you give it. If the input ends (Ctrl+D) at the cost question, or at a question whose answer leads to a model call, the run stops without billing anything and says "Stopped before anything was billed." At any other question, it saves and stops asking, like `q`.

A question with a sensible answer isn't asked: the brief assumes it. The converted flows do what the workflows do today, so one can be tested against the other, and fixes come afterwards. Possible defects are kept. One that makes its workflow fail every time, or a security problem such as a credential written to a log, is listed in the brief as a note to fix after the migration. Certificate and host checks that vRO turned off stay off, with a security note. Flows are tested in a simulated lab first, then against a non-production instance where one exists. The brief also assumes that nothing is off limits, that Kestra replaces vRA, that Kestra Enterprise Edition is available, and that what the code suggests is right. It covers what's in the export, and its next steps suggest checking each workflow's run history to find the ones that no longer run. A question about a value the sanitized copy hides isn't asked, since the real value is in the export. A detail of how one flow is built waits for the wave that converts it. Each assumption is written in `answers.yaml` with the answer it takes, and you can change any of them there.

If your Claude plan's limit (or your API key's rate limit) runs out during the analysis, the run stops and says so. Finished work is kept, so run the same command again once the limit resets.

To answer only in the file, add `--batch`. A run with no terminal attached never asks either.

## The output folder

The tool writes its output to `kestra-assessment/` inside the export folder, and leaves that folder out when it reads the export. If the export folder isn't writable, choose another location with `--out`.

```
acme/kestra-assessment/
├── index.html     start here: the brief, offline
├── your-input/    answers.yaml: your answers
├── report/        detail pages, rendered reports, and downloadable BRIEF.md
└── data/          stage outputs (not edited) and the model cache (model-cache/)
```

To open the brief yourself: `open ~/exports/acme/kestra-assessment/index.html` on macOS, `xdg-open` on Linux, `start` on Windows.

The brief follows the terminology of the [vRA migration guide](https://kestra.io/resources/migration/vra). It has:

- **Status:** what needs to happen next.
- **Estate inventory:** what's in the export.
- **Recommendation:** whether to convert, use a mixed approach, or rebuild; how many workflows need a conversion of their own once near-duplicates and wrappers are counted out; how many elements convert **Direct**, need a **Redesign**, or wait on a decision; and a table of what each element type becomes in Kestra.
- **What we need from you:** the questions still open, grouped and counted, and the workflows that nothing in the export calls or starts (retire candidates).
- **Notes to fix after the migration:** the security problems kept as today and the possible defects that make a workflow fail every time.
- **Add to the package:** code the workflows call that the export doesn't hold, such as an action module, a workflow on another vRO cluster, or the configuration elements a module reads at run time, with what calls it and how to add it. This isn't a question.
- **Assumptions:** each one the brief makes, and each answer taken from a suggestion, with how to change it.
- **Your answers about the estate:** what you said in the estate questions.
- **The plan:** the proposed waves, with a summary of each.
- **Next steps:** what to do once the brief reads right, such as checking each workflow's run history.

`report/BRIEF.md` holds the same brief with appendices: every element, the entry criteria of each wave, and every possible defect by what it does today.

When the brief reads right, the next step is to talk to Kestra about a proof of concept or the migration: [contact us](https://kestra.io/resources/migration/vra/assistant?utm_source=migration-assistant&utm_medium=readme).

## Answering in answers.yaml

Everything you can answer is in `kestra-assessment/your-input/answers.yaml`: the questions the brief couldn't assume, whatever you skipped, and every assumption. Answer or change them there, then run the command again. Every stage runs again, but only the work a change affects is redone: your answers are preserved, and cached model responses are reused instead of paying for the same analysis twice.

Each item has an `id`, a plain-language `question`, an `answer`, and a `suggested` value when the tool can recommend one. The file has these sections:

- **`estate`:** the questions about the whole estate, with their `options`, and what the tool assumes instead of asking. If you know your estate, you can fill this in before the first run: every run applies it everywhere and asks nothing it settles. An answer can be an option's number, or several numbers when the choices aren't exclusive. Under an answer, `closes` lists the questions it settled; under an open question, `settles` lists what its suggestion would settle. Change an answer, and the questions it closed are asked again. Each assumption lists the items it settles under `each:`, with the answer each takes (`assumed:`). To change one, write `answer:` under it. Leave an assumption's own `answer` empty to keep it, or write what to do instead.
- **`open`:** what's still unanswered, ordered by what the waves depend on. A question that repeats across workflows, such as a possible defect in an action several workflows share, is one entry for all of them.
- **`answered`:** what you've answered.
- **`waves`:** per wave, what's asked when that wave is converted (details of how its flows are built, and the review of a spec the model was less sure of). Answer them then, or now.
- **`no_longer_asked`:** an answer to a question the tool no longer asks, kept with the question as it was asked.

To accept a suggestion, write `yes`. Otherwise, write your answer. On the next run, answered items move from `open` to `answered`, with your text and each question's ID kept. The number of questions the brief shows is the number of empty answers in this file.

If the assessment gets a domain, cluster, integration reach, wrapper or workflow outcome wrong, correct it in `your-input/corrections.yaml`. [INPUTS.md](https://github.com/kestra-io/migration-assistant/blob/main/INPUTS.md) documents the format.

## In a container

The Docker image is `ghcr.io/kestra-io/migration-assistant`, for `linux/amd64` and `linux/arm64`. Its working directory is `/work`: mount the export folder beneath it, under the estate's name.

```bash
cd ~/exports
docker run --rm -it -v "$PWD/acme:/work/acme" ghcr.io/kestra-io/migration-assistant vra run acme --no-model
```

A Claude Code login can't be used inside the container. For a model, pass one of these:

- **Anthropic API:** `-e ANTHROPIC_API_KEY` (set it in your shell first).
- **OpenAI-compatible endpoint:** `-e KESTRA_MIGRATE_PROVIDER=openai-compatible -e KESTRA_MIGRATE_BASE_URL -e KESTRA_MIGRATE_MODEL -e OPENAI_API_KEY`. For an endpoint on your host machine, use `http://host.docker.internal:<port>/v1`, and on Linux add `--add-host=host.docker.internal:host-gateway`.

On Linux, add `--user "$(id -u):$(id -g)"` so the files it writes belong to you. Keep `-it`: with it, the run asks its questions in your terminal and asks before anything is billed, and it ends by naming the brief's path in the folder you mounted. Without an interactive terminal, it asks nothing, and a run over the cost threshold stops unless you pass `--yes`.

## Choose a model

The assistant uses one configured model for its analysis calls, through one of three providers:

| Provider | What it uses |
|---|---|
| `claude-code` | Your Claude Code login, via `claude -p`. No API key required |
| `anthropic` | An Anthropic API key, with the `anthropic` extra installed |
| `openai-compatible` | A base URL and an API key, for a self-hosted model or a service such as Azure OpenAI or Amazon Bedrock through a proxy like [LiteLLM](https://docs.litellm.ai/) |

By default, the tool looks for an installed, logged-in Claude Code CLI, then for `ANTHROPIC_API_KEY`. With neither, it runs without a model and says what analysis a model would still do.

`kestra-migrate init` writes the configuration file, with an explanation of each setting:

- macOS/Linux: `~/.config/kestra-migrate/config.yaml`
- Windows: `%APPDATA%\kestra-migrate\config.yaml`

Only your user can read the file. Settings are resolved in this order: command-line flags, environment variables (`KESTRA_MIGRATE_PROVIDER`, `KESTRA_MIGRATE_MODEL`, `KESTRA_MIGRATE_BASE_URL`), the configuration file, then defaults. API keys come from environment variables named in the configuration. The configuration and the keys are never written to the export folder; the only model setting kept there is the model you choose with `c` in the terminal (`data/model.json`, the provider and model name).

We tested `claude-opus-5-5` (the default), `claude-fable-5-1` and `claude-sonnet-5-5` through Claude Code on a load-balancer estate. All three gave the same recommendations and migration plan. Opus wrote a brief comparable to Fable's at about a third of the cost and half the time, so it's the default. Sonnet cost the least in that test. To use Fable, set `model: claude-fable-5-1` in the config file or pass `--model claude-fable-5-1`.

**For a self-hosted model, configure a context window of at least 32,768 tokens.** A workflow's prompt includes its whole call tree, and Ollama's default of 4,096 tokens can cut a long prompt without saying so. Start it with `OLLAMA_CONTEXT_LENGTH=32768 ollama serve` (or more). The assistant rejects a response when it detects the prompt was cut.

The assistant checks each response against the structure it asked for. If a response doesn't match, it retries once and explains the mismatch; a second wrong response ends that call with an error naming the model. Other models may work, but smaller ones can have more trouble with the structure.

### What a run costs

On our load-balancer test estate (4 workflows and 39 actions), `claude-opus-5-5` made 8 model calls that cost $1.32 at API rates. Most of the cost is the analysis of each workflow, so the total depends on how many workflows there are and how big they are.

Before the first billed call, the tool estimates the cost from the prompts, the number of calls and the model's price. Typed in a terminal, the run always asks then, whatever the estimate and also with `--yes`. With `--batch` or without a terminal, it prints the estimate like this:

```
Cost        Estimate: 8 model calls, about $1.28 at Claude Opus 5.5's API prices. With your Claude Code login this is not billed separately: it counts against your Claude plan.
```

and asks only above the approval threshold, $20 by default. Change it with `max_cost_usd` in the config file, or pass `--yes` to go ahead without asking. Where nobody can answer, as in CI, a run over the threshold stops unless you pass `--yes`.

Cached responses aren't billed again, so a later run estimates only the new work. For a model with no recorded price, including self-hosted ones, the estimate gives the number of calls without a dollar amount.

## What is sent to the model

Each run starts with a **Sanitize** stage, which writes a redacted copy of the export to `kestra-assessment/data/0-preflight/sanitized/`. Sanitize is the only stage that reads the original export. Preflight, Assess, Understand, Brief and report generation read the sanitized copy, and Sanitize runs again before them when the export changes.

Prompts are built from the sanitized copy. Here's how the export's content is handled:

- **Replaced with consistent placeholders:** hostnames, IP addresses, URLs, email addresses, user and service accounts, and credential-shaped values such as passwords, tokens, keys and JWTs. Each value gets a placeholder that's reused wherever it appears, such as `<HOST-01>` or `<IP-02>`.
  - For vRA exports, owners, subscribers, principals, tenant information, network names, tags and CIDRs are replaced too.
  - Addresses beginning with 255 are netmasks, not hosts, and are kept (for example, `255.255.255.0`).
  - In a URL to one of your servers whose path is a vendor's documented API path (ServiceNow's `/api/now/`, F5's `/mgmt/tm/`, Infoblox's `/wapi/`, vCenter's, Avi's and a few others), only the host is replaced, so the copy still says which API the code calls: `https://<HOST-03>/api/now/table/...`. A webhook URL, whose path is its secret, is still replaced whole.
- **Kept, because they identify nothing:** public schema and namespace hosts, such as `www.w3.org`, `schema.org` (in a Teams card's `@context`), `json-schema.org`, `purl.org` and `schemas.microsoft.com`.
- **Removed:** text values in configuration elements, and every SecureString value. Their names and types stay. Package signer and owner identities, signatures and certificates are removed too.
- **Recorded as a system, never as a value:** when a replaced URL or hostname belongs to a system the tool recognizes by its host, such as a Microsoft Teams or Slack incoming webhook, the sanitizer records which system it was, against the placeholder and against the configuration element attribute that held it (for example, `slackWebhookUrl: slack`). Later stages use that record to name the system. The value itself isn't kept.
- **Kept from configuration elements:** numbers and booleans, when the attribute's type says so, and netmasks (`255.255.255.0` or `/24`). A numeric monitoring timeout of `16` stays; a string holding `16` is removed because its type is text. SecureString values are removed whatever they hold.
- **Sent as written:** workflow, action, module, category, element, attribute and item names; export file and package names; scripts and workflow structure; vRA IDs and event topics; and the specifications of workflows that share state with the one being analyzed. Module and category names stay even when they look like hostnames (such as `com.acme.net`), because the tool needs them to resolve code identifiers and calls. **Names that identify your organization can therefore appear in prompts.**

The reports also use the sanitized copy, so a replaced vRA network tag appears as `<NETWORK-01>`. The redaction report shows masked previews such as `ab… (12 chars)`, and model responses are cached locally. Because scripts, names and some identifiers are kept, review the sanitized output before you send it to a hosted model or share the report. A model may also add information from its own knowledge to a response.

The provider decides where prompts are processed:

| Provider | Where prompts go |
|---|---|
| `claude-code` | Anthropic, through your Claude Code account and under its terms |
| `anthropic` | The Anthropic API, under the terms of the organization that owns the key |
| `openai-compatible` | The endpoint in `base_url`. A model hosted on your own network keeps prompt processing there |

The tool has no telemetry and no update check. Apart from requests to the configured model provider, it sends nothing off the machine. Model responses are cached in `kestra-assessment/data/model-cache/`.

**Sanitization has limits.** It relies on patterns, so it can miss a hostname without a dot or a known domain suffix, a password in an unusually named variable, or a secret inside a binary resource. It has been tested against public vRO libraries and synthetic fixtures, not yet against a real customer export.

Before you send an assessment to an external model, we recommend running with `--no-model` and reviewing `kestra-assessment/data/0-preflight/redaction-report.md` together with the sanitized copy. You can also run entirely without a model, or use a model hosted on your network. `kestra-migrate vra check-intake <path>` checks an incoming export for secret-shaped values and exits with code 1 if it finds any. That check is pattern-based too.

## Commands

| Command | What it does |
|---|---|
| `kestra-migrate init` | Create the model configuration file |
| `kestra-migrate vra run <export folder> [--out <folder>] [--no-model] [--batch] [--provider ...] [--model ...] [--base-url ...] [--yes]` | Run Sanitize, Preflight, Assess, the questions about the estate, Understand, Brief and report generation. In a terminal, ask the questions there. With `--batch`, ask nothing and read `answers.yaml` only. With `--no-model`, use cached model responses where there are any, make no new model calls, and say what still needs a model |
| `kestra-migrate vra status <export folder> [--out <folder>]` | Show the status of each stage and the next step |
| `kestra-migrate vra check-intake <path>` | Check an incoming export for secret-shaped values; exit with code 1 and a masked report if any are found |

## Help

Need help using the assistant, or want us to run the assessment for you? [Contact us](https://kestra.io/resources/migration/vra/assistant?utm_source=migration-assistant&utm_medium=readme).

For bugs and feature requests, [open a GitHub issue](https://github.com/kestra-io/migration-assistant/issues/new/choose). **Never attach customer exports to public issues.** This repository isn't accepting external contributions; see [CONTRIBUTING.md](https://github.com/kestra-io/migration-assistant/blob/main/CONTRIBUTING.md).

Report security vulnerabilities privately through [GitHub's security advisory form](https://github.com/kestra-io/kestra/security/advisories/new) on the main Kestra repository. Don't disclose them in a public issue.

## License

Licensed under Apache License 2.0. See [LICENSE](https://github.com/kestra-io/migration-assistant/blob/main/LICENSE). Bundled third-party files keep their own licenses, listed in [NOTICE](https://github.com/kestra-io/migration-assistant/blob/main/NOTICE). The Kestra name and logo are trademarks of Kestra Technologies and aren't covered by the software license.
