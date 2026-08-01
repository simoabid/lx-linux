"""lx pkg — unified package manager wrapper for apt/dnf/pacman + flatpak."""

from __future__ import annotations

import re
import shutil

import click
from rich.table import Table

from lx.utils.flags import apply_flags, json_option, json_watch_options
from lx.utils.output import center_rule, emit, err, ok, warn
from lx.utils.parse import parse_apt_simulate, parse_pkg_list
from lx.utils.prompt import confirm_destructive
from lx.utils.shell import is_root, run

BACKENDS = ("apt", "dnf", "pacman", "zypper", "apk", "flatpak", "snap")

#: Backends with a native concept of orphaned packages for `orphans`/`autoremove`.
ORPHAN_BACKENDS = ("apt", "dnf", "pacman")
#: Backends that support a simulated ("dry-run") upgrade listing.
SIMULATE_BACKENDS = ("apt", "dnf", "pacman", "zypper", "apk")


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
    table["apt"]["simulate"] = "apt-get -s upgrade"
    table["apt"]["orphans"] = "apt-get -s autoremove"
    table["apt"]["autoremove"] = "apt-get autoremove -y"
    table["dnf"]["simulate"] = "dnf upgrade --assumeno"
    table["dnf"]["orphans"] = "dnf autoremove --assumeno"
    table["dnf"]["autoremove"] = "dnf autoremove -y"
    table["pacman"]["simulate"] = "pacman -Qu"
    table["pacman"]["orphans"] = "pacman -Qdt"
    table["zypper"]["simulate"] = "zypper update --dry-run"
    table["apk"]["simulate"] = "apk upgrade --simulate"
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
        "update",
        "upgrade",
        "install",
        "remove",
        "clean",
    }


def _collect_status() -> dict:
    primary, avail = _detect_backend()
    backends = []
    totals: dict[str, int] = {}
    for be in BACKENDS:
        count = None
        if be in avail:
            n = _count_installed(be)
            count = int(n) if n >= 0 else None
            if count is not None:
                totals[be] = count
        backends.append(
            {
                "name": be,
                "installed": be in avail,
                "primary": be == primary,
                "count": count,
            }
        )
    return {"backends": backends, "primary": primary, "totals": totals}


def _render_status(console, data: dict) -> None:
    center_rule(console, "Package managers detected")
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("Backend")
    t.add_column("Installed?", justify="center")
    t.add_column("Packages", justify="right")
    for be in data["backends"]:
        if be["installed"] and be["primary"]:
            marker = "[green]✓[/green] primary"
        elif be["installed"]:
            marker = "[green]✓[/green]"
        else:
            marker = "[red]✗[/red]"
        count = be["count"]
        t.add_row(be["name"], marker, str(count) if count is not None else "—")
    console.print(t)
    primary = data["primary"]
    if primary:
        count = data["totals"].get(primary)
        count_str = count if count is not None else "?"
        console.print(
            f"\n[dim]Primary backend:[/dim] [bold]{primary}[/bold] "
            f"[dim](installed pkgs: {count_str})[/dim]"
        )


@click.group("pkg")
@click.option("-b", "--backend", type=click.Choice(BACKENDS), default=None, help="Force a backend.")
@click.pass_context
def pkg(ctx: click.Context, backend: str | None) -> None:
    """Unified wrapper around apt/dnf/pacman/zypper/apk/flatpak/snap.

    Auto-detects your package manager. Override with `--backend`.
    """
    ctx.obj.data["backend"] = backend or _detect_backend()[0]


@pkg.command("status")
@json_watch_options
@click.pass_context
def _pkg_status(
    ctx: click.Context, json_mode: bool | None = None, watch_secs: float | None = None
) -> None:
    """Show which package managers are installed and counts."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = _collect_status()
    if emit(ctx, data, command="pkg status"):
        return
    _render_status(console, data)


@pkg.command("search")
@click.argument("query")
@json_option
@click.pass_context
def _pkg_search(ctx: click.Context, query: str, json_mode: bool | None = None) -> None:
    """Search QUERY in your package manager."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    be = ctx.obj.data["backend"]
    if not be:
        err(console, "no supported package manager detected")
        raise click.exceptions.Exit(1)
    cmd = _commands(be)["search"] + " " + query
    console.print(f"[dim]$ {cmd}[/dim]")
    res = run(["sh", "-c", cmd], timeout=60)
    data = {
        "query": query,
        "command": cmd,
        "ok": res.ok,
        "stdout": res.stdout,
        "stderr": res.stderr,
    }
    if emit(ctx, data, command="pkg search"):
        return
    console.print(res.stdout or res.stderr or "[dim]nothing found[/dim]")


@pkg.command("update")
@json_option
@click.pass_context
def _pkg_update(ctx: click.Context, json_mode: bool | None = None) -> None:
    """Update package index (refresh)."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    be = ctx.obj.data["backend"]
    if not be:
        err(console, "no package manager detected")
        raise click.exceptions.Exit(1)
    if not _require_root(console):
        raise click.exceptions.Exit(1)
    cmd = _commands(be)["update"]
    res = run(["sh", "-c", cmd], sudo=_require_admin(be, "update"), timeout=120)
    data = {"ok": res.ok, "command": cmd, "stdout": res.stdout, "stderr": res.stderr}
    if emit(ctx, data, command="pkg update"):
        if not res.ok:
            raise click.exceptions.Exit(1)
        return
    if res.ok:
        ok(console, "index refreshed")
    else:
        err(console, res.stderr or res.stdout)
        raise click.exceptions.Exit(1)


@pkg.command("upgrade")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation.")
@click.option("--dry-run", is_flag=True, help="Only list what would be upgraded (no changes).")
@json_option
@click.pass_context
def _pkg_upgrade(
    ctx: click.Context, yes: bool, dry_run: bool, json_mode: bool | None = None
) -> None:
    """Upgrade all packages (--dry-run lists upgrades without changing anything)."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    be = ctx.obj.data["backend"]
    if not be:
        err(console, "no package manager detected")
        raise click.exceptions.Exit(1)
    if dry_run:
        data = _collect_dryrun()
        if emit(ctx, data, command="pkg upgrade"):
            return
        _render_dryrun(console, data)
        return
    if not _require_root(console):
        raise click.exceptions.Exit(1)
    if not confirm_destructive(ctx, f"Upgrade all packages with {be}?", yes=yes):
        raise click.exceptions.Abort()
    cmd = _commands(be)["upgrade"]
    res = run(["sh", "-c", cmd], sudo=_require_admin(be, "upgrade"), timeout=600)
    data = {"ok": res.ok, "command": cmd, "stdout": res.stdout, "stderr": res.stderr}
    if emit(ctx, data, command="pkg upgrade"):
        if not res.ok:
            raise click.exceptions.Exit(1)
        return
    if res.ok:
        ok(console, "upgrade complete")
    else:
        err(console, res.stderr or res.stdout)
        raise click.exceptions.Exit(1)


@pkg.command("install")
@click.argument("name")
@json_option
@click.pass_context
def _pkg_install(ctx: click.Context, name: str, json_mode: bool | None = None) -> None:
    """Install one or more packages (comma-separated)."""
    apply_flags(ctx, json_mode)
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
    data = {
        "ok": res.ok,
        "command": cmd,
        "packages": name,
        "stdout": res.stdout,
        "stderr": res.stderr,
    }
    if emit(ctx, data, command="pkg install"):
        if not res.ok:
            raise click.exceptions.Exit(1)
        return
    if res.ok:
        ok(console, f"installed {name}")
    else:
        err(console, res.stderr or res.stdout)
        raise click.exceptions.Exit(1)


@pkg.command("remove")
@click.argument("name")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation.")
@json_option
@click.pass_context
def _pkg_remove(ctx: click.Context, name: str, yes: bool, json_mode: bool | None = None) -> None:
    """Remove a package.

    Type 'remove' to confirm (or pass -y); in --json mode -y is required.
    """
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    be = ctx.obj.data["backend"]
    if not be:
        err(console, "no package manager detected")
        raise click.exceptions.Exit(1)
    if not _require_root(console):
        raise click.exceptions.Exit(1)
    if not confirm_destructive(
        ctx, f"Remove package {name} with {be}?", yes=yes, token="remove"
    ):
        raise click.exceptions.Abort()
    cmd = f"{_commands(be)['remove']} {name}"
    res = run(["sh", "-c", cmd], sudo=_require_admin(be, "remove"), timeout=300)
    data = {
        "ok": res.ok,
        "command": cmd,
        "package": name,
        "stdout": res.stdout,
        "stderr": res.stderr,
    }
    if emit(ctx, data, command="pkg remove"):
        if not res.ok:
            raise click.exceptions.Exit(1)
        return
    if res.ok:
        ok(console, f"removed {name}")
    else:
        err(console, res.stderr or res.stdout)
        raise click.exceptions.Exit(1)


@pkg.command("purge")
@json_option
@click.pass_context
def _pkg_purge(ctx: click.Context, json_mode: bool | None = None) -> None:
    """Clean local package caches."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    be = ctx.obj.data["backend"]
    if not be:
        err(console, "no package manager detected")
        raise click.exceptions.Exit(1)
    if not _require_root(console):
        raise click.exceptions.Exit(1)
    cmd = _commands(be)["clean"]
    res = run(["sh", "-c", cmd], sudo=_require_admin(be, "clean"), timeout=120)
    data = {"ok": res.ok, "command": cmd, "stdout": res.stdout, "stderr": res.stderr}
    if emit(ctx, data, command="pkg purge"):
        if not res.ok:
            raise click.exceptions.Exit(1)
        return
    if res.ok:
        ok(console, "cache cleaned")
    else:
        err(console, res.stderr or res.stdout)
        raise click.exceptions.Exit(1)


# ---------------------------------------------------------------- list


def _collect_list(pattern: str | None) -> dict:
    be = _detect_backend()[0]
    if not be:
        return {"backend": None, "ok": False, "error": "no supported package manager detected"}
    cmd = _commands(be)["list"]
    res = run(["sh", "-c", cmd], timeout=60)
    packages = parse_pkg_list(res.stdout, be)
    if pattern:
        packages = [p for p in packages if pattern.lower() in p["name"].lower()]
    return {
        "backend": be,
        "ok": res.ok,
        "error": None if res.ok else res.stderr,
        "pattern": pattern,
        "count": len(packages),
        "packages": packages,
    }


def _render_list(console, data: dict) -> None:
    if data.get("error"):
        err(console, data["error"])
        return
    center_rule(console, f"Installed packages via {data['backend']} ({data['count']})")
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("NAME")
    t.add_column("VERSION")
    for p in data["packages"][:60]:
        t.add_row(p["name"], p["version"])
    if data["count"] > 60:
        console.print(t)
        console.print(f"[dim]…{data['count'] - 60} more (use --json for all)[/dim]")
    else:
        console.print(t)


@pkg.command("list")
@click.option("-p", "--pattern", default=None, help="Only show packages matching NAME substring.")
@json_watch_options
@click.pass_context
def _pkg_list(
    ctx: click.Context,
    pattern: str | None,
    json_mode: bool | None = None,
    watch_secs: float | None = None,
) -> None:
    """List installed packages of the active backend."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = _collect_list(pattern)
    if emit(ctx, data, command="pkg list"):
        if not data["ok"]:
            raise click.exceptions.Exit(1)
        return
    _render_list(console, data)


# ---------------------------------------------------------------- info


def _parse_kv_info(text: str) -> dict:
    """Parse a backend `info` dump into a flat key→value dict.

    Handles `Key: value` (apt/pacman), `Key : value` (dnf) and multi-line
    values whose continuation lines start with whitespace (apt Description).
    """
    info: dict[str, str] = {}
    last_key = None
    for line in text.splitlines():
        m = re.match(r"^([A-Za-z][A-Za-z0-9_.-]*)\s*:\s?(.*)$", line)
        if m:
            key, value = m.group(1), m.group(2)
            info[key] = value
            last_key = key
        elif last_key and line.strip() and line.startswith((" ", "\t")):
            info[last_key] = f"{info[last_key]}\n{line.strip()}"
    return info


def _collect_info_pkg(package: str) -> dict:
    be = _detect_backend()[0]
    if not be:
        return {
            "backend": None,
            "package": package,
            "ok": False,
            "error": "no supported package manager detected",
            "info": {},
        }
    cmd = f"{_commands(be)['info']} {package}"
    res = run(["sh", "-c", cmd], timeout=30)
    info = _parse_kv_info(res.stdout)
    return {
        "backend": be,
        "package": package,
        "ok": res.ok,
        "error": None if res.ok else (res.stderr or res.stdout),
        "info": info,
    }


def _render_info_pkg(console, data: dict) -> None:
    if data.get("error"):
        err(console, data["error"])
        return
    center_rule(console, f"{data['package']} ({data['backend']})")
    t = Table(show_header=False, box=None, padding=(0, 2))
    t.add_column(style="bold cyan", no_wrap=True)
    t.add_column()
    for key, value in data["info"].items():
        t.add_row(key, value[:200])
    console.print(t)


@pkg.command("info")
@click.argument("package")
@json_watch_options
@click.pass_context
def _pkg_info(
    ctx: click.Context,
    package: str,
    json_mode: bool | None = None,
    watch_secs: float | None = None,
) -> None:
    """Show package metadata from the active backend."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = _collect_info_pkg(package)
    if emit(ctx, data, command="pkg info"):
        if not data["ok"]:
            raise click.exceptions.Exit(1)
        return
    _render_info_pkg(console, data)


# ---------------------------------------------------------------- simulate


def _parse_dnf_upgrade(text: str) -> list[str]:
    """Parse `dnf upgrade --assumeno` (or autoremove) package names."""
    names = []
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in ("Upgrading:", "Installing:", "Removing:"):
            in_section = True
            continue
        if not stripped:
            in_section = False
            continue
        if in_section and not stripped.startswith(("=", "-")) and "  " in stripped:
            names.append(stripped.split()[0])
    return names


def _collect_dryrun() -> dict:
    be = _detect_backend()[0]
    if not be:
        return {
            "backend": None,
            "ok": False,
            "error": "no supported package manager detected",
            "count": 0,
            "upgradable": [],
        }
    if be not in SIMULATE_BACKENDS:
        return {
            "backend": be,
            "ok": True,
            "error": None,
            "count": 0,
            "upgradable": [],
            "unsupported": f"{be} has no reliable dry-run upgrade listing",
        }
    cmd = _commands(be)["simulate"]
    res = run(["sh", "-c", cmd], timeout=120)
    upgradable: list[dict] = []
    if be == "apt":
        parsed = parse_apt_simulate(res.stdout)
        upgradable = [{"name": n, "old": None, "new": None} for n in parsed["packages"]["upgraded"]]
    elif be == "dnf":
        upgradable = [{"name": n, "old": None, "new": None} for n in _parse_dnf_upgrade(res.stdout)]
    elif be == "pacman":
        for line in res.stdout.splitlines():
            fields = line.split()
            if not fields:
                continue
            entry = {"name": fields[0], "old": None, "new": None}
            if "->" in fields:
                idx = fields.index("->")
                if idx >= 2 and idx + 1 < len(fields):
                    entry["old"], entry["new"] = fields[idx - 1], fields[idx + 1]
            upgradable.append(entry)
    elif be == "zypper":
        for line in res.stdout.splitlines():
            if " | " not in line:
                continue
            fields = [f.strip() for f in line.split("|")]
            if len(fields) >= 3 and fields[1] == "package":
                upgradable.append({"name": fields[2], "old": None, "new": None})
    elif be == "apk":
        m = re.findall(r"Upgrading\s+(\S+)\s+\(([^)]*)\)", res.stdout)
        upgradable = [
            {
                "name": name,
                "old": old.split("->")[0].strip(),
                "new": old.split("->")[1].strip() if "->" in old else None,
            }
            for name, old in m
        ]
    seen = set()
    unique = []
    for p in upgradable:
        if p["name"] not in seen:
            seen.add(p["name"])
            unique.append(p)
    return {"backend": be, "ok": True, "error": None, "count": len(unique), "upgradable": unique}


def _render_dryrun(console, data: dict) -> None:
    if data.get("error"):
        err(console, data["error"])
        return
    if data.get("unsupported"):
        warn(console, data["unsupported"])
        return
    center_rule(console, f"Upgradable via {data['backend']} ({data['count']})")
    if not data["upgradable"]:
        ok(console, "nothing to upgrade")
        return
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("NAME")
    t.add_column("OLD")
    t.add_column("NEW")
    for p in data["upgradable"][:60]:
        t.add_row(p["name"], p["old"] or "—", p["new"] or "—")
    console.print(t)
    if data["count"] > 60:
        console.print(f"[dim]…{data['count'] - 60} more (use --json for all)[/dim]")


# ---------------------------------------------------------------- orphans


def _collect_orphans() -> dict:
    be = _detect_backend()[0]
    if not be:
        return {
            "backend": None,
            "ok": False,
            "error": "no supported package manager detected",
            "count": 0,
            "orphans": [],
        }
    if be not in ORPHAN_BACKENDS:
        return {
            "backend": be,
            "ok": True,
            "error": None,
            "count": 0,
            "orphans": [],
            "unsupported": f"orphan detection is not implemented for {be}",
        }
    cmd = _commands(be)["orphans"]
    res = run(["sh", "-c", cmd], timeout=60)
    names: list[str] = []
    if be == "apt":
        names = parse_apt_simulate(res.stdout)["packages"]["removed"]
    elif be == "dnf":
        names = _parse_dnf_upgrade(res.stdout)
    elif be == "pacman":
        names = [ln.split()[0] for ln in res.stdout.splitlines() if ln.strip()]
    return {
        "backend": be,
        "ok": res.ok,
        "error": None if res.ok else res.stderr,
        "count": len(names),
        "orphans": names,
    }


def _render_orphans(console, data: dict) -> None:
    if data.get("error"):
        err(console, data["error"])
        return
    if data.get("unsupported"):
        warn(console, data["unsupported"])
        return
    center_rule(console, f"Orphaned packages via {data['backend']} ({data['count']})")
    if not data["orphans"]:
        ok(console, "no orphaned packages")
        return
    for name in data["orphans"]:
        console.print(f"  [bold]{name}[/bold]")


@pkg.command("orphans")
@json_watch_options
@click.pass_context
def _pkg_orphans(
    ctx: click.Context, json_mode: bool | None = None, watch_secs: float | None = None
) -> None:
    """List packages no longer required by anything (apt/dnf/pacman)."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = _collect_orphans()
    if emit(ctx, data, command="pkg orphans"):
        return
    _render_orphans(console, data)


# ---------------------------------------------------------------- autoremove


def _collect_autoremove(dry_run: bool) -> dict:
    be = _detect_backend()[0]
    if not be:
        return {
            "backend": None,
            "ok": False,
            "error": "no supported package manager detected",
            "dry_run": dry_run,
            "candidates": [],
            "removed": [],
        }
    if be not in ORPHAN_BACKENDS:
        return {
            "backend": be,
            "ok": True,
            "error": None,
            "dry_run": dry_run,
            "candidates": [],
            "removed": [],
            "unsupported": f"autoremove is not implemented for {be}",
        }
    orphans = _collect_orphans()
    if not orphans["ok"]:
        return {
            "backend": be,
            "ok": False,
            "error": orphans["error"],
            "dry_run": dry_run,
            "candidates": [],
            "removed": [],
        }
    candidates = orphans["orphans"]
    if not candidates or dry_run:
        return {
            "backend": be,
            "ok": True,
            "error": None,
            "dry_run": dry_run,
            "candidates": candidates,
            "removed": [],
        }
    if be == "pacman":
        res = run(["pacman", "-Rns", "--noconfirm", *candidates], sudo=True, timeout=300)
    else:
        res = run(["sh", "-c", _commands(be)["autoremove"]], sudo=True, timeout=300)
    return {
        "backend": be,
        "ok": res.ok,
        "error": None if res.ok else (res.stderr or res.stdout),
        "dry_run": dry_run,
        "candidates": candidates,
        "removed": candidates if res.ok else [],
    }


def _render_autoremove(console, data: dict) -> None:
    if data.get("error"):
        err(console, data["error"])
        return
    if data.get("unsupported"):
        warn(console, data["unsupported"])
        return
    center_rule(console, f"Autoremove via {data['backend']}")
    if not data["candidates"]:
        ok(console, "nothing to autoremove")
        return
    if data["dry_run"]:
        console.print(f"[bold]{len(data['candidates'])}[/bold] package(s) would be removed:")
        for name in data["candidates"]:
            console.print(f"  [bold]{name}[/bold]")
    else:
        ok(console, f"removed {len(data['removed'])} orphaned package(s)")
        for name in data["removed"]:
            console.print(f"  [bold]{name}[/bold]")


@pkg.command("autoremove")
@click.option("--dry-run", is_flag=True, help="Show candidates without removing.")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation.")
@json_option
@click.pass_context
def _pkg_autoremove(
    ctx: click.Context,
    dry_run: bool,
    yes: bool,
    json_mode: bool | None = None,
) -> None:
    """Remove orphaned packages (apt/dnf/pacman)."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    be = ctx.obj.data["backend"]
    if not be:
        err(console, "no package manager detected")
        raise click.exceptions.Exit(1)
    if dry_run:
        data = _collect_autoremove(dry_run=True)
        if emit(ctx, data, command="pkg autoremove"):
            return
        _render_autoremove(console, data)
        return
    if not _require_root(console):
        raise click.exceptions.Exit(1)
    preview = _collect_orphans()
    if not preview["ok"]:
        err(console, preview.get("error") or "could not compute orphaned packages")
        raise click.exceptions.Exit(1)
    if preview.get("unsupported"):
        warn(console, preview["unsupported"])
        return
    if not preview["orphans"]:
        if emit(ctx, {**preview, "dry_run": False, "removed": []}, command="pkg autoremove"):
            return
        ok(console, "nothing to autoremove")
        return
    if not confirm_destructive(
        ctx,
        f"Remove {len(preview['orphans'])} orphaned package(s) with {be}?",
        yes=yes,
        token="remove",
    ):
        raise click.exceptions.Abort()
    data = _collect_autoremove(dry_run=False)
    if emit(ctx, data, command="pkg autoremove"):
        if not data["ok"]:
            raise click.exceptions.Exit(1)
        return
    _render_autoremove(console, data)
    if not data["ok"]:
        raise click.exceptions.Exit(1)
