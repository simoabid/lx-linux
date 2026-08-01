"""lx net — network tools: interfaces, ports, public IP, speed test."""

from __future__ import annotations

import shutil
import socket
import time
import urllib.request

import click
import psutil
from rich.progress import BarColumn, Progress, TextColumn
from rich.table import Table

from lx.utils.flags import apply_flags, json_option, json_watch_options
from lx.utils.output import center_rule, emit, err, ok, warn
from lx.utils.parse import parse_arp_table, parse_ping, read_text
from lx.utils.shell import run


def _human(n: float) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}TB"


def _collect_interfaces() -> dict:
    addrs = psutil.net_if_addrs()
    io = psutil.net_io_counters(pernic=True)
    ifaces = []
    for name, addr_list in addrs.items():
        ipv4 = next((a.address for a in addr_list if a.family == socket.AF_INET), None)
        ipv6 = next((a.address for a in addr_list if a.family == socket.AF_INET6), None)
        mac = next((a.address for a in addr_list if a.family == psutil.AF_LINK), None)
        stats = io.get(name)
        ifaces.append(
            {
                "name": name,
                "ipv4": ipv4,
                "ipv6": ipv6.split("%")[0] if ipv6 else None,
                "mac": mac,
                "bytes_recv": stats.bytes_recv if stats else 0,
                "bytes_sent": stats.bytes_sent if stats else 0,
            }
        )
    return {"interfaces": ifaces}


def _render_interfaces(console, data: dict) -> None:
    center_rule(console, "Interfaces")
    for iface in data["interfaces"]:
        name = iface["name"]
        ipv4 = iface["ipv4"] or "—"
        mac = iface["mac"] or "—"
        line = f"[bold cyan]{name:<12}[/bold cyan]  [green]{ipv4:<16}[/green]  [dim]{mac}[/dim]"
        if iface["ipv6"]:
            line += f"  [dim]{iface['ipv6']}[/dim]"
        line += f"\n            [dim]↓ {_human(iface['bytes_recv'])}  ↑ {_human(iface['bytes_sent'])}[/dim]"
        console.print(line)
    console.print()


def _collect_ports() -> dict:
    listening = []
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
        try:
            name = psutil.Process(c.pid).name() if c.pid else None
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            name = None
        listening.append(
            {
                "proto": "tcp" if c.type == socket.SOCK_STREAM else "udp",
                "local": f"{c.laddr.ip}:{c.laddr.port}",
                "ip": c.laddr.ip,
                "port": c.laddr.port,
                "pid": c.pid,
                "process": name,
            }
        )
    return {"listening": listening}


def _collect_states() -> dict[str, int]:
    states: dict[str, int] = {}
    for c in psutil.net_connections(kind="inet"):
        if c.status:
            states[c.status] = states.get(c.status, 0) + 1
    return states


def _render_ports(console, data: dict) -> None:
    center_rule(console, "Listening Ports")
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("PROTO", no_wrap=True)
    t.add_column("LOCAL")
    t.add_column("PID", justify="right")
    t.add_column("PROC")
    for row in data["listening"]:
        t.add_row(row["proto"], row["local"], str(row["pid"] or "—"), row["process"] or "—")
    if t.row_count == 0:
        console.print("[dim]no listening sockets[/dim]")
    else:
        console.print(t)
    console.print()


def _render_states(console, states: dict[str, int], count: int) -> None:
    center_rule(console, f"Active Connections (top {count})")
    for s, n in sorted(states.items(), key=lambda kv: -kv[1]):
        console.print(f"  {s:<22} [bold]{n}[/bold]")
    console.print()


def _collect_pubip() -> dict:
    services = [
        "https://api.ipify.org",
        "https://ifconfig.me/ip",
        "https://icanhazip.com",
    ]
    for url in services:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                ip = resp.read().decode().strip()
            if ip:
                return {"public_ip": ip}
        except Exception:  # noqa: BLE001 — try next service
            continue
    return {"public_ip": None, "error": "could not determine public IP (offline or blocked)"}


def _render_pubip(console, data: dict) -> None:
    if data.get("public_ip"):
        ok(console, f"Public IP: [bold]{data['public_ip']}[/bold]")
    else:
        err(console, data.get("error", "could not determine public IP"))


def _collect_speed(size_mb: int, on_progress=None) -> dict:
    endpoints = [
        f"https://speed.cloudflare.com/__down?bytes={size_mb * 1024 * 1024}",
        f"https://proof.ovh.net/files/{size_mb * 10}Mb.dat",
        f"https://download.thinkbroadband.com/{size_mb}MB.zip",
    ]
    last_err = ""
    for url in endpoints:
        try:
            start = time.time()
            downloaded = 0
            with urllib.request.urlopen(url, timeout=60) as resp:
                try:
                    total = int(resp.headers.get("Content-Length") or 0)
                except (ValueError, AttributeError):
                    total = 0
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if on_progress is not None:
                        on_progress(downloaded, total)
            elapsed = time.time() - start
            if elapsed <= 0:
                return {"error": "measurement failed (zero elapsed)"}
            return {
                "size_mb": size_mb,
                "elapsed": round(elapsed, 2),
                "mbps": round((size_mb * 8) / elapsed, 2),
            }
        except Exception as exc:  # noqa: BLE001 — try next endpoint
            last_err = str(exc)
            continue
    return {"error": f"speed test failed: {last_err or 'all endpoints unreachable'}"}


def _render_speed(console, data: dict) -> None:
    if "error" in data:
        err(console, data["error"])
        return
    mbps = data["mbps"]
    color = "green" if mbps > 50 else ("yellow" if mbps > 10 else "red")
    console.print(
        f"  downloaded {data['size_mb']} MB in {data['elapsed']:.2f}s → "
        f"[{color}]{mbps:.2f} Mbps[/{color}]"
    )


def _collect_route(host: str, hops: int) -> dict:
    mtr_path = shutil.which("mtr")
    trace_path = shutil.which("traceroute")
    if not mtr_path and not trace_path:
        return {
            "host": host,
            "error": "neither traceroute nor mtr installed (e.g. sudo apt install mtr-tiny)",
        }
    if mtr_path:
        res = run(["mtr", "-n", "-c", str(hops), "--report", host], timeout=60)
        if res.ok:
            return {"host": host, "tool": "mtr", "output": res.stdout}
    if trace_path:
        res = run(["traceroute", "-n", "-m", str(hops), host], timeout=60)
        return {"host": host, "tool": "traceroute", "output": res.stdout or res.stderr}
    return {"host": host, "error": f"mtr failed for {host} and traceroute is not installed"}


def _render_route(console, data: dict) -> None:
    if data.get("error"):
        warn(console, data["error"])
        warn(console, "install one of them: sudo apt install mtr-tiny / traceroute")
        return
    console.print(data.get("output") or "[red]traceroute failed[/red]")


# ---------------------------------------------------------------- ping


def _collect_ping(host: str, count: int, interval: float, timeout: int) -> dict:
    ping_path = shutil.which("ping")
    if not ping_path:
        return {
            "host": host,
            "ok": False,
            "error": "ping binary not found (iputils-ping / iputils)",
        }
    res = run(
        ["ping", "-c", str(count), "-i", str(interval), "-W", str(timeout), host],
        timeout=count * (interval + timeout) + 10,
    )
    packets = parse_ping(res.stdout)
    error = None if res.ok else (res.stderr or (res.stdout or res.returncode and "no reply"))
    return {"host": host, "ok": res.ok, "error": error, "packets": packets}


def _render_ping(console, data: dict) -> None:
    packets = data["packets"]
    if not data["ok"] and packets["received"] == 0:
        err(console, f"{data['host']}: {data['error'] or 'no reply received'}")
        return
    loss = packets["loss_percent"]
    color = "green" if loss == 0 else ("yellow" if loss < 20 else "red")
    avg = packets["avg_ms"]
    bar_len = 20
    filled = int(bar_len * (avg / 100)) if avg is not None else 0
    bar = "█" * min(filled, bar_len) + "░" * (bar_len - min(filled, bar_len))
    console.print(
        f"[bold]{data['host']}[/bold]  [{color}]{packets['received']}/{packets['sent']}"
        f" replies ({loss}% loss)[/{color}]"
    )
    if avg is not None:
        console.print(
            f"  latency [{color}]{bar}[/{color}] "
            f"min {packets['min_ms']:.1f} · avg {avg:.1f} · max {packets['max_ms']:.1f} ms  "
            f"[dim]jitter {packets['jitter_ms']:.2f} ms[/dim]"
        )


# ---------------------------------------------------------------- dns


def _collect_dns(host: str) -> dict:
    start = time.time()
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        return {
            "host": host,
            "ok": False,
            "error": str(exc),
            "elapsed_ms": round((time.time() - start) * 1000, 1),
            "results": [],
        }
    results = []
    seen = set()
    for fam, _type, _proto, _canon, sockaddr in infos:
        family = "ipv4" if fam == socket.AF_INET else "ipv6"
        addr = sockaddr[0]
        if (family, addr) in seen:
            continue
        seen.add((family, addr))
        results.append({"family": family, "address": addr})
    return {
        "host": host,
        "ok": True,
        "error": None,
        "elapsed_ms": round((time.time() - start) * 1000, 1),
        "results": results,
    }


def _render_dns(console, data: dict) -> None:
    if not data["ok"]:
        err(console, f"{data['host']}: {data['error']}")
        return
    if not data["results"]:
        warn(console, f"{data['host']}: no addresses")
        return
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("FAMILY")
    t.add_column("ADDRESS")
    for row in data["results"]:
        t.add_row(row["family"], row["address"])
    console.print(t)
    console.print(f"[dim]{data['host']} resolved in {data['elapsed_ms']:.0f} ms[/dim]")


# ---------------------------------------------------------------- scan


def _collect_scan() -> dict:
    entries = parse_arp_table(read_text("/proc/net/arp"))
    complete = sum(1 for e in entries if e["complete"])
    return {
        "count": len(entries),
        "complete": complete,
        "incomplete": len(entries) - complete,
        "entries": entries,
    }


def _render_scan(console, data: dict) -> None:
    center_rule(console, f"ARP table ({data['count']} entries, {data['complete']} reachable)")
    if not data["entries"]:
        console.print("[dim]empty ARP table — no recent LAN activity[/dim]")
        return
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("IP")
    t.add_column("MAC")
    t.add_column("DEVICE")
    t.add_column("FLAGS")
    for e in data["entries"]:
        mac = e["mac"]
        if e["complete"]:
            t.add_row(e["ip"], mac, e["device"], "[green]complete[/green]")
        else:
            t.add_row(e["ip"], "[dim]incomplete[/dim]", e["device"], "[dim]—[/dim]")
    console.print(t)


@click.group("net")
@click.pass_context
def net(ctx: click.Context) -> None:
    """Network tools: interfaces, listening ports, public IP, speed test."""
    pass


@net.command("iface")
@json_watch_options
@click.pass_context
def _net_iface(
    ctx: click.Context, json_mode: bool | None = None, watch_secs: float | None = None
) -> None:
    """List network interfaces with addresses and traffic counters."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = _collect_interfaces()
    if emit(ctx, data, command="net iface"):
        return
    _render_interfaces(console, data)


@net.command("ports")
@click.option("-a", "--all", "show_all", is_flag=True, help="Show established connections too.")
@click.option(
    "-n", "--count", default=25, show_default=True, type=int, help="Number of connections."
)
@json_watch_options
@click.pass_context
def _net_ports(
    ctx: click.Context,
    show_all: bool,
    count: int,
    json_mode: bool | None = None,
    watch_secs: float | None = None,
) -> None:
    """Show listening sockets (and optionally established connections)."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = _collect_ports()
    data["states"] = _collect_states() if show_all else {}
    if emit(ctx, data, command="net ports"):
        return
    _render_ports(console, data)
    if show_all:
        _render_states(console, data["states"], count)


@net.command("ip")
@json_watch_options
@click.pass_context
def _net_pubip(
    ctx: click.Context, json_mode: bool | None = None, watch_secs: float | None = None
) -> None:
    """Print your public IP address."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = _collect_pubip()
    if emit(ctx, data, command="net ip"):
        return
    _render_pubip(console, data)


@net.command("speed")
@click.option("-s", "--size", default=25, show_default=True, type=int, help="MB to download.")
@json_option
@click.pass_context
def _net_speed(ctx: click.Context, size: int, json_mode: bool | None = None) -> None:
    """Quick download-speed test against a public CDN."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    center_rule(console, f"Download speed test ({size} MB)")
    if ctx.obj.json:
        data = _collect_speed(size)
    else:
        with Progress(
            TextColumn("[bold]{task.description}[/bold]"),
            BarColumn(bar_width=40),
            TextColumn("[cyan]{task.completed:>6}[/cyan] / {task.total:.0f} bytes"),
            console=console,
        ) as progress:
            task = progress.add_task("downloading", total=size * 1024 * 1024)
            data = _collect_speed(
                size, on_progress=lambda done, total: progress.update(task, completed=done)
            )
    if emit(ctx, data, command="net speed"):
        return
    _render_speed(console, data)


@net.command("ping")
@click.argument("host")
@click.option("-c", "--count", default=5, show_default=True, type=int, help="Number of pings.")
@click.option(
    "-i", "--interval", default=0.2, show_default=True, type=float, help="Seconds between pings."
)
@click.option(
    "-W", "--timeout", default=3, show_default=True, type=int, help="Seconds to wait per reply."
)
@json_watch_options
@click.pass_context
def _net_ping(
    ctx: click.Context,
    host: str,
    count: int,
    interval: float,
    timeout: int,
    json_mode: bool | None = None,
    watch_secs: float | None = None,
) -> None:
    """Measure latency and jitter to HOST (uses the system ping binary)."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = _collect_ping(host, count, interval, timeout)
    if emit(ctx, data, command="net ping"):
        return
    _render_ping(console, data)


@net.command("dns")
@click.argument("host")
@json_watch_options
@click.pass_context
def _net_dns(
    ctx: click.Context, host: str, json_mode: bool | None = None, watch_secs: float | None = None
) -> None:
    """Resolve HOST via DNS (A and AAAA records)."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = _collect_dns(host)
    if emit(ctx, data, command="net dns"):
        return
    _render_dns(console, data)


@net.command("scan")
@json_watch_options
@click.pass_context
def _net_scan(
    ctx: click.Context, json_mode: bool | None = None, watch_secs: float | None = None
) -> None:
    """Show the local ARP table (devices seen on this LAN)."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = _collect_scan()
    if emit(ctx, data, command="net scan"):
        return
    _render_scan(console, data)


@net.command("trace")
@click.argument("host")
@click.option("--hops", default=15, show_default=True, type=int, help="Max hops.")
@json_option
@click.pass_context
def _net_trace(ctx: click.Context, host: str, hops: int, json_mode: bool | None = None) -> None:
    """Run a traceroute/MTR to HOST."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    center_rule(console, f"Traceroute → {host}")
    if ctx.obj.json:
        data = _collect_route(host, hops)
    else:
        with console.status(f"Tracing route to {host}…", spinner="dots"):
            data = _collect_route(host, hops)
    if emit(ctx, data, command="net trace"):
        return
    _render_route(console, data)


@net.command("all")
@json_watch_options
@click.pass_context
def _net_all(
    ctx: click.Context, json_mode: bool | None = None, watch_secs: float | None = None
) -> None:
    """Run interfaces + ports + public IP together."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = {
        **_collect_interfaces(),
        **_collect_ports(),
        **_collect_pubip(),
    }
    if emit(ctx, data, command="net all"):
        return
    _render_interfaces(console, data)
    _render_ports(console, data)
    _render_pubip(console, data)
