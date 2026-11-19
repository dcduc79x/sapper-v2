#!/usr/bin/env python3
"""Validate Conventional Commits 1.0.0 structure and ASCII dash policy."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

TYPES = {
    "feat",
    "fix",
    "refactor",
    "perf",
    "test",
    "docs",
    "style",
    "chore",
    "build",
    "ci",
    "revert",
}
FORBIDDEN = {0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212}
SUBJECT_RE = re.compile(r"^([a-z]+)(!)?(\([^)]+\))?: (.+)$")


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args])


def validate(sha: str, message: str) -> list[str]:
    issues: list[str] = []
    lines = message.splitlines()
    subject = lines[0] if lines else ""
    match = SUBJECT_RE.match(subject)
    if not match:
        issues.append("non-conventional subject")
    else:
        ctype = match.group(1)
        if ctype not in TYPES:
            issues.append(f"unknown type {ctype}")
        description = match.group(4).strip()
        if not description:
            issues.append("empty description")
        if match.group(3) == "()":
            issues.append("empty scope")
    if len(subject) > 72:
        issues.append(f"subject length {len(subject)} exceeds 72")
    for ch in message:
        if ord(ch) in FORBIDDEN:
            issues.append(f"forbidden dash U+{ord(ch):04X}")
            break
    return issues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--all", action="store_true", help="validate all reachable commits"
    )
    parser.add_argument("--range", dest="commit_range", help="validate commits in A..B")
    parser.add_argument(
        "--refs",
        nargs="+",
        help="maintained refs whose reachable commits are validated",
    )
    parser.add_argument("--message-file", help="validate a single commit message file")
    args = parser.parse_args()

    if args.message_file:
        message = open(args.message_file, encoding="utf-8", errors="replace").read()
        issues = validate("message-file", message)
        if issues:
            print("FAIL message-file: " + "; ".join(issues))
            return 1
        print("PASS message-file: Conventional Commits")
        return 0

    if not args.all and not args.commit_range and not args.refs:
        parser.error("one of --all, --range, --refs or --message-file is required")

    log_args = ["log", "--format=%H%x00%B%x00"]
    if args.refs:
        log_args.extend(args.refs)
    else:
        log_args.append("--all" if args.all else args.commit_range)
    parts = git(*log_args).decode("utf-8", "replace").split("\0")
    count = 0
    failures = 0
    for i in range(0, len(parts) - 1, 2):
        sha = parts[i].strip()
        if not sha:
            continue
        count += 1
        issues = validate(sha, parts[i + 1])
        if issues:
            failures += 1
            print(f"FAIL {sha[:8]}: " + "; ".join(issues))
    print(f"Commits validated: {count}")
    if failures:
        print(f"FAIL invalid commits: {failures}")
        return 1
    print("PASS all validated commits follow Conventional Commits")
    return 0


if __name__ == "__main__":
    sys.exit(main())
