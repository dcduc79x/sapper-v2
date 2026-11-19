#!/usr/bin/env python3
"""Regenerate the Ruff baseline file from the current scoped findings."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

FINDING_RE = re.compile(r"^(.+?):(\d+):(\d+): ([A-Z][A-Z0-9]*) ")
OUTPUT = Path("ci/ruff-baseline.txt")


def main() -> int:
    proc = subprocess.run(
        ["uvx", "ruff", "check", "--output-format", "concise", "lib", "tests"],
        capture_output=True,
        text=True,
    )
    identities: set[str] = set()
    for line in (proc.stdout + "\n" + proc.stderr).splitlines():
        match = FINDING_RE.match(line.strip())
        if match:
            identities.add(
                f"{match.group(1)}:{match.group(2)}:{match.group(3)}:{match.group(4)}"
            )
    if not identities:
        print("FAIL no Ruff findings parsed; refusing to write an empty baseline")
        return 1
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text("\n".join(sorted(identities)) + "\n", encoding="utf-8")
    print(f"Wrote {len(identities)} findings to {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
