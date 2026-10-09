# Kestra Migration Assistant

**Reads a VMware vRA / vRO estate's export on your own machine and says what to do with every workflow
before anything is migrated to [Kestra](https://kestra.io).**

It takes one estate as exported (a vRO `.package`, a Build Tools source tree or single `.workflow` files,
and the vRA export if there is one) and parses it with deterministic code, behind guards for hostile
files. It works out what each workflow does and what starts it, which systems it touches and which Kestra
plugins cover them, and sorts every workflow into one of eight outcomes: convert, wrapper, near-duplicate,
plugin-covered library, needs owner input, manual, rebuild or retire. It plans the migration in waves and
writes a brief that a person answers and signs. A model is used only where reading intent needs one;
structure, counts and outcomes always come from code. The export is sanitized before anything reaches a
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

Python 3.10 or later. The only dependency is PyYAML. Until the first release is on PyPI, install from a
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
| `kestra-migrate vra run <export folder> [--out <folder>] [--no-model]` | Sanitize, Preflight, Assess, Understand, Brief, then the pages. `--no-model` never calls a model: it uses cached replies or code-made labels |
| `kestra-migrate vra status <export folder> [--out <folder>]` | Where each stage is, and what to do next |
| `kestra-migrate vra check-intake <path>` | Exit 1, with a masked report, if a received export carries anything secret-shaped |

Model calls use the local Claude Code login by default (the bundled `models.yaml`; `VRO_MODELS_CONFIG`
names another file).

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
