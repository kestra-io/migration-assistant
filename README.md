# Kestra Migration Assistant for vRA

**Reads a VMware vRA / vRO estate's export on your own machine and says what to do with every workflow
before anything is migrated to [Kestra](https://kestra.io).**

You give it one estate as exported: a vRO `.package`, a Build Tools source tree or single `.workflow` files,
and the vRA export if there is one. Deterministic code parses it, behind guards for hostile files. The tool
works out what each workflow does and what starts it, which systems it touches and which Kestra plugins cover
them, and sorts every workflow into one of eight outcomes: convert, wrapper, near-duplicate, plugin-covered
library, needs owner input, manual, rebuild or retire. Then it plans the migration in waves and writes a brief
for a person to answer and sign.

A model is used only where reading intent needs one. Structure, counts and outcomes always come from code.
Without a model, the brief still shows everything the code finds, and marks each workflow whose verdict needs
one.

The first step makes a sanitized copy of the export, and every later step reads only that copy, so a prompt
never carries the export's credentials, IPs, host names or emails. Nothing leaves the machine except the calls
to the configured model.

## Quickstart

### 1. Install

Install from GitHub with [uv](https://docs.astral.sh/uv/), which installs Python for you if it needs to. You
also need [git](https://git-scm.com/downloads) on the machine:

```bash
uv tool install git+https://github.com/kestra-io/migration-assistant@main
```

Or use pipx, or pip in a virtual environment (Python 3.10 or later), with the same URL:

```bash
pipx install git+https://github.com/kestra-io/migration-assistant@main
pip install git+https://github.com/kestra-io/migration-assistant@main
```

If `kestra-migrate` isn't found after `uv tool install`, run `uv tool update-shell` and open a new terminal.

To call a model with an Anthropic API key, add the `anthropic` extra:

```bash
uv tool install 'kestra-migration-assistant[anthropic] @ git+https://github.com/kestra-io/migration-assistant@main'
```

A Claude Code login or an OpenAI-compatible endpoint needs nothing else.

To update, run `uv tool upgrade kestra-migration-assistant`. It installs whatever `main` holds now, with the
extras you installed, and `kestra-migrate --version` tells you which version you have. To stay on one version,
replace `main` with its tag (`@v1.0.0`). The [releases](https://github.com/kestra-io/migration-assistant/releases)
page lists them.

### 2. Run it on an export folder

Put the export in a folder of its own. The folder's name becomes the estate's name.
[INPUTS.md](https://github.com/kestra-io/migration-assistant/blob/main/INPUTS.md) covers what goes in it and
how an administrator produces it.

```bash
kestra-migrate vra run ~/exports/acme
```

If you're logged in to Claude Code on this machine, it uses that login. Otherwise, see
[Choose a model](#choose-a-model). With no model, it still writes the brief from what the code finds. Before its
first billed call, it prints an estimate of what the run will cost.

### 3. Open the report

```bash
open ~/exports/acme/kestra-assessment/index.html        # macOS; xdg-open on Linux, start on Windows
```

Everything it writes goes to `kestra-assessment/` inside the export folder, or to the folder `--out` names if
you can't write to the export folder. The tool never reads that folder as part of the export.

```
acme/kestra-assessment/
├── index.html     start here: the brief, offline
├── your-input/    answers.yaml: everything you answer, and the signature
├── report/        the detail pages, every report rendered, and BRIEF.md to download
└── data/          the stages' working files, never edited, and the model cache (model-cache/)
```

### 4. Answer, and run again

Answer the open items in `kestra-assessment/your-input/answers.yaml`, then run the same command again:

```bash
kestra-migrate vra run ~/exports/acme
```

The run redoes only what changed, keeps every answer as you wrote it, and takes any model reply you already
paid for from the cache. When nothing is left open, sign the plan in the same file.

## In a container

The image is `ghcr.io/kestra-io/migration-assistant`, for linux/amd64 and linux/arm64. Its working folder is
`/work`. Mount the export folder inside it under its own name, and pass in an API key:

```bash
cd ~/exports
docker run --rm -it -v "$PWD/acme:/work/acme" -e ANTHROPIC_API_KEY \
  ghcr.io/kestra-io/migration-assistant vra run acme
```

A Claude Code login can't be used in a container. Use one of these instead:

- An Anthropic API key (`-e ANTHROPIC_API_KEY`).
- An OpenAI-compatible endpoint (`-e KESTRA_MIGRATE_PROVIDER=openai-compatible -e KESTRA_MIGRATE_BASE_URL -e KESTRA_MIGRATE_MODEL -e OPENAI_API_KEY`).
  For a server on your own machine, the address is `http://host.docker.internal:<port>/v1`, and on Linux you
  also add `--add-host=host.docker.internal:host-gateway`.
- No model (`--no-model`).

On Linux, add `--user "$(id -u):$(id -g)"` so the files it writes are yours. Keep `-it`. Above the cost
threshold the run asks before going on, and without a terminal it stops instead, unless you pass `--yes`.

## Choose a model

One model answers every call. There are three ways to connect to it:

| Provider | What it uses |
|---|---|
| `claude-code` | Your own Claude Code login, through `claude -p`. No API key needed |
| `anthropic` | An Anthropic API key, with the `anthropic` extra installed |
| `openai-compatible` | A base URL and a key: a model you host yourself, or Azure OpenAI and Amazon Bedrock through a proxy such as [LiteLLM](https://docs.litellm.ai/) |

With nothing set up, it uses `claude-code` if `claude` is installed and logged in, then `anthropic` if
`ANTHROPIC_API_KEY` is set. If neither is there, it runs without a model, and the brief still shows everything
the code can find and says what a model would add.

`kestra-migrate init` writes a config file that explains every setting (`~/.config/kestra-migrate/config.yaml`,
or `%APPDATA%\kestra-migrate\config.yaml` on Windows). Only you can read it. A command-line flag overrides an
environment variable (`KESTRA_MIGRATE_PROVIDER`, `KESTRA_MIGRATE_MODEL`, `KESTRA_MIGRATE_BASE_URL`), which
overrides the file, which overrides the defaults. Keys go in an environment variable that the file names.
Nothing about the model is written to the export folder.

We tested three models through `claude-code` on a load-balancer estate: `claude-opus-5-5` (the default),
`claude-fable-5-1` and `claude-sonnet-5-5`. Opus gave the best brief. Fable gave a brief of the same quality,
but cost about three times as much and took twice as long. To use it, set `model: claude-fable-5-1` in the
config file, or pass `--model claude-fable-5-1`. Sonnet is the cheapest, but it missed a real defect that the
other two found.

A model you host yourself needs a context of at least 32,768 tokens, because a workflow's prompt carries its
whole call tree. Ollama's default is 4,096, and it cuts a longer prompt without saying so, so start it with
`OLLAMA_CONTEXT_LENGTH=32768 ollama serve` (or more). If the server cut a prompt, the reply is refused and never
used.

The tool checks every reply against the structure it expects. If a reply doesn't match, it asks once more and
says what was wrong. A second miss stops that call with an error that names the model. Other models may work,
but a smaller one is more likely to miss the structure.

### What is sent to the model

The first step of every run, Sanitize, writes a redacted copy of the export to
`kestra-assessment/data/0-preflight/sanitized/`. Sanitize is the only step that opens the export. Every later
step (Preflight, Assess, Understand, Brief and the pages) reads only that copy, and if the export changes,
Sanitize runs again first.

Prompts are built from the copy, so this is what the model gets from the export:

- **Replaced by a placeholder:** host names, IP addresses, URLs, email addresses, user and service accounts,
  and anything credential-shaped (passwords and tokens in assignments, keys, JWTs). Each value gets one
  placeholder, used everywhere it appears (`<HOST-01>`, `<IP-02>`).
  - In a vRA export, owners, subscribers and principals, the tenant, and network names, tags and CIDRs are
    replaced too.
  - An address that starts with 255 is a netmask, not a host's address, so it's kept (`255.255.255.0`).
- **Dropped:** every configuration element value that is text, and every SecureString value. Their names and
  types stay. A package's signer and owner identities, and its signatures and certificates, are removed too.
- **Kept from configuration elements:** only the values that can't identify anything.
  - A number or a boolean, going by the attribute's type, so a monitor's timeout of `16` stays.
  - A netmask (`255.255.255.0`, or a prefix like `/24`).

  A string attribute holding a number is still dropped, and so is a SecureString, whatever it holds.
- **Sent as written:** the names of workflows, actions, modules, categories, elements, attributes and items;
  the export's file and package names; the scripts and the workflows' structure; vRA IDs and event topics; and
  the specs of the workflows a workflow shares state with. A module or category name is kept even when it
  reads like a host name (`com.acme.net`), because it's code, and the tool's IDs and calls are built from it.
  **If a name identifies your organization, the model sees it.**

The pages are built from the same copy, so wherever the export had a replaced value, they show the
placeholder. A vRA network tag, for example, reads `<NETWORK-01>` instead of its name. In exchange, nothing in
the `kestra-assessment/` folder comes from a value Sanitize replaced or dropped, so you can share it the same
way as the sanitized copy. The redaction report shows only masked previews (`ab… (12 chars)`). A model's reply
can still state a value from its own knowledge.

Where the prompts go depends on the provider:

| Provider | Prompts go to |
|---|---|
| `claude-code` | Anthropic, through your Claude Code login, under your account's terms |
| `anthropic` | The Anthropic API, under the terms of the organization that owns the key |
| `openai-compatible` | The endpoint `base_url` names. A model you host keeps everything on your network |

Nothing else leaves the machine. There's no telemetry and no update check. Replies are cached locally in
`kestra-assessment/data/model-cache/`.

The sanitizer works from patterns, so it can miss things, such as a hostname with no dot or known domain
suffix, a password in a variable not named like one, or a secret inside a binary resource. It has been tested
on public vRO libraries and synthetic fixtures, but not yet on a real customer export. Before a run with a
model, either read `kestra-assessment/data/0-preflight/redaction-report.md` and check the sanitized copy next
to it, or run with `--no-model` or with a model you host. `kestra-migrate vra check-intake <path>` rejects an
export that still carries anything secret-shaped.

### What a run costs

The load balancer use case (4 workflows, 39 actions) is a typical run. It cost $1.32 on `claude-opus-5-5`, in
8 calls. Most of the cost is one call per workflow, so it grows with the number and size of the workflows.
Before its first billed call, a run prints an estimate from the prompts it's about to send and the model's
price, with the number of calls:

```
Cost        Estimate: 8 model calls, about $1.28 at Claude Opus 5.5's API prices. With your Claude Code login this is not billed separately: it counts against your Claude plan.
```

With `claude-code`, the figure is what the calls would cost at API prices. They count against your Claude plan
instead of being billed separately. Above $20, the run asks before going on. Set `max_cost_usd` in the config
file to change that, or pass `--yes` to go ahead without asking. With no terminal to ask in (a script, a CI
job), a run above the threshold stops unless `--yes` is given. Cached replies cost nothing, so a rerun after
you answer estimates only what's new, and a run with every reply cached makes no billed call. For a model with
no price on record (a self-hosted one), the estimate gives the number of calls only.

## Commands

| Command | What it does |
|---|---|
| `kestra-migrate init` | Write the config file that says which model to call, and how |
| `kestra-migrate vra run <export folder> [--out <folder>] [--no-model] [--provider ...] [--model ...] [--base-url ...] [--yes]` | Sanitize, Preflight, Assess, Understand, Brief, then the pages. `--no-model` never calls a model: it uses cached replies, and the brief says what needs a model |
| `kestra-migrate vra status <export folder> [--out <folder>]` | Where each stage is, and what to do next |
| `kestra-migrate vra check-intake <path>` | Exit 1, with a masked report, if a received export carries anything secret-shaped |

## The brief

`index.html` is the brief. It uses the words of the [vRA migration guide](https://kestra.io/resources/migration/vra)
and shows:

- a status box with the next step
- what is in the estate
- the recommendation (convert, a mix or rebuild), with how many elements convert Direct, how many need a
  Redesign and what the rest wait on, and a table of what each kind becomes in Kestra
- what we need from you, grouped and counted
- the plan, with one line per wave

`report/BRIEF.md` is the same text with an appendix.

`your-input/answers.yaml` has two lists, `open` (with what a wave waits on first) and `answered`, then
`signed_for` (`wave 1` or `full plan`, empty until signed). Each item has an `id`, one plain `question`, a
`suggested` value when the code points to one, and an `answer`. Write `yes` to take the suggested value, or
write the answer itself. The tool moves an item to `answered` once it has an answer, keeps every answer as
written, and never drops an id.

If the tool got something wrong (domains, clusters, reach, wrappers, outcomes), put the corrections in a
`your-input/corrections.yaml` that you add yourself.
[INPUTS.md](https://github.com/kestra-io/migration-assistant/blob/main/INPUTS.md) shows its shape.

## Help

Need help running it, or want us to run it for you? [Ask us here](https://kestra.io/resources/migration/vra/assistant?utm_source=migration-assistant&utm_medium=readme).
Found a bug, or want something it doesn't do?
[Open an issue](https://github.com/kestra-io/migration-assistant/issues/new/choose), but never attach a
customer's export. This repository doesn't take outside contributions. See
[CONTRIBUTING.md](https://github.com/kestra-io/migration-assistant/blob/main/CONTRIBUTING.md).

Report a security issue privately, through [GitHub's security advisory form](https://github.com/kestra-io/kestra/security/advisories/new) on Kestra's main repository, never in a public issue.

## Licence

Apache License 2.0 ([LICENSE](https://github.com/kestra-io/migration-assistant/blob/main/LICENSE)). Bundled
third-party files keep their own licences, listed in
[NOTICE](https://github.com/kestra-io/migration-assistant/blob/main/NOTICE). The Kestra name and logo are
trademarks of Kestra Technologies and are not covered by the licence.
