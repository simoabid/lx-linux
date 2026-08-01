"""lx service — systemd wrapper: status, list, start/stop, enable/disable."""
from __future__ import annotations

import click
from rich.table import Table

from lx.utils.output import err, ok
from lx.utils.shell import is_root, run


def _check_systemctl(console) -> bool:
    res = run(["systemctl", "is-system-running"], timeout=10)
    if not res.ok and (
        res.returncode == 127
        or "no such file" in (res.stderr or "").lower()
        or "command not found" in (res.stderr or "").lower()
    ):
        err(console, "systemctl not found — this host is not running systemd")
        return False
    return True


@click.group("service")
@click.pass_context
def service(ctx: click.Context) -> None:
    """Wrapper around systemctl (status, start/stop/enable, log tail)."""
    pass


@service.command("status")
@click.argument("name")
@click.pass_context
def _svc_status(ctx: click.Context, name: str) -> None:
    """Show full status of a unit."""
    console = ctx.obj.console
    if not _check_systemctl(console):
        raise click.exceptions.Exit(1)
    res = run(["systemctl", "status", name, "--no-pager"], sudo=False, timeout=15)
    console.print(res.stdout or res.stderr)


@service.command("list")
@click.option("--state", default=None, help="Filter by state: failed|active|exited|running")
@click.option("--type", "unit_type", default=None, help="Filter by unit type: service|timer|socket|mount")
@click.pass_context
def _svc_list(ctx: click.Context, state: str | None, unit_type: str | None) -> None:
    """List systemd units, optionally filtered."""
    console = ctx.obj.console
    if not _check_systemctl(console):
        raise click.exceptions.Exit(1)
    args = ["systemctl", "list-units", "--no-pager", "--no-legend", "--plain"]
    if state:
        args += [f"--state={state}"]
    if unit_type:
        args += [f"--type={unit_type}"]
    res = run(args, timeout=30)
    if not res.ok:
        err(console, res.stderr)
        return
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("UNIT")
    t.add_column("LOAD")
    t.add_column("ACTIVE")
    t.add_column("SUB")
    t.add_column("DESCRIPTION")
    for line in res.stdout.splitlines():
        cols = line.split(None, 4)
        if len(cols) < 5:
            continue
        unit, load, active, sub, desc = cols
        active_color = "green" if active == "active" else ("red" if active == "failed" else "dim")
        t.add_row(unit, load, f"[{active_color}]{active}[/{active_color}]", sub, desc.rstrip("."))
    console.print(t)


@service.command("failed")
@click.pass_context
def _svc_failed(ctx: click.Context) -> None:
    """Show only failed units."""
    console = ctx.obj.console
    if not _check_systemctl(console):
        raise click.exceptions.Exit(1)
    res = run(["systemctl", "--failed", "--no-pager", "--no-legend", "--plain"], timeout=10)
    lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
    if not lines:
        ok(console, "no failed units")
        return
    for line in lines:
        cols = line.split(None, 4)
        unit = cols[0] if cols else "?"
        active = cols[2] if len(cols) > 2 else "?"
        console.print(f"  [red]✗[/red] [bold]{unit}[/bold]  [dim]({active})[/dim]")


@service.command("log")
@click.argument("name")
@click.option("-n", "--lines", default=50, show_default=True, type=int, help="Number of log lines.")
@click.pass_context
def _svc_log(ctx: click.Context, name: str, lines: int) -> None:
    """Show recent log lines from a unit's journal."""
    console = ctx.obj.console
    res = run(["journalctl", "-u", name, "-n", str(lines), "--no-pager"], timeout=15)
    console.print(res.stdout or "[dim]no log output[/dim]")


def _svc_action(action: str, console, name: str) -> None:
    """Run a systemctl action; requires root, exits 1 on failure."""
    if not is_root():
        err(console, f"'{action}' requires root. Retry with: sudo lx service {action} {name}")
        raise click.exceptions.Exit(1)
    res = run(["systemctl", action, name], timeout=30)
    if res.ok:
        ok(console, f"{action} {name}")
    else:
        err(console, res.stderr or res.stdout)
        raise click.exceptions.Exit(1)


@service.command("restart")
@click.argument("name")
@click.pass_context
def _svc_restart(ctx: click.Context, name: str) -> None:
    """Restart a unit."""
    _svc_action("restart", ctx.obj.console, name)


@service.command("stop")
@click.argument("name")
@click.pass_context
def _svc_stop(ctx: click.Context, name: str) -> None:
    """Stop a unit."""
    _svc_action("stop", ctx.obj.console, name)


@service.command("start")
@click.argument("name")
@click.pass_context
def _svc_start(ctx: click.Context, name: str) -> None:
    """Start a unit."""
    _svc_action("start", ctx.obj.console, name)


@service.command("enable")
@click.argument("name")
@click.pass_context
def _svc_enable(ctx: click.Context, name: str) -> None:
    """Enable a unit at boot."""
    _svc_action("enable", ctx.obj.console, name)


@service.command("disable")
@click.argument("name")
@click.pass_context
def _svc_disable(ctx: click.Context, name: str) -> None:
    """Disable a unit at boot."""
    _svc_action("disable", ctx.obj.console, name)


@service.command("timers")
@click.pass_context
def _svc_timers(ctx: click.Context) -> None:
    """List all systemd timers."""
    console = ctx.obj.console
    if not _check_systemctl(console):
        raise click.exceptions.Exit(1)
    res = run(["systemctl", "list-timers", "--no-pager", "--no-legend", "--plain"], timeout=10)
    if not res.ok:
        err(console, "no timers")
        return
    t = Table(show_header=True, header_style="bold cyan")
    for h in ["NEXT", "LEFT", "LAST", "PASSED", "UNIT", "ACTIVATES"]:
        t.add_column(h)
    for line in res.stdout.splitlines():
        cols = line.split()
        if len(cols) < 6:
            continue
        t.add_row(*cols[:6])
    console.print(t)
