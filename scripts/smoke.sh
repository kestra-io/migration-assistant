#!/bin/sh
# The release smoke test: a built kestra-migrate runs on a synthetic estate, with no model, and writes a brief.
#
#   scripts/smoke.sh <empty folder> <version> <command ...>
#
# Copies the helpers conformance estate (every name in it is invented) into <folder>/helpers as an export folder,
# then runs `<command ...> vra run helpers --no-model` from <folder>, so a container command can mount
# <folder>/helpers at /work/helpers. Fails unless the brief is there and carries <version>.
set -eu
here="$(cd "$(dirname "$0")/.." && pwd)"
dir="$1"; version="$2"; shift 2
mkdir -p "$dir/helpers"
cp -R "$here/tests/vra/conformance/helpers/vro" "$dir/helpers/vro"
cp -R "$here/tests/vra/conformance/helpers/vra" "$dir/helpers/helpers-vra"
cd "$dir"
"$@" --version | grep -qx "kestra-migrate $version" || { echo "smoke: expected kestra-migrate $version"; exit 1; }
"$@" vra run helpers --no-model
out="helpers/kestra-assessment"
for f in index.html report/BRIEF.md your-input/answers.yaml; do
  test -s "$out/$f" || { echo "smoke: $out/$f was not written"; exit 1; }
done
grep -q "Kestra Migration Assistant for vRA $version" "$out/report/BRIEF.md" \
  || { echo "smoke: BRIEF.md does not carry version $version"; exit 1; }
echo "smoke: $version wrote a brief ($(find "$out" -type f | wc -l | tr -d ' ') files)"
