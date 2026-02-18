#!/usr/bin/env bash
# Enable USB tethering (RNDIS or NCM) on Samsung Galaxy Z Fold3 phones
# and assign static IPs for direct TCP/IP over USB.
#
# This eliminates ADB tunnel overhead for prima.cpp ring communication.
# Expected latency: 0.5-2ms per hop (vs 10-20ms with ADB tunnels).
#
# Usage:
#   ./scripts/setup_usb_tethering.sh                # Enable RNDIS on all phones
#   ./scripts/setup_usb_tethering.sh --ncm           # Use NCM mode (Android 14+)
#   ./scripts/setup_usb_tethering.sh --phones 5      # Only first 5 phones
#   ./scripts/setup_usb_tethering.sh --test           # Just test connectivity
#   ./scripts/setup_usb_tethering.sh --disable        # Disable tethering on all
#   ./scripts/setup_usb_tethering.sh --status         # Show tethering status

set -euo pipefail

MODE="rndis"
N_PHONES=0  # 0 = all phones
TEST_ONLY=false
DISABLE=false
STATUS_ONLY=false

while [ $# -gt 0 ]; do
    case "$1" in
        --ncm)      MODE="ncm" ;;
        --rndis)    MODE="rndis" ;;
        --phones)   shift; N_PHONES="$1" ;;
        --test)     TEST_ONLY=true ;;
        --disable)  DISABLE=true ;;
        --status)   STATUS_ONLY=true ;;
        *)          echo "Unknown flag: $1"; exit 1 ;;
    esac
    shift
done

SSH_HOST="winpc"
ADB='"C:\Program Files\platform-tools\adb.exe"'

# Phone serial numbers — use BOTTOM of list to avoid conflict with other agents
ALL_PHONES=(R3CR904AQKA R3CR90AJCPF R3CRA0KPK9M R3CRB0726FZ
            R3CRA0CJCDD R3CRA0CP7TZ R3CRA0F51KB R3CRA0E77WZ
            R3CR904BTTM R3CRA0CZ35E R3CRA0D4LJZ R3CRA0D7E6Z
            R3CRA0D98AZ R3CRA0DQKVZ R3CRA0EK7HM R3CRA0FR3VD
            R3CRA0GJ4SF R3CRA0GJK0E R3CRA0GJK5M R3CRA0GK04F)

# Use bottom phones (reverse order) to avoid conflict with other agents
BOTTOM_PHONES=(R3CRA0GK04F R3CRA0GJK5M R3CRA0GJK0E R3CRA0GJ4SF
               R3CRA0FR3VD R3CRA0EK7HM R3CRA0DQKVZ R3CRA0D98AZ
               R3CRA0D7E6Z R3CRA0D4LJZ R3CRA0CZ35E R3CR904BTTM
               R3CRA0E77WZ R3CRA0F51KB R3CRA0CP7TZ R3CRA0CJCDD
               R3CRB0726FZ R3CRA0KPK9M R3CR90AJCPF R3CR904AQKA)

if [ "$N_PHONES" -eq 0 ]; then
    PHONES=("${BOTTOM_PHONES[@]}")
    N_PHONES=${#PHONES[@]}
else
    PHONES=("${BOTTOM_PHONES[@]:0:$N_PHONES}")
fi

IFACE="rndis0"
if [ "$MODE" = "ncm" ]; then
    IFACE="ncm0"
fi

SUBNET="10.0.0"

echo "============================================"
echo " USB Tethering Setup — ${MODE^^} mode"
echo "============================================"
echo "  Phones: $N_PHONES"
echo "  Interface: $IFACE"
echo "  Subnet: ${SUBNET}.0/24"
echo ""

# --- Status check ---
if $STATUS_ONLY; then
    echo "Checking tethering status..."
    for idx in $(seq 0 $((N_PHONES - 1))); do
        SERIAL="${PHONES[$idx]}"
        USB_STATE=$(ssh -o ConnectTimeout=5 "$SSH_HOST" "$ADB -s $SERIAL shell getprop sys.usb.state" 2>/dev/null || echo "unreachable")
        IP=$(ssh -o ConnectTimeout=5 "$SSH_HOST" "$ADB -s $SERIAL shell ip addr show $IFACE 2>/dev/null | grep 'inet ' | awk '{print \$2}'" 2>/dev/null || echo "none")
        echo "  $SERIAL: usb_state=$USB_STATE ip=$IP"
    done
    exit 0
fi

# --- Disable tethering ---
if $DISABLE; then
    echo "Disabling tethering on all phones..."
    for SERIAL in "${PHONES[@]}"; do
        ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $SERIAL shell svc usb setFunctions mtp,adb" 2>/dev/null || true
        echo "  $SERIAL: reset to mtp,adb"
    done
    echo "Done. USB tethering disabled."
    exit 0
fi

# --- Test connectivity only ---
if $TEST_ONLY; then
    echo "Testing connectivity to tethering IPs..."
    for idx in $(seq 0 $((N_PHONES - 1))); do
        IP="${SUBNET}.$((idx + 1))"
        SERIAL="${PHONES[$idx]}"
        RESULT=$(ssh -o ConnectTimeout=5 "$SSH_HOST" "ping -n 1 -w 2000 $IP" 2>/dev/null && echo "OK" || echo "FAIL")
        echo "  $SERIAL ($IP): $RESULT"
    done
    exit 0
fi

# --- Enable tethering ---
echo "Step 1: Enabling ${MODE^^} tethering on phones..."
for idx in $(seq 0 $((N_PHONES - 1))); do
    SERIAL="${PHONES[$idx]}"
    echo -n "  $SERIAL: "

    # Set USB functions to rndis,adb or ncm,adb
    ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $SERIAL shell svc usb setFunctions ${MODE},adb" 2>/dev/null || {
        echo "FAILED (svc usb setFunctions)"
        continue
    }
    echo "switching..."
done

# Wait for USB re-enumeration
echo ""
echo "Waiting for USB re-enumeration (10s)..."
sleep 10

# Verify ADB still works
echo ""
echo "Step 2: Verifying ADB connectivity..."
for idx in $(seq 0 $((N_PHONES - 1))); do
    SERIAL="${PHONES[$idx]}"
    STATE=$(ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $SERIAL shell getprop sys.usb.state" 2>/dev/null || echo "LOST")
    if echo "$STATE" | grep -q "$MODE"; then
        echo "  $SERIAL: OK (state=$STATE)"
    else
        echo "  $SERIAL: WARNING (state=$STATE, expected *${MODE}*)"
    fi
done

# Assign static IPs
echo ""
echo "Step 3: Assigning static IPs on $IFACE..."
for idx in $(seq 0 $((N_PHONES - 1))); do
    SERIAL="${PHONES[$idx]}"
    IP="${SUBNET}.$((idx + 1))"

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
echo "Step 4: Opening firewall for ZMQ ports (9000-10100)..."
for idx in $(seq 0 $((N_PHONES - 1))); do
    SERIAL="${PHONES[$idx]}"
    ssh -o ConnectTimeout=10 "$SSH_HOST" \
        "$ADB -s $SERIAL shell iptables -I INPUT -i $IFACE -p tcp --dport 9000:10100 -j ACCEPT" 2>/dev/null || true
    echo "  $SERIAL: firewall opened"
done

# Test connectivity from WinPC
echo ""
echo "Step 5: Testing connectivity from WinPC..."
ALL_OK=true
for idx in $(seq 0 $((N_PHONES - 1))); do
    SERIAL="${PHONES[$idx]}"
    IP="${SUBNET}.$((idx + 1))"
    if ssh -o ConnectTimeout=5 "$SSH_HOST" "ping -n 1 -w 2000 $IP" >/dev/null 2>&1; then
        echo "  $SERIAL ($IP): OK"
    else
        echo "  $SERIAL ($IP): UNREACHABLE"
        ALL_OK=false
    fi
done

echo ""
echo "============================================"
if $ALL_OK; then
    echo " TETHERING SETUP COMPLETE"
    echo ""
    echo " All ${N_PHONES} phones reachable via USB tethering."
    echo " Phones can now communicate via TCP/IP without ADB tunnels."
    echo ""
    echo " IP assignments:"
    for idx in $(seq 0 $((N_PHONES - 1))); do
        echo "   rank $idx: ${SUBNET}.$((idx + 1)) (${PHONES[$idx]})"
    done
else
    echo " TETHERING SETUP INCOMPLETE"
    echo ""
    echo " Some phones are unreachable. Check:"
    echo "   1. WinPC may need USB Ethernet adapter drivers (RNDIS)"
    echo "   2. WinPC firewall may block the subnet"
    echo "   3. Try --ncm mode if RNDIS fails"
    echo "   4. Fall back to Ethernet: ./scripts/setup_ethernet.sh"
fi
echo "============================================"
