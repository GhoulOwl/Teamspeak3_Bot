"""Voice client channel tracker.

Keeps the ServerQuery client in the same channel as the TS3 voice client
so that channel text messages (commands) are received regardless of which
channel the voice client is in.

Without this, ``servernotifyregister event=textchannel`` only delivers
messages from the ServerQuery client's own channel — which defaults to
channel 0 (the server's default channel).

**Important**: TS3 ``clientmoved`` events require per-channel registration
via ``servernotifyregister event=channel id={cid}``.  The global
``event=server`` registration does NOT cover channel move events.
This tracker re-registers channel events every time the voice client
(or the ServerQuery client following it) changes channel.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from bot.core.serverquery.events import (
    ClientMovedEvent,
    SQEvent,
)

if TYPE_CHECKING:
    from bot.app import BotApplication

logger = logging.getLogger(__name__)


class VoiceClientTracker:
    """Tracks the TS3 voice client and moves the ServerQuery client to follow.

    The TS3 voice client (the actual audio bot) and the ServerQuery client
    are two separate connections with different client IDs.  ServerQuery
    only receives ``textchannel`` events from its *own* channel, so it
    must be co-located with the voice client to receive user commands.
    """

    def __init__(
        self,
        app: BotApplication,
        voice_nickname: str = "MusicBot",
    ) -> None:
        self._app = app
        self._voice_nickname = voice_nickname

        # Tracked voice client identifiers
        self._voice_clid: int = 0
        self._voice_cldbid: str = ""
        self._voice_channel_id: int = 0

        # Channel we've registered events for (to avoid duplicate registration)
        self._registered_channel_id: int = -1

        # Debounce to avoid rapid moves
        self._move_task: asyncio.Task | None = None
        self._sync_task: asyncio.Task | None = None

    def subscribe(self) -> None:
        """Register event handlers."""
        d = self._app.sq.dispatcher
        d.subscribe("cliententerview", self._on_client_enter)
        d.subscribe("clientmoved", self._on_client_moved)

    async def initial_sync(self) -> None:
        """Find the voice client on startup, move ServerQuery to its channel,
        and start the periodic sync loop."""
        await asyncio.sleep(3)  # let the voice client connect first
        try:
            await self._find_and_follow_voice_client()
        except Exception:
            logger.exception("Voice client initial sync failed")

        # Start periodic sync as fallback (in case event-based tracking misses)
        self._sync_task = asyncio.create_task(
            self._periodic_sync_loop(), name="voice-tracker-sync"
        )

    async def _periodic_sync_loop(self) -> None:
        """Periodically check voice client channel and sync if needed."""
        try:
            while True:
                await asyncio.sleep(30)
                try:
                    await self._check_and_sync()
                except Exception:
                    logger.debug("Periodic sync failed", exc_info=True)
        except asyncio.CancelledError:
            pass

    async def _check_and_sync(self) -> None:
        """Check if voice client is in the same channel as ServerQuery."""
        try:
            clients = await self._app.sq.client_list()
        except Exception:
            return

        sq_clid = self._app.sq.client_id
        sq_channel_id = 0
        voice_channel_id = 0

        for client in clients:
            clid = int(client.get("clid", "0"))
            cid = int(client.get("cid", "0"))
            nickname = client.get("client_nickname", "")

            if clid == sq_clid:
                sq_channel_id = cid
            elif nickname == self._voice_nickname and clid != sq_clid:
                voice_channel_id = cid
                self._voice_clid = clid
                self._voice_cldbid = client.get("client_database_id", "")

        if voice_channel_id and voice_channel_id != sq_channel_id:
            logger.info(
                "Periodic sync: voice client in channel %d, ServerQuery in %d — moving",
                voice_channel_id, sq_channel_id,
            )
            self._voice_channel_id = voice_channel_id
            await self._move_to_channel(voice_channel_id)
        elif voice_channel_id:
            self._voice_channel_id = voice_channel_id
            # Ensure channel events are registered
            await self._register_channel_events(voice_channel_id)

    async def _find_and_follow_voice_client(self) -> None:
        """Search client list for the voice client and move to its channel."""
        try:
            clients = await self._app.sq.client_list()
        except Exception:
            logger.warning("Failed to fetch client list for voice tracking")
            return

        sq_clid = self._app.sq.client_id

        for client in clients:
            clid = int(client.get("clid", "0"))
            nickname = client.get("client_nickname", "")

            if clid == sq_clid:
                continue  # skip ourselves

            if nickname == self._voice_nickname:
                cid = int(client.get("cid", "0"))
                cldbid = client.get("client_database_id", "")
                self._voice_clid = clid
                self._voice_cldbid = cldbid
                self._voice_channel_id = cid
                logger.info(
                    "Found voice client '%s' (clid=%d, cldbid=%s) in channel %d",
                    nickname, clid, cldbid, cid,
                )
                await self._move_to_channel(cid)
                return

        logger.warning(
            "Voice client '%s' not found in client list (%d clients)",
            self._voice_nickname, len(clients),
        )

    async def _on_client_enter(self, event: SQEvent) -> None:
        """Handle client joining the server."""
        sq_clid = self._app.sq.client_id
        clid = int(event.data.get("clid", "0"))
        reason = int(event.data.get("reasonid", "0"))

        if clid == sq_clid:
            return

        # reason=0 means initial connect to server (not channel switch)
        if reason == 0:
            nickname = event.data.get("client_nickname", "")
            if nickname == self._voice_nickname:
                cid = int(event.data.get("ctid", "0"))
                cldbid = event.data.get("client_database_id", "")
                self._voice_clid = clid
                self._voice_cldbid = cldbid
                self._voice_channel_id = cid
                logger.info(
                    "Voice client connected (clid=%d, channel=%d)",
                    clid, cid,
                )
                await self._move_to_channel(cid)

    async def _on_client_moved(self, event: SQEvent) -> None:
        """Handle client moving between channels."""
        moved = ClientMovedEvent.from_event(event)
        sq_clid = self._app.sq.client_id

        if moved.clid == sq_clid:
            return  # ignore our own moves

        # Check if the voice client moved (by clid or cldbid)
        is_voice = (
            moved.clid == self._voice_clid
            or (self._voice_cldbid and moved.cldbid == self._voice_cldbid)
        )
        if not is_voice:
            return

        self._voice_clid = moved.clid
        self._voice_channel_id = moved.target_channel_id
        logger.info(
            "Voice client moved to channel %d",
            moved.target_channel_id,
        )
        await self._move_to_channel(moved.target_channel_id)

    async def _move_to_channel(self, channel_id: int) -> None:
        """Move the ServerQuery client to the given channel."""
        if channel_id == 0:
            return  # channel 0 is not a valid target

        sq_clid = self._app.sq.client_id
        if not sq_clid:
            return

        # Cancel any pending move to avoid conflicts
        if self._move_task and not self._move_task.done():
            self._move_task.cancel()

        self._move_task = asyncio.create_task(
            self._do_move(sq_clid, channel_id)
        )

    async def _do_move(self, clid: int, channel_id: int) -> None:
        """Execute the move with a small debounce, then re-register channel events."""
        try:
            await asyncio.sleep(0.5)
            await self._app.sq.client_move(clid, channel_id)
            logger.info("ServerQuery moved to channel %d", channel_id)
        except asyncio.CancelledError:
            return
        except Exception as e:
            # Error 770 = "already member of channel" — harmless
            if "770" in str(e):
                logger.debug("ServerQuery already in channel %d", channel_id)
            else:
                logger.exception(
                    "Failed to move ServerQuery to channel %d", channel_id,
                )
                return

        # After moving (or if already there), register channel-level events.
        # This is CRITICAL: clientmoved events require per-channel registration.
        await self._register_channel_events(channel_id)

    async def _register_channel_events(self, channel_id: int) -> None:
        """Register for channel-level events (clientmoved, etc.).

        TS3 ServerQuery requires ``event=channel id={cid}`` to receive
        ``clientmoved`` notifications.  Without this, the tracker cannot
        detect when the voice client is moved to another channel.
        """
        if channel_id == self._registered_channel_id:
            return  # already registered for this channel

        try:
            await self._app.sq.send(
                f"servernotifyregister event=channel id={channel_id}"
            )
            self._registered_channel_id = channel_id
            logger.info(
                "Registered channel events for channel %d", channel_id,
            )
        except Exception:
            logger.debug(
                "Failed to register channel events for %d (may already be registered)",
                channel_id,
            )
                logger.info(
                    "Voice client connected (clid=%d, channel=%d)",
                    clid, cid,
                )
                await self._move_to_channel(cid)

    async def _on_client_moved(self, event: SQEvent) -> None:
        """Handle client moving between channels."""
        moved = ClientMovedEvent.from_event(event)
        sq_clid = self._app.sq.client_id

        if moved.clid == sq_clid:
            return  # ignore our own moves

        # Check if the voice client moved (by clid or cldbid)
        is_voice = (
            moved.clid == self._voice_clid
            or (self._voice_cldbid and moved.cldbid == self._voice_cldbid)
        )
        if not is_voice:
            return

        self._voice_clid = moved.clid
        self._voice_channel_id = moved.target_channel_id
        logger.info(
            "Voice client moved to channel %d",
            moved.target_channel_id,
        )
        await self._move_to_channel(moved.target_channel_id)

    async def _move_to_channel(self, channel_id: int) -> None:
        """Move the ServerQuery client to the given channel."""
        if channel_id == 0:
            return  # channel 0 is not a valid target

        sq_clid = self._app.sq.client_id
        if not sq_clid:
            return

        # Cancel any pending move to avoid conflicts
        if self._move_task and not self._move_task.done():
            self._move_task.cancel()

        self._move_task = asyncio.create_task(
            self._do_move(sq_clid, channel_id)
        )

    async def _do_move(self, clid: int, channel_id: int) -> None:
        """Execute the move with a small debounce, then re-register channel events."""
        try:
            await asyncio.sleep(0.5)
            await self._app.sq.client_move(clid, channel_id)
            logger.info("ServerQuery moved to channel %d", channel_id)
        except asyncio.CancelledError:
            return
        except Exception as e:
            # Error 770 = "already member of channel" — harmless
            if "770" in str(e):
                logger.debug("ServerQuery already in channel %d", channel_id)
            else:
                logger.exception(
                    "Failed to move ServerQuery to channel %d", channel_id,
                )
                return

        # After moving (or if already there), register channel-level events.
        # This is CRITICAL: clientmoved events require per-channel registration.
        await self._register_channel_events(channel_id)

    async def _register_channel_events(self, channel_id: int) -> None:
        """Register for channel-level events (clientmoved, etc.).

        TS3 ServerQuery requires ``event=channel id={cid}`` to receive
        ``clientmoved`` notifications.  Without this, the tracker cannot
        detect when the voice client is moved to another channel.
        """
        if channel_id == self._registered_channel_id:
            return  # already registered for this channel

        try:
            await self._app.sq.send(
                f"servernotifyregister event=channel id={channel_id}"
            )
            self._registered_channel_id = channel_id
            logger.info(
                "Registered channel events for channel %d", channel_id,
            )
        except Exception:
            logger.debug(
                "Failed to register channel events for %d (may already be registered)",
                channel_id,
            )
