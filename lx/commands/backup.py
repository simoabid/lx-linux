"""lx backup — dotfile & config backup/restore using a tarball archive."""

from __future__ import annotations

import datetime as _dt
import fnmatch
import tarfile
from pathlib import Path

import click

from lx.utils.flags import apply_flags, json_option, json_watch_options
from lx.utils.output import center_rule, emit, err, ok, warn
from lx.utils.prompt import confirm_destructive

DEFAULT_TARGETS = [
    "~/.bashrc",
    "~/.zshrc",
    "~/.profile",
    "~/.bash_profile",
    "~/.gitconfig",
    "~/.tmux.conf",
    "~/.vimrc",
    "~/.config/nvim",
    "~/.config/starship.toml",
    "~/.config/alacritty",
    "~/.config/kitty",
    "~/.config/foot",
    "~/.config/sway",
    "~/.config/i3",
    "~/.config/rofi",
    "~/.config/waybar",
    "~/.config/hypr",
    "~/.ssh/config",
    "/etc/ssh/sshd_config",
    "/etc/fstab",
    "/etc/hosts",
    "/etc/hostname",
    "/etc/resolv.conf",
    "/etc/default/grub",
    "/etc/sysctl.conf",
]


def _archive_name(dest: Path) -> Path:
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return dest / f"lx-backup-{stamp}.tar.gz"


def _expand(p: str) -> Path:
    return Path(p).expanduser()


@click.group("backup")
@click.pass_context
def backup(ctx: click.Context) -> None:
    """Back up and restore dotfiles & key system configs."""
    pass


def _collect_create(
    dest: str, targets: tuple[str, ...], excludes: tuple[str, ...] = (), no_etc: bool = False
) -> dict:
    dest_path = _expand(dest)
    dest_path.mkdir(parents=True, exist_ok=True)
    base = (
        list(DEFAULT_TARGETS)
        if not no_etc
        else [t for t in DEFAULT_TARGETS if not t.startswith("/etc/")]
    )
    all_targets = base + list(targets)
    expanded = [_expand(t) for t in all_targets]
    if excludes:

        def _excluded(p: Path) -> bool:
            return any(fnmatch.fnmatch(str(p), e) or fnmatch.fnmatch(p.name, e) for e in excludes)

        expanded = [p for p in expanded if not _excluded(p)]
    present = [p for p in expanded if p.exists()]
    missing = [str(p) for p in expanded if not p.exists()]
    return {
        "dest": str(dest_path),
        "targets": [str(p) for p in present],
        "missing": missing,
        "excluded": list(excludes),
        "present": len(present),
        "total": len(expanded),
    }


def _render_create_targets(console, data: dict) -> None:
    present = [Path(p) for p in data["targets"]]
    expanded = present + [Path(p) for p in data["missing"]]
    for p in expanded:
        mark = "[green]✓[/green]" if p.exists() else "[red]✗[/red]"
        console.print(f"  {mark} {p}")
    if data["missing"]:
        warn(console, f"{len(data['missing'])} target(s) not found (skipped)")


@backup.command("create")
@click.option(
    "-d", "--dest", default="~/lx-backups", show_default=True, help="Destination directory."
)
@click.option(
    "-t", "--target", "targets", multiple=True, help="Extra file/dir to include (repeatable)."
)
@click.option(
    "--exclude", "excludes", multiple=True, help="Exclude target(s) by glob (repeatable)."
)
@click.option("--no-etc", is_flag=True, help="Skip /etc targets (dotfiles only).")
@click.option(
    "--list-only", is_flag=True, help="Just print what would be backed up, don't archive."
)
@json_option
@click.pass_context
def _backup_create(
    ctx: click.Context,
    dest: str,
    targets: tuple[str, ...],
    excludes: tuple[str, ...],
    no_etc: bool,
    list_only: bool,
    json_mode: bool | None = None,
) -> None:
    """Create a tar.gz of common dotfiles & configs."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    data = _collect_create(dest, targets, excludes, no_etc)
    center_rule(console, f"Backup targets ({data['present']}/{data['total']} present)")
    _render_create_targets(console, data)
    if data["excluded"]:
        console.print(f"[dim]excluded: {', '.join(data['excluded'])}[/dim]")
    if list_only:
        if emit(ctx, {**data, "list_only": True}, command="backup create"):
            return
        return
    if not data["present"]:
        err(console, "nothing to back up")
        raise click.exceptions.Exit(1)
    archive = _archive_name(Path(data["dest"]))
    with tarfile.open(archive, "w:gz") as tar:
        for p in data["targets"]:
            path = Path(p)
            arcname = str(path).replace(str(Path.home()), "home/USER").lstrip("/")
            tar.add(path, arcname=arcname)
    size = archive.stat().st_size
    if emit(ctx, {**data, "archive": str(archive), "bytes": size}, command="backup create"):
        return
    ok(console, f"created {archive}  ({size / 1024:.1f} KB)")


def _collect_list(dest: str) -> dict:
    dest_path = _expand(dest)
    if not dest_path.exists():
        return {"dest": str(dest_path), "exists": False, "archives": []}
    archives = sorted(dest_path.glob("lx-backup-*.tar.gz"))
    return {
        "dest": str(dest_path),
        "exists": True,
        "archives": [{"name": a.name, "bytes": a.stat().st_size} for a in archives],
    }


def _render_list(console, data: dict) -> None:
    if not data["exists"]:
        warn(console, f"{data['dest']} does not exist yet")
        return
    if not data["archives"]:
        console.print("[dim]no backups yet[/dim]")
        return
    center_rule(console, f"Backups in {data['dest']}")
    for a in data["archives"]:
        console.print(f"  [bold]{a['name']}[/bold]  [dim]({a['bytes'] / 1024:.1f} KB)[/dim]")


@backup.command("list")
@click.option("-d", "--dest", default="~/lx-backups", show_default=True)
@json_watch_options
@click.pass_context
def _backup_list(
    ctx: click.Context, dest: str, json_mode: bool | None = None, watch_secs: float | None = None
) -> None:
    """List existing backups in DEST."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = _collect_list(dest)
    if emit(ctx, data, command="backup list"):
        return
    _render_list(console, data)


def _collect_restore(archive: str, dry_run: bool) -> dict:
    path = Path(archive).expanduser()
    if not path.exists():
        return {
            "archive": str(path),
            "exists": False,
            "members": [],
            "dry_run": dry_run,
            "restored": False,
        }
    with tarfile.open(path, "r:gz") as tar:
        members = [m.name for m in tar.getmembers()]
    return {
        "archive": str(path),
        "exists": True,
        "members": members,
        "dry_run": dry_run,
        "restored": False,
    }


@backup.command("restore")
@click.argument("archive", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--dest",
    default="/",
    show_default=True,
    help="Extract to DIR instead of / (no root needed for a custom DIR).",
)
@click.option("--dry-run", is_flag=True, help="List contents without extracting.")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation.")
@json_option
@click.pass_context
def _backup_restore(
    ctx: click.Context,
    archive: str,
    dest: str,
    dry_run: bool,
    yes: bool,
    json_mode: bool | None = None,
) -> None:
    """Restore (extract) a backup archive. Root required for /etc paths."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    path = Path(archive).expanduser()
    dest_path = Path(dest).expanduser()
    if not path.exists():
        err(console, f"{path} not found")
        raise click.exceptions.Exit(1)
    if str(dest_path) != "/":
        dest_path.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "r:gz") as tar:
        members = tar.getmembers()
        if not ctx.obj.json:
            center_rule(console, f"Contents of {path.name} ({len(members)} entries) → {dest_path}")
            for m in members[:30]:
                console.print(f"  {m.name}")
            if len(members) > 30:
                console.print(f"[dim]…{len(members) - 30} more[/dim]")
        if dry_run:
            if emit(
                ctx,
                {
                    "archive": str(path),
                    "dest": str(dest_path),
                    "members": [m.name for m in members],
                    "dry_run": True,
                },
                command="backup restore",
            ):
                return
            return
        if not confirm_destructive(
            ctx,
            f"Extract these files to {dest_path} (overwriting existing)?",
            yes=yes,
            token="restore",
        ):
            raise click.exceptions.Abort()
        try:
            tar.extractall(path=str(dest_path), filter="tar")  # noqa: S202 — user-confirmed restore
        except OSError as exc:
            err(console, f"restore failed: {exc} (retry with sudo for /etc paths)")
            raise click.exceptions.Exit(1) from exc
    if emit(
        ctx,
        {"archive": str(path), "dest": str(dest_path), "restored": True},
        command="backup restore",
    ):
        return
    ok(console, "restored")


def _collect_verify(archive: str) -> dict:
    path = Path(archive).expanduser()
    if not path.exists():
        return {"archive": str(path), "exists": False, "ok": False, "error": "archive not found"}
    try:
        with tarfile.open(path, "r:gz") as tar:
            members = tar.getmembers()
            total_bytes = 0
            for m in members:
                f = tar.extractfile(m)
                if f is not None:
                    while f.read(65536):
                        total_bytes += 65536
                else:
                    total_bytes += m.size
            return {
                "archive": str(path),
                "exists": True,
                "ok": True,
                "members": len(members),
                "total_bytes": total_bytes,
                "error": None,
            }
    except (tarfile.TarError, OSError, EOFError) as exc:
        return {
            "archive": str(path),
            "exists": True,
            "ok": False,
            "members": 0,
            "total_bytes": 0,
            "error": str(exc),
        }


@backup.command("verify")
@click.argument("archive", type=click.Path(exists=True, dir_okay=False))
@json_option
@click.pass_context
def _backup_verify(ctx: click.Context, archive: str, json_mode: bool | None = None) -> None:
    """Check an archive's integrity (full gzip decompression, no extraction)."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    data = _collect_verify(archive)
    if emit(ctx, data, command="backup verify"):
        if not data["ok"]:
            raise click.exceptions.Exit(1)
        return
    if data["ok"]:
        ok(
            console,
            f"{Path(archive).name}: {data['members']} entries, {data['total_bytes'] / 1024:.1f} KB — OK",
        )
    else:
        err(console, f"corrupt or unreadable archive: {data['error']}")
        raise click.exceptions.Exit(1)


def _collect_prune(dest: str, keep: int) -> dict:
    dest_path = _expand(dest)
    if not dest_path.exists():
        return {
            "dest": str(dest_path),
            "exists": False,
            "kept": [],
            "removed": [],
            "candidates": [],
        }
    archives = sorted(dest_path.glob("lx-backup-*.tar.gz"))
    removed = archives[:-keep] if len(archives) > keep else []
    kept = [a for a in archives if a not in removed]
    return {
        "dest": str(dest_path),
        "exists": True,
        "kept": [{"name": a.name, "bytes": a.stat().st_size} for a in kept],
        "removed": [{"name": a.name, "bytes": a.stat().st_size} for a in removed],
        "candidates": [a.name for a in archives],
    }


@backup.command("prune")
@click.option("-d", "--dest", default="~/lx-backups", show_default=True)
@click.option(
    "--keep", default=5, show_default=True, type=int, help="Keep this many newest backups."
)
@click.option("--dry-run", is_flag=True, help="Only report what would be deleted.")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation.")
@json_option
@click.pass_context
def _backup_prune(
    ctx: click.Context,
    dest: str,
    keep: int,
    dry_run: bool,
    yes: bool,
    json_mode: bool | None = None,
) -> None:
    """Delete old backups, keeping the N newest."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    if keep < 1:
        err(console, "--keep must be >= 1")
        raise click.exceptions.Exit(1)
    data = _collect_prune(dest, keep)
    if not data["exists"] or not data["removed"]:
        if emit(ctx, {**data, "dry_run": dry_run}, command="backup prune"):
            return
        ok(console, "nothing to prune")
        return
    center_rule(console, f"Would delete {len(data['removed'])} old backup(s) in {data['dest']}")
    for a in data["removed"]:
        console.print(f"  [red]✗[/red] {a['name']}  [dim]({a['bytes'] / 1024:.1f} KB)[/dim]")
    if dry_run:
        if emit(ctx, {**data, "dry_run": True}, command="backup prune"):
            return
        return
    if not confirm_destructive(ctx, f"Delete {len(data['removed'])} old backup(s)?", yes=yes):
        raise click.exceptions.Abort()
    freed = 0
    for a in data["removed"]:
        try:
            (_expand(dest) / a["name"]).unlink()
            freed += a["bytes"]
        except OSError as exc:
            warn(console, f"{a['name']}: {exc}")
    if emit(ctx, {**data, "dry_run": False, "freed": freed}, command="backup prune"):
        return
    ok(console, f"removed {len(data['removed'])} backup(s), freed {freed / 1024:.1f} KB")
