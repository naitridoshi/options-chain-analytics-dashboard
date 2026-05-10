#!/usr/bin/env bash
# Redis Setup Verification Script
#
# Run this on the server to verify all Redis resilience measures are active:
#   bash scripts/redis_verify.sh

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m'

pass() { echo -e "  ${GREEN}[PASS]${NC} $*"; }
fail() { echo -e "  ${RED}[FAIL]${NC} $*"; }
warn() { echo -e "  ${YELLOW}[WARN]${NC} $*"; }
info() { echo -e "  ${CYAN}[INFO]${NC} $*"; }

echo ""
echo "========================================="
echo " Redis Resilience Setup Verification"
echo "========================================="
echo ""

# ---- 1. Redis is running ----
echo "--- 1. Redis Service Status ---"
if redis-cli ping > /dev/null 2>&1; then
    pass "Redis is responding to PING"
else
    fail "Redis is NOT responding to PING"
    exit 1
fi

if systemctl is-active --quiet redis-server 2>/dev/null || systemctl is-active --quiet redis 2>/dev/null; then
    pass "Redis service is active"
else
    warn "Could not confirm Redis systemd service status"
fi

echo ""

# ---- 2. Systemd restart policy ----
echo "--- 2. Systemd Restart Policy ---"
SERVICE_NAME=""
if systemctl list-unit-files redis-server.service > /dev/null 2>&1; then
    SERVICE_NAME="redis-server.service"
elif systemctl list-unit-files redis.service > /dev/null 2>&1; then
    SERVICE_NAME="redis.service"
fi

if [[ -n "$SERVICE_NAME" ]]; then
    RESTART_POLICY=$(systemctl show -p Restart "$SERVICE_NAME" 2>/dev/null | cut -d= -f2)
    RESTART_SEC=$(systemctl show -p RestartUSec "$SERVICE_NAME" 2>/dev/null | cut -d= -f2)

    if [[ "$RESTART_POLICY" == "always" ]]; then
        pass "Restart policy is 'always'"
    else
        fail "Restart policy is '$RESTART_POLICY' (expected 'always')"
        info "Fix: sudo cp deploy/redis-systemd-override.conf /etc/systemd/system/$SERVICE_NAME.d/override.conf"
    fi

    # RestartUSec may be in microseconds (integer) or human-readable (e.g. "5s")
    if [[ -n "$RESTART_SEC" ]]; then
        if [[ "$RESTART_SEC" =~ ^[0-9]+$ ]]; then
            RESTART_SEC_S=$((RESTART_SEC / 1000000))
            info "Restart delay: ${RESTART_SEC_S}s"
        else
            info "Restart delay: $RESTART_SEC"
        fi
    fi

    # Check override file exists
    OVERRIDE_DIR="/etc/systemd/system/$SERVICE_NAME.d"
    if [[ -d "$OVERRIDE_DIR" ]]; then
        pass "Systemd override directory exists: $OVERRIDE_DIR"
        info "Override files:"
        ls -1 "$OVERRIDE_DIR"/*.conf 2>/dev/null | while read -r f; do
            info "  $(basename "$f")"
        done
    else
        warn "No systemd override directory found"
    fi
else
    warn "Redis systemd service not found"
fi

echo ""

# ---- 3. Redis config: timeout (idle connection reclaim) ----
echo "--- 3. Redis Configuration ---"

TIMEOUT=$(redis-cli CONFIG GET timeout 2>/dev/null | tail -1)
if [[ "$TIMEOUT" -gt 0 ]]; then
    pass "Client idle timeout: ${TIMEOUT}s (idle connections will be closed)"
else
    fail "Client idle timeout is 0 (disabled) - idle connections never close"
    info "Fix: add 'timeout 300' to /etc/redis/redis.conf"
fi

TCP_KEEPALIVE=$(redis-cli CONFIG GET tcp-keepalive 2>/dev/null | tail -1)
if [[ "$TCP_KEEPALIVE" -ge 60 ]]; then
    pass "TCP keepalive: ${TCP_KEEPALIVE}s"
else
    warn "TCP keepalive: ${TCP_KEEPALIVE}s (recommended: 60)"
fi

MAXCLIENTS=$(redis-cli CONFIG GET maxclients 2>/dev/null | tail -1)
info "Max clients: $MAXCLIENTS"

echo ""

# ---- 4. Current connection count ----
echo "--- 4. Current Connections ---"

CONNECTED=$(redis-cli info clients 2>/dev/null | grep "^connected_clients:" | cut -d: -f2 | tr -d '\r')
MAX_CLIENTS=$(redis-cli info clients 2>/dev/null | grep "^maxclients:" | cut -d: -f2 | tr -d '\r')

if [[ -n "$CONNECTED" ]]; then
    info "Connected clients: $CONNECTED / $MAX_CLIENTS"
    if [[ "$CONNECTED" -lt 50 ]]; then
        pass "Connection count is healthy (< 50)"
    elif [[ "$CONNECTED" -lt 500 ]]; then
        warn "Connection count is moderate ($CONNECTED)"
    else
        fail "Connection count is HIGH ($CONNECTED) - investigate immediately"
    fi
fi

echo ""

# ---- 5. Cron watchdog ----
echo "--- 5. Cron Watchdog ---"

if crontab -l 2>/dev/null | grep -q "redis_watchdog"; then
    pass "redis_watchdog.sh is in crontab"
    CRON_LINE=$(crontab -l 2>/dev/null | grep "redis_watchdog" | head -1)
    info "  $CRON_LINE"
else
    fail "redis_watchdog.sh is NOT in crontab"
    info "Fix: add this line via 'crontab -e':"
    info "  */2 * * * * /opt/options-dashboard/options-chain-analytics-dashboard/scripts/redis_watchdog.sh >> /var/log/redis_watchdog.log 2>&1"
fi

if [[ -f /var/log/redis_watchdog.log ]]; then
    LAST_RUN=$(tail -1 /var/log/redis_watchdog.log 2>/dev/null)
    if [[ -n "$LAST_RUN" ]]; then
        info "Last watchdog log entry: $LAST_RUN"
    fi
else
    info "No watchdog log yet at /var/log/redis_watchdog.log (will appear after first cron run)"
fi

echo ""

# ---- 6. PM2 apps running ----
echo "--- 6. PM2 Application Status ---"

if command -v pm2 > /dev/null 2>&1; then
    pm2 jlist 2>/dev/null | python3 -c "
import json, sys
try:
    apps = json.load(sys.stdin)
    for app in apps:
        name = app.get('name', 'unknown')
        status = app.get('pm2_env', {}).get('status', 'unknown')
        restarts = app.get('pm2_env', {}).get('restart_time', '?')
        mem = app.get('monit', {}).get('memory', 0)
        mem_mb = round(mem / 1024 / 1024, 1) if mem else 0
        icon = '✓' if status == 'online' else '✗'
        print(f'  {icon} {name}: {status} | restarts: {restarts} | memory: {mem_mb}MB')
except:
    print('  Could not parse PM2 output')
" 2>/dev/null || warn "Could not read PM2 status"
else
    warn "PM2 not found in PATH"
fi

echo ""

# ---- 7. Leak test: restart apps and check connections don't accumulate ----
echo "--- 7. Connection Leak Quick Test ---"

BEFORE=$(redis-cli info clients 2>/dev/null | grep "^connected_clients:" | cut -d: -f2 | tr -d '\r')
info "Connections before app restart: $BEFORE"

read -p "  Restart all PM2 apps to test? (y/N): " CONFIRM
if [[ "$CONFIRM" == "y" || "$CONFIRM" == "Y" ]]; then
    pm2 restart all > /dev/null 2>&1
    info "Waiting 15s for apps to start..."
    sleep 15

    AFTER=$(redis-cli info clients 2>/dev/null | grep "^connected_clients:" | cut -d: -f2 | tr -d '\r')
    info "Connections after restart: $AFTER"

    # Allow small margin (apps reconnecting)
    EXPECTED_MAX=$((BEFORE + 15))
    if [[ "$AFTER" -le "$EXPECTED_MAX" ]]; then
        pass "No connection leak detected (before: $BEFORE, after: $AFTER)"
    else
        fail "Possible connection leak! before: $BEFORE, after: $AFTER (expected max: $EXPECTED_MAX)"
    fi

    # Second restart test
    pm2 restart all > /dev/null 2>&1
    info "Waiting 15s for second restart..."
    sleep 15

    AFTER2=$(redis-cli info clients 2>/dev/null | grep "^connected_clients:" | cut -d: -f2 | tr -d '\r')
    info "Connections after 2nd restart: $AFTER2"

    if [[ "$AFTER2" -le "$EXPECTED_MAX" ]]; then
        pass "Still no leak after 2nd restart (connections stable)"
    else
        fail "Connections accumulating: $BEFORE -> $AFTER -> $AFTER2"
    fi
else
    info "Skipped. Run manually: pm2 restart all && sleep 15 && redis-cli info clients"
fi

echo ""
echo "========================================="
echo " Verification Complete"
echo "========================================="
echo ""
