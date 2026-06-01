#!/bin/bash
set -e

echo "=== TS3 Bot Container Starting ==="

# ── Create runtime directories ───────────────────
mkdir -p /data/cache /data/logs
mkdir -p /home/ts3bot/.ts3client
# PulseAudio runtime directories
mkdir -p /var/run/pulse
mkdir -p /tmp/pulse-runtime
chmod 0755 /tmp/pulse-runtime
chmod 0755 /var/run/pulse
export PULSE_RUNTIME_PATH=/tmp/pulse-runtime

# ── Start dbus (required for PulseAudio) ──────
echo "Starting dbus..."
mkdir -p /run/dbus
dbus-daemon --system --fork 2>/dev/null || echo "dbus already running or failed"
sleep 1

# ── Initialize TS3 client identity (first run) ──
if [ ! -f /home/ts3bot/.ts3client/settings.db ]; then
    echo "Initializing TS3 client identity..."
    python3 /opt/bot/docker/ts3client/init_identity.py || echo "Identity init skipped (will use defaults)"
fi

# ── Start Xvfb (Virtual Display) ─────────────────
echo "Starting Xvfb..."
Xvfb :99 -screen 0 1024x768x24 -ac +extension GLX +render -noreset &
XVFB_PID=$!
sleep 1
echo "Xvfb started (PID: $XVFB_PID)"

# ── Start PulseAudio (user mode, not system mode) ──────
echo "Starting PulseAudio..."
# Use user mode (--start without --system) to avoid permission issues
# System mode switches to 'pulse' user which can't access /tmp properly
pulseaudio --start \
  --exit-idle-time=-1 \
  --log-level=info \
  --log-target=file:/data/logs/pulseaudio.log 2>/dev/null || true
sleep 2

# Set environment for pactl
export PULSE_SERVER=unix:/tmp/pulse-native

# Load required modules via pactl
echo "Configuring PulseAudio modules..."
pactl load-module module-native-protocol-unix auth-anonymous=1 socket=/tmp/pulse-native 2>/dev/null || true
pactl load-module module-null-sink sink_name=ts3bot_sink sink_properties=device.description="TS3Bot_Virtual_Sink" 2>/dev/null || true
pactl set-default-sink ts3bot_sink 2>/dev/null || true

sleep 1

# ── Verify PulseAudio is running ─────────────────
echo "Verifying PulseAudio..."
if pactl info > /dev/null 2>&1; then
    echo "PulseAudio is running"
    pactl list short sinks
    pactl list short modules
else
    echo "WARNING: PulseAudio not responding, continuing..."
    echo "PulseAudio log:"
    cat /data/logs/pulseaudio.log 2>/dev/null || echo "No log file"
fi

# ── Global environment for TS3 Client and Bot ──
export DISPLAY=:99
export PULSE_SERVER=unix:/tmp/pulse-native

# ── Start TS3 Client ─────────────────────────────
echo "Starting TS3 Client..."
cd /opt/ts3client

# Find TS3 client binary (try multiple patterns)
TS3BIN=""
# Try exact binary name first
for candidate in "ts3client_linux_amd64" "ts3client_linux.amd64" "ts3client_runscript.sh" "TeamSpeak3-Client-linux_amd64"; do
    if [ -f "$candidate" ]; then
        TS3BIN="$candidate"
        break
    fi
done

# Fallback: find any file (not just executable) with ts3/teamspeak in name
if [ -z "$TS3BIN" ]; then
    TS3BIN=$(find . -maxdepth 3 -type f \( -iname "*ts3*" -o -iname "*teamspeak*" \) 2>/dev/null | head -1)
fi

# Fallback: use 'file' to detect ELF binaries
if [ -z "$TS3BIN" ]; then
    echo "Searching for ELF executables in /opt/ts3client..."
    TS3BIN=$(find . -maxdepth 3 -type f -exec file {} \; 2>/dev/null \
        | grep -i "ELF.*executable" \
        | head -1 \
        | cut -d: -f1)
fi

# Last resort: list ALL files for debugging
if [ -z "$TS3BIN" ]; then
    echo "DEBUG: All regular files in /opt/ts3client (maxdepth 1):"
    find /opt/ts3client -maxdepth 1 -type f | head -30
    echo ""
    echo "DEBUG: All regular files in /opt/ts3client (maxdepth 3, non-.so):"
    find /opt/ts3client -maxdepth 3 -type f ! -name "*.so" | head -30
fi

if [ -n "$TS3BIN" ]; then
    echo "Found TS3 binary: $TS3BIN"
    chmod +x "$TS3BIN"

    # Pre-flight check: verify all shared libraries are available
    if command -v ldd > /dev/null 2>&1; then
        MISSING=$(ldd "./$TS3BIN" 2>/dev/null | grep "not found" || true)
        if [ -n "$MISSING" ]; then
            echo "ERROR: TS3 client has missing shared libraries:"
            echo "$MISSING"
            echo "Install the missing packages in the Dockerfile and rebuild."
        fi
    fi

    ./"$TS3BIN" > /data/logs/ts3client.log 2>&1 &
    TS3_PID=$!
    sleep 3

    # Check if the process is still alive
    if kill -0 "$TS3_PID" 2>/dev/null; then
        echo "TS3 Client started successfully (PID: $TS3_PID)"
    else
        echo "ERROR: TS3 Client exited immediately. Last output:"
        tail -20 /data/logs/ts3client.log 2>/dev/null || echo "  (no log output)"
        echo "Bot will continue without TS3 Client (ServerQuery only mode)"
    fi
else
    echo "WARNING: Could not find TS3 client binary"
    echo "All files in /opt/ts3client root:"
    ls -la /opt/ts3client/
    echo ""
    echo "Subdirectories:"
    find /opt/ts3client -maxdepth 1 -type d
    echo ""
    echo "Checking /opt/ts3client/bin/ if it exists:"
    ls -la /opt/ts3client/bin/ 2>/dev/null || echo "  No bin/ directory"
    echo "Bot will start without TS3 Client (ServerQuery only mode)"
fi

# ── Start Python Bot ─────────────────────────────
echo "Starting Python Bot..."
cd /opt/bot
exec python3 -m bot
