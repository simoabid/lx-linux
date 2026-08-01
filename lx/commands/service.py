"""lx service — systemd wrapper: status, list, start/stop, enable/disable."""

from __future__ import annotations

import click
from rich.table import Table

from lx.utils.flags import apply_flags, json_option, json_watch_options
from lx.utils.output import center_rule, emit, err, ok
from lx.utils.parse import (
    parse_blame,
    parse_critical_chain,
    parse_systemd_analyze,
    parse_timers,
)
from lx.utils.prompt import confirm_destructive
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
@click.option("--user", is_flag=True, help="Operate on user units (no root needed).")
@click.pass_context
def service(ctx: click.Context, user: bool) -> None:
    """Wrapper around systemctl (status, start/stop/enable, log tail)."""
    ctx.obj.data["user"] = user


def _svc_args(base: list[str], user: bool) -> list[str]:
    return ["systemctl", "--user", *base] if user else ["systemctl", *base]


def _collect_status(name: str, user: bool = False) -> dict:
    res = run(_svc_args(["status", name, "--no-pager"], user), sudo=False, timeout=15)
    return {"name": name, "user": user, "ok": res.ok, "stdout": res.stdout, "stderr": res.stderr}


def _render_status(console, data: dict) -> None:
    if data["ok"]:
        console.print(data["stdout"])
    else:
        console.print(data["stderr"] or "[dim]no output[/dim]")


@service.command("status")
@click.argument("name")
@json_watch_options
@click.pass_context
def _svc_status(
    ctx: click.Context, name: str, json_mode: bool | None = None, watch_secs: float | None = None
) -> None:
    """Show full status of a unit."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    if not _check_systemctl(console):
        raise click.exceptions.Exit(1)
    user = bool(ctx.obj.data.get("user"))
    data = _collect_status(name, user)
    if emit(ctx, data, command="service status"):
        return
    _render_status(console, data)


def _collect_list(state: str | None, unit_type: str | None, user: bool = False) -> dict:
    args = _svc_args(["list-units", "--no-pager", "--no-legend", "--plain"], user)
    if state:
        args += [f"--state={state}"]
    if unit_type:
        args += [f"--type={unit_type}"]
    res = run(args, timeout=30)
    units = []
    for line in res.stdout.splitlines():
        cols = line.split(None, 4)
        if len(cols) < 5:
            continue
        unit, load, active, sub, desc = cols
        units.append(
            {
                "unit": unit,
                "load": load,
                "active": active,
                "sub": sub,
                "description": desc.rstrip("."),
            }
        )
    return {
        "state": state,
        "type": unit_type,
        "user": user,
        "ok": res.ok,
        "units": units,
        "stderr": res.stderr,
    }


def _render_list(console, data: dict) -> None:
    if not data["ok"]:
        err(console, data["stderr"])
        return
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("UNIT")
    t.add_column("LOAD")
    t.add_column("ACTIVE")
    t.add_column("SUB")
    t.add_column("DESCRIPTION")
    for u in data["units"]:
        active_color = (
            "green" if u["active"] == "active" else ("red" if u["active"] == "failed" else "dim")
        )
        t.add_row(
            u["unit"],
            u["load"],
            f"[{active_color}]{u['active']}[/{active_color}]",
            u["sub"],
            u["description"],
        )
    console.print(t)


@service.command("list")
@click.option("--state", default=None, help="Filter by state: failed|active|exited|running")
@click.option(
    "--type", "unit_type", default=None, help="Filter by unit type: service|timer|socket|mount"
)
@json_watch_options
@click.pass_context
def _svc_list(
    ctx: click.Context,
    state: str | None,
    unit_type: str | None,
    json_mode: bool | None = None,
    watch_secs: float | None = None,
) -> None:
    """List systemd units, optionally filtered."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    if not _check_systemctl(console):
        raise click.exceptions.Exit(1)
    user = bool(ctx.obj.data.get("user"))
    data = _collect_list(state, unit_type, user)
    if emit(ctx, data, command="service list"):
        return
    _render_list(console, data)


def _collect_failed() -> dict:
    res = run(["systemctl", "--failed", "--no-pager", "--no-legend", "--plain"], timeout=10)
    units = []
    for line in res.stdout.splitlines():
        cols = line.split(None, 4)
        if not cols:
            continue
        units.append(
            {
                "unit": cols[0],
                "active": cols[2] if len(cols) > 2 else "?",
                "description": cols[4].rstrip(".") if len(cols) > 4 else "",
            }
        )
    return {"count": len(units), "units": units}


def _render_failed(console, data: dict) -> None:
    if not data["units"]:
        ok(console, "no failed units")
        return
    for u in data["units"]:
        console.print(f"  [red]✗[/red] [bold]{u['unit']}[/bold]  [dim]({u['active']})[/dim]")


@service.command("failed")
@json_watch_options
@click.pass_context
def _svc_failed(
    ctx: click.Context, json_mode: bool | None = None, watch_secs: float | None = None
) -> None:
    """Show only failed units."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    if not _check_systemctl(console):
        raise click.exceptions.Exit(1)
    data = _collect_failed()
    if emit(ctx, data, command="service failed"):
        return
    _render_failed(console, data)


def _collect_log(name: str, lines: int) -> dict:
    res = run(["journalctl", "-u", name, "-n", str(lines), "--no-pager"], timeout=15)
    return {"unit": name, "ok": res.ok, "stdout": res.stdout, "stderr": res.stderr}


def _render_log(console, data: dict) -> None:
    console.print(data["stdout"] or "[dim]no log output[/dim]")


@service.command("log")
@click.argument("name")
@click.option("-n", "--lines", default=50, show_default=True, type=int, help="Number of log lines.")
@json_watch_options
@click.pass_context
def _svc_log(
    ctx: click.Context,
    name: str,
    lines: int,
    json_mode: bool | None = None,
    watch_secs: float | None = None,
) -> None:
    """Show recent log lines from a unit's journal."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = _collect_log(name, lines)
    if emit(ctx, data, command="service log"):
        return
    _render_log(console, data)


def _svc_action_collect(action: str, name: str | None, user: bool = False) -> dict:
    args = [action] if name is None else [action, name]
    if not user and not is_root():
        verb = " ".join(args)
        return {
            "action": action,
            "name": name,
            "user": user,
            "ok": False,
            "stderr": f"'{verb}' requires root. Retry with: sudo lx service {verb}",
            "stdout": "",
        }
    res = run(_svc_args(args, user), timeout=30)
    return {
        "action": action,
        "name": name,
        "user": user,
        "ok": res.ok,
        "stdout": res.stdout,
        "stderr": res.stderr,
    }


@service.command("restart")
@click.argument("name")
@json_option
@click.pass_context
def _svc_restart(ctx: click.Context, name: str, json_mode: bool | None = None) -> None:
    """Restart a unit."""
    apply_flags(ctx, json_mode)
    data = _svc_action_collect("restart", name, bool(ctx.obj.data.get("user")))
    if emit(ctx, data, command="service restart"):
        return
    if data["ok"]:
        ok(ctx.obj.console, f"restart {name}")
    else:
        err(ctx.obj.console, data["stderr"] or data["stdout"])
        raise click.exceptions.Exit(1)


@service.command("stop")
@click.argument("name")
@json_option
@click.pass_context
def _svc_stop(ctx: click.Context, name: str, json_mode: bool | None = None) -> None:
    """Stop a unit."""
    apply_flags(ctx, json_mode)
    data = _svc_action_collect("stop", name, bool(ctx.obj.data.get("user")))
    if emit(ctx, data, command="service stop"):
        return
    if data["ok"]:
        ok(ctx.obj.console, f"stop {name}")
    else:
        err(ctx.obj.console, data["stderr"] or data["stdout"])
        raise click.exceptions.Exit(1)


@service.command("start")
@click.argument("name")
@json_option
@click.pass_context
def _svc_start(ctx: click.Context, name: str, json_mode: bool | None = None) -> None:
    """Start a unit."""
    apply_flags(ctx, json_mode)
    data = _svc_action_collect("start", name, bool(ctx.obj.data.get("user")))
    if emit(ctx, data, command="service start"):
        return
    if data["ok"]:
        ok(ctx.obj.console, f"start {name}")
    else:
        err(ctx.obj.console, data["stderr"] or data["stdout"])
        raise click.exceptions.Exit(1)


@service.command("enable")
@click.argument("name")
@json_option
@click.pass_context
def _svc_enable(ctx: click.Context, name: str, json_mode: bool | None = None) -> None:
    """Enable a unit at boot."""
    apply_flags(ctx, json_mode)
    data = _svc_action_collect("enable", name, bool(ctx.obj.data.get("user")))
    if emit(ctx, data, command="service enable"):
        return
    if data["ok"]:
        ok(ctx.obj.console, f"enable {name}")
    else:
        err(ctx.obj.console, data["stderr"] or data["stdout"])
        raise click.exceptions.Exit(1)


@service.command("disable")
@click.argument("name")
@json_option
@click.pass_context
def _svc_disable(ctx: click.Context, name: str, json_mode: bool | None = None) -> None:
    """Disable a unit at boot."""
    apply_flags(ctx, json_mode)
    data = _svc_action_collect("disable", name, bool(ctx.obj.data.get("user")))
    if emit(ctx, data, command="service disable"):
        return
    if data["ok"]:
        ok(ctx.obj.console, f"disable {name}")
    else:
        err(ctx.obj.console, data["stderr"] or data["stdout"])
        raise click.exceptions.Exit(1)


def _collect_timers() -> dict:
    res = run(["systemctl", "list-timers", "--no-pager", "--no-legend", "--plain"], timeout=10)
    timers = parse_timers(res.stdout)
    return {"ok": res.ok, "timers": timers, "stderr": res.stderr}


def _render_timers(console, data: dict) -> None:
    if not data["ok"]:
        err(console, "no timers")
        return
    t = Table(show_header=True, header_style="bold cyan")
    for h in ["NEXT", "LEFT", "LAST", "PASSED", "UNIT", "ACTIVATES"]:
        t.add_column(h)
    for timer in data["timers"]:
        t.add_row(
            timer["next"],
            timer["left"],
            timer["last"],
            timer["passed"],
            timer["unit"],
            timer["activates"],
        )
    console.print(t)


@service.command("timers")
@json_watch_options
@click.pass_context
def _svc_timers(
    ctx: click.Context, json_mode: bool | None = None, watch_secs: float | None = None
) -> None:
    """List all systemd timers."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    if not _check_systemctl(console):
        raise click.exceptions.Exit(1)
    data = _collect_timers()
    if emit(ctx, data, command="service timers"):
        return
    _render_timers(console, data)


# ---------------------------------------------------------------- analyze


def _collect_blame(num: int) -> dict:
    res = run(["systemd-analyze", "blame"], timeout=30)
    if not res.ok:
        return {"ok": False, "error": res.stderr or "systemd-analyze failed", "units": []}
    units = parse_blame(res.stdout)
    return {"ok": True, "error": None, "units": units, "count": len(units), "num": num}


def _render_blame(console, data: dict) -> None:
    if data.get("error"):
        err(console, data["error"])
        return
    center_rule(console, f"Boot unit time (top {data['num']} of {data['count']})")
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("TIME")
    t.add_column("UNIT")
    for u in data["units"][: data["num"]]:
        t.add_row(f"{u['time_ms'] / 1000:.2f}s", u["unit"])
    console.print(t)


@service.command("blame")
@click.option("-n", "--num", default=15, show_default=True, type=int, help="Show top N units.")
@json_watch_options
@click.pass_context
def _svc_blame(
    ctx: click.Context, num: int, json_mode: bool | None = None, watch_secs: float | None = None
) -> None:
    """Show which units slow the boot (systemd-analyze blame)."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = _collect_blame(num)
    if emit(ctx, data, command="service blame"):
        if not data["ok"]:
            raise click.exceptions.Exit(1)
        return
    _render_blame(console, data)
    if not data["ok"]:
        raise click.exceptions.Exit(1)


def _collect_boot() -> dict:
    summary = run(["systemd-analyze"], timeout=30)
    chain = run(["systemd-analyze", "critical-chain"], timeout=30)
    if not summary.ok:
        return {"ok": False, "error": summary.stderr or "systemd-analyze failed"}
    data = parse_systemd_analyze(summary.stdout)
    data["ok"] = True
    data["error"] = None
    data["critical_chain"] = parse_critical_chain(chain.stdout) if chain.ok else []
    return data


def _render_boot(console, data: dict) -> None:
    if data.get("error"):
        err(console, data["error"])
        return
    center_rule(console, "Boot time")
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("Phase")
    t.add_column("Duration")
    rows = [
        ("kernel", data.get("kernel")),
        ("userspace", data.get("userspace")),
        ("total", data.get("total")),
    ]
    for label, val in rows:
        if isinstance(val, float):
            val = f"{val:.3f}s"
        t.add_row(label, val or "—")
    for label, val in (("firmware", data.get("firmware")), ("loader", data.get("loader"))):
        if isinstance(val, float):
            val = f"{val:.3f}s"
        if val:
            t.add_row(label, str(val))
    console.print(t)
    if data["critical_chain"]:
        center_rule(console, "Critical chain")
        ct = Table(show_header=True, header_style="bold cyan")
        ct.add_column("ACTIVE")
        ct.add_column("DURATION")
        ct.add_column("UNIT")
        for u in data["critical_chain"]:
            ct.add_row(f"{u['active_s']:.2f}s", f"{u['duration_s']:.2f}s", u["unit"])
        console.print(ct)


@service.command("boot")
@json_watch_options
@click.pass_context
def _svc_boot(
    ctx: click.Context, json_mode: bool | None = None, watch_secs: float | None = None
) -> None:
    """Show boot-time breakdown (systemd-analyze + critical chain)."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = _collect_boot()
    if emit(ctx, data, command="service boot"):
        if not data["ok"]:
            raise click.exceptions.Exit(1)
        return
    _render_boot(console, data)
    if not data["ok"]:
        raise click.exceptions.Exit(1)


@service.command("reload")
@click.argument("name")
@json_option
@click.pass_context
def _svc_reload(ctx: click.Context, name: str, json_mode: bool | None = None) -> None:
    """Reload a unit's configuration without restarting it."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    user = bool(ctx.obj.data.get("user"))
    if not user and not is_root():
        err(console, f"'reload' requires root. Retry with: sudo lx service reload {name}")
        raise click.exceptions.Exit(1)
    data = _svc_action_collect("reload", name, user)
    if emit(ctx, data, command="service reload"):
        if not data["ok"]:
            raise click.exceptions.Exit(1)
        return
    if data["ok"]:
        ok(console, f"reload {name}")
    else:
        err(console, data["stderr"] or data["stdout"])
        raise click.exceptions.Exit(1)


@service.command("daemon-reload")
@json_option
@click.pass_context
def _svc_daemon_reload(ctx: click.Context, json_mode: bool | None = None) -> None:
    """Reload systemd's unit definitions (systemctl daemon-reload)."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    user = bool(ctx.obj.data.get("user"))
    data = _svc_action_collect("daemon-reload", None, user)
    if emit(ctx, data, command="service daemon-reload"):
        if not data["ok"]:
            raise click.exceptions.Exit(1)
        return
    if data["ok"]:
        ok(console, "daemon-reload done")
    else:
        err(console, data["stderr"] or data["stdout"])
        raise click.exceptions.Exit(1)


@service.command("mask")
@click.argument("name")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation.")
@json_option
@click.pass_context
def _svc_mask(ctx: click.Context, name: str, yes: bool, json_mode: bool | None = None) -> None:
    """Fully disable a unit (mask it so it cannot be started)."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    user = bool(ctx.obj.data.get("user"))
    if not confirm_destructive(ctx, f"Mask unit {name}?", yes=yes):
        raise click.exceptions.Abort()
    data = _svc_action_collect("mask", name, user)
    if emit(ctx, data, command="service mask"):
        if not data["ok"]:
            raise click.exceptions.Exit(1)
        return
    if data["ok"]:
        ok(console, f"mask {name}")
    else:
        err(console, data["stderr"] or data["stdout"])
        raise click.exceptions.Exit(1)


@service.command("unmask")
@click.argument("name")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation.")
@json_option
@click.pass_context
def _svc_unmask(ctx: click.Context, name: str, yes: bool, json_mode: bool | None = None) -> None:
    """Unmask a unit."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    user = bool(ctx.obj.data.get("user"))
    if not confirm_destructive(ctx, f"Unmask unit {name}?", yes=yes):
        raise click.exceptions.Abort()
    data = _svc_action_collect("unmask", name, user)
    if emit(ctx, data, command="service unmask"):
        if not data["ok"]:
            raise click.exceptions.Exit(1)
        return
    if data["ok"]:
        ok(console, f"unmask {name}")
    else:
        err(console, data["stderr"] or data["stdout"])
        raise click.exceptions.Exit(1)
