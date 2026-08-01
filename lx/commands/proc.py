"""lx proc — process manager: list, find, show, kill, tree view."""

from __future__ import annotations

import os
import signal
import time as _t
from datetime import datetime

import click
import psutil
from rich.table import Table

from lx.utils.flags import apply_flags, json_option, json_watch_options
from lx.utils.output import center_rule, emit, err, kv_table, ok, warn
from lx.utils.prompt import confirm_destructive


def _human_time(seconds: float) -> str:
    d = int(seconds // 86400)
    h = int((seconds % 86400) // 3600)
    m = int((seconds % 3600) // 60)
    if d:
        return f"{d}d{h}h"
    if h:
        return f"{h}h{m}m"
    return f"{m}m"


def _human_bytes(n: float) -> str:
    for u in ("B", "K", "M", "G"):
        if n < 1024:
            return f"{n:.0f}{u}"
        n /= 1024
    return f"{n:.0f}T"


def _proc_cmdline(info: dict) -> str:
    return " ".join(info.get("cmdline") or []) or info.get("name") or "—"


def _collect_top(count: int, sort_key: str) -> dict:
    procs = []
    for p in psutil.process_iter(
        ["pid", "name", "username", "cpu_percent", "memory_percent", "create_time", "cmdline"]
    ):
        try:
            info = p.info
            info["rss"] = p.memory_info().rss if p.is_running() else 0
            procs.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    key_map = {"cpu": "cpu_percent", "mem": "memory_percent", "rss": "rss", "time": "create_time"}
    key = key_map.get(sort_key, "cpu_percent")
    reverse = key != "create_time"
    if key == "create_time":
        procs.sort(key=lambda x: x.get("create_time") or 0)
    else:
        procs.sort(key=lambda x: x.get(key) or 0, reverse=reverse)

    rows = []
    for p in procs[:count]:
        rows.append(
            {
                "pid": p.get("pid"),
                "cpu_percent": round(p.get("cpu_percent", 0) or 0, 1),
                "memory_percent": round(p.get("memory_percent", 0) or 0, 1),
                "rss": p.get("rss", 0),
                "user": p.get("username"),
                "created": p.get("create_time"),
                "cmd": _proc_cmdline(p),
            }
        )
    return {"sort": sort_key, "count": len(rows), "processes": rows}


def _render_top(console, data: dict) -> None:
    center_rule(console, f"Top {len(data['processes'])} processes (by {data['sort']})")
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("PID", justify="right")
    t.add_column("CPU%", justify="right")
    t.add_column("MEM%", justify="right")
    t.add_column("RSS")
    t.add_column("USER")
    t.add_column("TIME")
    t.add_column("COMMAND")
    now = _t.time()
    for p in data["processes"]:
        cmd = p["cmd"]
        if len(cmd) > 45:
            cmd = cmd[:42] + "…"
        created = p["created"]
        age = _human_time(now - created) if created else "—"
        t.add_row(
            str(p["pid"]),
            f"{p['cpu_percent']:.1f}",
            f"{p['memory_percent']:.1f}",
            _human_bytes(p["rss"]),
            p["user"] or "—",
            age,
            cmd,
        )
    console.print(t)


def _collect_find(name: str) -> dict:
    pattern = name.lower()
    processes = []
    for p in psutil.process_iter(
        ["pid", "username", "cpu_percent", "memory_percent", "cmdline", "name"]
    ):
        try:
            cmd = _proc_cmdline(p.info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if pattern in cmd.lower():
            processes.append(
                {
                    "pid": p.info.get("pid"),
                    "user": p.info.get("username"),
                    "cpu_percent": round(p.info.get("cpu_percent") or 0, 1),
                    "memory_percent": round(p.info.get("memory_percent") or 0, 1),
                    "cmd": cmd,
                }
            )
    return {"pattern": name, "processes": processes}


def _render_find(console, data: dict) -> None:
    center_rule(console, f"Processes matching '{data['pattern']}'")
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("PID", justify="right")
    t.add_column("USER")
    t.add_column("CPU%", justify="right")
    t.add_column("MEM%", justify="right")
    t.add_column("COMMAND")
    for p in data["processes"]:
        disp = p["cmd"] if len(p["cmd"]) <= 70 else p["cmd"][:67] + "…"
        t.add_row(
            str(p["pid"]),
            p["user"] or "—",
            f"{p['cpu_percent']:.1f}",
            f"{p['memory_percent']:.1f}",
            disp,
        )
    if t.row_count:
        console.print(t)
    else:
        console.print(f"[dim]no processes matched '{data['pattern']}'[/dim]")


def _walk_node(proc: psutil.Process, max_depth: int | None = None, depth: int = 0) -> dict | None:
    try:
        info = proc.as_dict(["pid", "name", "cmdline"])
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None
    cmd = " ".join(info.get("cmdline") or []) or info.get("name") or "—"
    node: dict = {"pid": info["pid"], "name": info.get("name", "—"), "cmd": cmd, "depth": depth}
    if max_depth is not None and depth >= max_depth:
        try:
            node["truncated"] = bool(proc.children(recursive=False))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            node["truncated"] = False
        return node
    try:
        children = proc.children(recursive=False)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        children = []
    node["children"] = []
    for child in children:
        child_node = _walk_node(child, max_depth, depth + 1)
        if child_node:
            node["children"].append(child_node)
    return node


def _collect_tree(name: str | None, max_depth: int | None = None) -> dict:
    roots: list[psutil.Process] = []
    if name is not None:
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                cmd = _proc_cmdline(p.info)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if name.lower() in cmd.lower():
                roots.append(p)
    else:
        try:
            roots = [psutil.Process(1)]
        except psutil.NoSuchProcess:
            roots = []
    nodes = []
    for root in roots:
        node = _walk_node(root, max_depth)
        if node:
            nodes.append(node)
    return {"pattern": name, "max_depth": max_depth, "roots": nodes}


def _render_tree(console, data: dict) -> None:
    if data["pattern"] and not data["roots"]:
        console.print(f"[dim]no matches for '{data['pattern']}'[/dim]")
        return
    if not data["roots"]:
        console.print("[dim]no running processes found[/dim]")
        return

    def _print_sub(node: dict, indent: int) -> None:
        prefix = "  " * indent
        cmd = node["cmd"]
        if len(cmd) > 60:
            cmd = cmd[:57] + "…"
        console.print(
            f"{prefix}[bold]{node['pid']}[/bold] [cyan]{node['name']}[/cyan] [dim]{cmd}[/dim]"
        )
        if node.get("truncated"):
            console.print(f"{prefix}[dim]… (children hidden by --depth)[/dim]")
        for child in node["children"]:
            _print_sub(child, indent + 1)

    for node in data["roots"]:
        _print_sub(node, 0)


# ---------------------------------------------------------------- show


def _collect_show(pid: int, want: dict) -> dict:
    try:
        p = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return {"pid": pid, "exists": False}
    now = _t.time()
    try:
        pinfo = p.as_dict(
            attrs=[
                "pid",
                "name",
                "status",
                "username",
                "cmdline",
                "create_time",
                "exe",
                "cpu_percent",
                "memory_percent",
                "uids",
                "gids",
                "terminal",
                "nice",
            ]
        )
    except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
        return {"pid": pid, "exists": True, "error": str(exc)}
    try:
        mem = p.memory_info()
        rss, vms = mem.rss, mem.vms
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        rss = vms = 0
    created = pinfo.get("create_time")
    info = {
        "name": pinfo.get("name"),
        "status": pinfo.get("status"),
        "user": pinfo.get("username"),
        "uid": getattr(pinfo.get("uids"), "real", None) if pinfo.get("uids") else None,
        "gid": getattr(pinfo.get("gids"), "real", None) if pinfo.get("gids") else None,
        "nice": pinfo.get("nice"),
        "terminal": pinfo.get("terminal"),
        "exe": pinfo.get("exe"),
        "cmdline": pinfo.get("cmdline") or [],
        "cmd": _proc_cmdline(pinfo),
        "created": datetime.fromtimestamp(created).isoformat(timespec="seconds")
        if created
        else None,
        "age_seconds": int(now - created) if created else None,
        "cpu_percent": round(pinfo.get("cpu_percent") or 0, 1),
        "memory_percent": round(pinfo.get("memory_percent") or 0, 1),
        "rss": rss,
        "vms": vms,
    }
    data: dict = {"pid": pid, "exists": True, "info": info}
    if want["threads"]:
        try:
            threads = p.threads()
            data["threads"] = {
                "count": len(threads),
                "ids": [tid for tid, _utime, _stime in threads],
            }
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            data["threads"] = {"error": str(exc)}
    if want["env"]:
        try:
            data["env"] = p.environ()
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            data["env"] = {"error": str(exc)}
    if want["fd"]:
        fds: dict = {"count": None, "files": [], "sockets": 0}
        try:
            fds["count"] = p.num_fds()
        except (psutil.NoSuchProcess, psutil.AccessDenied, NotImplementedError):
            fds["count"] = None
        try:
            fds["files"] = [
                {"fd": fd, "path": path, "mode": mode} for path, fd, mode, _pos in p.open_files()
            ]
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            fds["files_error"] = str(exc)
        try:
            fds["sockets"] = len(p.net_connections(kind="inet"))
        except (psutil.NoSuchProcess, psutil.AccessDenied, NotImplementedError):
            fds["sockets"] = 0
        data["fds"] = fds
    if want["cwd"]:
        try:
            data["cwd"] = p.cwd()
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            data["cwd_error"] = str(exc)
    return data


def _render_show(console, data: dict) -> None:
    if not data["exists"]:
        err(console, f"no process with PID {data['pid']}")
        return
    if "error" in data:
        err(console, f"cannot inspect PID {data['pid']}: {data['error']}")
        return
    center_rule(console, f"Process {data['pid']}")
    info = data["info"]
    age = _human_time(info["age_seconds"]) if info["age_seconds"] is not None else "—"
    kv_table(
        console,
        [
            ("name", info["name"]),
            ("status", info["status"]),
            ("user", f"{info['user']} (uid {info['uid']}, gid {info['gid']})"),
            ("nice", info["nice"]),
            ("terminal", info["terminal"] or "—"),
            ("created", f"{info['created']} ({age} ago)"),
            ("cpu", f"{info['cpu_percent']:.1f}%"),
            ("memory", f"{info['memory_percent']:.1f}% (rss {_human_bytes(info['rss'])})"),
            ("exe", info["exe"] or "—"),
            ("cmd", info["cmd"]),
        ],
    )
    if "cwd" in data:
        console.print(f"[dim]cwd:[/dim] {data['cwd']}")
    elif "cwd_error" in data:
        warn(console, f"cwd: {data['cwd_error']}")
    console.print()

    if "threads" in data:
        center_rule(console, "Threads")
        threads = data["threads"]
        if "error" in threads:
            warn(console, threads["error"])
        else:
            console.print(f"[bold]{threads['count']}[/bold] thread(s)")
            if threads["ids"]:
                console.print(
                    f"  [dim]{', '.join(str(t) for t in threads['ids'][:20])}"
                    f"{'…' if len(threads['ids']) > 20 else ''}[/dim]"
                )
        console.print()

    if "env" in data:
        center_rule(console, "Environment")
        env = data["env"]
        if "error" in env:
            warn(console, env["error"])
        else:
            for key in sorted(env)[:40]:
                console.print(f"  [bold]{key}[/bold]={env[key][:80]}")
            if len(env) > 40:
                console.print(f"[dim]…{len(env) - 40} more (see --json for all)[/dim]")
        console.print()

    if "fds" in data:
        center_rule(console, "Open file descriptors")
        fds = data["fds"]
        if fds["count"] is not None:
            console.print(
                f"[bold]{fds['count']}[/bold] total fd(s), {len(fds['files'])} open file(s), {fds['sockets']} socket(s)"
            )
        if fds.get("files_error"):
            warn(console, fds["files_error"])
        elif fds["files"]:
            t = Table(show_header=True, header_style="bold cyan")
            t.add_column("FD", justify="right")
            t.add_column("MODE")
            t.add_column("PATH")
            for f in fds["files"][:25]:
                t.add_row(str(f["fd"]), f["mode"], f["path"])
            console.print(t)
            if len(fds["files"]) > 25:
                console.print(f"[dim]…{len(fds['files']) - 25} more[/dim]")
        console.print()


def _collect_kill_targets(pattern: str) -> dict:
    """Find processes matching PATTERN without sending any signal."""
    matches = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmd = _proc_cmdline(p.info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if pattern.lower() in cmd.lower():
            matches.append(
                {
                    "pid": p.info["pid"],
                    "name": p.info.get("name", "—"),
                    "cmd": cmd,
                }
            )
    return {"pattern": pattern, "matched": len(matches), "matches": matches}


def _execute_kill(matches: list[dict], sig: int) -> list[dict]:
    killed = []
    for m in matches:
        try:
            p = psutil.Process(m["pid"])
            p.send_signal(sig)
            killed.append({"pid": m["pid"], "name": m["name"], "ok": True, "error": None})
        except psutil.NoSuchProcess:
            killed.append(
                {"pid": m["pid"], "name": m["name"], "ok": False, "error": "already gone"}
            )
        except psutil.AccessDenied:
            killed.append(
                {
                    "pid": m["pid"],
                    "name": m["name"],
                    "ok": False,
                    "error": "access denied (try sudo)",
                }
            )
    return killed


@click.group("proc")
@click.pass_context
def proc(ctx: click.Context) -> None:
    """Process manager: list, find, kill, inspect a process tree."""
    pass


@proc.command("top")
@click.option("-n", "--num", default=15, show_default=True, type=int)
@click.option(
    "-s",
    "--sort",
    "sort_key",
    type=click.Choice(["cpu", "mem", "rss", "time"]),
    default="cpu",
    show_default=True,
)
@json_watch_options
@click.pass_context
def _proc_top(
    ctx: click.Context,
    num: int,
    sort_key: str,
    json_mode: bool | None = None,
    watch_secs: float | None = None,
) -> None:
    """List top processes by CPU or memory."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = _collect_top(num, sort_key)
    if emit(ctx, data, command="proc top"):
        return
    _render_top(console, data)


@proc.command("find")
@click.argument("name")
@json_watch_options
@click.pass_context
def _proc_find(
    ctx: click.Context, name: str, json_mode: bool | None = None, watch_secs: float | None = None
) -> None:
    """Find processes by command substring."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = _collect_find(name)
    if emit(ctx, data, command="proc find"):
        return
    _render_find(console, data)


@proc.command("tree")
@click.argument("name", required=False, default=None)
@click.option("--depth", default=None, type=int, help="Max depth (default: unlimited).")
@json_watch_options
@click.pass_context
def _proc_tree(
    ctx: click.Context,
    name: str | None,
    depth: int | None,
    json_mode: bool | None = None,
    watch_secs: float | None = None,
) -> None:
    """Display a process tree (filter by NAME or default to PID 1)."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    max_depth = max(0, depth) if depth is not None else None
    data = _collect_tree(name, max_depth)
    if emit(ctx, data, command="proc tree"):
        return
    _render_tree(console, data)


@proc.command("kill")
@click.argument("pattern")
@click.option("-9", "--force", is_flag=True, help="Send SIGKILL instead of SIGTERM.")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation.")
@json_option
@click.pass_context
def _proc_kill(
    ctx: click.Context, pattern: str, force: bool, yes: bool, json_mode: bool | None = None
) -> None:
    """Send signal to processes whose command line contains PATTERN."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    sig = signal.SIGKILL if force else signal.SIGTERM
    targets = _collect_kill_targets(pattern)
    if targets["matched"] == 0:
        if emit(ctx, {**targets, "signal": int(sig), "killed": []}, command="proc kill"):
            return
        console.print(f"[dim]no matching processes for '{pattern}'[/dim]")
        return
    if not ctx.obj.json:
        center_rule(console, f"Processes matching '{pattern}' (signal {sig:03d})")
        t = Table(show_header=True, header_style="bold cyan")
        t.add_column("PID", justify="right")
        t.add_column("NAME")
        t.add_column("COMMAND")
        for match in targets["matches"]:
            cmd = match["cmd"] if len(match["cmd"]) <= 60 else match["cmd"][:57] + "…"
            t.add_row(str(match["pid"]), match["name"], cmd)
        console.print(t)
    signal_name = "SIGKILL" if force else "SIGTERM"
    if not confirm_destructive(
        ctx,
        f"Send {signal_name} to {targets['matched']} process(es)?",
        yes=yes,
    ):
        raise click.exceptions.Abort()
    killed = _execute_kill(targets["matches"], sig)
    data = {**targets, "signal": int(sig), "killed": killed}
    if emit(ctx, data, command="proc kill"):
        if any(not k["ok"] for k in killed):
            raise click.exceptions.Exit(1)
        return
    for entry in killed:
        if entry["ok"]:
            ok(console, f"killed {entry['pid']} ({entry['name']})")
        else:
            warn(console, f"PID {entry['pid']} {entry['error']}")
    if any(not k["ok"] for k in killed):
        raise click.exceptions.Exit(1)


@proc.command("show")
@click.argument("pid", type=int)
@click.option("--threads/--no-threads", default=True, help="Include thread info.")
@click.option("--env/--no-env", default=True, help="Include environment variables.")
@click.option("--fd/--no-fd", default=True, help="Include open file descriptors.")
@click.option("--cwd/--no-cwd", default=True, help="Include working directory.")
@json_watch_options
@click.pass_context
def _proc_show(
    ctx: click.Context,
    pid: int,
    threads: bool,
    env: bool,
    fd: bool,
    cwd: bool,
    json_mode: bool | None = None,
    watch_secs: float | None = None,
) -> None:
    """Deep-inspect a single process: status, threads, env, fds, cwd."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    want = {"threads": threads, "env": env, "fd": fd, "cwd": cwd}
    data = _collect_show(pid, want)
    if emit(ctx, data, command="proc show"):
        return
    _render_show(console, data)


@proc.command("me")
@json_watch_options
@click.pass_context
def _proc_me(
    ctx: click.Context, json_mode: bool | None = None, watch_secs: float | None = None
) -> None:
    """Show processes owned by the current user."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    uid = os.getuid()
    rows = []
    for p in psutil.process_iter(
        ["pid", "uids", "cpu_percent", "memory_percent", "cmdline", "name"]
    ):
        try:
            if p.info["uids"].real != uid:
                continue
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        cmd = _proc_cmdline(p.info)
        rows.append(
            {
                "pid": p.info["pid"],
                "cpu_percent": round(p.info.get("cpu_percent") or 0, 1),
                "memory_percent": round(p.info.get("memory_percent") or 0, 1),
                "cmd": cmd,
            }
        )
    rows.sort(key=lambda r: r["cpu_percent"], reverse=True)
    data = {"uid": uid, "processes": rows[:40]}
    if emit(ctx, data, command="proc me"):
        return
    center_rule(console, f"Processes owned by uid {uid}")
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("PID", justify="right")
    t.add_column("CPU%", justify="right")
    t.add_column("MEM%", justify="right")
    t.add_column("COMMAND")
    for p in data["processes"]:
        cmd = p["cmd"] if len(p["cmd"]) <= 60 else p["cmd"][:57] + "…"
        t.add_row(str(p["pid"]), f"{p['cpu_percent']:.1f}", f"{p['memory_percent']:.1f}", cmd)
    console.print(t)
