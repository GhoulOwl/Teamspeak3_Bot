"""Formatting utilities for TS3 BBCode and display text."""

from __future__ import annotations

from datetime import timedelta


def bold(text: str) -> str:
    return f"[B]{text}[/B]"


def url(link: str, text: str | None = None) -> str:
    if text:
        return f"[URL={link}]{text}[/URL]"
    return f"[URL]{link}[/URL]"


def color(text: str, c: str) -> str:
    return f"[COLOR={c}]{text}[/COLOR]"


def format_duration(td: timedelta) -> str:
    """Format a timedelta as mm:ss or hh:mm:ss."""
    total = int(td.total_seconds())
    if total < 0:
        return "--:--"
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def truncate(text: str, max_len: int = 200) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."
