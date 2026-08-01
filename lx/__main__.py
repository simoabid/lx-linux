#!/usr/bin/env python3
"""lx entrypoint — dispatches to click command groups.

Implements the global options (--json, --watch, --no-color, -q), the
no-arguments banner, the `lx completion` command, and the --watch live
refresh loop (read-only commands only).
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from lx import __version__
from lx.commands import (
    backup as backup_cmd,
)
from lx.commands import (
    bench as bench_cmd,
)
from lx.commands import (
    clean as clean_cmd,
)
from lx.commands import (
    cron as cron_cmd,
)
from lx.commands import (
    doctor as doctor_cmd,
)
from lx.commands import (
    fs as fs_cmd,
)
from lx.commands import (
    health as health_cmd,
)
from lx.commands import (
    info as info_cmd,
)
from lx.commands import (
    log as log_cmd,
)
from lx.commands import (
    net as net_cmd,
)
from lx.commands import (
    pkg as pkg_cmd,
)
from lx.commands import (
    power as power_cmd,
)
from lx.commands import (
    proc as proc_cmd,
)
from lx.commands import (
    sec as sec_cmd,
)
from lx.commands import (
    service as service_cmd,
)
from lx.commands import (
    sys as sys_cmd,
)
from lx.commands import (
    tweak as tweak_cmd,
)
from lx.utils.context import get_context
from lx.utils.flags import is_watch_capable
from lx.utils.output import emit_json, set_flags

_console = Console()

_COMMANDS: list[tuple[str, str]] = [
    ("info", "system information"),
    ("net", "network tools"),
    ("proc", "process manager"),
    ("pkg", "package manager"),
    ("tweak", "system tuning"),
    ("sec", "security audit"),
    ("clean", "cleanup & optimizer"),
    ("service", "systemd wrapper"),
    ("backup", "dotfile backup"),
    ("health", "health score"),
    ("fs", "disk explorer"),
    ("sys", "system deep-dive"),
    ("bench", "benchmarks"),
    ("log", "journal explorer"),
    ("cron", "crontab & timers"),
    ("power", "battery & profiles"),
    ("doctor", "diagnostics"),
    ("completion", "shell completions"),
]

_WATCH_HELP = (
    "--watch is only supported for read-only commands (info, health, net, proc, "
    "tweak show, sec ports/ssh, service, clean report, backup list, pkg status, "
    "fs, sys, log show/errors, cron, power)."
)


def _prepare_context(
    json_mode: bool = False,
    watch_secs: float = 0.0,
    no_color: bool = False,
    quiet: bool = False,
) -> Any:
    """Build the shared context from the root-level flags."""
    ctx = get_context()
    if no_color or os.environ.get("NO_COLOR"):
        ctx.console = Console(color_system=None)
    ctx.data["json"] = bool(json_mode)
    ctx.data["watch"] = float(watch_secs or 0)
    ctx.data["quiet"] = bool(quiet)
    set_flags(quiet=ctx.data["quiet"], json=ctx.data["json"])
    return ctx


def _banner(console: Console) -> None:
    """The no-arguments home screen: version, command grid, quick tips."""
    half = (len(_COMMANDS) + 1) // 2
    left, right = _COMMANDS[:half], _COMMANDS[half:]
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", no_wrap=True)
    grid.add_column(style="dim")
    grid.add_column(style="bold cyan", no_wrap=True)
    grid.add_column(style="dim")
    for i in range(half):
        lname, ldesc = left[i]
        rname, rdesc = right[i] if i < len(right) else ("", "")
        grid.add_row(lname, ldesc, rname, rdesc)
    console.print(
        Panel(
            grid,
            title=f"[bold cyan]lx[/bold cyan] [dim]v{__version__}[/dim] — power your Linux experience",
            border_style="cyan",
            padding=(1, 2),
        )
    )
    console.print(
        "[dim]Run[/dim] [bold]lx <command> --help[/bold] "
        "[dim]for details. Quick start:[/dim] "
        "[bold]lx info[/bold] · [bold]lx health[/bold] · [bold]lx sec audit[/bold]"
    )
    console.print(
        "[dim]Global flags:[/dim] [bold]--json[/bold] [dim](machine output)[/dim] · "
        "[bold]--watch N[/bold] [dim](live refresh)[/dim] · "
        "[bold]-q[/bold] [dim](quiet)[/dim] · [bold]--no-color[/bold]"
    )


@click.group(
    context_settings={"help_option_names": ["-h", "--help"]},
    invoke_without_command=True,
)
@click.version_option(version=__version__, prog_name="lx")
@click.option("--json", "json_mode", is_flag=True, help="Emit machine-readable JSON output.")
@click.option(
    "--watch",
    "watch_secs",
    type=float,
    default=0.0,
    help="Repeat every N seconds (read-only commands).",
)
@click.option("--no-color", "no_color", is_flag=True, help="Disable ANSI colors.")
@click.option("-q", "--quiet", is_flag=True, help="Suppress decorative output.")
@click.pass_context
def cli(
    ctx: click.Context,
    json_mode: bool,
    watch_secs: float,
    no_color: bool,
    quiet: bool,
) -> None:
    """lx — power your Linux experience.

    A modern toolkit for inspecting, tweaking, cleaning, and managing Linux
    systems. Run `lx <command> --help` for per-command details.

    Global flags (also accepted after the subcommand where useful):
      --json       machine-readable output  (lx --json info | lx info --json)
      --watch N    live refresh every N s   (read-only commands only)
      -q/--quiet   suppress decorative output
      --no-color   disable ANSI colors (NO_COLOR env var is honored too)
    """
    ctx.obj = _prepare_context(json_mode, watch_secs, no_color, quiet)
    if ctx.invoked_subcommand is None:
        if ctx.obj.data.get("json"):
            emit_json(ctx.obj.console, {}, command=None)
        else:
            _banner(ctx.obj.console)
        ctx.exit(0)


cli.add_command(info_cmd.info)
cli.add_command(net_cmd.net)
cli.add_command(proc_cmd.proc)
cli.add_command(pkg_cmd.pkg)
cli.add_command(tweak_cmd.tweak)
cli.add_command(sec_cmd.sec)
cli.add_command(clean_cmd.clean)
cli.add_command(service_cmd.service)
cli.add_command(backup_cmd.backup)
cli.add_command(health_cmd.health)
cli.add_command(fs_cmd.fs)
cli.add_command(sys_cmd.sys)
cli.add_command(bench_cmd.bench)
cli.add_command(log_cmd.log)
cli.add_command(cron_cmd.cron)
cli.add_command(power_cmd.power)
cli.add_command(doctor_cmd.doctor)


@cli.command("completion")
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"], case_sensitive=False))
def _cmd_completion(shell: str) -> None:
    """Print a shell completion script for bash, zsh, or fish.

    Add to your shell, e.g.:

        lx completion bash >> ~/.bashrc
        lx completion zsh  >> ~/.zshrc
        lx completion fish >> ~/.config/fish/completions/lx.fish
    """
    os.environ["_LX_COMPLETE"] = f"{shell.lower()}_source"
    try:
        cli.main(prog_name="lx", standalone_mode=True)
    finally:
        os.environ.pop("_LX_COMPLETE", None)


def _resolve_leaf_command(ctx: click.Context) -> click.Command | None:
    """Resolve the leaf command from the not-yet-invoked token chain.

    click 8.4 does not resolve subcommands during parsing (invoked_subcommand
    is only set inside invoke()), so we walk the leftover tokens.
    """
    cmd: click.Command | None = ctx.command
    tokens: list[str] = list(getattr(ctx, "_protected_args", ())) + list(ctx.args)
    while isinstance(cmd, click.Group) and tokens and tokens[0] in cmd.commands:
        cmd = cmd.commands[tokens[0]]
        tokens = tokens[1:]
    return cmd


def _max_watch_iters() -> int:
    """Internal test knob: LX_WATCH_MAX_ITERS caps the --watch loop."""
    try:
        return max(0, int(os.environ.get("LX_WATCH_MAX_ITERS", "0") or 0))
    except ValueError:
        return 0


def main() -> int:
    argv = list(sys.argv[1:])
    max_iters = _max_watch_iters()
    iteration = 0
    while True:
        iteration += 1
        try:
            # click's parser pops consumed options off the list it parses,
            # so each iteration must parse from a fresh copy.
            ctx = cli.make_context("lx", list(argv))
        except click.exceptions.Exit as exc:
            return exc.exit_code
        except click.exceptions.Abort:
            return 130
        except click.ClickException as exc:
            exc.show()
            return exc.exit_code
        except KeyboardInterrupt:
            _console.print("\n[yellow]Interrupted.[/yellow]")
            return 130
        except Exception as exc:  # noqa: BLE001 — top-level guard
            _console.print(f"[bold red]error:[/bold red] {exc}")
            return 1

        ctx.obj = _prepare_context(
            ctx.params.get("json_mode") or False,
            ctx.params.get("watch_secs") or 0.0,
            ctx.params.get("no_color") or False,
            ctx.params.get("quiet") or False,
        )

        if ctx.obj.watch and (
            ctx.invoked_subcommand is not None or getattr(ctx, "_protected_args", ())
        ):
            leaf = _resolve_leaf_command(ctx)
            if leaf is None or not is_watch_capable(leaf):
                _console.print(f"[bold red]error:[/bold red] {_WATCH_HELP}")
                return 2

        try:
            cli.invoke(ctx)
        except click.exceptions.Exit as exc:
            return exc.exit_code
        except click.exceptions.Abort:
            return 130
        except click.ClickException as exc:
            exc.show()
            return exc.exit_code
        except KeyboardInterrupt:
            _console.print("\n[yellow]Interrupted.[/yellow]")
            return 130
        except Exception as exc:  # noqa: BLE001 — top-level guard
            _console.print(f"[bold red]error:[/bold red] {exc}")
            return 1

        if not ctx.obj.watch:
            return 0
        if max_iters and iteration >= max_iters:
            return 0
        try:
            time.sleep(ctx.obj.watch)
        except KeyboardInterrupt:
            _console.print("\n[yellow]Interrupted.[/yellow]")
            return 130
        if not ctx.obj.json and ctx.obj.console.is_terminal:
            ctx.obj.console.clear()


if __name__ == "__main__":
    sys.exit(main())
