"""Filesystem locations derived from the real (symlink-resolved) library path.

The CLI is installed as a symlink to ``lib/sapper.py``; every resource lookup
must therefore resolve symlinks before deriving a path. Centralising this here
keeps the facade, the hook, and the template loader on identical paths.
"""

from pathlib import Path

__all__ = ["LIB_DIR", "REPO_ROOT", "TEMPLATES_DIR", "KB_DIR", "CLI_SCRIPT"]

_PKG_DIR = Path(__file__).resolve().parent
LIB_DIR = _PKG_DIR.parent
REPO_ROOT = LIB_DIR.parent
TEMPLATES_DIR = REPO_ROOT / "templates"
KB_DIR = REPO_ROOT / "kb"
# The executable entrypoint. Extracted command modules must use this instead
# of their own __file__ when spawning a subprocess CLI.
CLI_SCRIPT = LIB_DIR / "sapper.py"
