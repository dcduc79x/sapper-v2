"""Schema validation for on-disk fact / intent / hint records.

The validator is deliberately table-driven: each check appends a fixed message
in a fixed order. Those messages and that order are part of the CLI contract --
`_read_node`, `_read_node_files` and `validate_graph` join them into user-facing
errors, and the characterization tests pin every string.
"""

from __future__ import annotations

from .model import _VALID_NODE_ID_RE, DATA_KEY_RE, FACT_KINDS, GOAL_ID

__all__ = ["node_schema_errors", "_node_schema_errors"]


def _id_errors(data: dict, kind: str) -> list[str]:
    """The id check is shared by every kind and yields at most one error."""
    node_id = data.get("id")
    if not isinstance(node_id, str):
        return ["missing string 'id'"]
    if node_id == GOAL_ID and kind != "fact":
        return ["reserved 'id' is only valid for the goal fact"]
    if node_id != GOAL_ID and not _VALID_NODE_ID_RE.fullmatch(node_id):
        return ["invalid 'id'"]
    return []


def _require_str(data: dict, key: str, errors: list[str]) -> None:
    if not isinstance(data.get(key), str):
        errors.append(f"missing string '{key}'")


def _optional_text(data: dict, key: str, errors: list[str]) -> None:
    """Absent is fine; present must be a string or null."""
    if key in data and data[key] is not None and not isinstance(data[key], str):
        errors.append(f"'{key}' must be a string or null")


def _required_nullable_str(data: dict, key: str, errors: list[str]) -> None:
    """Absent, or present as a non-null non-string, share one message."""
    if key not in data or (data[key] is not None and not isinstance(data[key], str)):
        errors.append(f"'{key}' must be a string or null")


def _fact_errors(data: dict, errors: list[str]) -> None:
    for key in ("description", "created_at", "creator"):
        _require_str(data, key, errors)

    # 'source_intent' has two distinct messages: legacy files omitting the key
    # report "missing", while a wrong type reports the type constraint.
    if "source_intent" not in data:
        errors.append("missing 'source_intent'")
    elif data["source_intent"] is not None and not isinstance(
        data["source_intent"], str
    ):
        errors.append("'source_intent' must be a string or null")

    for key in ("title", "doc"):
        _optional_text(data, key, errors)

    if "kind" in data and data["kind"] is not None:
        kind_value = data["kind"]
        if not isinstance(kind_value, str) or kind_value not in FACT_KINDS:
            errors.append(f"'kind' must be null or one of: {', '.join(FACT_KINDS)}")

    if "data" in data and data["data"] is not None:
        payload = data["data"]
        if not isinstance(payload, dict):
            errors.append("'data' must be an object or null")
        else:
            for key, value in payload.items():
                if not isinstance(key, str) or not DATA_KEY_RE.match(key):
                    errors.append("'data' key must match [A-Za-z0-9_.-]+")
                    break
                if not isinstance(value, (str, int, float)):
                    errors.append("'data' values must be scalars")
                    break


def _intent_errors(data: dict, errors: list[str]) -> None:
    from_ids = data.get("from")
    if (
        not isinstance(from_ids, list)
        or not from_ids
        or not all(
            isinstance(x, str) and x != GOAL_ID and _VALID_NODE_ID_RE.fullmatch(x)
            for x in from_ids
        )
    ):
        errors.append("'from' must be a list of strings")

    target = data.get("to")
    if "to" not in data or (
        target is not None
        and (
            not isinstance(target, str)
            or (target != GOAL_ID and not _VALID_NODE_ID_RE.fullmatch(target))
        )
    ):
        errors.append("'to' must be a string or null")

    for key in ("description", "created_at"):
        _require_str(data, key, errors)

    status = data.get("status")
    if not isinstance(status, str):
        errors.append("missing string 'status'")
    elif status not in {"open", "claimed", "done", "complete"}:
        errors.append("invalid 'status'")

    _required_nullable_str(data, "worker", errors)
    if not isinstance(data.get("requires_confirmation"), bool):
        errors.append("missing boolean 'requires_confirmation'")
    _required_nullable_str(data, "confirmed_at", errors)

    # Cross-field rules run after all field checks, in this exact order.
    if status in {"done", "complete"} and target is None:
        errors.append("completed status requires a target")
    if status in {"open", "claimed"} and target is not None:
        errors.append("open/claimed status cannot have a target")
    if status == "complete" and target != GOAL_ID:
        errors.append("complete status must target goal")
    if status == "done" and target == GOAL_ID:
        errors.append("done status cannot target goal")

    for key in ("title", "doc"):
        _optional_text(data, key, errors)


def _hint_errors(data: dict, errors: list[str]) -> None:
    for key in ("content", "creator", "created_at"):
        _require_str(data, key, errors)


def node_schema_errors(data: object, kind: str) -> list[str]:
    """Return schema errors for records used by renderers and graph logic."""
    if not isinstance(data, dict):
        return ["expected a JSON object"]
    errors: list[str] = []
    errors.extend(_id_errors(data, kind))
    if kind == "fact":
        _fact_errors(data, errors)
    elif kind == "intent":
        _intent_errors(data, errors)
    elif kind == "hint":
        _hint_errors(data, errors)
    return errors


# Facade-compatible alias: callers (and tests) import the leading underscore.
_node_schema_errors = node_schema_errors
