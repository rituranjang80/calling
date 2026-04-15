#!/bin/bash
# =============================================================
# Phone Call Controller - Docker Entrypoint
# Handles ADB connection setup and launches the PowerShell app
# =============================================================
set -e

echo "==========================================="
echo "  Phone Call Controller - Docker Container"
echo "==========================================="

# -----------------------------------------------------------
# Start ADB server
# -----------------------------------------------------------
echo "[i] Starting ADB server..."
adb start-server 2>/dev/null || true

# -----------------------------------------------------------
# Connect to phone via WiFi ADB if PHONE_IP is set
# -----------------------------------------------------------
if [ -n "$PHONE_IP" ]; then
    ADB_PORT="${ADB_PORT:-5555}"
    echo "[i] Connecting to phone at ${PHONE_IP}:${ADB_PORT}..."
    
    # Retry connection up to 5 times
    CONNECTED=false
    for i in 1 2 3 4 5; do
        RESULT=$(adb connect "${PHONE_IP}:${ADB_PORT}" 2>&1)
        if echo "$RESULT" | grep -qE "connected|already"; then
            echo "[OK] Connected to ${PHONE_IP}:${ADB_PORT}"
            CONNECTED=true
            break
        fi
        echo "[WARN] Attempt $i failed: $RESULT"
        sleep 2
    done

    # Wait for device to become authorized (phone may show auth dialog)
    if [ "$CONNECTED" = true ]; then
        echo "[i] Waiting for device authorization..."
        AUTHORIZED=false
        for w in 1 2 3 4 5 6 7 8 9 10; do
            DEV_STATUS=$(adb devices 2>/dev/null | grep -E "device$" | wc -l)
            if [ "$DEV_STATUS" -gt 0 ]; then
                echo "[OK] Device authorized and ready"
                AUTHORIZED=true
                break
            fi
            # Check if unauthorized - prompt user
            UNAUTH=$(adb devices 2>/dev/null | grep -c "unauthorized")
            if [ "$UNAUTH" -gt 0 ]; then
                echo "[WARN] Phone shows 'Allow USB debugging?' -- please tap ALLOW on your phone"
            fi
            sleep 2
        done
        if [ "$AUTHORIZED" = false ]; then
            echo "[WARN] Device not yet authorized. The script will retry during startup."
        fi
    fi

    if [ "$CONNECTED" = false ]; then
        echo "[FAIL] Could not connect to phone at ${PHONE_IP}:${ADB_PORT}"
        echo ""
        echo "  Make sure:"
        echo "    1. Phone and this machine are on the same WiFi network"
        echo "    2. USB Debugging is enabled on the phone"
        echo "    3. WiFi ADB was previously set up via USB:"
        echo "       adb tcpip 5555"
        echo ""
        echo "  You can also run setup first:"
        echo "    docker compose run phone-call setup"
        echo ""
        exit 1
    fi
else
    # Check if any device is connected (USB mode)
    sleep 2
    DEVICES=$(adb devices 2>/dev/null | grep -c "device$")
    if [ "$DEVICES" -eq 0 ]; then
        echo "[WARN] No PHONE_IP set and no USB device found."
        echo ""
        echo "  Usage:"
        echo "    PHONE_IP=<your-phone-ip> docker compose up phone-call"
        echo ""
        echo "  To find your phone IP:"
        echo "    Settings -> WiFi -> Tap your network -> IP Address"
        echo ""
        echo "  First-time setup (need USB cable once):"
        echo "    1. Connect phone via USB"
        echo "    2. Run: adb tcpip 5555"
        echo "    3. Disconnect USB"
        echo "    4. Set PHONE_IP and run container"
        echo ""
    fi
fi

# -----------------------------------------------------------
# Symlink data files to /app/data for persistence
# -----------------------------------------------------------
# Contacts
if [ -f /app/data/contacts.json ]; then
    ln -sf /app/data/contacts.json /app/contacts.json
else
    touch /app/data/contacts.json
    ln -sf /app/data/contacts.json /app/contacts.json
fi

# Call log
if [ -f /app/data/call_log.csv ]; then
    ln -sf /app/data/call_log.csv /app/call_log.csv
else
    touch /app/data/call_log.csv
    ln -sf /app/data/call_log.csv /app/call_log.csv
fi

# -----------------------------------------------------------
# Handle command modes
# -----------------------------------------------------------
case "${1:-interactive}" in
    # Interactive menu mode (default)
    interactive)
        if [ -n "$DIAL_NUMBER" ]; then
            echo "[i] Auto-dialing: $DIAL_NUMBER"
            exec pwsh -NoProfile -File /app/call.ps1 -Dial "$DIAL_NUMBER"
        else
            exec pwsh -NoProfile -File /app/call.ps1
        fi
        ;;

    # Direct dial mode
    dial)
        if [ -z "$2" ]; then
            echo "[FAIL] Usage: docker compose run phone-call dial <number>"
            exit 1
        fi
        exec pwsh -NoProfile -File /app/call.ps1 -Dial "$2"
        ;;

    # WiFi connect mode
    connect)
        CONNECT_IP="${2:-$PHONE_IP}"
        if [ -z "$CONNECT_IP" ]; then
            echo "[FAIL] Usage: docker compose run phone-call connect <phone-ip>"
            exit 1
        fi
        exec pwsh -NoProfile -File /app/call.ps1 -Phone "$CONNECT_IP"
        ;;

    # Status check
    status)
        exec pwsh -NoProfile -File /app/call.ps1 -Status
        ;;

    # Setup wizard
    setup)
        exec pwsh -NoProfile -File /app/setup.ps1
        ;;

    # Run any raw command
    shell|bash)
        exec /bin/bash
        ;;

    # ADB passthrough
    adb)
        shift
        exec adb "$@"
        ;;

    *)
        echo "Usage:"
        echo "  interactive   - Launch interactive call menu (default)"
        echo "  dial <number> - Directly dial a phone number"
        echo "  connect <ip>  - Connect to phone via WiFi ADB"
        echo "  status        - Show phone connection status"
        echo "  setup         - Run setup wizard"
        echo "  shell         - Open bash shell"
        echo "  adb <args>    - Run ADB command directly"
        exit 0
        ;;
esac
