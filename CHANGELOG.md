# Changelog

All notable changes to `lx` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.0] - 2026-08-01

### Changed
- **Type-to-confirm for the most destructive operations.** `clean kernels`
  (type `purge`), `pkg remove` and `pkg autoremove` (type `remove`),
  `tweak restore` and `backup restore` (type `restore`) now require typing
  the action word instead of answering y/N. An empty line, end-of-input,
  or a wrong word aborts with exit 130; `-y` still bypasses the prompt so
  scripts keep working, and `--json` mode still refuses without `-y`
  (exit 2)
- `pkg remove` now confirms before removing anything — it previously ran
  `apt remove -y` immediately after the root gate (no prompt, no `-y`,
  no `--json` refusal)
- Every destructive prompt now goes through the single
  `confirm_destructive` helper with one hint style; the last raw
  `click.confirm` call sites (`clean cache`, `clean logs`,
  `pkg upgrade`) were migrated to it, and `clean kernels` no longer
  bypasses the `--json` refusal
- Version bumped to 0.4.0

## [0.3.0] - 2026-08-01

### Added (Phase 2 — six new command groups)
- **`lx fs`** — disk explorer: `usage [PATH]` (largest dirs/files with
  size bars), `large [PATH] --min` (top-N heap scan, no full listing),
  `inodes [PATH]` (fs inode pressure + per-dir counts), `mounts` (real
  filesystems with usage + inodes); never follows symlinks, skips
  pseudo-filesystems, permission errors never abort the scan
- **`lx sys`** — system deep-dive: `usb` (lsusb + sysfs fallback), `pci`
  (lspci + sysfs fallback), `modules` (/proc/modules with used-by),
  `env` (secrets redacted via key-pattern matching), `time` (timedatectl +
  NTP servers), `boot` (journalctl --list-boots with durations), `all`
- **`lx bench`** — pure-Python, no-root benchmarks with a 0–100 score
  against a reference desktop: `cpu` (SHA-256 hashing, multi-threaded),
  `memory` (comprehension-write + sum-read bandwidth), `disk` (temp-file
  write/read, always cleaned up), `all` (summary table + verdict);
  `--quick` shrinks workloads ~50× for smoke runs
- **`lx log`** — journal explorer: `show` (lines/since/until/priority/
  unit/regex grep), `errors` (priority err+, noisiest units first),
  `follow` (live stream, NDJSON in `--json`); structured errors on hosts
  without systemd instead of crashes
- **`lx cron`** — `list` (user crontab + /etc/crontab + /etc/cron.d,
  other users require root), `timers` (systemd list-timers, anchored
  regex parser for space-containing timestamps); read-only
- **`lx power`** — `battery` (psutil + sysfs merge: charge rate in W,
  Wh energy, design capacity, cycles, time left), `profiles`
  (powerprofilesctl + ACPI sysfs fallback), `all`; sysfs µ-units
  (µV/µA/µWh) converted to SI per documented attribute units
- New parsers in `lx/utils/parse.py`: `parse_modules`, `parse_boots`,
  `parse_timedatectl`, `parse_journal_json`, `parse_crontab`,
  `parse_timers`, `parse_power_supply`
- `lx service timers` now uses the shared `parse_timers` parser

### Changed
- `lx doctor` tool table updated for Phase 2 (`lsusb`, `timedatectl`
  added; `nmap` removed; usage strings point at the new commands)
- Version bumped to 0.3.0

### Added
- **PRO UX infrastructure (v0.2.0)** — scriptable, live, and self-describing:

  - Global flags, accepted anywhere: `--json` (machine-readable output),
    `--watch N` (live refresh, read-only commands only), `-q/--quiet`
    (suppress decorative output), `--no-color` (plus `NO_COLOR` env honor)
  - Every command now follows a collect → render split, so `--json` output
    is a stable envelope: `{"tool", "version", "command", "timestamp",
    "data"}`; `--watch` + `--json` emits newline-delimited JSON (NDJSON),
    one document per tick
  - `--watch` safety gate: mutating commands reject `--watch` (exit 2) via
    a per-command `watch_capable` marker resolved before invocation
  - `lx doctor` — environment diagnostics: Python/package versions, root
    and sudo access, systemd state, required/optional tools, PATH and
    permission checks, verdict panel; always exits 0
  - `lx completion {bash,zsh,fish}` — shell tab-completion scripts
  - `LX_WATCH_MAX_ITERS` env knob to bound watch loops (used by tests)

### Changed
- `--json` output for bare `lx` now emits an empty envelope instead of the
  banner; JSON mode routes status messages (`✓`/`✗`/`⚠`) to stderr so
  stdout stays parseable
- Internal refactor: all command modules split into `_collect_*` (JSON-safe
  dicts) and `_render_*` (Rich) functions; `lx sec audit` now scans SUID
  binaries once instead of twice

### Added (Phase 1 — deepened commands)
- **`lx info`** — new `--battery`, `--temps`, `--ip`, `--procs`, `--short`
  sections; boot time in the header
- **`lx net`** — `ping HOST [-c N]` (latency/jitter/loss %, float-accurate),
  `dns HOST` (A + AAAA with timing), `scan` (ARP/neighbour table, no root);
  `trace --hops`
- **`lx proc`** — `show PID` deep inspection (`--no-threads/--no-env/--no-fd/
  --no-cwd`, per-section error reporting), `tree --depth N`,
  `kill` confirms before signaling (`-y` to skip; `--json` requires `-y`)
- **`lx pkg`** — `list [-p GLOB]`, `info P`, `orphans`, `autoremove
  [--dry-run] [-y]`, `upgrade --dry-run`; `status` reports per-backend
  installed counts
- **`lx tweak`** — `show` covers all block devices' IO schedulers;
  `swap create --size 2G` / `swap remove` (swapfile + `# lx-managed swap`
  fstab marker); `trim [--dry-run]` (fstrim -av); `network` profile (BBR if
  available, TFO, fq, somaxconn); `restore [-y]` resets all lx tuning with a
  timestamped drop-in backup
- **`lx sec`** — `users`, `updates` (security-flagged pending updates),
  `worldwritable` (scans /etc /usr /var /opt, capped at 200 entries),
  `sshkeys` (per-user authorized_keys + host keys); `audit --format md`
  prints a Markdown report
- **`lx clean`** — `pip`, `docker` (reclaims `docker system df` space),
  `snap` (disabled revisions), `tmp [--days N]` (stale user-owned files);
  `--dry-run` + `-y` on all destructive subcommands; `report` gains
  actionable recommendations (pip/docker/snap/journal)
- **`lx service`** — group `--user` flag (user units, no root);
  `blame [-n N]`, `boot` (systemd-analyze + critical chain),
  `reload`, `daemon-reload`, `mask`/`unmask` (confirm + `-y`)
- **`lx backup`** — `create --exclude GLOB` (repeatable) + `--no-etc`;
  `restore --dest DIR` (no root needed) + `--dry-run` + `-y`;
  `verify ARCHIVE` (full gzip integrity check); `prune --keep N` keeps the
  N newest archives
- **`lx health`** — new checks: connectivity, pending updates, zombies;
  battery drops its weight when absent; `--check cpu,mem` filters checks
  (short aliases; unknown names exit 2)
- New parsers in `lx/utils/parse.py`: `parse_ping`, `parse_dns`, `parse_fstrim`,
  `parse_docker_df`, `parse_blame`, `parse_critical_chain`, `parse_authorized_keys`
- Mutating commands (tweak restore/swap/network, clean *, service mask/
  unmask, backup restore/prune) confirm before acting; in `--json` mode
  without `-y` they exit 2 with a stderr hint instead of prompting

## [0.1.0] - 2026-08-01

- Initial release:

  - `lx info` — system information (OS, CPU, memory, disk, GPU, network)
    with Rich gauges and per-section flags (`--cpu`, `--mem`, `--disk`,
    `--gpu`, `--net`)
  - `lx net` — network tools: interfaces (`iface`), listening/established
    sockets (`ports`), public IP (`ip`), download speed test (`speed`),
    traceroute (`trace`), and a combined `all` run
  - `lx proc` — process manager: sortable `top`, substring `find`, `tree`,
    signal/kill by pattern (`kill`), and current-user processes (`me`)
  - `lx pkg` — unified package manager wrapper with auto-detection for
    apt, dnf, pacman, zypper, apk, flatpak, and snap (`status`, `search`,
    `update`, `upgrade`, `install`, `remove`, `purge`)
  - `lx tweak` — system tuning: `swappiness`, `max-files` (ulimits),
    CPU `governor`, `bbr`, a curated `interactive` profile, generic `set`,
    and read-only `show`; persistent settings land in
    `/etc/sysctl.d/90-lx.conf`
  - `lx sec` — security audit: SSH configuration + findings, listening
    ports, SUID/SGID scan, kernel hardening parameters, firewall checks,
    and an `audit` report with severity ranking; `hardssh` writes a
    hardening drop-in
  - `lx clean` — cleanup: `cache`, `logs` (journal vacuum + rotated-log
    trimming), `kernels` (old-kernel purge), and a read-only `report`
  - `lx service` — systemd wrapper: `status`, filtered `list`, `failed`,
    journal `log`, `start`/`stop`/`restart`, `enable`/`disable`, `timers`
  - `lx backup` — dotfile and system-config `create`/`list`/`restore`
    (tar.gz archives with dry-run preview)
  - `lx health` — weighted 0–100 system health score across eight checks
  - Safe-by-default behavior: root required for all mutations, explicit
    confirmation prompts, non-zero exit codes on failure (see README)
  - Man page (`docs/lx.1`) and `install.sh` installer
