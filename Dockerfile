FROM python:3.12-slim-bookworm

# Avoid interactive prompts
ENV DEBIAN_FRONTEND=noninteractive

# ── System packages ──────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Virtual display
    xvfb \
    # Audio
    pulseaudio \
    pulseaudio-utils \
    # Audio/video decoding
    ffmpeg \
    # TS3 client dependencies (Qt5, X11, SSL, etc.)
    libxcb1 \
    libx11-6 \
    libxrender1 \
    libxrandr2 \
    libxfixes3 \
    libxcb-xinerama0 \
    libxcb-image0 \
    libxcb-keysyms1 \
    libxcb-render-util0 \
    libxcb-icccm4 \
    libxcb-sync1 \
    libxcb-xkb1 \
    libxkbcommon0 \
    libxkbcommon-x11-0 \
    libfontconfig1 \
    libfreetype6 \
    libdbus-1-3 \
    libnss3 \
    libasound2 \
    libxcursor1 \
    libxcomposite1 \
    libxi6 \
    libxtst6 \
    libxkbfile1 \
    libxcb-cursor0 \
    libxcb-shape0 \
    libxcb-xfixes0 \
    libxcb-glx0 \
    libxcb-dri2-0 \
    libxcb-dri3-0 \
    libxcb-present0 \
    libxshmfence1 \
    libdrm2 \
    libgbm1 \
    libegl1 \
    libgl1 \
    # Process manager
    supervisor \
    # Utilities
    wget \
    bzip2 \
    xdotool \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# ── TeamSpeak 3 Client ───────────────────────────
# Download and extract the TS3 Linux client
# Note: Update the URL when a new version is released
ARG TS3_CLIENT_VERSION=3.6.2
RUN wget -q "https://files.teamspeak-services.com/releases/client/${TS3_CLIENT_VERSION}/TeamSpeak3-Client-linux_amd64-${TS3_CLIENT_VERSION}.run" \
    -O /tmp/ts3client.run \
    && chmod +x /tmp/ts3client.run \
    && /tmp/ts3client.run --noexec --target /opt/ts3client \
    && rm /tmp/ts3client.run

# Make TS3 client scripts executable
RUN chmod +x /opt/ts3client/ts3client_runscript.sh 2>/dev/null || true

# ── Python dependencies ──────────────────────────
COPY requirements.txt /opt/bot/requirements.txt
RUN pip install --no-cache-dir -r /opt/bot/requirements.txt

# ── Application code ─────────────────────────────
COPY bot/ /opt/bot/bot/
COPY config/ /opt/bot/config/
COPY docker/ /opt/bot/docker/

# ── Runtime setup ────────────────────────────────
RUN useradd -m -s /bin/bash ts3bot \
    && mkdir -p /data/cache /data/logs /home/ts3bot/.ts3client \
    && chown -R ts3bot:ts3bot /data /home/ts3bot \
    && chown -R ts3bot:ts3bot /opt/bot

# PulseAudio config
COPY docker/pulseaudio/default.pa /etc/pulse/default.pa
COPY docker/supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Entrypoint
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

WORKDIR /opt/bot

ENTRYPOINT ["/entrypoint.sh"]
