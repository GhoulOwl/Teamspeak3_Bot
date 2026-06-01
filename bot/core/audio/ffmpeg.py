"""FFmpeg subprocess management for audio playback."""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil
import signal
import subprocess
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

# Callback type for when FFmpeg finishes or errors
FFmpegCallback = Callable[[], Coroutine[Any, Any, None]]


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

    async def start(self, url: str, volume: int = 70) -> None:
        """Start FFmpeg to play a URL/file.

        On Linux: outputs to PulseAudio sink
        On macOS: outputs to Core Audio (system default speaker)

        Args:
            url: Audio source URL or file path
            volume: Volume level 0-100 (converted to FFmpeg gain)
        """
        if self.is_running:
            await self.stop()

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

        cmd.extend([
            "-i", url,
            "-af", f"volume={gain}",
            "-ac", str(self._channels),
            "-ar", str(self._sample_rate),
            "-nostdin",
            "-y",
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

        logger.info("Starting FFmpeg: %s ...", " ".join(cmd[:6]))

        # Reset stderr buffer for the new process
        self._stderr_lines = []

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
                if text:
                    logger.debug("FFmpeg: %s", text)
                    # Buffer last N lines for error diagnosis
                    self._stderr_lines.append(text)
                    if len(self._stderr_lines) > self._max_stderr_lines:
                        self._stderr_lines.pop(0)

        except asyncio.CancelledError:
            return

        # Process ended — check exit code
        if self._process:
            await self._process.wait()
            returncode = self._process.returncode

            if returncode == 0 or returncode == -signal.SIGTERM:
                # Normal end of stream
                logger.info("FFmpeg finished (exit code %d)", returncode)
                if self._on_eof:
                    await self._on_eof()
            elif returncode == -25:
                # SIGSTOP (pause), not an error
                pass
            else:
                # Log captured stderr so the user can diagnose the problem
                if self._stderr_lines:
                    logger.warning(
                        "FFmpeg exited with code %d. Last stderr lines:\n  %s",
                        returncode,
                        "\n  ".join(self._stderr_lines[-20:]),
                    )
                else:
                    logger.warning("FFmpeg exited with code %d", returncode)
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
