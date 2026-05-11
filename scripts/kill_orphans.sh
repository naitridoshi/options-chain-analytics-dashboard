#!/usr/bin/env bash
# Kill orphaned Python processes from this project before PM2 starts.
#
# Problem: When PM2 restarts (e.g. after server reboot), old processes can become
# orphaned (PPID=1) if PM2 lost track of them. These zombies hold Redis connections
# in CLOSE_WAIT indefinitely, eventually exhausting all available ports.
#
# Usage:
#   bash scripts/kill_orphans.sh          # dry-run (show what would be killed)
#   bash scripts/kill_orphans.sh --force  # actually kill them
#
# Recommended: add to PM2 pre_start in ecosystem.config.js (already configured).

set -euo pipefail

PROJECT_NAME="options-chain-analytics-dashboard"
FORCE=false

if [[ "${1:-}" == "--force" ]]; then
    FORCE=true
fi

# Find Python processes belonging to this project that are orphaned (PPID=1)
# or not managed by the current PM2 daemon.
ORPHANS=()

while IFS= read -r line; do
    PID=$(echo "$line" | awk '{print $1}')
    PPID=$(echo "$line" | awk '{print $2}')
    CMD=$(echo "$line" | awk '{for(i=3;i<=NF;i++) printf "%s ", $i; print ""}')

    # Skip if PPID is a known PM2 process (has a PM2 parent)
    if [[ "$PPID" != "1" ]]; then
        # Check if parent is PM2 daemon
        PARENT_CMD=$(ps -p "$PPID" -o comm= 2>/dev/null || echo "gone")
        if [[ "$PARENT_CMD" == *"PM2"* || "$PARENT_CMD" == *"node"* ]]; then
            continue
        fi
    fi

    # This is an orphaned process
    ORPHANS+=("$PID")
done < <(ps -eo pid,ppid,args | grep "$PROJECT_NAME" | grep -E "(live_market_data|scheduler|fastapi)" | grep -v grep | grep -v kill_orphans)

if [[ ${#ORPHANS[@]} -eq 0 ]]; then
    echo "No orphaned processes found."
    exit 0
fi

echo "Found ${#ORPHANS[@]} orphaned process(es):"
for PID in "${ORPHANS[@]}"; do
    CMD=$(ps -p "$PID" -o args= 2>/dev/null || echo "already gone")
    PPID=$(ps -p "$PID" -o ppid= 2>/dev/null || echo "?")
    CONNS=$(ls -l /proc/"$PID"/fd 2>/dev/null | grep "socket" | wc -l || echo "?")
    echo "  PID=$PID PPID=$(echo $PPID) connections=$CONNS"
    echo "    $CMD"
done

if [[ "$FORCE" != "true" ]]; then
    echo ""
    echo "Dry run. Use --force to kill these processes."
    exit 1
fi

echo ""
echo "Killing orphaned processes..."
for PID in "${ORPHANS[@]}"; do
    if kill -0 "$PID" 2>/dev/null; then
        kill -9 "$PID" 2>/dev/null && echo "  Killed PID $PID" || echo "  Failed to kill PID $PID"
    else
        echo "  PID $PID already gone"
    fi
done

echo "Done. Orphaned processes killed."
