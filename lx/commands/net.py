"""lx net — network tools: interfaces, ports, public IP, speed test."""
from __future__ import annotations

import socket
import time
import urllib.request

import click
import psutil
from rich.table import Table

from lx.utils.output import center_rule, err, ok, warn
from lx.utils.shell import run


def _human(n: float) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}TB"


def _interfaces(console) -> None:
    addrs = psutil.net_if_addrs()
    io = psutil.net_io_counters(pernic=True)
    center_rule(console, "Interfaces")
    for name, addr_list in addrs.items():
        ipv4 = next((a.address for a in addr_list if a.family == socket.AF_INET), "—")
        ipv6 = next((a.address for a in addr_list if a.family == socket.AF_INET6), None)
        mac = next((a.address for a in addr_list if a.family == psutil.AF_LINK), "—")
        stats = io.get(name)
        line = (
            f"[bold cyan]{name:<12}[/bold cyan]  "
            f"[green]{ipv4:<16}[/green]  "
            f"[dim]{mac}[/dim]"
        )
        if ipv6:
            line += f"  [dim]{ipv6.split('%')[0]}[/dim]"
        if stats:
            line += f"\n            [dim]↓ {_human(stats.bytes_recv)}  ↑ {_human(stats.bytes_sent)}[/dim]"
        console.print(line)
    console.print()


def _listening_ports(console) -> None:
    center_rule(console, "Listening Ports")
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("PROTO", no_wrap=True)
    table.add_column("LOCAL")
    table.add_column("PID", justify="right")
    table.add_column("PROC")
    seen = set()
    for c in psutil.net_connections(kind="inet"):
        if c.status != psutil.CONN_LISTEN:
            continue
        if not c.laddr:
            continue
        key = (c.type, c.laddr.port)
        if key in seen:
            continue
        seen.add(key)
        proto = "tcp" if c.type == socket.SOCK_STREAM else "udp"
        try:
            pid_name = psutil.Process(c.pid).name() if c.pid else "—"
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pid_name = "—"
        table.add_row(
            proto,
            f"{c.laddr.ip}:{c.laddr.port}",
            str(c.pid or "—"),
            pid_name,
        )
    if table.row_count == 0:
        console.print("[dim]no listening sockets[/dim]")
    else:
        console.print(table)
    console.print()


def _connections(console, count: int) -> None:
    center_rule(console, f"Active Connections (top {count})")
    states: dict[str, int] = {}
    for c in psutil.net_connections(kind="inet"):
        if c.status:
            states[c.status] = states.get(c.status, 0) + 1
    for s, n in sorted(states.items(), key=lambda kv: -kv[1]):
        console.print(f"  {s:<22} [bold]{n}[/bold]")
    console.print()


def _public_ip(console) -> None:
    center_rule(console, "Public IP / Connectivity")
    services = [
        ("https://api.ipify.org", ""),
        ("https://ifconfig.me/ip", ""),
        ("https://icanhazip.com", ""),
    ]
    for url, _ in services:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                ip = resp.read().decode().strip()
            ok(console, f"Public IP: [bold]{ip}[/bold]")
            return
        except Exception:  # noqa: BLE001 — try next service
            continue
    err(console, "Could not determine public IP (offline or blocked).")


def _speedtest(console, size_mb: int) -> None:
    center_rule(console, f"Download speed test ({size_mb} MB)")
    endpoints = [
        f"https://speed.cloudflare.com/__down?bytes={size_mb * 1024 * 1024}",
        f"https://proof.ovh.net/files/{size_mb * 10}Mb.dat",
        f"https://download.thinkbroadband.com/{size_mb}MB.zip",
    ]
    last_err = ""
    for url in endpoints:
        try:
            start = time.time()
            with urllib.request.urlopen(url, timeout=60) as resp:
                _ = resp.read()
            elapsed = time.time() - start
            if elapsed <= 0:
                err(console, "measurement failed (zero elapsed)")
                return
            mbps = (size_mb * 8) / elapsed
            color = "green" if mbps > 50 else ("yellow" if mbps > 10 else "red")
            console.print(
                f"  downloaded {size_mb} MB in {elapsed:.2f}s → "
                f"[{color}]{mbps:.2f} Mbps[/{color}]"
            )
            return
        except Exception as exc:  # noqa: BLE001 — try next endpoint
            last_err = str(exc)
            continue
    err(console, f"speed test failed: {last_err or 'all endpoints unreachable'}")


def _route(console, host: str, hops: int) -> None:
    center_rule(console, f"Traceroute → {host}")
    import shutil

    mtr_path = shutil.which("mtr")
    trace_path = shutil.which("traceroute")
    if not mtr_path and not trace_path:
        warn(console, "neither traceroute nor mtr installed — skipping")
        warn(console, "install one of them: sudo apt install mtr-tiny / traceroute")
        return
    if mtr_path:
        res = run(["mtr", "-n", "-c", str(hops), "--report", host], timeout=60)
        if res.ok:
            console.print(res.stdout)
            return
    if trace_path:
        res = run(["traceroute", "-n", "-m", str(hops), host], timeout=60)
        console.print(res.stdout or res.stderr or "[red]traceroute failed[/red]")
    else:
        err(console, f"mtr failed for {host} and traceroute is not installed")


@click.group("net")
@click.pass_context
def net(ctx: click.Context) -> None:
    """Network tools: interfaces, listening ports, public IP, speed test."""
    pass


@net.command("iface")
@click.pass_context
def _net_iface(ctx: click.Context) -> None:
    """List network interfaces with addresses and traffic counters."""
    _interfaces(ctx.obj.console)


@net.command("ports")
@click.option("-a", "--all", "show_all", is_flag=True, help="Show established connections too.")
@click.option("-n", "--count", default=25, show_default=True, type=int, help="Number of connections.")
@click.pass_context
def _net_ports(ctx: click.Context, show_all: bool, count: int) -> None:
    """Show listening sockets (and optionally established connections)."""
    console = ctx.obj.console
    _listening_ports(console)
    if show_all:
        _connections(console, count)


@net.command("ip")
@click.pass_context
def _net_pubip(ctx: click.Context) -> None:
    """Print your public IP address."""
    _public_ip(ctx.obj.console)


@net.command("speed")
@click.option("-s", "--size", default=25, show_default=True, type=int, help="MB to download.")
@click.pass_context
def _net_speed(ctx: click.Context, size: int) -> None:
    """Quick download-speed test against a public CDN."""
    _speedtest(ctx.obj.console, size)


@net.command("trace")
@click.argument("host")
@click.option("--hops", default=15, show_default=True, type=int, help="Max hops.")
@click.pass_context
def _net_trace(ctx: click.Context, host: str, hops: int) -> None:
    """Run a traceroute/MTR to HOST."""
    _route(ctx.obj.console, host, hops)


@net.command("all")
@click.pass_context
def _net_all(ctx: click.Context) -> None:
    """Run interfaces + ports + public IP together."""
    console = ctx.obj.console
    _interfaces(console)
    _listening_ports(console)
    _public_ip(console)
