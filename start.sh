#!/bin/sh
set -u

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT_DIR"

# Runtime source is isolated from the static website and the publisher package.
export PYTHONPATH="$ROOT_DIR/runtime${PYTHONPATH:+:$PYTHONPATH}"
export ZOMBCASH_PROJECT_MEMORY_FILE="$ROOT_DIR/runtime/project_memory.json"

SUPERVISOR_PIDS=""

supervise() {
    name="$1"
    shift
    (
        child_pid=""
        stop_worker() {
            if [ -n "$child_pid" ]; then
                kill "$child_pid" 2>/dev/null || true
                wait "$child_pid" 2>/dev/null || true
                child_pid=""
            fi
            exit 0
        }
        trap stop_worker INT TERM HUP EXIT

        while :; do
            "$@" &
            child_pid=$!
            if wait "$child_pid"; then
                exit_code=0
            else
                exit_code=$?
            fi
            child_pid=""
            echo "[supervisor] $name exited with code $exit_code; restarting in 10 seconds."
            sleep 10
        done
    ) &
    SUPERVISOR_PIDS="$SUPERVISOR_PIDS $!"
}

cleanup() {
    trap - INT TERM HUP EXIT
    echo "[runtime] stopping supervised workers"
    for pid in $SUPERVISOR_PIDS; do
        kill "$pid" 2>/dev/null || true
    done
    for pid in $SUPERVISOR_PIDS; do
        wait "$pid" 2>/dev/null || true
    done
    exit 0
}

trap cleanup INT TERM HUP EXIT

# Exactly one process slot for each existing service; no duplicate Telegram poller or RSS worker.
supervise "ZombieCash API" python -u runtime/api_server.py
supervise "Telegram bot" python -u runtime/bot.py
supervise "News worker" python -u runtime/news_worker.py
supervise "Meme Radar" python -u runtime/radar_bot.py
supervise "Boost Monitor" python -u runtime/boost_monitor.py

# Optional service remains opt-in, matching the previous deployment.
if [ "${MOLTBOOK_ENABLED:-false}" = "true" ]; then
    supervise "Moltbook agent" python -u runtime/moltbook_agent.py
fi

echo "[runtime] all configured workers are supervised"
while :; do
    sleep 3600
done
