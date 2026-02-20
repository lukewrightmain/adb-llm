#!/usr/bin/env bash
# Set up Ethernet networking for cellswarm phone ring.
#
# FALLBACK transport if USB tethering (RNDIS/NCM) fails.
# Each phone connects via USB-C hub with Ethernet adapter.
# Phones get a standard eth0 interface on a flat 10.0.1.0/24 subnet.
#
# Advantages over RNDIS:
#   - Zero ADB conflict (separate physical interface)
#   - Very reliable, low jitter
#   - Standard Ethernet — works on any Android device
#
# Expected latency: 1.5-3ms per hop (vs 10-20ms with ADB tunnels).
#
# Usage:
#   ./scripts/setup_ethernet.sh                # Configure all phones
#   ./scripts/setup_ethernet.sh --phones 5     # Only first 5 phones
#   ./scripts/setup_ethernet.sh --test          # Just test connectivity
#   ./scripts/setup_ethernet.sh --status        # Show Ethernet status

set -euo pipefail

N_PHONES=0
TEST_ONLY=false
STATUS_ONLY=false

while [ $# -gt 0 ]; do
    case "$1" in
        --phones)   shift; N_PHONES="$1" ;;
        --test)     TEST_ONLY=true ;;
        --status)   STATUS_ONLY=true ;;
        *)          echo "Unknown flag: $1"; exit 1 ;;
    esac
    shift
done

SSH_HOST="winpc"
ADB='"C:\Program Files\platform-tools\adb.exe"'

# Bottom phones (avoid conflicts with other agents)
ALL_PHONES=(R3CRA0GK04F R3CRA0GJK5M R3CRA0GJK0E R3CRA0GJ4SF
            R3CRA0FR3VD R3CRA0EK7HM R3CRA0DQKVZ R3CRA0D98AZ
            R3CRA0D7E6Z R3CRA0D4LJZ R3CRA0CZ35E R3CR904BTTM
            R3CRA0E77WZ R3CRA0F51KB R3CRA0CP7TZ R3CRA0CJCDD
            R3CRB0726FZ R3CRA0KPK9M R3CR90AJCPF R3CR904AQKA)

if [ "$N_PHONES" -eq 0 ]; then
    PHONES=("${ALL_PHONES[@]}")
    N_PHONES=${#PHONES[@]}
else
    PHONES=("${ALL_PHONES[@]:0:$N_PHONES}")
fi

# Ethernet subnet (different from RNDIS 10.0.0.0/24)
SUBNET="10.0.1"
IFACE="eth0"

echo "============================================"
echo " Ethernet Setup (Fallback Transport)"
echo "============================================"
echo "  Phones: $N_PHONES"
echo "  Interface: $IFACE"
echo "  Subnet: ${SUBNET}.0/24"
echo ""

# --- Status check ---
if $STATUS_ONLY; then
    echo "Checking Ethernet status..."
    for idx in $(seq 0 $((N_PHONES - 1))); do
        SERIAL="${PHONES[$idx]}"
        HAS_ETH=$(ssh -o ConnectTimeout=5 "$SSH_HOST" \
            "$ADB -s $SERIAL shell ip link show $IFACE 2>/dev/null | head -1" 2>/dev/null || echo "no interface")
        IP=$(ssh -o ConnectTimeout=5 "$SSH_HOST" \
            "$ADB -s $SERIAL shell ip addr show $IFACE 2>/dev/null | grep 'inet ' | awk '{print \$2}'" 2>/dev/null || echo "none")
        echo "  $SERIAL: iface=$HAS_ETH ip=$IP"
    done
    exit 0
fi

# --- Test connectivity ---
if $TEST_ONLY; then
    echo "Testing Ethernet connectivity..."
    for idx in $(seq 0 $((N_PHONES - 1))); do
        IP="${SUBNET}.$((idx + 1))"
        SERIAL="${PHONES[$idx]}"
        # Ping from another phone (rank 0 pings all others)
        if [ "$idx" -eq 0 ]; then
            echo "  $SERIAL ($IP): RANK 0 (pinger)"
        else
            RESULT=$(ssh -o ConnectTimeout=5 "$SSH_HOST" \
                "$ADB -s ${PHONES[0]} shell ping -c 1 -W 2 $IP 2>/dev/null" && echo "OK" || echo "FAIL")
            echo "  $SERIAL ($IP): $RESULT"
        fi
    done
    exit 0
fi

# --- Configure Ethernet ---
echo "Step 1: Configuring static IPs on $IFACE..."
for idx in $(seq 0 $((N_PHONES - 1))); do
    SERIAL="${PHONES[$idx]}"
    IP="${SUBNET}.$((idx + 1))"

    # Check if eth0 exists
    HAS_IFACE=$(ssh -o ConnectTimeout=10 "$SSH_HOST" \
        "$ADB -s $SERIAL shell ip link show $IFACE 2>/dev/null | wc -l" 2>/dev/null || echo "0")

    if [ "${HAS_IFACE:-0}" -eq 0 ]; then
        echo "  $SERIAL: NO $IFACE interface (Ethernet adapter not connected?)"
        continue
    fi

    ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $SERIAL shell ip link set $IFACE up" 2>/dev/null || true
    ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $SERIAL shell ip addr flush dev $IFACE" 2>/dev/null || true
    ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $SERIAL shell ip addr add ${IP}/24 dev $IFACE" 2>/dev/null || {
        echo "  $SERIAL: FAILED to assign $IP"
        continue
    }
    echo "  $SERIAL: $IP"
done

# Open firewall
echo ""
echo "Step 2: Opening firewall for ZMQ ports (9000-10100)..."
for idx in $(seq 0 $((N_PHONES - 1))); do
    SERIAL="${PHONES[$idx]}"
    ssh -o ConnectTimeout=10 "$SSH_HOST" \
        "$ADB -s $SERIAL shell iptables -I INPUT -i $IFACE -p tcp --dport 9000:10100 -j ACCEPT" 2>/dev/null || true
    echo "  $SERIAL: firewall opened"
done

# Test connectivity (phone-to-phone ping)
echo ""
echo "Step 3: Testing phone-to-phone connectivity..."
ALL_OK=true
RANK0="${PHONES[0]}"
for idx in $(seq 1 $((N_PHONES - 1))); do
    IP="${SUBNET}.$((idx + 1))"
    SERIAL="${PHONES[$idx]}"
    RESULT=$(ssh -o ConnectTimeout=5 "$SSH_HOST" \
        "$ADB -s $RANK0 shell ping -c 1 -W 2 $IP 2>/dev/null" 2>/dev/null && echo "OK" || echo "FAIL")
    echo "  ${RANK0} -> $SERIAL ($IP): $RESULT"
    if [ "$RESULT" = "FAIL" ]; then
        ALL_OK=false
    fi
done

echo ""
echo "============================================"
if $ALL_OK; then
    echo " ETHERNET SETUP COMPLETE"
    echo ""
    echo " IP assignments:"
    for idx in $(seq 0 $((N_PHONES - 1))); do
        echo "   rank $idx: ${SUBNET}.$((idx + 1)) (${PHONES[$idx]})"
    done
    echo ""
    echo " Use --tether with bench scripts (same transport code, just change subnet)."
else
    echo " ETHERNET SETUP INCOMPLETE"
    echo ""
    echo " Check:"
    echo "   1. USB-C hubs with Ethernet adapters are connected"
    echo "   2. Ethernet switch is powered on"
    echo "   3. Run --status to check interface availability"
fi
echo "============================================"
