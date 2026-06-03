"""Data models for Netease Cloud Music."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta


@dataclass
class Song:
    """A song from Netease Cloud Music."""

    id: int
    title: str
    artist: str
    album: str = ""
    duration: timedelta = field(default_factory=lambda: timedelta(0))
    cover_url: str | None = None

    @property
    def display_name(self) -> str:
        return f"{self.title} - {self.artist}"

    @classmethod
    def from_api(cls, data: dict) -> Song:
        """Create a Song from NeteaseCloudMusicApi response data."""
        artists = data.get("ar", [])
        artist_name = " / ".join(a.get("name", "") for a in artists) if artists else "Unknown"

        album = data.get("al", {})
        duration_ms = data.get("dt", 0)

        return cls(
            id=data.get("id", 0),
            title=data.get("name", ""),
            artist=artist_name,
            album=album.get("name", ""),
            duration=timedelta(milliseconds=duration_ms),
            cover_url=album.get("picUrl"),
        )


@dataclass
class Playlist:
    """A playlist from Netease Cloud Music."""

    id: int
    name: str
    tracks: list[Song] = field(default_factory=list)
    track_count: int = 0

    @classmethod
    def from_api(cls, data: dict, tracks: list[dict] | None = None) -> Playlist:
        playlist_data = data if "playlist" not in data else data.get("playlist", data)
        return cls(
            id=playlist_data.get("id", 0),
            name=playlist_data.get("name", ""),
            tracks=[Song.from_api(t) for t in (tracks or [])],
            track_count=playlist_data.get("trackCount", 0),
        )


@dataclass
class LyricLine:
    """A single lyric line with timestamp."""

    timestamp: timedelta
    text: str


@dataclass
class Lyrics:
    """Song lyrics with optional translation."""

    original: str = ""
    translated: str | None = None
    lines: list[LyricLine] = field(default_factory=list)

    @classmethod
    def from_lrc(cls, lrc_text: str, trans_text: str | None = None) -> Lyrics:
        """Parse LRC format text into Lyrics."""
        lines = _parse_lrc(lrc_text)
        return cls(
            original=lrc_text,
            translated=trans_text,
            lines=lines,
        )

    def format_display(self, max_lines: int | None = None) -> str:
        """Format lyrics for display (without timestamps).

        Args:
            max_lines: Maximum number of lyric lines to include.
                       None means no limit (show all).
        """
        display_lines = []
        source = self.lines[:max_lines] if max_lines else self.lines
        for line in source:
            if line.text.strip():
                display_lines.append(line.text.strip())
        if max_lines and len(self.lines) > max_lines:
            display_lines.append("...")
        return "\n".join(display_lines)


def _parse_lrc(lrc_text: str) -> list[LyricLine]:
    """Parse LRC format into a list of LyricLine."""
    import re

    lines: list[LyricLine] = []
    pattern = re.compile(r"\[(\d+):(\d+)\.(\d+)\](.*)")

    for line in lrc_text.split("\n"):
        match = pattern.match(line.strip())
        if match:
            minutes = int(match.group(1))
            seconds = int(match.group(2))
            ms = int(match.group(3))
            # LRC milliseconds can be 2 or 3 digits
            if ms > 99:
                ms = ms // 10
            text = match.group(4).strip()
            timestamp = timedelta(minutes=minutes, seconds=seconds, milliseconds=ms)
            lines.append(LyricLine(timestamp=timestamp, text=text))

    return lines
