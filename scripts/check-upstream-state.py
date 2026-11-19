#!/usr/bin/env python3
"""Validate upstream tracking state and preserve the verified baseline."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

CANONICAL = "https://github.com/dcduc79x/sapper-v2.git"
UPSTREAM = "https://github.com/N1nEmAn/sapper-v2.git"
BASELINE = "7b75e6f1f5d5b4ae0c55878b0dd0994d9b08f1fb"
CLEAN_TIP = "e22c17e9ddb2eff89146d39fb21e75b9913204a2"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
FORBIDDEN = {0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212}
STATE = Path(".upstream-state.json")


def main() -> int:
    raw = STATE.read_text(encoding="utf-8")
    if any(ord(ch) in FORBIDDEN for ch in raw):
        print("FAIL forbidden dash in .upstream-state.json")
        return 1
    data = json.loads(raw)
    required = {
        "canonical_repository": CANONICAL,
        "upstream_source": UPSTREAM,
        "baseline_sha": BASELINE,
        "rewritten_clean_tip": CLEAN_TIP,
    }
    for key, expected in required.items():
        if data.get(key) != expected:
            print(f"FAIL {key}: expected {expected}, got {data.get(key)!r}")
            return 1
    last = data.get("last_integrated_sha")
    if not isinstance(last, str) or not SHA_RE.match(last):
        print("FAIL last_integrated_sha must be a 40 char lowercase hex SHA")
        return 1
    log = data.get("integration_log")
    if not isinstance(log, list):
        print("FAIL integration_log must be a list")
        return 1
    for entry in log:
        if not isinstance(entry, dict):
            print("FAIL integration_log entries must be objects")
            return 1
        if not SHA_RE.match(str(entry.get("upstream_sha", ""))):
            print("FAIL integration_log entry missing valid upstream_sha")
            return 1
        if entry.get("source_url") != UPSTREAM:
            print("FAIL integration_log entry source_url mismatch")
            return 1
    print("PASS upstream state: verified baseline and attribution present")
    print(f"Baseline SHA: {BASELINE}")
    print(f"Clean tip:    {CLEAN_TIP}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
