"""Phase 1 tests: deepened commands (info, net, proc, pkg, tweak, sec, clean,
service, backup, health) — hermetic, no root, no network."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest
from click.testing import CliRunner

import lx.__main__ as m
from lx.commands import backup, clean, health, sec, service, tweak
from lx.utils.parse import (
    parse_arp_table,
    parse_authorized_keys,
    parse_blame,
    parse_critical_chain,
    parse_docker_df,
    parse_fstrim,
    parse_passwd,
    parse_ping,
    parse_pkg_list,
    parse_systemd_analyze,
)

runner = CliRunner()


def _invoke(args):
    return runner.invoke(m.cli, args)


# ---------------------------------------------------------------- parsers


def test_parse_ping_iputils():
    text = (
        "PING localhost (127.0.0.1) 56(84) bytes of data.\n"
        "64 bytes from 127.0.0.1: icmp_seq=1 ttl=64 time=0.042 ms\n"
        "64 bytes from 127.0.0.1: icmp_seq=2 ttl=64 time=0.035 ms\n"
        "--- localhost ping statistics ---\n"
        "2 packets transmitted, 2 received, 0% packet loss, time 1017ms\n"
        "rtt min/avg/max/mdev = 0.035/0.038/0.042/0.003 ms\n"
    )
    data = parse_ping(text)
    assert data["sent"] == 2
    assert data["received"] == 2
    assert data["loss_percent"] == 0
    assert data["avg_ms"] == pytest.approx(0.038)
    assert data["min_ms"] == pytest.approx(0.035)
    assert data["jitter_ms"] == pytest.approx(0.003)
    assert len(data["rtts"]) == 2


def test_parse_ping_loss():
    text = "3 packets transmitted, 1 received, 66.6667% packet loss, time 2002ms\n"
    data = parse_ping(text)
    assert data["received"] == 1
    assert data["sent"] == 3
    assert data["loss_percent"] == pytest.approx(66.6667)


def test_parse_arp_table():
    text = (
        "IP address       HW type     Flags       HW address            Mask     Device\n"
        "192.168.1.1      0x1         0x2         c0:49:43:34:63:87     *        wlp0s20f3\n"
        "192.168.1.99     0x1         0x0         00:00:00:00:00:00     *        wlp0s20f3\n"
    )
    rows = parse_arp_table(text)
    assert len(rows) == 2
    assert rows[0]["complete"] is True
    assert rows[0]["ip"] == "192.168.1.1"
    assert rows[1]["complete"] is False


def test_parse_passwd():
    text = (
        "root:x:0:0:root:/root:/bin/bash\n"
        "daemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin\n"
        "seemoo:x:1000:1000:Seemoo:/home/seemoo:/bin/zsh\n"
        "guest:x:1001:1001:Guest:/home/guest:/bin/false\n"
    )
    rows = parse_passwd(text)
    assert len(rows) == 4
    assert rows[0]["is_login_shell"] is True
    assert rows[1]["is_login_shell"] is False
    assert rows[2]["is_login_shell"] is True
    assert rows[3]["is_login_shell"] is False
    assert rows[2]["home"] == "/home/seemoo"


def test_parse_authorized_keys_options_prefix():
    text = (
        "# comment line\n"
        'no-pty,command="/usr/bin/top" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI admin@host\n'
        "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ user@laptop\n"
    )
    keys = parse_authorized_keys(text)
    assert len(keys) == 2
    assert keys[0]["type"] == "ssh-ed25519"
    assert keys[0]["comment"] == "admin@host"
    assert keys[1]["type"] == "ssh-rsa"
    assert keys[1]["comment"] == "user@laptop"


def test_parse_pkg_list_apt():
    rows = parse_pkg_list(
        "Listing... Done\npython3/now 3.12.3-1 amd64 [installed]\nzsh/now 5.9-1 amd64 [installed]\n",
        "apt",
    )
    assert rows == [
        {"name": "python3", "version": "3.12.3-1"},
        {"name": "zsh", "version": "5.9-1"},
    ]


def test_parse_pkg_list_pacman():
    rows = parse_pkg_list("python 3.12.2-1\nzsh 5.9-2\n", "pacman")
    assert len(rows) == 2
    assert rows[0]["name"] == "python"
    assert rows[0]["version"] == "3.12.2-1"


def test_parse_fstrim():
    rows = parse_fstrim("/: 123.4 MiB (129504256 bytes) trimmed\n/boot: 0 B (0 bytes) trimmed\n")
    assert len(rows) == 2
    assert rows[0]["mount"] == "/"
    assert rows[0]["bytes"] == 129504256


def test_parse_docker_df():
    rows = parse_docker_df(
        "TYPE            TOTAL     ACTIVE    SIZE      RECLAIMABLE\n"
        "Images          12        3         1.2GB     800.5MB (66%)\n"
        "Containers      4         2         50MB      25MB (50%)\n"
        "Local Volumes   2         1         100MB     100MB (100%)\n"
        "Build Cache     30        0         0B        0B\n"
    )
    assert len(rows) == 4
    assert rows[0]["type"] == "Images"
    assert rows[0]["reclaimable_bytes"] == pytest.approx(800.5 * 1024**2)
    assert rows[2]["type"] == "Local Volumes"


def test_parse_blame():
    rows = parse_blame("6.682s plymouth-quit-wait.service\n3.475s snapd.seeded.service\n")
    assert len(rows) == 2
    assert rows[0] == {"time_ms": 6682, "unit": "plymouth-quit-wait.service"}
    assert rows[1] == {"time_ms": 3475, "unit": "snapd.seeded.service"}


def test_parse_critical_chain():
    rows = parse_critical_chain(
        "graphical.target @11.640s\n└─multi-user.target @11.640s\n  └─plymouth-quit-wait.service @4.960s +6.682s\n"
    )
    assert len(rows) == 3
    assert rows[0]["unit"] == "graphical.target"
    assert rows[2]["unit"] == "plymouth-quit-wait.service"
    assert rows[2]["active_s"] == pytest.approx(4.960)
    assert rows[2]["duration_s"] == pytest.approx(6.682)


def test_parse_systemd_analyze_four_part():
    data = parse_systemd_analyze(
        "Startup finished in 3.592s (firmware) + 6.162s (loader) + 1.535s (kernel) + 11.674s (userspace) = 22.965s\n"
    )
    assert data["firmware"] == pytest.approx(3.592)
    assert data["loader"] == pytest.approx(6.162)
    assert data["kernel"] == pytest.approx(1.535)
    assert data["total"] == pytest.approx(22.965)


def test_parse_systemd_analyze_two_part():
    data = parse_systemd_analyze(
        "Startup finished in 1.500s (kernel) + 8.000s (userspace) = 9.500s\n"
    )
    assert data["kernel"] == pytest.approx(1.5)
    assert data["userspace"] == pytest.approx(8.0)
    assert data["total"] == pytest.approx(9.5)
    assert "firmware" not in data


# ---------------------------------------------------------------- tweak


def test_tweak_parse_size():
    assert tweak._parse_size("2G") == 2 * 1024**3
    assert tweak._parse_size("512M") == 512 * 1024**2
    assert tweak._parse_size("1048576") == 1048576
    assert tweak._parse_size("banana") is None
    assert tweak._parse_size("") is None


def test_tweak_collect_io_scheduler_monkeypatched(tmp_path, monkeypatch):
    (tmp_path / "sda").mkdir(parents=True)
    qdir = tmp_path / "sda" / "queue"
    qdir.mkdir()
    (qdir / "scheduler").write_text("[mq-deadline] none")
    monkeypatch.setattr(tweak.Path, "glob", lambda self, pat: [qdir / "scheduler"])
    data = tweak._collect_io_scheduler()
    assert data["devices"] == [{"device": "sda", "current": "mq-deadline", "available": ["none"]}]


def test_tweak_swap_status_fstab_marker(tmp_path, monkeypatch):
    fstab = tmp_path / "fstab"
    fstab.write_text("/dev/root / ext4 defaults 0 1\n")
    monkeypatch.setattr(tweak, "_FSTAB", fstab)
    assert tweak._collect_swap_status()["fstab_managed"] is False
    assert tweak._fstab_add_swap() is True
    assert tweak._FSTAB_MARKER in fstab.read_text()
    assert tweak._fstab_remove_swap() is True
    assert tweak._FSTAB_MARKER not in fstab.read_text()


# ---------------------------------------------------------------- sec


def test_sec_users_parses_passwd(tmp_path, monkeypatch):
    passwd = tmp_path / "passwd"
    passwd.write_text(
        "root:x:0:0:root:/root:/bin/bash\n"
        "seemoo:x:1000:1000:Seemoo:/home/seemoo:/bin/zsh\n"
        "daemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin\n"
    )
    group = tmp_path / "group"
    group.write_text("sudo:x:27:seemoo\nwheel:x:10:\n")
    data = sec._collect_users(passwd_text=passwd.read_text(), group_text=group.read_text())
    assert data["count"] == 3
    assert data["login_count"] == 2
    assert data["sudo_members"] == ["seemoo"]


def test_sec_worldwritable_scans(tmp_path):
    world_file = tmp_path / "world"
    world_file.write_text("x")
    world_file.chmod(0o666)
    safe_file = tmp_path / "safe"
    safe_file.write_text("x")
    safe_file.chmod(0o644)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(sec, "_WORLD_WRITABLE_ROOTS", (str(tmp_path),))
    try:
        data = sec._collect_worldwritable()
    finally:
        monkeypatch.undo()
    assert data["count"] == 1
    assert data["findings"][0]["path"] == str(world_file)


def test_sec_sshkeys_world_readable(tmp_path, monkeypatch):
    home = tmp_path / "home" / "alice"
    (home / ".ssh").mkdir(parents=True)
    keys = home / ".ssh" / "authorized_keys"
    keys.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI alice@host\n")
    keys.chmod(0o644)
    passwd_text = f"alice:x:1000:1000:Alice:{home}:/bin/bash\n"
    data = sec._collect_sshkeys(passwd_text=passwd_text)
    assert len(data["users"]) == 1
    assert data["users"][0]["count"] == 1
    assert data["users"][0]["world_readable"] is True


def test_sec_audit_md_format():
    data = {
        "findings": [("SSH", "high", "PermitRootLogin is yes")],
        "summary": {"high": 1},
        "sections": {
            "ssh": {"config_present": True, "config": {"Port": "22"}},
            "ports": {
                "listening": [{"proto": "tcp", "local": "0.0.0.0:22", "pid": 1, "process": "sshd"}]
            },
            "sudoers": {"users": ["seemoo"]},
            "suid": {"hits": [], "count": 0},
        },
    }
    from io import StringIO

    from rich.console import Console

    buf = StringIO()
    sec._render_md(Console(file=buf, force_terminal=False, width=120), data)
    out = buf.getvalue()
    assert "# lx security audit" in out
    assert "| Area | Severity | Issue |" in out
    assert "| SSH | high | PermitRootLogin is yes |" in out
    assert "| Port | 22 |" in out
    assert "0.0.0.0:22" in out


def test_sec_audit_md_flag_wired(monkeypatch):
    fake = {
        "findings": [("SSH", "high", "PermitRootLogin is yes")],
        "summary": {"high": 1},
        "sections": {
            "ssh": {"config_present": True, "config": {"Port": "22"}},
            "ports": {
                "listening": [{"proto": "tcp", "local": "0.0.0.0:22", "pid": 1, "process": "sshd"}]
            },
            "sudoers": {"users": ["seemoo"]},
            "suid": {"hits": [], "count": 0},
        },
    }
    monkeypatch.setattr(sec, "_collect_audit", lambda no_suid=False: fake)
    r = _invoke(["sec", "audit", "--format", "md"])
    assert r.exit_code == 0
    assert "# lx security audit" in r.output


def test_sec_audit_format_rejects_invalid():
    r = _invoke(["sec", "audit", "--format", "html"])
    assert r.exit_code == 2
    assert "Invalid value" in r.output


def test_sec_collect_ssh_unreadable_dropin_does_not_crash(tmp_path):
    (tmp_path / "sshd_config").write_text("PermitRootLogin yes\n")
    dropins = tmp_path / "sshd_config.d"
    dropins.mkdir()
    locked = dropins / "50-locked.conf"
    locked.write_text("PasswordAuthentication yes\n")
    locked.chmod(0)
    try:
        data = sec._collect_ssh(ssh_dir=tmp_path)
    finally:
        locked.chmod(0o644)
    assert data["config_present"] is True
    assert data["config"]["PermitRootLogin"] == "yes"
    assert data["config"]["PasswordAuthentication"] == ""


def test_sec_collect_ssh_missing_dir_is_graceful(tmp_path):
    data = sec._collect_ssh(ssh_dir=tmp_path / "does-not-exist")
    assert data["config_present"] is False
    assert data["findings"]


def test_sec_collect_suid_unreadable_dir_does_not_crash(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "suid-probe").write_text("x")
    locked.chmod(0)
    try:
        data = sec._collect_suid(roots=[tmp_path])
    finally:
        locked.chmod(0o755)
    assert isinstance(data["count"], int)
    assert data["hits"] == []


def test_sec_collect_sshkeys_unreadable_host_key_does_not_crash(tmp_path):
    pub = tmp_path / "ssh_host_rsa_key.pub"
    pub.write_text("ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ fake root@host\n")
    pub.chmod(0)
    try:
        data = sec._collect_sshkeys(passwd_text="", ssh_dir=tmp_path)
    finally:
        pub.chmod(0o644)
    assert data["host_keys"] == []
    assert data["users"] == []


# ---------------------------------------------------------------- clean


def test_clean_report_recommendations(tmp_path, monkeypatch):
    pip_cache = tmp_path / ".cache" / "pip"
    pip_cache.mkdir(parents=True)
    (pip_cache / "wheels").write_bytes(b"x" * (60 * 1024 * 1024))
    monkeypatch.setattr(clean.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        clean,
        "_collect_docker_df",
        lambda: {"ok": False, "skipped": True, "reclaimable": 0, "rows": []},
    )
    monkeypatch.setattr(
        clean, "_collect_snap_disabled", lambda: [{"name": "firefox", "rev": "100"}]
    )
    monkeypatch.setattr(clean, "_journal_size", lambda: 0)
    data = clean._collect_report()
    recs = "\n".join(data["recommendations"])
    assert "lx clean pip" in recs
    assert "lx clean snap" in recs


def test_clean_tmp_own_files_only(tmp_path, monkeypatch):
    import os

    from lx.commands import clean as clean_mod

    real_getuid = os.getuid
    mine = tmp_path / "mine"
    mine.mkdir()
    old = mine / "file"
    old.write_text("x")
    os.utime(old, (1, 1))  # ancient
    fresh = mine / "fresh"
    fresh.write_text("y")
    monkeypatch.setattr(clean_mod.os, "getuid", real_getuid)
    monkeypatch.setattr(
        clean_mod.Path,
        "iterdir",
        lambda self: iter([old, fresh]) if str(self) == str(tmp_path) else iter([]),
    )
    scanned, candidates, total = clean_mod._collect_tmp_candidates(days=10, tmp_dir=tmp_path)
    assert scanned == 2
    assert [c["path"] for c in candidates] == [str(old)]
    assert total == len(b"x")


def test_clean_pip_dry_run_json(tmp_path, monkeypatch):
    pip_cache = tmp_path / ".cache" / "pip"
    pip_cache.mkdir(parents=True)
    (pip_cache / "w").write_bytes(b"x" * 4096)
    monkeypatch.setattr(clean.Path, "home", classmethod(lambda cls: tmp_path))
    result = _invoke(["--json", "clean", "pip", "--dry-run"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["data"]["dry_run"] is True
    assert payload["data"]["bytes"] == 4096


# ---------------------------------------------------------------- service


def test_service_user_flag_skips_root_gate(monkeypatch):
    monkeypatch.setattr(service, "is_root", lambda: False)
    monkeypatch.setattr(
        service,
        "run",
        lambda args, timeout=30: type("R", (), {"ok": True, "stdout": "", "stderr": ""})(),
    )
    data = service._svc_action_collect("stop", "myservice.service", user=True)
    assert data["ok"] is True
    assert data["user"] is True


def test_service_action_requires_root():
    data = service._svc_action_collect("stop", "x.service", user=False)
    assert data["ok"] is False
    assert "requires root" in data["stderr"]


# ---------------------------------------------------------------- backup


def test_backup_collect_create_exclude(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    dot = home / ".bashrc"
    dot.write_text("x")
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "fstab").write_text("x")
    monkeypatch.setattr(
        backup, "_expand", lambda p: Path(p).expanduser() if not p.startswith("~") else home / p[2:]
    )
    monkeypatch.setattr(backup, "DEFAULT_TARGETS", ["~/.bashrc", "/etc/fstab"])
    data = backup._collect_create(str(tmp_path / "dest"), ("~/.zshrc",), ("~/.bashrc",))
    assert data["present"] == 2
    assert data["excluded"] == ["~/.bashrc"]
    data_no_etc = backup._collect_create(str(tmp_path / "dest"), (), no_etc=True)
    assert all(not t.startswith("/etc") for t in data_no_etc["targets"])


def test_backup_verify_ok_and_corrupt(tmp_path):
    archive = tmp_path / "lx-backup-test.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        content = b"hello world" * 100
        import io

        info = tarfile.TarInfo("test.txt")
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    data = backup._collect_verify(str(archive))
    assert data["ok"] is True
    assert data["members"] == 1
    assert data["total_bytes"] >= len(content)

    corrupt = tmp_path / "corrupt.tar.gz"
    corrupt.write_bytes(b"not a tarball")
    data = backup._collect_verify(str(corrupt))
    assert data["ok"] is False
    assert data["error"]


def test_backup_prune_keeps_newest(tmp_path):
    for name in (
        "lx-backup-20260701-000000.tar.gz",
        "lx-backup-20260710-000000.tar.gz",
        "lx-backup-20260720-000000.tar.gz",
    ):
        (tmp_path / name).write_bytes(b"x")
    data = backup._collect_prune(str(tmp_path), keep=2)
    assert len(data["removed"]) == 1
    assert data["removed"][0]["name"] == "lx-backup-20260701-000000.tar.gz"
    assert len(data["kept"]) == 2


def test_backup_restore_dest(tmp_path, monkeypatch):
    archive = tmp_path / "a.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        content = b"data"
        import io

        info = tarfile.TarInfo("dir/file.txt")
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    dest = tmp_path / "dest"
    result = _invoke(["--json", "backup", "restore", str(archive), "--dest", str(dest), "-y"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["data"]["restored"] is True
    assert (dest / "dir" / "file.txt").read_bytes() == b"data"


# ---------------------------------------------------------------- health


def test_health_weighted_overall(monkeypatch):
    monkeypatch.setattr(health, "_check_cpu", lambda: (100, "ok"))
    monkeypatch.setattr(health, "_check_mem", lambda: (50, "ok"))
    monkeypatch.setattr(health, "_check_disk", lambda: (100, "ok"))
    monkeypatch.setattr(health, "_check_failed_units", lambda: (100, "ok"))
    monkeypatch.setattr(health, "_check_connectivity", lambda: (100, "ok"))
    monkeypatch.setattr(health, "_check_updates", lambda: (100, "ok"))
    monkeypatch.setattr(health, "_check_temps", lambda: (100, "ok"))
    monkeypatch.setattr(health, "_check_battery", lambda: (100, "no battery detected"))
    monkeypatch.setattr(health, "_check_swap", lambda: (100, "ok"))
    monkeypatch.setattr(health, "_check_uptime", lambda: (100, "ok"))
    monkeypatch.setattr(health, "_check_zombies", lambda: (100, "ok"))
    data = health._collect(("CPU load", "Memory", "Battery"))
    assert data["overall"] == (100 * 25 + 50 * 20 + 100 * 0) // 45
    assert data["weight_total"] == 45
    battery = [c for c in data["checks"] if c["name"] == "Battery"]
    assert battery[0]["weight"] == 0  # optional check dropped


def test_health_check_filter_invalid_exits_2():
    result = _invoke(["--json", "health", "--check", "bogus"])
    assert result.exit_code == 2
    assert "unknown check" in result.output


def test_health_check_filter_short_names():
    result = _invoke(["--json", "health", "--check", "cpu,mem"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    names = [c["name"] for c in payload["data"]["checks"]]
    assert names == ["CPU load", "Memory"]


# ---------------------------------------------------------------- CLI wiring


def test_sec_new_subcommands_exist():
    result = _invoke(["--json", "sec", "users"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["command"] == "sec users"
    assert isinstance(payload["data"]["users"], list)


def test_clean_report_json_has_recommendations():
    result = _invoke(["--json", "clean", "report"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "recommendations" in payload["data"]
    assert "targets" in payload["data"]


def test_backup_verify_cli_wiring(tmp_path):
    archive = tmp_path / "ok.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        import io

        content = b"x" * 100
        info = tarfile.TarInfo("f")
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    result = _invoke(["--json", "backup", "verify", str(archive)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["data"]["ok"] is True


def test_service_blame_json():
    result = _invoke(["--json", "service", "blame", "-n", "3"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["data"]["ok"] is True
    assert isinstance(payload["data"]["units"], list)


def test_health_new_checks_present():
    result = _invoke(["--json", "health"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    names = [c["name"] for c in payload["data"]["checks"]]
    assert "Connectivity" in names
    assert "Updates" in names
    assert "Battery" in names
