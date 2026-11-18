"""autonomy cmds - handlers extracted from the facade."""

from ._shared import *


def cmd_auto(args) -> int:
    """Drive the whole graph forward until goal reached or budget exhausted."""
    ctx = _auto_setup(args)
    for step in range(1, ctx.max_steps + 1):
        if ctx.deadline is not None and time.time() >= ctx.deadline:
            print("[auto] deadline reached - stopping (record what you have)")
            break
        args.time_budget = format_time_budget(ctx.deadline)
        if ctx.store.load_project()["status"] == "completed":
            print(f"[auto] step {step}: project already completed")
            break
        before = _graph_signature(ctx.store)

        status, kind = _auto_reason(ctx, step)
        if status == "break":
            break
        if status == "continue":
            continue
        status = _auto_after_reason(ctx, step, kind)
        if status == "break":
            break
        if status == "continue":
            continue

        if _auto_explore_phase(ctx, step, before) == "break":
            break

    project = ctx.store.load_project()
    print(f"[auto] done  status={project['status']}  explored={ctx.explore_count}")
    return 0


def cmd_dispatch(args) -> int:
    store = Store(Path(args.store))
    store.require()
    model = args.model
    timeout = args.timeout
    if not isinstance(model, str) or not model.strip():
        raise FGError("--model cannot be empty")
    if timeout < 1:
        raise FGError("--timeout must be at least 1 second")
    skip = args.skip_permissions
    templates_dir = Path(args.templates)

    cwd = Path.cwd()

    if args.target == "reason":
        return _dispatch_reason(store, templates_dir, model, timeout, skip, cwd, args)
    elif args.target == "verify":
        return _dispatch_verify(store, templates_dir, model, timeout, skip, cwd, args)
    elif args.target == "blindspot":
        return _dispatch_blindspot(
            store, templates_dir, model, timeout, skip, cwd, args
        )
    elif args.target == "brainstorm":
        return _dispatch_brainstorm(
            store, templates_dir, model, timeout, skip, cwd, args
        )
    else:
        # treat as intent id -> explore
        intent = store.get_intent(args.target)
        return _dispatch_explore(
            store, templates_dir, intent, model, timeout, skip, cwd, args
        )


def cmd_sweep(args) -> int:
    """Drive multiple per-challenge graphs in rotation until deadline.

    Layout: one directory per challenge, each with its own .sapper/ graph:

        runs/
        ├── c01/.sapper/project.json   goal: capture flag for challenge c01
        ├── c02/.sapper/project.json
        └── ...

    Each round gives every unfinished challenge `--minutes-per` of autonomous
    work (reason -> explore -> ... via `sapper auto`). Stops when the global
    deadline passes or every graph reaches its goal.
    """
    root = Path(args.root)
    if not root.is_dir():
        raise FGError(f"sweep root {root} is not a directory")
    graph_dirs = sorted(p.parent for p in root.glob("*/.sapper/project.json"))
    if not graph_dirs:
        raise FGError(f"no */.sapper/project.json found under {root}")
    deadline = deadline_from_args(args)
    if deadline is None:
        raise FGError("sweep needs --minutes or --deadline (the match slot clock)")
    if args.minutes_per < 1:
        raise FGError("--minutes-per must be at least 1")

    cli = [sys.executable, str(Path(__file__).resolve()), "auto"]
    passthrough = ["--model", args.model]
    if args.skip_permissions:
        passthrough.append("--skip-permissions")
    if args.verify:
        passthrough.append("--verify")
    if args.templates:
        passthrough.extend(["--templates", args.templates])

    print(f"[sweep] {len(graph_dirs)} challenge graph(s) under {root}")
    print(
        f"[sweep] deadline {datetime.fromtimestamp(deadline).strftime('%H:%M')} "
        f"({format_time_budget(deadline)})"
    )
    while time.time() < deadline:
        pending = []
        for d in graph_dirs:
            try:
                status = (read_json(d / ".sapper" / "project.json")).get("status")
            except (json.JSONDecodeError, OSError):
                status = "active"  # unreadable -> still try to work it
            if status != "completed":
                pending.append(d)
        if not pending:
            print("[sweep] all challenge graphs completed")
            break
        for d in pending:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            budget = min(args.minutes_per, max(1, int(remaining // 60)))
            print(
                f"\n[sweep] {d.name}: auto for {budget}m "
                f"({len(pending)} pending, {format_time_budget(deadline)})"
            )
            cmd = (
                cli
                + [
                    "--store",
                    str(d / ".sapper"),
                    "auto",
                    "--minutes",
                    str(budget),
                    "--max-steps",
                    str(args.max_steps),
                ]
                + passthrough
            )
            subprocess.run(cmd, cwd=str(d), check=False)

    done = 0
    for d in graph_dirs:
        try:
            status = (read_json(d / ".sapper" / "project.json")).get("status")
        except (json.JSONDecodeError, OSError):
            status = "?"
        mark = "DONE" if status == "completed" else status
        print(f"[sweep] {d.name}: {mark}")
        done += 1 if status == "completed" else 0
    print(f"[sweep] finished: {done}/{len(graph_dirs)} goals reached")
    return 0


def cmd_bench(args) -> int:
    """Drive the TSecBench Challenges API end-to-end with sapper per challenge.

    Loop: list challenges -> for each unfinished (respecting the active
    container cap) start -> seed a graph -> run `sapper auto` -> submit the
    flags it captured -> close the container. Repeat until all are complete,
    the deadline passes, or a stop condition (task ended) is hit.
    """
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    deadline = deadline_from_args(args)
    templates_dir = _resolve_templates_dir(args)
    cli = [sys.executable, str(Path(__file__).resolve())]
    engine_args = ["--model", args.model]
    if args.skip_permissions:
        engine_args.append("--skip-permissions")
    if args.verify:
        engine_args.append("--verify")

    client = _arena_client_for_root(root)
    print(f"[bench] arena {client.base_url}")
    try:
        challenges = client.list_challenges()
    except FGError as exc:
        print(f"[bench] cannot list challenges: {exc}")
        return 1
    todo = [c for c in challenges if not c.get("is_completed")]
    print(f"[bench] {len(challenges)} challenges, {len(todo)} unfinished")

    if args.parallel > 1:
        return _bench_parallel(args, client, root, cli, engine_args, todo, deadline)

    rounds = 0
    while todo and (deadline is None or time.time() < deadline):
        rounds += 1
        progressed = False
        for ch in list(todo):
            if deadline is not None and time.time() >= deadline:
                print("[bench] deadline reached")
                break
            code = str(ch.get("unique_code"))
            store = _bench_store(root, code)
            try:
                started = client.start_challenge(code)
            except FGError as exc:
                if "max active" in str(exc).lower():
                    print(
                        "[bench] active-container cap reached; closing the oldest and retrying next round"
                    )
                    break
                print(f"[bench] {code}: cannot start ({exc})")
                continue
            addrs = list(started.get("container_addr") or [])
            print(f"\n[bench] {code} started -> {addrs or '(no addr)'}")
            try:
                _bench_prepare_graph(store, ch, started, addrs)
            except Exception as exc:  # graph problems must not kill the run
                print(f"[bench] {code}: graph prep failed ({exc}); skipping this round")
                try:
                    client.close_challenge(code)
                except FGError:
                    pass
                continue
            try:
                cmd = (
                    cli
                    + [
                        "--store",
                        str(store.root),
                        "auto",
                        "--minutes",
                        str(args.minutes_per_challenge),
                        "--max-steps",
                        str(args.max_steps),
                    ]
                    + engine_args
                )
                subprocess.run(cmd, cwd=str(root / code), check=False)
                try:
                    _bench_submit_recorded(store, code)
                except Exception as exc:
                    print(
                        f"[bench] {code}: flag submission failed ({exc}); "
                        f"flags stay recorded and will retry next round"
                    )
                # did we finish it?
                try:
                    fresh = {
                        str(c.get("unique_code")): c for c in client.list_challenges()
                    }
                    if fresh.get(code, {}).get("is_completed"):
                        print(f"[bench] {code} COMPLETE")
                except FGError:
                    pass
            finally:
                try:
                    client.close_challenge(code)
                    print(f"[bench] {code} closed")
                except FGError as exc:
                    print(f"[bench] {code} close failed (non-fatal): {exc}")
            progressed = True
        try:
            challenges = client.list_challenges()
        except FGError as exc:
            print(f"[bench] cannot refresh challenges: {exc}")
            break
        todo = [c for c in challenges if not c.get("is_completed")]
        if not progressed:
            print("[bench] no progress this round - stopping")
            break
        print(f"[bench] round {rounds}: {len(todo)} still unfinished")
        time.sleep(args.round_gap_seconds)

    done = len([c for c in challenges if c.get("is_completed")])
    print(
        f"[bench] finished: {done}/{len(challenges)} complete after {rounds} round(s)"
    )
    return 0


def cmd_command(args) -> int:
    """Field-commander loop: read the board, ask the model where the next
    time slice goes, run `sapper auto` there, repeat until deadline/stop."""
    ctx = _command_setup(args)
    while time.time() < ctx.deadline:
        workers = _active_workers(ctx.session)
        rows = [r for r in _board_summary(ctx.root) if r["status"] != "completed"]
        for r in rows:
            r["worker"] = "yes" if r["name"] in workers else "-"
        if not rows and not workers:
            print("[command] all challenge graphs completed")
            break
        if not rows:
            print(f"[command] waiting on hired workers: {sorted(workers)}")
            time.sleep(ctx.args.poll_seconds)
            continue
        action, target, minutes, note, _remaining = _command_decide(ctx, rows, workers)
        names = {r["name"] for r in rows}
        status = _command_apply(ctx, names, action, target, minutes, note)
        if status == "break":
            break
        if status == "continue":
            continue
    _command_final_board(ctx.root)
    return 0


def cmd_fleet(args) -> int:
    path = Path(args.file)
    if args.action == "init":
        if path.exists():
            raise FGError(f"fleet config already exists: {path}")
        path.write_text(
            json.dumps(FLEET_TEMPLATE, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {path} - edit deadline/panes, then `sapper fleet up`")
        return 0
    if args.action == "up":
        cfg = _fleet_load(path)
        session = cfg["session"]
        if _tmux("has-session", "-t", session, check=False).returncode == 0:
            raise FGError(
                f"tmux session {session!r} already exists; `sapper fleet down` first"
            )
        _tmux("new-session", "-d", "-s", session)
        first = True
        for pane in cfg["panes"]:
            name = str(pane.get("name") or pane.get("type"))
            command = _fleet_pane_command(cfg, pane, base_dir=path.resolve().parent)
            if first:
                _tmux("rename-window", "-t", f"{session}:0", name)
                first = False
            else:
                _tmux("new-window", "-d", "-t", session, "-n", name)
            # literal paste via buffer: avoids send-keys eating special chars
            buf = tempfile.NamedTemporaryFile("w", delete=False, suffix=".sh")
            buf.write(command + "\n")
            buf.close()
            _tmux("load-buffer", buf.name)
            _tmux("paste-buffer", "-t", f"{session}:{name}")
            _tmux("send-keys", "-t", f"{session}:{name}", "Enter")
            os.unlink(buf.name)
            print(f"[fleet] {name}: {command}")
        print(
            f"[fleet] session {session!r} up - detach with Ctrl-b d; "
            f"loops run unattended. `sapper fleet down` to stop."
        )
        return 0
    if args.action == "down":
        cfg = _fleet_load(path)
        session = cfg["session"]
        if _tmux("has-session", "-t", session, check=False).returncode != 0:
            print(f"[fleet] session {session!r} not running")
            return 0
        _tmux("kill-session", "-t", session)
        print(f"[fleet] session {session!r} killed")
        return 0
    if args.action == "hire":
        cfg = (
            _fleet_load(path)
            if path.exists()
            else {"session": "sapper-fleet", "root": "runs"}
        )
        cfg["_base_dir"] = str(Path.cwd())
        if not args.target:
            raise FGError("fleet hire needs --target <challenge>")
        minutes = args.minutes or 30
        spawned = _hire_worker(
            cfg,
            args.target,
            minutes,
            max_steps=args.max_steps or 8,
            extra=args.extra_args or "",
        )
        print(
            f"[fleet] hire {args.target}: "
            f"{'worker window w-%s up' % args.target if spawned else 'worker already running'}"
        )
        return 0
    if args.action == "fire":
        cfg = (
            _fleet_load(path)
            if path.exists()
            else {"session": "sapper-fleet", "root": "runs"}
        )
        if not args.target:
            raise FGError("fleet fire needs --target <challenge>")
        killed = _fire_worker(cfg["session"], args.target)
        print(
            f"[fleet] fire {args.target}: {'released' if killed else 'no worker running'}"
        )
        return 0
    if args.action == "workers":
        cfg = (
            _fleet_load(path)
            if path.exists()
            else {"session": "sapper-fleet", "root": "runs"}
        )
        workers = sorted(_active_workers(cfg["session"]))
        print("hired workers: " + (", ".join(workers) if workers else "(none)"))
        return 0
    if args.action == "status":
        cfg = _fleet_load(path)
        session = cfg["session"]
        if _tmux("has-session", "-t", session, check=False).returncode != 0:
            print(f"session {session!r}: not running")
            return 0
        proc = _tmux(
            "list-windows",
            "-t",
            session,
            "-F",
            "#I #W active=#{window_active} #{pane_current_command}",
        )
        print(f"session {session!r}:\n{proc.stdout.strip()}")
        return 0
    raise FGError(f"unknown fleet action {args.action!r}")
