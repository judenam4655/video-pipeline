#!/usr/bin/env bash
#
# start.sh — brings up everything needed to run a job on macOS, short of
# loading the UXP panel inside Premiere (no CLI hook exists for that —
# it's a one-time manual click per Premiere launch).
#
# What this automates:
#   1. Checks `node` is on PATH (mcp_client.py spawns index.js as a
#      subprocess per job run — it doesn't need to be started manually,
#      but it does need node to exist).
#   2. Kills any stale process already bound to the bridge's WebSocket
#      port (39281), since a leftover instance from a previous run/crash
#      will make the new one fail with EADDRINUSE.
#   3. Opens Premiere Pro, if it isn't already running, so you just need
#      to load the MCP Bridge panel once it's up.
#   4. Loads ANTHROPIC_API_KEY from a local .env file if present and not
#      already exported, and fails fast with a clear message if it's
#      still missing — removal_agent.py reads it at import time, so a
#      missing key crashes uvicorn on startup, not on first request.
#   5. Starts uvicorn.
#
# Usage:
#   chmod +x start.sh
#   ./start.sh

set -euo pipefail

BRIDGE_WS_PORT=39281
APP_PORT=8000
PREMIERE_APP_NAME="Adobe Premiere Pro 2026"   # adjust to your installed version

cd "$(dirname "${BASH_SOURCE[0]}")"

if [ -f .venv/bin/activate ]; then
  source .venv/bin/activate
else
  echo "ERROR: .venv not found. Run: python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt" >&2
  exit 1
fi

# --- 1. node present? -------------------------------------------------
if ! command -v node >/dev/null 2>&1; then
  echo "ERROR: node not found on PATH. Install Node.js (e.g. 'brew install node') and re-run." >&2
  exit 1
fi
echo "[start.sh] node found: $(node --version)"

# --- 2. clear a stale bridge process on the WS port --------------------
STALE_PID=$(lsof -ti tcp:"${BRIDGE_WS_PORT}" -sTCP:LISTEN || true)
if [ -n "${STALE_PID}" ]; then
  echo "[start.sh] Killing stale process on port ${BRIDGE_WS_PORT} (PID ${STALE_PID})"
  kill -9 ${STALE_PID}
fi

# --- 3. open Premiere if it's not already running -----------------------
if ! pgrep -f "${PREMIERE_APP_NAME}" >/dev/null 2>&1; then
  echo "[start.sh] Opening ${PREMIERE_APP_NAME}..."
  open -a "${PREMIERE_APP_NAME}"
  echo "[start.sh] Waiting for Premiere to finish launching..."
  sleep 8
else
  echo "[start.sh] Premiere already running."
fi
echo "[start.sh] >>> Load the MCP Bridge panel in Premiere now if it isn't loaded already. <<<"

# --- 4. ANTHROPIC_API_KEY -----------------------------------------------
if [ -z "${ANTHROPIC_API_KEY:-}" ] && [ -f .env ]; then
  echo "[start.sh] Loading .env"
  set -a
  source .env
  set +a
fi
if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "ERROR: ANTHROPIC_API_KEY is not set and no .env provided it. Export it or add it to .env." >&2
  exit 1
fi
echo "[start.sh] ANTHROPIC_API_KEY is set."

# --- 5. start the orchestrator -------------------------------------------
echo "[start.sh] Starting uvicorn on port ${APP_PORT}..."
exec python3 -m uvicorn main:app --app-dir orchestrator --reload --port "${APP_PORT}"