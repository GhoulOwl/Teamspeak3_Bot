"""Audio controller — high-level state machine for audio playback."""

from __future__ import annotations

import asyncio
import enum
import logging
import os
import platform
from typing import Any, Callable, Coroutine

from bot.core.audio.ffmpeg import FFmpegProcess
from bot.core.audio.volume import VolumeController

logger = logging.getLogger(__name__)

PlaybackCallback = Callable[[], Coroutine[Any, Any, None]]


class PlaybackState(enum.Enum):
    IDLE = "idle"
    PLAYING = "playing"
    PAUSED = "paused"


class AudioController:
    """High-level audio playback controller.

    Manages FFmpeg process lifecycle, volume control, and state transitions.
    Emits events when playback starts, stops, or errors.
    """

    def __init__(
        self,
        ffmpeg_path: str | None = None,
        pulse_sink: str = "ts3bot_music",
        default_volume: int = 70,
        fade_duration_ms: int = 500,
    ) -> None:
        self._ffmpeg = FFmpegProcess(
            ffmpeg_path=ffmpeg_path,
            pulse_sink=pulse_sink,
        )
        self._volume = VolumeController(pulse_sink=pulse_sink)
        self._volume._current_volume = default_volume

        self._state = PlaybackState.IDLE
        self._default_volume = default_volume
        self._fade_duration_ms = fade_duration_ms
        self._is_macos = platform.system() == "Darwin"

        # Temp file from yt-dlp download (cleaned up after playback)
        self._current_temp_file: str | None = None

        # Callbacks
        self._on_playback_stopped: PlaybackCallback | None = None
        self._on_playback_error: PlaybackCallback | None = None

    @property
    def state(self) -> PlaybackState:
        return self._state

    @property
    def volume(self) -> int:
        return self._volume.volume

    def set_callbacks(
        self,
        on_stopped: PlaybackCallback | None = None,
        on_error: PlaybackCallback | None = None,
    ) -> None:
        """Set callbacks for playback events."""
        self._on_playback_stopped = on_stopped
        self._on_playback_error = on_error

    async def play(
        self,
        source: str,
        *,
        temp_file: str | None = None,
        duration: float = 0,
    ) -> None:
        """Start playing a source (URL or local file path).

        Stops any current playback first. If *temp_file* is given it will
        be deleted once playback finishes or is stopped.

        Args:
            source: Audio source URL or local file path
            temp_file: Temp file to clean up after playback
            duration: Expected duration in seconds (for premature-exit detection)
        """
        if self._state != PlaybackState.IDLE:
            await self.stop()

        # Clean up previous temp file if any
        self._cleanup_temp_file()
        self._current_temp_file = temp_file

        # Set FFmpeg callbacks
        self._ffmpeg.set_callbacks(
            on_eof=self._handle_eof,
            on_error=self._handle_error,
        )

        await self._ffmpeg.start(
            source,
            volume=self._volume.volume,
            expected_duration=duration,
        )
        self._state = PlaybackState.PLAYING
        logger.info("Playback started: %s (duration: %.0fs)", source[:80], duration)

        # Refresh sink input for volume control (Linux only, give FFmpeg a moment to start)
        if not self._is_macos:
            await asyncio.sleep(0.5)
            await self._volume.refresh_sink_input()

    async def stop(self) -> None:
        """Stop playback."""
        await self._ffmpeg.stop()
        self._state = PlaybackState.IDLE
        self._cleanup_temp_file()
        logger.info("Playback stopped")

    async def pause(self) -> None:
        """Pause playback."""
        if self._state == PlaybackState.PLAYING:
            await self._ffmpeg.pause()
            self._state = PlaybackState.PAUSED
            logger.info("Playback paused")

    async def resume(self) -> None:
        """Resume paused playback."""
        if self._state == PlaybackState.PAUSED:
            await self._ffmpeg.resume()
            self._state = PlaybackState.PLAYING
            logger.info("Playback resumed")

    async def set_volume(self, volume: int) -> None:
        """Set volume with fade transition."""
        await self._volume.set_volume(volume, fade_ms=self._fade_duration_ms)
        logger.info("Volume set to %d%%", volume)

    async def fade_out_and_stop(self) -> None:
        """Fade out audio and stop playback."""
        if self._state == PlaybackState.IDLE:
            return

        if self._state == PlaybackState.PAUSED:
            await self.stop()
            return

        # Fade volume to 0, then stop
        old_volume = self._volume.volume
        await self._volume.set_volume(0, fade_ms=self._fade_duration_ms)
        await self.stop()
        # Restore volume for next playback
        self._volume._current_volume = old_volume

    async def _handle_eof(self) -> None:
        """Handle end-of-stream from FFmpeg."""
        self._state = PlaybackState.IDLE
        self._cleanup_temp_file()
        logger.info("Playback finished (EOF)")
        if self._on_playback_stopped:
            await self._on_playback_stopped()

    async def _handle_error(self) -> None:
        """Handle FFmpeg error."""
        self._state = PlaybackState.IDLE
        self._cleanup_temp_file()
        logger.warning("Playback error")
        if self._on_playback_error:
            await self._on_playback_error()

    def _cleanup_temp_file(self) -> None:
        """Remove the temp audio file if one exists."""
        path = self._current_temp_file
        self._current_temp_file = None
        if path:
            try:
                os.unlink(path)
                logger.debug("Cleaned up temp file: %s", path)
            except OSError:
                pass
