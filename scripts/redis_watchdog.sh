#!/usr/bin/env bash
# Redis Health Check & Auto-Restart Watchdog
#
# Usage:
#   Add to crontab for automated monitoring:
#   */2 * * * * /opt/options-dashboard/options-chain-analytics-dashboard/scripts/redis_watchdog.sh >> /var/log/redis_watchdog.log 2>&1
#
# Or run manually:
#   bash scripts/redis_watchdog.sh

set -euo pipefail

REDIS_CLI="${REDIS_CLI:-redis-cli}"
MAX_RETRIES=3
RETRY_DELAY=2  # seconds between retries
LOG_TAG="redis-watchdog"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [$LOG_TAG] $*"
}

check_redis() {
    "$REDIS_CLI" ping > /dev/null 2>&1
}

restart_redis() {
    log "Attempting to restart Redis via systemctl..."
    if systemctl restart redis-server 2>/dev/null; then
        log "systemctl restart redis-server succeeded"
    elif systemctl restart redis 2>/dev/null; then
        log "systemctl restart redis succeeded"
    else
        log "ERROR: Failed to restart Redis via systemctl"
        return 1
    fi

    # Wait for Redis to become available
    for i in $(seq 1 10); do
        if check_redis; then
            local clients
            clients=$("$REDIS_CLI" info clients 2>/dev/null | grep "^connected_clients:" | cut -d: -f2 | tr -d '\r')
            log "Redis is back up. Connected clients: ${clients:-unknown}"
            return 0
        fi
        sleep 1
    done

    log "ERROR: Redis did not come back up after restart"
    return 1
}

# --- Main ---

# First check: is Redis responding?
if check_redis; then
    exit 0
fi

log "WARNING: Redis is not responding to PING"

# Retry a few times before restarting (could be a momentary blip)
for attempt in $(seq 1 $MAX_RETRIES); do
    log "Retry $attempt/$MAX_RETRIES in ${RETRY_DELAY}s..."
    sleep "$RETRY_DELAY"
    if check_redis; then
        log "Redis recovered on retry $attempt"
        exit 0
    fi
done

log "Redis unresponsive after $MAX_RETRIES retries. Initiating restart."
if restart_redis; then
    log "Redis successfully restarted by watchdog"
else
    log "CRITICAL: Redis restart failed - manual intervention required"
    exit 1
fi
