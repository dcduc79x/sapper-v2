"""Internal modules extracted from the monolithic ``sapper.py`` facade.

This package is an implementation detail of the ``sapper`` CLI. The public
interface remains the flat ``sapper`` module (and the ``sapper`` executable),
which re-exports everything consumers import today.
"""

__all__ = ["errors", "model", "paths", "schema"]
