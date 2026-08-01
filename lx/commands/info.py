"""lx info — system information: CPU, RAM, disk, GPU, uptime, OS."""

from __future__ import annotations

import socket
from datetime import datetime, timedelta

import click
import psutil
from rich.table import Table

from lx.commands.proc import _collect_top
from lx.utils.flags import apply_flags, json_watch_options
from lx.utils.output import center_rule, emit, gauge, human_bytes, kv_table, pct_color
from lx.utils.parse import (
    count_cpus,
    parse_cpuinfo,
    read_first_line,
    read_kv,
    read_text,
)
from lx.utils.shell import run


def _uptime_seconds() -> int:
    try:
        with open("/proc/uptime") as fh:
            return int(float(fh.read().split()[0]))
    except (OSError, ValueError, IndexError):
        return 0


def _os_info() -> dict[str, str]:
    info = read_kv("/etc/os-release")
    if "PRETTY_NAME" not in info:
        info["PRETTY_NAME"] = read_first_line("/etc/system-version") or "Linux"
    info["kernel"] = read_text("/proc/sys/kernel/osrelease")
    info["hostname"] = read_text("/proc/sys/kernel/hostname")
    info["arch"] = run(["uname", "-m"]).stdout or "unknown"
    return info


def _cpu_info() -> dict[str, str]:
    cpu = parse_cpuinfo()
    return {
        "model": cpu.get("model name", "unknown"),
        "cores": str(count_cpus()),
        "governor": read_first_line("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor", "—")
        or "—",
        "max_freq": read_first_line("/sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq", "—")
        or "—",
        "vendor": cpu.get("vendor_id", "—"),
    }


def _gpu_info() -> list[str]:
    out = run(["lshw", "-C", "display", "-short"], timeout=10)
    if out.ok and "display" in out.stdout:
        lines = [
            ln
            for ln in out.stdout.splitlines()
            if "display" in ln.lower() and "warning" not in ln.lower()
        ]
        if lines:
            return lines
    fallback = run(["lspci"], timeout=10)
    return [
        ln for ln in fallback.stdout.splitlines() if "VGA" in ln or "3D" in ln or "Display" in ln
    ]


def _load_info() -> dict:
    try:
        parts = read_text("/proc/loadavg").split()
        load1, load5, load15 = (float(x) for x in parts[:3])
    except (ValueError, IndexError):
        load1 = load5 = load15 = 0.0
    cores = count_cpus()
    return {
        "load1": load1,
        "load5": load5,
        "load15": load15,
        "cores": cores,
        "percent": min(100.0, (load1 / cores) * 100) if cores else 0.0,
        "running": len(psutil.pids()),
    }


def _disk_usage() -> list[dict]:
    rows = []
    try:
        parts = psutil.disk_partitions(all=False)
    except Exception:  # noqa: BLE001
        return rows
    seen = set()
    for p in parts:
        # skip pseudo / loopback / snap squashfs filesystems
        if p.fstype in (
            "squashfs",
            "tmpfs",
            "devtmpfs",
            "overlay",
            "fuse.snapfuse",
            "fuse.gvfsd-fuse",
        ):
            continue
        if p.device.startswith("/dev/loop"):
            continue
        if p.mountpoint in seen:
            continue
        seen.add(p.mountpoint)
        try:
            u = psutil.disk_usage(p.mountpoint)
        except (PermissionError, OSError):
            continue
        rows.append(
            {
                "mountpoint": p.mountpoint,
                "device": p.device,
                "fstype": p.fstype,
                "total": u.total,
                "used": u.used,
                "free": u.free,
                "percent": u.percent,
            }
        )
    return rows


def _collect_info(show: dict[str, bool]) -> dict:
    """Collect a JSON-safe snapshot of the requested sections."""
    os_info = _os_info()
    uptime = _uptime_seconds()
    boot = datetime.fromtimestamp(psutil.boot_time())
    data: dict = {
        "hostname": os_info["hostname"],
        "os": {
            "pretty_name": os_info.get("PRETTY_NAME", "Linux"),
            "arch": os_info["arch"],
        },
        "kernel": os_info["kernel"],
        "uptime": {"seconds": uptime, "human": str(timedelta(seconds=uptime))},
        "booted": boot.isoformat(timespec="seconds"),
    }
    if show["cpu"]:
        cpu = _cpu_info()
        data["cpu"] = {
            "model": cpu["model"],
            "cores": cpu["cores"],
            "vendor": cpu["vendor"],
            "governor": cpu["governor"],
            "max_freq_khz": cpu["max_freq"],
        }
        data["load"] = _load_info()
    if show["mem"]:
        ram = psutil.virtual_memory()
        swap = psutil.swap_memory()
        data["memory"] = {
            "total": ram.total,
            "used": ram.used,
            "available": ram.available,
            "percent": ram.percent,
            "swap_total": swap.total,
            "swap_used": swap.used,
            "swap_percent": swap.percent,
        }
    if show["disk"]:
        data["disk"] = _disk_usage()
    if show["gpu"]:
        data["gpu"] = _gpu_info()
    if show["net"]:
        io = psutil.net_io_counters()
        data["net"] = {
            "bytes_recv": io.bytes_recv,
            "bytes_sent": io.bytes_sent,
            "packets_recv": io.packets_recv,
            "packets_sent": io.packets_sent,
        }
    if show["battery"]:
        data["battery"] = _collect_battery()
    if show["temps"]:
        data["temps"] = _collect_temps()
    if show["ip"]:
        data["ips"] = _collect_ips()
    if show["procs"]:
        data["procs"] = _collect_top(5, "cpu")
    return data


def _collect_battery() -> dict | None:
    """Battery state, or None when no battery is present."""
    try:
        bat = psutil.sensors_battery()
    except (AttributeError, OSError):
        return None
    if bat is None:
        return None
    return {
        "percent": round(bat.percent, 1),
        "power_plugged": bool(bat.power_plugged),
        "seconds_left": bat.secsleft
        if bat.secsleft
        not in (
            psutil.POWER_TIME_UNLIMITED,
            psutil.POWER_TIME_UNKNOWN,
        )
        else None,
    }


def _collect_temps() -> list[dict]:
    """All temperature sensors, hottest first."""
    try:
        sensors = psutil.sensors_temperatures()
    except (AttributeError, OSError):
        return []
    rows = []
    for sensor, entries in sorted(sensors.items()):
        for e in entries:
            if e.current is None:
                continue
            rows.append(
                {
                    "name": e.label or sensor,
                    "sensor": sensor,
                    "current": e.current,
                    "high": e.high,
                    "critical": e.critical,
                }
            )
    rows.sort(key=lambda r: -r["current"])
    return rows


def _collect_ips() -> list[dict]:
    """Per-interface IP addresses (interfaces without IPs are skipped)."""
    rows = []
    for name, addr_list in sorted(psutil.net_if_addrs().items()):
        ipv4 = next((a.address for a in addr_list if a.family == socket.AF_INET), None)
        ipv6 = next((a.address for a in addr_list if a.family == socket.AF_INET6), None)
        mac = next((a.address for a in addr_list if a.family == psutil.AF_LINK), None)
        if not ipv4 and not ipv6:
            continue
        rows.append(
            {
                "iface": name,
                "ipv4": ipv4,
                "ipv6": ipv6.split("%")[0] if ipv6 else None,
                "mac": mac,
            }
        )
    return rows


def _render_header(console, data: dict) -> None:
    boot = data.get("booted", "")
    try:
        boot_str = f"booted {datetime.fromisoformat(boot):%Y-%m-%d %H:%M}" if boot else ""
    except ValueError:
        boot_str = ""
    console.print(
        f"[bold cyan]lx[/bold cyan] [dim]·[/dim] "
        f"[bold]{data['hostname']}[/bold]  "
        f"[dim]({data['os']['pretty_name']} · {data['os']['arch']})[/dim]"
    )
    console.print(
        f"[dim]kernel {data['kernel']}  ·  uptime {data['uptime']['human']}"
        + (f"  ·  {boot_str}[/dim]" if boot_str else "[/dim]")
    )
    console.print()


def _render_cpu(console, data: dict) -> None:
    center_rule(console, "CPU")
    cpu = data["cpu"]
    max_freq = f"{cpu['max_freq_khz']} kHz" if cpu["max_freq_khz"] != "—" else "—"
    kv_table(
        console,
        [
            ("model", cpu["model"]),
            ("cores", cpu["cores"]),
            ("vendor", cpu["vendor"]),
            ("governor", cpu["governor"]),
            ("max freq", max_freq),
        ],
    )
    console.print()


def _render_load(console, data: dict) -> None:
    center_rule(console, "Load & Usage")
    load = data["load"]
    pct = load["percent"]
    color = pct_color(pct)
    bar = "█" * int(30 * pct / 100) + "░" * (30 - int(30 * pct / 100))
    console.print(
        f"[bold]load (1m)     [/bold] [{color}]{bar}[/{color}] "
        f"{load['load1']:.2f} (cores: {load['cores']})"
    )
    console.print(f"[dim]load 5/15min: {load['load5']:.2f} / {load['load15']:.2f}[/dim]")
    console.print(f"[dim]running procs: {load['running']}[/dim]\n")


def _render_mem(console, data: dict) -> None:
    center_rule(console, "Memory")
    m = data["memory"]
    gauge(console, "RAM", m["used"], m["total"], unit=" B")
    if m["swap_total"]:
        gauge(console, "swap", m["swap_used"], m["swap_total"], unit=" B")
    else:
        console.print("[dim]swap: disabled[/dim]")
    console.print()


def _render_disk(console, data: dict) -> None:
    center_rule(console, "Disk Usage")
    for row in data["disk"]:
        gauge(console, row["mountpoint"], row["used"], row["total"], unit=" B")
    console.print()


def _render_gpu(console, data: dict) -> None:
    center_rule(console, "GPU")
    gpus = data["gpu"]
    if gpus:
        for ln in gpus:
            console.print(f"  {ln}")
    else:
        console.print("[dim]no GPU detected (lshw/lspci unavailable)[/dim]")
    console.print()


def _render_net(console, data: dict) -> None:
    center_rule(console, "Network")
    io = data["net"]
    rx = human_bytes(io["bytes_recv"])
    tx = human_bytes(io["bytes_sent"])
    console.print(
        f"[bold]bytes[/bold]  ↓ {rx}  ↑ {tx}   "
        f"[dim]pkts ↓ {io['packets_recv']} ↑ {io['packets_sent']}[/dim]\n"
    )


def _battery_time(bat: dict | None) -> str:
    if not bat:
        return "no battery detected"
    left = bat["seconds_left"]
    if bat["power_plugged"]:
        if left is None:
            return f"{bat['percent']:.0f}% (on AC)"
        return f"{bat['percent']:.0f}% (charging, ~{int(left // 60)}m left)"
    if left is None:
        return f"{bat['percent']:.0f}% (on battery)"
    return f"{bat['percent']:.0f}% (discharging, ~{int(left // 60)}m left)"


def _render_battery(console, data: dict) -> None:
    center_rule(console, "Battery")
    bat = data["battery"]
    if bat is None:
        console.print("[dim]no battery detected (desktop / unsupported driver)[/dim]\n")
        return
    gauge(console, "battery", bat["percent"], 100.0, unit="%")
    status = "plugged in" if bat["power_plugged"] else "on battery"
    console.print(f"[dim]{status} · {_battery_time(bat)}[/dim]\n")


def _render_temps(console, data: dict) -> None:
    center_rule(console, "Temperatures")
    temps = data["temps"]
    if not temps:
        console.print("[dim]no temperature sensors available[/dim]\n")
        return
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("Sensor")
    t.add_column("Current", justify="right")
    t.add_column("High", justify="right")
    t.add_column("Critical", justify="right")
    for row in temps:
        cur = row["current"]
        color = "green" if cur < 60 else ("yellow" if cur < 75 else "red")
        t.add_row(
            row["name"],
            f"[{color}]{cur:.0f}°C[/{color}]",
            f"{row['high']:.0f}°C" if row["high"] else "—",
            f"{row['critical']:.0f}°C" if row["critical"] else "—",
        )
    console.print(t)
    console.print()


def _render_ips(console, data: dict) -> None:
    center_rule(console, "IP Addresses")
    ips = data["ips"]
    if not ips:
        console.print("[dim]no interfaces with addresses[/dim]\n")
        return
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("IFACE")
    t.add_column("IPv4")
    t.add_column("IPv6")
    t.add_column("MAC")
    for row in ips:
        t.add_row(row["iface"], row["ipv4"] or "—", row["ipv6"] or "—", row["mac"] or "—")
    console.print(t)
    console.print()


def _render_procs(console, data: dict) -> None:
    center_rule(console, "Top 5 processes (by CPU)")
    procs = data["procs"]["processes"]

    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("PID", justify="right")
    t.add_column("CPU%", justify="right")
    t.add_column("MEM%", justify="right")
    t.add_column("COMMAND")
    for p in procs:
        cmd = p["cmd"]
        if len(cmd) > 60:
            cmd = cmd[:57] + "…"
        t.add_row(str(p["pid"]), f"{p['cpu_percent']:.1f}", f"{p['memory_percent']:.1f}", cmd)
    console.print(t)
    console.print()


def _render_short(console, data: dict) -> None:
    """One-line summary; reads the same data dict as the full view."""
    os_short = data["os"]["pretty_name"].split()[0] if data["os"]["pretty_name"] else "Linux"
    mem = data.get("memory")
    mem_str = f"mem {mem['percent']:.0f}%" if mem else "mem —"
    disk = data.get("disk")
    disk_pct = max((d["percent"] for d in disk), default=0) if disk else 0
    ips = data.get("ips") or []
    ip = next(
        (r["ipv4"] for r in ips if r["ipv4"]), next((r["ipv6"] for r in ips if r["ipv6"]), "—")
    )
    load = data.get("load", {})
    load_str = f"load {load.get('load1', 0):.2f}" if load else ""
    console.print(
        f"[bold]{data['hostname']}[/bold] | {os_short} {data['os']['arch']} | "
        f"kernel {data['kernel']} | up {data['uptime']['human']} | "
        f"{load_str} | {mem_str} | disk {disk_pct:.0f}% | [green]{ip}[/green]"
    )


@click.command("info")
@click.option("--cpu", is_flag=True, help="Show only CPU details.")
@click.option("--mem", is_flag=True, help="Show only memory.")
@click.option("--disk", is_flag=True, help="Show only disk usage.")
@click.option("--gpu", is_flag=True, help="Show GPU info.")
@click.option("--net", is_flag=True, help="Show network I/O.")
@click.option("--battery", "battery", is_flag=True, help="Show battery status.")
@click.option("--temps", is_flag=True, help="Show temperature sensors.")
@click.option("--ip", is_flag=True, help="Show interface IP addresses.")
@click.option("--procs", is_flag=True, help="Show top 5 processes by CPU.")
@click.option("--short", is_flag=True, help="One-line summary.")
@click.option("--all", "show_all", is_flag=True, help="Show everything (default).")
@json_watch_options
@click.pass_context
def info(
    ctx: click.Context,
    cpu: bool,
    mem: bool,
    disk: bool,
    gpu: bool,
    net: bool,
    battery: bool,
    temps: bool,
    ip: bool,
    procs: bool,
    short: bool,
    show_all: bool,
    json_mode: bool | None = None,
    watch_secs: float | None = None,
) -> None:
    """Show system information: OS, CPU, memory, disk, GPU, network."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    if not any([cpu, mem, disk, gpu, net, battery, temps, ip, procs, show_all]):
        show_all = True

    show = {
        "cpu": show_all or cpu,
        "mem": show_all or mem,
        "disk": show_all or disk,
        "gpu": show_all or gpu,
        "net": show_all or net,
        "battery": show_all or battery,
        "temps": show_all or temps,
        "ip": show_all or ip,
        "procs": show_all or procs,
    }
    data = _collect_info(show)

    if emit(ctx, data, command="info"):
        return

    if short:
        _render_short(console, data)
        return

    _render_header(console, data)
    if show["cpu"]:
        _render_cpu(console, data)
        _render_load(console, data)
    if show["mem"]:
        _render_mem(console, data)
    if show["disk"]:
        _render_disk(console, data)
    if show["gpu"]:
        _render_gpu(console, data)
    if show["net"]:
        _render_net(console, data)
    if show["battery"]:
        _render_battery(console, data)
    if show["temps"]:
        _render_temps(console, data)
    if show["ip"]:
        _render_ips(console, data)
    if show["procs"]:
        _render_procs(console, data)
