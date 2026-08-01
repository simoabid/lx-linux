"""Re-usable output helpers built on top of Rich."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.text import Text


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


def _human_bytes(n: float) -> str:
    """Convert bytes to a compact human string (e.g. '7.5 GiB')."""
    f = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if abs(f) < 1024:
            return f"{f:.1f} {unit}"
        f /= 1024
    return f"{f:.1f} EiB"


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
    """A centered decorative rule with text in the middle."""
    width = console.width or 80
    side = max(2, (width - len(text) - 4) // 2)
    console.print(f"[dim]{char * side}[/dim] [bold]{text}[/bold] [dim]{char * side}[/dim]")


def warn(console: Any, msg: str) -> None:
    console.print(f"[bold yellow]⚠[/bold yellow] {msg}")


def ok(console: Any, msg: str) -> None:
    console.print(f"[bold green]✓[/bold green] {msg}")


def err(console: Any, msg: str) -> None:
    console.print(f"[bold red]✗[/bold red] {msg}")
