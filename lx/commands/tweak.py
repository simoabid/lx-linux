"""lx tweak — system tuning: swappiness, ulimits, sysctl, CPU governor."""

from __future__ import annotations

import re
import shutil
import time as _t
from pathlib import Path

import click
from rich.table import Table

from lx.utils.flags import apply_flags, json_option, json_watch_options
from lx.utils.output import center_rule, emit, err, kv_table, ok, warn
from lx.utils.parse import parse_fstrim, read_first_line, read_text
from lx.utils.prompt import confirm_destructive
from lx.utils.shell import is_root, run

_SYSCTL_DROP_IN = Path("/etc/sysctl.d/90-lx.conf")
_LIMITS_CONF = Path("/etc/security/limits.conf")
_SWAPFILE = Path("/swapfile")
_FSTAB = Path("/etc/fstab")
_FSTAB_MARKER = "# lx-managed swap"


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
        try:
            existing = _SYSCTL_DROP_IN.read_text() if _SYSCTL_DROP_IN.exists() else ""
        except OSError:
            existing = ""
        new_block = re.sub(rf"^{re.escape(key)}=.*\n", "", existing, flags=re.M)
        new_block += f"{key}={value}\n"
        _SYSCTL_DROP_IN.write_text(new_block)
        ok(console, f"persisted {key}={value} → {_SYSCTL_DROP_IN}")
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


_TUNABLES: list[tuple[str, str]] = [
    ("vm.swappiness", "lower → lean on swap less"),
    ("vm.vfs_cache_pressure", "lower → cache dirs/inodes longer"),
    ("vm.dirty_ratio", "percent RAM before sync to disk"),
    ("vm.max_map_count", "raise for DB/Java games"),
    ("net.core.somaxconn", "max listening queue length"),
    ("net.ipv4.tcp_fastopen", "3 = client+server TFO"),
    ("net.ipv4.tcp_congestion_control", "bbr/brutal for big pipes"),
]


def _collect_show() -> dict:
    return {
        "tunables": {
            key: {"value": _sysctl_get(key), "comment": comment} for key, comment in _TUNABLES
        },
        "governors": {
            "available": _cpu_governors(),
            "current": read_first_line("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"),
        },
        "io_scheduler": _collect_io_scheduler(),
    }


def _collect_io_scheduler() -> dict:
    devices = []
    for path in sorted(Path("/sys/block").glob("*/queue/scheduler")):
        if not path.is_file():
            continue
        text = read_text(path)
        if not text:
            continue
        current = None
        m = re.search(r"\[([^\]]+)\]", text)
        if m:
            current = m.group(1)
        devices.append(
            {
                "device": path.parent.parent.name,
                "current": current,
                "available": [s for s in re.sub(r"\[[^\]]*\]", "", text).split()],
            }
        )
    return {"devices": devices}


def _render_show(console, data: dict) -> None:
    center_rule(console, "Memory & Kernel Tunables")
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("Tunable")
    t.add_column("Current")
    t.add_column("Comment")
    for key, item in data["tunables"].items():
        t.add_row(key, item["value"] or "—", item["comment"])
    console.print(t)

    center_rule(console, "CPU Governors")
    gov = data["governors"]
    if gov["available"]:
        console.print(f"[dim]available:[/dim] {', '.join(gov['available'])}")
        console.print(f"[dim]current:  [/dim] [bold]{gov['current'] or '—'}[/bold]")
    else:
        console.print("[dim]governors not supported[/dim]")

    center_rule(console, "IO Schedulers")
    io = data["io_scheduler"]
    if io["devices"]:
        st = Table(show_header=True, header_style="bold cyan")
        st.add_column("Device")
        st.add_column("Current")
        st.add_column("Available")
        for dev in io["devices"]:
            st.add_row(dev["device"], dev["current"] or "—", ", ".join(dev["available"]) or "—")
        console.print(st)
    else:
        console.print("[dim]no block scheduler info[/dim]")


@click.group("tweak")
@click.pass_context
def tweak(ctx: click.Context) -> None:
    """System tuning: swappiness, ulimits, sysctl, CPU governors, IO scheduler."""
    pass


@tweak.command("show")
@json_watch_options
@click.pass_context
def _tweak_show(
    ctx: click.Context, json_mode: bool | None = None, watch_secs: float | None = None
) -> None:
    """Display common tunables and their current values."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = _collect_show()
    if emit(ctx, data, command="tweak show"):
        return
    _render_show(console, data)


@tweak.command("swappiness")
@click.argument("value", type=click.IntRange(0, 200))
@click.option("--persist/--no-persist", default=True, help="Write to /etc/sysctl.d/90-lx.conf")
@json_option
@click.pass_context
def _tweak_swappiness(
    ctx: click.Context, value: int, persist: bool, json_mode: bool | None = None
) -> None:
    """Lower (eg 10) for desktops/laptops; default 60 is usually too swap-happy."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    if not _require_root(console):
        raise click.exceptions.Exit(1)
    _set_sysctl("vm.swappiness", str(value), console, persist)
    _set_sysctl("vm.vfs_cache_pressure", "50", console, persist)
    if emit(ctx, {"ok": True, "swappiness": value, "persist": persist}, command="tweak swappiness"):
        return
    ok(console, f"swappiness set to {value}")


@tweak.command("max-files")
@click.argument("value", type=click.IntRange(1024, 2_097_152))
@json_option
@click.pass_context
def _tweak_max_files(ctx: click.Context, value: int, json_mode: bool | None = None) -> None:
    """Permanently raise the max open files (RLIMIT_NOFILE) system-wide."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    if not _require_root(console):
        raise click.exceptions.Exit(1)
    limits = _LIMITS_CONF
    content = limits.read_text() if limits.exists() else ""
    content = re.sub(r"^\*.*nofile.*\n", "", content, flags=re.M)
    content += f"\n* soft nofile {value}\n* hard nofile {value}\n"
    limits.write_text(content)
    if emit(ctx, {"ok": True, "nofile": value}, command="tweak max-files"):
        return
    ok(console, f"updated /etc/security/limits.conf → nofile={value}")
    warn(console, "log out & back in (or reboot) for it to fully take effect")


@tweak.command("governor")
@click.argument(
    "value",
    type=click.Choice(["performance", "powersave", "ondemand", "schedutil", "conservative"]),
)
@json_option
@click.pass_context
def _tweak_governor(ctx: click.Context, value: str, json_mode: bool | None = None) -> None:
    """Set CPU scaling governor (laptop → powersave; desktop/benchmarks → performance)."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    if not _require_root(console):
        raise click.exceptions.Exit(1)
    res = _set_governor(value, console)
    if emit(ctx, {"ok": res, "governor": value}, command="tweak governor"):
        if not res:
            raise click.exceptions.Exit(1)
        return


@tweak.command("bbr")
@click.option("--persist/--no-persist", default=True)
@json_option
@click.pass_context
def _tweak_bbr(ctx: click.Context, persist: bool, json_mode: bool | None = None) -> None:
    """Enable BBR TCP congestion control (great for high-bandwidth links)."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    if not _require_root(console):
        raise click.exceptions.Exit(1)
    cur = _sysctl_get("net.ipv4.tcp_congestion_control")
    if cur == "bbr":
        if emit(ctx, {"ok": True, "status": "already"}, command="tweak bbr"):
            return
        ok(console, "bbr already enabled")
        return
    available = read_text("/proc/sys/net/ipv4/tcp_available_congestion_control")
    if "bbr" in available:
        _set_sysctl("net.core.default_qdisc", "fq", console, persist)
        _set_sysctl("net.ipv4.tcp_congestion_control", "bbr", console, persist)
        if emit(ctx, {"ok": True, "status": "enabled"}, command="tweak bbr"):
            return
        ok(console, "bbr enabled (and qdisc → fq)")
    else:
        if emit(ctx, {"ok": False, "status": "unavailable"}, command="tweak bbr"):
            raise click.exceptions.Exit(1)
        err(console, "bbr module not loaded. Try: sudo modprobe tcp_bbr")


@tweak.command("interactive")
@json_option
@click.pass_context
def _tweak_inter(ctx: click.Context, json_mode: bool | None = None) -> None:
    """Apply a curated set of desktop/laptop-friendly tuning (safe, reversible)."""
    apply_flags(ctx, json_mode)
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
    if emit(ctx, {"ok": True, "profile": "interactive"}, command="tweak interactive"):
        return
    ok(console, "interactive profile applied")


@tweak.command("set")
@click.argument("key")
@click.argument("value")
@click.option("--persist/--no-persist", default=True)
@json_option
@click.pass_context
def _tweak_set(
    ctx: click.Context, key: str, value: str, persist: bool, json_mode: bool | None = None
) -> None:
    """Set an arbitrary sysctl tunable. (e.g. vx tweak set net.ipv4.ip_forward 1)"""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    if not _require_root(console):
        raise click.exceptions.Exit(1)
    res = _set_sysctl(key, value, console, persist)
    if emit(ctx, {"ok": res, "key": key, "value": value, "persist": persist}, command="tweak set"):
        if not res:
            raise click.exceptions.Exit(1)
        return


# ---------------------------------------------------------------- restore


def _collect_restore_preview() -> dict:
    if not _SYSCTL_DROP_IN.exists():
        return {"exists": False, "keys": [], "limits_has_lx_entries": False}
    keys = [
        ln
        for ln in _SYSCTL_DROP_IN.read_text().splitlines()
        if "=" in ln and not ln.lstrip().startswith("#")
    ]
    limits_has_lx_entries = False
    if _LIMITS_CONF.exists():
        limits_has_lx_entries = any(
            "nofile" in ln and ln.startswith("*") for ln in _LIMITS_CONF.read_text().splitlines()
        )
    return {"exists": True, "keys": keys, "limits_has_lx_entries": limits_has_lx_entries}


def _execute_restore() -> dict:
    backed_up_to = None
    if _SYSCTL_DROP_IN.exists():
        backed_up_to = str(_SYSCTL_DROP_IN) + f".bak-{_t.time():.0f}"
        _SYSCTL_DROP_IN.rename(backed_up_to)
    limits_cleaned = False
    if _LIMITS_CONF.exists():
        content = _LIMITS_CONF.read_text()
        cleaned = re.sub(r"^\*.*nofile.*\n", "", content, flags=re.M)
        if cleaned != content:
            _LIMITS_CONF.write_text(cleaned)
            limits_cleaned = True
    return {"ok": True, "backed_up_to": backed_up_to, "limits_cleaned": limits_cleaned}


@tweak.command("restore")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation.")
@json_option
@click.pass_context
def _tweak_restore(ctx: click.Context, yes: bool, json_mode: bool | None = None) -> None:
    """Reset all lx-applied tuning (removes the sysctl drop-in, restores defaults)."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    if not _require_root(console):
        raise click.exceptions.Exit(1)
    preview = _collect_restore_preview()
    if not preview["exists"] and not preview["limits_has_lx_entries"]:
        if emit(ctx, {"ok": True, "nothing_to_restore": True}, command="tweak restore"):
            return
        ok(console, "nothing to restore — no lx tuning found")
        return
    if not ctx.obj.json:
        center_rule(console, "lx-applied tuning")
        if preview["exists"]:
            kv_table(
                console,
                [
                    ("drop-in", str(_SYSCTL_DROP_IN)),
                    ("keys", ", ".join(k.split("=")[0] for k in preview["keys"]) or "—"),
                ],
            )
        if preview["limits_has_lx_entries"]:
            console.print(f"[bold]{_LIMITS_CONF}[/bold] contains lx nofile entries")
    if not confirm_destructive(
        ctx,
        "Reset lx-applied tuning (drop-in will be backed up, not deleted)?",
        yes=yes,
        token="restore",
    ):
        raise click.exceptions.Abort()
    data = _execute_restore()
    if emit(ctx, data, command="tweak restore"):
        return
    ok(console, f"tuning reset (drop-in backed up to {data['backed_up_to']})")
    if data["limits_cleaned"]:
        ok(console, f"lx nofile entries removed from {_LIMITS_CONF}")
    warn(console, "a reboot (or sysctl --system) applies the system defaults again")


# ---------------------------------------------------------------- swap


def _parse_size(size: str) -> int | None:
    """Parse sizes like '2G', '512M', '1048576' into bytes (None on error)."""
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([KMGTP]?)(?:i?B?)?", size.strip().upper())
    if not m:
        return None
    value = float(m.group(1))
    mult = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4, "P": 1024**5}[m.group(2)]
    return int(value * mult)


def _collect_swap_status() -> dict:
    exists = _SWAPFILE.exists()
    fstab_managed = False
    if _FSTAB.exists():
        fstab_managed = _FSTAB_MARKER in _FSTAB.read_text()
    return {
        "path": str(_SWAPFILE),
        "exists": exists,
        "size_bytes": _SWAPFILE.stat().st_size if exists else 0,
        "fstab_managed": fstab_managed,
    }


def _fstab_add_swap() -> bool:
    try:
        content = _FSTAB.read_text() if _FSTAB.exists() else ""
    except OSError:
        return False
    if _FSTAB_MARKER in content:
        return True
    try:
        with _FSTAB.open("a") as fh:
            fh.write(f"\n{_FSTAB_MARKER}\n{_SWAPFILE} none swap sw 0 0\n")
    except OSError:
        return False
    return True


def _fstab_remove_swap() -> bool:
    try:
        content = _FSTAB.read_text() if _FSTAB.exists() else ""
    except OSError:
        return False
    if _FSTAB_MARKER not in content:
        return True
    lines = [
        ln for ln in content.splitlines() if _FSTAB_MARKER not in ln and "none swap sw" not in ln
    ]
    try:
        _FSTAB.write_text("\n".join(lines) + "\n")
    except OSError:
        return False
    return True


def _execute_swap_create(size_bytes: int) -> dict:
    if _SWAPFILE.exists():
        return {"ok": False, "error": f"{_SWAPFILE} already exists"}
    fallocate = shutil.which("fallocate")
    if fallocate:
        res = run(["fallocate", "-l", str(size_bytes), str(_SWAPFILE)], sudo=True, timeout=60)
    else:
        count = max(1, size_bytes // (1024 * 1024))
        res = run(
            ["dd", "if=/dev/zero", f"of={_SWAPFILE}", "bs=1M", f"count={count}", "status=none"],
            sudo=True,
            timeout=120,
        )
    if not res.ok:
        return {"ok": False, "error": res.stderr or res.stdout}
    run(["chmod", "600", str(_SWAPFILE)], sudo=True, timeout=10)
    mkswap = run(["mkswap", str(_SWAPFILE)], sudo=True, timeout=30)
    if not mkswap.ok:
        return {"ok": False, "error": mkswap.stderr or mkswap.stdout}
    swapon = run(["swapon", str(_SWAPFILE)], sudo=True, timeout=30)
    if not swapon.ok:
        return {"ok": False, "error": swapon.stderr or swapon.stdout}
    fstab = _fstab_add_swap()
    return {
        "ok": True,
        "path": str(_SWAPFILE),
        "size_bytes": size_bytes,
        "fstab": "added" if fstab else "failed",
    }


def _execute_swap_remove() -> dict:
    size = _SWAPFILE.stat().st_size if _SWAPFILE.exists() else 0
    run(["swapoff", str(_SWAPFILE)], sudo=True, timeout=30)
    if _SWAPFILE.exists():
        try:
            _SWAPFILE.unlink()
        except OSError:
            run(["rm", "-f", str(_SWAPFILE)], sudo=True, timeout=10)
    return {"ok": True, "removed_bytes": size, "fstab_cleaned": _fstab_remove_swap()}


@tweak.group("swap")
@click.pass_context
def _tweak_swap(ctx: click.Context) -> None:
    """Create or remove a swapfile (default /swapfile)."""
    pass


@_tweak_swap.command("create")
@click.option("--size", default="2G", show_default=True, help="Size, e.g. 2G, 512M.")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation.")
@json_option
@click.pass_context
def _tweak_swap_create(
    ctx: click.Context, size: str, yes: bool, json_mode: bool | None = None
) -> None:
    """Create and enable a swapfile, persisted via /etc/fstab."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    if not _require_root(console):
        raise click.exceptions.Exit(1)
    size_bytes = _parse_size(size)
    if size_bytes is None or size_bytes < 1024 * 1024:
        err(console, f"invalid size: {size!r} (use e.g. 2G, 512M, 1048576)")
        raise click.exceptions.Exit(1)
    status = _collect_swap_status()
    if status["exists"]:
        err(console, f"{_SWAPFILE} already exists — run: sudo lx tweak swap remove")
        raise click.exceptions.Exit(1)
    if not confirm_destructive(ctx, f"Create a {size} swapfile at {_SWAPFILE}?", yes=yes):
        raise click.exceptions.Abort()
    data = _execute_swap_create(size_bytes)
    if emit(ctx, data, command="tweak swap create"):
        if not data["ok"]:
            raise click.exceptions.Exit(1)
        return
    if data["ok"]:
        ok(console, f"swapfile created and enabled ({size}, {_SWAPFILE})")
    else:
        err(console, data["error"])
        raise click.exceptions.Exit(1)


@_tweak_swap.command("remove")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation.")
@json_option
@click.pass_context
def _tweak_swap_remove(ctx: click.Context, yes: bool, json_mode: bool | None = None) -> None:
    """Disable and delete the swapfile, cleaning the fstab entry."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    if not _require_root(console):
        raise click.exceptions.Exit(1)
    status = _collect_swap_status()
    if not status["exists"]:
        if emit(ctx, {"ok": True, "nothing_to_remove": True}, command="tweak swap remove"):
            return
        ok(console, "no swapfile to remove")
        return
    if not confirm_destructive(ctx, f"Remove swapfile {_SWAPFILE}?", yes=yes):
        raise click.exceptions.Abort()
    data = _execute_swap_remove()
    if emit(ctx, data, command="tweak swap remove"):
        return
    ok(console, f"swapfile removed ({data['removed_bytes'] / 1024 / 1024:.0f} MiB)")
    if not data["fstab_cleaned"]:
        warn(console, f"could not clean {_FSTAB} — remove the '{_FSTAB_MARKER}' block manually")


# ---------------------------------------------------------------- trim


def _collect_trim(dry_run: bool) -> dict:
    args = ["fstrim"] + (["-avn"] if dry_run else ["-av"])
    res = run(args, sudo=True, timeout=120)
    rows = parse_fstrim(res.stdout)
    total = sum(r["bytes"] for r in rows)
    return {
        "ok": res.ok,
        "dry_run": dry_run,
        "mounts": rows,
        "total_bytes": total,
        "error": None if res.ok else (res.stderr or res.stdout),
    }


def _render_trim(console, data: dict) -> None:
    if data.get("error"):
        err(console, data["error"])
        return
    center_rule(console, "fstrim" + (" (dry run)" if data["dry_run"] else ""))
    if not data["mounts"]:
        ok(console, "nothing to trim")
        return
    for row in data["mounts"]:
        console.print(
            f"  [bold]{row['mount']}[/bold]  {row['bytes'] / 1024 / 1024:.0f} MiB trimmed  [dim]({row['device']})[/dim]"
        )
    console.print(f"[dim]total: {data['total_bytes'] / 1024 / 1024:.0f} MiB[/dim]")


@tweak.command("trim")
@click.option("--dry-run", is_flag=True, help="Report what would be trimmed without trimming.")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation.")
@json_option
@click.pass_context
def _tweak_trim(
    ctx: click.Context, dry_run: bool, yes: bool, json_mode: bool | None = None
) -> None:
    """TRIM mounted filesystems (fstrim -av). Requires util-linux."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    if not _require_root(console):
        raise click.exceptions.Exit(1)
    if not dry_run and not confirm_destructive(ctx, "TRIM all mounted filesystems?", yes=yes):
        raise click.exceptions.Abort()
    data = _collect_trim(dry_run)
    if emit(ctx, data, command="tweak trim"):
        if not data["ok"]:
            raise click.exceptions.Exit(1)
        return
    _render_trim(console, data)
    if not data["ok"]:
        raise click.exceptions.Exit(1)


# ---------------------------------------------------------------- network profile

_NETWORK_TUNABLES: list[tuple[str, str]] = [
    ("net.ipv4.tcp_fastopen", "3"),
    ("net.core.default_qdisc", "fq"),
    ("net.ipv4.tcp_congestion_control", "bbr"),
    ("net.core.somaxconn", "4096"),
    ("net.ipv4.tcp_slow_start_after_idle", "0"),
]


@tweak.command("network")
@click.option("--persist/--no-persist", default=True)
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation.")
@json_option
@click.pass_context
def _tweak_network(
    ctx: click.Context, persist: bool, yes: bool, json_mode: bool | None = None
) -> None:
    """Apply a curated network tuning profile (BBR if available, faster recovery)."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    if not _require_root(console):
        raise click.exceptions.Exit(1)
    if not confirm_destructive(ctx, "Apply the network tuning profile?", yes=yes):
        raise click.exceptions.Abort()
    applied = []
    skipped = []
    for key, value in _NETWORK_TUNABLES:
        if key == "net.ipv4.tcp_congestion_control":
            available = read_text("/proc/sys/net/ipv4/tcp_available_congestion_control")
            if "bbr" not in available:
                skipped.append({"key": key, "reason": "bbr not available (modprobe tcp_bbr)"})
                warn(console, f"skipping {key}: bbr not available")
                continue
        if _set_sysctl(key, value, console, persist):
            applied.append(key)
        else:
            skipped.append({"key": key, "reason": "sysctl failed"})
    if emit(
        ctx,
        {"ok": bool(applied), "profile": "network", "applied": applied, "skipped": skipped},
        command="tweak network",
    ):
        if not applied:
            raise click.exceptions.Exit(1)
        return
    if applied:
        ok(console, "network profile applied")
    else:
        err(console, "no tunables could be applied")
