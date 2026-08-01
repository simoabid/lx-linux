#!/usr/bin/env python3
"""lx entrypoint — dispatches to click command groups."""
from __future__ import annotations

import sys

import click
from rich.console import Console

from lx import __version__
from lx.commands import (
    backup as backup_cmd,
)
from lx.commands import (
    clean as clean_cmd,
)
from lx.commands import (
    health as health_cmd,
)
from lx.commands import (
    info as info_cmd,
)
from lx.commands import (
    net as net_cmd,
)
from lx.commands import (
    pkg as pkg_cmd,
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
    tweak as tweak_cmd,
)
from lx.utils.context import get_context

_console = Console()


@click.group(
    context_settings={"help_option_names": ["-h", "--help"]},
    invoke_without_command=True,
)
@click.version_option(version=__version__, prog_name="lx")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """lx — power your Linux experience.

    A modern toolkit for inspecting, tweaking, cleaning, and managing Linux
    systems. Run `lx <command> --help` for per-command details.
    """
    ctx.obj = get_context()
    if ctx.invoked_subcommand is None:
        _console.print(
            "[bold cyan]lx[/bold cyan] "
            f"[dim]v{__version__}[/dim] — power your Linux experience.\n"
        )
        _console.print(
            "[dim]Run[/dim] [bold]lx --help[/bold] "
            "[dim]to see all commands.[/dim]"
        )
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


def main() -> int:
    try:
        result = cli(prog_name="lx", standalone_mode=False)
        return result if isinstance(result, int) else 0
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


if __name__ == "__main__":
    sys.exit(main())
