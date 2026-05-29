"""Netease Cloud Music client using yt-dlp for audio extraction.

Replaces the deprecated NeteaseCloudMusicApi approach.
- Search: Uses Netease's web search API directly
- Audio URL: Uses yt-dlp to extract playable URLs from music.163.com
- Also supports YouTube, Bilibili, SoundCloud via yt-dlp
"""

from __future__ import annotations

import logging
from datetime import timedelta

from bot.services.netease.cache import TTLCache
from bot.services.netease.models import Lyrics, Song
from bot.services.netease.ytdlp import AudioInfo, YtDlpService

logger = logging.getLogger(__name__)


class MusicAPIError(Exception):
    """Error from music API."""


class NeteaseAPIClient:
    """Music client using Netease web search + yt-dlp for audio extraction.

    Key design: Audio URLs are NEVER cached — yt-dlp extracts fresh URLs
    each time since CDN links expire quickly.
    """

    def __init__(self, api_base_url: str = "", quality: str = "exhigh") -> None:
        # api_base_url kept for config compatibility, no longer used
        self._ytdlp = YtDlpService()
        self._cache = TTLCache()

    async def close(self) -> None:
        pass  # No persistent HTTP client needed

    async def search(self, keyword: str, limit: int = 10) -> list[Song]:
        """Search songs by keyword via Netease web search API."""
        cache_key = f"search:{keyword}:{limit}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            results = await self._ytdlp.search_netease(keyword, limit=limit)
        except Exception as e:
            raise MusicAPIError(f"Search failed: {e}") from e

        songs = []
        for r in results:
            songs.append(
                Song(
                    id=r["id"],
                    title=r["title"],
                    artist=r["artist"],
                    album=r.get("album", ""),
                    duration=timedelta(milliseconds=r.get("duration_ms", 0)),
                    cover_url=None,
                )
            )

        self._cache.set(cache_key, songs, ttl_seconds=600)  # 10 min
        return songs

    async def get_song_url(self, song_id: int) -> str | None:
        """Extract a fresh audio URL for a Netease song via yt-dlp.

        IMPORTANT: Always call this right before playback.
        yt-dlp returns CDN URLs that expire quickly.
        """
        netease_url = f"https://music.163.com/song?id={song_id}"
        return await self.extract_url(netease_url)

    async def extract_url(self, url: str) -> str | None:
        """Extract a playable audio URL from any supported platform.

        Supports: Netease Cloud Music, YouTube, Bilibili, SoundCloud, etc.
        """
        info = await self._ytdlp.extract_audio(url)
        if info:
            logger.info(
                "Extracted audio: %s (%s, %.0fs)",
                info.title,
                info.source,
                info.duration,
            )
            return info.url
        return None

    async def extract_info(self, url: str) -> AudioInfo | None:
        """Extract full audio info from a URL (title, duration, etc.)."""
        return await self._ytdlp.extract_audio(url)

    async def get_song_detail(self, song_ids: list[int]) -> list[Song]:
        """Get detailed info for songs.

        Note: With yt-dlp approach, details come from search results.
        This is kept for API compatibility.
        """
        # Details are already included in search results
        return []

    async def get_playlist_tracks(self, playlist_id: int) -> list[Song]:
        """Get tracks from a Netease playlist via yt-dlp.

        Note: Full playlist support is not yet implemented.
        """
        # TODO: Use yt-dlp to extract playlist tracks
        _ = playlist_id
        return []

    async def get_lyrics(self, song_id: int) -> Lyrics | None:
        """Get lyrics for a Netease song.

        Uses the Netease lyric API endpoint which is still functional.
        """
        cache_key = f"lyrics:{song_id}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        import httpx

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://music.163.com/api/song/lyric",
                    params={"id": song_id, "lv": 1, "tv": -1},
                    headers={
                        "Referer": "https://music.163.com",
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36"
                        ),
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            raise MusicAPIError(f"Get lyrics failed: {e}") from e

        lrc = data.get("lrc", {})
        tlyric = data.get("tlyric", {})

        lrc_text = lrc.get("lyric", "")
        if not lrc_text:
            return None

        trans_text = tlyric.get("lyric") if tlyric else None
        lyrics = Lyrics.from_lrc(lrc_text, trans_text)

        self._cache.set(cache_key, lyrics, ttl_seconds=1800)  # 30 min
        return lyrics

    def is_supported_url(self, url: str) -> bool:
        """Check if a URL is supported by yt-dlp."""
        return self._ytdlp.is_supported_url(url)
