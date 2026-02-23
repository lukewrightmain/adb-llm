#!/usr/bin/env bash
# mdns-watcher.sh — keeps cellswarm.local pointing at the active master phone.
#
# Polls all known phone IPs every 15s via the agent's /status endpoint.
# When it finds a phone running cellswarm-master, it (re)starts the mDNS
# advertiser with that phone's IP. Handles master migration on relaunch.
#
# Usage: ./scripts/mdns-watcher.sh
# Kill:  pkill -f mdns-watcher

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
MDNS_BIN="$PROJECT_DIR/bin/mdns-advertise-host"
HOSTNAME="cellswarm"
BIND_IP="10.69.1.235"
AGENT_PORT=8082
POLL_INTERVAL=15

# All known phone IPs
PHONES=(
    10.105.0.12 10.105.0.13 10.105.0.17 10.105.0.19 10.105.0.20
    10.105.0.24 10.105.0.28 10.105.0.29 10.105.0.30 10.105.0.31
    10.105.0.32 10.105.0.36 10.105.0.38 10.105.0.40 10.105.0.41
    10.105.0.42 10.105.0.44 10.105.0.45 10.105.0.48 10.105.0.156
)

CURRENT_MASTER=""
MDNS_PID=0

start_mdns() {
    local ip="$1"
    if [ $MDNS_PID -gt 0 ] && kill -0 $MDNS_PID 2>/dev/null; then
        kill $MDNS_PID 2>/dev/null
        wait $MDNS_PID 2>/dev/null || true
    fi
    "$MDNS_BIN" "$HOSTNAME" "$ip" "$BIND_IP" &
    MDNS_PID=$!
    CURRENT_MASTER="$ip"
    echo "$(date '+%H:%M:%S') mdns-watcher: $HOSTNAME → $ip (pid $MDNS_PID)"
}

cleanup() {
    if [ $MDNS_PID -gt 0 ] && kill -0 $MDNS_PID 2>/dev/null; then
        kill $MDNS_PID 2>/dev/null
    fi
    exit 0
}
trap cleanup INT TERM

echo "mdns-watcher: starting (polling ${#PHONES[@]} phones every ${POLL_INTERVAL}s)"

while true; do
    FOUND=""
    for ip in "${PHONES[@]}"; do
        resp=$(curl -s --connect-timeout 1 --max-time 2 "http://${ip}:${AGENT_PORT}/status" 2>/dev/null || true)
        if echo "$resp" | grep -q '"master_pid":[1-9]'; then
            FOUND="$ip"
            break
        fi
    done

    if [ -n "$FOUND" ]; then
        if [ "$FOUND" != "$CURRENT_MASTER" ]; then
            echo "$(date '+%H:%M:%S') mdns-watcher: master migrated $CURRENT_MASTER → $FOUND"
            start_mdns "$FOUND"
        elif [ $MDNS_PID -gt 0 ] && ! kill -0 $MDNS_PID 2>/dev/null; then
            echo "$(date '+%H:%M:%S') mdns-watcher: mDNS process died, restarting"
            start_mdns "$FOUND"
        fi
    else
        if [ -n "$CURRENT_MASTER" ]; then
            echo "$(date '+%H:%M:%S') mdns-watcher: no master found (ring may be restarting)"
        fi
    fi

    sleep "$POLL_INTERVAL"
done
