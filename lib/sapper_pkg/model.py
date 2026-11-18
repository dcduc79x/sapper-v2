"""Graph domain constants, ID validation, and typed-fact normalization.

No I/O lives here: these helpers are pure and shared by the facade, the schema
validator, the store, and the prompt renderers.
"""

from __future__ import annotations

import re
import time

from .errors import FGError

__all__ = [
    "GOAL_ID",
    "FACT_KINDS",
    "DATA_KEY_RE",
    "VALID_NODE_ID_RE",
    "normalize_fact_kind",
    "normalize_fact_data",
    "parse_kv_pairs",
    "now_iso",
]

GOAL_ID = "goal"

# Offensive-work fact kinds. A typed fact carries structured `data`
# (key=value scalars) so hosts/creds/vulns/flags can be counted, deduped
# and cross-referenced instead of buried in prose.
FACT_KINDS = ("host", "service", "cred", "vuln", "loot", "flag", "note")
DATA_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

VALID_NODE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,16}$")
# Backwards-compatible private alias used by the facade and schema module.
_VALID_NODE_ID_RE = VALID_NODE_ID_RE


def normalize_fact_kind(kind: str | None) -> str | None:
    """Validate + canonicalize a fact kind. None/empty -> None (untyped)."""
    if kind is None:
        return None
    k = kind.strip().lower()
    if not k:
        return None
    if k not in FACT_KINDS:
        raise FGError(
            f"unknown fact kind {kind!r}; expected one of: {', '.join(FACT_KINDS)}"
        )
    return k


def normalize_fact_data(data: dict | None) -> dict[str, str] | None:
    """Validate fact `data`: flat object, scalar values, safe keys."""
    if data is None:
        return None
    if not isinstance(data, dict):
        raise FGError("fact data must be an object of key=value scalars")
    out: dict[str, str] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not DATA_KEY_RE.match(key):
            raise FGError(f"fact data key {key!r} must match [A-Za-z0-9_.-]+")
        if value is None:
            continue
        if isinstance(value, bool):
            value = "true" if value else "false"
        if not isinstance(value, (str, int, float)):
            raise FGError(f"fact data value for {key!r} must be a scalar")
        out[key] = str(value)
    return out or None


def parse_kv_pairs(pairs: list[str] | None) -> dict[str, str]:
    """Parse CLI `--data key=value` repeatable args into a dict."""
    out: dict[str, str] = {}
    for item in pairs or []:
        if "=" not in item:
            raise FGError(f"--data expects key=value, got {item!r}")
        key, _, value = item.partition("=")
        key = key.strip()
        if not key:
            raise FGError(f"--data key cannot be empty: {item!r}")
        out[key] = value
    return out


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
