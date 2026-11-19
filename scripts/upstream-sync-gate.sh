#!/usr/bin/env bash
set -euo pipefail

# Quality gate for the maintained clean history and sync/upstream-* branches.
# MAINTAINED_REFS controls which refs history validators inspect.

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

maintained=${MAINTAINED_REFS:-"master develop"}
read -r -a refs <<<"$maintained"

echo "[gate] compile"
python3 -m compileall -q lib

echo "[gate] tests"
python3 -W always -m unittest discover -s tests

echo "[gate] format"
uvx ruff format --check lib tests

echo "[gate] lint baseline"
set +e
uvx ruff check --output-format concise lib tests >"$tmp/ruff.txt" 2>&1
ruff_status=$?
set -e
if [[ $ruff_status -gt 1 ]]; then
	echo "FAIL Ruff tool execution error, exit status $ruff_status"
	cat "$tmp/ruff.txt"
	exit 2
fi
python3 scripts/check-ruff-baseline.py ci/ruff-baseline.txt "$tmp/ruff.txt" "$ruff_status"

echo "[gate] upstream state"
python3 scripts/check-upstream-state.py

echo "[gate] ancestry"
ancestry=(--refs "${refs[@]}")
if [[ -n "${UPSTREAM_MIRROR:-}" ]]; then
	ancestry+=(--mirror "$UPSTREAM_MIRROR")
fi
python3 scripts/check-upstream-ancestry.py "${ancestry[@]}"

echo "[gate] forbidden dashes"
python3 scripts/check-forbidden-dashes.py --refs "${refs[@]}"

echo "[gate] conventional commits"
python3 scripts/check-conventional-commits.py --refs "${refs[@]}"

if [[ -n "${UPSTREAM_PATCH:-}" ]]; then
	echo "[gate] upstream patch paths"
	python3 scripts/check-upstream-patch-paths.py "$UPSTREAM_PATCH" ${UPSTREAM_PATCH_ACK:-}
fi

echo "[gate] diff check"
git diff --check

echo "[gate] cli smoke"
python3 lib/sapper.py --version
ln -s "$PWD/lib/sapper.py" "$tmp/sapper"
"$tmp/sapper" --version

echo "PASS all quality gates"
