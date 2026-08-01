"""lx clean — cleanup & optimizer: cache, old kernels, logs, package caches."""

from __future__ import annotations

import os
import shutil
import stat
import time as _t
from pathlib import Path

import click

from lx.utils.flags import apply_flags, json_option, json_watch_options
from lx.utils.output import center_rule, emit, err, ok, warn
from lx.utils.parse import parse_docker_df
from lx.utils.prompt import confirm_destructive
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


def _collect_report() -> dict:
    candidates = [
        ("~/.cache", Path.home() / ".cache"),
        ("/var/cache", Path("/var/cache")),
        ("/var/log", Path("/var/log")),
        ("/tmp", Path("/tmp")),
        ("/var/lib/snapd/cache", Path("/var/lib/snapd/cache")),
        ("/root/.cache", Path("/root/.cache")),
    ]
    targets = []
    for label, p in candidates:
        try:
            if not p.exists():
                continue
            targets.append({"label": label, "path": str(p), "bytes": _dir_size(p)})
        except (OSError, PermissionError):
            targets.append(
                {"label": label, "path": str(p), "bytes": 0, "skipped": "permission denied"}
            )
    targets.sort(key=lambda t: -t["bytes"])
    recommendations = []
    pip_cache = Path.home() / ".cache" / "pip"
    pip_size = _dir_size(pip_cache)
    if pip_size > 50 * 1024 * 1024:
        recommendations.append(f"~/.cache/pip is {_human(pip_size)} → run: lx clean pip")
    docker = _collect_docker_df()
    if docker["ok"] and docker["reclaimable"]:
        recommendations.append(
            f"docker can free {_human(docker['reclaimable'])} → run: lx clean docker"
        )
    snap_revisions = _collect_snap_disabled()
    if snap_revisions:
        recommendations.append(
            f"{len(snap_revisions)} disabled snap revision(s) → run: lx clean snap"
        )
    journal = _journal_size()
    if journal > 100 * 1024 * 1024:
        recommendations.append(f"journal is {_human(journal)} → run: lx clean logs --vacuum 100")
    return {
        "targets": targets,
        "total": sum(t["bytes"] for t in targets),
        "recommendations": recommendations,
    }


def _journal_size() -> int:
    res = run(["journalctl", "--disk-usage"], timeout=20)
    if not res.ok:
        return 0
    for line in res.stdout.splitlines():
        try:
            return int(float(line.split()[3].replace(",", "")) * 1024 * 1024)
        except (IndexError, ValueError):
            continue
    return 0


def _collect_docker_df() -> dict:
    if not shutil.which("docker"):
        return {"ok": False, "skipped": True, "reclaimable": 0, "rows": []}
    res = run(["docker", "system", "df"], timeout=60)
    if not res.ok:
        return {"ok": False, "skipped": True, "reclaimable": 0, "rows": []}
    rows = parse_docker_df(res.stdout)
    reclaimable = sum(r["reclaimable_bytes"] for r in rows if r["reclaimable_bytes"] is not None)
    return {"ok": True, "skipped": False, "reclaimable": reclaimable, "rows": rows}


def _collect_snap_disabled() -> list[dict]:
    if not shutil.which("snap"):
        return []
    res = run(["snap", "list", "--all"], timeout=30)
    if not res.ok:
        return []
    disabled = []
    for line in res.stdout.splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 4 and fields[3].strip() == "disabled":
            disabled.append({"name": fields[0], "rev": fields[1]})
    return disabled


def _render_report(console, data: dict) -> None:
    max_size = max((t["bytes"] for t in data["targets"]), default=1) or 1
    for t in data["targets"]:
        if t.get("skipped"):
            console.print(f"  [dim]{t['label']}: permission denied (skipped)[/dim]")
            continue
        bar = "█" * int(t["bytes"] / max_size * 25)
        console.print(f"[bold]{t['label']:<22}[/bold] {_human(t['bytes']):>10}  [cyan]{bar}[/cyan]")
    console.print(f"[bold]total[/bold]  {_human(data['total']):>10}")
    if data.get("recommendations"):
        center_rule(console, "Recommendations")
        for rec in data["recommendations"]:
            console.print(f"  [yellow]→[/yellow] {rec}")


@click.group("clean")
@click.pass_context
def clean(ctx: click.Context) -> None:
    """Free disk space: caches, old kernels, journal logs."""
    pass


@clean.command("cache")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation.")
@json_option
@click.pass_context
def _clean_cache(ctx: click.Context, yes: bool, json_mode: bool | None = None) -> None:
    """Clear user and root caches (~/.cache, /var/cache)."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    targets = [Path.home() / ".cache", Path("/var/cache")]
    center_rule(console, "Cache scan")
    totals = [{"path": str(t), "bytes": _dir_size(t)} for t in targets if t.exists()]
    for t in totals:
        console.print(f"  [bold]{t['path']}[/bold]  {_human(t['bytes'])}")
    total = sum(t["bytes"] for t in totals)
    if not total:
        if emit(ctx, {"ok": True, "targets": totals, "freed": 0}, command="clean cache"):
            return
        ok(console, "cache is already empty")
        return
    if not confirm_destructive(ctx, f"Clear {_human(total)} of cache?", yes=yes):
        raise click.exceptions.Abort()
    for t in totals:
        path = Path(t["path"])
        try:
            for child in path.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    try:
                        child.unlink()
                    except OSError:
                        continue
        except OSError as exc:
            warn(console, f"{path}: {exc}")
    if emit(ctx, {"ok": True, "targets": totals, "freed": total}, command="clean cache"):
        return
    ok(console, "cache cleared")


@clean.command("logs")
@click.option(
    "--vacuum",
    "vacuum_size",
    default=100,
    show_default=True,
    type=int,
    help="Reduce journal to N MB",
)
@click.option("--yes", "-y", is_flag=True)
@json_option
@click.pass_context
def _clean_logs(
    ctx: click.Context, vacuum_size: int, yes: bool, json_mode: bool | None = None
) -> None:
    """Vacuum the systemd journal and trim old rotated log files."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    if not _check_root(console):
        raise click.exceptions.Exit(1)
    center_rule(console, "Systemd journal cleanup")
    if not confirm_destructive(ctx, f"Vacuum journal to {vacuum_size} MB?", yes=yes):
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
    if emit(
        ctx,
        {
            "ok": res.ok,
            "vacuum": {"ok": res.ok, "stdout": res.stdout, "stderr": res.stderr},
            "rotated_freed": freed,
        },
        command="clean logs",
    ):
        return
    if freed:
        ok(console, f"removed 30+-day-old rotated logs ({_human(freed)})")


@clean.command("kernels")
@click.option(
    "--keep", default=2, show_default=True, type=int, help="Keep this many kernel versions."
)
@click.option("--yes", "-y", is_flag=True)
@json_option
@click.pass_context
def _clean_kernels(ctx: click.Context, keep: int, yes: bool, json_mode: bool | None = None) -> None:
    """Remove old kernel packages (Debian/Ubuntu: linux-image-*)."""
    apply_flags(ctx, json_mode)
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
    pkgs = [
        ln.split() for ln in res.stdout.splitlines() if ln.strip() and not ln.startswith("dpkg")
    ]
    pkgs = [p for p in pkgs if len(p) >= 2]
    removable = [name for name, _ver in pkgs if current not in name]
    if keep < 1:
        keep = 1
    removable = sorted(removable)
    to_remove = removable[:-keep] if len(removable) > keep else []
    kept = [name for name in removable if name not in to_remove]
    if not ctx.obj.json:
        for name in removable:
            marker = "[red]remove[/red]" if name in to_remove else "[green]keep[/green]"
            console.print(f"  {marker}  {name}")
    if not to_remove:
        if emit(ctx, {"ok": True, "removed": [], "kept": kept}, command="clean kernels"):
            return
        ok(console, "nothing to remove; current + old kept within limit")
        return
    if not confirm_destructive(
        ctx,
        f"Remove {len(to_remove)} old kernel package(s)?",
        yes=yes,
        token="purge",
    ):
        raise click.exceptions.Abort()
    res = run(["apt-get", "-y", "purge", *to_remove], sudo=True, timeout=600)
    if emit(ctx, {"ok": res.ok, "removed": to_remove, "kept": kept}, command="clean kernels"):
        if not res.ok:
            raise click.exceptions.Exit(1)
        return
    if res.ok:
        ok(console, f"removed {len(to_remove)} kernel package(s)")
    else:
        err(console, res.stderr or res.stdout)


@clean.command("report")
@json_watch_options
@click.pass_context
def _clean_report(
    ctx: click.Context, json_mode: bool | None = None, watch_secs: float | None = None
) -> None:
    """Report how much space various folders take, no changes."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    center_rule(console, "Space report")
    data = _collect_report()
    if emit(ctx, data, command="clean report"):
        return
    _render_report(console, data)


@clean.command("pip")
@click.option("--dry-run", is_flag=True, help="Only report size, make no changes.")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation.")
@json_option
@click.pass_context
def _clean_pip(ctx: click.Context, dry_run: bool, yes: bool, json_mode: bool | None = None) -> None:
    """Clear the pip wheel cache (~/.cache/pip)."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    pip_cache = Path.home() / ".cache" / "pip"
    size = _dir_size(pip_cache)
    if not size:
        if emit(
            ctx,
            {"ok": True, "path": str(pip_cache), "bytes": 0, "freed": 0, "dry_run": dry_run},
            command="clean pip",
        ):
            return
        ok(console, "pip cache is already empty")
        return
    if dry_run:
        if emit(
            ctx,
            {"ok": True, "path": str(pip_cache), "bytes": size, "freed": 0, "dry_run": True},
            command="clean pip",
        ):
            return
        ok(console, f"pip cache would free {_human(size)}")
        return
    if not confirm_destructive(ctx, f"Delete {_human(size)} pip cache at {pip_cache}?", yes=yes):
        raise click.exceptions.Abort()
    try:
        shutil.rmtree(pip_cache, ignore_errors=True)
    except OSError as exc:
        err(console, str(exc))
        raise click.exceptions.Exit(1) from exc
    if emit(
        ctx,
        {"ok": True, "path": str(pip_cache), "bytes": size, "freed": size, "dry_run": False},
        command="clean pip",
    ):
        return
    ok(console, f"freed {_human(size)}")


@clean.command("docker")
@click.option("--dry-run", is_flag=True, help="Only report reclaimable space, make no changes.")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation.")
@json_option
@click.pass_context
def _clean_docker(
    ctx: click.Context, dry_run: bool, yes: bool, json_mode: bool | None = None
) -> None:
    """Report and prune docker reclaimable space (docker system prune -f)."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    data = _collect_docker_df()
    if data["skipped"]:
        if emit(
            ctx,
            {"ok": False, "skipped": True, "error": "docker CLI not available"},
            command="clean docker",
        ):
            return
        warn(console, "docker CLI not available — nothing to do")
        return
    if dry_run:
        if emit(
            ctx,
            {"ok": True, "dry_run": True, "reclaimable": data["reclaimable"], "rows": data["rows"]},
            command="clean docker",
        ):
            return
        console.print(f"[bold]docker reclaimable:[/bold] {_human(data['reclaimable'])}")
        for row in data["rows"]:
            console.print(
                f"  {row['type']:<12} {_human(row['size_bytes']):>10}  reclaimable: {_human(row['reclaimable_bytes'])}"
            )
        return
    if not confirm_destructive(ctx, "Run docker system prune -f?", yes=yes):
        raise click.exceptions.Abort()
    res = run(["docker", "system", "prune", "-f"], sudo=True, timeout=300)
    if emit(
        ctx,
        {
            "ok": res.ok,
            "reclaimable": data["reclaimable"],
            "stdout": res.stdout,
            "stderr": res.stderr,
        },
        command="clean docker",
    ):
        if not res.ok:
            raise click.exceptions.Exit(1)
        return
    if res.ok:
        ok(console, "docker pruned")
    else:
        err(console, res.stderr or res.stdout)
        raise click.exceptions.Exit(1)


@clean.command("snap")
@click.option("--dry-run", is_flag=True, help="Only list disabled revisions, make no changes.")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation.")
@json_option
@click.pass_context
def _clean_snap(
    ctx: click.Context, dry_run: bool, yes: bool, json_mode: bool | None = None
) -> None:
    """Remove disabled snap revisions."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    disabled = _collect_snap_disabled()
    if not disabled:
        if emit(ctx, {"ok": True, "removed": [], "disabled": []}, command="clean snap"):
            return
        ok(console, "no disabled snap revisions")
        return
    if dry_run:
        if emit(
            ctx,
            {"ok": True, "dry_run": True, "disabled": disabled, "removed": []},
            command="clean snap",
        ):
            return
        for d in disabled:
            console.print(f"  {d['name']} ({d['rev']})")
        warn(console, f"would remove {len(disabled)} disabled revision(s)")
        return
    if not confirm_destructive(ctx, f"Remove {len(disabled)} disabled snap revision(s)?", yes=yes):
        raise click.exceptions.Abort()
    removed = []
    failed = 0
    for d in disabled:
        res = run(["snap", "remove", d["name"], "--revision", d["rev"]], timeout=120)
        if res.ok:
            removed.append({"name": d["name"], "rev": d["rev"], "ok": True})
        else:
            removed.append({"name": d["name"], "rev": d["rev"], "ok": False, "error": res.stderr})
            failed += 1
    if emit(
        ctx, {"ok": failed == 0, "disabled": disabled, "removed": removed}, command="clean snap"
    ):
        if failed:
            raise click.exceptions.Exit(1)
        return
    ok(console, f"removed {len(removed) - failed} disabled revision(s)")
    if failed:
        err(console, f"{failed} removal(s) failed (try sudo lx clean snap)")


def _collect_tmp_candidates(days: int, tmp_dir: Path = Path("/tmp")) -> tuple[int, list[dict], int]:
    """Return (entries_scanned, stale_candidates, total_bytes) for /tmp."""
    cutoff = _t.time() - days * 86400
    uid = os.getuid()
    try:
        entries = list(tmp_dir.iterdir())
    except OSError:
        return 0, [], 0
    candidates = []
    total = 0
    for p in entries:
        try:
            st = p.lstat()
        except OSError:
            continue
        if st.st_uid != uid:
            continue
        if st.st_mtime > cutoff:
            continue
        size = st.st_size if stat.S_ISREG(st.st_mode) else _dir_size(p)
        total += size
        candidates.append({"path": str(p), "bytes": size})
    return len(entries), candidates, total


@clean.command("tmp")
@click.option(
    "--days", default=10, show_default=True, type=int, help="Delete /tmp entries older than N days."
)
@click.option("--dry-run", is_flag=True, help="Only list candidates, make no changes.")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation.")
@json_option
@click.pass_context
def _clean_tmp(
    ctx: click.Context, days: int, dry_run: bool, yes: bool, json_mode: bool | None = None
) -> None:
    """Remove stale /tmp entries older than N days owned by the current user."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    entries, candidates, total = _collect_tmp_candidates(days)
    if not candidates:
        if emit(
            ctx,
            {"ok": True, "scanned": len(entries), "candidates": [], "removed": [], "freed": 0},
            command="clean tmp",
        ):
            return
        ok(console, f"no /tmp entries older than {days} days owned by you")
        return
    if dry_run:
        if emit(
            ctx,
            {
                "ok": True,
                "dry_run": True,
                "scanned": len(entries),
                "candidates": candidates,
                "removed": [],
                "freed": 0,
            },
            command="clean tmp",
        ):
            return
        center_rule(console, f"Stale /tmp entries ({len(candidates)})")
        for c in candidates[:25]:
            console.print(f"  {c['path']}  [dim]{_human(c['bytes'])}[/dim]")
        if len(candidates) > 25:
            console.print(f"[dim]…{len(candidates) - 25} more[/dim]")
        return
    if not confirm_destructive(
        ctx, f"Delete {len(candidates)} stale /tmp entrie(s) ({_human(total)})?", yes=yes
    ):
        raise click.exceptions.Abort()
    freed = 0
    removed = []
    for c in candidates:
        p = Path(c["path"])
        try:
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p, ignore_errors=True)
                if not p.exists():
                    freed += c["bytes"]
                    removed.append(c["path"])
            else:
                p.unlink(missing_ok=True)
                freed += c["bytes"]
                removed.append(c["path"])
        except OSError:
            continue
    if emit(
        ctx,
        {
            "ok": True,
            "scanned": len(entries),
            "candidates": candidates,
            "removed": removed,
            "freed": freed,
        },
        command="clean tmp",
    ):
        return
    ok(console, f"removed {len(removed)} entrie(s), freed {_human(freed)}")
