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
else
    echo "Settings database exists, checking identity..."
    python3 /opt/bot/docker/ts3client/init_identity.py || true
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
# NOTE: Modules are loaded via /etc/pulse/default.pa — do NOT load them
# again via pactl, as that creates duplicate module instances and breaks
# audio routing (FFmpeg and TS3 client would use different sink instances).
pulseaudio --start \
  --exit-idle-time=-1 \
  --log-level=info \
  --log-target=file:/data/logs/pulseaudio.log 2>/dev/null || true
sleep 2

# Set environment for pactl and all child processes
export PULSE_SERVER=unix:/tmp/pulse-native

# ── Verify PulseAudio is running ─────────────────
echo "Verifying PulseAudio..."
if pactl info > /dev/null 2>&1; then
    echo "PulseAudio is running"
    echo "  Default sink: $(pactl info 2>/dev/null | grep 'Default Sink' || echo 'unknown')"
    echo "  Default source: $(pactl info 2>/dev/null | grep 'Default Source' || echo 'unknown')"
    echo "  Sinks:"
    pactl list short sinks 2>/dev/null || echo "    (none)"
    echo "  Sources:"
    pactl list short sources 2>/dev/null || echo "    (none)"
    echo "  Loaded modules:"
    pactl list short modules 2>/dev/null || echo "    (none)"
else
    echo "WARNING: PulseAudio not responding, continuing..."
    echo "PulseAudio log:"
    cat /data/logs/pulseaudio.log 2>/dev/null || echo "No log file"
fi

# ── Global environment for TS3 Client and Bot ──
export DISPLAY=:99
# Chromium WebEngine cannot run sandboxed as root in Docker
export QTWEBENGINE_CHROMIUM_FLAGS="--no-sandbox --disable-gpu"
export CHROME_FLAGS="--no-sandbox --disable-gpu"

# ── Start TS3 Client ─────────────────────────────
echo "Starting TS3 Client..."

# Pre-flight: verify X server is reachable
echo "DISPLAY=$DISPLAY"
if xdotool getdisplaygeometry > /dev/null 2>&1; then
    echo "X server is responsive ($(xdotool getdisplaygeometry))"
else
    echo "WARNING: xdotool cannot reach X server on DISPLAY=$DISPLAY"
    echo "Xvfb process check:"
    ps aux | grep -v grep | grep Xvfb || echo "  Xvfb NOT running!"
fi

cd /opt/ts3client

# Find TS3 client binary
# Priority: ts3client_runscript.sh > ts3client_linux_amd64 > fallback search
# The runscript.sh wrapper sets LD_LIBRARY_PATH correctly for the TS3 client's
# bundled Qt libraries, which is essential for finding shared libs.
TS3BIN=""
for candidate in "ts3client_runscript.sh" "ts3client_linux_amd64" "ts3client_linux.amd64"; do
    if [ -f "$candidate" ]; then
        TS3BIN="$candidate"
        break
    fi
done

# Fallback: find any file with ts3/teamspeak in name
if [ -z "$TS3BIN" ]; then
    TS3BIN=$(find . -maxdepth 3 -type f \( -iname "*ts3*runscript*" -o -iname "*ts3client*" \) 2>/dev/null | head -1)
fi

# Fallback: use 'file' to detect ELF binaries or shell scripts
if [ -z "$TS3BIN" ]; then
    echo "Searching for executables/scripts in /opt/ts3client..."
    TS3BIN=$(find . -maxdepth 3 -type f \( -name "ts3*" -o -name "TeamSpeak*" \) 2>/dev/null | head -1)
fi

# Last resort: list ALL files for debugging
if [ -z "$TS3BIN" ]; then
    echo "DEBUG: All regular files in /opt/ts3client (maxdepth 1):"
    find /opt/ts3client -maxdepth 1 -type f | head -30
    echo ""
    echo "DEBUG: All regular files in /opt/ts3client (maxdepth 3, non-.so):"
    find /opt/ts3client -maxdepth 3 -type f ! -name "*.so" ! -name "*.so.*" | head -30
fi

if [ -n "$TS3BIN" ]; then
    echo "Found TS3 binary: $TS3BIN"
    chmod +x "$TS3BIN"

    # Pre-flight check: verify all shared libraries are available
    # Only run ldd on ELF binaries, not shell scripts
    if command -v ldd > /dev/null 2>&1 && file "./$TS3BIN" | grep -q "ELF"; then
        MISSING=$(ldd "./$TS3BIN" 2>/dev/null | grep "not found" || true)
        if [ -n "$MISSING" ]; then
            echo "ERROR: TS3 client has missing shared libraries:"
            echo "$MISSING"
            echo "Install the missing packages in the Dockerfile and rebuild."
        else
            echo "All shared libraries satisfied."
        fi
    fi

    # Build the ts3:// connection URL to force auto-connect on startup.
    # Without this, the TS3 client starts but does NOT connect to any server,
    # because bookmarks are only a GUI convenience and don't trigger auto-connect
    # in headless mode.
    TS3_HOST="${TS3_HOST:-localhost}"
    TS3_VOICE_PORT="${TS3_VOICE_PORT:-9987}"
    TS3_NICKNAME="${TS3_NICKNAME:-MusicBot}"
    CONNECT_URL="ts3://${TS3_HOST}:${TS3_VOICE_PORT}?nickname=${TS3_NICKNAME}"
    echo "TS3 connection URL: $CONNECT_URL"

    # Launch TS3 client with the connection URL
    # QT_DEBUG_PLUGINS=1 helps diagnose xcb/platform plugin issues
    QT_DEBUG_PLUGINS=1 ./"$TS3BIN" "$CONNECT_URL" > /data/logs/ts3client.log 2>&1 &
    TS3_PID=$!
    echo "TS3 Client launching (PID: $TS3_PID)..."

    # Wait longer for TS3 client to initialize, load identity, and connect
    sleep 5

    # Check if the process is still alive
    if kill -0 "$TS3_PID" 2>/dev/null; then
        echo "TS3 Client is running (PID: $TS3_PID)"
        # Show recent log output for diagnostics
        echo "--- TS3 Client log (last 10 lines) ---"
        tail -10 /data/logs/ts3client.log 2>/dev/null || echo "  (no log output yet)"
        echo "--- End of TS3 Client log ---"
    else
        echo "ERROR: TS3 Client exited immediately. Full output:"
        cat /data/logs/ts3client.log 2>/dev/null || echo "  (no log output)"
        echo ""
        echo "Bot will continue without TS3 Client (ServerQuery only mode)"
        echo "Audio playback to TS3 channels will NOT work."
    fi
else
    echo "ERROR: Could not find TS3 client binary in /opt/ts3client"
    echo "All files in /opt/ts3client root:"
    ls -la /opt/ts3client/
    echo ""
    echo "Subdirectories:"
    find /opt/ts3client -maxdepth 1 -type d
    echo ""
    echo "Bot will start without TS3 Client (ServerQuery only mode)"
    echo "Audio playback to TS3 channels will NOT work."
fi

# ── Start Python Bot ─────────────────────────────
echo "Starting Python Bot..."
cd /opt/bot
exec python3 -m bot
