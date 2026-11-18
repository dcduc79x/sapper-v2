"""Read-only graph analysis: predicates, validation, frontier and recall."""

from __future__ import annotations

import difflib
import json
import re

from .errors import FGError
from .model import GOAL_ID
from .schema import _node_schema_errors
from .storage import Store, read_json

__all__ = [
    "is_open", "is_ready", "build_graph_view", "validate_graph", "frontier",
    "recall", "_recall_tokenize", "_recall_score",
]


def is_open(intent: dict) -> bool:
    return intent["to"] is None


def is_ready(intent: dict, fact_ids: set[str] | None = None) -> bool:
    """Whether an intent can be worked safely.

    ``fact_ids`` is optional for callers that only need the state/confirmation
    check.  The frontier passes the current set so a manually deleted or
    corrupted dependency cannot remain actionable.
    """
    if not is_open(intent):
        return False
    if fact_ids is not None and not set(intent.get("from", ())).issubset(fact_ids):
        return False
    if intent.get("requires_confirmation") and not intent.get("confirmed_at"):
        return False
    return True


def build_graph_view(store: Store) -> dict:
    """Materialize the whole graph for prompt rendering / display."""
    facts = store.list_facts()
    intents = store.list_intents()
    hints = store.list_hints()
    fact_ids = {f["id"] for f in facts}
    open_intents = [i for i in intents if is_open(i)]
    done_intents = [i for i in intents if not is_open(i)]
    return {
        "project": store.load_project(),
        "facts": facts,
        "intents": intents,
        "open_intents": open_intents,
        "done_intents": done_intents,
        "hints": hints,
        "fact_ids": sorted(fact_ids),
    }


def _validate_project(project: dict) -> list[str]:
    """Project-level metadata checks (first phase of the audit)."""
    errors: list[str] = []
    if project.get("status") not in {"active", "completed"}:
        errors.append(
            f"project status must be 'active' or 'completed', got {project.get('status')!r}"
        )
    counters = project.get("counters", {})
    for kind in ("fact", "intent", "hint", "run"):
        if kind not in counters:
            errors.append(f"project counters missing {kind!r}")
    return errors


def _audit_nodes(store: Store) -> tuple[dict[str, dict[str, dict]], list[str]]:
    """Read every node file, reporting damage and indexing valid nodes by id."""
    errors: list[str] = []
    by_kind: dict[str, dict[str, dict]] = {"fact": {}, "intent": {}, "hint": {}}
    dirs = {"fact": store.facts_dir, "intent": store.intents_dir, "hint": store.hints_dir}
    for kind, directory in dirs.items():
        for path in sorted(directory.glob("*.json")):
            try:
                data = read_json(path)
            except (json.JSONDecodeError, UnicodeDecodeError, OSError, TypeError, ValueError) as exc:
                errors.append(f"{kind} {path.name}: unreadable JSON ({exc})")
                continue
            schema = _node_schema_errors(data, kind)
            if schema:
                errors.append(f"{kind} {path.name}: {'; '.join(schema)}")
                continue
            node_id = data["id"]
            if path.stem != node_id:
                errors.append(f"{kind} {path.name}: filename does not match id {node_id!r}")
            if node_id in by_kind[kind]:
                errors.append(f"duplicate {kind} id {node_id!r}")
            by_kind[kind][node_id] = data
    return by_kind, errors


def _validate_goal(facts: dict[str, dict], project: dict) -> list[str]:
    if GOAL_ID not in facts:
        return ["missing terminal goal fact"]
    if facts[GOAL_ID].get("description") != project.get("goal"):
        return ["goal fact description does not match project goal"]
    return []


def _validate_intent_links(facts: dict[str, dict], intents: dict[str, dict]) -> list[str]:
    errors: list[str] = []
    for iid, intent in intents.items():
        for fid in intent["from"]:
            if fid not in facts:
                errors.append(f"intent {iid}: missing dependency fact {fid}")
            elif fid == GOAL_ID:
                errors.append(f"intent {iid}: goal cannot be a dependency")
        target = intent["to"]
        if target is not None and target != GOAL_ID and target not in facts:
            errors.append(f"intent {iid}: target fact {target} does not exist")
        elif target is not None and target != GOAL_ID and facts[target].get("source_intent") != iid:
            errors.append(f"intent {iid}: target fact {target} does not name it as source intent")
    return errors


def _validate_fact_links(facts: dict[str, dict], intents: dict[str, dict]) -> list[str]:
    errors: list[str] = []
    for fid, fact in facts.items():
        source = fact.get("source_intent")
        if source is not None and source not in intents:
            errors.append(f"fact {fid}: source intent {source} does not exist")
        elif source is not None and intents[source].get("to") != fid:
            errors.append(f"fact {fid}: source intent {source} does not point to this fact")
    return errors


def _validate_completion(intents: dict[str, dict], project: dict) -> list[str]:
    errors: list[str] = []
    goal_intents = [i for i in intents.values() if i["to"] == GOAL_ID]
    if project.get("status") == "completed" and not goal_intents:
        errors.append("project is completed but no intent targets the goal")
    if project.get("status") == "active" and goal_intents:
        errors.append("project is active although an intent already targets the goal")
    return errors


def validate_graph(store: Store) -> list[str]:
    """Return integrity errors without mutating the graph.

    Normal read commands deliberately skip malformed node files so a damaged
    graph remains inspectable.  This explicit audit command gives operators a
    complete, actionable report before they dispatch agents or commit `.sapper/`.
    """
    errors: list[str] = []
    try:
        project = store.load_project()
    except FGError as exc:
        return [str(exc)]
    errors.extend(_validate_project(project))
    by_kind, node_errors = _audit_nodes(store)
    errors.extend(node_errors)
    facts, intents = by_kind["fact"], by_kind["intent"]
    errors.extend(_validate_goal(facts, project))
    errors.extend(_validate_intent_links(facts, intents))
    errors.extend(_validate_fact_links(facts, intents))
    errors.extend(_validate_completion(intents, project))
    return errors


def frontier(store: Store) -> list[dict]:
    intents = store.list_intents()
    fact_ids = {f["id"] for f in store.list_facts()}
    ready = [i for i in intents if is_ready(i, fact_ids)]
    # claimed intents first (someone's already on them but they're still in-flight),
    # then by creation order.
    ready.sort(key=lambda i: (i["status"] != "claimed", i["created_at"]))
    return ready


# --------------------------------------------------------------------------- #
# renderers
# --------------------------------------------------------------------------- #
_RECALL_TOKEN_RE = re.compile(r"[\w一-鿿]+", re.UNICODE)


def _recall_tokenize(text: str) -> list[str]:
    """Lowercase + Unicode-word/CJK tokenization. CJK each char is a token so
    '空指针定位' splits into 4 tokens that match individually."""
    if not text:
        return []
    out: list[str] = []
    for tok in _RECALL_TOKEN_RE.findall(text.lower()):
        # CJK runs: split into individual chars (each is content-bearing)
        if any("一" <= ch <= "鿿" for ch in tok):
            out.extend(list(tok))
        else:
            out.append(tok)
    return [t for t in out if t]


def _recall_score(query: str, node: dict) -> float:
    """Score a node against a free-text query. Combines:
      - token overlap on title (weighted x2) and description
      - difflib ratio against the joined haystack
    Returns 0.0 if nothing useful matches."""
    title = (node.get("title") or "").lower()
    desc = (node.get("description") or "").lower()
    haystack = f"{title} {title} {desc}".strip()  # title weighted x2
    if not haystack:
        return 0.0
    q_tokens = set(_recall_tokenize(query))
    if not q_tokens:
        return 0.0
    h_tokens = set(_recall_tokenize(haystack))
    if not h_tokens:
        return 0.0
    overlap = len(q_tokens & h_tokens)
    if overlap == 0:
        # cheap difflib fallback for fuzzy matches (typo, partial English word)
        ratio = difflib.SequenceMatcher(None, query.lower(), haystack).ratio()
        return ratio if ratio >= 0.45 else 0.0
    overlap_score = overlap / max(1, len(q_tokens))
    # add a small ratio bonus so longer aligned chunks edge out shorter ones
    ratio_bonus = 0.2 * difflib.SequenceMatcher(None, query.lower(), haystack).ratio()
    return overlap_score + ratio_bonus


def recall(store: Store, query: str, limit: int = 5,
           kinds: tuple[str, ...] = ("fact", "intent")) -> list[dict]:
    """Return top-N nodes by similarity to `query`. Each returned dict has:
        id, kind, title, description, has_doc, score
    Ordered by score desc, then by id (stable).
    """
    if not query or not query.strip():
        return []
    candidates: list[dict] = []
    if "fact" in kinds:
        for f in store.list_facts():
            if f.get("id") == GOAL_ID:
                continue  # skip the terminal goal node
            s = _recall_score(query, f)
            if s > 0:
                candidates.append({
                    "id": f["id"],
                    "kind": "fact",
                    "title": f.get("title"),
                    "description": f.get("description", ""),
                    "has_doc": bool(f.get("doc")),
                    "score": s,
                })
    if "intent" in kinds:
        for i in store.list_intents():
            s = _recall_score(query, i)
            if s > 0:
                candidates.append({
                    "id": i["id"],
                    "kind": "intent",
                    "title": i.get("title"),
                    "description": i.get("description", ""),
                    "has_doc": bool(i.get("doc")),
                    "score": s,
                })
    candidates.sort(key=lambda c: (-c["score"], c["id"]))
    return candidates[:limit]


