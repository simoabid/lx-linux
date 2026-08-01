"""Phase 0 PRO UX tests: JSON envelope, watch loop, completion, doctor, quiet."""

from __future__ import annotations

import json
import sys

import pytest
from click.testing import CliRunner

import lx.__main__ as m
from lx import __version__


def _invoke(args):
    return CliRunner().invoke(m.cli, args)


def _assert_envelope(result) -> dict:
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert set(payload) == {"tool", "version", "command", "timestamp", "data"}
    assert payload["tool"] == "lx"
    assert payload["version"] == __version__
    assert payload["timestamp"]
    return payload


def test_bare_json_envelope():
    payload = _assert_envelope(_invoke(["--json"]))
    assert payload["command"] is None
    assert payload["data"] == {}


@pytest.mark.parametrize(
    "args",
    [
        ["info", "--json"],
        ["health", "--json"],
        ["proc", "top", "--json"],
        ["net", "iface", "--json"],
        ["pkg", "status", "--json"],
        ["tweak", "show", "--json"],
        ["sec", "ports", "--json"],
        ["service", "failed", "--json"],
        ["backup", "list", "--json"],
        ["clean", "report", "--json"],
        ["fs", "mounts", "--json"],
        ["sys", "env", "--json"],
        ["sys", "modules", "--json"],
        ["power", "battery", "--json"],
    ],
)
def test_json_envelope_all_readonly(args):
    payload = _assert_envelope(_invoke(args))
    assert payload["command"] == " ".join(args[:-1])
    assert isinstance(payload["data"], dict)


def test_doctor_json_always_exits_zero():
    payload = _assert_envelope(_invoke(["doctor", "--json"]))
    assert payload["command"] == "doctor"
    assert "verdict" in payload["data"]


def test_json_stdout_is_pure_json():
    result = _invoke(["clean", "report", "--json"])
    assert result.exit_code == 0
    json.loads(result.output)


def test_no_color_flag_strips_ansi():
    result = _invoke(["--no-color", "info"])
    assert result.exit_code == 0
    assert "\x1b[" not in result.output


def test_quiet_suppresses_decorative_rules():
    result = _invoke(["-q", "clean", "report"])
    assert result.exit_code == 0
    assert "Space report" not in result.output


def test_watch_rejected_for_mutating_command(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["lx", "--watch", "1", "pkg", "install", "curl"])
    assert m.main() == 2


def test_watch_rejected_for_unknown_command(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["lx", "--watch", "1", "no-such-command"])
    assert m.main() == 2


def test_watch_loop_emits_ndjson(monkeypatch, capsys):
    monkeypatch.setenv("LX_WATCH_MAX_ITERS", "2")
    monkeypatch.setattr(sys, "argv", ["lx", "--watch", "0.05", "health", "--json"])
    assert m.main() == 0
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 2
    for line in lines:
        payload = json.loads(line)
        assert payload["command"] == "health"


def test_watch_loop_stops_on_interrupt(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["lx", "--watch", "1", "health"])
    real_sleep = m.time.sleep

    def raise_keyboard(*_args, **_kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(m.time, "sleep", raise_keyboard)
    assert m.main() == 130
    m.time.sleep = real_sleep


@pytest.mark.parametrize(
    "shell,marker",
    [
        ("bash", "_lx_completion"),
        ("zsh", "compdef"),
        ("fish", "complete --no-files --command lx"),
    ],
)
def test_completion_scripts(shell, marker):
    result = _invoke(["completion", shell])
    assert result.exit_code == 0
    assert marker in result.output


def test_completion_stdout_has_no_banner():
    result = _invoke(["completion", "bash"])
    assert "power your Linux experience" not in result.output
