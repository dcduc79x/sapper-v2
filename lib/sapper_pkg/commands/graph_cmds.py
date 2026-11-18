"""graph cmds - handlers extracted from the facade."""

from ._shared import *


def cmd_init(args) -> int:
    store = Store(Path(args.store))
    if store.exists() and not args.force:
        raise FGError(f"graph exists at {store.root}; use --force to overwrite")
    if args.force and store.root.exists():
        import shutil

        _assert_safe_store_root(store.root, "overwrite")
        # `--force` is an explicit destructive operation.  Do not hold the
        # graph's lock while deleting that lock file itself; another process
        # waiting on the old inode would otherwise be detached from the new
        # graph.  Normal (non-force) initialization is serialized in Store.init.
        shutil.rmtree(store.root)
    origin = args.origin or ""
    goal = args.goal
    if not goal:
        raise FGError("--goal is required")
    store.init(origin=origin, goal=goal)
    print(f"initialized fact graph at {store.root}")
    print(f"goal: {goal}")
    return 0

def cmd_status(args) -> int:
    store = Store(Path(args.store))
    store.require()
    print(render_status(store))
    return 0

def cmd_graph(args) -> int:
    store = Store(Path(args.store))
    store.require()
    if args.format == "json":
        print(json.dumps(build_graph_view(store), ensure_ascii=False, indent=2))
    else:
        print(render_graph_text(store))
    return 0

def cmd_validate(args) -> int:
    store = Store(Path(args.store))
    store.require()
    errors = validate_graph(store)
    if errors:
        print(f"graph invalid: {len(errors)} error(s)")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(f"graph valid: {store.root}")
    return 0

def cmd_frontier(args) -> int:
    store = Store(Path(args.store))
    store.require()
    fr = frontier(store)
    if not fr:
        print("(no ready intents)")
        return 0
    for i in fr:
        mark = ""
        if i["status"] == "claimed":
            mark = f"  [claimed: {i['worker']}]"
        elif i["requires_confirmation"] and not i["confirmed_at"]:
            mark = "  [needs confirmation]"
        print(f"{i['id']}\tfrom={','.join(i['from'])}\t{i['description']}{mark}")
    return 0

def cmd_pick(args) -> int:
    store = Store(Path(args.store))
    store.require()
    fr = frontier(store)
    worker = args.worker or _worker_id("agent") if args.claim else None
    pool = []
    if fr:
        # `frontier` shows claimed work first for observability, but a worker
        # asking to claim should skip another worker's lease and choose the
        # next unclaimed item instead of failing on an unrelated lease.
        pool = [
            i
            for i in fr
            if not args.claim or not i.get("worker") or i.get("worker") == worker
        ]
    if not pool and args.any:
        # escape hatch: include intents not yet ready (e.g. awaiting confirmation)
        pool = [i for i in store.list_intents() if is_open(i)]
    if not pool:
        print("(nothing ready; pass --any to include unconfirmed/blocked open intents)")
        return 0
    chosen = pool[0]
    if args.claim:
        assert worker is not None
        chosen = store.claim_intent(chosen["id"], worker)
    print(chosen["id"])
    return 0

def cmd_fact(args) -> int:
    store = Store(Path(args.store))
    store.require()
    fact = store.add_fact(
        description=args.description,
        creator=args.creator,
        source_intent=args.from_intent,
        title=args.title,
        doc=_read_doc_arg(args),
        kind=args.kind,
        data=parse_kv_pairs(args.data),
    )
    print(fact["id"])
    if args.json:
        print(json.dumps(fact, ensure_ascii=False))
    return 0

def cmd_intent(args) -> int:
    store = Store(Path(args.store))
    store.require()
    from_ids = parse_from(args.from_)
    intent = store.add_intent(
        from_ids=from_ids,
        description=args.description,
        creator=args.creator,
        requires_confirmation=args.confirm,
        title=args.title,
        doc=_read_doc_arg(args),
    )
    print(intent["id"])
    if args.json:
        print(json.dumps(intent, ensure_ascii=False))
    return 0

def cmd_hint(args) -> int:
    store = Store(Path(args.store))
    store.require()
    if args.list:
        for h in store.list_hints():
            print(f"{h['id']}\t({h['creator']})\t{h['content']}")
        return 0
    if not args.content:
        raise FGError("hint content required (or use --list)")
    h = store.add_hint(content=args.content, creator=args.creator)
    print(h["id"])
    return 0

def cmd_show(args) -> int:
    store = Store(Path(args.store))
    store.require()
    if args.entity == "project":
        print(json.dumps(store.load_project(), ensure_ascii=False, indent=2))
        return 0
    if not args.id:
        raise FGError(f"show {args.entity} requires an id")
    if args.entity == "fact":
        print(json.dumps(store.get_fact(args.id), ensure_ascii=False, indent=2))
    elif args.entity == "intent":
        print(json.dumps(store.get_intent(args.id), ensure_ascii=False, indent=2))
    return 0

def cmd_doc(args) -> int:
    """Read or write a node's detailed document (the third doc layer).
    `sapper doc f001` prints the doc; `sapper doc f001 --set '<text>'` /
    `--set-file <path>` writes it. Pass --edit to open $EDITOR."""
    store = Store(Path(args.store))
    store.require()
    _validate_lookup_id(args.id, "node")
    # Resolve the node (fact or intent) without ever accepting a path-like id.
    if args.id == GOAL_ID or (store.facts_dir / f"{args.id}.json").exists():
        node = store.get_fact(args.id)
    elif (store.intents_dir / f"{args.id}.json").exists():
        node = store.get_intent(args.id)
    else:
        raise FGError(f"no fact or intent named {args.id}")

    # WRITE paths
    if args.set is not None or args.set_file or args.edit:
        expected_doc = node.get("doc")
        if args.edit:
            new_doc = _edit_via_editor(node.get("doc") or "", title=f"{args.id} doc")
        elif args.set_file:
            new_doc = Path(args.set_file).read_text(encoding="utf-8")
        else:
            new_doc = args.set
        new_doc = new_doc.strip() if isinstance(new_doc, str) else None
        node = store.update_doc(args.id, new_doc, expected_doc=expected_doc)
        print(
            f"updated doc for {args.id} ({len(node['doc'] or '')} chars)"
            if node["doc"]
            else f"cleared doc for {args.id}"
        )
        return 0

    # READ path: print the doc (or note its absence)
    doc = node.get("doc")
    if doc:
        print(doc)
    else:
        sys.stderr.write(
            f"{args.id} has no doc. add one: sapper doc {args.id} --set '...' or --edit\n"
        )
        return 1
    return 0

def cmd_recall(args) -> int:
    """Find prior facts/intents whose title+description look similar to the
    given query. Use this BEFORE starting a new task: if a strong match
    exists, read its doc with `sapper doc <id>` and reuse what was learned."""
    store = Store(Path(args.store))
    store.require()
    query = (args.query or "").strip()
    if not query:
        raise FGError("recall: query is required")
    if args.limit < 1:
        raise FGError("recall: --limit must be >= 1")
    kinds: tuple[str, ...]
    if args.kind == "fact":
        kinds = ("fact",)
    elif args.kind == "intent":
        kinds = ("intent",)
    else:
        kinds = ("fact", "intent")
    matches = recall(store, query, limit=args.limit, kinds=kinds)
    if args.json:
        print(json.dumps(matches, ensure_ascii=False, indent=2))
    else:
        print(render_recall(matches, query))
    return 0 if matches else 1

def cmd_claim(args) -> int:
    store = Store(Path(args.store))
    store.require()
    intent = store.claim_intent(args.id, args.worker)
    print(f"claimed {intent['id']} -> {args.worker}")
    return 0

def cmd_release(args) -> int:
    store = Store(Path(args.store))
    store.require()
    worker = args.worker or f"agent-{os.getpid()}"
    intent = store.release_intent(args.id, worker)
    print(f"released {intent['id']}")
    return 0

def cmd_done(args) -> int:
    store = Store(Path(args.store))
    store.require()
    # atomic fact-create + conclude (no orphan if intent already done - review H1)
    fact, intent = store.conclude_with_new_fact(
        args.id,
        args.fact,
        args.worker,
        title=args.title,
        doc=_read_doc_arg(args),
        kind=args.kind,
        data=parse_kv_pairs(args.data),
    )
    print(f"{args.id} -> {fact['id']}  ({intent['status']})")
    return 0

def cmd_complete(args) -> int:
    store = Store(Path(args.store))
    store.require()
    from_ids = parse_from(args.from_)
    note = args.note or "goal satisfied"
    intent = store.complete_project(from_ids, note, args.worker)
    print(f"project complete via {intent['id']} (goal)")
    return 0

def cmd_confirm(args) -> int:
    store = Store(Path(args.store))
    store.require()
    intent = store.confirm_intent(args.id)
    print(f"confirmed {intent['id']}")
    return 0

def cmd_brainstorm_pick(args) -> int:
    """Pick one brainstorm option: confirm it, abandon siblings from same batch."""
    store = Store(Path(args.store))
    store.require()
    picked_id = args.id
    picked = store.get_intent(picked_id)
    if picked["to"] is not None:
        raise FGError(f"{picked_id} already concluded")
    if not picked["requires_confirmation"]:
        raise FGError(
            f"{picked_id} is not a brainstorm option (no confirmation required)"
        )
    doc = picked.get("doc") or ""
    batch_tag = None
    for line in doc.split("\n"):
        if line.startswith("[brainstorm batch: "):
            batch_tag = line.split(": ", 1)[1].rstrip("]")
            break
    if not batch_tag:
        raise FGError(f"{picked_id} has no brainstorm batch tag in its doc")
    if not picked["confirmed_at"]:
        store.confirm_intent(picked_id)
    all_intents = store.list_intents()
    abandoned = []
    for i in all_intents:
        if i["id"] == picked_id:
            continue
        if i["to"] is not None:
            continue
        i_doc = i.get("doc") or ""
        if f"[brainstorm batch: {batch_tag}]" in i_doc:
            fact, _ = store.conclude_with_new_fact(
                i["id"],
                f"option abandoned in favor of {picked_id}",
                "brainstorm-pick",
                title="已弃用",
                allow_unconfirmed=True,
            )
            abandoned.append(i["id"])
    print(
        f"confirmed {picked_id}, abandoned {len(abandoned)} sibling(s): {', '.join(abandoned) or '(none)'}"
    )
    return 0
