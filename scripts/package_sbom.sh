#!/bin/sh
# The package's SBOM (CycloneDX JSON): the wheel in <dist> and the runtime dependencies uv.lock pins
# (<dist>/requirements.txt, written by `make dist`), as installed in a fresh virtual environment.
#
#   scripts/package_sbom.sh <dist> <output file>
set -eu
dist="$1"; out="$2"
venv="$(mktemp -d)/venv"
uv venv --quiet --python 3.12 "$venv"
uv pip install --quiet --python "$venv" --require-hashes -r "$dist/requirements.txt"
uv pip install --quiet --python "$venv" --no-deps "$dist"/kestra_migration_assistant-*.whl
uvx --quiet --from cyclonedx-bom==7.5.0 cyclonedx-py environment --pyproject pyproject.toml --mc-type application \
  --output-reproducible --output-format JSON --output-file "$out" "$venv/bin/python"
# The root component's version is dynamic in pyproject.toml: take it from the wheel that was installed
"$venv/bin/python" - "$out" <<'PY'
import json, sys
from importlib.metadata import version
v = version("kestra-migration-assistant")
d = json.load(open(sys.argv[1]))
d["metadata"]["component"].update(version=v, purl=f"pkg:pypi/kestra-migration-assistant@{v}")
json.dump(d, open(sys.argv[1], "w"), indent=2)
print(f"package SBOM: kestra-migration-assistant {v} and {len(d['components'])} dependencies")
PY
rm -rf "$(dirname "$venv")"
