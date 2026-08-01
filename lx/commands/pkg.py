"""lx pkg — unified package manager wrapper for apt/dnf/pacman + flatpak."""
from __future__ import annotations

import shutil

import click
from rich.table import Table

from lx.utils.output import center_rule, err, ok
from lx.utils.shell import is_root, run

BACKENDS = ("apt", "dnf", "pacman", "zypper", "apk", "flatpak", "snap")


def _require_root(console) -> bool:
    if is_root():
        return True
    err(console, "subcommand requires root. Retry with: sudo lx pkg ...")
    return False


def _detect_backend() -> tuple[str | None, list[str]]:
    """Return (primary_backend, [secondary]) — first match wins."""
    avail = []
    for be in BACKENDS:
        if shutil.which(be):
            avail.append(be)
    primary = avail[0] if avail else None
    return primary, avail


def _commands(backend: str) -> dict[str, str]:
    """Map a generic action to a backend-specific command string."""
    table = {
        "apt": {
            "update": "apt update",
            "upgrade": "apt upgrade -y",
            "install": "apt install -y",
            "remove": "apt remove -y",
            "search": "apt search",
            "list": "apt list --installed",
            "clean": "apt clean",
            "info": "apt show",
        },
        "dnf": {
            "update": "dnf check-update",
            "upgrade": "dnf upgrade -y",
            "install": "dnf install -y",
            "remove": "dnf remove -y",
            "search": "dnf search",
            "list": "dnf list --installed",
            "clean": "dnf clean all",
            "info": "dnf info",
        },
        "pacman": {
            "update": "pacman -Syy",
            "upgrade": "pacman -Syu --noconfirm",
            "install": "pacman -S --noconfirm",
            "remove": "pacman -Rns --noconfirm",
            "search": "pacman -Ss",
            "list": "pacman -Q",
            "clean": "pacman -Sc --noconfirm",
            "info": "pacman -Si",
        },
        "zypper": {
            "update": "zypper refresh",
            "upgrade": "zypper update -y",
            "install": "zypper install -y",
            "remove": "zypper remove -y",
            "search": "zypper search",
            "list": "zypper search --installed-only",
            "clean": "zypper clean",
            "info": "zypper info",
        },
        "apk": {
            "update": "apk update",
            "upgrade": "apk upgrade",
            "install": "apk add",
            "remove": "apk del",
            "search": "apk search",
            "list": "apk list --installed",
            "clean": "apk cache clean",
            "info": "apk info",
        },
        "flatpak": {
            "update": "flatpak update --appstream",
            "upgrade": "flatpak update -y",
            "install": "flatpak install -y",
            "remove": "flatpak uninstall -y",
            "search": "flatpak search",
            "list": "flatpak list",
            "clean": "flatpak uninstall --unused -y",
            "info": "flatpak info",
        },
        "snap": {
            "update": "snap refresh",
            "upgrade": "snap refresh",
            "install": "snap install",
            "remove": "snap remove",
            "search": "snap find",
            "list": "snap list",
            "clean": "echo 'no purge'",
            "info": "snap info",
        },
    }
    return table.get(backend, {})


def _count_installed(backend: str) -> int:
    """Return how many packages are installed for a given backend, or -1."""
    cmds = _commands(backend).get("list")
    if not cmds:
        return -1
    res = run(["sh", "-c", cmds], timeout=60)
    if not res.ok or not res.stdout:
        return -1
    return sum(1 for ln in res.stdout.splitlines() if ln.strip())


def _require_admin(backend: str, action: str) -> bool:
    """Whether an action would need root (apt/dnf/pacman do)."""
    return backend in {"apt", "dnf", "pacman", "zypper", "apk"} and action in {
        "update", "upgrade", "install", "remove", "clean"
    }


@click.group("pkg")
@click.option("-b", "--backend", type=click.Choice(BACKENDS), default=None, help="Force a backend.")
@click.pass_context
def pkg(ctx: click.Context, backend: str | None) -> None:
    """Unified wrapper around apt/dnf/pacman/zypper/apk/flatpak/snap.

    Auto-detects your package manager. Override with `--backend`.
    """
    ctx.obj.data["backend"] = backend or _detect_backend()[0]


@pkg.command("status")
@click.pass_context
def _pkg_status(ctx: click.Context) -> None:
    """Show which package managers are installed and counts."""
    console = ctx.obj.console
    primary, avail = _detect_backend()
    center_rule(console, "Package managers detected")
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("Backend")
    t.add_column("Installed?", justify="center")
    for be in BACKENDS:
        here = be in avail
        marker = "[green]✓[/green] primary" if here and be == primary else (
            "[green]✓[/green]" if here else "[red]✗[/red]"
        )
        t.add_row(be, marker)
    console.print(t)
    if primary:
        n = _count_installed(primary)
        console.print(f"\n[dim]Primary backend:[/dim] [bold]{primary}[/bold] "
                      f"[dim](installed pkgs: {int(n) if n >=0 else '?'})[/dim]")


@pkg.command("search")
@click.argument("query")
@click.pass_context
def _pkg_search(ctx: click.Context, query: str) -> None:
    """Search QUERY in your package manager."""
    console = ctx.obj.console
    be = ctx.obj.data["backend"]
    if not be:
        err(console, "no supported package manager detected")
        raise click.exceptions.Exit(1)
    cmd = _commands(be)["search"] + " " + query
    console.print(f"[dim]$ {cmd}[/dim]")
    res = run(["sh", "-c", cmd], timeout=60)
    console.print(res.stdout or res.stderr or "[dim]nothing found[/dim]")


@pkg.command("update")
@click.pass_context
def _pkg_update(ctx: click.Context) -> None:
    """Update package index (refresh)."""
    console = ctx.obj.console
    be = ctx.obj.data["backend"]
    if not be:
        err(console, "no package manager detected")
        raise click.exceptions.Exit(1)
    if not _require_root(console):
        raise click.exceptions.Exit(1)
    res = run(["sh", "-c", _commands(be)["update"]], sudo=_require_admin(be, "update"), timeout=120)
    if res.ok:
        ok(console, "index refreshed")
    else:
        err(console, res.stderr or res.stdout)
        raise click.exceptions.Exit(1)


@pkg.command("upgrade")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation.")
@click.pass_context
def _pkg_upgrade(ctx: click.Context, yes: bool) -> None:
    """Upgrade all packages."""
    console = ctx.obj.console
    be = ctx.obj.data["backend"]
    if not be:
        err(console, "no package manager detected")
        raise click.exceptions.Exit(1)
    if not _require_root(console):
        raise click.exceptions.Exit(1)
    if not yes and not click.confirm(f"Upgrade all packages with {be}?", default=False):
        raise click.exceptions.Abort()
    res = run(["sh", "-c", _commands(be)["upgrade"]], sudo=_require_admin(be, "upgrade"), timeout=600)
    if res.ok:
        ok(console, "upgrade complete")
    else:
        err(console, res.stderr or res.stdout)
        raise click.exceptions.Exit(1)


@pkg.command("install")
@click.argument("name")
@click.pass_context
def _pkg_install(ctx: click.Context, name: str) -> None:
    """Install one or more packages (comma-separated)."""
    console = ctx.obj.console
    be = ctx.obj.data["backend"]
    if not be:
        err(console, "no package manager detected")
        raise click.exceptions.Exit(1)
    if not _require_root(console):
        raise click.exceptions.Exit(1)
    pkgs = " ".join(p.strip() for p in name.split(",")) if "," in name else name
    cmd = f"{_commands(be)['install']} {pkgs}"
    res = run(["sh", "-c", cmd], sudo=_require_admin(be, "install"), timeout=300)
    if res.ok:
        ok(console, f"installed {name}")
    else:
        err(console, res.stderr or res.stdout)
        raise click.exceptions.Exit(1)


@pkg.command("remove")
@click.argument("name")
@click.pass_context
def _pkg_remove(ctx: click.Context, name: str) -> None:
    """Remove a package."""
    console = ctx.obj.console
    be = ctx.obj.data["backend"]
    if not be:
        err(console, "no package manager detected")
        raise click.exceptions.Exit(1)
    if not _require_root(console):
        raise click.exceptions.Exit(1)
    cmd = f"{_commands(be)['remove']} {name}"
    res = run(["sh", "-c", cmd], sudo=_require_admin(be, "remove"), timeout=300)
    if res.ok:
        ok(console, f"removed {name}")
    else:
        err(console, res.stderr or res.stdout)
        raise click.exceptions.Exit(1)


@pkg.command("purge")
@click.pass_context
def _pkg_purge(ctx: click.Context) -> None:
    """Clean local package caches."""
    console = ctx.obj.console
    be = ctx.obj.data["backend"]
    if not be:
        err(console, "no package manager detected")
        raise click.exceptions.Exit(1)
    if not _require_root(console):
        raise click.exceptions.Exit(1)
    res = run(["sh", "-c", _commands(be)["clean"]], sudo=_require_admin(be, "clean"), timeout=120)
    if res.ok:
        ok(console, "cache cleaned")
    else:
        err(console, res.stderr or res.stdout)
        raise click.exceptions.Exit(1)
