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
finds, and marks each workflow whose verdict needs one. The export is sanitized before anything reaches a
model, and nothing leaves the machine except the calls to the configured model.

## Install

With [uv](https://docs.astral.sh/uv/):

```bash
uv tool install kestra-migration-assistant
```

Or with pipx, or pip in a virtual environment:

```bash
pipx install kestra-migration-assistant
pip install kestra-migration-assistant
```

Python 3.10 or later. The only dependency is PyYAML; to call a model with an Anthropic API key, add the
`anthropic` extra (`uv tool install 'kestra-migration-assistant[anthropic]'`). Until the first release is on PyPI, install from a
checkout: `uv tool install ./migration-assistant` (or `pipx install ./migration-assistant`).

## Run it

Put the export in a folder of its own. The folder's name becomes the estate's name.

```bash
kestra-migrate vra run ~/exports/acme
open ~/exports/acme/kestra-assessment/index.html
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

Answer the open items in `your-input/answers.yaml` and run the same command again. Only what changed is
re-done, every answer is kept, and a model reply already paid for comes from the cache.

| Command | What it does |
|---|---|
| `kestra-migrate init` | Write the config file that says which model to call, and how |
| `kestra-migrate vra run <export folder> [--out <folder>] [--no-model] [--provider ...] [--model ...] [--base-url ...]` | Sanitize, Preflight, Assess, Understand, Brief, then the pages. `--no-model` never calls a model: it uses cached replies, and the brief says what needs a model |
| `kestra-migrate vra status <export folder> [--out <folder>]` | Where each stage is, and what to do next |
| `kestra-migrate vra check-intake <path>` | Exit 1, with a masked report, if a received export carries anything secret-shaped |

## Choose a model

One model answers every call. There are three ways to connect to it:

| Provider | What it uses |
|---|---|
| `claude-code` | Your own Claude Code login, through `claude -p`. No API key needed |
| `anthropic` | An Anthropic API key |
| `openai-compatible` | A base URL and a key: a model you host yourself, or Azure OpenAI and Amazon Bedrock through a proxy such as [LiteLLM](https://docs.litellm.ai/) |

A model you host yourself needs a context of at least 32,768 tokens: a workflow's prompt carries its whole call
tree. Ollama's default is 4,096, and it cuts a longer prompt without saying so, so start it with
`OLLAMA_CONTEXT_LENGTH=32768 ollama serve` (or more); a reply to a prompt the server cut is refused, never used.

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

Every reply is checked against the structure the tool expects. A reply that does not match is asked for once
more, with what was wrong; a second miss stops that call with an error that names the model. Other models
may work, but a smaller one is more likely to miss the structure.

[INPUTS.md](INPUTS.md) says what goes in the export folder and how an administrator produces it, and how
to answer. [SECURITY.md](SECURITY.md) lists what protects the tool and the export, and what is still missing.

## The brief

`index.html` is the brief, in the words of the [vRA migration guide](https://kestra.io/resources/migration/vra):
a status box (the next step, and how many elements are ready to convert today), what is in the estate, the
recommendation (convert, a mix or rebuild, with how many elements convert Direct and how many need a
Redesign, and a table of what each kind becomes in Kestra), what we need from you, grouped and counted, the
plan with one line per wave, and where to check. `report/BRIEF.md` is the same text with an appendix.

`your-input/answers.yaml` has two lists, `open` (what a wave waits on, first) and `answered`, then
`signed_for` (`wave 1` or `full plan`, empty until signed). Each item is an `id`, one plain `question`, a
`suggested` value when the code points to one, and an `answer`. Write `yes` to take the suggested value, or
write the answer itself. The tool moves an item to `answered` once it has an answer, keeps every answer as
written, and never drops an id. Corrections to what the tool found (domains, clusters, reach, wrappers,
outcomes) go in a `your-input/corrections.yaml` you add yourself; [INPUTS.md](INPUTS.md) shows its shape.

## Development

```bash
git clone https://github.com/kestra-io/migration-assistant && cd migration-assistant
uv sync                  # the package, editable, with the pinned development tools (uv.lock)
make check               # tests, ruff, mypy, the secret scan over the full history, the boundary test
make hooks               # run make check before every commit
```

Each stage also runs on its own for development (`preflight`, `sanitize`, `assess`, `understand`, `brief`,
`visuals`, and `all`, which is `run` without Understand); they are not listed in `--help`.
The tests are in `tests/vra/`, on synthetic inputs in `tests/vra/conformance/` (every name in them is invented);
`scripts/` holds `make check` and the secret scan. Neither ships with the package.

## Licence

Apache License 2.0 ([LICENSE](LICENSE)). Bundled third-party files keep their own licences, listed in
[NOTICE](NOTICE). The Kestra name and logo are trademarks of Kestra Technologies and are not covered by
the licence.
