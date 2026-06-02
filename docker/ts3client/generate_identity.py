"""Generate a TeamSpeak 3 identity and insert it into settings.db.

The TS3 identity is an ECDSA key pair on the prime256v1 (P-256) curve,
stored in a proprietary obfuscated format in the settings.db identities table.

This implementation is based on the TS3AudioBot's TsCrypt.cs logic (OSL-3.0).
"""

import base64
import hashlib
import sqlite3
import struct
import sys
from pathlib import Path

# TS3 identity obfuscation key (from TS3AudioBot TsCrypt.cs)
OBFUSCATION_KEY = bytes.fromhex(
    "b9dfaa7bee6ac57ac7b65f1094a1c155"
    "e747327bc2fe5d51c512023fe54a2802"
    "01004e90ad1daaae1075d53b7d571c30"
    "e063b5a62a4a017bb394833aa0983e6e"
)


def generate_ecdsa_keypair():
    """Generate an ECDSA key pair on prime256v1 (P-256) curve."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization

    private_key = ec.generate_private_key(ec.SECP256R1())
    pub = private_key.public_key().public_numbers()
    priv = private_key.private_numbers()

    return pub.x, pub.y, priv.private_value


def int_to_der_integer(value: int) -> bytes:
    """Encode an integer as an ASN.1 DER INTEGER."""
    if value == 0:
        return b"\x02\x01\x00"

    # Convert to bytes (big-endian, unsigned)
    byte_length = (value.bit_length() + 7) // 8
    raw = value.to_bytes(byte_length, "big")

    # Add leading zero byte if high bit is set (DER integers are signed)
    if raw[0] & 0x80:
        raw = b"\x00" + raw

    return b"\x02" + _der_length(len(raw)) + raw


def _der_length(length: int) -> bytes:
    """Encode a DER length."""
    if length < 0x80:
        return bytes([length])
    elif length < 0x100:
        return b"\x81" + bytes([length])
    else:
        return b"\x82" + struct.pack(">H", length)


def _der_bitstring(data: bytes, unused_bits: int) -> bytes:
    """Encode a DER BIT STRING."""
    content = bytes([unused_bits]) + data
    return b"\x03" + _der_length(len(content)) + content


def _der_sequence(items: list[bytes]) -> bytes:
    """Encode a DER SEQUENCE."""
    content = b"".join(items)
    return b"\x30" + _der_length(len(content)) + content


def build_identity_asn1(pub_x: int, pub_y: int, priv_key: int) -> bytes:
    """Build the ASN.1 DER encoding for a TS3 identity.

    Format: DerSequence(
        DerBitString(0x80, unused_bits=7),
        DerInteger(32),
        DerInteger(pub_x),
        DerInteger(pub_y),
        DerInteger(priv_key),
    )
    """
    items = [
        _der_bitstring(b"\x80", 7),
        int_to_der_integer(32),
        int_to_der_integer(pub_x),
        int_to_der_integer(pub_y),
        int_to_der_integer(priv_key),
    ]
    return _der_sequence(items)


def compute_security_level(pub_x: int, pub_y: int, target_level: int = 8) -> int:
    """Brute-force the key offset to achieve the target security level.

    The security level is determined by counting leading zero bits in
    SHA1(public_key_string + offset_as_string).
    """
    # Build the public key string (same format as TS3 uses)
    pub_key_str = _public_key_string(pub_x, pub_y)
    pub_key_bytes = pub_key_str.encode("ascii")

    offset = 0
    while True:
        offset_str = str(offset).encode("ascii")
        h = hashlib.sha1(pub_key_bytes + offset_str).digest()

        # Count leading zero bits
        leading_zeros = 0
        for byte in h:
            if byte == 0:
                leading_zeros += 8
            else:
                leading_zeros += _count_leading_zero_bits(byte)
                break

        if leading_zeros >= target_level:
            return offset
        offset += 1

        if offset > 10_000_000:
            # Safety limit - use whatever we have
            return offset


def _count_leading_zero_bits(byte: int) -> int:
    """Count leading zero bits in a byte."""
    count = 0
    for i in range(7, -1, -1):
        if byte & (1 << i):
            break
        count += 1
    return count


def _public_key_string(pub_x: int, pub_y: int) -> str:
    """Get the public key string used for UID and security level calculation.

    This matches the TS3 client's public key export format.
    """
    # Export public key as ASN.1 DER:
    # DerSequence(DerBitString(0x00, unused_bits=0), DerInteger(32),
    #             DerInteger(pub_x), DerInteger(pub_y))
    items = [
        _der_bitstring(b"\x00", 0),
        int_to_der_integer(32),
        int_to_der_integer(pub_x),
        int_to_der_integer(pub_y),
    ]
    der_bytes = _der_sequence(items)
    return base64.b64encode(der_bytes).decode("ascii")


def obfuscate_identity(der_bytes: bytes, level: int) -> str:
    """Obfuscate the ASN.1 DER identity into TS3 native format.

    The import (deobfuscate) process from TS3AudioBot:
      1. SHA1 = hash(obf_bytes[20:])
      2. XOR obf_bytes[20:] with SHA1
      3. XOR bytes[0:min(100,len)] with OBFUSCATION_KEY
      4. Base64 decode → ASN.1 DER

    Export (obfuscate) must reverse:
      1. Base64 encode ASN.1 DER → orig_bytes
      2. XOR orig[0:min(100,len)] with OBFUSCATION_KEY
      3. SHA1 = hash(result[20:])
      4. XOR result[20:] with SHA1
      → obfuscated bytes, stored as "{level}V{base64(obfuscated)}"
    """
    # Step 1: Base64 encode the DER bytes
    b64_data = bytearray(base64.b64encode(der_bytes))

    # Step 2: XOR first min(100, len) bytes with OBFUSCATION_KEY
    xor_len = min(100, len(b64_data))
    for i in range(xor_len):
        b64_data[i] ^= OBFUSCATION_KEY[i % len(OBFUSCATION_KEY)]

    # Step 3: Compute SHA1 of bytes [20:]
    data_from_20 = bytes(b64_data[20:])
    sha1_hash = hashlib.sha1(data_from_20).digest()

    # Step 4: XOR bytes [20:] with SHA1
    for i in range(len(data_from_20)):
        b64_data[20 + i] ^= sha1_hash[i % len(sha1_hash)]

    # The obfuscated bytes are stored as base64 in the identity string
    obfuscated_b64 = base64.b64encode(bytes(b64_data)).decode("ascii")
    return f"{level}V{obfuscated_b64}"


def get_client_uid(pub_x: int, pub_y: int) -> str:
    """Compute the client UID from the public key.

    UID = Base64(SHA1(public_key_string))
    """
    pub_key_str = _public_key_string(pub_x, pub_y)
    uid_hash = hashlib.sha1(pub_key_str.encode("ascii")).digest()
    return base64.b64encode(uid_hash).decode("ascii")


def generate_identity(target_level: int = 8) -> tuple[str, str, int]:
    """Generate a complete TS3 identity.

    Returns:
        (identity_string, client_uid, key_offset)
    """
    pub_x, pub_y, priv_key = generate_ecdsa_keypair()
    key_offset = compute_security_level(pub_x, pub_y, target_level)
    der_bytes = build_identity_asn1(pub_x, pub_y, priv_key)
    identity_str = obfuscate_identity(der_bytes, key_offset)
    client_uid = get_client_uid(pub_x, pub_y)

    return identity_str, client_uid, key_offset


def insert_identity_into_db(db_path: str, identity_str: str, nickname: str = "") -> None:
    """Insert the identity into the settings.db identities table."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute(
        "INSERT INTO identities (key_blob, nickname) VALUES (?, ?)",
        (identity_str, nickname),
    )
    conn.commit()

    # Verify
    cursor.execute("SELECT id, substr(key_blob, 1, 20), nickname FROM identities")
    rows = cursor.fetchall()
    print(f"  Identities in DB: {len(rows)}")
    for row in rows:
        print(f"    ID={row[0]}, key={row[1]}..., nickname={row[2]}")

    conn.close()


def main():
    if len(sys.argv) < 2:
        db_path = Path.home() / ".ts3client" / "settings.db"
    else:
        db_path = Path(sys.argv[1])

    if not db_path.exists():
        print(f"ERROR: settings.db not found at {db_path}")
        sys.exit(1)

    target_level = int(sys.argv[2]) if len(sys.argv) > 2 else 8

    print(f"Generating TS3 identity (security level {target_level})...")
    identity_str, client_uid, key_offset = generate_identity(target_level)

    print(f"  Identity: {identity_str[:40]}...")
    print(f"  Client UID: {client_uid}")
    print(f"  Key offset: {key_offset}")

    print(f"Inserting into {db_path}...")
    insert_identity_into_db(str(db_path), identity_str)

    print("Done!")


if __name__ == "__main__":
    main()
