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
# IMPORTANT: Run as ts3bot user so settings.db is created at
# /home/ts3bot/.ts3client/ (NOT /root/.ts3client/ which the client
# would use if running as root, causing it to ignore our pre-configured
# audio settings, bookmarks, and license acceptance).
echo "Initializing TS3 client settings (as ts3bot user)..."
runuser -u ts3bot -- env \
    TS3_HOST="${TS3_HOST:-}" \
    TS3_VOICE_PORT="${TS3_VOICE_PORT:-9987}" \
    TS3_NICKNAME="${TS3_NICKNAME:-MusicBot}" \
    python3 /opt/bot/docker/ts3client/init_identity.py || echo "Identity init skipped (will use defaults)"

# ── Start Xvfb (Virtual Display) ─────────────────
echo "Starting Xvfb..."
Xvfb :99 -screen 0 1024x768x24 -ac +extension GLX +render -noreset &
XVFB_PID=$!
sleep 1
echo "Xvfb started (PID: $XVFB_PID)"

# ── Start Window Manager (required for xdotool focus/activate) ──
export DISPLAY=:99
openbox &
sleep 0.5
echo "Openbox window manager started"

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
    TS3_HOST="${TS3_HOST:-localhost}"
    TS3_VOICE_PORT="${TS3_VOICE_PORT:-9987}"
    TS3_NICKNAME="${TS3_NICKNAME:-MusicBot}"
    CONNECT_URL="ts3://${TS3_HOST}:${TS3_VOICE_PORT}?nickname=${TS3_NICKNAME}"
    echo "TS3 connection URL: $CONNECT_URL"

    # Verify settings.db is in the correct location (ts3bot's home, NOT root's)
    echo "Checking settings.db location..."
    if [ -f /home/ts3bot/.ts3client/settings.db ]; then
        echo "  settings.db found at /home/ts3bot/.ts3client/settings.db (correct)"
        echo "  Key settings:"
        sqlite3 /home/ts3bot/.ts3client/settings.db \
            "SELECT key, value FROM settings WHERE key IN ('license/accepted_version','capture/device','playback/device','gui/eula_accepted');" \
            2>/dev/null || echo "  (could not query settings)"
        echo "  Bookmarks:"
        sqlite3 /home/ts3bot/.ts3client/settings.db \
            "SELECT name, address, port, auto_connect FROM bookmarks;" \
            2>/dev/null || echo "  (no bookmarks)"
    else
        echo "  WARNING: settings.db NOT found at /home/ts3bot/.ts3client/"
        ls -la /home/ts3bot/.ts3client/ 2>/dev/null || echo "  (directory does not exist)"
    fi

    # Prepare XDG_RUNTIME_DIR for ts3bot user
    mkdir -p /tmp/runtime-ts3bot
    chmod 700 /tmp/runtime-ts3bot
    chown ts3bot:ts3bot /tmp/runtime-ts3bot

    # Launch TS3 client as ts3bot user (NOT root!).
    # Running as ts3bot ensures the client reads/writes to
    # /home/ts3bot/.ts3client/ where our pre-configured audio settings,
    # bookmarks, and license acceptance are stored.
    # Running as root would use /root/.ts3client/ (wrong, empty config).
    runuser -u ts3bot -- env \
        DISPLAY="$DISPLAY" \
        PULSE_SERVER="$PULSE_SERVER" \
        PULSE_RUNTIME_PATH="$PULSE_RUNTIME_PATH" \
        XDG_RUNTIME_DIR="/tmp/runtime-ts3bot" \
        HOME="/home/ts3bot" \
        QT_DEBUG_PLUGINS=1 \
        /opt/ts3client/"$TS3BIN" "$CONNECT_URL" > /data/logs/ts3client.log 2>&1 &
    TS3_PID=$!
    echo "TS3 Client launching as ts3bot user (PID: $TS3_PID)..."

    # Give the client a moment to initialize
    sleep 5

    # Fallback: If the license dialog is still blocking despite our
    # settings-based pre-acceptance, use xdotool to auto-dismiss it.
    # IMPORTANT: The TS3 license dialog requires scrolling the license
    # text to the bottom before the "Agree" button becomes clickable.
    # Openbox WM is required for xdotool windowactivate to work.
    # CRITICAL: After each xdotool action, check if dialog is gone to
    # prevent stray events from reaching the main TS3 window.
    echo "Checking for blocking license dialog..."

    # Helper: returns 0 if license dialog is still present
    license_dialog_present() {
        xdotool search --name "License" > /dev/null 2>&1
    }

    for attempt in 1 2 3 4 5 6 7 8 9 10; do
        WINDOW=$(xdotool search --name "License" 2>/dev/null | head -1 || true)
        if [ -z "$WINDOW" ]; then
            if [ "$attempt" -gt 1 ]; then
                echo "  License dialog dismissed after attempt $((attempt-1))."
            else
                echo "  No license dialog detected."
            fi
            break
        fi

        echo "  License dialog detected (attempt $attempt, window: $WINDOW)"

        # Get dialog geometry
        GEO=$(xdotool getwindowgeometry --shell "$WINDOW" 2>/dev/null)
        eval "$GEO"
        W=${WIDTH:-740}
        H=${HEIGHT:-700}
        echo "  Dialog size: ${W}x${H}"

        # Activate the dialog
        xdotool windowactivate --sync "$WINDOW" 2>/dev/null || true
        sleep 0.3

        # Step 1: Click center to focus the text area
        CX=$((W / 2))
        CY=$((H / 2))
        xdotool mousemove --window "$WINDOW" "$CX" "$CY" 2>/dev/null || true
        xdotool click 1 2>/dev/null || true
        sleep 0.3
        license_dialog_present || { echo "  License dialog dismissed!"; break; }

        # Step 2: Scroll to bottom incrementally, checking after each batch
        echo "  Scrolling license text to bottom..."
        xdotool key End 2>/dev/null || true
        sleep 0.3
        license_dialog_present || { echo "  License dialog dismissed!"; break; }

        for batch in 1 2 3 4; do
            for i in $(seq 1 15); do
                xdotool key Page_Down 2>/dev/null || true
            done
            sleep 0.3
            license_dialog_present || { echo "  License dialog dismissed during scroll!"; break 2; }
        done

        # Mouse wheel scroll
        xdotool mousemove --window "$WINDOW" "$CX" "$CY" 2>/dev/null || true
        for batch in 1 2 3 4; do
            for i in $(seq 1 20); do
                xdotool click 5 2>/dev/null || true
            done
            sleep 0.3
            license_dialog_present || { echo "  License dialog dismissed during scroll!"; break 2; }
        done

        # Step 3: Click Agree button (bottom-right area)
        echo "  Clicking Agree button..."
        BTN_X=$((W - 80))
        BTN_Y=$((H - 30))
        xdotool mousemove --window "$WINDOW" "$BTN_X" "$BTN_Y" 2>/dev/null || true
        xdotool click 1 2>/dev/null || true
        sleep 1
        license_dialog_present || { echo "  License dialog dismissed by Agree click!"; break; }

        # Step 4: Tab + Enter as backup
        xdotool windowactivate --sync "$WINDOW" 2>/dev/null || true
        for i in $(seq 1 5); do
            xdotool key Tab 2>/dev/null || true
            sleep 0.1
        done
        xdotool key Return 2>/dev/null || true
        sleep 2
        license_dialog_present || { echo "  License dialog dismissed by Tab+Enter!"; break; }

        # Step 5: Grid click brute force
        echo "  Dialog still present, trying grid click..."
        DISMISSED=0
        for bx in $((W-60)) $((W-100)) $((W-140)) $((W-180)); do
            for by in $((H-25)) $((H-40)) $((H-55)) $((H-70)); do
                xdotool mousemove --window "$WINDOW" "$bx" "$by" 2>/dev/null || true
                xdotool click 1 2>/dev/null || true
                sleep 0.3
                if ! license_dialog_present; then
                    echo "  Dismissed by clicking at ($bx, $by)!"
                    DISMISSED=1
                    break 2
                fi
            done
        done
        [ "$DISMISSED" = "1" ] && break

        echo "  WARNING: Could not dismiss license dialog on attempt $attempt"
    done

    # After dismissing, wait for client to process and connect
    if xdotool search --name "License" > /dev/null 2>&1; then
        echo "WARNING: License dialog could not be dismissed after all attempts"
    else
        echo "License dialog handled. Waiting for TS3 client connection..."
        sleep 10
    fi

    # Wait for TS3 client to finish connecting
    echo "Waiting for TS3 client connection..."
    sleep 10

    # Check if the process is still alive
    if kill -0 "$TS3_PID" 2>/dev/null; then
        echo "TS3 Client is running (PID: $TS3_PID)"
        echo "--- TS3 Client log (connection-related) ---"
        grep -iE "connect|identity|server|channel|login|error|fail|reject|license" /data/logs/ts3client.log 2>/dev/null | tail -30 || echo "  (no connection-related lines)"
        echo "--- Last 30 lines ---"
        tail -30 /data/logs/ts3client.log 2>/dev/null || echo "  (no log output yet)"
        echo "--- End of TS3 Client log ---"
    else
        wait "$TS3_PID" 2>/dev/null
        EXIT_CODE=$?
        echo "ERROR: TS3 Client exited with code: $EXIT_CODE"
        echo "Full output:"
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
