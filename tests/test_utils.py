"""Tests for lx.utils — parsers, shell runner, output helpers."""

from __future__ import annotations

from pathlib import Path

from lx.utils import shell
from lx.utils.parse import (
    count_cpus,
    parse_kv,
    read_first_line,
    read_kv,
    read_text,
)


def test_read_text_missing(tmp_path: Path) -> None:
    assert read_text(tmp_path / "nope") == ""
    assert read_text(tmp_path / "nope", "fallback") == "fallback"


def test_read_text_ok(tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("hello world\n")
    assert read_text(f) == "hello world"


def test_read_first_line(tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("first\nsecond\n")
    assert read_first_line(f) == "first"
    assert read_first_line(tmp_path / "nope", "x") == "x"


def test_read_kv(tmp_path: Path) -> None:
    f = tmp_path / "release"
    f.write_text('NAME="Ubuntu"\nVERSION="22.04"\n# comment\nEMPTY=\n')
    d = read_kv(f)
    assert d["NAME"] == "Ubuntu"
    assert d["VERSION"] == "22.04"
    assert "EMPTY" in d


def test_parse_kv(tmp_path: Path) -> None:
    f = tmp_path / "conf"
    f.write_text("a=1\nb=2\n")
    assert parse_kv(f) == {"a": "1", "b": "2"}


def test_count_cpus() -> None:
    n = count_cpus()
    assert isinstance(n, int)
    assert n >= 1


def test_shell_run_ok() -> None:
    res = shell.run(["echo", "hi"])
    assert res.ok
    assert res.stdout == "hi"
    assert res.returncode == 0


def test_shell_run_fail() -> None:
    res = shell.run(["false"])
    assert not res.ok
    assert res.returncode == 1


def test_shell_run_string_split() -> None:
    res = shell.run("echo split")
    assert res.ok
    assert res.stdout == "split"


def test_shell_run_not_found() -> None:
    res = shell.run(["this-does-not-exist-xyz"])
    assert not res.ok
    assert res.returncode == 127


def test_shell_run_timeout() -> None:
    res = shell.run(["sleep", "10"], timeout=1)
    assert not res.ok
    assert res.returncode == 124


def test_which() -> None:
    assert shell.which("echo") is not None
    assert shell.which("definitely-not-a-real-bin-xyz123") is None


def test_is_root_type() -> None:
    assert isinstance(shell.is_root(), bool)
