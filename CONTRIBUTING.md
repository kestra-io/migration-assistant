# Contributing

This repository is the open half of the Kestra Migration Assistant for vRA, the assessment. Kestra maintains it,
and it **does not take outside contributions**. Pull requests from outside the team are closed without review.
Bug reports and feature requests are welcome as issues. Everyone taking part follows the
[Kestra Code of Conduct](https://github.com/kestra-io/kestra/blob/develop/.github/CODE_OF_CONDUCT.md).

## Never share a customer's export

Don't put a customer's export in an issue, a pull request, a test or a screenshot. The same goes for its
`kestra-assessment/` folder, `answers.yaml` and the model cache, because they hold the estate's hosts, accounts
and code. Describe what happened, or reproduce it with names you make up. Every input under `tests/` is synthetic,
and stays that way.

## Issues

Search the [open issues](https://github.com/kestra-io/migration-assistant/issues) first, then use a template,
[a bug report](https://github.com/kestra-io/migration-assistant/issues/new?template=bug_report.yml) or
[a feature request](https://github.com/kestra-io/migration-assistant/issues/new?template=feature_request.yml).
The maintainers triage issues, with no promised response time.

Never report a security issue in a public issue. Report it privately through
[GitHub's security advisory form](https://github.com/kestra-io/kestra/security/advisories/new) on Kestra's main repository.

## Development (maintainers)

1. Branch from `main`.
2. Set up, with [uv](https://docs.astral.sh/uv/):

   ```bash
   uv sync          # the package, editable, with the pinned development tools (uv.lock)
   make hooks       # run make check before every commit
   ```

3. Every bug fixed comes with a test that fails without the fix. Tests are in `tests/vra/`, on the synthetic
   inputs in `tests/vra/conformance/`.
4. `make check` passes: the tests, ruff, mypy, the secret scan over the full history, and the boundary test.
   It also runs on every pull request, but run it before you push.
5. Open the pull request against `main`, saying what changed and why. Keep it to one change.

To try a change the way a customer installs a release, install the branch from its git URL (git is needed):
`uv tool install git+https://github.com/kestra-io/migration-assistant@<branch>`. Customers install from `main`
(`@main`), so whatever is merged to `main` is what `uv tool upgrade kestra-migration-assistant` gives them. Merge
to `main` only what is ready to ship. A release is also tagged (`v1.0.0`) for the CHANGELOG, the container image
and anyone who pins a version (`@v1.0.0`).

For development, each stage also runs on its own (`kestra-migrate vra preflight`, `sanitize`, `assess`, `understand`,
`brief`, `visuals`, and `all`, which is `run` without Understand). They aren't listed in `--help`.

## Writing for a customer

The pages, `BRIEF.md`, the comments in `answers.yaml`, the terminal output and the README are read by a
customer's platform team. `tests/vra/test_writing.py` checks the pages for some of these rules.

- Use the words of the [vRA migration guide](https://kestra.io/resources/migration/vra). A wave starts with its
  first of a kind (never a "pilot"), an element converts Direct or needs a Redesign (never "mechanical" work),
  and nothing is a "prototype".
- Say vRA and vRO, not the products' longer names.
- The product is "Kestra Migration Assistant for vRA"; the command is `kestra-migrate`.
- Plain, short sentences that say what to do next. No em dash.
- No internal IDs on a page a customer reads. Point to the `answers.yaml` entry they answer instead.
- Nothing about Kestra's own files, people or plans, because this repository is public.
