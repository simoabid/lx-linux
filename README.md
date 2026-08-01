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
 │  backup · health · fs · sys · bench · log · cron · power │
 │  doctor · completion                                     │
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
- [Scripting & Live Output](#scripting--live-output)
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
| Disk explorer | `lx fs` | Directory usage, largest files, inodes, real filesystems |
| Deep dive | `lx sys` | USB/PCI, kernel modules, environment, time/NTP, boot history |
| Benchmarks | `lx bench` | Pure-Python CPU, memory and disk benchmarks (0–100 score) |
| Journal | `lx log` | Filtered journal views, error focus, live follow |
| Scheduling | `lx cron` | Crontab overview + systemd timers (read-only) |
| Power | `lx power` | Deep battery status, charge rates, power profiles |

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
| `lshw` / `lspci` | `lx info --gpu`, `lx sys pci` | GPU/PCI section skipped gracefully |
| `lsusb` | `lx sys usb` | sysfs fallback (bus numbers only) |
| `mtr` / `traceroute` | `lx net trace` | Friendly install hint shown |
| `systemctl` / `journalctl` | `lx service`, `lx sys boot`, `lx log`, `lx cron timers` | Clear error message |
| `timedatectl` | `lx sys time` | Fallback to `/etc/timezone` |
| `powerprofilesctl` | `lx power profiles` | ACPI sysfs fallback, else hint |
| `crontab` | `lx cron list` | Clear error message |
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

## Scripting & Live Output

Every command accepts four global flags (also accepted after the subcommand):

| Flag | Meaning |
|------|---------|
| `--json` | Emit a machine-readable JSON envelope on stdout |
| `--watch N` | Repeat a read-only command every N seconds |
| `-q, --quiet` | Suppress decorative output (headers, hints) |
| `--no-color` | Disable ANSI colors (the `NO_COLOR` env var is honored too) |

### JSON envelope

All `--json` output uses a stable envelope:

```json
{
  "tool": "lx",
  "version": "0.4.0",
  "command": "health",
  "timestamp": "2026-08-01T19:00:00.000000+00:00",
  "data": { }
}
```

The `data` payload differs per command and is safe to feed into `jq`:

```bash
lx --json health | jq '.data.verdict'
lx sec ports --json | jq '.data.ports'
lx doctor --json | jq '.data.verdict'
```

Status messages (`✓`, `✗`, `⚠`) are routed to stderr in JSON mode, so stdout
stays parseable.

### Live refresh (`--watch`)

`--watch N` re-runs a command every N seconds. Only **read-only** commands
support it — mutating commands reject `--watch` with exit code 2 *before*
anything runs. With `--json`, each tick is one newline-delimited JSON (NDJSON)
document:

```bash
lx --watch 2 health              # live-refresh the scoreboard
lx --watch 5 --json sec ports    # NDJSON stream: one doc per tick
```

Set `LX_WATCH_MAX_ITERS=N` to cap the loop (handy in scripts/tests).

### Environment diagnostics

```bash
lx doctor          # Python/deps, root & sudo access, systemd, tools, PATH
lx doctor --json   # same, as JSON (always exits 0)
```

`lx doctor` checks your runtime environment and reports what's missing
(optional tools, permissions, sudo) without changing anything.

### Shell completion

```bash
lx completion bash >> ~/.bashrc
lx completion zsh  >> ~/.zshrc
lx completion fish >> ~/.config/fish/completions/lx.fish
```

## Command Reference

### `lx info` — System information

```
Usage: lx info [OPTIONS]

Options:
  --cpu       Show only CPU details
  --mem       Show only memory
  --disk      Show only disk usage
  --gpu       Show GPU info
  --net       Show network I/O
  --battery   Show battery status (laptops)
  --temps     Show temperature sensors
  --ip        Show IP addresses per interface
  --procs     Show top 5 processes by CPU
  --short     One-line summary
  --all       Show everything (default)
```

Reads `/proc`, `sysfs`, and `psutil` — shows OS, kernel, uptime (incl. boot
time), CPU model and governor, load-avg gauges, RAM/swap bars, real-disk
usage (snap/pseudo filesystems excluded), GPU, cumulative network traffic,
battery status, temperatures (color-coded), per-interface IPs, and the top-5
CPU consumers.

### `lx net` — Network tools

```
Usage: lx net COMMAND [OPTIONS]

  iface                 List interfaces, addresses, traffic counters
  ports [-a] [-n N]     Listening sockets (+ established with -a)
  ip                    Print your public IP (multi-service fallback)
  speed [-s MB]         Download speed test (25 MB default, CDN fallback)
  trace HOST [--hops N] Traceroute via mtr or traceroute
  ping HOST [-c N]      Latency + jitter (min/avg/max, loss %)
  dns HOST              Resolve a hostname (A + AAAA, timing)
  scan                  Neighbour/ARP table scan (no root, no nmap)
  all                   interfaces + ports + public IP in one run
```

### `lx proc` — Process manager

```
Usage: lx proc COMMAND [OPTIONS]

  top [-n N] [-s cpu|mem|rss|time]   Top processes, sortable
  find NAME                          Find processes by command substring
  tree [NAME] [--depth N]            Process tree (default: from PID 1)
  kill [-9] [-y] PATTERN             Signal processes (confirms first!)
  show PID [--no-threads/--no-env/--no-fd/--no-cwd]
                                     Deep-inspect one process
  me                                 Processes owned by your user
```

`lx proc kill` now lists the matching processes and asks for confirmation
before signaling (skip with `-y`; `--json` requires `-y`). `lx proc show`
reports per-section errors (e.g. `AccessDenied` on env/fds for other users'
processes) instead of failing the whole command.

### `lx pkg` — Unified package manager

```
Usage: lx pkg [OPTIONS] COMMAND

Options:
  -b, --backend [apt|dnf|pacman|zypper|apk|flatpak|snap]

  status           Show detected backends + installed package count
  list [-p GLOB]   List installed packages, optionally filtered
  info P           Show package details (Key: value)
  search Q         Search packages
  update           Refresh package index (root)
  upgrade [--dry-run] [-y]   Upgrade all packages (root, confirms first)
  install P        Install package(s), comma-separated accepted (root)
  remove P [-y]    Remove a package (root, type 'remove' to confirm)
  purge            Clean local package caches (root)
  orphans          List orphaned packages (read-only)
  autoremove [--dry-run] [-y]  Remove orphaned packages (root, type 'remove')
```

`pkg remove` and `pkg autoremove` are **type-to-confirm**: you must type
`remove` to proceed (anything else — including empty input — aborts).
`-y` skips the prompt for scripting; `--json` without `-y` exits 2.

The backend is auto-detected; override any command with `-b` (e.g.
`lx pkg -b flatpak install org.blender.Blender`). `pkg status` now reports
per-backend installed counts for every detected manager, not just the
primary one.

### `lx tweak` — System tuning (most subcommands require root)

```
Usage: lx tweak COMMAND [OPTIONS]

  show                       Display tunables, governors, IO schedulers (all block devices)
  swappiness N [0-200]       Set vm.swappiness (+vfs_cache_pressure), persists
  max-files N                Raise system-wide open-file limits (limits.conf)
  governor NAME              Set CPU governor (performance/powersave/…)
  bbr                        Enable BBR TCP congestion control (+ fq qdisc)
  interactive                Curated desktop/laptop-friendly profile
  network                    Curated network profile (BBR if available, TFO, somaxconn)
  swap create --size 2G      Create + enable a swapfile, persisted via fstab
  swap remove                Disable + delete the swapfile, clean fstab
  trim [--dry-run]           TRIM mounted filesystems (fstrim -av)
  restore [-y]               Reset all lx-applied tuning (type 'restore'; drop-in backed up)
  set KEY VALUE              Set any sysctl tunable, optionally persist
```

Settings written by `--persist` (the default) go to
`/etc/sysctl.d/90-lx.conf`, which is respected by systemd and re-applied at
boot. `lx tweak restore` moves the drop-in to a timestamped `.bak` (never
hard-deletes it) and strips lx's `nofile` entries from limits.conf.

### `lx sec` — Security audit

```
Usage: lx sec COMMAND [OPTIONS]

  audit [--no-suid] [--format md|text]   Full audit (md = Markdown report)
  ssh                 SSH server configuration + findings
  ports               Listening sockets
  suid                Scan SUID/SGID binaries
  users               System users + sudo/wheel membership
  updates             Pending updates, security ones flagged
  worldwritable       Scan /etc /usr /var /opt for world-writable entries
  sshkeys             Per-user authorized_keys + host keys
  hardssh             Write SSH hardening drop-in (root)
```

`lx sec audit` produces a severity-sorted findings report (critical → info);
`--format md` prints a Markdown report suitable for redirecting to a file.
`hardssh` writes `/etc/ssh/sshd_config.d/99-lx-hardening.conf` — it does **not**
restart sshd, and it warns you to verify key-based login first.

### `lx clean` — Cleanup & optimizer

```
Usage: lx clean COMMAND [OPTIONS]

  cache [-y]                Clear ~/.cache and /var/cache
  pip [--dry-run] [-y]      Clear the pip wheel cache (~/.cache/pip)
  docker [--dry-run] [-y]   Report + prune docker reclaimable space (root)
  snap [--dry-run] [-y]     Remove disabled snap revisions
  tmp [--days N] [--dry-run] [-y]   Remove stale /tmp entries you own
  logs [--vacuum MB] [-y]   Vacuum systemd journal + trim rotated logs (root)
  kernels [--keep N] [-y]   Purge old kernel packages via apt (root, type 'purge')
  report                    Space report + cleanup recommendations
```

Every destructive `clean` subcommand supports `--dry-run` (report only) and
`--yes/-y` (skip confirmation); in `--json` mode a destructive run without
`-y` exits 2 with a hint instead of prompting. `clean kernels` is
**type-to-confirm**: you must type `purge` to proceed. `clean report` also
lists actionable recommendations (pip cache, docker, snap revisions,
journal size).

### `lx service` — systemd wrapper

```
Usage: lx service [--user] COMMAND [OPTIONS]

  status NAME            Full unit status
  list [--state S] [--type T]   List units (filter by state/type)
  failed                 List failed units only
  log NAME [-n N]        Tail a unit's journal (50 lines default)
  start|stop|restart NAME     (root, not needed with --user)
  reload NAME            Reload a unit's config (root, not needed with --user)
  daemon-reload          Reload systemd's unit definitions
  enable|disable NAME    (root, not needed with --user)
  mask|unmask NAME [-y]  Fully disable / re-enable a unit (confirms first)
  blame [-n N]           systemd-analyze blame — what slows the boot
  boot                   Boot-time breakdown + critical chain
  timers                 List systemd timers
```

`--user` (group flag, before the subcommand) operates on user units and
skips the root requirement, e.g. `lx service --user list --state running`.

### `lx backup` — Dotfile & config backups

```
Usage: lx backup COMMAND [OPTIONS]

  create [-d DEST] [-t EXTRA ...] [--exclude GLOB ...] [--no-etc]
                                 Archive common dotfiles + /etc configs
  list [-d DEST]                 List existing archives
  verify ARCHIVE                 Full integrity check (gzip decompression)
  prune [-d DEST] [--keep N] [--dry-run] [-y]
                                 Delete old backups, keep the N newest
  restore ARCHIVE [--dest DIR] [--dry-run] [-y]
                                 Preview or extract an archive (type 'restore')
```

Defaults to `~/lx-backups/lx-backup-<timestamp>.tar.gz` and includes shell
rc files, gitconfig, tmux/vim config, common terminal/WM configs, SSH client
config, and key `/etc` files (fstab, hosts, grub, sysctl). `restore --dest`
extracts to a directory instead of `/` (no root needed for a custom dest).

### `lx health` — Health score

```
Usage: lx health [--check NAME[,NAME] ...]
```

Scores weighted checks (CPU load, RAM, disk, failed units, connectivity,
pending updates, temperature, battery, swap, uptime, zombies) into an
overall 0–100 rating with per-check details. Optional checks (battery, swap,
uptime, zombies) drop their weight when unsupported, so laptop and desktop
scores are comparable. `--check cpu,mem` runs only the named checks
(short names accepted; unknown names exit 2).

### `lx fs` — Disk explorer

```
Usage: lx fs COMMAND [OPTIONS]

  usage [PATH] [-n N] [-d N]     Largest directories (and files) under PATH
  large [PATH] [-n N] [--min M]  Largest files >= MIN MiB (100 MiB default)
  inodes [PATH] [-n N] [-d N]    Inode usage + per-directory entry counts
  mounts                         Real filesystems with usage + inode stats
```

Scans never follow symlinks, skip pseudo-filesystems (`proc`, `sys`, `dev`,
`run`, `snap`), and report permission errors without aborting — partial
results always come back.

### `lx sys` — System deep-dive

```
Usage: lx sys COMMAND [OPTIONS]

  usb       USB devices (lsusb, sysfs fallback)
  pci       PCI devices (lspci, sysfs fallback)
  modules   Loaded kernel modules from /proc/modules
  env       Environment variables (secrets redacted!)
  time      Time zone, NTP state, configured time servers
  boot      Boot history with durations (journalctl --list-boots)
  all       Everything above in one run
```

`lx sys env` redacts values whose names match secret patterns
(`TOKEN`, `KEY`, `SECRET`, `PASSWORD`, `AWS_`, `GITHUB_`, `API`, …) so
credentials never leak into `--json` output.

### `lx bench` — Benchmarks (pure Python, no root)

```
Usage: lx bench COMMAND [OPTIONS]

  cpu [-s SECS] [-t THREADS]    SHA-256 hashing throughput
  memory [--mb N]               Write + read bandwidth on a float buffer
  disk [--mb N] [--path DIR]    Write + read bandwidth on a temp file
  all [options...]              Run all three, summary table with verdict
```

Each benchmark reports a 0–100 score against a reference mid-range desktop
(hash throughput, comprehension-write + `sum()`-read bandwidth, buffered file
I/O). `--quick` shrinks every workload ~50× for fast smoke runs. The temp
file is always removed, even on error.

### `lx log` — Journal explorer

```
Usage: lx log COMMAND [OPTIONS]

  show [-n N] [--since S] [--until U] [-p PRIORITY] [-u UNIT] [--grep RE]
                              Filtered journal view (regex-capable)
  errors [-n N] [--since S] [-u UNIT]
                              Error-priority entries, noisiest units first
  follow [-n N] [-p PRIORITY] [-u UNIT]
                              Stream new entries live (Ctrl-C to stop)
```

`show` and `errors` support `--watch`; `follow` streams raw JSON lines when
`--json` is given (NDJSON). Without systemd/journalctl, every command
degrades to a clear structured error — exit code stays 0 in `--json` mode.

### `lx cron` — Crontab & timers overview (read-only)

```
Usage: lx cron COMMAND [OPTIONS]

  list [--user U]   Crontab entries (you, /etc/crontab, /etc/cron.d)
  timers            Scheduled systemd timers
```

`list --user` shows another user's crontab only when run as root (hint
otherwise). Both support `--watch` and `--json`.

### `lx power` — Battery & power profiles

```
Usage: lx power COMMAND [OPTIONS]

  battery     Deep status: percent, charge rate (W), Wh, design capacity,
              cycles, time left
  profiles    Current power profile + available ones
  all         Battery + profiles together
```

Battery data merges psutil readings with sysfs attributes (µV/µA/µWh
converted to SI), so charge rates work even when psutil only reports a
percentage. All commands support `--watch`.

## Usage Examples

```bash
# System snapshot — everything
lx info

# Machine-readable output (pipe into jq, scripts, dashboards)
lx --json info | jq '.data.os'
lx health --json | jq '.data.overall'

# Live-refresh a read-only command
lx --watch 2 health

# Watch the health score as an NDJSON stream
lx --watch 5 --json health

# Check your environment
lx doctor

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

# Where is my disk space going?
lx fs usage
lx fs large --min 500

# USB devices, kernel modules, boot history
lx sys usb
lx sys modules
lx sys boot

# Quick benchmark, score against a reference desktop
lx bench all --quick

# Errors from the last hour, noisiest units first
lx log errors --since "1h ago"

# Stream journal entries live (Ctrl-C to stop)
lx log follow

# Cron jobs and systemd timers at a glance
lx cron list
lx cron timers

# Battery and power profile
lx power battery
lx power profiles
```

## Safety & Permissions

`lx` follows a strict principle: **inspection is free, mutation asks twice.**

- Read-only commands (`info`, `net`, `proc`, `tweak show`, `pkg status`,
  `sec audit/ports/ssh/suid`, `clean report`, `service status/list/failed/log/timers`,
  `backup list`, `health`, `doctor`, `fs`, `sys`, `bench`, `log show/errors`,
  `cron`, `power`) run as your normal user and support `--watch`.
- Mutating commands (`tweak *`, `clean cache/logs/kernels`,
  `service start/stop/restart/enable/disable`, `pkg update/upgrade/install/remove/purge`,
  `sec hardssh`) require **root** and, except where `-y` is given, ask for
  explicit confirmation. Run them with `sudo lx …`.
- The most destructive operations — `clean kernels`, `pkg remove`,
  `pkg autoremove`, `tweak restore`, `backup restore` — are
  **type-to-confirm**: you must type the action word (`purge`, `remove`, or
  `restore`) instead of answering y/N. A wrong word, an empty line, or
  end-of-input aborts with exit 130, so a stray Enter can never destroy
  your kernels or restore over your configs.
- `-y`/`--yes` skips every prompt for scripting; in `--json` mode a
  destructive run without `-y` refuses with exit 2 and a stderr hint, so
  scripts must opt in explicitly.
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
| 2 | CLI usage error, `--watch` used on a mutating command, or `--json` destructive run refused without `--yes` |
| 124 | A subprocess timed out |
| 130 | Interrupted (Ctrl-C, or declined/wrong typed confirmation) |

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
│   ├── __main__.py         # click root + dispatch + watch loop + exit codes
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
│   │   ├── health.py       # health score
│   │   ├── doctor.py       # environment diagnostics
│   │   ├── fs.py           # disk explorer
│   │   ├── sys.py          # system deep-dive
│   │   ├── bench.py        # benchmarks
│   │   ├── log.py          # journal explorer
│   │   ├── cron.py         # crontab & timers
│   │   └── power.py        # battery & profiles
│   └── utils/
│       ├── context.py      # shared runtime context
│       ├── output.py       # Rich output helpers + JSON envelope
│       ├── flags.py        # --json/--watch decorators + watch gate
│       ├── shell.py        # safe subprocess runner (sudo-aware, timeouts)
│       └── parse.py        # /proc & config parsers
└── tests/
    ├── test_utils.py       # parsers, shell runner, helpers
    ├── test_cli.py         # dispatch, help, exit codes
    ├── test_commands.py    # command internals
    ├── test_phase1.py      # Phase 1 feature tests
    ├── test_phase2.py      # fs/sys/bench/log/cron/power tests
    └── test_pro.py         # JSON envelope, watch loop, completion, doctor
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
