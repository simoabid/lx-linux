"""lx fs — disk explorer: directory usage, largest files, inodes, mounts."""

from __future__ import annotations

import heapq
import os
from pathlib import Path
from typing import Any

import click
import psutil
from rich.table import Table

from lx.utils.flags import apply_flags, json_watch_options
from lx.utils.output import center_rule, emit, err, gauge, human_bytes, kv_table

#: Directories never walked (pseudo filesystems and known volatile trees).
_SKIP_DIRS = {"proc", "sys", "dev", "run", "snap", "lost+found"}

_ERR_CAP = 20


def _walk(path: Path, depth: int, on_error=None) -> Any:
    """Yield (dir_path, dirnames, filenames) tuples, bounded by depth.

    Never follows symlinks and records permission errors via ``on_error``
    instead of raising, so partial results always come back.
    """
    stack = [(path, 0)]
    while stack:
        current, level = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError as exc:
            if on_error is not None:
                on_error(current, exc)
            continue
        dirs: list[str] = []
        files: list[str] = []
        for entry in entries:
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                if entry.name not in _SKIP_DIRS:
                    dirs.append(entry.name)
            else:
                files.append(entry.name)
        yield current, dirs, files
        if level < depth:
            for name in reversed(dirs):
                stack.append((current / name, level + 1))


def _dir_size(path: Path, depth: int, on_error) -> int:
    total = 0
    for root, _dirs, files in _walk(path, depth, on_error):
        for name in files:
            try:
                total += os.lstat(root / name).st_size
            except OSError:
                continue
    return total


def _collect_usage(path: str, top_n: int, depth: int) -> dict:
    root = Path(path).expanduser()
    if not root.is_dir():
        return {"path": str(root), "error": "not a directory", "top": [], "total_bytes": 0}
    errors: list[str] = []
    rows: list[dict] = []
    try:
        entries = list(os.scandir(root))
    except OSError as exc:
        return {"path": str(root), "error": str(exc), "top": [], "total_bytes": 0}
    for entry in entries:
        try:
            if entry.is_dir(follow_symlinks=False):
                if entry.name in _SKIP_DIRS:
                    continue
                size = _dir_size(root / entry.name, depth, lambda p, e: errors.append(str(e)))
                rows.append({"path": entry.path, "bytes": size, "kind": "dir"})
            else:
                rows.append(
                    {
                        "path": entry.path,
                        "bytes": os.lstat(entry.path).st_size,
                        "kind": "file",
                    }
                )
        except OSError as exc:
            errors.append(str(exc))
    rows.sort(key=lambda r: -r["bytes"])
    return {
        "path": str(root),
        "total_bytes": sum(r["bytes"] for r in rows),
        "top": rows[:top_n],
        "errors": errors[:_ERR_CAP],
    }


def _render_usage(console, data: dict) -> None:
    center_rule(console, f"Directory usage — {data['path']}")
    if data.get("error"):
        err(console, data["error"])
        return
    if not data["top"]:
        console.print("[dim]empty directory[/dim]")
        return
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("PATH")
    t.add_column("SIZE", justify="right")
    t.add_column("BAR", width=24)
    largest = data["top"][0]["bytes"] or 1
    for row in data["top"]:
        pct = row["bytes"] / largest
        bar = "█" * int(24 * pct) + "░" * (24 - int(24 * pct))
        color = "green" if pct < 0.5 else ("yellow" if pct < 0.85 else "red")
        t.add_row(row["path"], human_bytes(row["bytes"]), f"[{color}]{bar}[/{color}]")
    console.print(t)
    console.print(f"[dim]total {human_bytes(data['total_bytes'])}"
                  + (f" · {len(data['errors'])} error(s) skipped[/dim]" if data["errors"] else "[/dim]"))
    console.print()


def _collect_large(path: str, top_n: int, min_bytes: int) -> dict:
    root = Path(path).expanduser()
    if not root.is_dir():
        return {"path": str(root), "error": "not a directory", "top": [], "total_bytes": 0}
    errors: list[str] = []
    heap: list[tuple[int, str]] = []

    def on_error(_p, exc) -> None:
        errors.append(str(exc))

    for base, _dirs, files in _walk(root, 2**31 - 1, on_error):
        for name in files:
            try:
                size = os.lstat(base / name).st_size
            except OSError:
                continue
            if size < min_bytes:
                continue
            item = (size, str(base / name))
            if len(heap) < top_n:
                heapq.heappush(heap, item)
            elif size > heap[0][0]:
                heapq.heapreplace(heap, item)
    largest = sorted(heap, reverse=True)
    return {
        "path": str(root),
        "top": [{"path": p, "bytes": s} for s, p in largest],
        "total_bytes": sum(s for s, _ in largest),
        "errors": errors[:_ERR_CAP],
    }


def _render_large(console, data: dict) -> None:
    center_rule(console, f"Largest files under {data['path']}")
    if data.get("error"):
        err(console, data["error"])
        return
    if not data["top"]:
        console.print("[dim]no files found (raise --min or widen the path)[/dim]")
        return
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("SIZE", justify="right")
    t.add_column("FILE")
    for row in data["top"]:
        t.add_row(human_bytes(row["bytes"]), row["path"])
    console.print(t)
    console.print("[dim]top entries only — run with a bigger --top for more[/dim]\n")


def _collect_inodes(path: str, top_n: int, depth: int) -> dict:
    root = Path(path).expanduser()
    if not root.is_dir():
        return {"path": str(root), "error": "not a directory", "top": [], "inodes": {}}
    try:
        vfs = os.statvfs(root)
    except OSError as exc:
        return {"path": str(root), "error": str(exc), "top": [], "inodes": {}}
    total = vfs.f_files or 0
    free = vfs.f_ffree or 0
    used = max(0, total - free)
    inodes = {
        "total": total,
        "used": used,
        "free": free,
        "percent": round(used / total * 100, 1) if total else None,
    }
    errors: list[str] = []
    rows: list[dict] = []
    try:
        entries = list(os.scandir(root))
    except OSError as exc:
        return {"path": str(root), "error": str(exc), "top": [], "inodes": inodes}
    for entry in entries:
        try:
            if not entry.is_dir(follow_symlinks=False) or entry.name in _SKIP_DIRS:
                continue
        except OSError:
            continue
        count = 0
        for _base, _dirs, files in _walk(root / entry.name, depth, lambda p, e: errors.append(str(e))):
            count += len(files) + 1
        rows.append({"path": entry.path, "count": count})
    rows.sort(key=lambda r: -r["count"])
    return {
        "path": str(root),
        "inodes": inodes,
        "top": rows[:top_n],
        "errors": errors[:_ERR_CAP],
    }


def _render_inodes(console, data: dict) -> None:
    center_rule(console, f"Inodes — {data['path']}")
    if data.get("error"):
        err(console, data["error"])
        return
    ino = data["inodes"]
    if ino.get("total"):
        kv_table(
            console,
            [
                ("inodes", f"{ino['used']:,} used / {ino['total']:,} total"),
                ("free", f"{ino['free']:,}"),
                ("usage", f"[{ 'green' if (ino['percent'] or 0) < 80 else 'red'}]{ino['percent']}%[/]"),
            ],
        )
    else:
        console.print("[dim]inode accounting not available on this filesystem[/dim]")
    if data["top"]:
        t = Table(show_header=True, header_style="bold cyan")
        t.add_column("PATH")
        t.add_column("ENTRIES", justify="right")
        for row in data["top"]:
            t.add_row(row["path"], f"{row['count']:,}")
        console.print(t)
    console.print()


def _collect_mounts() -> dict:
    rows: list[dict] = []
    seen = set()
    skip_fs = {
        "squashfs",
        "tmpfs",
        "devtmpfs",
        "overlay",
        "fuse.snapfuse",
        "fuse.gvfsd-fuse",
        "proc",
        "sysfs",
        "cgroup2",
        "cgroup",
        "devpts",
        "securityfs",
        "pstore",
        "bpf",
        "autofs",
        "hugetlbfs",
        "mqueue",
        "debugfs",
        "tracefs",
        "fusectl",
        "configfs",
        "binfmt_misc",
    }
    try:
        partitions = psutil.disk_partitions(all=False)
    except Exception:  # noqa: BLE001
        return {"mounts": []}
    for p in partitions:
        if p.fstype in skip_fs or p.device.startswith("/dev/loop"):
            continue
        if p.mountpoint in seen:
            continue
        seen.add(p.mountpoint)
        try:
            u = psutil.disk_usage(p.mountpoint)
            vfs = os.statvfs(p.mountpoint)
        except (PermissionError, OSError):
            continue
        total_ino = vfs.f_files or 0
        free_ino = vfs.f_ffree or 0
        rows.append(
            {
                "mountpoint": p.mountpoint,
                "device": p.device,
                "fstype": p.fstype,
                "total": u.total,
                "used": u.used,
                "free": u.free,
                "percent": u.percent,
                "inodes": {
                    "total": total_ino,
                    "used": max(0, total_ino - free_ino),
                    "free": free_ino,
                    "percent": round((total_ino - free_ino) / total_ino * 100, 1) if total_ino else None,
                },
            }
        )
    return {"mounts": rows}


def _render_mounts(console, data: dict) -> None:
    center_rule(console, "Filesystems")
    if not data["mounts"]:
        console.print("[dim]no real filesystems detected[/dim]")
        return
    for mnt in data["mounts"]:
        gauge(console, mnt["mountpoint"], mnt["used"], mnt["total"], unit=" B")
        ino = mnt["inodes"]
        ino_str = f"{ino['percent']}% inodes" if ino["percent"] is not None else "inodes n/a"
        console.print(
            f"[dim]  {mnt['device']} · {mnt['fstype']} · {ino_str}[/dim]"
        )
    console.print()


@click.group("fs")
@click.pass_context
def fs(ctx: click.Context) -> None:
    """Disk explorer: directory usage, largest files, inodes, mounts."""
    pass


@fs.command("usage")
@click.argument("path", required=False, default=".")
@click.option("-n", "--top", default=10, show_default=True, type=int, help="Show top N entries.")
@click.option("-d", "--depth", default=3, show_default=True, type=int, help="Max recursion depth.")
@json_watch_options
@click.pass_context
def _fs_usage(
    ctx: click.Context,
    path: str,
    top: int,
    depth: int,
    json_mode: bool | None = None,
    watch_secs: float | None = None,
) -> None:
    """Show the largest directories (and files) under PATH."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    if ctx.obj.json:
        data = _collect_usage(path, top, depth)
    else:
        with console.status(f"Scanning {path}…", spinner="dots"):
            data = _collect_usage(path, top, depth)
    if emit(ctx, data, command="fs usage"):
        return
    _render_usage(console, data)


@fs.command("large")
@click.argument("path", required=False, default=".")
@click.option("-n", "--top", default=10, show_default=True, type=int, help="Show top N files.")
@click.option(
    "--min", "min_mb", default=100.0, show_default=True, type=float, help="Only files >= MIN MiB."
)
@json_watch_options
@click.pass_context
def _fs_large(
    ctx: click.Context,
    path: str,
    top: int,
    min_mb: float,
    json_mode: bool | None = None,
    watch_secs: float | None = None,
) -> None:
    """Find the largest files under PATH (minimum size --min MiB)."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    if ctx.obj.json:
        data = _collect_large(path, top, int(min_mb * 1024 * 1024))
    else:
        with console.status(f"Scanning {path}…", spinner="dots"):
            data = _collect_large(path, top, int(min_mb * 1024 * 1024))
    if emit(ctx, data, command="fs large"):
        return
    _render_large(console, data)


@fs.command("inodes")
@click.argument("path", required=False, default=".")
@click.option("-n", "--top", default=10, show_default=True, type=int, help="Show top N dirs.")
@click.option("-d", "--depth", default=3, show_default=True, type=int, help="Max recursion depth.")
@json_watch_options
@click.pass_context
def _fs_inodes(
    ctx: click.Context,
    path: str,
    top: int,
    depth: int,
    json_mode: bool | None = None,
    watch_secs: float | None = None,
) -> None:
    """Show filesystem inode usage and per-directory entry counts."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    if ctx.obj.json:
        data = _collect_inodes(path, top, depth)
    else:
        with console.status(f"Scanning {path}…", spinner="dots"):
            data = _collect_inodes(path, top, depth)
    if emit(ctx, data, command="fs inodes"):
        return
    _render_inodes(console, data)


@fs.command("mounts")
@json_watch_options
@click.pass_context
def _fs_mounts(
    ctx: click.Context, json_mode: bool | None = None, watch_secs: float | None = None
) -> None:
    """List real filesystems with usage and inode stats."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = _collect_mounts()
    if emit(ctx, data, command="fs mounts"):
        return
    _render_mounts(console, data)
