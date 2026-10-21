# Kestra Migration Assistant for vRA

Assess your VMware vRA and vRO environment before migrating workflows to [Kestra](https://kestra.io).

The Kestra Migration Assistant reads an export of your existing environment and produces a workflow inventory, migration recommendations, a plan organized into waves, and a brief for the people who need to review and approve it. It works from a vRO `.package`, a Build Tools source tree, or individual `.workflow` files. You can include a vRA export as well. The parser includes safeguards for malformed and potentially hostile export files.

The tool identifies what each workflow does, what triggers it, which systems it interacts with, and which Kestra plugins may cover those integrations. It then assigns each workflow one of eight outcomes: convert, wrapper, near-duplicate, plugin-covered library, needs owner input, manual, rebuild, or retire. A workflow whose verdict needs a model gets "needs a model" instead. The assessment helps you decide what to migrate, what to consolidate, and where you still need input from an owner. It does not perform the migration.

Parsing, inventory, counts, and outcomes are controlled by deterministic code. A language model helps interpret workflow intent where the structure alone isn't enough. You can also run the assessment without a model. In that case, the report includes what the code can determine and marks workflows that still need model analysis.

The tool runs locally. Before analysis, it creates a sanitized copy of the export, which is the only copy later stages read. When you enable a model, prompts are built from that sanitized copy. See [What is sent to the model](#what-is-sent-to-the-model) for what the sanitizer removes, what it preserves, and where you should review the results before sending them to an external provider.

## Learn more

- **[The vRA migration guide](https://kestra.io/resources/migration/vra?utm_source=migration-assistant&utm_medium=readme)**: the full method this tool follows, from assessing your estate to switching vRA off.
- **[A sample migration brief](https://kestra.io/resources/migration/vra/assistant?utm_source=migration-assistant&utm_medium=readme)**: see what the tool produces before you run it.
- **Live walkthrough, with Q&A:** from the sample export to a converted flow tested in the VMware Migration Lab. [Europe](https://watch.getcontrast.io/register/kestra-leaving-vra-without-the-big-rewrite), Wednesday, November 4 at 2 PM CET. [Americas](https://watch.getcontrast.io/register/kestra-leaving-vra-without-the-big-rewrite-9061c391), Thursday, November 5 at 11 AM PT.
- **[Get help running it](https://kestra.io/resources/migration/vra/assistant?utm_source=migration-assistant&utm_medium=readme)**, or ask us to run it for you.

## Quickstart

### 1. Install

Install from GitHub using [uv](https://docs.astral.sh/uv/). You'll also need [git](https://git-scm.com/downloads). `uv` can install Python if it isn't already available.

```bash
uv tool install git+https://github.com/kestra-io/migration-assistant@main
```

You can also use `pipx`, or `pip` in a virtual environment with Python 3.10 or later:

```bash
pipx install git+https://github.com/kestra-io/migration-assistant@main
pip install git+https://github.com/kestra-io/migration-assistant@main
```

If your terminal can't find `kestra-migrate` after a `uv` installation, run `uv tool update-shell` and open a new terminal.

For the Anthropic API provider, install the `anthropic` extra:

```bash
uv tool install 'kestra-migration-assistant[anthropic] @ git+https://github.com/kestra-io/migration-assistant@main'
```

The Claude Code and OpenAI-compatible providers don't require that extra. See [Choose a model](#choose-a-model) for configuration details.

To update an installation made with `uv`, run `uv tool upgrade kestra-migration-assistant`. This follows the latest version on `main` and preserves the installed extras. Check your installed version with `kestra-migrate --version`. For a pinned release, replace `@main` in the installation URL with a release tag such as `@v1.0.0`. Available tags are listed on the [releases page](https://github.com/kestra-io/migration-assistant/releases).

### 2. Run it on an export folder

Place the export in its own folder. The folder name becomes the name of the estate in the assessment. [INPUTS.md](https://github.com/kestra-io/migration-assistant/blob/main/INPUTS.md) explains the supported export formats and how an administrator can produce them.

```bash
kestra-migrate vra run ~/exports/acme
```

If Claude Code is installed and you're logged in, the tool uses that login by default. Otherwise, it checks for an Anthropic API key. If neither is available, the assessment runs without a model. Before its first billed model call, it shows an estimated cost. See [Choose a model](#choose-a-model) for other options.

**Working with sensitive exports?** Run the command with `--no-model` first. You can then inspect the redaction report and sanitized copy before running it with an external model. The [data handling section](#what-is-sent-to-the-model) explains what to look for.

No export at hand? Download the [sample load balancer export](https://kestra.io/kestra-migration-assistant-sample-vra-load-balancer.zip) (a vRA catalog item and four vRO workflows), unzip it into a folder, and run the tool on that.

### 3. Open the report

```bash
open ~/exports/acme/kestra-assessment/index.html        # macOS; xdg-open on Linux, start on Windows
```

The tool writes its output to `kestra-assessment/` inside the export folder. If that folder isn't writable, use `--out` to choose another location. The assessment folder is excluded when the tool reads the export.

```
acme/kestra-assessment/
├── index.html     start here: the brief, offline
├── your-input/    answers.yaml: your answers and sign-off
├── report/        detail pages, rendered reports, and downloadable BRIEF.md
└── data/          stage outputs (not edited) and the model cache (model-cache/)
```

### 4. Answer the open questions and run again

Open `kestra-assessment/your-input/answers.yaml` and answer the questions that need an owner's input. Then rerun the same command:

```bash
kestra-migrate vra run ~/exports/acme
```

Every stage runs again, but only work affected by a change is redone. The tool preserves your answers and reuses cached model responses instead of paying for the same analysis again. Once the open questions are resolved, you can sign off on the plan in `answers.yaml`.

## In a container

The Docker image is `ghcr.io/kestra-io/migration-assistant`, available for `linux/amd64` and `linux/arm64`. The container uses `/work` as its working directory. Mount the export folder beneath `/work` using the estate name. Make sure `ANTHROPIC_API_KEY` is set in your shell, then pass it into the container:

```bash
cd ~/exports
docker run --rm -it -v "$PWD/acme:/work/acme" -e ANTHROPIC_API_KEY \
  ghcr.io/kestra-io/migration-assistant vra run acme
```

Claude Code login isn't supported inside the container. You can use:

- **Anthropic API:** Pass `-e ANTHROPIC_API_KEY`.
- **OpenAI-compatible endpoint:** Pass `-e KESTRA_MIGRATE_PROVIDER=openai-compatible -e KESTRA_MIGRATE_BASE_URL -e KESTRA_MIGRATE_MODEL -e OPENAI_API_KEY`. If the endpoint runs on your host machine, use `http://host.docker.internal:<port>/v1`. On Linux, add `--add-host=host.docker.internal:host-gateway`.
- **No model:** Add `--no-model` to the command.

On Linux, add `--user "$(id -u):$(id -g)"` so the generated files belong to your user. Keep `-it` when using a model: if the estimated cost exceeds the configured threshold, the tool asks for confirmation. Without an interactive terminal, it stops instead unless you pass `--yes`.

## Choose a model

The assistant uses one configured model for its analysis calls. It supports three providers:

| Provider | What it uses |
|---|---|
| `claude-code` | Your Claude Code login, via `claude -p`. No API key required |
| `anthropic` | An Anthropic API key, with the `anthropic` extra installed |
| `openai-compatible` | A base URL and an API key, for a self-hosted model or a service such as Azure OpenAI or Amazon Bedrock through a proxy like [LiteLLM](https://docs.litellm.ai/) |

By default, the tool checks for an installed, authenticated Claude Code CLI. If that's unavailable, it uses `ANTHROPIC_API_KEY` when present. Otherwise, it runs without a model and identifies the analysis a model would still need to perform.

Run `kestra-migrate init` to generate the configuration file, including an explanation of each setting:

- macOS/Linux: `~/.config/kestra-migrate/config.yaml`
- Windows: `%APPDATA%\kestra-migrate\config.yaml`

The file is readable only by your user. Settings are resolved in this order: command-line flags, environment variables (`KESTRA_MIGRATE_PROVIDER`, `KESTRA_MIGRATE_MODEL`, `KESTRA_MIGRATE_BASE_URL`), the configuration file, then defaults. API keys are supplied through environment variables named in the configuration. Model configuration isn't written to the export folder.

We tested `claude-opus-5-5` (the default), `claude-fable-5-1`, and `claude-sonnet-5-5` through Claude Code on a load-balancer estate. All three produced the same verdicts and migration plan in that test. Opus produced a brief comparable to Fable's, at roughly one-third of the cost and half the time, so we selected it as the default. Sonnet had the lowest cost in that comparison. To choose Fable, set `model: claude-fable-5-1` in the config file or pass `--model claude-fable-5-1`.

**For self-hosted models, configure a context window of at least 32,768 tokens.** A workflow prompt includes its full call tree. Ollama's default context is 4,096 tokens and can truncate longer prompts silently. Start it with `OLLAMA_CONTEXT_LENGTH=32768 ollama serve` (or a larger value). The assistant rejects a response if it detects that the prompt was truncated.

The assistant validates each model response against the required structure. If a response is invalid, it retries once and explains the mismatch. A second invalid response ends that call with an error identifying the model. Other models may work, but smaller models can have more difficulty meeting the required structure.

### What a run costs

In our load-balancer test estate (4 workflows and 39 actions), `claude-opus-5-5` used 8 model calls and cost $1.32 at API rates. A large part of the cost comes from analyzing individual workflows, so the total depends on both the number of workflows and their size.

Before the first billed call, the tool estimates the cost based on the prompts, number of calls, and configured model pricing. For example:

```
Cost        Estimate: 8 model calls, about $1.28 at Claude Opus 5.5's API prices. With your Claude Code login this is not billed separately: it counts against your Claude plan.
```

The default approval threshold is $20. Above that amount, the tool asks before proceeding. Change the threshold using `max_cost_usd` in the config file, or pass `--yes` to proceed without the prompt. In a noninteractive environment, such as CI, a run over the threshold stops unless you pass `--yes`.

Cached responses don't trigger new model calls, so subsequent runs estimate only the additional work. For models without a recorded price, including self-hosted models, the estimate shows the number of calls without a dollar amount.

## What is sent to the model

The assistant starts each run with a **Sanitize** stage, which writes a redacted copy of the export to `kestra-assessment/data/0-preflight/sanitized/`. Sanitize is the only stage that reads the original export. Preflight, Assess, Understand, Brief, and report generation use the sanitized copy. If the export changes, Sanitize runs again before those stages.

Prompts are assembled from the sanitized copy. Here's how source content is handled:

- **Replaced with consistent placeholders:** Hostnames, IP addresses, URLs, email addresses, user and service accounts, and credential-shaped values such as passwords, tokens, keys, and JWTs. Each detected value receives a placeholder that is reused across its occurrences, such as `<HOST-01>` or `<IP-02>`.
  - For vRA exports, owners, subscribers, principals, tenant information, network names, tags, and CIDRs are also replaced.
  - Addresses beginning with 255 are treated as netmasks rather than host addresses and are preserved (for example, `255.255.255.0`).
- **Removed:** Text values in configuration elements and all SecureString values. Their names and types remain. The sanitizer also removes package signer and owner identities, signatures, and certificates.
- **Preserved from configuration elements:** Numeric and boolean values when the attribute type identifies them as such, plus netmasks (`255.255.255.0` or `/24`). For example, a numeric monitoring timeout of `16` remains. A string containing `16` is removed because its type is text. SecureString values are removed regardless of their contents.
- **Sent as written:** Workflow, action, module, category, element, attribute, and item names; export file and package names; scripts and workflow structure; vRA IDs and event topics; and specifications of workflows that share state with the workflow being analyzed. Module and category names remain intact even if they resemble hostnames (such as `com.acme.net`), because the tool uses them to resolve code identifiers and calls. **Organization-identifying names can therefore appear in model prompts.**

The generated reports also use the sanitized copy. A replaced vRA network tag, for example, appears as `<NETWORK-01>`. The redaction report uses masked previews such as `ab… (12 chars)`, and model responses are cached locally. Because scripts, names, and some identifiers are preserved, review the sanitized output before sending it to a hosted model or sharing the report. A model may also include information from its own knowledge in a response.

The provider determines where prompts are processed:

| Provider | Where prompts go |
|---|---|
| `claude-code` | Anthropic, through your Claude Code account and under its terms |
| `anthropic` | The Anthropic API, under the terms of the organization that owns the key |
| `openai-compatible` | The endpoint specified by `base_url`. A model hosted on your own network keeps prompt processing there |

The tool has no telemetry or update check. Apart from requests to the configured model provider, it doesn't send data off the machine. Model responses are cached in `kestra-assessment/data/model-cache/`.

**Sanitization has limits.** It relies on patterns and can miss a hostname without a dot or recognized domain suffix, a password stored in an unusually named variable, or a secret embedded in a binary resource. It has been tested against public vRO libraries and synthetic fixtures, but not yet against a real customer export.

Before sending an assessment to an external model, we recommend running with `--no-model` and reviewing `kestra-assessment/data/0-preflight/redaction-report.md` together with the sanitized copy. You can also run entirely without a model or use a model hosted on your network. `kestra-migrate vra check-intake <path>` checks an incoming export for secret-shaped values and exits with code 1 if it detects any. That check is also pattern-based.

## Commands

| Command | What it does |
|---|---|
| `kestra-migrate init` | Create the model configuration file |
| `kestra-migrate vra run <export folder> [--out <folder>] [--no-model] [--provider ...] [--model ...] [--base-url ...] [--yes]` | Run Sanitize, Preflight, Assess, Understand, Brief, and report generation. With `--no-model`, use cached model responses where available, but make no new model calls; identify what still needs model analysis |
| `kestra-migrate vra status <export folder> [--out <folder>]` | Show the status of each stage and the next action |
| `kestra-migrate vra check-intake <path>` | Check an incoming export for secret-shaped values; exit with code 1 and a masked report if any are detected |

## The brief

Open `kestra-assessment/index.html` to review the assessment offline. The brief follows the terminology in the [vRA migration guide](https://kestra.io/resources/migration/vra) and includes:

- **Status:** What needs to happen next.
- **Estate inventory:** What's included in the export.
- **Recommendation:** Whether to convert, use a mixed approach, or rebuild; counts of elements that can convert **Direct**, require a **Redesign**, or are waiting on a decision; and a table showing what each element type becomes in Kestra.
- **Owner input:** Outstanding questions, grouped and counted.
- **Migration plan:** The proposed waves, with a summary of each.

`report/BRIEF.md` contains the same brief with an appendix.

### Answer questions and approve the plan

`your-input/answers.yaml` holds two lists, `open` and `answered`, plus a `signed_for` field. Open questions are ordered by what the migration waves depend on. Each item includes an `id`, a plain-language `question`, an `answer`, and, when the tool can recommend one, a `suggested` value.

To accept a suggestion, write `yes` as the answer. Otherwise, provide your answer directly. On the next run, the tool moves answered items from `open` to `answered`, preserves your text, and keeps each question's ID. When you're ready to approve the plan, set `signed_for` to `wave 1` or `full plan`.

If the assessment misidentifies a domain, cluster, integration reach, wrapper, or workflow outcome, add `your-input/corrections.yaml` with the corrections. [INPUTS.md](https://github.com/kestra-io/migration-assistant/blob/main/INPUTS.md) documents the expected format.

## Help

Need help using the assistant, or want us to run the assessment for you? [Contact us](https://kestra.io/resources/migration/vra/assistant?utm_source=migration-assistant&utm_medium=readme).

For bugs and feature requests, [open a GitHub issue](https://github.com/kestra-io/migration-assistant/issues/new/choose). **Never attach customer exports to public issues.** This repository isn't accepting external contributions; see [CONTRIBUTING.md](https://github.com/kestra-io/migration-assistant/blob/main/CONTRIBUTING.md).

Report security vulnerabilities privately through [GitHub's security advisory form](https://github.com/kestra-io/kestra/security/advisories/new) on the main Kestra repository. Don't disclose them in a public issue.

## License

Licensed under Apache License 2.0. See [LICENSE](https://github.com/kestra-io/migration-assistant/blob/main/LICENSE). Bundled third-party files retain their own licenses, listed in [NOTICE](https://github.com/kestra-io/migration-assistant/blob/main/NOTICE). The Kestra name and logo are trademarks of Kestra Technologies and aren't covered by the software license.
