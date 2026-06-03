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

            # Parse sink inputs into individual blocks to avoid cross-block
            # regex matching (re.DOTALL with .*? can span across blocks).
            blocks = re.split(r"(?=Sink Input #\d+)", output)
            entries: list[tuple[str, str, str]] = []  # (id, sink, app_name)
            for block in blocks:
                id_m = re.search(r"Sink Input #(\d+)", block)
                sink_m = re.search(r"Sink:\s*(\d+)", block)
                app_m = re.search(r'application\.name\s*=\s*"([^"]*)"', block)
                if id_m and sink_m:
                    entries.append((
                        id_m.group(1),
                        sink_m.group(1),
                        app_m.group(1) if app_m else "",
                    ))

            # Step 1: Check if FFmpeg is already on the correct sink.
            # Match by application name starting with "Lavf" (libavformat).
            for input_id, sink_id, app_name in entries:
                if app_name.startswith("Lavf") and sink_id == self._pulse_sink:
                    self._sink_input_id = input_id
                    logger.debug("FFmpeg sink input #%s on correct sink", input_id)
                    await self._apply_volume(self._sink_input_id)
                    return

            # Step 2: FFmpeg not on correct sink — find and move it.
            for input_id, sink_id, app_name in entries:
                if app_name.startswith("Lavf"):
                    logger.info(
                        "FFmpeg sink input #%s on wrong sink %s, moving to %s",
                        input_id, sink_id, self._pulse_sink,
                    )
                    move_proc = await asyncio.create_subprocess_exec(
                        "pactl", "move-sink-input", input_id, self._pulse_sink,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    _, stderr = await move_proc.communicate()
                    if move_proc.returncode == 0:
                        self._sink_input_id = input_id
                        logger.info(
                            "Moved FFmpeg sink input #%s to %s",
                            input_id, self._pulse_sink,
                        )
                        await self._apply_volume(self._sink_input_id)
                    else:
                        logger.warning(
                            "Failed to move sink input: %s",
                            stderr.decode(errors="replace").strip(),
                        )
                        self._sink_input_id = None
                    return

            logger.debug("FFmpeg sink input not found for sink %s", self._pulse_sink)
            self._sink_input_id = None

        except FileNotFoundError:
            logger.warning("pactl not found, volume control unavailable")
        except Exception:
            logger.exception("Failed to refresh sink input")

    def get_ffmpeg_gain(self) -> float:
        """Get the FFmpeg volume gain value for process start."""
        return self._current_volume / 100.0
