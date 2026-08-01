"""lx sys — system deep-dive: USB/PCI, kernel modules, environment, time, boot history."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import click
from rich.table import Table

from lx.utils.flags import apply_flags, json_watch_options
from lx.utils.output import center_rule, emit, err, human_bytes, kv_table, warn
from lx.utils.parse import parse_boots, parse_modules, parse_timedatectl, read_text
from lx.utils.shell import run

_REDACT_RE = re.compile(r"(TOKEN|KEY|SECRET|PASSWORD|PASSWD|CREDENTIAL|AWS_|GITHUB_|API)")


# ---------------------------------------------------------------- usb / pci


def _collect_usb(base: Path | None = None) -> dict:
    if base is None:
        base = Path("/sys/bus/usb/devices")
    if shutil.which("lsusb"):
        res = run(["lsusb"], timeout=10)
        if res.ok:
            devices = [{"raw": line} for line in res.stdout.splitlines() if line.strip()]
            return {"tool": "lsusb", "count": len(devices), "devices": devices}
    devices = []
    if base.is_dir():
        for dev in sorted(base.iterdir()):
            if not (dev / "idVendor").exists():
                continue
            devices.append(
                {
                    "bus": read_text(dev / "busnum") or None,
                    "device": read_text(dev / "devnum") or None,
                    "vendor_id": read_text(dev / "idVendor") or None,
                    "product_id": read_text(dev / "idProduct") or None,
                    "manufacturer": read_text(dev / "manufacturer") or None,
                    "product": read_text(dev / "product") or None,
                }
            )
    if not devices:
        return {
            "tool": None,
            "count": 0,
            "devices": [],
            "error": "neither lsusb nor /sys/bus/usb/devices available",
        }
    return {"tool": "sysfs", "count": len(devices), "devices": devices}


def _render_usb(console, data: dict) -> None:
    center_rule(console, "USB devices")
    if data.get("error"):
        err(console, data["error"])
        warn(console, "install usbutils to get bus numbers and descriptions")
        return
    if data["tool"] == "lsusb":
        for dev in data["devices"]:
            console.print(f"  {dev['raw']}")
    else:
        t = Table(show_header=True, header_style="bold cyan")
        t.add_column("BUS:DEV")
        t.add_column("VENDOR")
        t.add_column("PRODUCT")
        t.add_column("DESCRIPTION")
        for dev in data["devices"]:
            bus_dev = (
                f"{dev['bus']}:{dev['device']}" if dev["bus"] and dev["device"] else "—"
            )
            t.add_row(
                bus_dev,
                dev["vendor_id"] or "—",
                dev["product_id"] or "—",
                " ".join(x for x in (dev["manufacturer"], dev["product"]) if x) or "—",
            )
        console.print(t)
    console.print()


def _collect_pci(base: Path | None = None) -> dict:
    if base is None:
        base = Path("/sys/bus/pci/devices")
    if shutil.which("lspci"):
        res = run(["lspci", "-nn"], timeout=10)
        if res.ok:
            devices = [{"raw": line} for line in res.stdout.splitlines() if line.strip()]
            return {"tool": "lspci", "count": len(devices), "devices": devices}
    devices = []
    if base.is_dir():
        for dev in sorted(base.iterdir()):
            if not (dev / "vendor").exists():
                continue
            devices.append(
                {
                    "slot": dev.name,
                    "vendor_id": read_text(dev / "vendor") or None,
                    "device_id": read_text(dev / "device") or None,
                    "class_id": read_text(dev / "class") or None,
                }
            )
    if not devices:
        return {
            "tool": None,
            "count": 0,
            "devices": [],
            "error": "neither lspci nor /sys/bus/pci/devices available",
        }
    return {"tool": "sysfs", "count": len(devices), "devices": devices}


def _render_pci(console, data: dict) -> None:
    center_rule(console, "PCI devices")
    if data.get("error"):
        err(console, data["error"])
        warn(console, "install pciutils to get device descriptions")
        return
    if data["tool"] == "lspci":
        for dev in data["devices"]:
            console.print(f"  {dev['raw']}")
    else:
        t = Table(show_header=True, header_style="bold cyan")
        t.add_column("SLOT")
        t.add_column("VENDOR")
        t.add_column("DEVICE")
        t.add_column("CLASS")
        for dev in data["devices"]:
            t.add_row(
                dev["slot"],
                dev["vendor_id"] or "—",
                dev["device_id"] or "—",
                dev["class_id"] or "—",
            )
        console.print(t)
    console.print()


# ---------------------------------------------------------------- modules / env


def _collect_modules() -> dict:
    modules = parse_modules(read_text("/proc/modules"))
    return {
        "count": len(modules),
        "total_size": sum(m["size"] for m in modules),
        "modules": modules,
    }


def _render_modules(console, data: dict) -> None:
    center_rule(console, f"Kernel modules ({data['count']}, {human_bytes(data['total_size'])} total)")
    if not data["modules"]:
        console.print("[dim]no modules reported[/dim]")
        return
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("MODULE")
    t.add_column("SIZE", justify="right")
    t.add_column("REF")
    t.add_column("USED BY")
    for mod in data["modules"][:30]:
        t.add_row(
            mod["name"],
            human_bytes(mod["size"]),
            str(mod["refcount"]),
            ", ".join(mod["used_by"]) or "—",
        )
    console.print(t)
    if len(data["modules"]) > 30:
        console.print(f"[dim]… {len(data['modules']) - 30} more (top 30 shown)[/dim]")
    console.print()


def _collect_env() -> dict:
    variables = []
    redacted: list[str] = []
    for key in sorted(os.environ):
        value = os.environ[key]
        if _REDACT_RE.search(key):
            redacted.append(key)
            value = "***"
        variables.append({"key": key, "value": value})
    shell = Path(os.environ.get("SHELL", "")).name or None
    locale = os.environ.get("LC_ALL") or os.environ.get("LANG") or None
    return {
        "shell": shell,
        "locale": locale,
        "count": len(variables),
        "redacted": redacted,
        "vars": variables,
    }


def _render_env(console, data: dict) -> None:
    center_rule(console, "Environment")
    kv_table(
        console,
        [
            ("shell", data["shell"] or "—"),
            ("locale", data["locale"] or "—"),
            ("variables", f"{data['count']} ({len(data['redacted'])} redacted)"),
        ],
    )
    console.print()
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("KEY")
    t.add_column("VALUE")
    for var in data["vars"][:80]:
        t.add_row(var["key"], var["value"])
    console.print(t)
    if len(data["vars"]) > 80:
        console.print(f"[dim]… {len(data['vars']) - 80} more variables[/dim]")
    console.print()


# ---------------------------------------------------------------- time / boot


def _ntp_servers(paths: tuple[str, ...] | None = None) -> list[str]:
    if paths is None:
        paths = ("/etc/systemd/timesyncd.conf", "/etc/ntp.conf")
    servers: list[str] = []
    for path in paths:
        for line in read_text(path).splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", ";")):
                continue
            if stripped.startswith("NTP="):
                servers.extend(s for s in stripped[4:].split() if s)
            elif stripped.startswith(("server ", "pool ")):
                parts = stripped.split()
                if len(parts) >= 2:
                    servers.append(parts[1])
    seen = set()
    unique = []
    for server in servers:
        if server not in seen:
            seen.add(server)
            unique.append(server)
    return unique


def _collect_time() -> dict:
    timed = run(["timedatectl"], timeout=10)
    if timed.ok:
        data = parse_timedatectl(timed.stdout)
        data["tool"] = "timedatectl"
    else:
        data = {
            "tool": "fallback",
            "time_zone": read_text("/etc/timezone") or None,
            "error": timed.stderr or "timedatectl unavailable",
        }
    data["ntp_servers"] = _ntp_servers()
    return data


def _render_time(console, data: dict) -> None:
    center_rule(console, "Time & synchronization")
    if data.get("error") and data.get("tool") == "fallback":
        console.print(f"[dim]{data['error']}[/dim]")
    rows = []
    for key in ("local_time", "universal_time", "rtc_time", "time_zone"):
        if data.get(key):
            rows.append((key.replace("_", " "), data[key]))
    if data.get("synchronized") is not None:
        rows.append(("clock synchronized", data["synchronized"]))
    if data.get("ntp_service") is not None:
        rows.append(("NTP service", data["ntp_service"]))
    if rows:
        kv_table(console, rows)
    if data["ntp_servers"]:
        console.print(f"[dim]NTP servers:[/dim] {', '.join(data['ntp_servers'])}")
    console.print()


def _journal_error(res) -> str:
    if res.returncode == 127:
        return "journalctl not found — this host is not running systemd"
    message = (res.stderr or res.stdout or "journalctl failed").strip()
    return (
        message
        + " — run with sudo or add your user to the adm/systemd-journal group"
        if message
        else "journalctl failed — run with sudo or add your user to the adm/systemd-journal group"
    )


def _collect_boots() -> dict:
    res = run(["journalctl", "--list-boots", "--no-pager"], timeout=15)
    if not res.ok:
        return {"ok": False, "count": 0, "boots": [], "error": _journal_error(res)}
    boots = parse_boots(res.stdout)
    return {"ok": True, "count": len(boots), "boots": boots, "error": None}


def _render_boots(console, data: dict) -> None:
    center_rule(console, "Boot history")
    if not data["ok"]:
        err(console, data["error"])
        return
    if not data["boots"]:
        console.print("[dim]no boot history available[/dim]")
        return
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("IDX", justify="right")
    t.add_column("BOOT ID")
    t.add_column("FIRST ENTRY")
    t.add_column("LAST ENTRY")
    t.add_column("DURATION", justify="right")
    for boot in data["boots"]:
        duration = (
            f"{boot['duration_s'] // 3600}h {(boot['duration_s'] % 3600) // 60}m"
            if boot["duration_s"] is not None
            else "—"
        )
        t.add_row(
            str(boot["idx"]),
            boot["boot_id"][:16],
            boot["start"] or "—",
            boot["end"] or "still running",
            duration,
        )
    console.print(t)
    console.print()


@click.group("sys")
@click.pass_context
def sys(ctx: click.Context) -> None:
    """System deep-dive: USB/PCI, kernel modules, environment, time, boots."""
    pass


@sys.command("usb")
@json_watch_options
@click.pass_context
def _sys_usb(
    ctx: click.Context, json_mode: bool | None = None, watch_secs: float | None = None
) -> None:
    """List USB devices (lsusb, with a sysfs fallback)."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = _collect_usb()
    if emit(ctx, data, command="sys usb"):
        return
    _render_usb(console, data)


@sys.command("pci")
@json_watch_options
@click.pass_context
def _sys_pci(
    ctx: click.Context, json_mode: bool | None = None, watch_secs: float | None = None
) -> None:
    """List PCI devices (lspci, with a sysfs fallback)."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = _collect_pci()
    if emit(ctx, data, command="sys pci"):
        return
    _render_pci(console, data)


@sys.command("modules")
@json_watch_options
@click.pass_context
def _sys_modules(
    ctx: click.Context, json_mode: bool | None = None, watch_secs: float | None = None
) -> None:
    """Show loaded kernel modules from /proc/modules."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = _collect_modules()
    if emit(ctx, data, command="sys modules"):
        return
    _render_modules(console, data)


@sys.command("env")
@json_watch_options
@click.pass_context
def _sys_env(
    ctx: click.Context, json_mode: bool | None = None, watch_secs: float | None = None
) -> None:
    """Show environment variables (secrets redacted)."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = _collect_env()
    if emit(ctx, data, command="sys env"):
        return
    _render_env(console, data)


@sys.command("time")
@json_watch_options
@click.pass_context
def _sys_time(
    ctx: click.Context, json_mode: bool | None = None, watch_secs: float | None = None
) -> None:
    """Show time zone, NTP state, and configured time servers."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = _collect_time()
    if emit(ctx, data, command="sys time"):
        return
    _render_time(console, data)


@sys.command("boot")
@json_watch_options
@click.pass_context
def _sys_boot(
    ctx: click.Context, json_mode: bool | None = None, watch_secs: float | None = None
) -> None:
    """Show boot history (journalctl --list-boots)."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = _collect_boots()
    if emit(ctx, data, command="sys boot"):
        return
    _render_boots(console, data)


@sys.command("all")
@json_watch_options
@click.pass_context
def _sys_all(
    ctx: click.Context, json_mode: bool | None = None, watch_secs: float | None = None
) -> None:
    """Run all sys sections together."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = {
        "usb": _collect_usb(),
        "pci": _collect_pci(),
        "modules": _collect_modules(),
        "env": _collect_env(),
        "time": _collect_time(),
        "boots": _collect_boots(),
    }
    if emit(ctx, data, command="sys all"):
        return
    _render_usb(console, data["usb"])
    _render_pci(console, data["pci"])
    _render_modules(console, data["modules"])
    _render_env(console, data["env"])
    _render_time(console, data["time"])
    _render_boots(console, data["boots"])
