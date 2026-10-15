# Kestra Migration Assistant for vRA

**Reads a VMware vRA / vRO estate's export on your own machine and says what to do with every workflow
before anything is migrated to [Kestra](https://kestra.io).**

It takes one estate as exported (a vRO `.package`, a Build Tools source tree or single `.workflow` files,
and the vRA export if there is one) and parses it with deterministic code, behind guards for hostile
files. It works out what each workflow does and what starts it, which systems it touches and which Kestra
plugins cover them, and sorts every workflow into one of eight outcomes: convert, wrapper, near-duplicate,
plugin-covered library, needs owner input, manual, rebuild or retire. It plans the migration in waves and
writes a brief that a person answers and signs. A model is used only where reading intent needs one;
structure, counts and outcomes always come from code. Without a model, the brief shows everything the code
finds, and marks each workflow whose verdict needs one. The first step makes a sanitized copy of the export,
and every later step reads only that copy, so a prompt never carries the export's credentials, IPs, host names
or emails. Nothing leaves the machine except the calls to the configured model.

## Quickstart

### 1. Install

From the release's tag on GitHub, with [uv](https://docs.astral.sh/uv/), which installs Python for you if it needs
to. It needs [git](https://git-scm.com/downloads) on the machine:

```bash
uv tool install git+https://github.com/kestra-io/migration-assistant@v1.0.0
```

Or with pipx, or pip in a virtual environment (Python 3.10 or later), from the same URL:

```bash
pipx install git+https://github.com/kestra-io/migration-assistant@v1.0.0
pip install git+https://github.com/kestra-io/migration-assistant@v1.0.0
```

If `kestra-migrate` isn't found after `uv tool install`, run `uv tool update-shell` and open a new terminal.

To call a model with an Anthropic API key, add the `anthropic` extra:
`uv tool install 'kestra-migration-assistant[anthropic] @ git+https://github.com/kestra-io/migration-assistant@v1.0.0'`. Nothing else is needed for a Claude Code
login or an OpenAI-compatible endpoint. To move to a later version, run the same command with its tag; the
[releases](https://github.com/kestra-io/migration-assistant/releases) list them.

### 2. Run it on an export folder

Put the export in a folder of its own. The folder's name becomes the estate's name.
[INPUTS.md](https://github.com/kestra-io/migration-assistant/blob/main/INPUTS.md) says what goes in it and
how an administrator produces it.

```bash
kestra-migrate vra run ~/exports/acme
```

With a Claude Code login on this machine, it uses that; otherwise see [Choose a model](#choose-a-model). With
no model, it still writes the brief from what the code finds. Before its first billed call, it prints what the
run will cost.

### 3. Open the report

```bash
open ~/exports/acme/kestra-assessment/index.html        # macOS; xdg-open on Linux, start on Windows
```

Everything it writes goes to `kestra-assessment/` inside the export folder (or the folder `--out` names, for
an export folder you cannot write to). The tool never reads that folder as part of the export.

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

Only what changed is re-done, every answer is kept as you wrote it, and a model reply already paid for comes
from the cache. When nothing is left open, sign the plan in the same file.

## In a container

The image is `ghcr.io/kestra-io/migration-assistant`, for linux/amd64 and linux/arm64. Its working folder is
`/work`: mount the export folder inside it, under its own name, and pass an API key in.

```bash
cd ~/exports
docker run --rm -it -v "$PWD/acme:/work/acme" -e ANTHROPIC_API_KEY \
  ghcr.io/kestra-io/migration-assistant vra run acme
```

A Claude Code login cannot be used in a container. Use an Anthropic API key (`-e ANTHROPIC_API_KEY`), an
OpenAI-compatible endpoint (`-e KESTRA_MIGRATE_PROVIDER=openai-compatible -e KESTRA_MIGRATE_BASE_URL
-e KESTRA_MIGRATE_MODEL -e OPENAI_API_KEY`; a server on your own machine is `http://host.docker.internal:<port>/v1`,
with `--add-host=host.docker.internal:host-gateway` on Linux), or no model (`--no-model`). On Linux, add
`--user "$(id -u):$(id -g)"` so the files it writes are yours. Keep `-it`: above the cost threshold the run asks
before going on, and without a terminal it stops instead (or pass `--yes`).

## Choose a model

One model answers every call. There are three ways to connect to it:

| Provider | What it uses |
|---|---|
| `claude-code` | Your own Claude Code login, through `claude -p`. No API key needed |
| `anthropic` | An Anthropic API key, with the `anthropic` extra installed |
| `openai-compatible` | A base URL and a key: a model you host yourself, or Azure OpenAI and Amazon Bedrock through a proxy such as [LiteLLM](https://docs.litellm.ai/) |

With nothing set up, it uses `claude-code` if `claude` is installed and logged in, else `anthropic` if
`ANTHROPIC_API_KEY` is set, else it runs without a model: the brief still shows everything the code can find,
and says what a model would add. `kestra-migrate init` writes a config file with every setting explained
(`~/.config/kestra-migrate/config.yaml`, or `%APPDATA%\kestra-migrate\config.yaml` on Windows), readable by
you only. A command-line flag overrides an environment variable (`KESTRA_MIGRATE_PROVIDER`,
`KESTRA_MIGRATE_MODEL`, `KESTRA_MIGRATE_BASE_URL`), which overrides the file, which overrides the defaults.
Keys go in an environment variable the file names; nothing about the model is written to the export folder.

Tested models, through `claude-code` on a load-balancer estate: `claude-opus-5-5` (the default),
`claude-fable-5-1` and `claude-sonnet-5-5`. Opus gave the best brief. Fable gave a brief of the same quality,
but cost about three times as much and took twice as long; to use it, set `model: claude-fable-5-1` in the
config file, or pass `--model claude-fable-5-1`. Sonnet is the cheapest, but it missed a real defect that the
other two found.

A model you host yourself needs a context of at least 32,768 tokens: a workflow's prompt carries its whole call
tree. Ollama's default is 4,096, and it cuts a longer prompt without saying so, so start it with
`OLLAMA_CONTEXT_LENGTH=32768 ollama serve` (or more); a reply to a prompt the server cut is refused, never used.

Every reply is checked against the structure the tool expects. A reply that does not match is asked for once
more, with what was wrong; a second miss stops that call with an error that names the model. Other models
may work, but a smaller one is more likely to miss the structure.

### What a run costs

A typical run: the load balancer use case (4 workflows, 39 actions) cost $1.32 on `claude-opus-5-5`, in 8
calls. Most of it is one call per workflow, so the cost grows with the number and size of
the workflows. Before its first billed call, a run prints an estimate from the prompts it is about to send and
the model's price, with the number of calls:

```
Cost        Estimate: 8 model calls, about $1.28 at Claude Opus 5.5's API prices. With your Claude Code login this is not billed separately: it counts against your Claude plan.
```

With `claude-code`, the figure is what the calls would cost at API prices; they count against your Claude
plan instead of being billed separately. Above $20 the run asks before going on; set `max_cost_usd` in the
config file to change that, or pass `--yes` to go ahead without asking. With no terminal to ask in (a
script, a CI job), a run above the threshold stops unless `--yes` is given. Cached replies cost nothing, so a
run again after answering estimates only what is new, and one with every reply cached makes no billed call.
For a model with no price on record (a self-hosted one), the estimate gives the number of calls only.

## Commands

| Command | What it does |
|---|---|
| `kestra-migrate init` | Write the config file that says which model to call, and how |
| `kestra-migrate vra run <export folder> [--out <folder>] [--no-model] [--provider ...] [--model ...] [--base-url ...] [--yes]` | Sanitize, Preflight, Assess, Understand, Brief, then the pages. `--no-model` never calls a model: it uses cached replies, and the brief says what needs a model |
| `kestra-migrate vra status <export folder> [--out <folder>]` | Where each stage is, and what to do next |
| `kestra-migrate vra check-intake <path>` | Exit 1, with a masked report, if a received export carries anything secret-shaped |

## The brief

`index.html` is the brief, in the words of the [vRA migration guide](https://kestra.io/resources/migration/vra):
a status box (the next step), what is in the estate, the recommendation (convert, a mix or rebuild, with how
many elements convert Direct, how many need a Redesign and what the rest wait on, and a table of what each kind
becomes in Kestra), what we need from you, grouped and counted, and the plan with one line per wave.
`report/BRIEF.md` is the same text with an appendix.

`your-input/answers.yaml` has two lists, `open` (what a wave waits on, first) and `answered`, then
`signed_for` (`wave 1` or `full plan`, empty until signed). Each item is an `id`, one plain `question`, a
`suggested` value when the code points to one, and an `answer`. Write `yes` to take the suggested value, or
write the answer itself. The tool moves an item to `answered` once it has an answer, keeps every answer as
written, and never drops an id. Corrections to what the tool found (domains, clusters, reach, wrappers,
outcomes) go in a `your-input/corrections.yaml` you add yourself;
[INPUTS.md](https://github.com/kestra-io/migration-assistant/blob/main/INPUTS.md) shows its shape.
[SECURITY.md](https://github.com/kestra-io/migration-assistant/blob/main/SECURITY.md) lists what protects the
tool and the export, and what is still missing.

## Help

Need help running it, or want us to run it for you? [Ask us here](https://kestra.io/resources/migration/vra/assistant?utm_source=migration-assistant&utm_medium=readme).
Found a bug, or want something it doesn't do?
[Open an issue](https://github.com/kestra-io/migration-assistant/issues/new/choose), and never attach a
customer's export. To contribute, see
[CONTRIBUTING.md](https://github.com/kestra-io/migration-assistant/blob/main/CONTRIBUTING.md).

## Licence

Apache License 2.0 ([LICENSE](https://github.com/kestra-io/migration-assistant/blob/main/LICENSE)). Bundled
third-party files keep their own licences, listed in
[NOTICE](https://github.com/kestra-io/migration-assistant/blob/main/NOTICE). The Kestra name and logo are
trademarks of Kestra Technologies and are not covered by the licence.
