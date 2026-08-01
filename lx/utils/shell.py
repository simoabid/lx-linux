"""Shell runner helpers — safely run commands and capture output."""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass


@dataclass
class Result:
    """Normalized result of a subprocess call."""

    returncode: int
    stdout: str
    stderr: str
    ok: bool

    @classmethod
    def from_completed(cls, cp: subprocess.CompletedProcess[str]) -> Result:
        return cls(
            returncode=cp.returncode,
            stdout=cp.stdout.strip() if cp.stdout else "",
            stderr=cp.stderr.strip() if cp.stderr else "",
            ok=(cp.returncode == 0),
        )


def run(
    cmd: str | list[str],
    *,
    shell: bool = False,
    sudo: bool = False,
    check: bool = False,
    timeout: int | None = 30,
) -> Result:
    """Run a command safely.

    Prefer passing a list; if a string is given and shell=False, it will be
    shlex-split. When sudo=True the command is prefixed with `sudo -n` so any
    password prompt surfaces as an error rather than hanging.
    """
    if isinstance(cmd, str) and not shell:
        argv = shlex.split(cmd)
    else:
        argv = cmd if isinstance(cmd, list) else [cmd]

    if sudo and argv and argv[0] != "sudo":
        argv = ["sudo", "-n", *argv]

    try:
        cp = subprocess.run(
            argv if not shell else cmd,
            shell=shell,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return Result(124, "", "command timed out", False)
    except FileNotFoundError as exc:
        return Result(127, "", str(exc), False)

    result = Result.from_completed(cp)
    if check and not result.ok:
        raise RuntimeError(
            f"command failed ({result.returncode}): {result.stderr or result.stdout}"
        )
    return result


def which(name: str) -> str | None:
    """Return absolute path to an executable on $PATH, or None."""
    res = run(f"command -v {shlex.quote(name)}", shell=True)
    return res.stdout or None


def is_root() -> bool:
    """True when the calling process has uid 0."""
    import os

    return os.geteuid() == 0
