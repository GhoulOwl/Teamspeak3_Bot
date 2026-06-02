"""Initialize TeamSpeak 3 client settings database.

This script creates a minimal settings.db for the headless TS3 client,
configuring audio devices to use the PulseAudio null sink and
pre-accepting the license to prevent blocking GUI dialogs.

The TS3 identity is NOT pre-generated here -- the TS3 Client uses a
proprietary encoding that is incompatible with standard PEM keys.
The client generates its own identity on first launch.
"""

import os
import shutil
import sqlite3
import sys


def _init_identity(cursor: sqlite3.Cursor) -> None:
    """Ensure the identities table exists.

    IMPORTANT: We do NOT pre-generate an identity here.  The TS3 Client uses a
    proprietary identity encoding that is NOT compatible with standard PEM keys.
    Storing a raw PEM key causes the client to silently fail authentication.

    Instead, we let the TS3 Client generate its own identity on first launch,
    which guarantees the correct format.  The ``ts3://`` connection URL passed
    on the command line triggers the client to connect (and generate an
    identity if needed).
    """
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS identities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_blob TEXT,
            valid_until INTEGER DEFAULT 0,
            nickname TEXT DEFAULT ''
        )
    """)

    cursor.execute("SELECT COUNT(*) FROM identities")
    count = cursor.fetchone()[0]
    if count > 0:
        print(f"  Identity already exists ({count} found), TS3 Client will use it")
    else:
        print("  No identity yet -- TS3 Client will generate one on first launch")


def _ensure_settings_table(cursor: sqlite3.Cursor) -> None:
    """Ensure the settings table exists (for existing databases)."""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)


def _apply_critical_settings(cursor: sqlite3.Cursor) -> None:
    """Apply settings that are critical for headless operation.

    These settings prevent blocking GUI dialogs and configure audio routing.
    Uses INSERT OR REPLACE so they are always applied, even on existing DBs.
    """
    critical_settings = {
        # Pre-accept license to prevent blocking dialog in headless mode.
        # The LicenseViewer log shows "require accept=1" for version 5,
        # which creates a modal dialog that blocks the ts3:// URL handler.
        "license/accepted_version": "5",
        # Additional keys that may suppress license/EULA dialogs
        "gui/eula_accepted": "1",
        "gui/license_accepted": "1",
        # Disable first-run setup wizard
        "gui/show_setup_wizard": "0",
        # Mute notification sounds (no GUI to show them)
        "sound/play/sound_connected": "0",
        "sound/play/sound_disconnected": "0",
        "sound/play/sound_error": "0",
        # Disable update checker (can't install updates in Docker)
        "gui/auto_update": "0",
    }
    for key, value in critical_settings.items():
        cursor.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )
    print("  Applied critical settings (license acceptance, headless config)")


def init_settings():
    settings_dir = os.path.expanduser("~/.ts3client")
    os.makedirs(settings_dir, exist_ok=True)

    # Clean up orphaned root settings (from previous runs where container
    # ran as root -- those settings are at /root/.ts3client and are useless
    # now that we run the client as the ts3bot user).
    root_settings_dir = "/root/.ts3client"
    if root_settings_dir != settings_dir and os.path.isdir(root_settings_dir):
        print(f"  Cleaning orphaned root settings at {root_settings_dir}")
        shutil.rmtree(root_settings_dir, ignore_errors=True)

    db_path = os.path.join(settings_dir, "settings.db")

    if os.path.exists(db_path):
        print(f"Settings database already exists: {db_path}")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        try:
            _ensure_settings_table(cursor)
            _init_identity(cursor)
            # Re-apply critical settings in case they were missing from
            # a previous version of this script.
            _apply_critical_settings(cursor)
            conn.commit()
        except Exception as e:
            print(f"  Settings check failed: {e}", file=sys.stderr)
        finally:
            conn.close()
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

    # Audio device settings -- use PulseAudio null sink
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
        # Start capturing on connect (critical: TS3 must transmit audio)
        "capture/autostart": "1",
    }

    for key, value in audio_settings.items():
        cursor.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )

    # Apply license acceptance and other critical settings
    _apply_critical_settings(cursor)

    # Check identity table (TS3 client generates identity on first launch)
    print("Checking TS3 identity table...")
    _init_identity(cursor)

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
        print(f"  Added auto-connect bookmark for {ts3_host}:{ts3_port}")
    else:
        print("  WARNING: TS3_HOST not set, no bookmark created")

    conn.commit()
    conn.close()
    print("Settings database created successfully")


if __name__ == "__main__":
    try:
        init_settings()
    except Exception as e:
        print(f"Error initializing settings: {e}", file=sys.stderr)
        sys.exit(1)
