"""Human- and prompt-facing renderers over the fact graph.

Prompt-safety scrubbing lives here so every renderer shares the same controls.
"""

from __future__ import annotations

import json
import re

from .graph import build_graph_view, frontier, is_open, recall
from .storage import Store

__all__ = [
    "render_status",
    "render_recall",
    "render_history_block",
    "render_graph_text",
    "render_prompt",
    "render_intent_prompt",
    "_replace_placeholders",
    "_intent_brief",
    "_hint_brief",
    "_to_yaml_like",
    "_scrub_inline",
    "_prompt_inline",
]


_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]+")


def _scrub_inline(text: str) -> str:
    """Strip control characters (newlines, tabs, etc.) so a single node's
    title/description always occupies one line in rendered output. This prevents
    a hostile node from spoofing extra rows in the history/recall/graph view."""
    return _CONTROL_CHAR_RE.sub(" ", text) if text else ""


def _prompt_inline(text: str) -> str:
    """Make graph text safe to place inside markdown prompt fences."""
    return _scrub_inline(text).replace("`", "'")


# --------------------------------------------------------------------------- #
# graph helpers
# --------------------------------------------------------------------------- #
def render_status(store: Store) -> str:
    g = build_graph_view(store)
    p = g["project"]
    lines = []
    lines.append(
        f"project: {_scrub_inline(p.get('id') or '')}  "
        f"status: {_scrub_inline(p['status'])}"
    )
    lines.append(f"origin: {_scrub_inline(p['origin'])}")
    lines.append(f"goal:   {_scrub_inline(p['goal'])}")
    lines.append(
        f"counts: {len(g['facts'])} facts, {len(g['open_intents'])} open / "
        f"{len(g['done_intents'])} done intents, {len(g['hints'])} hints"
    )
    typed: dict[str, int] = {}
    for f in g["facts"]:
        k = f.get("kind")
        if k:
            typed[k] = typed.get(k, 0) + 1
    if typed:
        summary = ", ".join(
            f"{n} {k}s" if k != "flag" else f"{n} flag(s)" for k, n in typed.items()
        )
        lines.append(f"kinds:  {summary}")
    flags = [f for f in g["facts"] if f.get("kind") == "flag"]
    for f in flags:
        d = f.get("data") or {}
        ch = f" challenge={d['challenge']}" if d.get("challenge") else ""
        lines.append(
            f"flag:   {f['id']} status={d.get('status', 'recorded')}{ch} value={_scrub_inline(str(d.get('value', '')))[:48]}"
        )
    fr = frontier(store)
    pool = []
    if fr:
        lines.append("")
        lines.append("frontier (ready to work):")
        for i in fr:
            flag = ""
            if i["requires_confirmation"] and not i["confirmed_at"]:
                flag = "  [needs confirmation]"
            elif i["status"] == "claimed":
                flag = f"  [claimed by {i['worker']}]"
            lines.append(
                f"  {i['id']}  from {i['from']}  {_scrub_inline(i['description'])[:80]}{flag}"
            )
    elif g["open_intents"]:
        lines.append("")
        lines.append("(open intents exist but none ready - check confirmations)")
    else:
        lines.append("")
        lines.append("(no open intents - dispatch a reason step or complete)")
    if g["hints"]:
        lines.append("")
        lines.append(f"hints: {len(g['hints'])} (use `sapper hint list`)")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# recall - "have we done something like this before?"
#
# The model receives a task; before acting, it should consult the graph for
# prior facts/intents whose title or description overlaps. This is the
# "experience reuse" entry point: cheap substring + difflib similarity, no
# embedding, no deps. Recall is by design TITLE+DESCRIPTION only - the heavy
# `doc` field is opt-in via `sapper doc <id>` once a candidate looks promising.
# --------------------------------------------------------------------------- #
def render_recall(matches: list[dict], query: str) -> str:
    """Human-readable recall output. First line is a usage hint so the
    caller (model or human) knows the next step is `sapper doc <id>`."""
    if not matches:
        return f"(no similar prior work for {query!r})"
    lines = [
        f"# recall for: {query}",
        "# use `sapper doc <id>` to read the detailed document",
        "",
    ]
    for m in matches:
        title = f"[{_scrub_inline(m['title'])}]  " if m["title"] else ""
        doc = "  📄" if m["has_doc"] else ""
        desc = _scrub_inline(m["description"])[:90]
        lines.append(
            f"  {m['id']} ({m['kind']}, score={m['score']:.2f})  {title}{desc}{doc}"
        )
    return "\n".join(lines)


def render_history_block(
    store: Store, *, max_facts: int = 8, max_intents: int = 4
) -> str:
    """Compact history snapshot for hook injection: recent confirmed facts
    + recently-done intents, title + one-line description only. The model
    eyeballs this to decide whether it has seen the current request before."""
    facts = [f for f in store.list_facts() if f.get("id") != GOAL_ID]
    # facts are immutable; show most recent first
    facts.sort(key=lambda f: f.get("created_at", ""), reverse=True)
    done = [i for i in store.list_intents() if not is_open(i)]
    done.sort(key=lambda i: i.get("concluded_at") or "", reverse=True)

    lines: list[str] = []
    if facts:
        lines.append("history facts (most recent first):")
        for f in facts[:max_facts]:
            title = f"[{_scrub_inline(f['title'])}] " if f.get("title") else ""
            doc = " 📄" if f.get("doc") else ""
            desc = _scrub_inline(f.get("description") or "")[:90]
            lines.append(f"  {f['id']}: {title}{desc}{doc}")
    if done:
        if lines:
            lines.append("")
        lines.append("recently-done intents:")
        for i in done[:max_intents]:
            title = f"[{_scrub_inline(i['title'])}] " if i.get("title") else ""
            doc = " 📄" if i.get("doc") else ""
            desc = _scrub_inline(i.get("description") or "")[:90]
            lines.append(f"  {i['id']}: {title}{desc}{doc}")
    if not lines:
        return ""
    return "\n".join(lines)


def render_graph_text(store: Store) -> str:
    g = build_graph_view(store)
    lines = [f"# graph  ({_scrub_inline(g['project']['status'])})"]
    lines.append("")
    lines.append("## facts")
    for f in g["facts"]:
        src = (
            f"  <- {_scrub_inline(f['source_intent'])}"
            if f.get("source_intent")
            else ""
        )
        t = f"[{_scrub_inline(f['title'])}]  " if f.get("title") else ""
        k = f"<{f['kind']}>" if f.get("kind") else ""
        data = ""
        if f.get("data"):
            pairs = " ".join(
                f"{key}={_scrub_inline(str(val))}" for key, val in f["data"].items()
            )
            data = f"  ({pairs})"
        doc = "  📄" if f.get("doc") else ""
        lines.append(
            f"  {f['id']}: {k}{t}{_scrub_inline(f['description'])}{data}{src}{doc}"
        )
    lines.append("")
    lines.append("## intents")
    for i in g["intents"]:
        to = i["to"] or "·"
        flag = ""
        if i["requires_confirmation"] and not i["confirmed_at"]:
            flag = "  !confirm"
        elif i["status"] == "claimed":
            flag = f"  ~{i['worker']}"
        t = f"[{_scrub_inline(i['title'])}]  " if i.get("title") else ""
        doc = "  📄" if i.get("doc") else ""
        lines.append(
            f"  {i['id']}: {i['from']} -> {to}  {t}{_scrub_inline(i['description'])[:70]}{flag}{doc}"
        )
    if g["hints"]:
        lines.append("")
        lines.append("## hints")
        for h in g["hints"]:
            lines.append(
                f"  {h['id']} ({_scrub_inline(h['creator'])}): {_scrub_inline(h['content'])}"
            )
    return "\n".join(lines)


def render_prompt(store: Store, template_text: str, time_budget: str = "") -> str:
    """Render a prompt template with the current graph. Simple {key} substitution."""
    g = build_graph_view(store)
    p = g["project"]
    replacements = {
        "origin": _prompt_inline(p["origin"]),
        "goal": _prompt_inline(p["goal"]),
        "graph_yaml": _to_yaml_like(g),
        "fact_ids": json.dumps(g["fact_ids"], ensure_ascii=False, indent=2),
        "open_intents": json.dumps(
            [_intent_brief(i) for i in g["open_intents"]],
            ensure_ascii=False,
            indent=2,
        ),
        "hints": json.dumps(
            [_hint_brief(h) for h in g["hints"]], ensure_ascii=False, indent=2
        ),
        "max_intents": "3",
        "time_budget": _prompt_inline(time_budget),
    }
    return _replace_placeholders(template_text, replacements)


def render_intent_prompt(store: Store, template_text: str, intent: dict) -> str:
    g = build_graph_view(store)
    # Recall: surface prior facts/intents whose title/description look similar
    # to THIS intent. Excludes the intent itself (it would otherwise score 1.0
    # against its own description). Keep small (5) - the model just needs a
    # short list of ids it can `sapper doc` into.
    matches = [
        m
        for m in recall(store, intent.get("description", ""), limit=6)
        if m["id"] != intent["id"]
    ][:5]
    recall_block = (
        render_recall(matches, intent.get("description", ""))
        if matches
        else "(no prior nodes look similar to this intent)"
    )
    replacements = {
        "graph_yaml": _to_yaml_like(g),
        "intent_id": intent["id"],
        "intent_description": _prompt_inline(intent["description"]),
        "recall_block": recall_block,
    }
    return _replace_placeholders(template_text, replacements)


def _replace_placeholders(template_text: str, replacements: dict[str, str]) -> str:
    """Substitute template tokens in one pass.

    Repeated ``str.replace`` calls re-scan inserted graph content, so a fact
    containing text such as ``{goal}`` could accidentally rewrite a later
    placeholder.  A regex pass only touches tokens present in the template.
    """
    return re.sub(
        r"\{([A-Za-z_][A-Za-z0-9_]*)\}",
        lambda match: replacements.get(match.group(1), match.group(0)),
        template_text,
    )


def _intent_brief(i: dict) -> dict:
    return {
        "id": i["id"],
        "from": i["from"],
        "description": i["description"],
        "requires_confirmation": i["requires_confirmation"],
        "confirmed": bool(i["confirmed_at"]),
    }


def _hint_brief(h: dict) -> dict:
    return {"id": h["id"], "creator": h["creator"], "content": h["content"]}


def _to_yaml_like(g: dict) -> str:
    """Compact human-readable dump of the graph for prompts.
    Shows title + description (the two summary layers) but NOT `doc` - the
    detailed layer is opt-in, read via `sapper doc <id>`, so prompts stay small."""
    lines = []
    lines.append(f"project: status={_prompt_inline(g['project']['status'])}")
    lines.append(f"goal: {_prompt_inline(g['project']['goal'])}")
    lines.append("facts:")
    for f in g["facts"]:
        src = (
            f" (from {_prompt_inline(f['source_intent'])})"
            if f.get("source_intent")
            else ""
        )
        title = f"[{_prompt_inline(f['title'])}] " if f.get("title") else ""
        kind = f"<{f['kind']}> " if f.get("kind") else ""
        data = ""
        if f.get("data"):
            pairs = " ".join(
                f"{k}={_prompt_inline(str(v))}" for k, v in f["data"].items()
            )
            data = f" {{{pairs}}}"
        has_doc = " (has doc)" if f.get("doc") else ""
        lines.append(
            f"  - {f['id']}: {kind}{title}{_prompt_inline(f['description'])}{data}{src}{has_doc}"
        )
    lines.append("intents:")
    for i in g["intents"]:
        to = i["to"] or "open"
        flag = ""
        if i["requires_confirmation"] and not i["confirmed_at"]:
            flag = " [needs_confirmation]"
        title = f"[{_prompt_inline(i['title'])}] " if i.get("title") else ""
        has_doc = " (has doc)" if i.get("doc") else ""
        lines.append(
            f"  - {i['id']}: {i['from']} -> {to}{flag} | {title}"
            f"{_prompt_inline(i['description'])}{has_doc}"
        )
    if g["hints"]:
        lines.append("hints:")
        for h in g["hints"]:
            lines.append(
                f"  - {h['id']} ({_prompt_inline(h['creator'])}): "
                f"{_prompt_inline(h['content'])}"
            )
    lines.append(
        "note: nodes marked (has doc) carry a detailed layer - read it with "
        "`sapper doc <id>` when you need the full context."
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# JSON extraction (LLM output may have prose around the JSON object)
# --------------------------------------------------------------------------- #
