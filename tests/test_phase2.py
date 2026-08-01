"""Phase 2 tests: fs, sys, bench, log, cron, power collectors and parsers.

All tests are hermetic: they feed fixtures into collectors and parsers and
monkeypatch subprocess-backed paths, so they run on any host (including CI
runners without systemd, journalctl, or root).
"""

from __future__ import annotations

import json

import click
import pytest

from lx.commands import bench as bench_cmd
from lx.commands import cron as cron_cmd
from lx.commands import fs as fs_cmd
from lx.commands import log as log_cmd
from lx.commands import power as power_cmd
from lx.commands import sys as sys_cmd
from lx.utils import parse
from lx.utils.shell import Result

# ------------------------------------------------------------------ parsers


def test_parse_modules() -> None:
    text = (
        "nf_tables 262144 2 nfnetlink,iptable_nat, Live 0xffffffffc0000000\n"
        "ext4 1024000 3 Live 0xffffffffc1000000\n"
    )
    modules = parse.parse_modules(text)
    assert [m["name"] for m in modules] == ["nf_tables", "ext4"]
    assert modules[0]["size"] == 262144
    assert modules[0]["refcount"] == 2
    assert modules[0]["used_by"] == ["nfnetlink", "iptable_nat"]
    assert modules[1]["used_by"] == []


def test_parse_modules_bad_line_skipped() -> None:
    assert parse.parse_modules("garbage line without numbers\n") == []


_BOOTS_SPACE = (
    "-1 b3fd9b16eafe4f1789df7a0abd608b2d Wed 2026-07-29 16:24:46 +01 "
    "Thu 2026-07-30 05:49:14 +01\n"
    " 0 8f4291df63ef4f6d95e1727d4aa12ac4 Thu 2026-07-30 23:09:03 +01 "
    "Sat 2026-08-01 22:13:36 +01\n"
)
_BOOTS_DASH = (
    "0 8f4291df63ef4f6d95e1727d4aa12ac4 Thu 2026-07-30 23:09:03 CEST \u2014 "
    "Fri 2026-07-31 02:00:00 CEST\n"
)


def test_parse_boots_space_separated() -> None:
    boots = parse.parse_boots(_BOOTS_SPACE)
    assert [b["idx"] for b in boots] == [-1, 0]
    first = boots[0]
    assert first["boot_id"].startswith("b3fd9b16")
    assert first["start"].startswith("Wed 2026-07-29")
    assert first["end"].startswith("Thu 2026-07-30")
    assert first["duration_s"] == 13 * 3600 + 24 * 60 + 28
    assert boots[1]["duration_s"] == 47 * 3600 + 4 * 60 + 33


def test_parse_boots_em_dash_separated() -> None:
    boots = parse.parse_boots(_BOOTS_DASH)
    assert len(boots) == 1
    assert boots[0]["idx"] == 0
    assert boots[0]["start"].startswith("Thu 2026-07-30")
    assert boots[0]["end"].startswith("Fri 2026-07-31")
    assert boots[0]["duration_s"] == 2 * 3600 + 50 * 60 + 57


def test_parse_boots_headers_and_garbage_ignored() -> None:
    boots = parse.parse_boots("IDX BOOT ID FIRST ENTRY LAST ENTRY\n\n" + _BOOTS_DASH + "garbage\n")
    assert len(boots) == 1


_TIMERS = (
    "Sat 2026-08-01 22:20:00 +01     5min Sat 2026-08-01 22:10:06 +01 4min 47s ago "
    "sysstat-collect.timer          sysstat-collect.service\n"
    "n/a n/a n/a n/a btrfs-dedupe.timer btrfs-dedupe.service\n"
    "\n"
    "TIMERS WITH NO SCHEDULE:\n"
    "0 timers listed.\n"
)


def test_parse_timers() -> None:
    timers = parse.parse_timers(_TIMERS)
    assert [t["unit"] for t in timers] == ["sysstat-collect.timer", "btrfs-dedupe.timer"]
    t = timers[0]
    assert t["next"] == "Sat 2026-08-01 22:20:00 +01"
    assert t["left"] == "5min"
    assert t["last"] == "Sat 2026-08-01 22:10:06 +01"
    assert t["passed"] == "4min 47s ago"
    assert t["activates"] == "sysstat-collect.service"


def test_parse_timers_stops_at_no_schedule_section() -> None:
    assert len(parse.parse_timers(_TIMERS)) == 2


_CRONTAB = """\
# comment line
SHELL=/bin/bash
MAILTO=root@example.com

@reboot /usr/bin/example --flag
15 * * * *  /usr/local/bin/backup.sh
*/5 * * * *  echo hello > /tmp/x
"""


def test_parse_crontab() -> None:
    jobs = parse.parse_crontab(_CRONTAB)
    assert len(jobs) == 3
    assert jobs[0]["minute"] is None
    assert jobs[0]["command"] == "/usr/bin/example --flag"
    assert jobs[1] == {
        "minute": "15",
        "hour": "*",
        "day": "*",
        "month": "*",
        "dow": "*",
        "command": "/usr/local/bin/backup.sh",
    }
    assert jobs[2]["command"] == "echo hello > /tmp/x"


def test_parse_crontab_ignores_comments_and_env_lines() -> None:
    jobs = parse.parse_crontab(_CRONTAB)
    assert all("SHELL" not in j.get("command", "") for j in jobs)


def test_parse_crontab_malformed_line_reported() -> None:
    jobs = parse.parse_crontab("garbage\n")
    assert jobs == [{"error": "malformed line: garbage"}]


def test_parse_crontab_unknown_alias_reported() -> None:
    jobs = parse.parse_crontab("@sometimes /bin/true\n")
    assert jobs[0]["error"].startswith("unrecognized schedule alias")


_TIMEDATECTL = """\
               Local time: Sat 2026-08-01 22:18:59 +01
           Universal time: Sat 2026-08-01 21:18:59 UTC
                 RTC time: Sat 2026-08-01 21:18:59
                Time zone: Africa/Casablanca (+01, +0100)
System clock synchronized: yes
              NTP service: active
           RTC in local TZ: no
"""


def test_parse_timedatectl() -> None:
    data = parse.parse_timedatectl(_TIMEDATECTL)
    assert data["local_time"] == "Sat 2026-08-01 22:18:59 +01"
    assert data["time_zone"] == "Africa/Casablanca (+01, +0100)"
    assert data["synchronized"] == "yes"
    assert data["ntp_service"] == "active"


_JOURNAL = [
    {"__CURSOR": "x1", "_PID": "1", "_CMDLINE": "init", "PRIORITY": "5", "MESSAGE": "hi"},
    {"__CURSOR": "x2", "_PID": "2", "_CMDLINE": "init", "PRIORITY": "9", "MESSAGE": "odd"},
    {"__CURSOR": "x3", "_PID": "3", "_CMDLINE": "init", "PRIORITY": "2", "MESSAGE": "warn"},
]


def test_parse_journal_json() -> None:
    entries = parse.parse_journal_json([json.dumps(e) for e in _JOURNAL])
    assert len(entries) == 3
    assert entries[0]["priority"] == 5
    assert entries[0]["level"] == "notice"
    assert entries[0]["message"] == "hi"
    assert entries[1]["level"] == "debug"
    assert entries[2]["level"] == "crit"


def test_parse_journal_json_skips_garbage_lines() -> None:
    assert parse.parse_journal_json(["not json"]) == []
    entries = parse.parse_journal_json(["{}"])
    assert len(entries) == 1
    assert entries[0]["level"] == "info"
    assert entries[0]["message"] == ""


def test_parse_power_supply_si_units() -> None:
    """Bare sysfs numbers are per-key units (µV, µA, µWh), not SI."""
    data = parse.parse_power_supply(
        "POWER_SUPPLY_NAME=BAT1\n"
        "POWER_SUPPLY_STATUS=Full\n"
        "POWER_SUPPLY_CAPACITY=100\n"
        "POWER_SUPPLY_VOLTAGE_NOW=12398000\n"
        "POWER_SUPPLY_CURRENT_NOW=52000\n"
        "POWER_SUPPLY_ENERGY_NOW=25200000\n"
        "POWER_SUPPLY_ENERGY_FULL=25200000\n"
        "POWER_SUPPLY_ENERGY_FULL_DESIGN=42600000\n"
        "POWER_SUPPLY_CYCLE_COUNT=0\n"
    )
    assert data["voltage_now"] == pytest.approx(12.398)
    assert data["current_now"] == pytest.approx(0.052)
    assert data["energy_now"] == pytest.approx(25.2)
    assert data["energy_full"] == pytest.approx(25.2)
    assert data["energy_full_design"] == pytest.approx(42.6)


def test_parse_power_supply_percent_values_are_plain() -> None:
    data = parse.parse_power_supply(
        "POWER_SUPPLY_NAME=BAT0\nPOWER_SUPPLY_CAPACITY=42\nPOWER_SUPPLY_STATUS=Discharging\n"
    )
    assert data["capacity"] == 42.0
    assert data["status"] == "Discharging"
    assert "energy_now" not in data


# ------------------------------------------------------------------ fs


def test_fs_usage_tmp_tree(tmp_path) -> None:
    (tmp_path / "big").mkdir()
    (tmp_path / "big" / "a.bin").write_bytes(b"x" * 2048)
    (tmp_path / "big" / "sub").mkdir()
    (tmp_path / "big" / "sub" / "b.bin").write_bytes(b"y" * 512)
    (tmp_path / "small.txt").write_bytes(b"z" * 100)
    data = fs_cmd._collect_usage(str(tmp_path), top_n=10, depth=5)
    assert not data.get("error")
    assert data["total_bytes"] == 2048 + 512 + 100
    top = {r["path"]: r["bytes"] for r in data["top"]}
    assert top[str(tmp_path / "big")] == 2560
    assert top[str(tmp_path / "small.txt")] == 100


def test_fs_usage_skips_pseudo_dirs(tmp_path) -> None:
    (tmp_path / "proc").mkdir()
    (tmp_path / "proc" / "kcore").write_bytes(b"x" * 1024)
    (tmp_path / "real.txt").write_bytes(b"x")
    data = fs_cmd._collect_usage(str(tmp_path), top_n=10, depth=5)
    paths = [r["path"] for r in data["top"]]
    assert str(tmp_path / "proc") not in paths
    assert data["total_bytes"] == 1


def test_fs_usage_not_a_directory(tmp_path) -> None:
    data = fs_cmd._collect_usage(str(tmp_path / "missing"), top_n=10, depth=5)
    assert data["error"] == "not a directory"
    assert data["top"] == []


def test_fs_large_heap_filter(tmp_path) -> None:
    for name, size in (("a.bin", 300), ("b.bin", 500), ("c.bin", 700)):
        (tmp_path / name).write_bytes(b"x" * size)
    data = fs_cmd._collect_large(str(tmp_path), top_n=2, min_bytes=400)
    assert [r["path"].rsplit("/", 1)[-1] for r in data["top"]] == ["c.bin", "b.bin"]
    assert data["top"][0]["bytes"] == 700
    assert len(data["top"]) == 2


def test_fs_inodes_tmp_tree(tmp_path) -> None:
    (tmp_path / "d1").mkdir()
    (tmp_path / "d1" / "f.txt").write_text("x")
    (tmp_path / "d2").mkdir()
    data = fs_cmd._collect_inodes(str(tmp_path), top_n=10, depth=5)
    assert data["inodes"]["used"] >= 0
    by_path = {r["path"]: r["count"] for r in data["top"]}
    assert by_path.get(str(tmp_path / "d1"), 0) >= 2


# ------------------------------------------------------------------ sys


def test_sys_collect_usb_sysfs_fallback(tmp_path, monkeypatch) -> None:
    dev = tmp_path / "1-1"
    dev.mkdir()
    (dev / "idVendor").write_text("1234")
    (dev / "idProduct").write_text("5678")
    (dev / "busnum").write_text("1")
    (dev / "devnum").write_text("2")
    monkeypatch.setattr(sys_cmd.shutil, "which", lambda _name: None)
    data = sys_cmd._collect_usb(base=dev.parent)
    assert data["tool"] == "sysfs"
    assert data["count"] == 1
    assert data["devices"][0]["vendor_id"] == "1234"
    assert data["devices"][0]["product"] is None


def test_sys_collect_pci_sysfs_fallback(tmp_path, monkeypatch) -> None:
    dev = tmp_path / "0000:00:02.0"
    dev.mkdir()
    (dev / "vendor").write_text("0x8086")
    (dev / "device").write_text("0x8a56")
    (dev / "class").write_text("0x030000")
    monkeypatch.setattr(sys_cmd.shutil, "which", lambda _name: None)
    data = sys_cmd._collect_pci(base=dev.parent)
    assert data["tool"] == "sysfs"
    assert data["devices"][0]["slot"] == "0000:00:02.0"
    assert data["devices"][0]["vendor_id"] == "0x8086"


def test_sys_collect_env_redacts_secrets(monkeypatch) -> None:
    monkeypatch.setenv("HOME", "/home/tester")
    monkeypatch.setenv("MY_API_TOKEN", "super-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret-key")
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.setenv("SHELL", "/bin/bash")
    data = sys_cmd._collect_env()
    assert set(data["redacted"]) >= {"AWS_SECRET_ACCESS_KEY", "MY_API_TOKEN"}
    values = {v["key"]: v["value"] for v in data["vars"]}
    assert values["MY_API_TOKEN"] == "***"
    assert values["AWS_SECRET_ACCESS_KEY"] == "***"
    assert values["HOME"] == "/home/tester"
    assert all(v["value"] != "***" for v in data["vars"] if v["key"] not in data["redacted"])


def test_sys_collect_boots_error_on_nonzero(monkeypatch) -> None:
    monkeypatch.setattr(
        sys_cmd,
        "run",
        lambda *_a, **_k: Result(returncode=127, stdout="", stderr="", ok=False),
    )
    data = sys_cmd._collect_boots()
    assert data["ok"] is False
    assert "journalctl not found" in data["error"]


def test_sys_collect_boots_ok(monkeypatch) -> None:
    monkeypatch.setattr(
        sys_cmd,
        "run",
        lambda *_a, **_k: Result(returncode=0, stdout=_BOOTS_SPACE, stderr="", ok=True),
    )
    data = sys_cmd._collect_boots()
    assert data["ok"] is True
    assert data["count"] == 2


def test_sys_ntp_servers(tmp_path) -> None:
    conf_a = tmp_path / "timesyncd.conf"
    conf_a.write_text("# NTP=pool.ntp.org\nNTP=0.debian.pool.ntp.org 1.debian.pool.ntp.org\n")
    conf_b = tmp_path / "ntp.conf"
    conf_b.write_text("server ntp.ubuntu.com iburst\npool 2.debian.pool.ntp.org\n")
    servers = sys_cmd._ntp_servers((str(conf_a), str(conf_b)))
    assert servers == [
        "0.debian.pool.ntp.org",
        "1.debian.pool.ntp.org",
        "ntp.ubuntu.com",
        "2.debian.pool.ntp.org",
    ]


# ------------------------------------------------------------------ cron


def test_cron_collect_list_ok(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return Result(returncode=0, stdout=_CRONTAB, stderr="", ok=True)

    monkeypatch.setattr(cron_cmd, "run", fake_run)
    monkeypatch.setattr(
        cron_cmd,
        "_read_crontab_file",
        lambda _path: {"source": "system", "readable": True, "entries": []},
    )
    data = cron_cmd._collect_list(user=None)
    assert data["count"] == 3
    assert data["errors"] == []
    assert data["entries"][0]["command"] == "/usr/bin/example --flag"
    assert calls == [["crontab", "-l"]]


def test_cron_collect_list_no_crontab(monkeypatch) -> None:
    monkeypatch.setattr(
        cron_cmd,
        "run",
        lambda *_a, **_k: Result(
            returncode=1, stdout="", stderr="no crontab for seemoo", ok=False
        ),
    )
    monkeypatch.setattr(
        cron_cmd,
        "_read_crontab_file",
        lambda _path: {"source": "system", "readable": True, "entries": []},
    )
    data = cron_cmd._collect_list(user=None)
    assert data["count"] == 0
    assert data["errors"] == []
    assert data["notes"] == ["no crontab installed for this user"]


def test_cron_collect_list_other_user_requires_root(monkeypatch) -> None:
    monkeypatch.setattr(
        cron_cmd,
        "_read_crontab_file",
        lambda _path: {"source": "system", "readable": True, "entries": []},
    )
    data = cron_cmd._collect_list(user="alice")
    assert data["count"] == 0
    assert any("requires root" in e["error"] for e in data["errors"])


def test_cron_collect_timers_ok(monkeypatch) -> None:
    monkeypatch.setattr(
        cron_cmd,
        "run",
        lambda *_a, **_k: Result(returncode=0, stdout=_TIMERS, stderr="", ok=True),
    )
    data = cron_cmd._collect_timers()
    assert data["ok"] is True
    assert len(data["timers"]) == 2


def test_cron_collect_timers_error(monkeypatch) -> None:
    monkeypatch.setattr(
        cron_cmd,
        "run",
        lambda *_a, **_k: Result(returncode=127, stdout="", stderr="", ok=False),
    )
    data = cron_cmd._collect_timers()
    assert data["ok"] is False
    assert "systemctl" in data["error"]


# ------------------------------------------------------------------ log


def test_log_normalize_priority() -> None:
    assert log_cmd._normalize_priority("err") == 3
    assert log_cmd._normalize_priority("debug") == 7
    assert log_cmd._normalize_priority("2") == 2
    assert log_cmd._normalize_priority(None) is None


def test_log_normalize_priority_bad() -> None:
    with pytest.raises(click.BadParameter):
        log_cmd._normalize_priority("not-a-priority")


def test_log_collect_show(monkeypatch) -> None:
    raw = [json.dumps(e) for e in _JOURNAL]
    monkeypatch.setattr(
        log_cmd,
        "run",
        lambda *_a, **_k: Result(returncode=0, stdout="\n".join(raw), stderr="", ok=True),
    )
    data = log_cmd._collect_show(50, None, None, 7, None, None)
    assert data["ok"] is True
    assert len(data["entries"]) == 3
    assert data["entries"][0]["message"] == "hi"


def test_log_collect_show_invalid_grep(monkeypatch) -> None:
    monkeypatch.setattr(
        log_cmd,
        "run",
        lambda *_a, **_k: Result(returncode=0, stdout="", stderr="", ok=True),
    )
    data = log_cmd._collect_show(50, None, None, 7, None, "[unclosed")
    assert data["ok"] is False
    assert "regex" in data["error"]


def test_log_collect_show_journalctl_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        log_cmd,
        "run",
        lambda *_a, **_k: Result(returncode=127, stdout="", stderr="", ok=False),
    )
    data = log_cmd._collect_show(50, None, None, 7, None, None)
    assert data["ok"] is False
    assert "not running systemd" in data["error"]


def test_log_collect_errors(monkeypatch) -> None:
    raw = [json.dumps(e) for e in _JOURNAL]
    monkeypatch.setattr(
        log_cmd,
        "run",
        lambda *_a, **_k: Result(returncode=0, stdout="\n".join(raw), stderr="", ok=True),
    )
    data = log_cmd._collect_errors(lines=100, since=None, unit=None)
    assert data["ok"] is True
    assert len(data["entries"]) == 3
    assert data["top_units"][0]["count"] == 3


# ------------------------------------------------------------------ power


def test_power_collect_battery_via_sysfs(monkeypatch) -> None:
    monkeypatch.setattr(power_cmd.psutil, "sensors_battery", lambda: None)
    monkeypatch.setattr(
        power_cmd,
        "_sysfs_battery",
        lambda: {
            "voltage_now": 12.398,
            "current_now": 0.052,
            "energy_now": 7.0,
            "energy_full": 7.0,
            "energy_full_design": 11.83,
            "capacity": 100,
            "status": "Full",
            "cycle_count": 0,
        },
    )
    data = power_cmd._collect_battery()
    assert data["present"] is True
    assert data["percent"] == pytest.approx(100.0)
    assert data["status"] == "Full"
    assert data["charge_rate_w"] == pytest.approx(0.64, abs=0.01)
    assert data["design_wh"] == pytest.approx(11.83)
    assert data["cycle_count"] == 0


def test_power_collect_battery_no_battery(monkeypatch) -> None:
    monkeypatch.setattr(power_cmd.psutil, "sensors_battery", lambda: None)
    monkeypatch.setattr(power_cmd, "_sysfs_battery", lambda: {})
    data = power_cmd._collect_battery()
    assert data["present"] is False


def test_power_parse_profiles_list_only_headings() -> None:
    listing = (
        "  performance:\n    CpuDriver:\tintel_pstate\n\n"
        "* balanced:\n    CpuDriver:\tintel_pstate\n\n"
        "  power-saver:\n    PlatformDriver:\tplaceholder\n"
    )
    assert power_cmd._parse_powerprofiles_list(listing) == [
        "performance",
        "balanced",
        "power-saver",
    ]


# ------------------------------------------------------------------ bench


def test_bench_score_bounds() -> None:
    assert bench_cmd._score(0.0, "cpu") == 0
    assert bench_cmd._score(1500.0, "cpu") == 100
    assert bench_cmd._score(5000.0, "cpu") == 100
    assert bench_cmd._score(700.0, "memory") == 100
    assert bench_cmd._score(350.0, "memory") == 50


def test_bench_memory_quick_consistency() -> None:
    data = bench_cmd._bench_memory(256, quick=True)
    assert data["mb"] == 5
    assert data["write_mbps"] > 0
    assert data["read_mbps"] > 0
    assert 0 <= data["score"] <= 100
    expected = sum(float(i) * 1.5 for i in range(5 * 1024 * 1024 // 8))
    assert data["checksum"] == pytest.approx(expected, rel=1e-9)


def test_bench_disk_cleanup(tmp_path) -> None:
    data = bench_cmd._bench_disk(64, str(tmp_path), quick=True)
    assert data["mb"] == 1
    assert data["write_mbps"] > 0
    assert data["read_mbps"] > 0
    assert data["bytes_verified"] == 1024 * 1024
    assert list(tmp_path.glob("lx-bench-*")) == []


def test_bench_cpu_quick() -> None:
    data = bench_cmd._bench_cpu(2.0, threads=1, quick=True)
    assert data["threads"] == 1
    assert data["ops_per_sec"] > 0
    assert data["bytes_hashed"] > 0
    assert 0 <= data["score"] <= 100
