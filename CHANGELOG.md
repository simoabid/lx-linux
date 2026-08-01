# Changelog

All notable changes to `lx` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial public release: `lx` v0.1.0 feature set, including:

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

### Changed
- (none — first release)

### Fixed
- (none — first release)

## [0.1.0] - 2026-08-01

- Initial release (feature set above).
