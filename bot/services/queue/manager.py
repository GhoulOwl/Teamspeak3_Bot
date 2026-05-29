"""Music queue manager with multi-user support and skip voting."""

from __future__ import annotations

import enum
import logging
import random
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from bot.services.netease.models import Song
from bot.utils.formatting import format_duration

logger = logging.getLogger(__name__)


class RepeatMode(enum.Enum):
    OFF = "off"
    ONE = "one"
    ALL = "all"


@dataclass
class QueueEntry:
    """An entry in the music queue."""

    song: Song
    requester_clid: int
    requester_name: str
    request_time: datetime = field(default_factory=datetime.now)
    is_url: bool = False  # True if this is a direct URL, not a Netease song


class MusicQueue:
    """Manages the music playback queue.

    Features:
    - FIFO queue with multi-user fairness
    - Skip voting (majority of channel users)
    - Repeat modes (off/one/all)
    - History tracking
    """

    def __init__(self) -> None:
        self._queue: list[QueueEntry] = []
        self._current: QueueEntry | None = None
        self._history: deque[QueueEntry] = deque(maxlen=50)
        self._repeat_mode = RepeatMode.OFF
        self._skip_votes: set[int] = set()

    @property
    def current(self) -> QueueEntry | None:
        return self._current

    @property
    def is_empty(self) -> bool:
        return len(self._queue) == 0

    @property
    def length(self) -> int:
        return len(self._queue)

    @property
    def repeat_mode(self) -> RepeatMode:
        return self._repeat_mode

    @repeat_mode.setter
    def repeat_mode(self, mode: RepeatMode) -> None:
        self._repeat_mode = mode

    def add(
        self,
        song: Song,
        requester_clid: int,
        requester_name: str,
        is_url: bool = False,
    ) -> QueueEntry:
        """Add a song to the queue."""
        entry = QueueEntry(
            song=song,
            requester_clid=requester_clid,
            requester_name=requester_name,
            is_url=is_url,
        )
        self._queue.append(entry)
        logger.info("Queued: %s (requested by %s)", song.display_name, requester_name)
        return entry

    def next(self) -> QueueEntry | None:
        """Get the next song to play.

        Handles repeat modes:
        - ONE: replay the current song
        - ALL: put the current song back at the end
        - OFF: normal FIFO
        """
        # Handle repeat-one
        if self._repeat_mode == RepeatMode.ONE and self._current:
            self._skip_votes.clear()
            return self._current

        # Move current to history
        if self._current:
            self._history.append(self._current)

            # Handle repeat-all: put current back at end of queue
            if self._repeat_mode == RepeatMode.ALL:
                self._queue.append(self._current)

        # Get next from queue
        if not self._queue:
            self._current = None
            self._skip_votes.clear()
            return None

        self._current = self._queue.pop(0)
        self._skip_votes.clear()
        return self._current

    def peek(self, n: int = 10) -> list[QueueEntry]:
        """Peek at the next N songs without removing them."""
        return self._queue[:n]

    def remove(self, index: int) -> QueueEntry | None:
        """Remove a song at the given index (0-based)."""
        if 0 <= index < len(self._queue):
            return self._queue.pop(index)
        return None

    def clear(self) -> int:
        """Clear the queue. Returns number of removed entries."""
        count = len(self._queue)
        self._queue.clear()
        self._skip_votes.clear()
        return count

    def shuffle(self) -> None:
        """Shuffle the queue."""
        random.shuffle(self._queue)

    def vote_skip(self, clid: int, total_channel_users: int) -> bool:
        """Register a skip vote. Returns True if skip threshold is met."""
        self._skip_votes.add(clid)
        threshold = max(1, total_channel_users // 2 + 1)
        return len(self._skip_votes) >= threshold

    def toggle_repeat(self) -> RepeatMode:
        """Cycle through repeat modes: OFF → ONE → ALL → OFF."""
        modes = [RepeatMode.OFF, RepeatMode.ONE, RepeatMode.ALL]
        current_idx = modes.index(self._repeat_mode)
        self._repeat_mode = modes[(current_idx + 1) % len(modes)]
        return self._repeat_mode

    def format_queue(self, max_items: int = 10) -> str:
        """Format the queue for display in TS3 chat."""
        if not self._current and not self._queue:
            return "队列为空"

        lines = []

        # Current song
        if self._current:
            song = self._current.song
            lines.append(
                f"[B]正在播放:[/B] {song.display_name} "
                f"({format_duration(song.duration)}) "
                f"- 点歌: {self._current.requester_name}"
            )
            lines.append(f"重复模式: {self._repeat_mode.value}")
            lines.append("")

        # Upcoming songs
        if self._queue:
            lines.append(f"[B]队列 ({len(self._queue)} 首):[/B]")
            for i, entry in enumerate(self._queue[:max_items], 1):
                song = entry.song
                lines.append(
                    f"  {i}. {song.display_name} "
                    f"({format_duration(song.duration)}) "
                    f"- {entry.requester_name}"
                )
            if len(self._queue) > max_items:
                lines.append(f"  ... 还有 {len(self._queue) - max_items} 首")
        else:
            lines.append("队列为空，输入 !play <歌曲名> 点歌")

        return "\n".join(lines)

    def format_now_playing(self) -> str:
        """Format the current song info."""
        if not self._current:
            return "当前没有在播放"

        song = self._current.song
        return (
            f"[B]正在播放:[/B]\n"
            f"  歌曲: {song.title}\n"
            f"  歌手: {song.artist}\n"
            f"  专辑: {song.album}\n"
            f"  时长: {format_duration(song.duration)}\n"
            f"  点歌: {self._current.requester_name}"
        )
