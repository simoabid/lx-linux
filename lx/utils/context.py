"""Shared runtime context passed through click commands."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

_NO_COLOR_ENV = "NO_COLOR"


@dataclass
class Context:
    """Holds a shared Rich console, global flags, and cross-command state.

    Flags are merged into ``data`` by the root CLI callback (``--json``,
    ``--watch``, ``--no-color``, ``-q/--quiet``) and by leaf commands via
    ``lx.utils.flags.apply_flags``. Everything reads from ``data`` so group-
    and subcommand-level flags compose predictably.
    """

    console: Any = None
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.console is None:
            from rich.console import Console

            self.console = Console()

    @property
    def json(self) -> bool:
        """True when machine-readable JSON output is requested."""
        return bool(self.data.get("json"))

    @property
    def watch(self) -> float:
        """Watch interval in seconds (0 means no live refresh)."""
        return float(self.data.get("watch") or 0)

    @property
    def quiet(self) -> bool:
        """True when decorative output should be suppressed."""
        return bool(self.data.get("quiet"))


def get_context() -> Context:
    """Construct a fresh context with a configured console.

    Honors the ``NO_COLOR`` environment variable (https://no-color.org).
    """
    ctx = Context()
    if os.environ.get(_NO_COLOR_ENV):
        from rich.console import Console

        ctx.console = Console(color_system=None)
    return ctx
