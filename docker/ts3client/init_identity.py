"""Initialize TeamSpeak 3 client identity and audio settings.

This script creates a minimal settings.db for the headless TS3 client,
configuring audio devices to use the PulseAudio null sink.
"""

import os
import sqlite3
import sys


def init_settings():
    settings_dir = os.path.expanduser("~/.ts3client")
    os.makedirs(settings_dir, exist_ok=True)

    db_path = os.path.join(settings_dir, "settings.db")

    if os.path.exists(db_path):
        print(f"Settings database already exists: {db_path}")
        return

    print(f"Creating settings database: {db_path}")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Create the tables that TS3 client expects
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bookmarks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            address TEXT,
            port INTEGER DEFAULT 9987,
            nickname TEXT,
            auto_connect INTEGER DEFAULT 0
        )
    """)

    # Audio device settings — use PulseAudio null sink
    audio_settings = {
        "capture/mode": "1",  # Custom device
        "capture/device": "ts3bot_sink.monitor",
        "playback/mode": "1",  # Custom device
        "playback/device": "ts3bot_sink",
        "sound/pack": "default",
        "sound/volume/master": "100",
        "sound/volume/microphone": "100",
        "sound/volume/speaker": "100",
        # Disable voice activation (we only play audio, no mic)
        "capture/voiceactivation": "0",
        # Connection settings
        "connection/auto_reconnect": "1",
    }

    for key, value in audio_settings.items():
        cursor.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )

    # Add bookmark for auto-connect
    ts3_host = os.environ.get("TS3_HOST", "")
    ts3_port = int(os.environ.get("TS3_VOICE_PORT", "9987"))
    ts3_nickname = os.environ.get("TS3_NICKNAME", "MusicBot")

    if ts3_host:
        cursor.execute(
            """INSERT INTO bookmarks (name, address, port, nickname, auto_connect)
               VALUES (?, ?, ?, ?, ?)""",
            ("Bot Server", ts3_host, ts3_port, ts3_nickname, 1),
        )
        print(f"Added auto-connect bookmark for {ts3_host}:{ts3_port}")

    conn.commit()
    conn.close()
    print("Settings database created successfully")


if __name__ == "__main__":
    try:
        init_settings()
    except Exception as e:
        print(f"Error initializing settings: {e}", file=sys.stderr)
        sys.exit(1)
