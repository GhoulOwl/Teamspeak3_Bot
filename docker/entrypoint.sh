#!/bin/bash
set -e

echo "=== TS3 Bot Container Starting ==="

# ── Create runtime directories ───────────────────
mkdir -p /data/cache /data/logs
mkdir -p /home/ts3bot/.ts3client

# ── Copy cookie file to writable /data/ if present ──
# config/ is mounted read-only, but yt-dlp needs to write cookies back
if [ -f /opt/bot/config/cookies.txt ] && [ ! -f /data/cookies.txt ]; then
    cp /opt/bot/config/cookies.txt /data/cookies.txt
    echo "Cookie file copied to /data/cookies.txt (writable)"
fi
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

# ── Fix existing bookmarks: disable auto_connect ─────────────────
# The bookmark auto_connect feature conflicts with ClientQuery connect
# and can cause double-connection issues. ClientQuery handles connecting.
echo "Fixing bookmark auto_connect setting..."
if [ -f /home/ts3bot/.ts3client/settings.db ]; then
    sqlite3 /home/ts3bot/.ts3client/settings.db \
        "UPDATE bookmarks SET auto_connect=0 WHERE auto_connect=1;" 2>/dev/null || true
    # Also ensure license version is high enough to prevent dialog
    sqlite3 /home/ts3bot/.ts3client/settings.db \
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('license/accepted_version', '99');" 2>/dev/null || true
    echo "  Bookmarks auto_connect disabled, license version set to 99"

    # ── Migrate audio device settings to dual-sink architecture ──
    # Old config used a single sink (ts3bot_sink) for both capture and playback,
    # which caused other users' voices to echo back through the bot.
    # New config uses separate sinks: ts3bot_music (capture) and ts3bot_playback.
    sqlite3 /home/ts3bot/.ts3client/settings.db \
        "INSERT OR REPLACE INTO settings (key, value) VALUES \
         ('capture/device', 'ts3bot_music.monitor'), \
         ('playback/device', 'ts3bot_playback');" 2>/dev/null || true
    echo "  Audio devices migrated to dual-sink (ts3bot_music + ts3bot_playback)"

    # ── Disable TS3 audio processing (harmful for music playback) ──
    # These features are designed for voice communication and degrade music quality.
    # Echo cancellation, noise suppression, AGC all compress/distort music signals.
    sqlite3 /home/ts3bot/.ts3client/settings.db \
        "INSERT OR REPLACE INTO settings (key, value) VALUES \
         ('capture/echo_cancel', '0'), \
         ('capture/echo_cancel_aggressive', '0'), \
         ('capture/echo_suppression', '0'), \
         ('capture/noise_suppression', '0'), \
         ('capture/automatic_gain_control', '0');" 2>/dev/null || true
    echo "  Audio processing disabled (echo cancel, noise suppression, AGC)"
fi

# ── Block TeamSpeak license/update/CDN servers ──────────────────
# The TS3 client checks for license updates on startup and downloads
# remote images (avatars, icons). Even with license/accepted_version
# set high, the client may still show a blocking modal dialog.
# Additionally, "Failed to download remote image" errors cause the
# client to disconnect. Block ALL TeamSpeak domains to prevent this.
echo "Blocking TeamSpeak license/update/CDN servers..."
cat >> /etc/hosts <<'HOSTS'
# Block ALL TeamSpeak servers to prevent license dialog, update checks,
# CDN image fetches, and telemetry that cause headless client disconnects.
127.0.0.1 accounting.teamspeak.com
127.0.0.1 license.teamspeak.com
127.0.0.1 update.teamspeak.com
127.0.0.1 files.teamspeak.com
127.0.0.1 addons.teamspeak.com
127.0.0.1 named.teamspeak.com
127.0.0.1 webfiles.teamspeak.com
127.0.0.1 api.teamspeak.com
127.0.0.1 myteamspeak.com
127.0.0.1 www.teamspeak.com
127.0.0.1 telemetry.teamspeak.com
127.0.0.1 web.teamspeak.com
127.0.0.1 news.teamspeak.com
127.0.0.1 ts3.tracker.baseflow.com
HOSTS

# ── Clear TS3 client cache to prevent stale update/license data ──
echo "Clearing TS3 client cache..."
rm -rf /home/ts3bot/.ts3client/cache/* 2>/dev/null || true
chown -R ts3bot:ts3bot /home/ts3bot/.ts3client/ 2>/dev/null || true

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

# ── Block outbound HTTP/HTTPS for TS3 client user (ts3bot, UID 1000) ──
# The TS3 client makes HTTPS requests to TeamSpeak servers for:
#   - License updates (triggers blocking modal dialog)
#   - Remote images like avatars/icons (failures cause disconnect)
#   - myTeamSpeak addon/sync services (unnecessary for headless bot)
# We block these at the iptables level using UID-based rules so that:
#   - The TS3 client (ts3bot UID) cannot make outbound HTTP/HTTPS
#   - The Python bot (root UID) retains full HTTP/HTTPS access for
#     OpenAI API, Netease API, yt-dlp downloads, etc.
#   - UDP voice traffic is unaffected (only TCP 80/443 are blocked)
# Requires NET_ADMIN capability in docker-compose.yml.
echo "Setting iptables rules to block TS3 client outbound HTTP/HTTPS..."
TS3BOT_UID=1000
# Block outbound HTTP (80) and HTTPS (443) for ts3bot user
iptables -A OUTPUT -p tcp --dport 80 -m owner --uid-owner $TS3BOT_UID -j DROP 2>/dev/null && \
iptables -A OUTPUT -p tcp --dport 443 -m owner --uid-owner $TS3BOT_UID -j DROP 2>/dev/null && \
echo "  iptables rules applied: ts3bot user blocked from outbound TCP 80/443" || \
echo "  WARNING: iptables rules failed (need cap_add: NET_ADMIN in docker-compose.yml)"

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
TS3BIN=""
for candidate in "ts3client_runscript.sh" "ts3client_linux_amd64" "ts3client_linux.amd64"; do
    if [ -f "$candidate" ]; then
        TS3BIN="$candidate"
        break
    fi
done

if [ -z "$TS3BIN" ]; then
    TS3BIN=$(find . -maxdepth 3 -type f \( -iname "*ts3*runscript*" -o -iname "*ts3client*" \) 2>/dev/null | head -1)
fi

if [ -z "$TS3BIN" ]; then
    echo "Searching for executables/scripts in /opt/ts3client..."
    TS3BIN=$(find . -maxdepth 3 -type f \( -name "ts3*" -o -name "TeamSpeak*" \) 2>/dev/null | head -1)
fi

if [ -z "$TS3BIN" ]; then
    echo "DEBUG: All regular files in /opt/ts3client (maxdepth 1):"
    find /opt/ts3client -maxdepth 1 -type f | head -30
fi

if [ -n "$TS3BIN" ]; then
    echo "Found TS3 binary: $TS3BIN"
    chmod +x "$TS3BIN"

    TS3_HOST="${TS3_HOST:-localhost}"
    TS3_VOICE_PORT="${TS3_VOICE_PORT:-9987}"
    TS3_NICKNAME="${TS3_NICKNAME:-MusicBot}"
    echo "TS3 connection target: ${TS3_HOST}:${TS3_VOICE_PORT} (via ClientQuery)"

    # Verify settings.db
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
    fi

    # Prepare XDG_RUNTIME_DIR for ts3bot user
    mkdir -p /tmp/runtime-ts3bot
    chmod 700 /tmp/runtime-ts3bot
    chown ts3bot:ts3bot /tmp/runtime-ts3bot

    # Launch TS3 client as ts3bot user (NOT root!).
    runuser -u ts3bot -- env \
        DISPLAY="$DISPLAY" \
        PULSE_SERVER="$PULSE_SERVER" \
        PULSE_RUNTIME_PATH="$PULSE_RUNTIME_PATH" \
        XDG_RUNTIME_DIR="/tmp/runtime-ts3bot" \
        HOME="/home/ts3bot" \
        /opt/ts3client/"$TS3BIN" > /data/logs/ts3client.log 2>&1 &
    TS3_PID=$!
    echo "TS3 Client launching as ts3bot user (PID: $TS3_PID)..."

    # ── Connect via ClientQuery ──────────────────────────────
    # ClientQuery port (25639) opens within ~3 seconds of client start.
    # We connect via ClientQuery (not bookmark auto_connect) for reliable
    # connection control.
    echo "Connecting via ClientQuery..."
    python3 - "$TS3_HOST" "$TS3_VOICE_PORT" "$TS3_NICKNAME" <<'PYEOF'
import socket, sys, time, os

host, port, nickname = sys.argv[1], int(sys.argv[2]), sys.argv[3]
connected = False
api_key = ""

# Wait for clientquery.ini and read API key
for _ in range(15):
    ini_path = "/home/ts3bot/.ts3client/clientquery.ini"
    if os.path.exists(ini_path):
        with open(ini_path) as f:
            for line in f:
                if line.startswith("api_key="):
                    api_key = line.split("=", 1)[1].strip()
                    break
        if api_key:
            break
    time.sleep(1)
if not api_key:
    print("  WARNING: Could not read ClientQuery API key")

for attempt in range(25):
    if attempt > 0:
        time.sleep(1)
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect(("127.0.0.1", 25639))
        time.sleep(0.3)
        welcome = sock.recv(4096).decode(errors="replace").strip()
        if "TS3 Client" not in welcome and "Welcome" not in welcome:
            sock.close()
            continue
        print(f"  ClientQuery ready: {welcome[:60]}")

        if api_key:
            sock.sendall(f"auth apikey={api_key}\n".encode())
            time.sleep(0.5)
            resp = sock.recv(4096).decode(errors="replace").strip()
            print(f"  Auth: {resp}")

        cmd = f"connect address={host}:{port} nickname={nickname}\n"
        print(f"  Sending: {cmd.strip()}")
        sock.sendall(cmd.encode())

        sock.settimeout(2)
        end_time = time.time() + 15
        while time.time() < end_time:
            try:
                data = sock.recv(4096).decode(errors="replace").strip()
                if data:
                    print(f"  Response: {data}")
            except socket.timeout:
                break

        sock.settimeout(5)
        sock.sendall(b"whoami\n")
        time.sleep(2)
        try:
            resp = sock.recv(4096).decode(errors="replace").strip()
            print(f"  Whoami: {resp}")
            if "not connected" not in resp and "error" not in resp.lower():
                connected = True
        except socket.timeout:
            print("  Whoami: timeout (connection may be in progress)")
        sock.close()
        break
    except (ConnectionRefusedError, OSError):
        if attempt % 5 == 0:
            print(f"  Waiting for ClientQuery port (attempt {attempt+1})...")
        continue
    except Exception as e:
        print(f"  ClientQuery error: {e}")
        break

if connected:
    print("  ClientQuery: connected successfully!")
else:
    print("  ClientQuery: connection attempt completed (may need retry)")
PYEOF
    echo "ClientQuery connection attempt finished."

    # ── Handle license dialog (if it still appears despite /etc/hosts block) ──
    echo "Checking for blocking license dialog..."
    license_dialog_present() {
        xdotool search --name "License" > /dev/null 2>&1
    }

    for attempt in 1 2 3; do
        WINDOW=$(xdotool search --name "License" 2>/dev/null | head -1 || true)
        if [ -z "$WINDOW" ]; then
            echo "  No license dialog detected."
            break
        fi
        echo "  License dialog detected (attempt $attempt, window: $WINDOW)"
        GEO=$(xdotool getwindowgeometry --shell "$WINDOW" 2>/dev/null)
        eval "$GEO"
        W=${WIDTH:-740}; H=${HEIGHT:-700}

        # Focus the dialog window
        xdotool windowactivate --sync "$WINDOW" 2>/dev/null || true
        sleep 0.5

        # Click the Agree button (bottom-right area of the dialog)
        BTN_X=$((W - 80)); BTN_Y=$((H - 30))
        xdotool mousemove --window "$WINDOW" "$BTN_X" "$BTN_Y" 2>/dev/null || true
        xdotool click 1 2>/dev/null || true
        sleep 1
        license_dialog_present || { echo "  Dismissed by Agree click!"; break; }

        # Fallback: try clicking center-bottom
        BTN_X2=$((W / 2)); BTN_Y2=$((H - 40))
        xdotool mousemove --window "$WINDOW" "$BTN_X2" "$BTN_Y2" 2>/dev/null || true
        xdotool click 1 2>/dev/null || true
        sleep 1
        license_dialog_present || { echo "  Dismissed by center-bottom click!"; break; }

        # Last resort: try Alt+F4 to close the dialog
        xdotool windowactivate --sync "$WINDOW" 2>/dev/null || true
        xdotool key alt+F4 2>/dev/null || true
        sleep 1
        license_dialog_present || { echo "  Dismissed by Alt+F4!"; break; }

        echo "  WARNING: Could not dismiss license dialog on attempt $attempt"
    done

    # ── Wait for connection to stabilize ──
    sleep 5

    # ── Verify TS3 client status ──
    echo "Checking TS3 client status..."
    if kill -0 "$TS3_PID" 2>/dev/null; then
        echo "TS3 Client is running (PID: $TS3_PID)"
        grep -iE "connect|identity|server|channel" /data/logs/ts3client.log 2>/dev/null | tail -10 || true
        tail -5 /data/logs/ts3client.log 2>/dev/null || true
    else
        wait "$TS3_PID" 2>/dev/null
        EXIT_CODE=$?
        echo "WARNING: TS3 Client exited with code: $EXIT_CODE"
        tail -20 /data/logs/ts3client.log 2>/dev/null || echo "  (no log output)"
    fi

    # ── Background watchdog: restart TS3 client if it exits ──
    # This runs in the background and restarts the client + reconnects
    # via ClientQuery if the client process exits unexpectedly.
    echo "Starting TS3 client watchdog..."
    (
        WATCHDOG_DELAY=10
        while true; do
            sleep "$WATCHDOG_DELAY"
            WATCHDOG_DELAY=5  # shorter delay after first check

            if ! kill -0 "$TS3_PID" 2>/dev/null; then
                echo "[watchdog] TS3 Client exited, restarting..."

                # Restart the client
                runuser -u ts3bot -- env \
                    DISPLAY="$DISPLAY" \
                    PULSE_SERVER="$PULSE_SERVER" \
                    PULSE_RUNTIME_PATH="$PULSE_RUNTIME_PATH" \
                    XDG_RUNTIME_DIR="/tmp/runtime-ts3bot" \
                    HOME="/home/ts3bot" \
                    /opt/ts3client/"$TS3BIN" >> /data/logs/ts3client.log 2>&1 &
                TS3_PID=$!
                echo "[watchdog] TS3 Client restarted (PID: $TS3_PID)"

                # Wait for ClientQuery to be ready, then reconnect
                sleep 8
                python3 - "$TS3_HOST" "$TS3_VOICE_PORT" "$TS3_NICKNAME" <<'WDEOF'
import socket, sys, time, os

host, port, nickname = sys.argv[1], int(sys.argv[2]), sys.argv[3]
api_key = ""
ini_path = "/home/ts3bot/.ts3client/clientquery.ini"
if os.path.exists(ini_path):
    with open(ini_path) as f:
        for line in f:
            if line.startswith("api_key="):
                api_key = line.split("=", 1)[1].strip()
                break

for attempt in range(15):
    if attempt > 0:
        time.sleep(1)
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect(("127.0.0.1", 25639))
        time.sleep(0.3)
        welcome = sock.recv(4096).decode(errors="replace").strip()
        if "TS3 Client" not in welcome and "Welcome" not in welcome:
            sock.close()
            continue

        if api_key:
            sock.sendall(f"auth apikey={api_key}\n".encode())
            time.sleep(0.5)
            sock.recv(4096)

        cmd = f"connect address={host}:{port} nickname={nickname}\n"
        sock.sendall(cmd.encode())
        time.sleep(3)

        sock.sendall(b"whoami\n")
        time.sleep(2)
        resp = sock.recv(4096).decode(errors="replace").strip()
        print(f"[watchdog] Reconnect whoami: {resp}")
        sock.close()
        break
    except (ConnectionRefusedError, OSError):
        continue
    except Exception:
        break
WDEOF
                echo "[watchdog] Reconnection attempt finished."

                # Handle license dialog after restart
                sleep 2
                for _attempt in 1 2 3; do
                    _WINDOW=$(xdotool search --name "License" 2>/dev/null | head -1 || true)
                    [ -z "$_WINDOW" ] && break
                    _GEO=$(xdotool getwindowgeometry --shell "$_WINDOW" 2>/dev/null)
                    eval "$_GEO"
                    _W=${WIDTH:-740}; _H=${HEIGHT:-700}
                    xdotool windowactivate --sync "$_WINDOW" 2>/dev/null || true
                    sleep 0.3
                    xdotool mousemove --window "$_WINDOW" "$((_W - 80))" "$((_H - 30))" 2>/dev/null || true
                    xdotool click 1 2>/dev/null || true
                    sleep 1
                done
            fi
        done
    ) &
    echo "Watchdog started (background PID: $!)"

else
    echo "ERROR: Could not find TS3 client binary in /opt/ts3client"
    ls -la /opt/ts3client/
    echo "Bot will start without TS3 Client (ServerQuery only mode)"
    echo "Audio playback to TS3 channels will NOT work."
fi

# ── Start Python Bot ─────────────────────────────
echo "Starting Python Bot..."
cd /opt/bot
exec python3 -m bot
