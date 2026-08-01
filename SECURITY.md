# Security Policy

`lx` is a system administration tool. It inspects your machine and can make
changes when run as root, so we take security seriously.

## Supported versions

| Version | Supported          |
|---------|--------------------|
| 0.1.x   | :white_check_mark: |
| < 0.1   | :x:                |

## Reporting a vulnerability

**Do not open a public issue for security vulnerabilities.** Please report
them privately:

- Open a [private advisory](https://github.com/simoabid/lx-linux/security/advisories/new) on GitHub (preferred).

Please include:

- The `lx` version (`lx --version`) and your OS/distro + kernel
- A description of the vulnerability and its impact
- Steps to reproduce, and a proof-of-concept if available
- Any suggested fix

You can expect:

- An acknowledgment within **48 hours**
- A status update within **5 business days**
- Credit for the finding in the release notes (if you want it)

## Scope

Things we care about:

- **Command injection** — any subprocess invocation that interpolates user
  input unsafely (see `lx/utils/shell.py` — everything must be arg-array or
  strictly quoted).
- **Privilege handling** — commands that require root must fail *before*
  doing partial work, and never run with elevated privileges unnecessarily.
- **Path traversal** — `lx backup restore` extraction is restricted via
  tarfile's `filter="tar"`; regressions here are critical.
- **Data loss** — destructive commands must confirm and be accurate about
  what they delete.

Out of scope: vulnerabilities in upstream dependencies (report those to the
respective projects), and intentional features like `lx proc kill`.

## Security-conscious development

- All shell execution goes through `lx.utils.shell.run()` — never build raw
  shell strings with user input.
- `sudo -n` is used so commands fail fast instead of prompting/parking.
- Destructive operations require root **and** explicit confirmation.
