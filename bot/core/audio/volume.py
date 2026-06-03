"""Volume control via PulseAudio pactl and FFmpeg filter."""

from __future__ import annotations

import asyncio
import logging
import platform
import re

logger = logging.getLogger(__name__)


class VolumeController:
    """Controls audio volume using a two-layer approach:

    - Coarse: FFmpeg volume filter (set at process start)
    - Fine: pactl set-sink-input-volume (real-time adjustment, Linux only)

    On macOS, only FFmpeg-level volume control is available.
    """

    def __init__(self, pulse_sink: str = "ts3bot_music") -> None:
        self._pulse_sink = pulse_sink
        self._current_volume: int = 70  # 0-100
        self._sink_input_id: str | None = None
        self._is_macos = platform.system() == "Darwin"

    @property
    def volume(self) -> int:
        return self._current_volume

    async def set_volume(self, target: int, fade_ms: int = 500) -> None:
        """Set volume with optional fade transition.

        Args:
            target: Target volume 0-100
            fade_ms: Fade duration in milliseconds
        """
        target = max(0, min(100, target))

        # On macOS, only FFmpeg-level volume (no pactl)
        if self._is_macos:
            self._current_volume = target
            return

        if fade_ms > 0 and self._sink_input_id:
            await self._fade_volume(self._current_volume, target, fade_ms)
        else:
            self._current_volume = target
            if self._sink_input_id:
                await self._apply_volume(self._sink_input_id)

    async def _fade_volume(self, from_vol: int, to_vol: int, duration_ms: int) -> None:
        """Smoothly fade volume using pactl in small steps."""
        if not self._sink_input_id:
            self._current_volume = to_vol
            return

        steps = max(1, duration_ms // 20)  # 20ms per step
        step_delay = duration_ms / 1000.0 / steps

        for i in range(1, steps + 1):
            vol = from_vol + (to_vol - from_vol) * i // steps
            self._current_volume = vol
            await self._apply_volume(self._sink_input_id)
            await asyncio.sleep(step_delay)

        self._current_volume = to_vol

    async def _apply_volume(self, sink_input_id: str) -> None:
        """Apply volume to a PulseAudio sink input via pactl."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "pactl", "set-sink-input-volume", sink_input_id, f"{self._current_volume}%",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except FileNotFoundError:
            logger.warning("pactl not found, volume control unavailable")
        except Exception:
            logger.exception("Failed to set volume via pactl")

    async def refresh_sink_input(self) -> None:
        """Find the current FFmpeg sink input ID and ensure correct routing.

        Called after starting a new FFmpeg process to locate its
        PulseAudio sink input for volume control.

        PulseAudio may route FFmpeg's stream to the wrong sink (the default
        sink) even when FFmpeg explicitly requests our music sink.  When this
        happens we move the stream to the correct sink via ``pactl
        move-sink-input`` so that the TS3 client can capture it.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "pactl", "list", "sink-inputs",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            output = stdout.decode("utf-8", errors="replace")

            # Step 1: Check if FFmpeg is already on the correct sink
            pattern = rf"Sink Input #(\d+).*?Sink:\s*{re.escape(self._pulse_sink)}"
            match = re.search(pattern, output, re.DOTALL)
            if match:
                self._sink_input_id = match.group(1)
                logger.debug("Found sink input ID: %s", self._sink_input_id)
                await self._apply_volume(self._sink_input_id)
                return

            # Step 2: FFmpeg not on correct sink — find it by application name
            # (Lavf* = libavformat = FFmpeg) and move to the correct sink
            ffmpeg_pattern = (
                r"Sink Input #(\d+).*?application\.name\s*=\s*\"Lavf[^\"]*\""
            )
            ff_match = re.search(ffmpeg_pattern, output, re.DOTALL)
            if ff_match:
                input_id = ff_match.group(1)
                logger.info(
                    "FFmpeg sink input #%s on wrong sink, moving to %s",
                    input_id, self._pulse_sink,
                )
                move_proc = await asyncio.create_subprocess_exec(
                    "pactl", "move-sink-input", input_id, self._pulse_sink,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await move_proc.communicate()
                if move_proc.returncode == 0:
                    self._sink_input_id = input_id
                    logger.info("Moved FFmpeg sink input #%s to %s", input_id, self._pulse_sink)
                    await self._apply_volume(self._sink_input_id)
                else:
                    logger.warning(
                        "Failed to move sink input: %s",
                        stderr.decode(errors="replace").strip(),
                    )
                    self._sink_input_id = None
            else:
                logger.debug("Sink input not found for sink %s", self._pulse_sink)
                self._sink_input_id = None

        except FileNotFoundError:
            logger.warning("pactl not found, volume control unavailable")
        except Exception:
            logger.exception("Failed to refresh sink input")

    def get_ffmpeg_gain(self) -> float:
        """Get the FFmpeg volume gain value for process start."""
        return self._current_volume / 100.0
