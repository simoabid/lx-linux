#!/usr/bin/env bash
# install.sh — install lx into ~/.local/bin
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"

echo "==> Installing lx from ${SCRIPT_DIR}"

if ! command -v python3 >/dev/null 2>&1; then
    echo "error: python3 is required" >&2
    exit 1
fi

# Prefer pipx for an isolated install; fall back to --user pip
if command -v pipx >/dev/null 2>&1; then
    echo "==> Using pipx"
    pipx install --force "${SCRIPT_DIR}"
else
    echo "==> pipx not found, using pip --user"
    python3 -m pip install --user --upgrade "${SCRIPT_DIR}"
fi

mkdir -p "${BIN_DIR}"

# Install the man page (best-effort; harmless if man dirs are read-only)
MAN_DIR="${HOME}/.local/share/man/man1"
if [ -d "${SCRIPT_DIR}/docs" ]; then
    mkdir -p "${MAN_DIR}"
    if [ -w "${MAN_DIR}" ]; then
        install -m 0644 "${SCRIPT_DIR}/docs/lx.1" "${MAN_DIR}/lx.1" 2>/dev/null \
            && echo "==> Man page installed to ${MAN_DIR}" \
            || echo "==> Warning: could not write man page to ${MAN_DIR}"
    fi
fi

echo
echo "==> Done!"
echo
echo "Make sure ${BIN_DIR} is on your PATH:"
echo "  export PATH=\"${BIN_DIR}:\$PATH\""
echo
echo "Try it out:"
echo "  lx --help"
echo "  lx info"
echo "  lx health"
