"""yt-dlp integration service for extracting audio URLs from various platforms.

Supports: Netease Cloud Music, YouTube, Bilibili, SoundCloud, and many more.
Replaces the deprecated NeteaseCloudMusicApi for URL extraction.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Any

import yt_dlp

logger = logging.getLogger(__name__)


@dataclass
class AudioInfo:
    """Extracted audio information from a URL."""

    url: str  # Direct audio stream URL
    title: str
    duration: float  # seconds
    thumbnail: str | None = None
    source: str = ""  # e.g., "NetEaseMusic", "Youtube", "BiliBili"
    original_url: str = ""


@dataclass
class DownloadedAudio:
    """Result of downloading audio to a local temp file."""

    path: str  # Local file path
    title: str
    duration: float
    source: str = ""
    original_url: str = ""


class YtDlpService:
    """Async wrapper around yt-dlp for extracting audio URLs.

    yt-dlp is run in a thread pool to avoid blocking the event loop.
    Supports Netease Cloud Music, YouTube, Bilibili, and many more platforms.
    """

    def __init__(self, cookie_file: str | None = None) -> None:
        self._base_opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "nocheckcertificate": True,
            "retries": 3,
            "socket_timeout": 15,
            # Only extract audio
            "format": "bestaudio/best",
            # Don't download, just extract info
            "skip_download": True,
            # Extract flat playlist info (don't recurse into each song)
            "extract_flat": False,
        }
        if cookie_file:
            self._base_opts["cookiefile"] = cookie_file
            logger.info("yt-dlp cookie file configured: %s", cookie_file)

    async def extract_audio(self, url: str) -> AudioInfo | None:
        """Extract a playable audio URL from any supported platform.

        Args:
            url: The page/song URL (e.g., music.163.com/song?id=123)

        Returns:
            AudioInfo with direct stream URL, or None on failure
        """
        try:
            info = await asyncio.to_thread(self._extract_sync, url)
            return info
        except Exception:
            logger.exception("yt-dlp extraction failed for %s", url)
            return None

    async def download_audio(
        self,
        url: str,
        cache_dir: str | None = None,
    ) -> DownloadedAudio | None:
        """Download audio to a local temp file to avoid CDN URL expiration.

        Args:
            url: The page/song URL
            cache_dir: Directory for temp files (default: system temp)

        Returns:
            DownloadedAudio with local file path, or None on failure
        """
        try:
            return await asyncio.to_thread(self._download_sync, url, cache_dir)
        except Exception:
            logger.exception("yt-dlp download failed for %s", url)
            return None

    def _download_sync(
        self,
        url: str,
        cache_dir: str | None,
    ) -> DownloadedAudio | None:
        """Synchronous download (runs in thread pool)."""
        out_dir = cache_dir or tempfile.gettempdir()
        os.makedirs(out_dir, exist_ok=True)
        outtmpl = os.path.join(out_dir, "ts3bot_%(id)s.%(ext)s")

        opts = {
            **self._base_opts,
            "skip_download": False,
            "outtmpl": outtmpl,
            "noplaylist": True,
        }

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)

        if not info:
            return None

        # Find the downloaded file
        filepath = ydl.prepare_filename(info)
        if not os.path.isfile(filepath):
            # Extension might differ after post-processing
            base = os.path.splitext(filepath)[0]
            for ext in (".mp3", ".m4a", ".opus", ".ogg", ".webm", ".flac"):
                candidate = base + ext
                if os.path.isfile(candidate):
                    filepath = candidate
                    break
            else:
                logger.error("Downloaded file not found: %s", filepath)
                return None

        # Validate file size (at least 10 KB — smaller files are likely errors)
        file_size = os.path.getsize(filepath)
        if file_size < 10 * 1024:
            logger.error(
                "Downloaded file too small: %s (%d bytes), likely corrupted",
                filepath,
                file_size,
            )
            return None

        # Validate actual duration with ffprobe (metadata duration may be wrong)
        actual_duration = self._ffprobe_duration(filepath)
        expected_duration = info.get("duration", 0) or 0
        logger.info(
            "Downloaded file: %s (%.2f MB, ffprobe: %.1fs, metadata: %.0fs)",
            filepath,
            file_size / (1024 * 1024),
            actual_duration,
            expected_duration,
        )

        if actual_duration > 0 and expected_duration > 10:
            if actual_duration < expected_duration * 0.5:
                logger.error(
                    "Downloaded file duration %.1fs much less than expected %.0fs "
                    "— file is truncated or a low-quality preview",
                    actual_duration,
                    expected_duration,
                )
                return None

        # Use actual duration if available, fallback to metadata
        final_duration = actual_duration if actual_duration > 0 else expected_duration

        extractor = info.get("extractor", "")
        source_map = {
            "NetEaseMusic": "网易云音乐",
            "Youtube": "YouTube",
            "BiliBili": "Bilibili",
            "SoundCloud": "SoundCloud",
        }
        source = source_map.get(extractor, extractor)

        logger.info(
            "Downloaded audio: %s (%.0fs) -> %s",
            info.get("title", "?"),
            final_duration,
            filepath,
        )

        return DownloadedAudio(
            path=filepath,
            title=info.get("title", "Unknown"),
            duration=final_duration,
            source=source,
            original_url=url,
        )

    @staticmethod
    def _ffprobe_duration(filepath: str) -> float:
        """Get actual file duration using ffprobe.  Returns 0 on failure."""
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            return 0.0
        try:
            result = subprocess.run(
                [
                    ffprobe,
                    "-v", "quiet",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    filepath,
                ],
                capture_output=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                return float(result.stdout.strip())
        except (subprocess.TimeoutExpired, ValueError, FileNotFoundError):
            pass
        return 0.0

    def _extract_sync(self, url: str) -> AudioInfo | None:
        """Synchronous extraction (runs in thread pool)."""
        opts = {**self._base_opts}

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            return None

        # Get the best audio URL
        audio_url = info.get("url")

        # If no direct URL, check formats
        if not audio_url:
            formats = info.get("formats", [])
            # Find best audio-only format
            audio_formats = [
                f for f in formats
                if f.get("acodec") != "none" and f.get("vcodec") in ("none", None)
            ]
            if audio_formats:
                # Pick highest quality audio
                best = max(audio_formats, key=lambda f: f.get("abr", 0) or 0)
                audio_url = best.get("url")
            elif formats:
                # Fallback to any format with audio
                for f in reversed(formats):
                    if f.get("acodec") != "none" and f.get("url"):
                        audio_url = f["url"]
                        break

        if not audio_url:
            return None

        # Detect source platform
        extractor = info.get("extractor", "")
        source_map = {
            "NetEaseMusic": "网易云音乐",
            "Youtube": "YouTube",
            "BiliBili": "Bilibili",
            "SoundCloud": "SoundCloud",
        }
        source = source_map.get(extractor, extractor)

        return AudioInfo(
            url=audio_url,
            title=info.get("title", "Unknown"),
            duration=info.get("duration", 0) or 0,
            thumbnail=info.get("thumbnail"),
            source=source,
            original_url=url,
        )

    async def search_netease(self, keyword: str, limit: int = 10) -> list[dict]:
        """Search Netease Cloud Music via their web API.

        This endpoint is still functional even though the old
        NeteaseCloudMusicApi project is deprecated.

        Args:
            keyword: Search query
            limit: Max results

        Returns:
            List of song dicts with id, title, artist fields
        """
        import httpx

        url = "https://music.163.com/api/search/get/web"
        params = {
            "s": keyword,
            "type": "1",  # 1 = songs
            "limit": str(limit),
            "offset": "0",
        }
        headers = {
            "Referer": "https://music.163.com",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, data=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            logger.exception("Netease search failed")
            return []

        songs_raw = data.get("result", {}).get("songs", [])
        results = []
        for s in songs_raw:
            artists = s.get("artists", [])
            artist_name = " / ".join(a.get("name", "") for a in artists) if artists else ""

            results.append({
                "id": s.get("id", 0),
                "title": s.get("name", ""),
                "artist": artist_name,
                "album": s.get("album", {}).get("name", ""),
                "duration_ms": s.get("duration", 0),
                "url": f"https://music.163.com/song?id={s.get('id', 0)}",
            })

        return results

    def is_supported_url(self, url: str) -> bool:
        """Check if a URL is likely supported by yt-dlp."""
        supported_domains = [
            "music.163.com",
            "youtube.com",
            "youtu.be",
            "bilibili.com",
            "b23.tv",
            "soundcloud.com",
            "bandcamp.com",
        ]
        return any(domain in url for domain in supported_domains)
