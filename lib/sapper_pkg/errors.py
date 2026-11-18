"""User-facing error type shared by the facade and extracted modules."""

__all__ = ["FGError"]


class FGError(Exception):
    """User-facing error; message is printed, exit code 1."""
