"""Shared click option decorators and flag plumbing for lx commands."""

from __future__ import annotations

from typing import Any

import click

from lx.utils.output import set_flags

JSON_HELP = "Emit machine-readable JSON output for this command."
WATCH_HELP = "Repeat the command every N seconds (read-only commands only)."

_WATCH_CAPABLE = "watch_capable"


def json_option(fn: Any) -> Any:
    """Add a --json flag to any leaf command (read-only or mutating).

    Must be applied *outside* @click.command so it decorates the Command
    object; the callback then receives an extra ``json_mode`` argument
    (None when the flag is absent).
    """
    fn = click.option("--json", "json_mode", is_flag=True, default=None, help=JSON_HELP)(fn)
    return fn


def json_watch_options(fn: Any) -> Any:
    """Add --json and --watch to a read-only leaf command.

    Marks the command as watch-capable, which is what the entrypoint uses
    to allow `lx --watch N <command>` loops (safety gate: mutating commands
    never receive --watch).
    """
    fn = click.option("--json", "json_mode", is_flag=True, default=None, help=JSON_HELP)(fn)
    fn = click.option("--watch", "watch_secs", type=float, default=None, help=WATCH_HELP)(fn)
    setattr(fn, _WATCH_CAPABLE, True)
    return fn


def is_watch_capable(cmd: click.Command) -> bool:
    """True when a command declares it is safe to run under --watch.

    The marker is applied to the callback function *before* click wraps it
    into a Command, so check both the command and its callback.
    """
    on_cmd = getattr(cmd, _WATCH_CAPABLE, False)
    callback = getattr(cmd, "callback", None)
    on_callback = bool(getattr(callback, _WATCH_CAPABLE, False))
    return bool(on_cmd or on_callback)


def apply_flags(
    ctx: click.Context,
    json_mode: bool | None = None,
    watch_secs: float | None = None,
) -> None:
    """Merge subcommand-level flags into ctx.obj.data.

    ``None`` leaves the group-level value untouched, so `lx --json net ports`
    and `lx net ports --json` behave identically (leaf wins on conflict).
    """
    if json_mode is not None:
        ctx.obj.data["json"] = bool(json_mode)
    if watch_secs is not None:
        ctx.obj.data["watch"] = float(watch_secs)
        ctx.obj.data[_WATCH_CAPABLE] = True
    set_flags(quiet=ctx.obj.data.get("quiet", False), json=ctx.obj.data.get("json", False))
