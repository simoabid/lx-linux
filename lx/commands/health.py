"""lx health — overall system health score with weighted checks."""
from __future__ import annotations

import click
import psutil
from rich.panel import Panel
from rich.table import Table

from lx.utils.output import center_rule, err, ok, warn
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
        if p.fstype in ("squashfs", "tmpfs", "devtmpfs", "overlay", "fuse.snapfuse", "fuse.gvfsd-fuse"):
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
    except OSError:
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


@click.command("health")
@click.pass_context
def health(ctx: click.Context) -> None:
    """Compute an overall system health score (0–100)."""
    console = ctx.obj.console
    checks = [
        ("CPU load", _check_cpu),
        ("Memory", _check_mem),
        ("Disk", _check_disk),
        ("Swap", _check_swap),
        ("Temperature", _check_temps),
        ("Uptime", _check_uptime),
        ("Failed units", _check_failed_units),
        ("Zombies", _check_zombies),
    ]
    center_rule(console, "System health checks")
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("Check")
    t.add_column("Score", justify="right")
    t.add_column("Detail")

    scores = []
    for label, fn in checks:
        try:
            score, detail = fn()
        except Exception as exc:  # noqa: BLE001
            score, detail = 0, f"check failed: {exc}"
        scores.append(score)
        color = _score_color(score)
        t.add_row(label, f"[{color}]{score}[/{color}]", detail)

    console.print(t)
    overall = sum(scores) // len(scores) if scores else 0
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
