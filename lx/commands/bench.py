"""lx bench — pure-Python CPU, memory and disk benchmarks (no deps, no root)."""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
import time
from pathlib import Path

import click
from rich.table import Table

from lx.utils.flags import apply_flags, json_option
from lx.utils.output import center_rule, emit, err

#: One unit of CPU work: a SHA-256 update over a 1 MiB block (releases the GIL,
#: so threads scale on real cores).
_WORK_BLOCK = b"x" * (1024 * 1024)

#: Reference rates for the 0–100 score. Rates are the *measured* throughput of
#: this benchmark's pure-Python workload (not raw hardware bandwidth): SHA-256
#: hashing for cpu, list-comprehension writes + sum() reads for memory, and
#: buffered file I/O for disk. A representative mid-range desktop scores ~75.
_REFERENCE = {"cpu": 1500.0, "memory": 700.0, "disk": 1024.0}

#: Shrinks every workload by this factor with --quick (smoke-test mode).
_QUICK_FACTOR = 50

_MB = 1024 * 1024


def _score(rate: float, kind: str) -> int:
    """Map a measured rate to a 0–100 score against the reference machine."""
    reference = _REFERENCE.get(kind, 1.0)
    if reference <= 0:
        return 0
    return int(max(0, min(100, rate / reference * 100)))


def _score_color(score: int) -> str:
    if score >= 75:
        return "green"
    if score >= 40:
        return "yellow"
    return "red"


# ---------------------------------------------------------------- cpu


def _bench_cpu(seconds: float, threads: int, quick: bool = False) -> dict:
    duration = max(0.05, seconds / _QUICK_FACTOR if quick else seconds)
    counts: list[int] = []
    lock = threading.Lock()

    def worker() -> None:
        n = 0
        digest = hashlib.sha256()
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            digest.update(_WORK_BLOCK)
            n += 1
        with lock:
            counts.append(n)

    workers = [threading.Thread(target=worker) for _ in range(max(1, threads))]
    started = time.monotonic()
    for worker_thread in workers:
        worker_thread.start()
    for worker_thread in workers:
        worker_thread.join()
    elapsed = max(1e-6, time.monotonic() - started)
    ops = sum(counts)
    ops_per_sec = ops / elapsed
    return {
        "kind": "cpu",
        "seconds": round(duration, 2),
        "threads": len(workers),
        "bytes_hashed": ops * len(_WORK_BLOCK),
        "ops_per_sec": round(ops_per_sec, 1),
        "per_thread": [round(n / elapsed, 1) for n in sorted(counts, reverse=True)],
        "score": _score(ops_per_sec, "cpu"),
    }


def _render_cpu(console, data: dict) -> None:
    score = data["score"]
    console.print(
        f"  cpu: [bold]{data['ops_per_sec']:,.0f} ops/s[/bold] "
        f"({data['threads']} thread(s), {data['seconds']:.2f}s)  "
        f"[{_score_color(score)}]score {score}/100[/{_score_color(score)}]"
    )
    if data["threads"] > 1:
        per = ", ".join(f"{n:,.0f}" for n in data["per_thread"])
        console.print(f"  [dim]per-thread: {per} ops/s[/dim]")
    console.print()


# ---------------------------------------------------------------- memory


def _bench_memory(mb: int, quick: bool = False) -> dict:
    size_mb = max(1, mb // _QUICK_FACTOR if quick else mb)
    count = size_mb * _MB // 8
    started = time.monotonic()
    buffer = [float(i) * 1.5 for i in range(count)]
    write_secs = max(1e-6, time.monotonic() - started)
    started = time.monotonic()
    checksum = sum(buffer)
    read_secs = max(1e-6, time.monotonic() - started)
    write_mbps = (size_mb / write_secs)
    read_mbps = (size_mb / read_secs)
    combined = (write_mbps + read_mbps) / 2
    return {
        "kind": "memory",
        "mb": size_mb,
        "write_mbps": round(write_mbps, 1),
        "read_mbps": round(read_mbps, 1),
        "checksum": round(checksum, 1),
        "score": _score(combined, "memory"),
    }


def _render_memory(console, data: dict) -> None:
    score = data["score"]
    console.print(
        f"  memory: write [bold]{data['write_mbps']:,.0f} MB/s[/bold] · "
        f"read [bold]{data['read_mbps']:,.0f} MB/s[/bold] "
        f"({data['mb']} MiB)  "
        f"[{_score_color(score)}]score {score}/100[/{_score_color(score)}]"
    )
    console.print()


# ---------------------------------------------------------------- disk


def _bench_disk(mb: int, path: str, quick: bool = False) -> dict:
    size_mb = max(1, mb // _QUICK_FACTOR if quick else mb)
    chunk = b"x" * _MB
    fd, tmp_path = tempfile.mkstemp(prefix="lx-bench-", dir=path, suffix=".tmp")
    os.close(fd)
    try:
        started = time.monotonic()
        remaining = size_mb
        with open(tmp_path, "wb") as fh:
            while remaining:
                fh.write(chunk)
                remaining -= 1
        write_secs = max(1e-6, time.monotonic() - started)
        started = time.monotonic()
        read_back = 0
        with open(tmp_path, "rb") as fh:
            while True:
                block = fh.read(_MB)
                if not block:
                    break
                read_back += len(block)
        read_secs = max(1e-6, time.monotonic() - started)
        write_mbps = size_mb / write_secs
        read_mbps = size_mb / read_secs
        combined = (write_mbps + read_mbps) / 2
        return {
            "kind": "disk",
            "path": tmp_path,
            "mb": size_mb,
            "write_mbps": round(write_mbps, 1),
            "read_mbps": round(read_mbps, 1),
            "bytes_verified": read_back,
            "score": _score(combined, "disk"),
        }
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _render_disk(console, data: dict) -> None:
    score = data["score"]
    console.print(
        f"  disk: write [bold]{data['write_mbps']:,.0f} MB/s[/bold] · "
        f"read [bold]{data['read_mbps']:,.0f} MB/s[/bold] "
        f"({data['mb']} MiB temp file)  "
        f"[{_score_color(score)}]score {score}/100[/{_score_color(score)}]"
    )
    console.print(f"  [dim]temp file {data['path']} (removed after the run)[/dim]")
    console.print()


# ---------------------------------------------------------------- command


@click.group("bench")
@click.pass_context
def bench(ctx: click.Context) -> None:
    """Pure-Python CPU, memory and disk benchmarks (no root needed)."""
    pass


@bench.command("cpu")
@click.option("-s", "--seconds", default=2.0, show_default=True, type=float, help="Benchmark duration.")
@click.option(
    "-t", "--threads", default=0, show_default=True, type=int, help="Threads (0 = all cores)."
)
@click.option("--quick", is_flag=True, help="Tiny smoke-test workload.")
@json_option
@click.pass_context
def _bench_cpu_cmd(
    ctx: click.Context,
    seconds: float,
    threads: int,
    quick: bool,
    json_mode: bool | None = None,
) -> None:
    """Benchmark CPU throughput (SHA-256 hashing)."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    thread_count = threads or (os.cpu_count() or 1)
    if ctx.obj.json:
        data = _bench_cpu(seconds, thread_count, quick)
    else:
        with console.status("Benchmarking CPU…", spinner="dots"):
            data = _bench_cpu(seconds, thread_count, quick)
    if emit(ctx, data, command="bench cpu"):
        return
    _render_cpu(console, data)


@bench.command("memory")
@click.option("--mb", default=256, show_default=True, type=int, help="Buffer size in MiB.")
@click.option("--quick", is_flag=True, help="Tiny smoke-test workload.")
@json_option
@click.pass_context
def _bench_mem_cmd(
    ctx: click.Context, mb: int, quick: bool, json_mode: bool | None = None
) -> None:
    """Benchmark memory write/read bandwidth."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    if ctx.obj.json:
        data = _bench_memory(mb, quick)
    else:
        with console.status("Benchmarking memory…", spinner="dots"):
            data = _bench_memory(mb, quick)
    if emit(ctx, data, command="bench memory"):
        return
    _render_memory(console, data)


@bench.command("disk")
@click.option("--mb", default=512, show_default=True, type=int, help="File size in MiB.")
@click.option(
    "--path",
    default=None,
    help="Directory for the temp file (default: system temp dir).",
)
@click.option("--quick", is_flag=True, help="Tiny smoke-test workload.")
@json_option
@click.pass_context
def _bench_disk_cmd(
    ctx: click.Context, mb: int, path: str | None, quick: bool, json_mode: bool | None = None
) -> None:
    """Benchmark disk write/read bandwidth on a temporary file."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    target = path or tempfile.gettempdir()
    if not Path(target).is_dir():
        err(console, f"benchmark directory does not exist: {target}")
        raise click.exceptions.Exit(1)
    if ctx.obj.json:
        data = _bench_disk(mb, target, quick)
    else:
        with console.status("Benchmarking disk…", spinner="dots"):
            data = _bench_disk(mb, target, quick)
    if emit(ctx, data, command="bench disk"):
        return
    _render_disk(console, data)


@bench.command("all")
@click.option("-s", "--seconds", default=2.0, show_default=True, type=float, help="CPU duration.")
@click.option(
    "-t", "--threads", default=0, show_default=True, type=int, help="CPU threads (0 = all cores)."
)
@click.option("--mb-mem", default=256, show_default=True, type=int, help="Memory buffer MiB.")
@click.option("--mb-disk", default=512, show_default=True, type=int, help="Disk file MiB.")
@click.option(
    "--path", default=None, help="Directory for the disk temp file (default: system temp dir)."
)
@click.option("--quick", is_flag=True, help="Tiny smoke-test workload.")
@json_option
@click.pass_context
def _bench_all_cmd(
    ctx: click.Context,
    seconds: float,
    threads: int,
    mb_mem: int,
    mb_disk: int,
    path: str | None,
    quick: bool,
    json_mode: bool | None = None,
) -> None:
    """Run the CPU, memory, and disk benchmarks in sequence."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    target = path or tempfile.gettempdir()
    if not Path(target).is_dir():
        err(console, f"benchmark directory does not exist: {target}")
        raise click.exceptions.Exit(1)
    thread_count = threads or (os.cpu_count() or 1)
    center_rule(console, "lx bench")
    data = {
        "cpu": _bench_cpu(seconds, thread_count, quick),
        "memory": _bench_memory(mb_mem, quick),
        "disk": _bench_disk(mb_disk, target, quick),
    }
    if emit(ctx, data, command="bench all"):
        return
    _render_cpu(console, data["cpu"])
    _render_memory(console, data["memory"])
    _render_disk(console, data["disk"])
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("BENCH")
    table.add_column("SCORE", justify="right")
    table.add_column("VERDICT")
    for kind in ("cpu", "memory", "disk"):
        score = data[kind]["score"]
        table.add_row(kind, f"{score}/100", f"[{_score_color(score)}]{_verdict(score)}[/{_score_color(score)}]")
    console.print(table)


def _verdict(score: int) -> str:
    if score >= 75:
        return "great"
    if score >= 40:
        return "okay"
    return "slow"
