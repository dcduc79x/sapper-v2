#!/usr/bin/env python3
"""Compare current Ruff findings with a baseline by finding identity."""

from __future__ import annotations

import re
import sys

FINDING_RE = re.compile(r"^(.+?):(\d+):(\d+): ?([A-Z][A-Z0-9]*)(?: |$)")


def identities(path: str) -> set[str]:
    found: set[str] = set()
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            match = FINDING_RE.match(line.strip())
            if match:
                found.add(
                    f"{match.group(1)}:{match.group(2)}:{match.group(3)}:{match.group(4)}"
                )
    return found


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: check-ruff-baseline.py BASELINE CURRENT STATUS")
        return 2
    status_text = sys.argv[3]
    try:
        status = int(status_text)
    except ValueError:
        print(f"FAIL invalid Ruff status: {status_text}")
        return 2
    if status not in (0, 1):
        print(f"FAIL Ruff tool execution error, exit status {status}")
        return 2
    baseline = identities(sys.argv[1])
    current = identities(sys.argv[2])
    new = sorted(current - baseline)
    missing = sorted(baseline - current)
    print(f"Baseline findings: {len(baseline)}")
    print(f"Current findings:  {len(current)}")
    for item in new:
        print(f"NEW {item}")
    for item in missing:
        print(f"MISSING {item}")
    if new or missing:
        print("FAIL Ruff finding identities differ from baseline")
        return 1
    print("PASS Ruff findings match baseline by identity")
    return 0


if __name__ == "__main__":
    sys.exit(main())
