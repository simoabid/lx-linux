#!/usr/bin/env python3
"""lx — a modern, feature-rich CLI toolkit to power your Linux experience.

lx bundles dozens of system inspection, network, process, package, tweak,
security, cleanup, and service utilities behind a single, colorful command.

Usage:
    lx <command> [subcommand] [options]
    lx --help          # see all commands
    lx <command> --help # command help

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
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
