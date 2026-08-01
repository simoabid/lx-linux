"""Tests for command module internals (logic, not live system calls)."""

from __future__ import annotations

from lx.commands import backup, clean, pkg, sec, tweak


def test_pkg_detect_backend_returns_tuple() -> None:
    primary, avail = pkg._detect_backend()
    assert isinstance(primary, (str, type(None)))
    assert isinstance(avail, list)


def test_pkg_commands_known_backends() -> None:
    for be in ("apt", "dnf", "pacman", "zypper", "apk", "flatpak", "snap"):
        cmds = pkg._commands(be)
        assert "install" in cmds
        assert "remove" in cmds
        assert "search" in cmds
        assert "list" in cmds


def test_pkg_commands_unknown_backend_empty() -> None:
    assert pkg._commands("nonexistent") == {}


def test_pkg_require_admin_logic() -> None:
    assert pkg._require_admin("apt", "install") is True
    assert pkg._require_admin("apt", "search") is False
    assert pkg._require_admin("flatpak", "install") is False
    assert pkg._require_admin("pacman", "upgrade") is True


def test_tweak_severity_color() -> None:
    assert tweak._sysctl_get("kernel.osrelease")  # always exists on Linux


def test_sec_severity_score() -> None:
    assert sec._severity_score("critical") == 4
    assert sec._severity_score("high") == 3
    assert sec._severity_score("medium") == 2
    assert sec._severity_score("low") == 1
    assert sec._severity_score("info") == 0
    assert sec._severity_score("unknown") == 0


def test_clean_human_bytes() -> None:
    assert clean._human(0) == "0 B"
    assert clean._human(1024) == "1 KB"
    assert clean._human(1048576) == "1 MB"
    assert clean._human(1073741824) == "1 GB"


def test_clean_dir_size_missing(tmp_path) -> None:
    assert clean._dir_size(tmp_path / "nope") == 0


def test_clean_dir_size_files(tmp_path) -> None:
    (tmp_path / "a").write_bytes(b"x" * 100)
    (tmp_path / "b").write_bytes(b"y" * 50)
    assert clean._dir_size(tmp_path) == 150


def test_backup_default_targets_is_list() -> None:
    assert isinstance(backup.DEFAULT_TARGETS, list)
    assert len(backup.DEFAULT_TARGETS) > 5


def test_backup_archive_name(tmp_path) -> None:
    name = backup._archive_name(tmp_path)
    assert name.name.startswith("lx-backup-")
    assert name.name.endswith(".tar.gz")
    assert name.parent == tmp_path


def test_backup_expand() -> None:
    p = backup._expand("~/test")
    assert "~" not in str(p)
