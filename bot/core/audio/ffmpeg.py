"""FFmpeg subprocess management for audio playback."""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import re
import shutil
import signal
import subprocess
import time
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

# Callback type for when FFmpeg finishes or errors
FFmpegCallback = Callable[[], Coroutine[Any, Any, None]]

# Pattern to parse FFmpeg progress: time=HH:MM:SS.cc
_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")


def _parse_ffmpeg_time(line: str) -> float | None:
    """Parse FFmpeg progress time from a stderr line.  Returns seconds or None."""
    m = _TIME_RE.search(line)
    if not m:
        return None
    h, mn, s = m.groups()
    return int(h) * 3600 + int(mn) * 60 + float(s)


class FFmpegProcess:
    """Manages a single FFmpeg subprocess that decodes audio.

    On Linux: outputs to PulseAudio sink via ``-f pulse``
    On macOS: outputs to AudioToolbox (system default) via ``-f audiotoolbox``

    On Linux the PulseAudio server address is read from the ``PULSE_SERVER``
    environment variable (e.g. ``unix:/tmp/pulse-native``).  When the variable
    is unset the default PulseAudio detection is used.
    """

    def __init__(
        self,
        ffmpeg_path: str | None = None,
        pulse_sink: str = "ts3bot_sink",
        sample_rate: int = 48000,
        channels: int = 2,
    ) -> None:
        # Auto-detect ffmpeg if not specified
        if ffmpeg_path is None:
            ffmpeg_path = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"
        self._ffmpeg_path = ffmpeg_path
        self._pulse_sink = pulse_sink
        self._sample_rate = sample_rate
        self._channels = channels
        self._is_macos = platform.system() == "Darwin"

        self._process: asyncio.subprocess.Process | None = None
        self._monitor_task: asyncio.Task | None = None

        self._on_eof: FFmpegCallback | None = None
        self._on_error: FFmpegCallback | None = None

        # Buffer the last N stderr lines so we can log them on failure
        self._stderr_lines: list[str] = []
        self._max_stderr_lines = 50

        # Progress / duration tracking
        self._expected_duration: float = 0
        self._start_time: float = 0
        self._last_progress_time: float = 0.0
        self._last_progress_log: float = 0.0

        # Warn early when PulseAudio looks unavailable on Linux
        if not self._is_macos and not self._check_pulse_available():
            logger.warning(
                "PulseAudio does not appear to be running — "
                "FFmpeg playback will fail. Start PulseAudio "
                "(e.g. 'pulseaudio --start') or set PULSE_SERVER."
            )

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    def set_callbacks(
        self,
        on_eof: FFmpegCallback | None = None,
        on_error: FFmpegCallback | None = None,
    ) -> None:
        """Set callbacks for end-of-stream and error events."""
        self._on_eof = on_eof
        self._on_error = on_error

    async def start(
        self,
        url: str,
        volume: int = 70,
        expected_duration: float = 0,
    ) -> None:
        """Start FFmpeg to play a URL/file.

        On Linux: outputs to PulseAudio sink
        On macOS: outputs to Core Audio (system default speaker)

        Args:
            url: Audio source URL or file path
            volume: Volume level 0-100 (converted to FFmpeg gain)
            expected_duration: Expected duration in seconds (for premature-exit detection)
        """
        if self.is_running:
            await self.stop()

        # Log local file info before starting
        if not url.startswith(("http://", "https://")):
            if os.path.isfile(url):
                file_size = os.path.getsize(url)
                logger.info(
                    "Input file: %s (size: %.2f MB)",
                    url,
                    file_size / (1024 * 1024),
                )
            else:
                logger.warning("Input file does not exist: %s", url)

        gain = volume / 100.0
        is_http = url.startswith(("http://", "https://"))

        cmd = [self._ffmpeg_path]

        # Reconnect flags only apply to HTTP streams
        if is_http:
            cmd.extend([
                "-reconnect", "1",
                "-reconnect_streamed", "1",
                "-reconnect_delay_max", "5",
            ])
        else:
            # For local files, read at native frame rate (real-time).
            # Without this FFmpeg pushes audio as fast as possible, which can
            # overwhelm PulseAudio buffers and cause early stream termination.
            cmd.append("-re")

        cmd.extend([
            "-i", url,
            "-af", f"volume={gain}",
            "-ac", str(self._channels),
            "-ar", str(self._sample_rate),
            "-nostdin",
            "-y",
            # Show warnings/errors in stderr for diagnosis
            "-v", "warning",
        ])

        if self._is_macos:
            cmd.extend(["-f", "audiotoolbox"])
            cmd.append("default")
        else:
            # Linux: output to PulseAudio.
            # Respect PULSE_SERVER environment variable if set,
            # otherwise let FFmpeg auto-detect PulseAudio.
            cmd.extend(["-f", "pulse"])
            pulse_server = os.environ.get("PULSE_SERVER")
            if pulse_server:
                cmd.extend(["-server", pulse_server])
            cmd.append(self._pulse_sink)

        logger.info("Starting FFmpeg: %s", " ".join(cmd))

        # Reset state for the new process
        self._stderr_lines = []
        self._expected_duration = expected_duration
        self._start_time = time.monotonic()
        self._last_progress_time = 0.0
        self._last_progress_log = 0.0

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )

        self._monitor_task = asyncio.create_task(
            self._monitor_stderr(), name="ffmpeg-monitor"
        )

    async def stop(self) -> None:
        """Stop the FFmpeg process."""
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        if self._process and self._process.returncode is None:
            try:
                self._process.terminate()
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    self._process.kill()
                    await self._process.wait()
            except ProcessLookupError:
                pass

        self._process = None
        self._monitor_task = None

    async def pause(self) -> None:
        """Pause playback by sending SIGSTOP."""
        if self._process and self._process.returncode is None:
            self._process.send_signal(signal.SIGSTOP)

    async def resume(self) -> None:
        """Resume playback by sending SIGCONT."""
        if self._process and self._process.returncode is None:
            self._process.send_signal(signal.SIGCONT)

    async def _monitor_stderr(self) -> None:
        """Monitor FFmpeg stderr for progress, errors, and EOF."""
        if not self._process or not self._process.stderr:
            return

        try:
            while True:
                line = await self._process.stderr.readline()
                if not line:
                    break

                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue

                # Parse FFmpeg progress lines (contain time=, size=, etc.)
                progress = _parse_ffmpeg_time(text)
                if progress is not None:
                    self._last_progress_time = progress
                    # Log progress every ~30 seconds of audio
                    if progress - self._last_progress_log >= 30:
                        logger.info(
                            "FFmpeg progress: %.1fs / %.0fs",
                            progress,
                            self._expected_duration or 0,
                        )
                        self._last_progress_log = progress
                    # Don't buffer progress lines — they flood the buffer
                    continue

                # Non-progress line: buffer and log at debug
                logger.debug("FFmpeg: %s", text)
                self._stderr_lines.append(text)
                if len(self._stderr_lines) > self._max_stderr_lines:
                    self._stderr_lines.pop(0)

        except asyncio.CancelledError:
            return

        # ── Process ended — analyse exit ──────────────────────────────
        if self._process:
            await self._process.wait()
            returncode = self._process.returncode
            elapsed = time.monotonic() - self._start_time

            if returncode == 0 or returncode == -signal.SIGTERM:
                # Check for premature exit
                premature = (
                    self._expected_duration > 10
                    and self._last_progress_time < self._expected_duration * 0.8
                )

                if premature:
                    logger.warning(
                        "FFmpeg finished EARLY (exit code %d): "
                        "progress %.1fs / expected %.0fs, wall %.1fs. "
                        "Last stderr:\n  %s",
                        returncode,
                        self._last_progress_time,
                        self._expected_duration,
                        elapsed,
                        "\n  ".join(self._stderr_lines[-20:])
                        if self._stderr_lines
                        else "(no stderr captured)",
                    )
                else:
                    logger.info(
                        "FFmpeg finished (exit code %d, progress %.1fs, wall %.1fs)",
                        returncode,
                        self._last_progress_time,
                        elapsed,
                    )
                    # Still log stderr if any warnings were captured
                    if self._stderr_lines:
                        logger.info(
                            "FFmpeg stderr:\n  %s",
                            "\n  ".join(self._stderr_lines[-20:]),
                        )

                if self._on_eof:
                    await self._on_eof()

            elif returncode == -25:
                # SIGSTOP (pause), not an error
                pass
            else:
                # Error exit — always show stderr
                if self._stderr_lines:
                    logger.warning(
                        "FFmpeg exited with code %d (progress %.1fs, wall %.1fs). "
                        "Last stderr:\n  %s",
                        returncode,
                        self._last_progress_time,
                        elapsed,
                        "\n  ".join(self._stderr_lines[-20:]),
                    )
                else:
                    logger.warning(
                        "FFmpeg exited with code %d (progress %.1fs, wall %.1fs)",
                        returncode,
                        self._last_progress_time,
                        elapsed,
                    )
                if self._on_error:
                    await self._on_error()

    @staticmethod
    def _check_pulse_available() -> bool:
        """Return True if PulseAudio seems reachable."""
        pulse_server = os.environ.get("PULSE_SERVER")
        try:
            cmd = ["pactl", "info"]
            env = {**os.environ, "PULSE_SERVER": pulse_server} if pulse_server else None
            result = subprocess.run(cmd, capture_output=True, timeout=5, env=env)
            if result.returncode == 0:
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Fallback: try connecting to the Unix socket when PULSE_SERVER
        # looks like unix:/path
        if pulse_server and pulse_server.startswith("unix:"):
            socket_path = pulse_server[len("unix:"):]
            import socket as _socket
            try:
                sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
                sock.settimeout(2)
                sock.connect(socket_path)
                sock.close()
                return True
            except OSError:
                pass

        return False
