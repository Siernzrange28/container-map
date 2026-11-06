#!/usr/bin/env bash
# demo.sh — the stranger run: `container-map demo` without installing anything. It builds a fresh
# demo workspace (never beside this file), puts two threads through the real engine with a scripted
# agent, and serves the map. Needs python3 and git. No network, no login, no agent CLI.
# Arguments go to the command: --no-serve, --port N, --static (the old one-page demo), --root DIR.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
PYTHON=${CM_PYTHON:-python3}    # the Python the CLI runs on (it may be a venv's)
exec "$PYTHON" cli.py demo "$@"
