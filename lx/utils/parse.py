"""Tiny data parsers shared across commands."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path


def read_text(path: str | Path, default: str = "") -> str:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return default


def read_first_line(path: str | Path, default: str = "") -> str:
    text = read_text(path, "")
    return text.splitlines()[0] if text else default


def read_kv(path: str | Path, sep: str = "=") -> dict[str, str]:
    """Parse a simple `key=value` file (e.g. /etc/os-release)."""
    out: dict[str, str] = {}
    for line in read_text(path).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or sep not in line:
            continue
        k, _, v = line.partition(sep)
        out[k.strip().strip('"')] = v.strip().strip('"')
    return out


# Alias kept for callers that reach for `parse_kv`.
parse_kv = read_kv


def parse_meminfo() -> dict[str, int]:
    """Parse /proc/meminfo into {key: bytes}."""
    out: dict[str, int] = {}
    for line in read_text("/proc/meminfo").splitlines():
        if ":" not in line:
            continue
        key, _, rest = line.partition(":")
        # trailing units (kB)
        key = key.strip()
        rest = rest.strip()
        if rest.endswith("kB"):
            rest = rest[:-2].strip()
        try:
            out[key] = int(rest) * 1024
        except ValueError:
            continue
    return out


def parse_cpuinfo() -> dict[str, str]:
    """Return a flat summary of /proc/cpuinfo (first processor)."""
    fields: dict[str, str] = {}
    for line in read_text("/proc/cpuinfo").splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k, v = k.strip(), v.strip()
        if k not in fields:
            fields[k] = v
    return fields


def count_cpus() -> int:
    """Number of logical CPUs (nproc with fallback)."""
    try:
        return len(list(Path("/sys/devices/system/cpu/").glob("cpu[0-9]*")))
    except OSError:
        import os

        return os.cpu_count() or 1


# ---------------------------------------------------------------- phase 1


_PING_RTT_RE = re.compile(r"time=([\d.]+)\s*ms")
_PING_STATS_RE = re.compile(r"(\d+)\s+packets transmitted,\s+(\d+)\s+(?:packets\s+)?received")
_PING_LOSS_RE = re.compile(r"([\d.]+)%\s+packet loss")
_PING_RTT_SUMMARY_RE = re.compile(
    r"rtt\s+min/avg/max/mdev\s*=\s*([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)\s*ms"
)


def parse_ping(text: str) -> dict:
    """Parse `ping` (iputils) output into a structured summary.

    Returns a dict with packet counters, loss percentage, min/avg/max
    latency and jitter (mdev when reported, else sample std-dev) in ms.
    """
    rtts = [float(m) for m in _PING_RTT_RE.findall(text)]
    stats = _PING_STATS_RE.search(text)
    sent = int(stats.group(1)) if stats else len(rtts)
    received = int(stats.group(2)) if stats else len(rtts)
    loss = _PING_LOSS_RE.search(text)
    loss_pct = float(loss.group(1)) if loss else (100.0 if sent else 0.0)
    summary = _PING_RTT_SUMMARY_RE.search(text)
    if summary:
        min_ms, avg_ms, max_ms, mdev = (float(g) for g in summary.groups())
        jitter = mdev
    elif rtts:
        avg = sum(rtts) / len(rtts)
        min_ms, max_ms = min(rtts), max(rtts)
        avg_ms = avg
        jitter = (sum((r - avg) ** 2 for r in rtts) / len(rtts)) ** 0.5
    else:
        min_ms = avg_ms = max_ms = jitter = None
    return {
        "sent": sent,
        "received": received,
        "loss_percent": loss_pct,
        "min_ms": round(min_ms, 3) if min_ms is not None else None,
        "avg_ms": round(avg_ms, 3) if avg_ms is not None else None,
        "max_ms": round(max_ms, 3) if max_ms is not None else None,
        "jitter_ms": round(jitter, 3) if jitter is not None else None,
        "rtts": [round(r, 3) for r in rtts],
    }


def parse_arp_table(text: str) -> list[dict]:
    """Parse /proc/net/arp content into entry dicts (skip header line)."""
    entries = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 6 or fields[0] == "IP":
            continue
        entries.append(
            {
                "ip": fields[0],
                "hw_type": fields[1],
                "flags": fields[2],
                "mac": fields[3],
                "mask": fields[4],
                "device": fields[5],
                "complete": fields[2] == "0x2",
            }
        )
    return entries


_LOGIN_FALSE_SHELLS = {"/bin/false", "/usr/bin/false", "/sbin/nologin", "/usr/sbin/nologin"}


def parse_passwd(text: str) -> list[dict]:
    """Parse /etc/passwd content into user dicts."""
    users = []
    for line in text.splitlines():
        fields = line.split(":")
        if len(fields) < 7:
            continue
        name, _pw, uid, gid, gecos, home, shell = fields[:7]
        users.append(
            {
                "name": name,
                "uid": int(uid) if uid.isdigit() else -1,
                "gid": int(gid) if gid.isdigit() else -1,
                "gecos": gecos,
                "home": home,
                "shell": shell,
                "is_login_shell": shell not in _LOGIN_FALSE_SHELLS,
            }
        )
    return users


_KEY_TYPES = (
    "ssh-ed25519",
    "ssh-rsa",
    "ssh-dss",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "sk-ssh-ed25519@openssh.com",
    "sk-ecdsa-sha2-nistp256@openssh.com",
)


def parse_authorized_keys(text: str) -> list[dict]:
    """Parse an authorized_keys file into {type, key, comment} dicts.

    Lines with option prefixes (e.g. `no-pty,command=...`) are handled by
    locating the ssh key type token anywhere in the line.
    """
    keys = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tokens = line.split()
        idx = next((i for i, t in enumerate(tokens) if t in _KEY_TYPES), None)
        if idx is None or idx + 1 >= len(tokens):
            continue
        keys.append(
            {
                "type": tokens[idx],
                "key": tokens[idx + 1],
                "comment": " ".join(tokens[idx + 2 :]) or None,
            }
        )
    return keys


_APT_LIST_RE = re.compile(r"^(\S+)/(\S+),now\s+(\S+)\s+\S+")
_DNF_LIST_RE = re.compile(r"^(\S+)\s+(\S+)\s+@\S+")
_PACMAN_LIST_RE = re.compile(r"^(\S+)\s+(\S+)\s*$")
_APK_LIST_RE = re.compile(r"^(\S+)-(\S+-r\d+)\s+\S+")
_ZYPPER_LIST_RE = re.compile(r"^\s*i?\s*\|\s*([^|]+)\s*\|\s*package\s*\|\s*([^|]+)")


def parse_pkg_list(text: str, backend: str) -> list[dict]:
    """Parse an installed-packages listing into [{name, version}] dicts.

    Handles apt, dnf, pacman, zypper and apk output formats; falls back to
    a first-token name heuristic for anything else (e.g. flatpak/snap).
    """
    packages: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("WARNING", "Listing", "Reading")):
            continue
        name = version = None
        if backend == "apt":
            m = _APT_LIST_RE.match(line)
            if m:
                name, version = m.group(1), m.group(3)
        elif backend == "dnf":
            m = _DNF_LIST_RE.match(line)
            if m:
                name, version = m.group(1), m.group(2)
        elif backend == "pacman":
            m = _PACMAN_LIST_RE.match(line)
            if m:
                name, version = m.group(1), m.group(2)
        elif backend == "apk":
            m = _APK_LIST_RE.match(line)
            if m:
                name, version = m.group(1), m.group(2)
        elif backend == "zypper":
            m = _ZYPPER_LIST_RE.match(line)
            if m:
                name, version = m.group(1).strip(), m.group(2).strip()
        if name is None:
            tokens = line.split()
            if not tokens:
                continue
            name = tokens[0].split("/")[0].split(",")[0]
            version = tokens[1] if len(tokens) > 1 else None
        name = name.rstrip(":") if name else name
        if name and version and (name, version) not in seen:
            seen.add((name, version))
            packages.append({"name": name, "version": version})
    return packages


_APT_SUMMARY_RE = re.compile(
    r"(\d+) upgraded,\s+(\d+) newly installed,\s+(\d+) to remove\s+"
    r"and\s+(\d+) not upgraded"
)


def parse_apt_simulate(text: str) -> dict:
    """Parse `apt-get -s upgrade` output into counts + package lists."""
    headers = {
        "The following packages will be upgraded:": "upgraded",
        "The following NEW packages will be installed:": "newly_installed",
        "The following packages will be REMOVED:": "removed",
        "The following packages have been kept back:": "not_upgraded",
    }
    sections: dict[str, list[str]] = {
        "upgraded": [],
        "newly_installed": [],
        "removed": [],
        "not_upgraded": [],
    }
    current: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in headers:
            current = headers[stripped]
            continue
        if not stripped:
            current = None
            continue
        if current is not None:
            if stripped.endswith("."):
                current = None
            else:
                sections[current].extend(stripped.split())
    m = _APT_SUMMARY_RE.search(text)
    counts = [int(g) for g in m.groups()] if m else [len(sections["upgraded"]), 0, 0, 0]
    summary = {
        "upgraded": counts[0],
        "newly_installed": counts[1],
        "removed": counts[2],
        "not_upgraded": counts[3],
    }
    return {"summary": summary, "packages": sections}


def parse_fstrim(text: str) -> list[dict]:
    """Parse `fstrim -av` output into [{mount, bytes, device}] dicts.

    Supports both `/: 123.4 MiB (129504256 bytes) trimmed` and
    `/: 123.4 MiB (129504256 bytes) trimmed on /dev/sda2` formats.
    """
    rows = []
    for line in text.splitlines():
        m = re.match(
            r"^(\S+?)\s*:\s*([\d.]+)\s+\S+\s+\((\d+)\s+bytes\)\s+trimmed(?:\s+on\s+(\S+))?", line
        )
        if m:
            rows.append(
                {
                    "mount": m.group(1),
                    "bytes": int(m.group(3)),
                    "device": m.group(4) if m.group(4) else "?",
                }
            )
    return rows


_DOCKER_DF_RE = re.compile(
    r"^([A-Za-z ]+?)\s+(\d+)\s+(\d+)\s+([\d.]+\S+)\s+([\d.]+\S+)(?:\s+\(([\d.]+)%\))?"
)


_HUMAN_SIZE_RE = re.compile(r"^([\d.]+)\s*([KMGTP]?B?|B)?$")


def _human_to_bytes(size: str) -> int:
    """Convert human sizes like '800.5MB', '1.2GB', '0B' to bytes."""
    m = _HUMAN_SIZE_RE.match(size.strip().upper())
    if not m:
        return 0
    value = float(m.group(1))
    unit = (m.group(2) or "").upper()
    if unit.startswith("K"):
        return int(value * 1024)
    if unit.startswith("M"):
        return int(value * 1024**2)
    if unit.startswith("G"):
        return int(value * 1024**3)
    if unit.startswith("T"):
        return int(value * 1024**4)
    if unit.startswith("P"):
        return int(value * 1024**5)
    return int(value)


def parse_docker_df(text: str) -> list[dict]:
    """Parse `docker system df` output into structured rows (bytes)."""
    rows = []
    for line in text.splitlines():
        if line.startswith("TYPE") or not line.strip():
            continue
        m = _DOCKER_DF_RE.match(line)
        if m:
            rows.append(
                {
                    "type": m.group(1).strip(),
                    "total": int(m.group(2)),
                    "active": int(m.group(3)),
                    "size": m.group(4),
                    "size_bytes": _human_to_bytes(m.group(4)),
                    "reclaimable": m.group(5),
                    "reclaimable_bytes": _human_to_bytes(m.group(5)),
                    "reclaimable_percent": float(m.group(6)) if m.group(6) else None,
                }
            )
    return rows


def parse_blame(text: str) -> list[dict]:
    """Parse `systemd-analyze blame` output into [{time_ms, unit}] dicts."""
    rows = []
    for line in text.splitlines():
        m = re.match(r"^([\d.]+)s\s+(.+)$", line)
        if m:
            rows.append({"time_ms": round(float(m.group(1)) * 1000, 1), "unit": m.group(2)})
    return rows


def parse_critical_chain(text: str) -> list[dict]:
    """Parse `systemd-analyze critical-chain` output.

    Each line: `└─unit @<active> +<duration>`; tree glyphs are stripped.
    """
    rows = []
    for line in text.splitlines():
        if "analysis finished" in line or line.startswith("The time"):
            continue
        cleaned = re.sub(r"^[\s└─│├┐┌┴┬]+", "", line)
        m = re.match(r"^(.+?)\s+@([\d.]+s)(?:\s+\+([\d.]+s))?$", cleaned)
        if m:
            rows.append(
                {
                    "unit": m.group(1),
                    "active_s": float(m.group(2).rstrip("s")),
                    "duration_s": float(m.group(3).rstrip("s")) if m.group(3) else 0.0,
                }
            )
    return rows


def parse_systemd_analyze(text: str) -> dict:
    """Parse `systemd-analyze` boot summary (4-part and 2-part variants)."""
    m = re.search(
        r"([\d.]+)s\s+\(firmware\)\s*\+\s*([\d.]+)s\s+\(loader\)\s*\+\s*"
        r"([\d.]+)s\s+\(kernel\)\s*\+\s*([\d.]+)s\s+\(userspace\)\s*=\s*([\d.]+)s",
        text,
    )
    if m:
        fw, loader, kernel, userspace, total = (float(g) for g in m.groups())
        return {
            "firmware": fw,
            "loader": loader,
            "kernel": kernel,
            "userspace": userspace,
            "total": total,
        }
    m = re.search(
        r"([\d.]+)s\s+\(kernel\)\s*\+\s*([\d.]+)s\s+\(userspace\)\s*=\s*([\d.]+)s",
        text,
    )
    if m:
        kernel, userspace, total = (float(g) for g in m.groups())
        return {
            "kernel": kernel,
            "userspace": userspace,
            "total": total,
        }
    return {}


# ---------------------------------------------------------------- phase 2


def parse_modules(text: str) -> list[dict]:
    """Parse /proc/modules content into [{name, size, refcount, used_by}]."""
    modules = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        name = fields[0]
        try:
            size = int(fields[1])
            refcount = int(fields[2])
        except ValueError:
            continue
        used_by: list[str] = []
        tail = fields[3:]
        for token in tail:
            if token in ("Live", "OBSOLETE", "CRC"):
                break
            used_by.extend(m.rstrip(",") for m in token.split(",") if m)
        modules.append({"name": name, "size": size, "refcount": refcount, "used_by": used_by})
    return modules


_BOOT_RE = re.compile(r"^\s*(-?\d+)\s+([0-9a-fA-F]+)\s+(.*)$")
_JOURNAL_TS_RE = re.compile(r"([A-Za-z]{3} \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \S+)")
_TS_BODY_RE = re.compile(r"^[A-Za-z]{3} (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def _parse_journal_dt(text: str):
    """Best-effort parse of a journal date like 'Sat 2026-08-01 09:00:00 CEST'."""
    m = _TS_BODY_RE.match(text.strip())
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def parse_boots(text: str) -> list[dict]:
    """Parse `journalctl --list-boots` output into boot-history dicts.

    Each boot gets its index, boot id, first/last entry timestamps and the
    derived duration (None when either timestamp is missing or unparseable).
    Handles both the em-dash and space-separated timestamp formats.
    """
    boots = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.lower().startswith(("idx", "boot")):
            continue
        m = _BOOT_RE.match(line)
        if not m:
            continue
        idx = int(m.group(1))
        boot_id = m.group(2)
        matches = _JOURNAL_TS_RE.findall(m.group(3))
        start_s = matches[0].strip() if matches else None
        end_s = matches[1].strip() if len(matches) > 1 else None
        start = _parse_journal_dt(start_s) if start_s else None
        end = _parse_journal_dt(end_s) if end_s else None
        duration = None
        if start and end:
            duration = int(max(0, (end - start).total_seconds()))
        boots.append(
            {
                "idx": idx,
                "boot_id": boot_id,
                "start": start_s,
                "end": end_s,
                "duration_s": duration,
            }
        )
    return boots


_TD_KEYS = {
    "Local time": "local_time",
    "Universal time": "universal_time",
    "RTC time": "rtc_time",
    "Time zone": "time_zone",
    "System clock synchronized": "synchronized",
    "NTP service": "ntp_service",
    "RTC in local TZ": "rtc_in_local_tz",
}


def parse_timedatectl(text: str) -> dict:
    """Parse `timedatectl` output into a normalized key/value dict."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        out[_TD_KEYS.get(key, key.lower().replace(" ", "_"))] = value.strip()
    return out


_PRIORITY_NAMES = ("emerg", "alert", "crit", "err", "warning", "notice", "info", "debug")


def parse_journal_json(lines) -> list[dict]:
    """Parse `journalctl -o json` lines into [{ts, priority, level, unit, message}].

    Lines that are not valid JSON objects are skipped; missing fields get
    safe defaults so one odd record never breaks the whole batch.
    """
    rows = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        try:
            ts = int(rec.get("__REALTIME_TIMESTAMP") or 0) / 1_000_000
        except (TypeError, ValueError):
            ts = None
        try:
            priority = int(rec.get("PRIORITY", "6"))
        except (TypeError, ValueError):
            priority = 6
        priority = max(0, min(7, priority))
        unit = (
            rec.get("_SYSTEMD_UNIT")
            or rec.get("_COMM")
            or rec.get("SYSLOG_IDENTIFIER")
            or "—"
        )
        rows.append(
            {
                "ts": ts,
                "priority": priority,
                "level": _PRIORITY_NAMES[priority],
                "unit": unit,
                "message": rec.get("MESSAGE", ""),
            }
        )
    return rows


_CRON_ALIASES = {
    "@reboot": "reboot",
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}

_ENV_LINE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*=")


def parse_crontab(text: str) -> list[dict]:
    """Parse a crontab file into schedule entries.

    Handles 5-field schedules, @-aliases, comments, blank lines and
    environment assignments. Malformed lines are reported as error entries
    instead of failing the whole parse.
    """
    entries = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or _ENV_LINE_RE.match(stripped):
            continue
        if stripped.startswith("@"):
            name, _, command = stripped.partition(" ")
            spec = _CRON_ALIASES.get(name)
            if spec is None or not command.strip():
                entries.append({"error": f"unrecognized schedule alias: {stripped}"})
                continue
            if spec == "reboot":
                entries.append(
                    {
                        "minute": None,
                        "hour": None,
                        "day": None,
                        "month": None,
                        "dow": None,
                        "command": command.strip(),
                    }
                )
                continue
            fields = spec.split()
            entries.append(
                {
                    "minute": fields[0],
                    "hour": fields[1],
                    "day": fields[2],
                    "month": fields[3],
                    "dow": fields[4],
                    "command": command.strip(),
                }
            )
            continue
        fields = stripped.split(None, 5)
        if len(fields) < 6:
            entries.append({"error": f"malformed line: {stripped}"})
            continue
        entries.append(
            {
                "minute": fields[0],
                "hour": fields[1],
                "day": fields[2],
                "month": fields[3],
                "dow": fields[4],
                "command": fields[5],
            }
        )
    return entries


_TIMER_RE = re.compile(
    r"^(?P<next>n/a|[A-Za-z]{3} \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \S+)"
    r"\s+(?P<left>\S+(?: \S+){0,2}|n/a)"
    r"\s+(?P<last>n/a|[A-Za-z]{3} \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \S+)"
    r"\s+(?P<passed>\S+(?: \S+){0,2}|n/a)"
    r"\s+(?P<unit>\S+)\s+(?P<activates>\S+)$"
)


def parse_timers(text: str) -> list[dict]:
    """Parse `systemctl list-timers --plain --no-legend --no-pager` output.

    NEXT/LAST timestamps contain spaces, so columns are extracted with an
    anchored regex rather than whitespace splitting. Stops at the optional
    'TIMERS WITH NO SCHEDULE' second table.
    """
    rows = []
    for line in text.splitlines():
        if line.startswith("TIMERS WITH NO SCHEDULE"):
            break
        m = _TIMER_RE.match(line)
        if not m:
            continue
        rows.append(
            {
                "next": m.group("next"),
                "left": m.group("left"),
                "last": m.group("last"),
                "passed": m.group("passed"),
                "unit": m.group("unit"),
                "activates": m.group("activates"),
            }
        )
    return rows


_POWER_UNITS = (
    (("\u00b5Ah", "uAh"), 1e-6),
    (("\u00b5Wh", "uWh"), 1e-6),
    (("\u00b5V", "uV"), 1e-6),
    (("\u00b5A", "uA"), 1e-6),
    (("\u00b5W", "uW"), 1e-6),
    (("mAh",), 1e-3),
    (("mWh",), 1e-3),
    (("mA",), 1e-3),
    (("mV",), 1e-3),
    (("mW",), 1e-3),
    (("Ah",), 1.0),
    (("Wh",), 1.0),
    (("A",), 1.0),
    (("V",), 1.0),
    (("W",), 1.0),
)

#: sysfs values are bare numbers whose unit is documented per attribute
#: (e.g. current_now is always µA) — applied when no suffix is present.
_POWER_KEY_UNITS = {
    "charge_now": 1e-6,
    "charge_full": 1e-6,
    "charge_full_design": 1e-6,
    "energy_now": 1e-6,
    "energy_full": 1e-6,
    "energy_full_design": 1e-6,
    "current_now": 1e-6,
    "voltage_now": 1e-6,
    "power_now": 1e-6,
}


def parse_power_supply(text: str) -> dict:
    """Parse sysfs power-supply `key=value` text into a normalized dict.

    Currents (µA), voltages (µV), energies (µWh) and powers (µW) are
    converted to base SI units (A, V, Wh, W) — whether the value carries an
    explicit suffix or relies on the documented per-attribute unit. Non-
    numeric values (e.g. ``status=Discharging``) are kept as strings; parse
    failures are skipped.
    """
    out: dict = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        # sysfs uevent lines carry an uppercase POWER_SUPPLY_ prefix; attribute
        # files are bare lowercase names. Normalize to the lowercase short key.
        if key.startswith("POWER_SUPPLY_"):
            key = key[len("POWER_SUPPLY_") :]
        key = key.lower()
        value = value.strip()
        if not value:
            continue
        converted = False
        for suffixes, factor in _POWER_UNITS:
            if value.endswith(suffixes):
                try:
                    out[key] = float(value[: -len(suffixes[0])]) * factor
                except ValueError:
                    pass
                converted = True
                break
        if converted:
            continue
        if key in _POWER_KEY_UNITS:
            try:
                out[key] = float(value) * _POWER_KEY_UNITS[key]
                continue
            except ValueError:
                pass
        try:
            out[key] = float(value)
        except ValueError:
            out[key] = value
    return out
