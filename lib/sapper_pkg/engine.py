"""Model-CLI invocation, quota/key rotation and LLM JSON envelope parsing.

The engine is ``claude -p --output-format json`` by default; ``SAPPER_BIN`` may
replace it with any command speaking the same stdin/stdout contract.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from .errors import FGError

__all__ = [
    "_extract_first_balanced",
    "extract_json_object",
    "_QUOTA_ERROR_RE",
    "_load_key_pool",
    "_is_quota_error",
    "_rotate_key",
    "run_claude",
    "_run_engine_once",
]


def _extract_first_balanced(text: str, start: int) -> tuple[dict | None, int]:
    """Scan from `start` (a '{') to its matching '}'. Return (parsed|None, end).
    parsed is None if the balanced object is not valid JSON - caller should try
    the next '{'. end is the index past the closing '}' (or len(text))."""
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    blob = text[start : i + 1]
                    try:
                        return json.loads(blob), i + 1
                    except json.JSONDecodeError:
                        return None, i + 1
    # ran off the end with depth>0 → unterminated
    return None, len(text)


def extract_json_object(text: str) -> dict:
    """Find the first VALID JSON object in `text`. Tolerates a prose preamble
    that contains stray braces (e.g. '{step 1}') by trying each balanced
    object in turn until one parses (review H3)."""
    if not text or not text.strip():
        raise FGError("empty model output")
    pos = 0
    last_error = None
    while True:
        start = text.find("{", pos)
        if start == -1:
            break
        obj, end = _extract_first_balanced(text, start)
        if obj is not None:
            return obj
        # remember the failure for a useful error, then look past this object
        blob = text[start : min(end, len(text))]
        last_error = blob[:200]
        pos = end if end > start else start + 1
    if last_error is not None:
        raise FGError(f"model returned no valid JSON object; nearest={last_error!r}")
    raise FGError(f"no JSON object in model output: {text[:200]!r}")


# --------------------------------------------------------------------------- #
# claude dispatch
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# key-pool rotation - swap the model gateway credentials when a key runs dry.
#
# SAPPER_KEY_POOL is a JSON array of {"base_url","token","model"} entries.
# On a quota/balance/auth error the next entry is loaded into os.environ and
# the very next engine call uses it - no process restart needed (the -p loop
# spawns a fresh engine per call). The last entry simply keeps retrying.
# --------------------------------------------------------------------------- #
_QUOTA_ERROR_RE = re.compile(
    r"insufficient|balance|quota|exceeded|arrearage|billing|payment|"
    r"401|unauthorized|invalid.{0,12}api.?key|欠费|余额",
    re.IGNORECASE,
)


def _load_key_pool() -> list[dict]:
    raw = os.environ.get("SAPPER_KEY_POOL", "").strip()
    if not raw:
        return []
    try:
        pool = json.loads(raw)
    except json.JSONDecodeError:
        sys.stderr.write("[sapper] SAPPER_KEY_POOL is not valid JSON - ignoring\n")
        return []
    out = []
    for item in pool if isinstance(pool, list) else []:
        if (
            isinstance(item, dict)
            and isinstance(item.get("token"), str)
            and item["token"].strip()
        ):
            out.append(
                {
                    "base_url": item.get("base_url")
                    or os.environ.get("ANTHROPIC_BASE_URL", ""),
                    "token": item["token"].strip(),
                    "model": item.get("model")
                    or os.environ.get("SAPPER_MODEL", "sonnet"),
                }
            )
    return out


def _is_quota_error(message: str) -> bool:
    return bool(_QUOTA_ERROR_RE.search(message or ""))


def _rotate_key() -> bool:
    """Advance to the next pool entry. Returns False if pool is empty."""
    pool = _load_key_pool()
    if not pool:
        return False
    current = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
    start = -1
    for i, entry in enumerate(pool):
        if entry["token"] == current:
            start = i
            break
    nxt = pool[(start + 1) % len(pool)]
    if start >= 0 and nxt["token"] == current:
        return True  # single-entry pool: keep the key, caller retries
    os.environ["ANTHROPIC_BASE_URL"] = nxt["base_url"]
    os.environ["ANTHROPIC_AUTH_TOKEN"] = nxt["token"]
    os.environ["SAPPER_MODEL"] = nxt["model"]
    sys.stderr.write(
        f"[sapper] key rotated -> {nxt['base_url']} model={nxt['model']} "
        f"(token …{nxt['token'][-6:]})\n"
    )
    return True


def run_claude(
    prompt: str,
    *,
    model: str,
    timeout: int,
    skip_permissions: bool,
    append_system: str | None,
    cwd: Path,
) -> dict:
    """Call the agent CLI and return the parsed JSON result envelope.

    The engine is `claude -p --output-format json` by default. Set
    SAPPER_BIN to any command prefix (e.g. "codex-wrapper" or a script that
    wraps another CLI) - it must accept the prompt on stdin and print a
    claude-compatible envelope {"result": "..."} on stdout. --model and
    --dangerously-skip-permissions / --append-system-prompt are appended
    claude-style, so non-claude engines should ignore or translate them in
    the wrapper."""
    if timeout < 1:
        raise FGError("claude timeout must be at least 1 second")
    if not isinstance(model, str) or not model.strip():
        raise FGError("claude model cannot be empty")
    prefix = os.environ.get("SAPPER_BIN", "claude").split()
    argv = prefix + [
        "-p",
        "--output-format",
        "json",
        "--model",
        model,
    ]
    if skip_permissions:
        argv.append("--dangerously-skip-permissions")
    if append_system:
        argv += ["--append-system-prompt", append_system]
    engine = prefix[0]
    attempts = 1 + max(0, int(os.environ.get("SAPPER_RETRY_MODEL", "0")))
    last_error: FGError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return _run_engine_once(
                argv,
                prompt,
                timeout,
                cwd,
                engine,
            )
        except FGError as exc:
            last_error = exc
            if "CLI not found" in str(exc):
                raise  # not transient
            if _is_quota_error(str(exc)) and _rotate_key():
                sys.stderr.write(
                    f"[sapper] quota/auth error (attempt {attempt}/{attempts}); "
                    "key rotated - retrying immediately\n"
                )
                continue
            if attempt < attempts:
                backoff = min(120, 10 * (2 ** (attempt - 1)))
                sys.stderr.write(
                    f"[sapper] engine call failed (attempt {attempt}/{attempts}): "
                    f"{exc}; retrying in {backoff}s\n"
                )
                time.sleep(backoff)
    assert last_error is not None
    raise last_error


def _run_engine_once(argv, prompt, timeout, cwd, engine) -> dict:
    try:
        proc = subprocess.run(
            argv,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd),
        )
    except FileNotFoundError:
        raise FGError(
            f"`{engine}` CLI not found on PATH; install it or fix SAPPER_BIN."
        )
    except subprocess.TimeoutExpired:
        raise FGError(f"{engine} dispatch timed out after {timeout}s")
    if proc.returncode != 0:
        raise FGError(f"{engine} exited {proc.returncode}: {proc.stderr.strip()[:500]}")
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise FGError(
            f"{engine} did not return JSON. stdout[:300]={proc.stdout[:300]!r}"
        )
    if not isinstance(envelope, dict):
        raise FGError(f"{engine} returned a JSON value instead of an object envelope")
    if envelope.get("is_error"):
        result = envelope.get("result", "")
        raise FGError(f"{engine} reported error: {str(result)[:300]}")
    if not isinstance(envelope.get("result", ""), str):
        raise FGError("response envelope has a non-string result")
    return envelope


# --------------------------------------------------------------------------- #
# command handlers
# --------------------------------------------------------------------------- #
