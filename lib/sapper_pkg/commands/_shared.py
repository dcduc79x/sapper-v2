"""Compatibility bridge for extracted command handlers.

Facade callables are exposed as lazy proxies that resolve on *every* call, so
tests patching ``sapper.run_claude`` / ``sapper._tmux`` still intercept calls
made from the extracted handlers. Package-internal values are imported
directly; only facade-owned helpers are proxied.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

# When the CLI runs as a script the facade is __main__, not "sapper".
_main = sys.modules.get("__main__")
if (
    _main is not None
    and getattr(_main, "build_parser", None) is not None
    and Path(getattr(_main, "__file__", "")).resolve()
    == Path(__file__).resolve().parents[2] / "sapper.py"
):
    _facade = _main
else:
    import sapper as _facade

from ..config import AI_CHANNEL_NAME, DEFAULT_STORE, LOCK_NAME, PEER_NAME_RE, PEERS_FILE
from ..engine import extract_json_object
from ..errors import FGError
from ..graph import build_graph_view, frontier, is_open, recall, validate_graph
from ..model import GOAL_ID
from ..render import render_graph_text, render_status
from ..storage import Store, _UNSET

_direct = set(globals())


def _proxy(name: str):
    def _call(*args, **kwargs):
        return getattr(_facade, name)(*args, **kwargs)

    _call.__name__ = name
    return _call


for _name in dir(_facade):
    if _name.startswith("__") or _name in _direct:
        continue
    value = getattr(_facade, _name)
    if callable(value):
        globals()[_name] = _proxy(_name)
    elif not hasattr(value, "__path__") and not hasattr(value, "__spec__"):
        # Constants/dicts such as FLEET_TEMPLATE and _KB_ACTIONS.
        globals()[_name] = value

# Star-imports skip private names by default; expose the proxied facade helpers too.
__all__ = [n for n in globals() if not n.startswith("__")]
