# Contributing to lx

Thanks for wanting to contribute! `lx` is a small, focused project — here's
how to help without friction.

## Ground rules

- **Keep it Linux-only, distro-agnostic.** Everything must auto-detect
  (package managers, available tools, sysfs paths). No hard-coded Ubuntu paths.
- **Read-only by default.** New commands that inspect are welcome; mutating
  commands must require root and confirm before acting (see existing
  `clean`/`tweak`/`pkg` commands for the pattern).
- **Fail gracefully.** If a system tool is missing, print a friendly message
  and exit non-zero — never crash with a traceback.
- **Match the style.** Type hints on every signature, docstrings on commands,
  no comments unless they explain *why*. Keep `ruff check` clean.

## Development setup

```bash
git clone https://github.com/OWNER/lx.git
cd lx
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
```

## Workflow

1. **Open an issue first** describing what you want to change or fix (unless
   it's a trivial typo). This avoids wasted work.
2. Create a branch: `git checkout -b feat/your-feature` (or
   `fix/your-fix`).
3. Make your changes with tests. For a new subcommand, add at least one test
   that exercises the CLI wiring (`tests/test_cli.py`) and any pure logic.
4. Run the checks locally:

   ```bash
   ruff check lx/ tests/
   pytest -v
   ```

5. Open a pull request against `main`. CI runs the same checks on Python
   3.9–3.13, so make sure everything is green before requesting review.

## Testing conventions

- Unit tests live in `tests/` and mirror the module layout.
- Tests must **never** touch the real system: mock subprocess calls, use
  `tmp_path` fixtures, and avoid running `sudo`-requiring code paths.
- New commands that only inspect the live system should be tested through
  their internal helpers, not by executing them against real hardware.

## Commit style

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add lx net whois command
fix: handle missing traceroute gracefully
docs: expand README command reference
test: cover exit codes for root-guarded commands
```

## Project layout reminder

```
lx/commands/    one module per top-level command group
lx/utils/       shared helpers (output, shell, parsers, context)
tests/          pytest suite
docs/lx.1       man page (update it when CLI surface changes)
```

Thank you for contributing!
