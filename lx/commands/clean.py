"""lx clean — cleanup & optimizer: cache, old kernels, logs, package caches."""
from __future__ import annotations

import shutil
import time as _t
from pathlib import Path

import click

from lx.utils.output import center_rule, err, ok, warn
from lx.utils.shell import is_root, run


def _dir_size(path: Path) -> int:
    try:
        if not path.exists() or not path.is_dir():
            return 0
    except (OSError, PermissionError):
        return 0
    total = 0
    try:
        for p in path.rglob("*"):
            try:
                total += p.stat().st_size
            except (OSError, PermissionError):
                continue
    except (OSError, PermissionError):
        pass
    return total


def _human(n: int) -> str:
    f = float(n)
    for u in ("B", "KB", "MB", "GB"):
        if f < 1024:
            return f"{f:.0f} {u}"
        f /= 1024
    return f"{f:.0f} TB"


def _check_root(console) -> bool:
    if is_root():
        return True
    err(console, "this destroys files; retry with: sudo lx clean ...")
    return False


@click.group("clean")
@click.pass_context
def clean(ctx: click.Context) -> None:
    """Free disk space: caches, old kernels, journal logs."""
    pass


@clean.command("cache")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation.")
@click.pass_context
def _clean_cache(ctx: click.Context, yes: bool) -> None:
    """Clear user and root caches (~/.cache, /var/cache)."""
    console = ctx.obj.console
    targets = [Path.home() / ".cache", Path("/var/cache")]
    center_rule(console, "Cache scan")
    totals = [(t, _dir_size(t)) for t in targets if t.exists()]
    for t, size in totals:
        console.print(f"  [bold]{t}[/bold]  {_human(size)}")
    total = sum(s for _, s in totals)
    if not total:
        ok(console, "cache is already empty")
        return
    if not yes and not click.confirm(f"Clear {_human(total)} of cache?", default=False):
        raise click.exceptions.Abort()
    for t, _ in totals:
        try:
            for child in t.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    try:
                        child.unlink()
                    except OSError:
                        continue
        except OSError as exc:
            warn(console, f"{t}: {exc}")
    ok(console, "cache cleared")


@clean.command("logs")
@click.option("--vacuum", "vacuum_size", default=100, show_default=True, type=int, help="Reduce journal to N MB")
@click.option("--yes", "-y", is_flag=True)
@click.pass_context
def _clean_logs(ctx: click.Context, vacuum_size: int, yes: bool) -> None:
    """Vacuum the systemd journal and trim old rotated log files."""
    console = ctx.obj.console
    if not _check_root(console):
        raise click.exceptions.Exit(1)
    center_rule(console, "Systemd journal cleanup")
    if not yes and not click.confirm(f"Vacuum journal to {vacuum_size} MB?", default=False):
        raise click.exceptions.Abort()
    res = run(["journalctl", "--vacuum-size", f"{vacuum_size}M"], sudo=True, timeout=120)
    if res.ok:
        ok(console, res.stdout.splitlines()[-1] if res.stdout else "journal vacuumed")
    else:
        err(console, res.stderr)
    freed = 0
    now = _t.time()
    for p in Path("/var/log").glob("**/*"):
        try:
            st = p.stat()
            if (now - st.st_mtime) > 30 * 86400 and p.name.endswith((".gz", ".1", ".old")):
                p.unlink()
                freed += st.st_size
        except OSError:
            continue
    if freed:
        ok(console, f"removed 30+-day-old rotated logs ({_human(freed)})")


@clean.command("kernels")
@click.option("--keep", default=2, show_default=True, type=int, help="Keep this many kernel versions.")
@click.option("--yes", "-y", is_flag=True)
@click.pass_context
def _clean_kernels(ctx: click.Context, keep: int, yes: bool) -> None:
    """Remove old kernel packages (Debian/Ubuntu: linux-image-*)."""
    console = ctx.obj.console
    if not _check_root(console):
        raise click.exceptions.Exit(1)
    current = run(["uname", "-r"]).stdout
    if not current:
        err(console, "could not get current kernel")
        raise click.exceptions.Exit(1)
    center_rule(console, "Installed kernel images")
    res = run(["dpkg-query", "-W", "-f=${Package} ${Version}\n", "linux-image-*"], timeout=30)
    if not res.ok or not res.stdout.strip():
        warn(console, "no dpkg-able kernels; this subcommand needs apt.")
        return
    pkgs = [ln.split() for ln in res.stdout.splitlines() if ln.strip() and not ln.startswith("dpkg")]
    pkgs = [p for p in pkgs if len(p) >= 2]
    removable = [name for name, _ver in pkgs if current not in name]
    if keep < 1:
        keep = 1
    removable = sorted(removable)
    to_remove = removable[:-keep] if len(removable) > keep else []
    for name in removable:
        marker = "[red]remove[/red]" if name in to_remove else "[green]keep[/green]"
        console.print(f"  {marker}  {name}")
    if not to_remove:
        ok(console, "nothing to remove; current + old kept within limit")
        return
    if not yes and not click.confirm(f"Remove {len(to_remove)} old kernel package(s)?", default=False):
        raise click.exceptions.Abort()
    res = run(["apt-get", "-y", "purge", *to_remove], sudo=True, timeout=600)
    if res.ok:
        ok(console, f"removed {len(to_remove)} kernel package(s)")
    else:
        err(console, res.stderr or res.stdout)


@clean.command("report")
@click.pass_context
def _clean_report(ctx: click.Context) -> None:
    """Report how much space various folders take, no changes."""
    console = ctx.obj.console
    center_rule(console, "Space report")
    candidates = [
        ("~/.cache", Path.home() / ".cache"),
        ("/var/cache", Path("/var/cache")),
        ("/var/log", Path("/var/log")),
        ("/tmp", Path("/tmp")),
        ("/var/lib/snapd/cache", Path("/var/lib/snapd/cache")),
        ("/root/.cache", Path("/root/.cache")),
    ]
    rows = []
    for label, p in candidates:
        try:
            if not p.exists():
                continue
            rows.append((label, _dir_size(p)))
        except (OSError, PermissionError):
            console.print(f"  [dim]{label}: permission denied (skipped)[/dim]")
    rows.sort(key=lambda kv: -kv[1])
    max_size = max((s for _, s in rows), default=1) or 1
    for label, size in rows:
        bar = "█" * int(size / max_size * 25)
        console.print(f"[bold]{label:<22}[/bold] {_human(size):>10}  [cyan]{bar}[/cyan]")
