"""lx doctor — environment & installation diagnostics (read-only)."""

from __future__ import annotations

import importlib.metadata
import os
import shutil
import sys
from pathlib import Path

import click
from rich.panel import Panel
from rich.table import Table

from lx import __version__
from lx.utils.flags import apply_flags, json_option
from lx.utils.output import center_rule, emit, kv_table, ok, warn
from lx.utils.parse import read_kv
from lx.utils.shell import is_root, run

_SUPPORTED_PYTHON = (3, 9)

# (binary, used by, where it comes from)
_TOOLS: list[tuple[str, str, str]] = [
    ("systemctl", "service · health", "systemd systems"),
    ("journalctl", "service log · lx log", "systemd systems"),
    ("sysctl", "tweak · sec audit", "procps"),
    ("lspci", "info --gpu · sys pci", "pciutils"),
    ("lshw", "info --gpu", "lshw"),
    ("mtr", "net trace", "mtr / mtr-tiny"),
    ("traceroute", "net trace", "traceroute"),
    ("ufw", "sec audit", "ufw"),
    ("firewall-cmd", "sec audit", "firewalld"),
    ("iptables", "sec audit", "iptables"),
    ("dpkg-query", "clean kernels", "dpkg (Debian/Ubuntu)"),
    ("crontab", "lx cron list", "cron / cronie"),
    ("fstrim", "lx tweak trim", "util-linux"),
    ("powerprofilesctl", "lx power profiles", "power-profiles-daemon"),
    ("lsusb", "lx sys usb", "usbutils"),
    ("timedatectl", "lx sys time", "systemd"),
]


def _dep_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _collect() -> dict:
    py_ok = sys.version_info >= _SUPPORTED_PYTHON
    systemd = shutil.which("systemctl") is not None
    systemd_state = None
    if systemd:
        res = run(["systemctl", "is-system-running"], timeout=10)
        systemd_state = res.stdout or res.stderr or None

    sysctl_drop = Path("/etc/sysctl.d/90-lx.conf")
    ssh_drop = Path("/etc/ssh/sshd_config.d/99-lx-hardening.conf")
    backups = Path.home() / "lx-backups"
    backup_count = 0
    if backups.is_dir():
        try:
            backup_count = len(list(backups.glob("lx-backup-*.tar.gz")))
        except OSError:
            backup_count = -1

    local_bin = Path.home() / ".local/bin"
    path_ok = str(local_bin) in os.environ.get("PATH", "")

    tools = [
        {"name": name, "used_by": used, "hint": hint, "present": shutil.which(name) is not None}
        for name, used, hint in _TOOLS
    ]
    missing = [t["name"] for t in tools if not t["present"]]

    errors = []
    warnings = []
    if not py_ok:
        errors.append(
            f"Python {sys.version.split()[0]} is older than the supported "
            f"{_SUPPORTED_PYTHON[0]}.{_SUPPORTED_PYTHON[1]}+"
        )
    if not systemd:
        warnings.append("systemd not found — service/health systemctl features unavailable")

    return {
        "version": __version__,
        "python": {
            "version": sys.version.split()[0],
            "supported": py_ok,
            "minimum": f"{_SUPPORTED_PYTHON[0]}.{_SUPPORTED_PYTHON[1]}",
            "executable": sys.executable,
        },
        "deps": [
            {"name": "rich", "version": _dep_version("rich")},
            {"name": "psutil", "version": _dep_version("psutil")},
            {"name": "click", "version": _dep_version("click")},
        ],
        "system": {
            "distro": read_kv("/etc/os-release").get("PRETTY_NAME", "Linux"),
            "kernel": run(["uname", "-r"]).stdout,
            "arch": run(["uname", "-m"]).stdout,
            "hostname": run(["uname", "-n"]).stdout,
        },
        "permissions": {"root": is_root(), "sudo": shutil.which("sudo") is not None},
        "systemd": {"present": systemd, "state": systemd_state},
        "tools": tools,
        "missing": missing,
        "state": {
            "sysctl_drop_in": {"path": str(sysctl_drop), "exists": sysctl_drop.exists()},
            "ssh_hardening": {"path": str(ssh_drop), "exists": ssh_drop.exists()},
            "backups": {"dir": str(backups), "count": backup_count},
        },
        "path": {"local_bin": str(local_bin), "on_path": path_ok},
        "verdict": {"errors": errors, "warnings": warnings},
    }


def _render(console, data: dict) -> None:
    console.print(
        Panel.fit(
            "[bold cyan]lx doctor[/bold cyan] — environment & installation diagnostics",
            border_style="cyan",
        )
    )
    console.print(f"[dim]lx {data['version']} · python {data['python']['version']}[/dim]\n")

    center_rule(console, "Runtime")
    kv_table(
        console,
        [
            (
                "python",
                data["python"]["version"]
                + ("" if data["python"]["supported"] else " [red](unsupported)[/red]"),
            ),
            ("minimum", data["python"]["minimum"]),
            ("executable", data["python"]["executable"]),
            ("rich", data["deps"][0]["version"] or "[red]missing[/red]"),
            ("psutil", data["deps"][1]["version"] or "[red]missing[/red]"),
            ("click", data["deps"][2]["version"] or "[red]missing[/red]"),
        ],
    )
    console.print()

    center_rule(console, "System")
    kv_table(
        console,
        [
            ("distro", data["system"]["distro"]),
            ("kernel", data["system"]["kernel"]),
            ("arch", data["system"]["arch"]),
            ("hostname", data["system"]["hostname"]),
        ],
    )
    console.print()

    center_rule(console, "Permissions")
    kv_table(
        console,
        [
            ("root", "[green]yes[/green]" if data["permissions"]["root"] else "no"),
            (
                "sudo",
                "[green]✓[/green]"
                if data["permissions"]["sudo"]
                else "[yellow]not found[/yellow] (install sudo)",
            ),
        ],
    )
    console.print()

    center_rule(console, "systemd")
    if data["systemd"]["present"]:
        state = data["systemd"]["state"] or "unknown"
        color = "green" if state == "running" else ("yellow" if state == "degraded" else "dim")
        console.print(f"[bold]systemctl[/bold] present — state: [{color}]{state}[/{color}]")
    else:
        console.print("[yellow]not found[/yellow] — service commands unavailable")
    console.print()

    center_rule(console, "Optional tools")
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("Tool")
    t.add_column("Used by")
    t.add_column("Present", justify="center")
    for tool in data["tools"]:
        mark = "[green]✓[/green]" if tool["present"] else f"[dim]✗ ({tool['hint']})[/dim]"
        t.add_row(tool["name"], tool["used_by"], mark)
    console.print(t)
    console.print()

    center_rule(console, "lx state")
    kv_table(
        console,
        [
            (
                "sysctl drop-in",
                "[green]✓[/green]"
                if data["state"]["sysctl_drop_in"]["exists"]
                else "not present (nothing tuned yet)",
            ),
            (
                "ssh hardening",
                "[green]✓[/green]"
                if data["state"]["ssh_hardening"]["exists"]
                else "not present (lx sec hardssh)",
            ),
            (
                "backups",
                f"{data['state']['backups']['count']} archive(s) in {data['state']['backups']['dir']}",
            ),
        ],
    )
    console.print()

    center_rule(console, "PATH")
    if data["path"]["on_path"]:
        console.print(f"[green]✓[/green] {data['path']['local_bin']} is on your PATH")
    else:
        warn(
            console,
            f"{data['path']['local_bin']} is NOT on your PATH — add it to run `lx` from install.sh",
        )
    console.print()

    errors = data["verdict"]["errors"]
    warnings = data["verdict"]["warnings"]
    missing = data["missing"]
    if errors:
        console.print(
            Panel.fit(
                f"[bold red]doctor: {len(errors)} error(s)[/bold red]\n"
                + "\n".join(f"[red]•[/red] {e}" for e in errors),
                border_style="red",
            )
        )
    elif warnings:
        console.print(
            Panel.fit(
                f"[bold yellow]doctor: {len(warnings)} warning(s)[/bold yellow]\n"
                + "\n".join(f"[yellow]•[/yellow] {w}" for w in warnings),
                border_style="yellow",
            )
        )
    else:
        ok(
            console,
            f"doctor: everything looks good ({len(missing)} optional tool(s) not installed)",
        )


@click.command("doctor")
@json_option
@click.pass_context
def doctor(ctx: click.Context, json_mode: bool | None = None) -> None:
    """Diagnose the environment: Python, deps, distro, tools, lx state."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    data = _collect()
    if emit(ctx, data, command="doctor"):
        return
    _render(console, data)
