"""lx proc — process manager: list, find, kill, tree view."""
from __future__ import annotations

import os
import signal

import click
import psutil
from rich.table import Table

from lx.utils.output import center_rule, err, ok, warn


def _human_time(seconds: float) -> str:
    d = int(seconds // 86400)
    h = int((seconds % 86400) // 3600)
    m = int((seconds % 3600) // 60)
    if d:
        return f"{d}d{h}h"
    if h:
        return f"{h}h{m}m"
    return f"{m}m"


def _top(console, count: int, sort_key: str) -> None:
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

    center_rule(console, f"Top {count} processes (by {sort_key})")
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("PID", justify="right")
    t.add_column("CPU%", justify="right")
    t.add_column("MEM%", justify="right")
    t.add_column("RSS")
    t.add_column("USER")
    t.add_column("TIME")
    t.add_column("COMMAND")
    import time as _t

    now = _t.time()
    for p in procs[:count]:
        cmd = " ".join(p.get("cmdline") or []) or p.get("name") or "—"
        # take only the executable name + first flag to keep rows compact
        if len(cmd) > 45:
            cmd = cmd[:42] + "…"
        created = p.get("create_time")
        age = _human_time(now - created) if created else "—"
        t.add_row(
            str(p.get("pid")),
            f"{p.get('cpu_percent', 0) or 0:.1f}",
            f"{p.get('memory_percent', 0) or 0:.1f}",
            _human_bytes(p.get("rss", 0)),
            p.get("username") or "—",
            age,
            cmd,
        )
    console.print(t)


def _human_bytes(n: float) -> str:
    for u in ("B", "K", "M", "G"):
        if n < 1024:
            return f"{n:.0f}{u}"
        n /= 1024
    return f"{n:.0f}T"


def _find(console, name: str) -> None:
    center_rule(console, f"Processes matching '{name}'")
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("PID", justify="right")
    t.add_column("USER")
    t.add_column("CPU%", justify="right")
    t.add_column("MEM%", justify="right")
    t.add_column("COMMAND")
    pattern = name.lower()
    for p in psutil.process_iter(["pid", "username", "cpu_percent", "memory_percent", "cmdline", "name"]):
        try:
            cmd = " ".join(p.info.get("cmdline") or []) or p.info.get("name") or ""
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if pattern in cmd.lower():
            disp = cmd if len(cmd) <= 70 else cmd[:67] + "…"
            t.add_row(
                str(p.info.get("pid")),
                p.info.get("username") or "—",
                f"{p.info.get('cpu_percent') or 0:.1f}",
                f"{p.info.get('memory_percent') or 0:.1f}",
                disp,
            )
    if t.row_count:
        console.print(t)
    else:
        console.print(f"[dim]no processes matched '{name}'[/dim]")


def _tree(console, name: str | None) -> None:
    center_rule(console, "Process tree (children of matching processes)")
    matches = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        if name is None:
            continue
        try:
            cmd = " ".join(p.info.get("cmdline") or []) or p.info.get("name") or ""
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if name.lower() in cmd.lower() and p.info.get("pid") not in (m.pid for m in matches):
            matches.append(p)

    if name and not matches:
        console.print(f"[dim]no matches for '{name}'[/dim]")
        return

    def _print_sub(root: psutil.Process, indent: int) -> None:
        prefix = "  " * indent
        try:
            info = root.as_dict(["pid", "name", "cmdline"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return
        cmd = " ".join(info.get("cmdline") or []) or info.get("name") or "—"
        if len(cmd) > 60:
            cmd = cmd[:57] + "…"
        console.print(f"{prefix}[bold]{info['pid']}[/bold] [cyan]{info.get('name', '—')}[/cyan] [dim]{cmd}[/dim]")
        try:
            for child in root.children(recursive=False):
                _print_sub(child, indent + 1)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    roots = matches if matches else [
        psutil.Process(p) for p in (1,)
    ]
    if not matches and not roots:
        console.print("[dim]no running processes found[/dim]")
        return
    for root in roots:
        try:
            _print_sub(root, 0)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


def _kill(console, pattern: str, sig: int, force: bool) -> None:
    center_rule(console, f"Kill processes matching '{pattern}' (signal {sig:{'03d'}})")
    matches = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmd = " ".join(p.info.get("cmdline") or []) or p.info.get("name") or ""
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if pattern.lower() in cmd.lower():
            matches.append(p)
    if not matches:
        console.print(f"[dim]no matching processes for '{pattern}'[/dim]")
        return

    for p in matches:
        try:
            pinfo = p.info
            console.print(f"  → killing PID {pinfo['pid']} ({pinfo.get('name', '—')})")
            p.send_signal(sig)
            ok(console, f"  killed {pinfo['pid']}")
        except psutil.NoSuchProcess:
            warn(console, f"  PID {p.info.get('pid')} already gone")
        except psutil.AccessDenied:
            err(console, f"  access denied for PID {p.info.get('pid')} (try sudo)")


@click.group("proc")
@click.pass_context
def proc(ctx: click.Context) -> None:
    """Process manager: list, find, kill, inspect a process tree."""
    pass


@proc.command("top")
@click.option("-n", "--num", default=15, show_default=True, type=int)
@click.option("-s", "--sort", "sort_key",
              type=click.Choice(["cpu", "mem", "rss", "time"]),
              default="cpu", show_default=True)
@click.pass_context
def _proc_top(ctx: click.Context, num: int, sort_key: str) -> None:
    """List top processes by CPU or memory."""
    _top(ctx.obj.console, num, sort_key)


@proc.command("find")
@click.argument("name")
@click.pass_context
def _proc_find(ctx: click.Context, name: str) -> None:
    """Find processes by command substring."""
    _find(ctx.obj.console, name)


@proc.command("tree")
@click.argument("name", required=False, default=None)
@click.pass_context
def _proc_tree(ctx: click.Context, name: str | None) -> None:
    """Display a process tree (filter by NAME or default to PID 1)."""
    _tree(ctx.obj.console, name)


@proc.command("kill")
@click.argument("pattern")
@click.option("-9", "--force", is_flag=True, help="Send SIGKILL instead of SIGTERM.")
@click.pass_context
def _proc_kill(ctx: click.Context, pattern: str, force: bool) -> None:
    """Send signal to processes whose command line contains PATTERN."""
    sig = signal.SIGKILL if force else signal.SIGTERM
    _kill(ctx.obj.console, pattern, sig, force)


@proc.command("me")
@click.pass_context
def _proc_me(ctx: click.Context) -> None:
    """Show processes owned by the current user."""
    console = ctx.obj.console
    uid = os.getuid()
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("PID", justify="right")
    t.add_column("CPU%", justify="right")
    t.add_column("MEM%", justify="right")
    t.add_column("COMMAND")
    rows = []
    for p in psutil.process_iter(["pid", "uids", "cpu_percent", "memory_percent", "cmdline", "name"]):
        try:
            if p.info["uids"].real != uid:
                continue
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        cmd = " ".join(p.info.get("cmdline") or []) or p.info.get("name") or "—"
        if len(cmd) > 60:
            cmd = cmd[:57] + "…"
        rows.append((p.info["pid"], p.info.get("cpu_percent") or 0, p.info.get("memory_percent") or 0, cmd))
    rows.sort(key=lambda r: r[1], reverse=True)
    center_rule(console, f"Processes owned by uid {uid}")
    for pid, cpu, mem, cmd in rows[:40]:
        t.add_row(str(pid), f"{cpu:.1f}", f"{mem:.1f}", cmd)
    console.print(t)
