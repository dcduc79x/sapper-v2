"""Store/channel/peer constants shared by the facade and command modules."""

from .storage import LOCK_NAME

__all__ = ["DEFAULT_STORE", "LOCK_NAME", "PEERS_FILE", "AI_CHANNEL_NAME", "PEER_NAME_RE"]

DEFAULT_STORE = ".sapper"
PEERS_FILE = "ai-peers.json"
AI_CHANNEL_NAME = "ai-channel.txt"
PEER_NAME_RE = __import__("re").compile(r"^[A-Za-z0-9_.-]+$")
