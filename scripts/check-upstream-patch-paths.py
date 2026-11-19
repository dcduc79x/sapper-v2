#!/usr/bin/env python3
"""Flag upstream patch paths that need manual architecture mapping."""

from __future__ import annotations

import argparse
import sys

HIGH_CONFLICT = (
    "lib/sapper.py",
    "lib/sapper_pkg/",
    "tests/",
    "pyproject.toml",
    "scripts/",
    ".upstream-state.json",
    "UPSTREAM_SYNC.md",
    ".github/workflows/",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("patch")
    parser.add_argument("--ack", action="store_true", help="acknowledge manual review")
    args = parser.parse_args()

    flagged: list[str] = []
    for line in open(args.patch, encoding="utf-8", errors="replace"):
        if line.startswith("+++ b/"):
            path = line[6:].strip()
            if any(path == item or path.startswith(item) for item in HIGH_CONFLICT):
                flagged.append(path)
    flagged = sorted(set(flagged))
    print(f"Patch paths requiring review: {len(flagged)}")
    for path in flagged:
        print(f"  REVIEW {path}")
    if flagged and not args.ack:
        print("FAIL high-conflict paths need manual mapping and --ack")
        return 1
    print("PASS patch path review recorded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
