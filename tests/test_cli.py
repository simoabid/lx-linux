"""Tests for the lx CLI dispatch and version/help output."""
from __future__ import annotations

from click.testing import CliRunner

from lx import __version__
from lx.__main__ import cli


def test_version() -> None:
    r = CliRunner()
    result = r.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_help_lists_commands() -> None:
    r = CliRunner()
    result = r.invoke(cli, ["--help"])
    assert result.exit_code == 0
    for cmd in ("info", "net", "proc", "pkg", "tweak", "sec", "clean", "service", "backup", "health"):
        assert cmd in result.output


def test_no_subcommand_prints_banner() -> None:
    r = CliRunner()
    result = r.invoke(cli, [])
    assert result.exit_code == 0
    assert "lx" in result.output


def test_each_command_has_help() -> None:
    r = CliRunner()
    for cmd in ("info", "net", "proc", "pkg", "tweak", "sec", "clean", "service", "backup", "health"):
        result = r.invoke(cli, [cmd, "--help"])
        assert result.exit_code == 0, f"{cmd} --help failed: {result.output}"
        assert "Usage" in result.output or "usage" in result.output.lower()


def test_subcommands_resolve() -> None:
    """Spot-check that subcommands are wired up."""
    r = CliRunner()
    cases = [
        ("net", "iface"),
        ("net", "ports"),
        ("proc", "top"),
        ("proc", "find"),
        ("pkg", "status"),
        ("tweak", "show"),
        ("sec", "audit"),
        ("clean", "report"),
        ("service", "failed"),
        ("backup", "list"),
    ]
    for group, sub in cases:
        result = r.invoke(cli, [group, sub, "--help"])
        assert result.exit_code == 0, f"{group} {sub} --help failed: {result.output}"


def test_mutating_commands_exit_nonzero_on_failure() -> None:
    """Commands that need root must fail with a non-zero exit code, not 0."""
    r = CliRunner()
    cases = [
        ["tweak", "swappiness", "10"],
        ["tweak", "governor", "performance"],
        ["tweak", "bbr"],
        ["service", "restart", "ssh"],
        ["pkg", "install", "curl"],
        ["clean", "logs"],
    ]
    for args in cases:
        result = r.invoke(cli, args, input="n\n")
        assert result.exit_code != 0, f"{args} should fail without root, got exit 0"


def test_confirm_decline_aborts(monkeypatch) -> None:
    """Declining a destructive prompt aborts with 130 via the real entrypoint."""
    import io
    import sys

    import lx.__main__ as m

    monkeypatch.setattr(sys, "argv", ["lx", "clean", "cache"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("n\n"))
    assert m.main() == 130


def test_unknown_command_fails() -> None:
    r = CliRunner()
    result = r.invoke(cli, ["not-a-real-command"])
    assert result.exit_code != 0
