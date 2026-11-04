#!/usr/bin/env bash
# Boot-time autospawn for a standing Discord handoff thread.
#
# Wraps supervisor.spawn_claude_handoff(thread_id) so a systemd --user unit (or
# launchd agent) can recreate the tmux + Claude session for a fixed thread
# whenever the user's service manager starts (e.g. after reboot).
#
# Idempotent — supervisor short-circuits if the tmux session is already alive.
# Usage: autospawn_handoff.sh <thread_id>
#   or set HANDOFF_THREAD_ID in the environment.

set -euo pipefail

THREAD_ID="${1:-${HANDOFF_THREAD_ID:-}}"
if [[ -z "$THREAD_ID" ]]; then
  echo "usage: $0 <thread_id>  (or set HANDOFF_THREAD_ID)" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

: "${CLAUDE_BIN:=$(command -v claude || echo "$HOME/.local/bin/claude")}"
: "${HANDOFF_CWD:=$HOME}"
: "${HANDOFF_ENV_FILE:=$HOME/.config/discord-bot-handoff/discord.env}"
# Python interpreter that has the requirements installed (venv recommended).
: "${HANDOFF_PYTHON:=python3}"
export CLAUDE_BIN HANDOFF_CWD HANDOFF_ENV_FILE

exec "$HANDOFF_PYTHON" - "$THREAD_ID" "$SCRIPT_DIR" <<'PY'
import asyncio, sys
sys.path.insert(0, sys.argv[2])
from supervisor import spawn_claude_handoff
thread_id = sys.argv[1]
ok = asyncio.run(spawn_claude_handoff(thread_id))
raise SystemExit(0 if ok else 1)
PY
