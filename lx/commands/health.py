"""lx health — overall system health score with weighted checks."""

from __future__ import annotations

import socket

import click
import psutil
from rich.panel import Panel
from rich.table import Table

from lx.utils.flags import apply_flags, json_watch_options
from lx.utils.output import center_rule, emit, err, ok, warn
from lx.utils.parse import count_cpus, read_text
from lx.utils.shell import run


def _score_color(score: int) -> str:
    if score >= 80:
        return "green"
    if score >= 60:
        return "yellow"
    return "red"


def _check_cpu() -> tuple[int, str]:
    cores = count_cpus()
    load1 = float(read_text("/proc/loadavg").split()[0]) if read_text("/proc/loadavg") else 0
    ratio = load1 / cores if cores else 0
    score = max(0, 100 - int(ratio * 100))
    return score, f"load {load1:.2f} / {cores} cores (ratio {ratio:.2f})"


def _check_mem() -> tuple[int, str]:
    ram = psutil.virtual_memory()
    score = max(0, 100 - int(ram.percent))
    return score, f"RAM {ram.percent:.0f}% used ({ram.available / (1024**3):.1f} GB free)"


def _check_disk() -> tuple[int, str]:
    worst = 0
    worst_path = "/"
    for p in psutil.disk_partitions(all=False):
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
        try:
            u = psutil.disk_usage(p.mountpoint)
        except (PermissionError, OSError):
            continue
        if u.percent > worst:
            worst = u.percent
            worst_path = p.mountpoint
    score = max(0, 100 - int(worst))
    return score, f"{worst_path} {worst:.0f}% full"


def _check_swap() -> tuple[int, str]:
    swap = psutil.swap_memory()
    if not swap.total:
        return 100, "no swap configured"
    score = max(0, 100 - int(swap.percent))
    return score, f"swap {swap.percent:.0f}% used"


def _check_temps() -> tuple[int, str]:
    try:
        temps = psutil.sensors_temperatures()
    except AttributeError:
        return 100, "temperature sensors unavailable"
    if not temps:
        return 100, "no temperature sensors"
    hottest = 0
    hottest_label = ""
    for name, entries in temps.items():
        for e in entries:
            if e.current and e.current > hottest:
                hottest = e.current
                hottest_label = e.label or name
    if not hottest:
        return 100, "no temperature data"
    if hottest < 60:
        score = 100
    elif hottest < 75:
        score = 70
    elif hottest < 85:
        score = 40
    else:
        score = 10
    return score, f"{hottest_label} {hottest:.0f}°C"


def _check_uptime() -> tuple[int, str]:
    try:
        with open("/proc/uptime") as fh:
            secs = float(fh.read().split()[0])
    except (OSError, ValueError, IndexError):
        return 100, "uptime unknown"
    days = secs / 86400
    if days < 30:
        score = 100
    elif days < 90:
        score = 80
    else:
        score = 50
    return score, f"up {days:.1f} days"


def _check_failed_units() -> tuple[int, str]:
    res = run(["systemctl", "--failed", "--no-pager", "--no-legend", "--plain"], timeout=10)
    if not res.ok:
        return 100, "systemctl unavailable"
    lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
    if not lines:
        return 100, "no failed units"
    score = max(0, 100 - len(lines) * 15)
    return score, f"{len(lines)} failed unit(s)"


def _check_zombies() -> tuple[int, str]:
    zombies = 0
    for p in psutil.process_iter(["status"]):
        try:
            if p.info["status"] == psutil.STATUS_ZOMBIE:
                zombies += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    score = max(0, 100 - zombies * 20)
    return score, f"{zombies} zombie process(es)"


def _check_battery() -> tuple[int, str]:
    try:
        battery = psutil.sensors_battery()
    except AttributeError:
        return 100, "battery sensors unavailable"
    if battery is None:
        return 100, "no battery detected"
    score = max(0, int(battery.percent))
    state = "plugged in" if battery.power_plugged else "on battery"
    return score, f"{battery.percent:.0f}% ({state})"


def _check_updates() -> tuple[int, str]:
    try:
        from lx.commands.sec import _collect_updates
    except ImportError:
        return 100, "updates check unavailable"
    data = _collect_updates()
    if not data.get("ok") or data.get("unsupported"):
        return 100, "updates check unavailable"
    pending = data.get("pending", 0)
    security = data.get("security") or 0
    if not pending:
        return 100, "no pending updates"
    score = max(0, 100 - security * 15 - (pending - security) * 2)
    text = f"{pending} pending"
    if security:
        text += f" ({security} security)"
    return score, text


def _check_connectivity() -> tuple[int, str]:
    try:
        sock = socket.create_connection(("1.1.1.1", 443), timeout=3)
    except OSError:
        return 0, "offline (cannot reach 1.1.1.1:443)"
    sock.close()
    return 100, "online (1.1.1.1:443 reachable)"


# (name, fn_name, weight, optional) — optional checks drop their weight when
# unsupported. Functions are resolved by name at collect time (monkeypatchable).
CHECK_REGISTRY: list[tuple[str, str, int, bool]] = [
    ("CPU load", "_check_cpu", 25, False),
    ("Memory", "_check_mem", 20, False),
    ("Disk", "_check_disk", 20, False),
    ("Failed units", "_check_failed_units", 10, False),
    ("Connectivity", "_check_connectivity", 10, False),
    ("Updates", "_check_updates", 10, False),
    ("Temperature", "_check_temps", 5, False),
    ("Battery", "_check_battery", 5, True),
    ("Swap", "_check_swap", 5, True),
    ("Uptime", _check_uptime, 5, True),
    ("Zombies", _check_zombies, 5, True),
]


def _collect(checks_filter: tuple[str, ...] | None = None) -> dict:
    """Run the selected checks and produce a weighted health report."""
    selected = [c for c in CHECK_REGISTRY if not checks_filter or c[0] in checks_filter]
    if checks_filter and not selected:
        return {
            "checks": [],
            "overall": 0,
            "verdict": "unknown",
            "weights": {},
            "weight_total": 0,
            "error": f"no such check(s): {', '.join(checks_filter)}",
        }
    checks = []
    total_weight = 0
    weighted = 0
    weights: dict[str, int] = {}
    import sys

    module = sys.modules[__name__]
    for label, fn_name, weight, optional in selected:
        try:
            score, detail = getattr(module, fn_name)()
        except Exception as exc:  # noqa: BLE001
            score, detail = 0, f"check failed: {exc}"
        if optional and detail in ("battery sensors unavailable", "no battery detected"):
            weight = 0
        checks.append({"name": label, "score": score, "detail": detail, "weight": weight})
        weights[label] = weight
        total_weight += weight
        weighted += score * weight
    overall = weighted // total_weight if total_weight else 0
    if overall >= 80:
        verdict = "good"
    elif overall >= 60:
        verdict = "attention"
    else:
        verdict = "stress"
    return {
        "checks": checks,
        "overall": overall,
        "verdict": verdict,
        "weights": weights,
        "weight_total": total_weight,
    }


def _render(console, data: dict) -> None:
    center_rule(console, "System health checks")
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("Check")
    t.add_column("Score", justify="right")
    t.add_column("Wt", justify="right")
    t.add_column("Detail")

    for check in data["checks"]:
        score = check["score"]
        color = _score_color(score)
        t.add_row(
            check["name"], f"[{color}]{score}[/{color}]", str(check["weight"]), check["detail"]
        )
    console.print(t)

    overall = data["overall"]
    color = _score_color(overall)
    console.print()
    console.print(
        Panel.fit(
            f"[bold {color}]Overall health: {overall}/100[/bold {color}]",
            border_style=color,
        )
    )
    if overall >= 80:
        ok(console, "system is in good shape")
    elif overall >= 60:
        warn(console, "some areas need attention (see above)")
    else:
        err(console, "system is under stress — investigate the low scores")


@click.command("health")
@click.option(
    "--check", "check_names", multiple=True, help="Run only named check(s) (comma-separated)."
)
@json_watch_options
@click.pass_context
def health(
    ctx: click.Context,
    check_names: tuple[str, ...],
    json_mode: bool | None = None,
    watch_secs: float | None = None,
) -> None:
    """Compute an overall system health score (0–100)."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    valid = [c[0] for c in CHECK_REGISTRY]
    aliases: dict[str, str] = {
        "cpu": "CPU load",
        "mem": "Memory",
        "memory": "Memory",
        "disk": "Disk",
        "failed": "Failed units",
        "units": "Failed units",
        "connectivity": "Connectivity",
        "online": "Connectivity",
        "updates": "Updates",
        "temp": "Temperature",
        "temps": "Temperature",
        "temperature": "Temperature",
        "battery": "Battery",
        "swap": "Swap",
        "uptime": "Uptime",
        "zombies": "Zombies",
    }
    for name in valid:
        aliases[name.lower()] = name
        aliases[name.replace(" ", "").lower()] = name
    requested: list[str] = []
    for chunk in check_names:
        for raw in chunk.split(","):
            raw = raw.strip()
            if not raw:
                continue
            requested.append(aliases.get(raw.lower(), raw))
    invalid = [n for n in requested if n not in valid]
    if invalid:
        err(console, f"unknown check(s): {', '.join(invalid)}. Valid: {', '.join(valid)}")
        raise click.exceptions.Exit(2)
    data = _collect(tuple(requested) or None)
    if emit(ctx, data, command="health"):
        return
    _render(console, data)
