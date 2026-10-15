# Changelog

Every release of the Kestra Migration Assistant for vRA. Versions follow [semantic versioning](https://semver.org).

## 1.0.0 (2026-10-22)

The first release: the assessment of a VMware vRA / vRO estate, on your own machine, from its export.

- `kestra-migrate vra run <export folder>` reads a vRO `.package`, a Build Tools source tree or `.workflow` files,
  and a vRA content export, and writes everything to `kestra-assessment/` inside the export folder: the brief
  (`index.html`, and `report/BRIEF.md`), the detail pages, and `your-input/answers.yaml`.
- The export is parsed by code, behind guards for hostile XML, ZIP, JSON and YAML. The first step writes a
  sanitized copy (host names, IPs, URLs, emails, accounts and credential-shaped values replaced by placeholders;
  configuration element and SecureString values dropped), and every later step, the prompts and the pages
  included, reads only that copy. Workflow, action, module, element and file names are kept as written.
  `kestra-migrate vra check-intake` rejects an export that carries anything secret-shaped.
- What each workflow does and what starts it, the call graph, the systems it touches and the Kestra plugins that
  cover them (from a plugin catalog snapshot bundled with the release, its date on every page), and one of eight
  outcomes per workflow: convert, wrapper, near-duplicate, plugin-covered library, needs owner input, manual,
  rebuild or retire.
- A migration plan in waves, each starting with its first of a kind, and a recommendation: how many elements
  convert Direct, how many need a Redesign, and what the rest wait on.
- One file to answer, `answers.yaml`, with what is settled together grouped, and a suggested answer where the code
  points to one. Run again after answering: only what changed is re-done, every answer is kept as typed, and the
  plan is signed in the same file.
- One model for every call, three ways to connect: your Claude Code login, an Anthropic API key, or an
  OpenAI-compatible endpoint (a model you host, or Azure OpenAI and Amazon Bedrock through a proxy). Claude Opus 5.5
  is the default. `kestra-migrate init` writes a commented config file in your home folder, never the export.
- Without a model, the brief still shows everything the code finds, and says what a model would add.
- A cost estimate before the first billed call, and a question before going on above $20 (`max_cost_usd`). Replies
  are cached, so a run again pays only for what is new.
- Nothing leaves the machine except the calls to the configured model: no telemetry, no update check.
- Installs from GitHub with `uv tool install git+https://github.com/kestra-io/migration-assistant@main` (or pipx,
  or pip, with the same URL; git is needed), on Python 3.10 or later, and updates with
  `uv tool upgrade kestra-migration-assistant`. To stay on this version, install `@v1.0.0` instead. A container
  image for linux/amd64 and linux/arm64, `ghcr.io/kestra-io/migration-assistant`, for an Anthropic API key, an
  OpenAI-compatible endpoint or no model. Each release carries SHA-256 checksums and CycloneDX SBOMs for the package
  and the image.
