"""lx cron — crontab and systemd timer overview (read-only)."""

from __future__ import annotations

import getpass
from pathlib import Path

import click
from rich.table import Table

from lx.utils.flags import apply_flags, json_watch_options
from lx.utils.output import center_rule, emit, err, warn
from lx.utils.parse import parse_crontab, parse_timers
from lx.utils.shell import run

_CRON_D = "/etc/cron.d"


def _read_crontab_file(path: Path) -> dict:
    """Read and parse a single crontab file into a source entry."""
    try:
        text = path.read_text()
    except OSError as exc:
        return {
            "source": "system",
            "path": str(path),
            "readable": False,
            "error": str(exc),
            "entries": [],
        }
    return {
        "source": "system",
        "path": str(path),
        "readable": True,
        "entries": parse_crontab(text),
    }


def _read_user_crontab(user: str | None) -> dict:
    me = getpass.getuser()
    if user is not None and user != me:
        return {
            "source": f"user:{user}",
            "readable": False,
            "error": f"listing another user's crontab requires root: sudo lx cron list --user {user}",
            "entries": [],
        }
    args = ["crontab", "-l"] if user is None else ["crontab", "-l", "-u", user]
    res = run(args, timeout=10)
    if res.ok:
        return {
            "source": f"user:{user or me}",
            "readable": True,
            "entries": parse_crontab(res.stdout),
        }
    if res.returncode == 127:
        return {
            "source": f"user:{user or me}",
            "readable": False,
            "error": "crontab binary not found — install cron (cronie on Arch)",
            "entries": [],
        }
    if res.returncode == 1 and "no crontab" in (res.stderr or "").lower():
        return {
            "source": f"user:{user or me}",
            "readable": True,
            "note": "no crontab installed for this user",
            "entries": [],
        }
    return {
        "source": f"user:{user or me}",
        "readable": False,
        "error": (res.stderr or res.stdout or "crontab -l failed").strip(),
        "entries": [],
    }


def _collect_list(user: str | None) -> dict:
    sources = [_read_user_crontab(user)]
    sources.append(_read_crontab_file(Path("/etc/crontab")))
    cron_d = Path(_CRON_D)
    if cron_d.is_dir():
        for entry in sorted(cron_d.iterdir()):
            if entry.is_file():
                sources.append(_read_crontab_file(entry))
    entries = [
        {**entry, "source": source["source"]}
        for source in sources
        for entry in source["entries"]
    ]
    errors = [
        {"source": source["source"], "error": source.get("error")}
        for source in sources
        if not source["readable"]
    ]
    notes = [source["note"] for source in sources if source.get("note")]
    return {
        "sources": len(sources),
        "count": len(entries),
        "entries": entries,
        "errors": errors,
        "notes": notes,
    }


def _render_list(console, data: dict) -> None:
    center_rule(console, f"Cron jobs ({data['count']} across {data['sources']} sources)")
    for note in data["notes"]:
        warn(console, note)
    for item in data["errors"]:
        warn(console, f"{item['source']}: {item['error']}")
    if not data["entries"] and not data["errors"]:
        console.print("[dim]no cron jobs found[/dim]")
        return
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("SOURCE")
    t.add_column("SCHEDULE")
    t.add_column("COMMAND")
    for entry in data["entries"]:
        if "error" in entry:
            t.add_row(entry["source"], "[red]—[/red]", f"[red]{entry['error']}[/red]")
            continue
        schedule = (
            f"{entry['minute']} {entry['hour']} {entry['day']} {entry['month']} {entry['dow']}"
            if entry.get("minute") is not None
            else "at reboot"
        )
        t.add_row(entry["source"], schedule, entry["command"])
    console.print(t)
    console.print()


def _collect_timers() -> dict:
    res = run(
        ["systemctl", "list-timers", "--no-pager", "--no-legend", "--plain"], timeout=10
    )
    if not res.ok:
        return {
            "ok": False,
            "count": 0,
            "timers": [],
            "error": res.stderr
            or "systemctl unavailable — this host is not running systemd",
        }
    timers = parse_timers(res.stdout)
    return {"ok": True, "count": len(timers), "timers": timers, "error": None}


def _render_timers(console, data: dict) -> None:
    center_rule(console, "systemd timers")
    if not data["ok"]:
        err(console, data["error"])
        console.print()
        return
    if not data["timers"]:
        console.print("[dim]no timers scheduled[/dim]")
        return
    t = Table(show_header=True, header_style="bold cyan")
    for header in ("NEXT", "LEFT", "LAST", "PASSED", "UNIT", "ACTIVATES"):
        t.add_column(header)
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
    console.print()


@click.group("cron")
@click.pass_context
def cron(ctx: click.Context) -> None:
    """Overview of crontab jobs and systemd timers."""
    pass


@cron.command("list")
@click.option("--user", default=None, help="List another user's crontab (root required).")
@json_watch_options
@click.pass_context
def _cron_list(
    ctx: click.Context,
    user: str | None,
    json_mode: bool | None = None,
    watch_secs: float | None = None,
) -> None:
    """Show crontab entries (your user, /etc/crontab, and /etc/cron.d)."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = _collect_list(user)
    if emit(ctx, data, command="cron list"):
        return
    _render_list(console, data)


@cron.command("timers")
@json_watch_options
@click.pass_context
def _cron_timers(
    ctx: click.Context, json_mode: bool | None = None, watch_secs: float | None = None
) -> None:
    """Show scheduled systemd timers."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = _collect_timers()
    if emit(ctx, data, command="cron timers"):
        return
    _render_timers(console, data)
