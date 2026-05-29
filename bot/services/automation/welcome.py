"""Auto welcome service — greets new members on join."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from bot.core.serverquery.events import ClientJoinEvent, SQEvent

if TYPE_CHECKING:
    from bot.app import BotApplication

logger = logging.getLogger(__name__)


class WelcomeService:
    """Sends welcome messages when new users join the server."""

    def __init__(
        self,
        app: BotApplication,
        enabled: bool = True,
        message: str = "欢迎 {username}！",
        delay_seconds: int = 2,
        mode: str = "channel",
    ) -> None:
        self._app = app
        self._enabled = enabled
        self._message = message
        self._delay = delay_seconds
        self._mode = mode  # "channel" or "private"

    def subscribe(self) -> None:
        """Subscribe to client join events."""
        if self._enabled:
            self._app.sq.dispatcher.subscribe("cliententerview", self._on_join)

    async def _on_join(self, event: SQEvent) -> None:
        join_event = ClientJoinEvent.from_event(event)

        # Skip bot itself
        if join_event.clid == self._app.sq.client_id:
            return

        # Delay to avoid spam
        await asyncio.sleep(self._delay)

        # Verify user is still connected
        try:
            clients = await self._app.sq.client_list()
            online_ids = {int(c.get("clid", "0")) for c in clients}
            if join_event.clid not in online_ids:
                return
        except Exception:
            pass

        # Format message
        msg = self._message.format(
            username=join_event.nickname,
            uid=join_event.unique_id,
        )

        try:
            if self._mode == "private":
                await self._app.sq.reply_to_client(join_event.clid, msg)
            else:
                await self._app.sq.reply_to_channel(msg)
        except Exception:
            logger.exception("Failed to send welcome message")
