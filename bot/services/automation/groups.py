"""Auto group assigner — assigns server groups to new members."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from bot.core.serverquery.events import ClientJoinEvent, SQEvent

if TYPE_CHECKING:
    from bot.app import BotApplication

logger = logging.getLogger(__name__)


class GroupAssigner:
    """Automatically assigns server groups when users join."""

    def __init__(
        self,
        app: BotApplication,
        enabled: bool = False,
        group_ids: list[int] | None = None,
        delay_seconds: int = 3,
    ) -> None:
        self._app = app
        self._enabled = enabled
        self._group_ids = group_ids or []
        self._delay = delay_seconds

    def subscribe(self) -> None:
        if self._enabled and self._group_ids:
            self._app.sq.dispatcher.subscribe("cliententerview", self._on_join)

    async def _on_join(self, event: SQEvent) -> None:
        join_event = ClientJoinEvent.from_event(event)

        # Skip bot itself
        if join_event.clid == self._app.sq.client_id:
            return

        await asyncio.sleep(self._delay)

        # Get client database ID for group assignment
        cldbid = join_event.cldbid
        if not cldbid:
            try:
                info = await self._app.sq.client_info(join_event.clid)
                cldbid = info.get("client_database_id", "")
            except Exception:
                logger.warning("Could not get cldbid for client %d", join_event.clid)
                return

        if not cldbid:
            return

        for sgid in self._group_ids:
            try:
                await self._app.sq.server_group_add_client(sgid, cldbid)
                logger.info("Assigned group %d to %s", sgid, join_event.nickname)
            except Exception as e:
                # Error 256 = already in group, which is fine
                if "256" not in str(e):
                    logger.warning("Failed to assign group %d: %s", sgid, e)
