#!/bin/bash
set -e

echo "=== TS3 Bot Container Starting ==="

# ── Create runtime directories ───────────────────
mkdir -p /data/cache /data/logs
mkdir -p /home/ts3bot/.ts3client
mkdir -p /tmp/pulse-runtime
export PULSE_RUNTIME_PATH=/tmp/pulse-runtime

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

# ── Start PulseAudio (system mode for root) ──────
echo "Starting PulseAudio..."
# Create system mode config
cat > /tmp/pulse-system.conf << 'EOF'
load-module module-native-protocol-unix auth-anonymous=1 socket=/tmp/pulse-native
load-module module-null-sink sink_name=ts3bot_sink sink_properties=device.description="TS3Bot_Virtual_Sink"
set-default-sink ts3bot_sink
set-default-source ts3bot_sink.monitor
EOF

pulseaudio --system --exit-idle-time=-1 --daemonize=no --conf-file=/tmp/pulse-system.conf > /data/logs/pulseaudio.log 2>&1 &
PA_PID=$!
sleep 3

# Set environment for pactl
export PULSE_SERVER=unix:/tmp/pulse-native

# ── Verify PulseAudio is running ─────────────────
echo "Verifying PulseAudio..."
if pactl info > /dev/null 2>&1; then
    echo "PulseAudio is running"
    pactl list short modules
else
    echo "WARNING: PulseAudio not responding, but continuing..."
    cat /data/logs/pulseaudio.log
fi

# ── Start TS3 Client ─────────────────────────────
echo "Starting TS3 Client..."
cd /opt/ts3client
# TS3 client binary (not script)
TS3BIN=$(find . -name "ts3client_linux.amd64" -o -name "TeamSpeak3-Client-linux_amd64" -type f 2>/dev/null | head -1)
if [ -n "$TS3BIN" ]; then
    echo "Found TS3 binary: $TS3BIN"
    chmod +x "$TS3BIN"
    ./"$TS3BIN" &
    TS3_PID=$!
    sleep 10
    echo "TS3 Client started (PID: $TS3_PID)"
else
    echo "WARNING: Could not find TS3 client binary"
    echo "TS3 Client directory contents:"
    find /opt/ts3client -maxdepth 2 -type f -name "ts3*" | head -20
    echo "Bot will start without TS3 Client (ServerQuery only mode)"
fi

# ── Start Python Bot ─────────────────────────────
echo "Starting Python Bot..."
cd /opt/bot
export PULSE_SERVER=unix:/tmp/pulse-native
exec python3 -m bot
