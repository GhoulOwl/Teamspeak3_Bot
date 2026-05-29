#!/bin/bash
set -e

echo "=== TS3 Bot Container Starting ==="

# ── Create runtime directories ───────────────────
mkdir -p /data/cache /data/logs
mkdir -p /run/user/$(id -u)/pulse
mkdir -p /home/ts3bot/.ts3client

# ── Initialize TS3 client identity (first run) ──
if [ ! -f /home/ts3bot/.ts3client/settings.db ]; then
    echo "Initializing TS3 client identity..."
    python3 /opt/bot/docker/ts3client/init_identity.py || echo "Identity init skipped (will use defaults)"
fi

# ── Start supervisord (manages all processes) ────
echo "Starting supervisord..."
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
