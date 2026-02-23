#!/data/data/com.termux/files/usr/bin/bash
# Quick SSH setup for Termux
# Usage: bash /data/local/tmp/termux_ssh_setup.sh YOUR_PASSWORD
#
# After running, connect from server with: ssh -p 8022 <phone-ip>

set -e

if [ -z "$1" ]; then
    echo "Usage: bash $0 YOUR_PASSWORD"
    echo "Example: bash $0 cellswarm123"
    exit 1
fi

PASSWORD="$1"

echo "============================================"
echo " Termux SSH Setup"
echo "============================================"

# Set password non-interactively
echo -e "$PASSWORD\n$PASSWORD" | passwd 2>/dev/null && \
    echo "  ✓ Password set" || \
    echo "  ✗ passwd failed — try running 'passwd' manually"

# Kill any existing sshd, then start fresh
pkill sshd 2>/dev/null || true
sshd
echo "  ✓ sshd started on port 8022"

# Show this phone's IP
echo ""
echo "  Connect from server with:"
IP=$(ifconfig 2>/dev/null | grep -A1 'eth0\|wlan0\|swlan0' | grep 'inet ' | awk '{print $2}' | head -1)
if [ -z "$IP" ]; then
    IP=$(ifconfig 2>/dev/null | grep 'inet ' | grep -v '127.0.0.1' | awk '{print $2}' | head -1)
fi
echo "    ssh -p 8022 $IP"
echo ""
echo "============================================"
