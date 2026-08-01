"""Tiny data parsers shared across commands."""
from __future__ import annotations

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
