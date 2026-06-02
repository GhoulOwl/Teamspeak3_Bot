"""Initialize TeamSpeak 3 client identity and audio settings.

This script creates a minimal settings.db for the headless TS3 client,
configuring audio devices to use the PulseAudio null sink and generating
an RSA identity if one does not already exist.
"""

import os
import sqlite3
import subprocess
import sys


def _generate_identity_via_openssl() -> str | None:
    """Generate an RSA 2048 private key using the openssl CLI tool.

    Returns PEM-encoded key string, or None on failure.
    """
    # Try multiple openssl commands in order of preference
    commands = [
        ["openssl", "genrsa", "2048"],
        ["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048"],
        ["openssl", "genrsa", "-outform", "PEM", "2048"],
    ]

    for cmd in commands:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=15,
            )
            # Print diagnostic info
            print(f"  [{cmd[1]}] rc={result.returncode}, "
                  f"stdout={len(result.stdout)}B, stderr={result.stderr[:200] if result.stderr else 'none'}")

            if result.returncode == 0 and result.stdout:
                pem = result.stdout.decode("ascii", errors="replace")
                if "BEGIN" in pem and "PRIVATE KEY" in pem:
                    print(f"  Key generated via: {' '.join(cmd)}")
                    return pem

        except FileNotFoundError:
            print(f"  openssl not found in PATH", file=sys.stderr)
            break  # No point trying more openssl commands
        except subprocess.TimeoutExpired:
            print(f"  [{' '.join(cmd)}] timed out", file=sys.stderr)
            continue

    print("  WARNING: All openssl key generation attempts failed.", file=sys.stderr)
    return None


def _init_identity(cursor: sqlite3.Cursor) -> None:
    """Create the identities table and insert a generated RSA key if none exists.

    The TS3 Client stores identities in the settings.db SQLite database.
    Each identity is an RSA key pair used for server authentication.
    Without a valid identity, the client cannot connect to any server.
    """
    # Create the identities table (TS3 Client expects this schema)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS identities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_blob TEXT,
            valid_until INTEGER DEFAULT 0,
            nickname TEXT DEFAULT ''
        )
    """)

    # Check if any identity already exists
    cursor.execute("SELECT COUNT(*) FROM identities")
    count = cursor.fetchone()[0]
    if count > 0:
        print(f"  Identity already exists ({count} found), skipping generation")
        return

    # Generate RSA key via openssl
    pem_key = _generate_identity_via_openssl()
    if pem_key is None:
        print("  WARNING: Could not generate identity via openssl.")
        print("  The TS3 Client will attempt to generate one on first launch.")
        return

    # Store the PEM key in the identities table
    cursor.execute(
        "INSERT INTO identities (key_blob, valid_until, nickname) VALUES (?, ?, ?)",
        (pem_key.strip(), 0, ""),
    )
    print("  Generated and stored new RSA identity")


def init_settings():
    settings_dir = os.path.expanduser("~/.ts3client")
    os.makedirs(settings_dir, exist_ok=True)

    db_path = os.path.join(settings_dir, "settings.db")

    if os.path.exists(db_path):
        print(f"Settings database already exists: {db_path}")
        # Still check for identity
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        try:
            _init_identity(cursor)
            conn.commit()
        except Exception as e:
            print(f"  Identity check failed: {e}", file=sys.stderr)
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
        # Start capturing on connect (critical: TS3 must transmit audio)
        "capture/autostart": "1",
    }

    for key, value in audio_settings.items():
        cursor.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )

    # Generate and store identity
    print("Generating TS3 identity...")
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
