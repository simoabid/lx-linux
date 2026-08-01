<div align="center">

# lx

**Power your Linux experience** — a modern, feature-rich CLI toolkit that bundles
system inspection, network tools, process management, package handling, tuning,
security auditing, cleanup, services, and backups behind a single colorful
command.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9+-blue.svg)](pyproject.toml)
[![CI](https://github.com/simoabid/lx-linux/actions/workflows/ci.yml/badge.svg)](https://github.com/simoabid/lx-linux/actions/workflows/ci.yml)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

```
 ┌──────────────────────────────────────────────────────────┐
 │  lx · power your Linux experience                        │
 │  info · net · proc · pkg · tweak · sec · clean · service │
 │  backup · health                                         │
 └──────────────────────────────────────────────────────────┘
```

</div>

`lx` is designed for Linux users who want one consistent, well-organized tool
instead of remembering twenty different commands and their distro-specific
flags. Everything is auto-detected, colorized with [Rich], and safe by default:
destructive operations require root and always ask for confirmation.

## Table of Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Command Reference](#command-reference)
- [Usage Examples](#usage-examples)
- [Safety & Permissions](#safety--permissions)
- [Exit Codes](#exit-codes)
- [Development](#development)
- [Project Structure](#project-structure)
- [Continuous Integration](#continuous-integration)
- [Contributing](#contributing)
- [License](#license)

## Features

| Category | Command | What it does |
|----------|---------|--------------|
| System info | `lx info` | OS, kernel, uptime, CPU + load gauges, memory, disk bars, GPU, network I/O |
| Network | `lx net` | Interfaces, listening ports, public IP, download speed test, traceroute |
| Processes | `lx proc` | Top by CPU/mem/RSS, find, process tree, signal/kill, your processes |
| Packages | `lx pkg` | Unified wrapper: apt, dnf, pacman, zypper, apk, flatpak, snap |
| Tuning | `lx tweak` | Swappiness, open-file limits, sysctl, CPU governor, BBR, curated profiles |
| Security | `lx sec` | Full audit: SSH config, ports, SUID binaries, kernel hardening, firewall |
| Cleanup | `lx clean` | Cache, journal logs, old kernels, disk-space report |
| Services | `lx service` | systemd wrapper: status, list, failed, logs, start/stop/enable, timers |
| Backups | `lx backup` | Dotfile & system-config archive / restore |
| Health | `lx health` | Weighted 0–100 health score across 8 live checks |

Highlights:

- **One command, every distro** — package management and paths are detected
  automatically (Ubuntu, Fedora, Arch, openSUSE, Alpine, and more).
- **Beautiful, readable output** — Rich-powered tables, gauges, and colored
  severity levels; works in any modern terminal.
- **Safe by default** — nothing destructive happens without root *and*
  confirmation. Writes are confined to clearly documented drop-in files.
- **Zero hard dependencies on system tools** — optional utilities (`lspci`,
  `mtr`, `systemd`, …) are auto-detected and skipped gracefully when missing.
- **Lightweight** — pure Python, three small libraries, no daemons, no config
  files required to get started.

## Requirements

- **Python 3.9 or newer** (3.12/3.13 recommended)
- **Linux** (developed on Zorin/Ubuntu; designed for Debian, Fedora, Arch,
  openSUSE, Alpine, and other systemd or non-systemd distros)
- Python packages installed automatically: `rich`, `psutil`, `click`

Optional system tools, auto-detected at runtime:

| Tool | Used by | If missing |
|------|---------|------------|
| `lshw` / `lspci` | `lx info --gpu` | GPU section skipped gracefully |
| `mtr` / `traceroute` | `lx net trace` | Friendly install hint shown |
| `systemctl` / `journalctl` | `lx service` | Clear error message |
| `sysctl` | `lx tweak` | Tuning commands report failure |

## Installation

### Recommended: pipx

```bash
git clone https://github.com/simoabid/lx-linux.git
cd lx
pipx install .
```

### pip (user install)

```bash
git clone https://github.com/simoabid/lx-linux.git
cd lx
pip install --user .
```

### Install script (installs binary + man page)

```bash
git clone https://github.com/simoabid/lx-linux.git
cd lx
./install.sh
```

The script uses `pipx` when available and falls back to `pip --user`, then
installs the man page into `~/.local/share/man/man1`. Make sure
`~/.local/bin` is on your `PATH`.

### Development install

```bash
git clone https://github.com/simoabid/lx-linux.git
cd lx
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
```

## Quick Start

```bash
lx --help          # all commands
lx info            # full system snapshot
lx health          # overall system health score
lx net ports       # what's listening on this machine
lx sec audit       # full security audit
```

## Command Reference

### `lx info` — System information

```
Usage: lx info [OPTIONS]

Options:
  --cpu     Show only CPU details
  --mem     Show only memory
  --disk    Show only disk usage
  --gpu     Show GPU info
  --net     Show network I/O
  --all     Show everything (default)
```

Reads `/proc`, `sysfs`, and `psutil` — shows OS, kernel, uptime, CPU model and
governor, load-avg gauges, RAM/swap bars, real-disk usage (snap/pseudo
filesystems excluded), GPU, and cumulative network traffic.

### `lx net` — Network tools

```
Usage: lx net COMMAND [OPTIONS]

  iface                 List interfaces, addresses, traffic counters
  ports [-a] [-n N]     Listening sockets (+ established with -a)
  ip                    Print your public IP (multi-service fallback)
  speed [-s MB]         Download speed test (25 MB default, CDN fallback)
  trace HOST [--hops N] Traceroute via mtr or traceroute
  all                   interfaces + ports + public IP in one run
```

### `lx proc` — Process manager

```
Usage: lx proc COMMAND [OPTIONS]

  top [-n N] [-s cpu|mem|rss|time]   Top processes, sortable
  find NAME                          Find processes by command substring
  tree [NAME]                        Process tree (default: from PID 1)
  kill [-9] PATTERN                  Signal processes by substring match
  me                                 Processes owned by your user
```

### `lx pkg` — Unified package manager

```
Usage: lx pkg [OPTIONS] COMMAND

Options:
  -b, --backend [apt|dnf|pacman|zypper|apk|flatpak|snap]

  status     Show detected backends + installed package count
  search Q   Search packages
  update     Refresh package index (root)
  upgrade    Upgrade all packages (root, confirms first)
  install P  Install package(s), comma-separated accepted (root)
  remove P   Remove a package (root)
  purge      Clean local package caches (root)
```

The backend is auto-detected; override any command with `-b` (e.g.
`lx pkg -b flatpak install org.blender.Blender`).

### `lx tweak` — System tuning (all subcommands require root)

```
Usage: lx tweak COMMAND [OPTIONS]

  show                       Display current tunables, governors, IO scheduler
  swappiness N [0-200]       Set vm.swappiness (+vfs_cache_pressure), persists
  max-files N                Raise system-wide open-file limits (limits.conf)
  governor NAME              Set CPU governor (performance/powersave/…)
  bbr                        Enable BBR TCP congestion control (+ fq qdisc)
  interactive                Curated desktop/laptop-friendly profile
  set KEY VALUE              Set any sysctl tunable, optionally persist
```

Settings written by `--persist` (the default) go to
`/etc/sysctl.d/90-lx.conf`, which is respected by systemd and re-applied at
boot. Reversible: edit or remove that file.

### `lx sec` — Security audit

```
Usage: lx sec COMMAND [OPTIONS]

  audit [--no-suid]   Full audit: SSH, ports, firewall, sudoers, kernel
  ssh                 SSH server configuration + findings
  ports               Listening sockets
  suid                Scan SUID/SGID binaries
  hardssh             Write SSH hardening drop-in (root)
```

`lx sec audit` produces a severity-sorted findings report (critical → info).
`hardssh` writes `/etc/ssh/sshd_config.d/99-lx-hardening.conf` — it does **not**
restart sshd, and it warns you to verify key-based login first.

### `lx clean` — Cleanup & optimizer

```
Usage: lx clean COMMAND [OPTIONS]

  cache [-y]                Clear ~/.cache and /var/cache
  logs [--vacuum MB] [-y]   Vacuum systemd journal + trim rotated logs (root)
  kernels [--keep N] [-y]   Purge old kernel packages via apt (root)
  report                    Space report only — no changes
```

### `lx service` — systemd wrapper

```
Usage: lx service COMMAND [OPTIONS]

  status NAME     Full unit status
  list [--state S] [--type T]   List units (filter by state/type)
  failed          List failed units only
  log NAME [-n N] Tail a unit's journal (50 lines default)
  start|stop|restart NAME      (root)
  enable|disable NAME          (root)
  timers          List systemd timers
```

### `lx backup` — Dotfile & config backups

```
Usage: lx backup COMMAND [OPTIONS]

  create [-d DEST] [-t EXTRA ...]    Archive common dotfiles + /etc configs
  list [-d DEST]                     List existing archives
  restore ARCHIVE [--dry-run]        Preview or extract an archive
```

Defaults to `~/lx-backups/lx-backup-<timestamp>.tar.gz` and includes shell
rc files, gitconfig, tmux/vim config, common terminal/WM configs, SSH client
config, and key `/etc` files (fstab, hosts, grub, sysctl).

### `lx health` — Health score

```
Usage: lx health
```

Scores 8 weighted checks (CPU load, RAM, disk, swap, temperature, uptime,
failed units, zombies) into an overall 0–100 rating with per-check details.

## Usage Examples

```bash
# System snapshot — everything
lx info

# Just memory and disk
lx info --mem --disk

# What's listening on this machine, including established connections
lx net ports -a

# 50 MB download speed test
lx net speed -s 50

# Top 20 processes by memory
lx proc top -n 20 -s mem

# Find and inspect the tree of everything named 'codex'
lx proc find codex
lx proc tree codex

# Force-kill all firefox processes (be careful!)
lx proc kill -9 firefox

# Package management, auto-detected (apt here)
lx pkg status
lx pkg update
lx pkg upgrade -y
lx pkg install ripgrep

# Desktop-friendly tuning profile (persists across reboots)
sudo lx tweak interactive

# Enable BBR congestion control
sudo lx tweak bbr

# Full security audit, skipping the slow SUID scan
lx sec audit --no-suid

# Harden SSH (writes drop-in config; verify before restarting sshd!)
sudo lx sec hardssh

# How much space can I reclaim?
lx clean report

# Free up cache and shrink the journal
sudo lx clean cache -y
sudo lx clean logs --vacuum 50 -y

# Remove old kernels, keeping the newest 2
sudo lx clean kernels --keep 2 -y

# Back up your dotfiles
lx backup create
lx backup list

# Restart a service
sudo lx service restart networking

# Overall health score
lx health
```

## Safety & Permissions

`lx` follows a strict principle: **inspection is free, mutation asks twice.**

- Read-only commands (`info`, `net`, `proc`, `sec audit`, `clean report`,
  `service status/list/failed/log`, `health`, `backup list`) run as your
  normal user.
- Mutating commands (`tweak *`, `clean cache/logs/kernels`,
  `service start/stop/restart/enable/disable`, `pkg update/upgrade/install/remove/purge`,
  `sec hardssh`) require **root** and, except where `-y` is given, ask for
  explicit confirmation. Run them with `sudo lx …`.
- When a command needs root and you are not root, it fails with exit code 1
  and a hint — it never silently half-runs.

Files `lx` writes (always the *documented* drop-in locations):

| File | Written by |
|------|-----------|
| `/etc/sysctl.d/90-lx.conf` | `lx tweak swappiness`, `bbr`, `interactive`, `set --persist` |
| `/etc/security/limits.conf` | `lx tweak max-files` |
| `/etc/ssh/sshd_config.d/99-lx-hardening.conf` | `lx sec hardssh` |
| `~/lx-backups/lx-backup-*.tar.gz` | `lx backup create` |

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Runtime error (e.g. root required, command failed, no backend) |
| 2 | CLI usage error (unknown command/option) |
| 124 | A subprocess timed out |
| 130 | Interrupted (Ctrl-C or declined confirmation) |

## Development

```bash
# Setup
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

# Run the test suite
pytest -v

# With coverage
pytest --cov=lx --cov-report=term-missing

# Lint
ruff check lx/ tests/

# Auto-fix lint issues
ruff check lx/ tests/ --fix

# Build a wheel
pip wheel . -w dist/
```

Please keep the suite green and `ruff check` clean before opening a pull
request — CI enforces both.

## Project Structure

```
lx/
├── pyproject.toml          # packaging, metadata, tool config
├── install.sh              # pipx/pip + man page installer
├── README.md
├── LICENSE                 # MIT
├── CHANGELOG.md
├── CONTRIBUTING.md
├── SECURITY.md
├── docs/
│   └── lx.1                # man page
├── .github/
│   ├── workflows/ci.yml    # lint + test matrix
│   └── dependabot.yml
├── lx/
│   ├── __init__.py         # version
│   ├── __main__.py         # click root + dispatch + exit-code handling
│   ├── commands/
│   │   ├── info.py         # system info
│   │   ├── net.py          # network tools
│   │   ├── proc.py         # process manager
│   │   ├── pkg.py          # package manager wrapper
│   │   ├── tweak.py        # system tuning
│   │   ├── sec.py          # security audit
│   │   ├── clean.py        # cleanup
│   │   ├── service.py      # systemd wrapper
│   │   ├── backup.py       # dotfile backup
│   │   └── health.py       # health score
│   └── utils/
│       ├── context.py      # shared runtime context
│       ├── output.py       # Rich-based output helpers
│       ├── shell.py        # safe subprocess runner (sudo-aware, timeouts)
│       └── parse.py        # /proc & config parsers
└── tests/
    ├── test_utils.py       # parsers, shell runner, helpers
    ├── test_cli.py         # dispatch, help, exit codes
    └── test_commands.py    # command internals
```

## Continuous Integration

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push and
pull request:

- **Matrix**: Python 3.9, 3.10, 3.11, 3.12, 3.13 on Ubuntu latest
- **Lint**: `ruff check`
- **Tests**: `pytest` with coverage summary
- Dependencies are kept fresh by Dependabot (weekly, grouped)

## Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) for
the workflow (issue → branch → PR), code conventions, and testing guidance.
Security-related bugs should be reported privately per
[SECURITY.md](SECURITY.md).

## License

MIT — see [LICENSE](LICENSE).

---

<div align="center">

Built with [Rich], [psutil], and [Click].

[Rich]: https://github.com/Textualize/rich
[psutil]: https://github.com/giampaolo/psutil
[Click]: https://github.com/pallets/click

</div>
