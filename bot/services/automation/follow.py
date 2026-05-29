"""Follow mode — bot follows users between channels."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from bot.core.serverquery.events import (
    ClientJoinEvent,
    ClientLeaveEvent,
    ClientMovedEvent,
    SQEvent,
)

if TYPE_CHECKING:
    from bot.app import BotApplication

logger = logging.getLogger(__name__)


class FollowMode:
    """Bot follows users into monitored channels.

    When a user enters a monitored channel, the bot moves there.
    When the channel becomes empty, the bot returns to the default channel.
    Includes debounce and rate limiting to avoid rapid channel hopping.
    """

    def __init__(
        self,
        app: BotApplication,
        enabled: bool = False,
        channel_ids: list[int] | None = None,
        leave_when_empty: bool = True,
        debounce_seconds: float = 1.5,
        default_channel_id: int = 0,
    ) -> None:
        self._app = app
        self._enabled = enabled
        self._channel_ids = set(channel_ids or [])
        self._leave_when_empty = leave_when_empty
        self._debounce = debounce_seconds
        self._default_channel_id = default_channel_id

        # Track channel occupancy: channel_id → set of client IDs
        self._occupancy: dict[int, set[int]] = {cid: set() for cid in self._channel_ids}
        self._last_move_time: float = 0
        self._debounce_task: asyncio.Task | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        if value and not self._enabled:
            self._subscribe()
        self._enabled = value

    def subscribe(self) -> None:
        if self._enabled:
            self._subscribe()

    def _subscribe(self) -> None:
        d = self._app.sq.dispatcher
        d.subscribe("cliententerview", self._on_client_join)
        d.subscribe("clientleftview", self._on_client_leave)
        d.subscribe("clientmoved", self._on_client_moved)

    async def _on_client_join(self, event: SQEvent) -> None:
        join = ClientJoinEvent.from_event(event)
        if join.clid == self._app.sq.client_id:
            return
        # We don't know which channel they joined yet from this event alone
        # Use clientlist to check
        await self._reconcile()

    async def _on_client_leave(self, event: SQEvent) -> None:
        leave = ClientLeaveEvent.from_event(event)
        if leave.clid == self._app.sq.client_id:
            return
        # Remove from all occupancy sets
        for cid in self._occupancy:
            self._occupancy[cid].discard(leave.clid)
        await self._schedule_move()

    async def _on_client_moved(self, event: SQEvent) -> None:
        moved = ClientMovedEvent.from_event(event)
        if moved.clid == self._app.sq.client_id:
            return

        # Remove from old channels
        for cid in self._occupancy:
            self._occupancy[cid].discard(moved.clid)

        # Add to target channel if monitored
        target = moved.target_channel_id
        if target in self._channel_ids:
            self._occupancy.setdefault(target, set()).add(moved.clid)

        await self._schedule_move()

    async def _schedule_move(self) -> None:
        """Schedule a debounced move action."""
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()

        self._debounce_task = asyncio.create_task(self._debounced_move())

    async def _debounced_move(self) -> None:
        """Wait for debounce period, then execute move."""
        try:
            await asyncio.sleep(self._debounce)
            await self._execute_move()
        except asyncio.CancelledError:
            pass

    async def _execute_move(self) -> None:
        """Move the bot to the appropriate channel."""
        import time

        # Rate limiting: min 3s between moves
        now = time.time()
        if now - self._last_move_time < 3.0:
            return

        # Find a monitored channel with people
        target_channel = None
        for cid in self._channel_ids:
            if self._occupancy.get(cid):
                target_channel = cid
                break

        bot_cid = self._app.sq.client_id
        if not bot_cid:
            return

        if target_channel is not None:
            # Move to the occupied channel
            try:
                await self._app.sq.client_move(bot_cid, target_channel)
                self._last_move_time = time.time()
                logger.info("Follow: moved to channel %d", target_channel)
            except Exception:
                logger.exception("Failed to move bot to channel %d", target_channel)
        elif self._leave_when_empty and self._default_channel_id:
            # All monitored channels empty — return to default
            try:
                await self._app.sq.client_move(bot_cid, self._default_channel_id)
                self._last_move_time = time.time()
                logger.info("Follow: returned to default channel")
            except Exception:
                logger.exception("Failed to move bot to default channel")

    async def _reconcile(self) -> None:
        """Rebuild occupancy map from server state."""
        try:
            clients = await self._app.sq.client_list()
            bot_cid = self._app.sq.client_id

            for cid in self._channel_ids:
                self._occupancy[cid] = set()

            for client in clients:
                clid = int(client.get("clid", "0"))
                cid = int(client.get("cid", "0"))
                if clid == bot_cid:
                    continue
                if cid in self._channel_ids:
                    self._occupancy.setdefault(cid, set()).add(clid)

            await self._schedule_move()
        except Exception:
            logger.exception("Failed to reconcile channel occupancy")
