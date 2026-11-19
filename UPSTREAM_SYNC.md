# Upstream synchronization workflow

This document defines how upstream changes enter the clean sapper-v2 history without importing original upstream commit objects.

## Verified identities

- Canonical GitHub fork: `https://github.com/dcduc79x/sapper-v2.git`
- Original upstream: `https://github.com/N1nEmAn/sapper-v2.git`
- Verified upstream baseline: `7b75e6f1f5d5b4ae0c55878b0dd0994d9b08f1fb`
- Rewritten clean tip: `e22c17e9ddb2eff89146d39fb21e75b9913204a2`
- Persistent backup: `/home/dcduc/stuffs/sapper-backups/20261119-history-normalization`

The baseline is the verified upstream master commit, not the current upstream HEAD. Never substitute upstream HEAD when the baseline is missing.

## Branch model

- `master`: clean, stable production branch. Fast-forward only from `develop`.
- `develop`: clean integration branch. Fast-forward only from `sync/upstream-*`.
- `sync/upstream-<shortsha>`: temporary local branch from `develop`, never pushed.
- `refs/remotes/origin/*`: old remote-tracking refs, not maintained branches.

Clean maintained branches are local `master`, `develop`, and temporary `sync/*` branches. Old remote-tracking refs remain at `7b75e6f...` until an approved fetch or push updates them. Do not confuse the two groups during migration.

## Invariants

1. One GitHub repository only. The clean repo has one remote, `origin`, pointing to the canonical fork.
2. Upstream is fetched only into a local external mirror outside the clean repository. That mirror is never pushed.
3. No original upstream commit object becomes an ancestor of `master` or `develop`.
4. All maintained commits follow Conventional Commits and contain only ASCII hyphen-minus.
5. Promotion is `git merge --ff-only`. Pushes are normal pushes after the one-time approved migration.
6. No Git configuration is changed without approval. Hooks below are opt-in only.

## Persistent backup

The backup was created before any migration:

```bash
/home/dcduc/stuffs/sapper-backups/20261119-history-normalization/original.git
/home/dcduc/stuffs/sapper-backups/20261119-history-normalization/rewritten-clean.git
/home/dcduc/stuffs/sapper-backups/20261119-history-normalization/untracked/
```

`git clone --mirror <source> <path>` is the required layout. It copies all refs and sets `remote.origin.fetch=+refs/*:refs/*`. Do not add a second remote to the clean repository.

## One-time clean-history adoption, not yet executed

Fetch from the disposable rewrite clone and verify the exact SHA:

```bash
CLEAN=/home/dcduc/stuffs/sapper
REWRITE=/tmp/opencode/sapper-history-normalization-20261119/rewrite

git -C "$CLEAN" fetch "$REWRITE" refs/heads/master:refs/heads/migration/master-clean
test "$(git -C "$CLEAN" rev-parse refs/heads/migration/master-clean)" = "e22c17e9ddb2eff89146d39fb21e75b9913204a2"
git -C "$CLEAN" branch develop refs/heads/migration/master-clean
```

Do not move `master`. Keep the existing `master` at `899b062...` until explicit approval.

After approval only:

```bash
git -C "$CLEAN" switch develop
git -C "$CLEAN" branch -f master refs/heads/migration/master-clean
git -C "$CLEAN" rev-parse master
git -C "$CLEAN" push --force-with-lease=master:7b75e6f1f5d5b4ae0c55878b0dd0994d9b08f1fb origin master
git -C "$CLEAN" push origin develop
git -C "$CLEAN" fetch origin
git -C "$CLEAN" branch -D migration/master-clean
```

Never force-update a checked-out `master`. The `switch develop` step is mandatory.

## External upstream mirror

```bash
mkdir -p /tmp/opencode/upstream-mirrors
git clone --mirror https://github.com/N1nEmAn/sapper-v2.git /tmp/opencode/upstream-mirrors/sapper-v2.git
git -C /tmp/opencode/upstream-mirrors/sapper-v2.git fetch --prune origin
```

The mirror has the full upstream refs and is local only. Never push from it into the clean repository or add it as a clean-repo remote.

## Incremental sync procedure

1. Read `.upstream-state.json`. Use `last_integrated_sha`; never fall back to upstream HEAD.
2. Fetch the mirror and list only new commits:
   ```bash
   UP=/tmp/opencode/upstream-mirrors/sapper-v2.git
   LAST=$(python3 -c 'import json; print(json.load(open(".upstream-state.json"))["last_integrated_sha"])')
   git -C "$UP" fetch --prune origin
   NEW=$(git -C "$UP" rev-parse refs/remotes/origin/master)
   git -C "$UP" log --oneline "$LAST..$NEW"
   git -C "$UP" rev-list --reverse "$LAST..$NEW"
   ```
3. Create a temporary branch: `git switch -c "sync/upstream-${NEW:0:8}" develop`.
4. For each upstream commit, export a parent-to-child binary diff. Do not apply cumulative range diffs repeatedly:
   ```bash
   PARENT=$(git -C "$UP" rev-parse "$COMMIT^")
   git -C "$UP" diff --binary "$PARENT" "$COMMIT" > "$PATCH"
   python3 scripts/check-upstream-patch-paths.py "$PATCH"
   ```
5. Apply only after the path review. `git apply --3way` requires the preimage blobs in the clean repository. Since the clean history normalized blobs, do not assume they exist. Prefer `git apply --binary --check`, then `git apply --binary`. If it fails, translate the change manually.
6. Commit with our own Conventional message and exact attribution:
   ```bash
   git add -A
   git commit -m "chore: integrate upstream ${COMMIT:0:8}" \
     -m "upstream_sha: $COMMIT" \
     -m "source_url: https://github.com/N1nEmAn/sapper-v2.git"
   ```
7. Update `.upstream-state.json` with the exact upstream SHA and source URL in `integration_log`.
8. Run `bash scripts/upstream-sync-gate.sh`.
9. Verify no upstream commit leaked:
   ```bash
   git -C "$UP" rev-list "$LAST..$NEW" > /tmp/opencode/upstream-shas.txt
   if git rev-list HEAD | grep -F -f /tmp/opencode/upstream-shas.txt; then exit 1; fi
   ```

## Former monolith mapping

Upstream changes to `lib/sapper.py` must never recreate the monolith. Map them as follows:

| Former area | Current location |
|---|---|
| constants and normalizers | `lib/sapper_pkg/model.py`, `config.py`, `paths.py` |
| Store, locks, JSON I/O | `lib/sapper_pkg/storage.py` |
| schema and validation | `lib/sapper_pkg/schema.py`, `graph.py` |
| renderers and prompts | `lib/sapper_pkg/render.py` |
| model CLI and key rotation | `lib/sapper_pkg/engine.py` |
| command handlers | `lib/sapper_pkg/commands/*.py` |
| parser and facade | `lib/sapper.py` |

High-conflict paths are flagged by `scripts/check-upstream-patch-paths.py`.

## Quality gates

`scripts/upstream-sync-gate.sh` runs:

- `python3 -m compileall -q lib`
- `python3 -W always -m unittest discover -s tests`
- `uvx ruff format --check lib tests`
- `uvx ruff check` with exit status 0 or 1 accepted; status above 1 is a tool execution failure and stops the gate
- identity comparison against `ci/ruff-baseline.txt`
- `scripts/check-upstream-state.py`
- `scripts/check-upstream-ancestry.py`
- `scripts/check-forbidden-dashes.py`
- `scripts/check-conventional-commits.py`
- optional upstream patch path review
- `git diff --check`
- CLI version via script and temporary symlink

History validators use `MAINTAINED_REFS`, which defaults to `master develop`. Backup refs and old remote-tracking refs are never scanned. CI sets `MAINTAINED_REFS` to `origin/master origin/develop` with `fetch-depth: 0`. Set `UPSTREAM_MIRROR` to also verify that no upstream commit object is reachable.

Ruff baseline is compared by `path:line:column:code`, not by count. Regenerate only for intentional changes:

```bash
python3 scripts/generate-ruff-baseline.py
```

## Non-destructive staging branch and CI plan

Before any remote migration, prepare a normal staging branch from `develop`:

```bash
git switch develop
git switch -c staging/normalized-20261119
git push origin staging/normalized-20261119:refs/heads/staging/normalized-20261119
```

The push above requires separate approval and is not part of preparation. The workflow selects refs by branch name. On `staging/normalized-*`, it uses `MAINTAINED_REFS=HEAD` so validation follows the clean staging head while the old `origin/master` and absent `origin/develop` are ignored. On maintained production branches it uses `origin/master origin/develop`. The gate fails closed if any selected ref is missing. Historical blobs, filenames and commit messages for all commits reachable from the selected refs are scanned. A green run confirms the clean history, tooling and docs validate from a fresh clone. No remote branch is deleted until after the approved migration and a second verification.

## Automated upstream monitoring

`.github/workflows/upstream-monitor.yml` runs daily at 01:00 UTC (08:00 Asia/Ho_Chi_Minh) and on manual dispatch. It reads `last_integrated_sha` from `.upstream-state.json` and queries the official upstream through the read-only GitHub API. It never fetches upstream Git objects into this repository.

Possible results:

- `UP_TO_DATE`: no issue is created or changed.
- `UPDATE_AVAILABLE`: one monitoring issue is created or updated.
- `UPSTREAM_DIVERGED`: one issue requests manual review.
- `CHECK_FAILED`: the workflow fails; no issue is created.

The workflow has `contents: read` and `issues: write` permissions only. It never merges, cherry-picks, rewrites or pushes upstream code, and it never updates `last_integrated_sha`. Upstream commit messages are treated as untrusted data and are sanitized before appearing in an issue.

Integration remains manual through the sync workflow above. Scheduled workflows run from the default branch and may be delayed by GitHub.

## Promotion and push

After all gates pass:

```bash
git switch develop
git merge --ff-only "sync/upstream-${NEW:0:8}"
git switch master
git merge --ff-only develop
git push origin develop
git push origin master
git branch -d "sync/upstream-${NEW:0:8}"
```

If fast-forward fails, stop. Do not rebase or force push without explicit approval.

## Automation hooks

Hooks are prepared but not installed because Git configuration must not change without approval. To opt in:

```bash
git config core.hooksPath .githooks
```

## Excluded files

`.agents/` and `skills-lock.json` are untracked user files and are never staged or modified. Some third-party skill files under `.agents/` contain forbidden Unicode dashes. Therefore no validation claims that the entire working directory is dash-free; validation covers tracked files, staged changes and selected commit messages only.
