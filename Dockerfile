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
    libpulse0 \
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
    # Process manager (removed - using direct process management)
    # Utilities
    wget \
    bzip2 \
    xdotool \
    sqlite3 \
    dbus \
    && rm -rf /var/lib/apt/lists/*

# ── TeamSpeak 3 Client ───────────────────────────
# Download and extract the TS3 Linux client
# Note: Update the URL when a new version is released
# The .run file is a makeself archive that contains:
#   1. A setup script
#   2. The actual application files (possibly in an inner tarball)
ARG TS3_CLIENT_VERSION=3.6.2
RUN wget -q "https://files.teamspeak-services.com/releases/client/${TS3_CLIENT_VERSION}/TeamSpeak3-Client-linux_amd64-${TS3_CLIENT_VERSION}.run" \
    -O /tmp/ts3client.run \
    && chmod +x /tmp/ts3client.run \
    && echo "y" | /tmp/ts3client.run --noexec --target /tmp/ts3client_extract \
    && echo "=== Extracted files (top-level) ===" \
    && ls -la /tmp/ts3client_extract/ \
    && echo "=== Extracting inner archives if any ===" \
    && (cd /tmp/ts3client_extract \
        && for f in *.tar.xz *.tar.gz *.tar.bz2 *.tgz; do \
            [ -f "$f" ] && echo "Extracting $f..." && tar xf "$f" && break; \
        done; \
        true) \
    && mkdir -p /opt/ts3client \
    && echo "=== Copying extracted files ===" \
    && if ls -d /tmp/ts3client_extract/*/ >/dev/null 2>&1; then \
        cp -r /tmp/ts3client_extract/*/. /opt/ts3client/; \
       else \
        cp -r /tmp/ts3client_extract/. /opt/ts3client/; \
       fi \
    && echo "=== TS3 Client directory structure (root level) ===" \
    && ls -la /opt/ts3client/ \
    && echo "=== TS3 Client executables (maxdepth 3) ===" \
    && find /opt/ts3client -maxdepth 3 -type f -executable | head -30 \
    && find /opt/ts3client -maxdepth 3 -type f -executable -exec chmod +x {} \; \
    && find /opt/ts3client -maxdepth 3 -type f ! -executable \( -name "ts3*" -o -name "TeamSpeak*" -o -name "teamspeak*" \) -exec chmod +x {} \; \
    && rm -rf /tmp/ts3client.run /tmp/ts3client_extract

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

# PulseAudio configuration for Docker container
# NOTE: Do NOT set "default-server" in client.conf — it prevents pulseaudio --start
# from launching. Use PULSE_SERVER env variable instead (set in entrypoint.sh).
# NOTE: "allow-root" is NOT a valid PulseAudio config option. The root warning
# ("This program is not intended to be run as root") is harmless and can be ignored.
RUN mkdir -p /etc/pulse \
    && echo "autospawn = no" > /etc/pulse/client.conf \
    && echo "allow-module-loading = yes" > /etc/pulse/daemon.conf \
    && echo "exit-idle-time = -1" >> /etc/pulse/daemon.conf \
    && echo "flat-volumes = no" >> /etc/pulse/daemon.conf

# PulseAudio config
COPY docker/pulseaudio/default.pa /etc/pulse/default.pa

# Entrypoint
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

WORKDIR /opt/bot

ENTRYPOINT ["/entrypoint.sh"]
