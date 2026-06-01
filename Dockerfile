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
    # TS3 client runtime dependencies
    libevent-2.1-7 \
    # Utilities
    wget \
    bzip2 \
    xdotool \
    sqlite3 \
    dbus \
    && rm -rf /var/lib/apt/lists/*

# ── TeamSpeak 3 Client ───────────────────────────
# The TS3 Linux client .run file must be manually downloaded and placed
# in the project root directory before building:
#
#   wget https://files.teamspeak-services.com/releases/client/3.6.2/TeamSpeak3-Client-linux_amd64-3.6.2.run
#
# The .run file is a makeself self-extracting archive containing:
#   1. A makeself wrapper (handles --noexec, --target, --nox11 etc.)
#   2. An embedded setup script (the actual installer)
#   3. An inner tar.xz archive with the full application files
#
# IMPORTANT: Do NOT use --noexec — it prevents the embedded setup script
# from running, which means the inner tar.xz is never extracted.
ARG TS3_CLIENT_VERSION=3.6.2
COPY TeamSpeak3-Client-linux_amd64-${TS3_CLIENT_VERSION}.run /tmp/ts3client.run
RUN chmod +x /tmp/ts3client.run \
    && cd /tmp \
    # Run the TS3 installer (without --noexec).
    # PAGER=cat  → prevents the license pager (less/more) from blocking
    # yes        → pipes 'y' repeatedly to accept the license prompt
    # --nox11    → skips X11 availability checks (we provide Xvfb at runtime)
    && PAGER=cat yes | /tmp/ts3client.run --nox11 \
    && echo "=== Extracted contents ===" \
    && ls -la /tmp/TeamSpeak3-Client-linux_amd64/ 2>/dev/null || echo "Directory not found" \
    # Copy extracted files to /opt/ts3client
    && mkdir -p /opt/ts3client \
    && if [ -d "/tmp/TeamSpeak3-Client-linux_amd64" ]; then \
        cp -r /tmp/TeamSpeak3-Client-linux_amd64/. /opt/ts3client/; \
       fi \
    && echo "=== Final /opt/ts3client ===" \
    && ls -la /opt/ts3client/ \
    && echo "=== TS3 Client files (maxdepth 3) ===" \
    && find /opt/ts3client -maxdepth 3 -type f | head -40 \
    && find /opt/ts3client -maxdepth 3 -type f -executable -exec chmod +x {} \; \
    && find /opt/ts3client -maxdepth 3 -type f ! -executable \( -name "ts3*" -o -name "TeamSpeak*" -o -name "teamspeak*" \) -exec chmod +x {} \; \
    && rm -f /tmp/ts3client.run && rm -rf /tmp/TeamSpeak3-Client-linux_amd64

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
