"""lx log — journal explorer: filtered views, error focus, live follow."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime

import click

from lx.utils.flags import apply_flags, json_option, json_watch_options
from lx.utils.output import center_rule, emit, err, kv_table
from lx.utils.parse import _PRIORITY_NAMES, parse_journal_json
from lx.utils.shell import run

_PRIORITY_BY_NAME = {name: idx for idx, name in enumerate(_PRIORITY_NAMES)}

_LEVEL_COLORS = {
    "emerg": "red",
    "alert": "red",
    "crit": "red",
    "err": "red",
    "warning": "yellow",
    "notice": "cyan",
    "info": "dim",
    "debug": "dim",
}


def _normalize_priority(priority: str | None) -> int | None:
    """Map a priority name or digit (0–7) to an int; None when unset."""
    if priority is None:
        return None
    if priority.isdigit():
        value = int(priority)
        if 0 <= value <= 7:
            return value
        raise click.BadParameter("must be between 0 and 7")
    name = priority.lower()
    if name in _PRIORITY_BY_NAME:
        return _PRIORITY_BY_NAME[name]
    raise click.BadParameter(
        "must be a priority name (emerg, alert, crit, err, warning, notice, info, debug) or 0–7"
    )


def _base_args(
    lines: int,
    priority: int | None,
    unit: str | None,
    since: str | None,
    until: str | None,
    follow: bool = False,
) -> list[str]:
    args = ["journalctl", "-o", "json", "-n", str(lines)]
    args.append("-f" if follow else "--no-pager")
    if priority is not None:
        args += ["-p", str(priority)]
    if unit:
        args += ["-u", unit]
    if since:
        args += ["--since", since]
    if until:
        args += ["--until", until]
    return args


def _journal_error(res) -> str:
    if res.returncode == 127:
        return "journalctl not found — this host is not running systemd"
    message = (res.stderr or res.stdout or "journalctl failed").strip()
    suffix = "run with sudo or add your user to the adm/systemd-journal group"
    return f"{message} — {suffix}" if message else f"journalctl failed — {suffix}"


def _format_entry(entry: dict) -> str:
    ts = ""
    if entry["ts"] is not None:
        try:
            ts = datetime.fromtimestamp(entry["ts"]).strftime("%H:%M:%S")
        except (OverflowError, OSError, ValueError):
            ts = ""
    color = _LEVEL_COLORS.get(entry["level"], "dim")
    message = entry["message"] if len(entry["message"]) <= 500 else entry["message"][:497] + "…"
    return (
        f"[dim]{ts}[/dim] "
        f"[{color}]{entry['level']:>7}[/{color}] "
        f"[bold]{entry['unit'][:24]:<24}[/bold] {message}"
    )


def _collect_show(
    lines: int, since: str | None, until: str | None, priority: int | None, unit: str | None, grep: str | None
) -> dict:
    args = _base_args(lines, priority, unit, since, until)
    res = run(args, timeout=30)
    if not res.ok:
        return {"ok": False, "error": _journal_error(res), "count": 0, "entries": []}
    entries = parse_journal_json(res.stdout.splitlines())
    if grep:
        try:
            pattern = re.compile(grep)
        except re.error as exc:
            return {"ok": False, "error": f"invalid --grep regex: {exc}", "count": 0, "entries": []}
        entries = [e for e in entries if pattern.search(e["message"])]
    return {
        "ok": True,
        "count": len(entries),
        "entries": entries,
        "filters": {
            "lines": lines,
            "since": since,
            "until": until,
            "priority": priority,
            "unit": unit,
            "grep": grep,
        },
    }


def _render_show(console, data: dict) -> None:
    if not data["ok"]:
        err(console, data["error"])
        return
    center_rule(console, f"Journal (last {data['count']} matching entries)")
    if not data["entries"]:
        console.print("[dim]no matching entries[/dim]")
        return
    for entry in data["entries"][:200]:
        console.print(_format_entry(entry))
    if data["count"] > 200:
        console.print(f"[dim]… {data['count'] - 200} more[/dim]")
    console.print()


def _collect_errors(lines: int, since: str | None, unit: str | None) -> dict:
    args = ["journalctl", "-o", "json", "--no-pager", "-p", "err", "-n", str(lines)]
    if unit:
        args += ["-u", unit]
    if since:
        args += ["--since", since]
    res = run(args, timeout=30)
    if not res.ok:
        return {"ok": False, "error": _journal_error(res), "count": 0, "top_units": [], "entries": []}
    entries = parse_journal_json(res.stdout.splitlines())
    per_unit: dict[str, int] = {}
    for entry in entries:
        unit = entry["unit"]
        per_unit[unit] = per_unit.get(unit, 0) + 1
    top_units = sorted(per_unit.items(), key=lambda kv: -kv[1])[:10]
    return {
        "ok": True,
        "count": len(entries),
        "top_units": [{"unit": u, "count": n} for u, n in top_units],
        "entries": entries,
        "filters": {"lines": lines, "since": since, "unit": unit},
    }


def _render_errors(console, data: dict) -> None:
    if not data["ok"]:
        err(console, data["error"])
        return
    center_rule(console, "Error focus (priority: err+)")
    if not data["count"]:
        ok_glyph = "[bold green]✓[/bold green]"
        console.print(f"{ok_glyph} no error-priority entries matched")
        return
    console.print(f"[bold red]{data['count']}[/bold red] error-priority entries")
    kv_table(console, [(u["unit"], str(u["count"])) for u in data["top_units"]])
    console.print()
    for entry in data["entries"][:50]:
        console.print(_format_entry(entry))
    if data["count"] > 50:
        console.print(f"[dim]… {data['count'] - 50} more[/dim]")
    console.print()


@click.group("log")
@click.pass_context
def log(ctx: click.Context) -> None:
    """Explore the systemd journal: filters, error focus, live follow."""
    pass


@log.command("show")
@click.option("-n", "--lines", default=50, show_default=True, type=int, help="Number of entries.")
@click.option("--since", default=None, help="Start time (journalctl syntax, e.g. '1h ago').")
@click.option("--until", default=None, help="End time (journalctl syntax, e.g. 'today').")
@click.option(
    "-p",
    "--priority",
    default=None,
    help="Minimum priority: emerg|alert|crit|err|warning|notice|info|debug.",
)
@click.option("-u", "--unit", default=None, help="Only entries from this unit.")
@click.option("--grep", default=None, help="Keep entries whose message matches this regex.")
@json_watch_options
@click.pass_context
def _log_show(
    ctx: click.Context,
    lines: int,
    since: str | None,
    until: str | None,
    priority: str | None,
    unit: str | None,
    grep: str | None,
    json_mode: bool | None = None,
    watch_secs: float | None = None,
) -> None:
    """Show journal entries with optional filters."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = _collect_show(
        lines, since, until, _normalize_priority(priority), unit, grep
    )
    if emit(ctx, data, command="log show"):
        return
    _render_show(console, data)


@log.command("errors")
@click.option("-n", "--lines", default=100, show_default=True, type=int, help="Number of entries.")
@click.option("--since", default=None, help="Start time (journalctl syntax, e.g. '1h ago').")
@click.option("-u", "--unit", default=None, help="Only entries from this unit.")
@json_watch_options
@click.pass_context
def _log_errors(
    ctx: click.Context,
    lines: int,
    since: str | None,
    unit: str | None,
    json_mode: bool | None = None,
    watch_secs: float | None = None,
) -> None:
    """Show error-priority entries with the most noisy units first."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = _collect_errors(lines, since, unit)
    if emit(ctx, data, command="log errors"):
        return
    _render_errors(console, data)


@log.command("follow")
@click.option("-n", "--lines", default=50, show_default=True, type=int, help="Backlog lines.")
@click.option(
    "-p",
    "--priority",
    default=None,
    help="Minimum priority: emerg|alert|crit|err|warning|notice|info|debug.",
)
@click.option("-u", "--unit", default=None, help="Only entries from this unit.")
@json_option
@click.pass_context
def _log_follow(
    ctx: click.Context,
    lines: int,
    priority: str | None,
    unit: str | None,
    json_mode: bool | None = None,
) -> None:
    """Stream new journal entries live (Ctrl-C to stop)."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    args = _base_args(lines, _normalize_priority(priority), unit, None, None, follow=True)
    proc = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        if proc.poll() is not None:
            detail = (proc.stderr.read() if proc.stderr else "").strip()
            err(console, detail or "journalctl failed to start")
            raise click.exceptions.Exit(1)
        for line in proc.stdout:
            for entry in parse_journal_json([line]):
                if ctx.obj.json:
                    console.file.write(json.dumps(entry) + "\n")
                    console.file.flush()
                else:
                    console.print(_format_entry(entry))
    except KeyboardInterrupt:
        raise click.exceptions.Abort() from None
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
