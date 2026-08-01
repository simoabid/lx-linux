"""Consistent confirmation handling for destructive operations.

Every destructive prompt in lx goes through :func:`confirm_destructive` so
the hint style, ``--yes`` bypass, and ``--json`` refusal stay uniform.
Commands with irreversible consequences may pass ``token=`` to require the
user to type the action word ("type-to-confirm") instead of answering y/N.
"""

from __future__ import annotations

import click

from lx.utils.output import stderr_console

#: Hint text appended to destructive confirmations, kept consistent so
#: scripts can rely on it.
_JSON_YES_HINT = "pass --yes (or -y) to run it non-interactively"

_DEFAULT_HINT = "type 'y' to confirm, anything else to abort"

#: Prompt text for the typed confirmations; ``{token}`` is substituted.
_TOKEN_HINT = "Type '{token}' to confirm"

#: Exit code used when --json mode refuses a destructive run without --yes.
_JSON_REFUSAL_CODE = 2


def confirm_destructive(
    ctx: click.Context,
    message: str,
    *,
    yes: bool = False,
    hint: str = _DEFAULT_HINT,
    token: str | None = None,
) -> bool:
    """Gate a destructive operation behind an explicit confirmation.

    * ``--yes`` skips the prompt.
    * In ``--json`` mode a destructive command **refuses** to run without
      ``--yes`` (exit 2) — scripts must opt in explicitly.
    * With ``token=`` set, the user must type that exact word (after
      stripping surrounding whitespace) to proceed; anything else —
      including an empty line or EOF — raises ``Abort`` (exit 130).
    * Declining raises ``Abort`` (exit 130 via the entrypoint).
    """
    if ctx.obj.data.get("json") and not yes:
        stderr_console(ctx.obj.console).print(
            f"[bold red]error:[/bold red] {message} — {_JSON_YES_HINT}"
        )
        raise click.exceptions.Exit(_JSON_REFUSAL_CODE)
    if yes:
        return True
    if token is not None:
        answer = click.prompt(
            f"{message} {_TOKEN_HINT.format(token=token)}",
            default="",
            show_default=False,
        )
        if answer.strip() != token:
            raise click.exceptions.Abort()
        return True
    return click.confirm(f"{message} {hint}", default=False)
