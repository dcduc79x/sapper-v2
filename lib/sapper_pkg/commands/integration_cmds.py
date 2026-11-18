"""integration cmds - handlers extracted from the facade."""

from ._shared import *


def cmd_flag(args) -> int:
    store = Store(Path(args.store))
    store.require()
    value = args.value.strip()
    if not value:
        raise FGError("flag value cannot be empty")
    existing = _find_flag_fact(store, value)
    if existing is not None:
        d = existing.get("data") or {}
        raise FGError(
            f"flag already recorded as {existing['id']} "
            f"(status={d.get('status', 'recorded')}, challenge={d.get('challenge', '?')})"
        )
    challenge = (args.challenge or "").strip()
    description = f"flag captured: {value}" + (
        f" (challenge {challenge})" if challenge else ""
    )
    fact = store.add_fact(
        description=description,
        creator=args.creator,
        source_intent=None,
        title=args.title or "flag",
        kind="flag",
        data={"value": value, "challenge": challenge, "status": "recorded"},
    )
    print(f"{fact['id']} recorded")
    if not args.submit:
        print("(not submitted; pass --submit to send it via the arena API)")
        return 0
    client = _arena_client(store)
    if not challenge:
        print(
            "sapper: warning: no --challenge given; submit endpoint may need it",
            file=sys.stderr,
        )
    try:
        reply = client.submit_flag(challenge or "-", value)
    except FGError:
        raise
    except Exception as exc:  # ArenaError subclasses Exception
        store.update_flag_fact(fact["id"], "rejected", str(exc))
        raise FGError(
            f"submit failed, flag kept as {fact['id']} (recorded): {exc}"
        ) from exc
    raw = json.dumps(reply.get("result", {}), ensure_ascii=False)
    accepted = reply.get("status") == 200
    status = "submitted" if accepted else "rejected"
    store.update_flag_fact(fact["id"], status, raw[:500])
    print(f"{fact['id']} {status}: {raw[:300]}")
    return 0 if accepted else 1

def cmd_arena(args) -> int:
    store = Store(Path(args.store))
    if args.action == "init":
        arena = _load_arena_module()
        path = arena.ArenaClient.config_path(store.root)
        if path.exists():
            raise FGError(f"arena config already exists: {path}")
        written = arena.ArenaClient.write_template(store.root)
        print(
            f"wrote {written} - fill in the official API base_url/endpoints, "
            f"then export SAPPER_ARENA_TOKEN"
        )
        return 0
    store.require()
    client = _arena_client(store)
    action = args.action
    if action == "challenges":
        reply = client.list_challenges()
    elif action == "start":
        reply = client.start_challenge(args.challenge_id)
    elif action == "stop":
        reply = client.close_challenge(args.challenge_id)
    elif action == "submit":
        reply = client.submit_flag(args.challenge_id, args.flag)
    elif action == "hint":
        print(
            "sapper: note - official rules deduct score for hints (-10%); "
            "only ask when genuinely stuck",
            file=sys.stderr,
        )
        reply = client.get_hint(args.challenge_id)
    else:
        raise FGError(f"unknown arena action {action!r}")
    print(json.dumps(reply.get("result", {}), ensure_ascii=False, indent=2))
    return 0

def cmd_kb(args) -> int:
    module, kb = _kb(args)
    handler = _KB_ACTIONS.get(args.action)
    if handler is None:
        raise FGError(f"unknown kb action {args.action!r}")
    try:
        if args.action == "import":
            return handler(args, module, kb)
        if args.action == "root":
            return handler(kb)
        return handler(args, kb)
    except module.KBError as exc:
        raise FGError(str(exc)) from exc

def cmd_peers(args) -> int:
    store = Store(Path(args.store))
    store.require()

    if args.discover:
        rows = _tmux_discover()
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
            return 0
        if not rows:
            print("no tmux panes found")
            return 0
        print("tmux panes:")
        for r in rows:
            marker = "*" if r["active"] else " "
            title = f" title={r['title']!r}" if r["title"] else ""
            print(
                f"  {marker} {r['target']:<14} session={r['session']:<10} "
                f"window={r['window']:<12} cmd={r['command']}{title}"
            )
        print(
            "\nAuthorize explicitly before sending, for example:\n"
            '  sapper peers --add harley --target api-6:0.0 --sender "Codex/api2-4"'
        )
        return 0

    data = _load_peers(store)
    peers = data["peers"]

    if args.add:
        name = args.add.strip()
        if not PEER_NAME_RE.match(name):
            raise FGError(
                "peer name must contain only letters, numbers, '.', '_' or '-'"
            )
        if not args.target:
            raise FGError("--target is required with --add")
        if not _tmux_target_exists(args.target):
            raise FGError(f"tmux target not found: {args.target}")
        if args.channel:
            data["channel"] = str(Path(args.channel).expanduser())
        peers[name] = {
            "target": args.target,
            "sender": args.sender or os.environ.get("FGC_SENDER") or "sapper-agent",
            "created_at": now_iso(),
            "note": args.note or None,
        }
        _save_peers(store, data)
        print(
            f"authorized peer {name}: target={args.target} sender={peers[name]['sender']}"
        )
        return 0

    if args.remove:
        name = args.remove.strip()
        if name not in peers:
            raise FGError(f"peer not configured: {name}")
        del peers[name]
        _save_peers(store, data)
        print(f"removed peer {name}")
        return 0

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    print(f"peer config: {_peers_path(store)}")
    print(f"shared log:  {data.get('channel')}")
    if not peers:
        print("(no authorized peers)")
        print(
            "run `sapper peers --discover` to list tmux panes, then authorize one with --add"
        )
        return 0
    print("authorized peers:")
    for name, peer in sorted(peers.items()):
        note = f" note={peer.get('note')!r}" if peer.get("note") else ""
        print(
            f"  {name:<16} target={peer.get('target')} sender={peer.get('sender')}{note}"
        )
    return 0

def cmd_send(args) -> int:
    store = Store(Path(args.store))
    store.require()
    data = _load_peers(store)
    peers = data["peers"]
    peer = peers.get(args.peer)
    if peer is None:
        raise FGError(
            f"peer not configured: {args.peer}. Run `sapper peers` to see authorized peers."
        )
    target = peer.get("target")
    if not target:
        raise FGError(f"peer {args.peer} has no tmux target")
    if not _tmux_target_exists(target):
        raise FGError(f"tmux target not found: {target}")

    if args.message:
        body = " ".join(args.message).strip()
    else:
        body = sys.stdin.read().strip()
    if not body:
        raise FGError("empty message")

    if args.raw:
        message = body
    else:
        stamp = time.strftime("%Y-%m-%d %H:%M")
        sender = (
            args.sender
            or peer.get("sender")
            or os.environ.get("FGC_SENDER")
            or "sapper-agent"
        )
        message = f"[{stamp}][{sender}] {body}"

    if not args.no_log:
        channel = Path(
            args.channel or data.get("channel") or (store.root / AI_CHANNEL_NAME)
        )
        channel.parent.mkdir(parents=True, exist_ok=True)
        with open(channel, "a", encoding="utf-8") as fh:
            fh.write(message + "\n")

    # Claude Code TUI often needs pasted LF, not tmux send-keys Enter. A second
    # LF makes submission reliable after idle / goal-complete states.
    _paste_tmux(target, message)
    time.sleep(float(os.environ.get("AI_SEND_INITIAL_DELAY", "0.1")))
    _paste_tmux(target, "\n")
    time.sleep(float(os.environ.get("AI_SEND_SUBMIT_DELAY", "0.5")))
    _paste_tmux(target, "\n")
    print(f"sent to {args.peer} ({target}): {message}")
    return 0

def cmd_capture(args) -> int:
    store = Store(Path(args.store))
    store.require()
    data = _load_peers(store)
    peer = data["peers"].get(args.peer)
    if peer is None:
        raise FGError(
            f"peer not configured: {args.peer}. Run `sapper peers` to see authorized peers."
        )
    target = peer.get("target")
    if not target:
        raise FGError(f"peer {args.peer} has no tmux target")
    if not _tmux_target_exists(target):
        raise FGError(f"tmux target not found: {target}")
    if args.lines < 1 or args.lines > 2000:
        raise FGError("--lines must be between 1 and 2000")

    text = _capture_tmux(target, args.lines)
    print(text)

    if args.log:
        channel = Path(
            args.channel or data.get("channel") or (store.root / AI_CHANNEL_NAME)
        )
        channel.parent.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M")
        with open(channel, "a", encoding="utf-8") as fh:
            fh.write(
                f"[{stamp}][sapper-capture] peer={args.peer} "
                f"target={target} lines={args.lines}\n"
            )
            fh.write(text + "\n")
    return 0

def cmd_setup(args) -> int:
    """`sapper setup` - turn THIS project into a fact-graph project.

    Does three things, idempotently, scoped to the current project only:
      1. creates the local graph (.sapper/) if --goal is given (else requires one
         to already exist, or asks you to pass --goal)
      2. registers the two hooks in <project>/.claude/settings.json so this
         project auto-injects the graph into every turn
      3. writes AGENTS.md so dispatched sub-agents know the protocol

    Nothing is written under ~. Other projects are untouched.
    """
    project_dir = Path.cwd()
    store = Store(Path(args.store))
    # Validate an existing settings file before creating a new graph.  A
    # malformed Claude config should not leave setup half-completed with a
    # graph that has no working hooks.
    patch = _load_settings_patch()
    target = patch.project_settings_path(project_dir)
    if target.exists():
        try:
            patch.validate(target)
        except (OSError, ValueError) as exc:
            raise FGError(f"could not read {target}: {exc}") from exc
    made_graph = False
    if not store.exists():
        if not args.goal:
            raise FGError(
                'no .sapper/ here. Pass --goal "<one-line goal>" to create one, '
                "or run from a directory that already has a graph."
            )
        store.init(origin=args.origin or "", goal=args.goal)
        made_graph = True
        print(f"created graph at {store.root} (goal: {args.goal})")
    else:
        print(f"graph already present at {store.root}")

    # register hooks into project-level settings
    try:
        changed, msgs = patch.add(target, str(_hook_script_path()))
    except (OSError, ValueError) as exc:
        raise FGError(f"could not update {target}: {exc}") from exc
    for m in msgs:
        print(m)

    # AGENTS.md
    if args.agents:
        ap = project_dir / "AGENTS.md"
        if ap.exists() and not args.force:
            print(f"AGENTS.md exists - leaving it (--force to overwrite)")
        else:
            spec_txt = _AGENTS_SPEC.format(
                store=str(store.root.resolve()), marker=_AGENTS_MARKER
            )
            ap.write_text(spec_txt, encoding="utf-8")
            print(f"wrote {ap}")

    print(
        "\nsetup done. This project now auto-reads/maintains its fact graph.\n"
        "  sapper status          # see current state\n"
        "  sapper dispatch reason # propose next work\n"
        "  sapper peers --discover # optional: list tmux panes, then ask user who to authorize\n"
        "  sapper auto --skip-permissions  # drive to completion\n"
        "To undo:  sapper teardown   (removes sapper hooks + its AGENTS.md; keeps .sapper/)"
    )
    return 0

def cmd_teardown(args) -> int:
    """`sapper teardown` - remove the hooks and sapper-owned AGENTS.md from THIS project.

    Never touches the graph data (.sapper/) unless --purge is given.
    """
    project_dir = Path.cwd()
    patch = _load_settings_patch()
    target = patch.project_settings_path(project_dir)
    removed_any = False
    if target.exists():
        try:
            _, msgs = patch.remove(target, str(_hook_script_path()))
        except (OSError, ValueError) as exc:
            raise FGError(f"could not update {target}: {exc}") from exc
        for m in msgs:
            print(m)
            if m.startswith("  -"):
                removed_any = True
        # if the project settings.json is now empty, drop it (and its dir)
        try:
            leftover = json.loads(target.read_text(encoding="utf-8") or "{}")
        except Exception:
            leftover = {}
        if not leftover:
            target.unlink(missing_ok=True)
            print(f"removed empty {target}")
            if target.parent.exists() and not any(target.parent.iterdir()):
                target.parent.rmdir()
                print(f"removed empty {target.parent}")

    ap = project_dir / "AGENTS.md"
    if ap.exists():
        try:
            agents_text = ap.read_text(encoding="utf-8")
        except OSError:
            agents_text = ""
        generated = agents_text.startswith(
            _AGENTS_MARKER
        ) or agents_text == _AGENTS_SPEC.format(
            store=str(Path(args.store).resolve()), marker=_AGENTS_MARKER
        )
        if generated:
            ap.unlink()
            print(f"removed {ap}")
        else:
            print(f"kept existing {ap} (not generated by sapper)")

    if args.purge:
        import shutil

        store_root = Path(args.store)
        if store_root.exists():
            _assert_safe_store_root(store_root, "purge")
            shutil.rmtree(store_root)
            print(f"purged graph data {store_root}")
    else:
        sr = Path(args.store)
        if sr.exists():
            print(f"(kept graph data at {sr}; pass --purge to delete it too)")
    return 0

def cmd_view(args) -> int:
    store = Store(Path(args.store))
    store.require()

    if args.serve:
        return _view_serve(store, args)
    if args.print:
        sys.stdout.write(_render_static_html(store))
        return 0
    # default: write a snapshot file next to .sapper and tell the user where it is
    out = Path(args.out) if args.out else (store.root.parent / "sapper-graph.html")
    out.write_text(_render_static_html(store), encoding="utf-8")
    # try to open it in the default browser (best-effort, non-fatal)
    opened = _try_open(out)
    print(f"wrote {out}")
    if opened:
        print(f"(opened in browser)")
    else:
        print(f"open it: file://{out.resolve()}")
    return 0
