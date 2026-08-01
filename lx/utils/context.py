"""Shared runtime context passed through click commands."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Context:
    """Holds a shared Rich console and a scratch dict for cross-command state."""

    console: Any = None
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.console is None:
            from rich.console import Console

            self.console = Console()


def get_context() -> Context:
    """Construct a fresh context with a configured console."""
    return Context()
