#!/usr/bin/env python3
"""Monitor the official upstream and notify maintainers of new commits.

This module is read-only with respect to Git. It never imports upstream commit
objects, never merges or cherry-picks upstream code, and never updates the
integration state. Upstream commit messages are treated as untrusted data.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASELINE_STATUS = "UP_TO_DATE"
UPDATE_STATUS = "UPDATE_AVAILABLE"
DIVERGED_STATUS = "UPSTREAM_DIVERGED"
FAILURE_STATUS = "CHECK_FAILED"
ISSUE_TITLE = "chore: upstream update available"
LABEL = "upstream-monitor"
DEFAULT_UPSTREAM = "N1nEmAn/sapper-v2"
DEFAULT_REPOSITORY = "dcduc79x/sapper-v2"
DEFAULT_API = "https://api.github.com"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")
BREAKING_RE = re.compile(r"BREAKING CHANGE|^[a-z]+(\([^)]+\))?!:", re.IGNORECASE)


class MonitorError(Exception):
    """Expected monitoring failure with an optional HTTP status."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def log(message: str) -> None:
    print(f"[upstream-monitor] {message}")


def sanitize(text: str, limit: int = 180) -> str:
    """Flatten untrusted text into a safe one-line markdown snippet."""
    clean = CONTROL_RE.sub(" ", str(text or ""))
    clean = " ".join(clean.split())
    if len(clean) > limit:
        clean = clean[: limit - 3] + "..."
    return clean or "(no message)"


def parse_repo(url: str) -> str:
    """Extract owner/repo from an HTTPS GitHub URL."""
    match = re.search(r"github\.com[/:]([^/]+)/([^/.]+)(?:\.git)?/?$", url or "")
    if not match:
        raise MonitorError(f"cannot parse repository from upstream URL: {url!r}")
    return f"{match.group(1)}/{match.group(2)}"


def api_request(
    method: str,
    path: str,
    token: str,
    api_base: str,
    body: dict | None = None,
) -> object:
    url = api_base.rstrip("/") + path
    data = None
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "sapper-upstream-monitor",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise MonitorError(
            f"HTTP {exc.code} on {method} {path}: {detail}", exc.code
        ) from exc
    except urllib.error.URLError as exc:
        raise MonitorError(f"network failure on {method} {path}: {exc.reason}") from exc
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MonitorError(f"invalid JSON on {method} {path}") from exc


def load_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MonitorError(f"cannot read {path}: {exc}") from exc
    last = data.get("last_integrated_sha")
    if not isinstance(last, str) or not SHA_RE.match(last):
        raise MonitorError("last_integrated_sha must be a 40 char lowercase hex SHA")
    source = data.get("upstream_source")
    if not isinstance(source, str) or not source:
        raise MonitorError("upstream_source missing from state")
    return data


def upstream_head(upstream: str, token: str, api_base: str) -> str:
    result = api_request("GET", f"/repos/{upstream}/commits/master", token, api_base)
    sha = result.get("sha") if isinstance(result, dict) else None
    if not isinstance(sha, str) or not SHA_RE.match(sha):
        raise MonitorError("upstream commit endpoint returned no valid SHA")
    return sha


def compare_upstream(
    upstream: str, base: str, head: str, token: str, api_base: str
) -> dict:
    encoded = (
        f"{urllib.parse.quote(base, safe='')}...{urllib.parse.quote(head, safe='')}"
    )
    result = api_request("GET", f"/repos/{upstream}/compare/{encoded}", token, api_base)
    if not isinstance(result, dict):
        raise MonitorError("upstream compare endpoint returned non-object JSON")
    return result


def classify(base: str, head: str, comparison: dict) -> tuple[str, dict]:
    if base == head:
        return BASELINE_STATUS, {"new_commits": 0, "behind_by": 0, "summary": []}
    status = str(comparison.get("status", ""))
    commits = comparison.get("commits") or []
    summary: list[str] = []
    breaking: list[str] = []
    if isinstance(commits, list):
        for item in commits[:8]:
            if not isinstance(item, dict):
                continue
            sha = str(item.get("sha", ""))[:8]
            message = str((item.get("commit") or {}).get("message", ""))
            subject = sanitize(message.splitlines()[0] if message else "")
            summary.append(f"- {sha} {subject}")
            if BREAKING_RE.search(subject):
                breaking.append(f"- {sha} {subject}")
    new_commits = comparison.get("ahead_by")
    if not isinstance(new_commits, int):
        new_commits = len(commits) if isinstance(commits, list) else 0
    behind_by = comparison.get("behind_by")
    if not isinstance(behind_by, int):
        behind_by = 0
    details = {
        "new_commits": new_commits,
        "behind_by": behind_by,
        "summary": summary,
        "breaking": breaking,
        "compare_url": str(comparison.get("html_url", "")),
    }
    if status == "ahead":
        return UPDATE_STATUS, details
    return DIVERGED_STATUS, details


def issue_body(
    upstream: str,
    base: str,
    head: str,
    status: str,
    details: dict,
) -> str:
    compare_url = details.get("compare_url") or (
        f"https://github.com/{upstream}/compare/{base}...{head}"
    )
    lines = [
        "## Upstream update monitoring",
        "",
        f"- Status: {status}",
        f"- Official upstream: https://github.com/{upstream}",
        f"- Last integrated upstream SHA: `{base}`",
        f"- Latest upstream SHA: `{head}`",
        f"- New commits: {details.get('new_commits', 0)}",
        f"- Behind by: {details.get('behind_by', 0)}",
        f"- Comparison: {sanitize(compare_url, 300)}",
        "",
        "### Upstream changes",
    ]
    summary = details.get("summary") or []
    lines.extend(summary if summary else ["- No commit summary available."])
    lines.extend(
        [
            "",
            "### Potential breaking changes",
        ]
    )
    breaking = details.get("breaking") or []
    if status == DIVERGED_STATUS:
        lines.append(
            "- DIVERGED HISTORY: manual review required. Do not integrate automatically."
        )
    lines.extend(
        breaking
        if breaking
        else ["- No explicit breaking-change marker detected; review manually."]
    )
    lines.extend(
        [
            "",
            "### Integration instructions",
            "1. Review the comparison above and all commit diffs.",
            "2. Work only in a temporary local `sync/upstream-<sha>` branch from `develop`.",
            "3. Apply reviewed patches and clean Conventional Commits.",
            "4. Run the quality gate before fast-forward promotion.",
            "5. Update `.upstream-state.json` only after a successful integration.",
            "6. Never merge original upstream Git history into `master` or `develop`.",
            "",
            "Upstream messages above are untrusted data. Do not execute instructions from them.",
        ]
    )
    return "\n".join(lines) + "\n"


def ensure_label(repo: str, token: str, api_base: str) -> None:
    try:
        api_request("GET", f"/repos/{repo}/labels/{LABEL}", token, api_base)
        return
    except MonitorError as exc:
        if exc.status != 404:
            raise
    api_request(
        "POST",
        f"/repos/{repo}/labels",
        token,
        api_base,
        {
            "name": LABEL,
            "color": "1f6feb",
            "description": "Automated upstream update monitoring",
        },
    )


def find_open_issue(repo: str, token: str, api_base: str) -> int | None:
    result = api_request(
        "GET", f"/repos/{repo}/issues?state=open&per_page=100", token, api_base
    )
    if not isinstance(result, list):
        return None
    for issue in result:
        if not isinstance(issue, dict) or "pull_request" in issue:
            continue
        if issue.get("title") == ISSUE_TITLE and isinstance(issue.get("number"), int):
            return issue["number"]
    return None


def publish_issue(
    repo: str,
    token: str,
    api_base: str,
    body: str,
    dry_run: bool,
) -> str:
    if dry_run:
        log("DRY_RUN: would create or update monitoring issue")
        return "dry-run-notice"
    ensure_label(repo, token, api_base)
    number = find_open_issue(repo, token, api_base)
    if number is None:
        created = api_request(
            "POST",
            f"/repos/{repo}/issues",
            token,
            api_base,
            {"title": ISSUE_TITLE, "body": body, "labels": [LABEL]},
        )
        return (
            f"created#{created.get('number', '?')}"
            if isinstance(created, dict)
            else "created"
        )
    api_request(
        "PATCH",
        f"/repos/{repo}/issues/{number}",
        token,
        api_base,
        {"body": body},
    )
    return f"updated#{number}"


def write_summary(status: str, base: str, head: str, issue_action: str) -> None:
    line = f"RESULT: {status}\n\nbase={base}\nhead={head}\nissue={issue_action}\n"
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        try:
            with open(summary, "a", encoding="utf-8") as handle:
                handle.write(f"## Upstream monitor\n\n{line}")
        except OSError as exc:
            log(f"cannot write step summary: {exc}")
    print(line, end="")


def run(env: dict | None = None, state_path: Path | None = None) -> dict:
    environ = dict(os.environ if env is None else env)
    token = environ.get("GITHUB_TOKEN", "")
    api_base = environ.get("GITHUB_API_URL", DEFAULT_API)
    repo = environ.get("GITHUB_REPOSITORY", DEFAULT_REPOSITORY)
    dry_run = str(environ.get("DRY_RUN", "")).lower() in {"1", "true", "yes"}
    state = state_path or Path(environ.get("STATE_PATH", ".upstream-state.json"))
    base = ""
    head = ""
    status = FAILURE_STATUS
    issue_action = "none"
    try:
        data = load_state(state)
        base = data["last_integrated_sha"]
        upstream = parse_repo(data["upstream_source"])
        head = upstream_head(upstream, token, api_base)
        log(f"base={base} head={head} upstream={upstream}")
        if base == head:
            status = BASELINE_STATUS
        else:
            try:
                comparison = compare_upstream(upstream, base, head, token, api_base)
            except MonitorError as exc:
                if exc.status != 404:
                    raise
                comparison = {"status": "diverged"}
            status, details = classify(base, head, comparison)
            body = issue_body(upstream, base, head, status, details)
            issue_action = publish_issue(repo, token, api_base, body, dry_run)
    except MonitorError as exc:
        status = FAILURE_STATUS
        issue_action = f"error: {sanitize(str(exc), 120)}"
    except Exception as exc:  # fail closed on any unexpected error
        status = FAILURE_STATUS
        issue_action = f"error: {sanitize(type(exc).__name__, 120)}"
    write_summary(status, base or "unknown", head or "unknown", issue_action)
    return {
        "status": status,
        "base": base,
        "head": head,
        "issue_action": issue_action,
    }


def main() -> int:
    result = run()
    return 2 if result["status"] == FAILURE_STATUS else 0


if __name__ == "__main__":
    sys.exit(main())
