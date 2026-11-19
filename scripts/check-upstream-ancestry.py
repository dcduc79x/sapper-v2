#!/usr/bin/env python3
"""Verify upstream SHA ancestry on the intended maintained refs."""

from __future__ import annotations

import argparse
import subprocess
import sys

BASELINE = "7b75e6f1f5d5b4ae0c55878b0dd0994d9b08f1fb"
CLEAN_TIP = "71084d8f477d0e215c317cb75fd14321c81e0fdd"


def git_ok(*args: str) -> bool:
    return subprocess.run(["git", *args], capture_output=True).returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refs", nargs="+", required=True)
    parser.add_argument("--mirror", help="optional external upstream mirror path")
    args = parser.parse_args()

    failures = 0
    for ref in args.refs:
        if not git_ok("rev-parse", "--verify", ref):
            print(f"FAIL ref not found: {ref}")
            failures += 1
            continue
        if not git_ok("merge-base", "--is-ancestor", CLEAN_TIP, ref):
            print(f"FAIL clean tip is not an ancestor of {ref}")
            failures += 1
        if git_ok("merge-base", "--is-ancestor", BASELINE, ref):
            print(f"FAIL upstream baseline is an ancestor of {ref}")
            failures += 1
        print(f"CHECKED {ref}: clean tip reachable, upstream baseline absent")

    if args.mirror:
        upstream = subprocess.check_output(
            ["git", "-C", args.mirror, "rev-list", "--all"], text=True
        ).split()
        reachable = set(
            subprocess.check_output(["git", "rev-list", *args.refs], text=True).split()
        )
        leaked = sorted(set(upstream) & reachable)
        if leaked:
            print(f"FAIL upstream commit objects reachable: {len(leaked)}")
            for sha in leaked[:8]:
                print(f"  LEAK {sha}")
            failures += 1
        else:
            print(f"PASS no upstream commits reachable from {len(args.refs)} refs")

    if failures:
        print(f"FAIL ancestry checks: {failures}")
        return 1
    print("PASS exact SHA ancestry")
    return 0


if __name__ == "__main__":
    sys.exit(main())
