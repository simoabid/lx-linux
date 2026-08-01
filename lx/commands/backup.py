"""lx backup — dotfile & config backup/restore using a tarball archive."""
from __future__ import annotations

import datetime as _dt
import tarfile
from pathlib import Path

import click

from lx.utils.output import center_rule, err, ok, warn

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


@backup.command("create")
@click.option("-d", "--dest", default="~/lx-backups", show_default=True, help="Destination directory.")
@click.option("-t", "--target", "targets", multiple=True, help="Extra file/dir to include (repeatable).")
@click.option("--list-only", is_flag=True, help="Just print what would be backed up, don't archive.")
@click.pass_context
def _backup_create(ctx: click.Context, dest: str, targets: tuple[str, ...], list_only: bool) -> None:
    """Create a tar.gz of common dotfiles & configs."""
    console = ctx.obj.console
    dest_path = _expand(dest)
    dest_path.mkdir(parents=True, exist_ok=True)

    all_targets = list(DEFAULT_TARGETS) + list(targets)
    expanded = [_expand(t) for t in all_targets]
    present = [p for p in expanded if p.exists()]

    center_rule(console, f"Backup targets ({len(present)}/{len(expanded)} present)")
    for p in expanded:
        mark = "[green]✓[/green]" if p.exists() else "[red]✗[/red]"
        console.print(f"  {mark} {p}")

    missing = [p for p in expanded if not p.exists()]
    if missing:
        warn(console, f"{len(missing)} target(s) not found (skipped)")

    if list_only:
        return
    if not present:
        err(console, "nothing to back up")
        raise click.exceptions.Exit(1)

    archive = _archive_name(dest_path)
    with tarfile.open(archive, "w:gz") as tar:
        for p in present:
            arcname = str(p).replace(str(Path.home()), "home/USER").lstrip("/")
            if p.is_dir():
                tar.add(p, arcname=arcname)
            else:
                tar.add(p, arcname=arcname)
    size = archive.stat().st_size
    ok(console, f"created {archive}  ({size / 1024:.1f} KB)")


@backup.command("list")
@click.option("-d", "--dest", default="~/lx-backups", show_default=True)
@click.pass_context
def _backup_list(ctx: click.Context, dest: str) -> None:
    """List existing backups in DEST."""
    console = ctx.obj.console
    dest_path = _expand(dest)
    if not dest_path.exists():
        warn(console, f"{dest_path} does not exist yet")
        return
    archives = sorted(dest_path.glob("lx-backup-*.tar.gz"))
    if not archives:
        console.print("[dim]no backups yet[/dim]")
        return
    center_rule(console, f"Backups in {dest_path}")
    for a in archives:
        size = a.stat().st_size
        console.print(f"  [bold]{a.name}[/bold]  [dim]({size / 1024:.1f} KB)[/dim]")


@backup.command("restore")
@click.argument("archive", type=click.Path(exists=True, dir_okay=False))
@click.option("--dry-run", is_flag=True, help="List contents without extracting.")
@click.pass_context
def _backup_restore(ctx: click.Context, archive: str, dry_run: bool) -> None:
    """Restore (extract) a backup archive. Root required for /etc paths."""
    console = ctx.obj.console
    path = Path(archive).expanduser()
    if not path.exists():
        err(console, f"{path} not found")
        raise click.exceptions.Exit(1)
    with tarfile.open(path, "r:gz") as tar:
        members = tar.getmembers()
        center_rule(console, f"Contents of {path.name} ({len(members)} entries)")
        for m in members[:30]:
            console.print(f"  {m.name}")
        if len(members) > 30:
            console.print(f"[dim]…{len(members) - 30} more[/dim]")
        if dry_run:
            return
        if not click.confirm("Extract these files (overwriting existing)?", default=False):
            raise click.exceptions.Abort()
        try:
            tar.extractall(path="/", filter="tar")  # noqa: S202 — user-confirmed restore
        except OSError as exc:
            err(console, f"restore failed: {exc} (retry with sudo for /etc paths)")
            raise click.exceptions.Exit(1) from exc
    ok(console, "restored")
