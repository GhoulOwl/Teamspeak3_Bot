#!/bin/bash
set -e

echo "=== TS3 Bot Container Starting ==="

# ── Create runtime directories ───────────────────
mkdir -p /data/cache /data/logs
mkdir -p /home/ts3bot/.ts3client

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
pulseaudio --system --exit-idle-time=-1 --daemonize=no > /data/logs/pulseaudio.log 2>&1 &
PA_PID=$!
sleep 3

# ── Configure PulseAudio null sink ───────────────
echo "Configuring PulseAudio null sink..."
pactl load-module module-null-sink sink_name=ts3bot_sink sink_properties=device.description="TS3Bot_Virtual_Sink" || echo "Failed to load null sink (will retry)"
pactl set-default-sink ts3bot_sink || echo "Failed to set default sink"
pactl set-default-source ts3bot_sink.monitor || echo "Failed to set default source"
echo "PulseAudio configured"

# ── Start TS3 Client ─────────────────────────────
echo "Starting TS3 Client..."
cd /opt/ts3client
TS3SCRIPT=$(find . -name "ts3client_runscript.sh" 2>/dev/null | head -1)
if [ -n "$TS3SCRIPT" ]; then
    echo "Found TS3 script: $TS3SCRIPT"
    chmod +x "$TS3SCRIPT"
    ./"$TS3SCRIPT" &
    TS3_PID=$!
    sleep 10
    echo "TS3 Client started (PID: $TS3_PID)"
else
    echo "ERROR: Could not find ts3client_runscript.sh"
    ls -la /opt/ts3client/
fi

# ── Start Python Bot ─────────────────────────────
echo "Starting Python Bot..."
cd /opt/bot
exec python3 -m bot
