# vro2k: VMware vRO to Kestra

**Reads a customer's vRealize Orchestrator export and says what to do with every workflow before anything is converted.**

vro2k takes one vRO estate as exported (a `.package`, a Build Tools source tree, or single
`.workflow` files) and parses it with deterministic code, behind guards for hostile files. It then
works out which systems each workflow touches and sorts every workflow into one of eight outcomes:
convert, wrapper, near-duplicate, plugin-covered library, needs owner input, manual, rebuild, or
retire. It
plans the work in waves and writes a brief that a person approves before any conversion starts.
A model is used only where reading intent needs one. Structure, counts and outcomes always come
from code.

It is used for:

- **Sizing a migration.** The brief says how much of an estate converts, how much needs a person,
  and in what order, from the export alone.
- **The migration guide.** It runs the guide's first stages on a real export (see
  [Migration strategy](#migration-strategy)).
- **The VMware Migration Lab.** Converted flows are meant to run in
  [kestra-io/marketing-vmware-migration-lab](https://github.com/kestra-io/marketing-vmware-migration-lab)
  before anywhere real. Nothing here runs against the lab yet.

**This repository is private and staged for architecture review.** Nothing in it deploys to Kestra.
The licence is pending (see [NOTICE](NOTICE)).

---

## Repository structure

```
├── README.md, INPUTS.md, SECURITY.md, NOTICE
├── sources/                  The only input: put each estate's export in sources/<estate>/. Only its README is committed
├── stages/                   The only output: stages/<estate>/, written by the tool. Only its README is committed
├── samples/                  Two public vRO plugin libraries (Avi, Nuage): the export, SOURCE.md, the licence,
│                             a hand-written WALKTHROUGH.md, and model-output/ (the sample's saved specs)
├── plugin/                   Claude Code plugin: /vro-* commands, and scripts/vro.py (the CLI)
├── vro2k_assess/             Discovery: parse, cluster, sanitize, intake check, understand, stages 0 to 3.
├── kestra-reference/         The dated Kestra plugin snapshot Assess reads
├── models.yaml               Which model answers: a reasoning tier and a drafting tier
├── tools/                    secret_scan.py (the repository and its history)
└── tests/                    pytest suite; conformance/ holds public test inputs and the planted-secrets fixture
```

An estate's results, once the stages have run:

```
stages/<estate>/
├── index.html     start here: the brief, offline
├── your-input/    answers.yaml (everything you answer); entry-points.yaml only when there is no vRA export
├── report/        the detail pages, reports/ (every Markdown report rendered), and BRIEF.md to download
└── data/          the stages' working files, never edited: 0-preflight/, 1-assess/, 2-understand/, 3-brief/, and 4-convert/ once a wave is converted
```

`index.html` is the brief, written in the words of the [vRA migration guide](https://kestra.io/resources/migration/vra):
a status box (the next step, and how many elements are ready to convert today), what is in the estate, the
recommendation (convert, a mix or rebuild, with how many elements convert Direct and how many need a Redesign,
and a table of what each kind becomes in Kestra), what we need from you, grouped and counted, the plan with one
line per wave, and where to check. Every list on it is grouped and capped; the full lists are one click away in
`report/`. `report/BRIEF.md` is the same text with an appendix (every element, and each wave's entry criteria).

## Getting it running

Python 3.11 or later, and PyYAML. Model calls use the local Claude Code login by default
(`models.yaml`). `--no-model` never calls a model: it uses cached replies or code-made labels.

```bash
python3 -m venv .venv && .venv/bin/pip install pyyaml pytest
.venv/bin/python plugin/scripts/vro.py use-sample avi                        # samples/avi -> sources/avi-sample/
.venv/bin/python plugin/scripts/vro.py run --estate avi-sample --no-model   # sanitize, preflight, assess, understand, brief, pages
open stages/avi-sample/index.html
```

`use-sample` also puts the sample's saved specs in `stages/avi-sample/data/2-understand/`, so a run on it
needs no billed model call (`--no-model` uses the saved names and walkthrough text too). On a new estate,
`run` without `--no-model` writes the specs, and that step costs money. Re-running after answering is the
same command: only what changed is re-done, and a spec is billed again only when its input changed.
The stage commands (`sanitize`, `preflight`, `assess`, `understand`, `brief`, `visuals`, `status`) are kept
for development; `all` is `run` without Understand.

For a customer's export, check it before storing it. The check exits non-zero with a masked report
if the export carries anything secret-shaped:

```bash
.venv/bin/python plugin/scripts/vro.py check-intake ~/Downloads/estate.package
```

Then put the export in `sources/<estate>/` (INPUTS.md says what goes there) and run
`run --estate <estate>`. Nothing it writes leaves `stages/<estate>/`.

Tests: `.venv/bin/python -m pytest tests -q`.

## Migration strategy

vro2k follows the migration guide's stages: decide first, convert second. The migration guide is
internal until it's published, so the stages below are the tool's own. Each stage is a command,
writes into its own folder, and can be re-run after a person edits its inputs.

| Stage | Command | What it produces | Status |
|---|---|---|---|
| 0. Preflight | `preflight`, `sanitize` | Whether the export is complete enough to assess, the questions it can't answer (in `your-input/answers.yaml`), and a redacted copy for review before anything leaves the customer's network | Built |
| 1. Assess | `assess` | Parse inventory, clusters of look-alike workflows, domains, and the target systems each workflow reaches | Built, code only |
| 2. Understand | `understand` | A spec per workflow, from its code and every action it calls: what it is for, its inputs and outputs, and open questions (model-written, billed) | Built |
| 3. Brief | `brief` | An outcome per workflow and action, waves with entry criteria, a recommendation, the translation table and its number, and the brief (`index.html`, and `report/BRIEF.md` to download) | Built |

A person steers by editing one file, never by chat: `your-input/answers.yaml`. It has two lists, `open`
(what a wave waits on, first) and `answered`, then `signed_for` (`wave 1` or `full plan`, empty until signed).
Each item is an `id`, one plain `question`, a `suggested` value when the code points to one, and an `answer`.
Write `yes` to take the suggested value, or write the answer itself; a remark goes in the answer too. The tool
moves an item to `answered` once it has an answer, keeps every answer as written, and never drops an id: an
item no longer asked stays under `answered`, its question starting "No longer asked:". Every wave entry
criterion names the id that closes it. Conversion starts only after the signature. Corrections to what the
tool found (domains, clusters, reach, wrappers, outcomes) go in a `your-input/corrections.yaml` you add
yourself; [INPUTS.md](INPUTS.md) shows its shape. An `answers.yaml` in the old format is moved to this one on
the next run, every answer and the signature carried over, and the old file kept as
`data/answers.yaml.before-2g`.

An estate written by an earlier version (stage folders at the top, `pages/`) is moved by the next run:
the folders go into `data/`, the pages are regenerated in `report/`, and `answers.md`, `decisions.yaml`,
`reviews.yaml`, the `BRIEF.md` signature and both `overrides.yaml` files are carried into `your-input/`. The
old files are kept, renamed `*.migrated`, and read by nothing. The Claude Code commands in `plugin/commands/` run the same stages.
[plugin/README.md](plugin/README.md) has the details.

## Where to look next

- [INPUTS.md](INPUTS.md): what to put in `sources/<estate>/`, and how an admin produces it.
- [samples/avi/WALKTHROUGH.md](samples/avi/WALKTHROUGH.md): one estate from export to brief.
- [SECURITY.md](SECURITY.md): what protects the tool and the export, and what is still missing.
- [plugin/README.md](plugin/README.md): the Claude Code commands and the files a person edits.
