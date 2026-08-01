"""lx info — system information: CPU, RAM, disk, GPU, uptime, OS."""
from __future__ import annotations

from datetime import timedelta

import click
import psutil

from lx.utils.output import center_rule, gauge, kv_table, pct_color
from lx.utils.parse import (
    count_cpus,
    parse_cpuinfo,
    read_first_line,
    read_kv,
    read_text,
)
from lx.utils.shell import run


def _human_bytes(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} EiB"


def _uptime_str() -> str:
    with open("/proc/uptime") as fh:
        secs = float(fh.read().split()[0])
    return str(timedelta(seconds=int(secs)))


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
        "governor": read_first_line("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor", "—") or "—",
        "max_freq": read_first_line("/sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq", "—") or "—",
        "vendor": cpu.get("vendor_id", "—"),
    }


def _gpu_info() -> list[str]:
    out = run(["lshw", "-C", "display", "-short"], timeout=10)
    if out.ok and "display" in out.stdout:
        lines = [
            ln for ln in out.stdout.splitlines()
            if "display" in ln.lower() and "warning" not in ln.lower()
        ]
        if lines:
            return lines
    fallback = run(["lspci"], timeout=10)
    return [ln for ln in fallback.stdout.splitlines() if "VGA" in ln or "3D" in ln or "Display" in ln]


def _cpu_usage(console) -> None:
    center_rule(console, "Load & Usage")
    load1, load5, load15 = (float(x) for x in read_text("/proc/loadavg").split()[:3])
    cores = count_cpus()
    pct = min(100.0, (load1 / cores) * 100)
    color = pct_color(pct)
    bar = "█" * int(30 * pct / 100) + "░" * (30 - int(30 * pct / 100))
    console.print(f"[bold]load (1m)     [/bold] [{color}]{bar}[/{color}] {load1:.2f} (cores: {cores})")
    console.print(f"[dim]load 5/15min: {load5:.2f} / {load15:.2f}[/dim]")
    console.print(f"[dim]running procs: {len(psutil.pids())}[/dim]\n")


def _cpu_summary() -> dict[str, str]:
    cpu = _cpu_info()
    return {
        "model": cpu["model"],
        "cores": cpu["cores"],
        "vendor": cpu["vendor"],
        "governor": cpu["governor"],
        "max freq": cpu["max_freq"] + " kHz" if cpu["max_freq"] != "—" else "—",
    }


def _disk_summary(console) -> None:
    center_rule(console, "Disk Usage")
    try:
        parts = psutil.disk_partitions(all=False)
    except Exception:  # noqa: BLE001
        return
    seen = set()
    for p in parts:
        # skip pseudo / loopback / snap squashfs filesystems
        if p.fstype in ("squashfs", "tmpfs", "devtmpfs", "overlay", "fuse.snapfuse", "fuse.gvfsd-fuse"):
            continue
        if p.device.startswith("/dev/loop"):
            continue
        if p.mountpoint in seen:
            continue
        seen.add(p.mountpoint)
        try:
            u = psutil.disk_usage(p.mountpoint)
        except PermissionError:
            continue
        except OSError:
            continue
        gauge(console, p.mountpoint, u.used, u.total, unit=" B")
    console.print()


def _net_summary(console) -> None:
    center_rule(console, "Network")
    io = psutil.net_io_counters()
    rx = _human_bytes(io.bytes_recv)
    tx = _human_bytes(io.bytes_sent)
    console.print(
        f"[bold]bytes[/bold]  ↓ {rx}  ↑ {tx}   "
        f"[dim]pkts ↓ {io.packets_recv} ↑ {io.packets_sent}[/dim]\n"
    )


@click.command("info")
@click.option("--cpu", is_flag=True, help="Show only CPU details.")
@click.option("--mem", is_flag=True, help="Show only memory.")
@click.option("--disk", is_flag=True, help="Show only disk usage.")
@click.option("--gpu", is_flag=True, help="Show GPU info.")
@click.option("--net", is_flag=True, help="Show network I/O.")
@click.option("--all", "show_all", is_flag=True, help="Show everything (default).")
@click.pass_context
def info(ctx: click.Context, cpu: bool, mem: bool, disk: bool, gpu: bool, net: bool, show_all: bool) -> None:
    """Show system information: OS, CPU, memory, disk, GPU, network."""
    console = ctx.obj.console
    if not any([cpu, mem, disk, gpu, net, show_all]):
        show_all = True

    # Header banner
    os_info = _os_info()
    ram = psutil.virtual_memory()
    swap = psutil.swap_memory()

    header = (
        f"[bold cyan]lx[/bold cyan] [dim]·[/dim] "
        f"[bold]{os_info['hostname']}[/bold]  "
        f"[dim]({os_info.get('PRETTY_NAME', 'Linux')} · {os_info['arch']})[/dim]"
    )
    console.print(header)
    console.print(f"[dim]kernel {os_info['kernel']}  ·  uptime {_uptime_str()}[/dim]\n")

    sections = {"all": show_all, "cpu": cpu, "mem": mem, "disk": disk, "gpu": gpu, "net": net}
    show = sections

    if show["all"] or show["cpu"]:
        center_rule(console, "CPU")
        kv_table(console, _cpu_summary().items())
        console.print()

    if show["all"] or show["cpu"]:
        _cpu_usage(console)

    if show["all"] or show["mem"]:
        center_rule(console, "Memory")
        gauge(console, "RAM", ram.used, ram.total, unit=" B")
        if swap.total:
            gauge(console, "swap", swap.used, swap.total, unit=" B")
        else:
            console.print("[dim]swap: disabled[/dim]")
        console.print()

    if show["all"] or show["disk"]:
        _disk_summary(console)

    if show["all"] or show["gpu"]:
        center_rule(console, "GPU")
        gpus = _gpu_info()
        if gpus:
            for ln in gpus:
                console.print(f"  {ln}")
        else:
            console.print("[dim]no GPU detected (lshw/lspci unavailable)[/dim]")
        console.print()

    if show["all"] or show["net"]:
        _net_summary(console)
