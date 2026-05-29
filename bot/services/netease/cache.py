"""Simple in-memory TTL cache."""

from __future__ import annotations

import time
from typing import Any


class TTLCache:
    """In-memory key-value cache with per-entry TTL."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float]] = {}

    def get(self, key: str) -> Any | None:
        """Get a value if it exists and hasn't expired."""
        if key not in self._store:
            return None

        value, expires_at = self._store[key]
        if time.time() > expires_at:
            del self._store[key]
            return None

        return value

    def set(self, key: str, value: Any, ttl_seconds: int = 600) -> None:
        """Set a value with TTL in seconds."""
        self._store[key] = (value, time.time() + ttl_seconds)

    def delete(self, key: str) -> None:
        """Delete a key."""
        self._store.pop(key, None)

    def clear(self) -> None:
        """Clear all entries."""
        self._store.clear()

    def cleanup(self) -> int:
        """Remove expired entries. Returns count of removed entries."""
        now = time.time()
        expired = [k for k, (_, exp) in self._store.items() if now > exp]
        for k in expired:
            del self._store[k]
        return len(expired)
