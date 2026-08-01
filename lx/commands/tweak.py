"""lx tweak — system tuning: swappiness, ulimits, sysctl, CPU governor."""
from __future__ import annotations

import re
from pathlib import Path

import click
from rich.table import Table

from lx.utils.output import center_rule, err, ok, warn
from lx.utils.parse import read_first_line, read_text
from lx.utils.shell import is_root, run


def _require_root(console) -> bool:
    if is_root():
        return True
    err(console, "subcommand requires root. Retry with: sudo lx tweak ...")
    return False


def _sysctl_get(name: str) -> str:
    res = run(["sysctl", "-n", name], timeout=10)
    return res.stdout if res.ok else ""


def _set_sysctl(key: str, value: str, console, persistent: bool) -> bool:
    res = run(["sysctl", "-w", f"{key}={value}"], sudo=True, timeout=10)
    if not res.ok:
        err(console, f"failed to set {key}: {res.stderr or res.stdout}")
        return False
    if persistent:
        drop_in = Path("/etc/sysctl.d/90-lx.conf")
        try:
            existing = drop_in.read_text() if drop_in.exists() else ""
        except OSError:
            existing = ""
        new_block = re.sub(rf"^{re.escape(key)}=.*\n", "", existing, flags=re.M)
        new_block += f"{key}={value}\n"
        drop_in.write_text(new_block)
        ok(console, f"persisted {key}={value} → {drop_in}")
    return True


def _cpu_governors() -> list[str]:
    avail = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_available_governors")
    return read_text(avail).split() if avail.exists() else []


def _set_governor(value: str, console) -> bool:
    cpus = sorted(Path("/sys/devices/system/cpu/").glob("cpu[0-9]*/cpufreq/scaling_governor"))
    if not cpus:
        warn(console, "no CPU scaling governors available (unsupported CPU/driver)")
        return False
    failed = 0
    for path in cpus:
        try:
            path.write_text(value)
        except PermissionError:
            failed += 1
    if failed:
        warn(console, f"{failed} CPU(s) not updated (try sudo)")
    ok(console, f"set governor → {value} ({len(cpus)} cpus)")
    return failed == 0


@click.group("tweak")
@click.pass_context
def tweak(ctx: click.Context) -> None:
    """System tuning: swappiness, ulimits, sysctl, CPU governors, IO scheduler."""
    pass


@tweak.command("show")
@click.pass_context
def _tweak_show(ctx: click.Context) -> None:
    """Display common tunables and their current values."""
    console = ctx.obj.console
    center_rule(console, "Memory & Kernel Tunables")

    swappy = _sysctl_get("vm.swappiness")
    vfs_cache = _sysctl_get("vm.vfs_cache_pressure")
    dirty_ratio = _sysctl_get("vm.dirty_ratio")
    max_map = _sysctl_get("vm.max_map_count")
    somaxconn = _sysctl_get("net.core.somaxconn")
    tcp_fastopen = _sysctl_get("net.ipv4.tcp_fastopen")
    tcp_congestion = _sysctl_get("net.ipv4.tcp_congestion_control")

    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("Tunable")
    t.add_column("Current")
    t.add_column("Comment")
    t.add_row("vm.swappiness", swappy, "lower → lean on swap less")
    t.add_row("vm.vfs_cache_pressure", vfs_cache, "lower → cache dirs/inodes longer")
    t.add_row("vm.dirty_ratio", dirty_ratio, "percent RAM before sync to disk")
    t.add_row("vm.max_map_count", max_map, "raise for DB/Java games")
    t.add_row("net.core.somaxconn", somaxconn, "max listening queue length")
    t.add_row("net.ipv4.tcp_fastopen", tcp_fastopen, "3 = client+server TFO")
    t.add_row("net.ipv4.tcp_congestion_control", tcp_congestion, "bbr/brutal for big pipes")
    console.print(t)

    center_rule(console, "CPU Governors")
    avail = _cpu_governors()
    cur = read_first_line("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
    if avail:
        console.print(f"[dim]available:[/dim] {', '.join(avail)}")
        console.print(f"[dim]current:  [/dim] [bold]{cur}[/bold]")
    else:
        console.print("[dim]governors not supported[/dim]")

    center_rule(console, "IO Scheduler (sda)")
    sched_path = Path("/sys/block/sda/queue/scheduler")
    if sched_path.exists():
        content = sched_path.read_text().strip()
        console.print(content)
    else:
        console.print("[dim]no block scheduler info[/dim]")


@tweak.command("swappiness")
@click.argument("value", type=click.IntRange(0, 200))
@click.option("--persist/--no-persist", default=True, help="Write to /etc/sysctl.d/90-lx.conf")
@click.pass_context
def _tweak_swappiness(ctx: click.Context, value: int, persist: bool) -> None:
    """Lower (eg 10) for desktops/laptops; default 60 is usually too swap-happy."""
    console = ctx.obj.console
    if not _require_root(console):
        raise click.exceptions.Exit(1)
    _set_sysctl("vm.swappiness", str(value), console, persist)
    _set_sysctl("vm.vfs_cache_pressure", "50", console, persist)
    ok(console, f"swappiness set to {value}")


@tweak.command("max-files")
@click.argument("value", type=click.IntRange(1024, 2_097_152))
@click.pass_context
def _tweak_max_files(ctx: click.Context, value: int) -> None:
    """Permanently raise the max open files (RLIMIT_NOFILE) system-wide."""
    console = ctx.obj.console
    if not _require_root(console):
        raise click.exceptions.Exit(1)
    limits = Path("/etc/security/limits.conf")
    content = limits.read_text() if limits.exists() else ""
    content = re.sub(r"^\*.*nofile.*\n", "", content, flags=re.M)
    content += f"\n* soft nofile {value}\n* hard nofile {value}\n"
    limits.write_text(content)
    ok(console, f"updated /etc/security/limits.conf → nofile={value}")
    warn(console, "log out & back in (or reboot) for it to fully take effect")


@tweak.command("governor")
@click.argument("value", type=click.Choice(["performance", "powersave", "ondemand", "schedutil", "conservative"]))
@click.pass_context
def _tweak_governor(ctx: click.Context, value: str) -> None:
    """Set CPU scaling governor (laptop → powersave; desktop/benchmarks → performance)."""
    console = ctx.obj.console
    if not _require_root(console):
        raise click.exceptions.Exit(1)
    _set_governor(value, console)


@tweak.command("bbr")
@click.option("--persist/--no-persist", default=True)
@click.pass_context
def _tweak_bbr(ctx: click.Context, persist: bool) -> None:
    """Enable BBR TCP congestion control (great for high-bandwidth links)."""
    console = ctx.obj.console
    if not _require_root(console):
        raise click.exceptions.Exit(1)
    cur = _sysctl_get("net.ipv4.tcp_congestion_control")
    if cur == "bbr":
        ok(console, "bbr already enabled")
        return
    available = read_text("/proc/sys/net/ipv4/tcp_available_congestion_control")
    if "bbr" in available:
        _set_sysctl("net.core.default_qdisc", "fq", console, persist)
        _set_sysctl("net.ipv4.tcp_congestion_control", "bbr", console, persist)
        ok(console, "bbr enabled (and qdisc → fq)")
    else:
        err(console, "bbr module not loaded. Try: sudo modprobe tcp_bbr")


@tweak.command("interactive")
@click.pass_context
def _tweak_inter(ctx: click.Context) -> None:
    """Apply a curated set of desktop/laptop-friendly tuning (safe, reversible)."""
    console = ctx.obj.console
    if not _require_root(console):
        raise click.exceptions.Exit(1)
    console.print("[bold]Applying interactive-friendly defaults…[/bold]")
    _set_sysctl("vm.swappiness", "10", console, True)
    _set_sysctl("vm.vfs_cache_pressure", "50", console, True)
    _set_sysctl("net.core.default_qdisc", "fq", console, True)
    if "bbr" in read_text("/proc/sys/net/ipv4/tcp_available_congestion_control"):
        _set_sysctl("net.ipv4.tcp_congestion_control", "bbr", console, True)
    _set_sysctl("net.ipv4.tcp_fastopen", "3", console, True)
    if "schedutil" in _cpu_governors():
        _set_governor("schedutil", console)
    ok(console, "interactive profile applied")


@tweak.command("set")
@click.argument("key")
@click.argument("value")
@click.option("--persist/--no-persist", default=True)
@click.pass_context
def _tweak_set(ctx: click.Context, key: str, value: str, persist: bool) -> None:
    """Set an arbitrary sysctl tunable. (e.g. vx tweak set net.ipv4.ip_forward 1)"""
    console = ctx.obj.console
    if not _require_root(console):
        raise click.exceptions.Exit(1)
    _set_sysctl(key, value, console, persist)
