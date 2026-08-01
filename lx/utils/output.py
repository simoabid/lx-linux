"""Re-usable output helpers built on top of Rich."""

from __future__ import annotations

import datetime as _dt
import json
import sys
from collections.abc import Iterable
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.text import Text

from lx import __version__

# Module-level UI flags, reset at the start of every CLI invocation by
# lx.utils.output.set_flags (called from the root CLI callback).
_FLAGS: dict[str, bool] = {"quiet": False, "json": False}


def set_flags(*, quiet: bool = False, json: bool = False) -> None:
    """Record UI flags for output helpers for the current invocation."""
    _FLAGS["quiet"] = bool(quiet)
    _FLAGS["json"] = bool(json)


def quiet() -> bool:
    """True when decorative output (headers, hints) should be suppressed."""
    return _FLAGS["quiet"] or _FLAGS["json"]


def section(console: Any, title: str, style: str = "cyan") -> Panel:
    """Return a titled Panel wrapper for a section header."""
    return Panel(Text(title, style=f"bold {style}"), border_style=style, padding=(0, 1))


def kv_table(console: Any, rows: Iterable[tuple[str, Any]], title: str | None = None) -> None:
    """Print a clean two-column key/value table."""
    table = Table(title=title, show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column()
    for k, v in rows:
        table.add_row(str(k), str(v))
    console.print(table)


def make_bar(value: float, total: float = 100.0, label: str = "") -> Progress:
    """Build a styled Progress bar to render manually."""
    progress = Progress(
        TextColumn("[bold]{label}[/bold]") if label else TextColumn(""),
        BarColumn(bar_width=40),
        TextColumn("[cyan]{value:>5.0f}[/] / {total:.0f}"),
        TimeRemainingColumn(),
        transient=False,
    )
    return progress


def pct_color(pct: float) -> str:
    """Pick a color by load percentage: green / yellow / red."""
    if pct < 60:
        return "green"
    if pct < 85:
        return "yellow"
    return "red"


def human_bytes(n: float) -> str:
    """Convert bytes to a compact human string (e.g. '7.5 GiB')."""
    f = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if abs(f) < 1024:
            return f"{f:.1f} {unit}"
        f /= 1024
    return f"{f:.1f} EiB"


_human_bytes = human_bytes  # back-compat alias used by gauge()


def gauge(console: Any, label: str, value: float, total: float, unit: str = "%") -> None:
    """Render a single inline gauge line with a colored bar."""
    pct = (value / total * 100) if total else 0
    color = pct_color(pct)
    bar_len = 30
    filled = int(bar_len * (value / total)) if total else 0
    bar = "█" * filled + "░" * (bar_len - filled)
    if unit == " B":
        val_str = f"{_human_bytes(value)} / {_human_bytes(total)}"
    else:
        val_str = f"{value:.1f}/{total:.1f}{unit}"
    console.print(
        f"[bold]{label:<12}[/bold] [{color}]{bar}[/{color}] "
        f"{val_str} ([{color}]{pct:5.1f}%[/{color}])"
    )


def center_rule(console: Any, text: str, char: str = "─") -> None:
    """A centered decorative rule with text in the middle (hidden with -q)."""
    if quiet():
        return
    width = console.width or 80
    side = max(2, (width - len(text) - 4) // 2)
    console.print(f"[dim]{char * side}[/dim] [bold]{text}[/bold] [dim]{char * side}[/dim]")


def warn(console: Any, msg: str) -> None:
    _status_line(console, msg, "yellow", "⚠")


def ok(console: Any, msg: str) -> None:
    _status_line(console, msg, "green", "✓")


def err(console: Any, msg: str) -> None:
    _status_line(console, msg, "red", "✗")


_STDERR_CACHE: dict[str, tuple[Any, Any]] = {}


def stderr_console(console: Any) -> Any:
    """A Console writing to sys.stderr, matching the caller's color mode.

    Rich's ``Console.print`` accepts neither ``file=`` nor ``stderr=``, so
    status lines routed to stderr (JSON mode) need their own console. The
    console is rebuilt when ``sys.stderr`` changes identity (e.g. under
    test harnesses that swap streams between invocations) so cached
    consoles never point at a closed stream.
    """
    key = str(getattr(console, "color_system", None))
    cached = _STDERR_CACHE.get(key)
    if cached is None or cached[0] is not sys.stderr:
        cached = (
            sys.stderr,
            Console(file=sys.stderr, color_system=getattr(console, "color_system", None)),
        )
        _STDERR_CACHE[key] = cached
    return cached[1]


def _status_line(console: Any, msg: str, color: str, glyph: str) -> None:
    """Human status lines; routed to stderr in JSON mode so stdout stays pure JSON."""
    if _FLAGS["json"]:
        stderr_console(console).print(f"[bold {color}]{glyph}[/bold {color}] {msg}")
    else:
        console.print(f"[bold {color}]{glyph}[/bold {color}] {msg}")


def _envelope(data: Any, command: str | None) -> dict[str, Any]:
    """Wrap command data in the stable lx JSON envelope."""
    return {
        "tool": "lx",
        "version": __version__,
        "command": command,
        "timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "data": data,
    }


def emit_json(
    console: Any,
    data: Any,
    *,
    command: str | None = None,
    ndjson: bool = False,
) -> None:
    """Print data as a JSON envelope (pretty by default, NDJSON per tick when watching)."""
    payload = _envelope(data, command)
    if ndjson:
        console.file.write(json.dumps(payload) + "\n")
        console.file.flush()
    else:
        console.print_json(data=payload)


def emit(ctx: Any, data: Any, *, command: str | None = None) -> bool:
    """Emit ``data`` as JSON when --json is active; return True if it did.

    Commands call this after collecting their payload and before rendering,
    so machine output never touches Rich formatting. When combined with
    --watch, each tick is emitted as newline-delimited JSON (NDJSON).
    """
    if not ctx.obj.data.get("json"):
        return False
    emit_json(
        ctx.obj.console,
        data,
        command=command,
        ndjson=bool(ctx.obj.data.get("watch")),
    )
    return True
