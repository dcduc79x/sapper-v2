#!/usr/bin/env python3
"""Check forbidden Unicode dash characters in tracked content and commits."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

FORBIDDEN = {
    0x2010: "U+2010",
    0x2011: "U+2011",
    0x2012: "U+2012",
    0x2013: "U+2013",
    0x2014: "U+2014",
    0x2015: "U+2015",
    0x2212: "U+2212",
}


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args])


def scan(text: str, scope: str, violations: list[str]) -> None:
    for lineno, line in enumerate(text.splitlines(), 1):
        for ch in line:
            code = ord(ch)
            if code in FORBIDDEN:
                violations.append(f"{scope}:{lineno}:{FORBIDDEN[code]}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--all", action="store_true", help="scan all refs (explicit use only)"
    )
    parser.add_argument("--range", dest="commit_range", help="scan commits in A..B")
    parser.add_argument(
        "--refs", nargs="+", help="maintained refs whose reachable commits are scanned"
    )
    args = parser.parse_args()

    violations: list[str] = []

    tracked = [os.fsdecode(p) for p in git("ls-files", "-z").split(b"\0") if p]
    for path in tracked:
        for ch in path:
            if ord(ch) in FORBIDDEN:
                violations.append(f"filename:{path}:{FORBIDDEN[ord(ch)]}")
        try:
            data = open(path, "rb").read()
        except OSError:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        scan(text, path, violations)

    staged = git("diff", "--cached", "--unified=0").decode("utf-8", "replace")
    scan(staged, "staged-diff", violations)

    if args.all or args.commit_range or args.refs:
        log_args = ["log", "--format=%H%x00%B%x00"]
        if args.refs:
            log_args.extend(args.refs)
        else:
            log_args.append("--all" if args.all else args.commit_range)
        parts = git(*log_args).decode("utf-8", "replace").split("\0")
        for i in range(0, len(parts) - 1, 2):
            sha = parts[i].strip()
            msg = parts[i + 1]
            scan(msg, f"commit:{sha[:8]}", violations)

    untracked = [
        os.fsdecode(p)
        for p in git("ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
        if p
    ]
    print(f"Tracked files scanned: {len(tracked)}")
    print(f"EXCLUDED untracked files (not validated): {len(untracked)}")
    if untracked:
        print("Excluded untracked paths are not part of the maintained tree.")
        for path in untracked[:8]:
            print(f"  EXCLUDED {path}")

    if violations:
        print(f"FAIL forbidden dash occurrences: {len(violations)}")
        for item in violations[:40]:
            print(f"  {item}")
        if len(violations) > 40:
            print(f"  ... and {len(violations) - 40} more")
        return 1
    print("PASS tracked files, staged diff and selected commits are dash-clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
