"""lx power — deep battery status, charge rates, and power profiles."""

from __future__ import annotations

import shutil
from pathlib import Path

import click
import psutil

from lx.utils.flags import apply_flags, json_watch_options
from lx.utils.output import center_rule, emit, err, gauge, kv_table, warn
from lx.utils.parse import parse_power_supply, read_text
from lx.utils.shell import run

_POWER_FILES = (
    "charge_now",
    "charge_full",
    "charge_full_design",
    "energy_now",
    "energy_full",
    "energy_full_design",
    "current_now",
    "voltage_now",
    "power_now",
    "cycle_count",
    "capacity",
    "status",
)


def _read_power_dir(base: Path) -> dict:
    """Read the sysfs attributes of one power-supply device into SI units."""
    lines = []
    for name in _POWER_FILES:
        text = read_text(base / name)
        if text:
            lines.append(f"{name}={text}")
    return parse_power_supply("\n".join(lines))


def _sysfs_battery() -> dict:
    """Merge attributes of the first present BAT* supply ({} when none)."""
    base = Path("/sys/class/power_supply")
    if not base.is_dir():
        return {}
    for supply in sorted(base.glob("BAT*")):
        data = _read_power_dir(supply)
        if data:
            return data
    return {}


def _battery_payload(
    raw: dict,
    percent: float | None = None,
    plugged: bool | None = None,
    secsleft: int | None = None,
) -> dict:
    """Combine psutil and sysfs readings into the stable battery payload."""
    if not raw and percent is None:
        return {"present": False}
    voltage = raw.get("voltage_now")
    energy_now = raw.get("energy_now")
    if energy_now is None and raw.get("charge_now") is not None and voltage:
        energy_now = raw["charge_now"] * voltage
    energy_full = raw.get("energy_full")
    if energy_full is None and raw.get("charge_full") is not None and voltage:
        energy_full = raw["charge_full"] * voltage
    design = raw.get("energy_full_design")
    if design is None and raw.get("charge_full_design") is not None and voltage:
        design = raw["charge_full_design"] * voltage
    capacity_pct = raw.get("capacity")
    if capacity_pct is None and energy_now is not None and energy_full:
        capacity_pct = energy_now / energy_full * 100
    rate_w = raw.get("power_now")
    if rate_w is None and raw.get("current_now") is not None and voltage:
        rate_w = raw["current_now"] * voltage
    if percent is None and capacity_pct is not None:
        percent = capacity_pct
    status = raw.get("status")
    if not isinstance(status, str):
        status = "charging" if plugged else "discharging"
        if plugged is None:
            status = "unknown"
    time_left_min = None
    if (
        secsleft is not None
        and secsleft
        not in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN)
    ):
        time_left_min = secsleft // 60
    return {
        "present": True,
        "percent": round(percent, 1) if percent is not None else None,
        "plugged": plugged,
        "status": status,
        "charge_rate_w": round(rate_w, 2) if rate_w is not None else None,
        "energy_now_wh": round(energy_now, 2) if energy_now is not None else None,
        "energy_full_wh": round(energy_full, 2) if energy_full is not None else None,
        "design_wh": round(design, 2) if design is not None else None,
        "capacity_pct": round(capacity_pct, 1) if capacity_pct is not None else None,
        "cycle_count": raw.get("cycle_count"),
        "time_left_min": time_left_min,
    }


def _collect_battery() -> dict:
    try:
        bat = psutil.sensors_battery()
    except (AttributeError, OSError):
        bat = None
    raw = _sysfs_battery()
    return _battery_payload(
        raw,
        percent=bat.percent if bat else None,
        plugged=bat.power_plugged if bat else None,
        secsleft=bat.secsleft if bat else None,
    )


def _render_battery(console, data: dict) -> None:
    center_rule(console, "Battery")
    if not data["present"]:
        console.print("[dim]no battery detected (desktop / unsupported driver)[/dim]\n")
        return
    if data["percent"] is not None:
        gauge(console, "battery", data["percent"], 100.0, unit="%")
    status = data["status"] or "unknown"
    console.print(f"[dim]{status}"
                  + (f" · ~{data['time_left_min'] // 60}h {data['time_left_min'] % 60}m left"
                     if data["time_left_min"] else "")
                  + "[/dim]")
    rows = []
    if data["charge_rate_w"] is not None:
        rows.append(("charge rate", f"{data['charge_rate_w']:.2f} W"))
    if data["energy_now_wh"] is not None:
        rows.append(
            ("energy", f"{data['energy_now_wh']:.1f} Wh"
             + (f" / {data['energy_full_wh']:.1f} Wh" if data["energy_full_wh"] is not None else ""))
        )
    if data["design_wh"] is not None:
        rows.append(("design", f"{data['design_wh']:.1f} Wh"))
    if data["capacity_pct"] is not None:
        rows.append(("capacity", f"{data['capacity_pct']:.0f}% of design"))
    if data["cycle_count"] is not None:
        rows.append(("cycles", str(data["cycle_count"])))
    if rows:
        kv_table(console, rows)
    console.print()


def _parse_powerprofiles_list(text: str) -> list[str]:
    names = []
    for line in text.splitlines():
        name = line.strip().rstrip(":").replace("*", "").strip()
        if not name:
            continue
        if not line.strip().endswith(":"):
            continue
        if name != "Profiles":
            names.append(name)
    return names


def _sysfs_profile(base: Path) -> dict:
    """Read ACPI platform profile attributes under base (no daemon needed)."""
    current = read_text(base)
    if not current:
        return {"ok": False, "current": None, "available": []}
    available = read_text(Path(f"{base}_available"))
    return {
        "ok": True,
        "current": current,
        "available": [a for a in available.split() if a] if available else [],
    }


def _collect_profiles() -> dict:
    if shutil.which("powerprofilesctl"):
        current = run(["powerprofilesctl", "get"], timeout=5)
        listing = run(["powerprofilesctl", "list"], timeout=5)
        return {
            "ok": current.ok,
            "tool": "powerprofilesctl",
            "current": current.stdout or None,
            "available": _parse_powerprofiles_list(listing.stdout),
        }
    data = _sysfs_profile(Path("/sys/firmware/acpi/platform_profile"))
    if data["ok"]:
        return {
            "ok": True,
            "tool": "sysfs (ACPI)",
            "current": data["current"],
            "available": data["available"],
        }
    return {
        "ok": False,
        "tool": None,
        "current": None,
        "available": [],
        "error": "no power profile daemon found",
        "hint": "install power-profiles-daemon to manage profiles",
    }


def _render_profiles(console, data: dict) -> None:
    center_rule(console, "Power profiles")
    if not data["ok"]:
        err(console, data.get("error") or "no power profile available")
        warn(console, data.get("hint") or "")
        console.print()
        return
    console.print(f"[bold]{data['current']}[/bold] [dim](via {data['tool']})[/dim]")
    if data["available"]:
        console.print("[dim]available:[/dim] " + ", ".join(data["available"]))
    console.print()


@click.group("power")
@click.pass_context
def power(ctx: click.Context) -> None:
    """Battery status, charge rates, and power profiles."""
    pass


@power.command("battery")
@json_watch_options
@click.pass_context
def _power_battery(
    ctx: click.Context, json_mode: bool | None = None, watch_secs: float | None = None
) -> None:
    """Show deep battery status (rates, cycles, design capacity)."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = _collect_battery()
    if emit(ctx, data, command="power battery"):
        return
    _render_battery(console, data)


@power.command("profiles")
@json_watch_options
@click.pass_context
def _power_profiles(
    ctx: click.Context, json_mode: bool | None = None, watch_secs: float | None = None
) -> None:
    """Show the current power profile and the available ones."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = _collect_profiles()
    if emit(ctx, data, command="power profiles"):
        return
    _render_profiles(console, data)


@power.command("all")
@json_watch_options
@click.pass_context
def _power_all(
    ctx: click.Context, json_mode: bool | None = None, watch_secs: float | None = None
) -> None:
    """Show battery status and power profiles together."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = {"battery": _collect_battery(), "profiles": _collect_profiles()}
    if emit(ctx, data, command="power all"):
        return
    _render_battery(console, data["battery"])
    _render_profiles(console, data["profiles"])
