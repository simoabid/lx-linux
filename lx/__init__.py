#!/usr/bin/env python3
"""lx — a modern, feature-rich CLI toolkit to power your Linux experience.

lx bundles dozens of system inspection, network, process, package, tweak,
security, cleanup, and service utilities behind a single, colorful command.

Usage:
    lx <command> [subcommand] [options]
    lx --help          # see all commands
    lx <command> --help # command help
    lx --json info     # machine-readable output
    lx --watch 2 health # live refresh every 2 seconds (read-only commands)
    lx doctor          # environment diagnostics
    lx completion bash # shell tab-completion script

Global flags:
    --json       machine-readable JSON envelope on stdout
    --watch N    repeat a read-only command every N seconds (NDJSON + --json)
    -q/--quiet   suppress decorative output (headers, hints)
    --no-color   disable ANSI colors (NO_COLOR env var is honored too)

Commands:
    info        System information (CPU, RAM, disk, GPU, uptime)
    net         Network tools (interfaces, ports, public IP, speed test)
    proc        Process manager (list, find, kill, tree)
    pkg         Unified package manager wrapper (apt/dnf/pacman)
    tweak       System tuning (swappiness, ulimits, sysctl governors)
    sec         Security audit (open ports, sudo users, SSH hardening)
    clean       Cleanup & optimizer (cache, old kernels, logs)
    service     systemd wrapper (status, start/stop, enable/disable)
    backup      Dotfile & config backup utility
    health      Overall system health score
    fs          Disk explorer (usage, largest files, inodes, mounts)
    sys         System deep-dive (USB/PCI, modules, env, time, boots)
    bench       Pure-Python CPU/memory/disk benchmarks
    log         Journal explorer (filters, error focus, follow)
    cron        Crontab and systemd timer overview
    power       Battery status, charge rates, power profiles
    doctor      Environment diagnostics (deps, permissions, tools)
    completion  Shell completion scripts (bash/zsh/fish)
"""

__version__ = "0.4.0"
__all__ = ["__version__"]
