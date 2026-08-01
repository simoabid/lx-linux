"""Phase 3 tests: type-to-confirm safety, unified hint styling, version bump.

Hermetic — no root, no network, no real destructive commands. All
subprocess calls are monkeypatched and file mutations stay in tmp_path.
"""

from __future__ import annotations

import io
import json
import sys
import tarfile
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

import lx.__main__ as m
from lx import __version__
from lx.commands import clean, tweak
from lx.commands import pkg as pkg_cmd
from lx.utils.prompt import confirm_destructive
from lx.utils.shell import Result

runner = CliRunner()

LX_ROOT = Path(clean.__file__).resolve().parents[2]


def _invoke(args, stdin_text: str | None = None):
    return runner.invoke(m.cli, args, input=stdin_text)


def _run_as_main(monkeypatch, args, stdin_text: str) -> int:
    """Invoke through the real entrypoint so Abort maps to exit 130."""
    monkeypatch.setattr(sys, "argv", ["lx", *args])
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin_text))
    return m.main()


# ------------------------------------------------------------ prompt core


class _FakeConsole:
    pass


def _ctx(json_mode: bool = False) -> click.Context:
    ctx = click.Context(click.Command("fake"))
    ctx.obj = type("Obj", (), {})()
    ctx.obj.data = {"json": json_mode, "watch": 0, "quiet": False}
    ctx.obj.console = _FakeConsole()
    return ctx


def test_typed_accepts_exact_token(monkeypatch):
    monkeypatch.setattr(click, "prompt", lambda *a, **k: "purge")
    assert confirm_destructive(_ctx(), "Remove 3 old kernel package(s)?", token="purge") is True


def test_typed_accepts_token_with_whitespace(monkeypatch):
    monkeypatch.setattr(click, "prompt", lambda *a, **k: "  purge  ")
    assert confirm_destructive(_ctx(), "msg", token="purge") is True


def test_typed_rejects_mismatch(monkeypatch):
    monkeypatch.setattr(click, "prompt", lambda *a, **k: "yes")
    with pytest.raises(click.exceptions.Abort):
        confirm_destructive(_ctx(), "msg", token="purge")


def test_typed_rejects_empty_line(monkeypatch):
    monkeypatch.setattr(click, "prompt", lambda *a, **k: "")
    with pytest.raises(click.exceptions.Abort):
        confirm_destructive(_ctx(), "msg", token="restore")


def test_typed_prompt_message_contains_token_hint(monkeypatch):
    captured: dict[str, str] = {}
    monkeypatch.setattr(
        click, "prompt", lambda *a, **k: captured.update(text=str(a[0])) or "purge"
    )
    confirm_destructive(_ctx(), "Remove 3 old kernel package(s)?", token="purge")
    assert captured["text"].startswith("Remove 3 old kernel package(s)?")
    assert "Type 'purge' to confirm" in captured["text"]


def test_yes_bypasses_typed_prompt(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("prompt must not be called with --yes")

    monkeypatch.setattr(click, "prompt", _boom)
    assert confirm_destructive(_ctx(), "msg", yes=True, token="purge") is True


def test_plain_prompt_uses_unified_hint(monkeypatch):
    captured: dict[str, str] = {}
    monkeypatch.setattr(
        click, "confirm", lambda *a, **k: captured.update(text=str(a[0])) or True
    )
    assert confirm_destructive(_ctx(), "Clear 1.2 GB of cache?") is True
    assert captured["text"] == "Clear 1.2 GB of cache? type 'y' to confirm, anything else to abort"


def _flat(text: str) -> str:
    """Collapse whitespace so Rich word-wrapping can't split assertions."""
    return " ".join(text.split())


def test_json_refusal_exits_2(capsys):
    for kwargs in ({}, {"token": "purge"}):
        with pytest.raises(click.exceptions.Exit) as exc:
            confirm_destructive(_ctx(json_mode=True), "msg", **kwargs)
        assert exc.value.exit_code == 2
        assert "pass --yes (or -y) to run it non-interactively" in _flat(
            capsys.readouterr().err
        )


def test_json_refusal_never_prompts(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("prompt must not be called in --json mode")

    monkeypatch.setattr(click, "prompt", _boom)
    with pytest.raises(click.exceptions.Exit):
        confirm_destructive(_ctx(json_mode=True), "msg", token="purge")


# ------------------------------------------------------------ clean kernels

KERNEL_LIST = (
    "linux-image-5.4.0-150-generic 5.4.0-150\n"
    "linux-image-5.15.0-100-generic 5.15.0-100\n"
    "linux-image-6.8.0-45-generic 6.8.0-45\n"
    "linux-image-6.8.0-44-generic 6.8.0-44\n"
    "linux-image-6.8.0-43-generic 6.8.0-43\n"
)


def _fake_kernels_run(calls: list) -> object:
    def _run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        argv = cmd if isinstance(cmd, list) else cmd.split()
        if argv and argv[0] == "uname":
            return Result(0, "6.8.0-45-generic", "", True)
        if argv and argv[0] == "dpkg-query":
            return Result(0, KERNEL_LIST, "", True)
        if argv and argv[0] == "apt-get":
            return Result(0, "purged\n", "", True)
        return Result(0, "", "", True)

    return _run


@pytest.fixture
def kernels_env(monkeypatch):
    monkeypatch.setattr(clean, "is_root", lambda: True)
    calls: list = []
    monkeypatch.setattr(clean, "run", _fake_kernels_run(calls))
    return calls


def test_clean_kernels_typed_purge_executes(kernels_env):
    result = _invoke(["clean", "kernels", "--keep", "2"], stdin_text="purge\n")
    assert result.exit_code == 0, result.output
    assert "removed 2 kernel package(s)" in result.output
    purge = [c for c in kernels_env if c[0][0] == "apt-get"]
    assert len(purge) == 1
    removed = set(purge[0][0][3:])
    assert removed == {"linux-image-5.4.0-150-generic", "linux-image-5.15.0-100-generic"}
    assert purge[0][1]["sudo"] is True


def test_clean_kernels_wrong_token_aborts_130(kernels_env, monkeypatch):
    assert _run_as_main(monkeypatch, ["clean", "kernels"], "no\n") == 130
    assert not any(c[0][0] == "apt-get" for c in kernels_env)


def test_clean_kernels_empty_input_aborts_130(kernels_env, monkeypatch):
    assert _run_as_main(monkeypatch, ["clean", "kernels"], "") == 130
    assert not any(c[0][0] == "apt-get" for c in kernels_env)


def test_clean_kernels_json_without_yes_exits_2(kernels_env):
    result = _invoke(["--json", "clean", "kernels", "--keep", "2"])
    assert result.exit_code == 2
    assert "pass --yes (or -y) to run it non-interactively" in _flat(result.output)
    assert not any(c[0][0] == "apt-get" for c in kernels_env)


def test_clean_kernels_yes_skips_prompt(kernels_env):
    result = _invoke(["clean", "kernels", "--keep", "2", "-y"])
    assert result.exit_code == 0, result.output
    assert any(c[0][0] == "apt-get" for c in kernels_env)


def test_clean_kernels_json_with_yes_works(kernels_env):
    result = _invoke(["--json", "clean", "kernels", "--keep", "2", "-y"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload["data"]["removed"]) == 2


# ------------------------------------------------------------ pkg remove

def _fake_pkg_run(calls: list) -> object:
    def _run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        argv = cmd if isinstance(cmd, list) else cmd.split()
        joined = " ".join(argv)
        if "autoremove" in joined and "-s" in joined:
            return Result(
                0,
                "Reading package lists... Done\n"
                "The following packages will be REMOVED:\n"
                "  libfoo1\n"
                "  libbar2\n"
                "0 upgraded, 0 newly installed, 2 to remove and 0 not upgraded.\n",
                "",
                True,
            )
        return Result(0, "ok\n", "", True)

    return _run


@pytest.fixture
def pkg_env(monkeypatch):
    monkeypatch.setattr(pkg_cmd, "is_root", lambda: True)
    monkeypatch.setattr(pkg_cmd, "_detect_backend", lambda: ("apt", ["apt"]))
    calls: list = []
    monkeypatch.setattr(pkg_cmd, "run", _fake_pkg_run(calls))
    return calls


def test_pkg_remove_typed_executes(pkg_env):
    result = _invoke(["pkg", "remove", "curl"], stdin_text="remove\n")
    assert result.exit_code == 0, result.output
    assert "removed curl" in result.output
    assert ["sh", "-c", "apt remove -y curl"] in [c[0] for c in pkg_env]


def test_pkg_remove_yes_executes(pkg_env):
    result = _invoke(["pkg", "remove", "curl", "-y"])
    assert result.exit_code == 0, result.output
    assert ["sh", "-c", "apt remove -y curl"] in [c[0] for c in pkg_env]


def test_pkg_remove_wrong_token_aborts_130(pkg_env, monkeypatch):
    assert _run_as_main(monkeypatch, ["pkg", "remove", "curl"], "nope\n") == 130
    assert not pkg_env


def test_pkg_remove_json_without_yes_exits_2(pkg_env):
    result = _invoke(["--json", "pkg", "remove", "curl"])
    assert result.exit_code == 2
    assert "pass --yes (or -y) to run it non-interactively" in _flat(result.output)
    assert not pkg_env


def test_pkg_remove_help_documents_yes():
    result = _invoke(["pkg", "remove", "--help"])
    assert result.exit_code == 0
    assert "--yes" in result.output


# ------------------------------------------------------------ pkg autoremove

def test_pkg_autoremove_typed_executes(pkg_env):
    result = _invoke(["pkg", "autoremove"], stdin_text="remove\n")
    assert result.exit_code == 0, result.output
    assert "removed 2 orphaned package(s)" in result.output
    assert ["sh", "-c", "apt-get autoremove -y"] in [c[0] for c in pkg_env]


def test_pkg_autoremove_wrong_token_aborts_130(pkg_env, monkeypatch):
    assert _run_as_main(monkeypatch, ["pkg", "autoremove"], "remove now\n") == 130
    assert not any("autoremove -y" in " ".join(c[0]) for c in pkg_env)


def test_pkg_autoremove_json_without_yes_exits_2(pkg_env):
    result = _invoke(["--json", "pkg", "autoremove"])
    assert result.exit_code == 2
    assert not any("autoremove -y" in " ".join(c[0]) for c in pkg_env)


# ------------------------------------------------------------ backup restore

def _make_archive(path: Path) -> None:
    with tarfile.open(path, "w:gz") as tar:
        payload = b"alias ll='ls -la'\n"
        info = tarfile.TarInfo("home/USER/.bashrc")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))


def test_backup_restore_typed_extracts(tmp_path):
    archive = tmp_path / "lx-backup-20260801-000000.tar.gz"
    dest = tmp_path / "out"
    _make_archive(archive)
    result = _invoke(
        ["backup", "restore", str(archive), "--dest", str(dest)],
        stdin_text="restore\n",
    )
    assert result.exit_code == 0, result.output
    assert (dest / "home" / "USER" / ".bashrc").read_text() == "alias ll='ls -la'\n"


def test_backup_restore_wrong_token_aborts_130(tmp_path, monkeypatch):
    archive = tmp_path / "lx-backup-20260801-000000.tar.gz"
    dest = tmp_path / "out"
    _make_archive(archive)
    code = _run_as_main(
        monkeypatch,
        ["backup", "restore", str(archive), "--dest", str(dest)],
        "restart\n",
    )
    assert code == 130
    assert not (dest / "home").exists()


def test_backup_restore_json_without_yes_exits_2(tmp_path):
    archive = tmp_path / "lx-backup-20260801-000000.tar.gz"
    dest = tmp_path / "out"
    _make_archive(archive)
    result = _invoke(["--json", "backup", "restore", str(archive), "--dest", str(dest)])
    assert result.exit_code == 2
    assert "pass --yes (or -y) to run it non-interactively" in _flat(result.output)
    assert not (dest / "home").exists()


# ------------------------------------------------------------ tweak restore

@pytest.fixture
def tweak_env(tmp_path, monkeypatch):
    monkeypatch.setattr(tweak, "is_root", lambda: True)
    drop_in = tmp_path / "90-lx.conf"
    drop_in.write_text("vm.swappiness=10\n")
    limits = tmp_path / "limits.conf"
    limits.write_text("# base\n* soft nofile 65536\n* hard nofile 65536\n")
    monkeypatch.setattr(tweak, "_SYSCTL_DROP_IN", drop_in)
    monkeypatch.setattr(tweak, "_LIMITS_CONF", limits)
    return drop_in, limits


def test_tweak_restore_typed_backs_up_drop_in(tweak_env):
    drop_in, limits = tweak_env
    result = _invoke(["tweak", "restore"], stdin_text="restore\n")
    assert result.exit_code == 0, result.output
    assert not drop_in.exists()
    backups = list(drop_in.parent.glob("90-lx.conf.bak-*"))
    assert len(backups) == 1
    assert backups[0].read_text() == "vm.swappiness=10\n"
    assert "nofile" not in limits.read_text()


def test_tweak_restore_wrong_token_leaves_files(tweak_env, monkeypatch):
    drop_in, limits = tweak_env
    assert _run_as_main(monkeypatch, ["tweak", "restore"], "reset\n") == 130
    assert drop_in.exists()
    assert "nofile" in limits.read_text()
    assert not list(drop_in.parent.glob("90-lx.conf.bak-*"))


def test_tweak_restore_json_without_yes_exits_2(tweak_env):
    drop_in, _limits = tweak_env
    result = _invoke(["--json", "tweak", "restore"])
    assert result.exit_code == 2
    assert drop_in.exists()


# ------------------------------------------------------------ consistency & version

def test_no_raw_click_confirm_outside_prompt_module():
    offenders = []
    for py in sorted(LX_ROOT.glob("lx/**/*.py")):
        if py.name == "prompt.py":
            continue
        for i, line in enumerate(py.read_text().splitlines(), 1):
            if "click.confirm" in line:
                offenders.append(f"{py.relative_to(LX_ROOT)}:{i}")
    assert not offenders, f"raw click.confirm outside prompt.py: {offenders}"


def test_typed_commands_all_refuse_json_without_yes(kernels_env, pkg_env, tweak_env):
    """The four gateable type-to-confirm ops share the JSON refusal contract."""
    for args in (
        ["clean", "kernels", "--keep", "2"],
        ["pkg", "remove", "curl"],
        ["pkg", "autoremove"],
        ["tweak", "restore"],
    ):
        result = _invoke(["--json", *args])
        assert result.exit_code == 2, f"{args}: {result.output}"
        assert "pass --yes (or -y) to run it non-interactively" in _flat(result.output)


def test_version_bumped_to_040():
    assert __version__ == "0.4.0"
    result = _invoke(["--version"])
    assert result.exit_code == 0
    assert "0.4.0" in result.output
    payload = json.loads(_invoke(["info", "--json"]).output)
    assert payload["version"] == "0.4.0"
