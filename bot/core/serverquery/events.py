"""Event dispatcher for ServerQuery notifications."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

from bot.core.serverquery.protocol import parse_record

logger = logging.getLogger(__name__)

# Type alias for event handler callbacks
EventHandler = Callable[..., Coroutine[Any, Any, None]]


@dataclass
class SQEvent:
    """Base ServerQuery event."""

    event_type: str
    data: dict[str, str] = field(default_factory=dict)


@dataclass
class ClientJoinEvent:
    """Client joined the server."""

    clid: int
    cldbid: str
    unique_id: str
    nickname: str

    @classmethod
    def from_event(cls, event: SQEvent) -> ClientJoinEvent:
        return cls(
            clid=int(event.data.get("clid", "0")),
            cldbid=event.data.get("client_database_id", ""),
            unique_id=event.data.get("client_unique_identifier", ""),
            nickname=event.data.get("client_nickname", ""),
        )


@dataclass
class ClientLeaveEvent:
    """Client left the server."""

    clid: int
    cldbid: str
    reason: int

    @classmethod
    def from_event(cls, event: SQEvent) -> ClientLeaveEvent:
        return cls(
            clid=int(event.data.get("clid", "0")),
            cldbid=event.data.get("client_database_id", ""),
            reason=int(event.data.get("reasonid", "0")),
        )


@dataclass
class TextMessageEvent:
    """Text message received."""

    invoker_clid: int
    invoker_uid: str
    invoker_name: str
    message: str
    target_mode: int

    @classmethod
    def from_event(cls, event: SQEvent) -> TextMessageEvent:
        return cls(
            invoker_clid=int(event.data.get("invokerid", "0")),
            invoker_uid=event.data.get("invokeruid", ""),
            invoker_name=event.data.get("invokername", ""),
            message=event.data.get("msg", ""),
            target_mode=int(event.data.get("targetmode", "0")),
        )


@dataclass
class ClientMovedEvent:
    """Client moved to a different channel."""

    clid: int
    cldbid: str
    target_channel_id: int
    reason: int

    @classmethod
    def from_event(cls, event: SQEvent) -> ClientMovedEvent:
        return cls(
            clid=int(event.data.get("clid", "0")),
            cldbid=event.data.get("client_database_id", ""),
            target_channel_id=int(event.data.get("ctid", "0")),
            reason=int(event.data.get("reasonid", "0")),
        )


class EventDispatcher:
    """Lightweight pub/sub event dispatcher for ServerQuery events."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = {}

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """Subscribe to an event type."""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)
        logger.debug("Subscribed %s to event '%s'", handler.__name__, event_type)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        """Unsubscribe from an event type."""
        if event_type in self._handlers:
            self._handlers[event_type] = [h for h in self._handlers[event_type] if h is not handler]

    async def emit(self, event: SQEvent) -> None:
        """Emit an event to all subscribed handlers."""
        handlers = self._handlers.get(event.event_type, [])
        if not handlers:
            return

        tasks = []
        for handler in handlers:
            tasks.append(self._safe_call(handler, event))

        await asyncio.gather(*tasks)

    async def _safe_call(self, handler: EventHandler, event: SQEvent) -> None:
        """Call a handler, catching and logging any errors."""
        try:
            await handler(event)
        except Exception:
            logger.exception("Error in event handler %s for %s", handler.__name__, event.event_type)

    def parse_and_emit(self, line: str) -> asyncio.Task | None:
        """Parse a notify line and emit the event. Returns the emit task or None."""
        if not line.startswith("notify"):
            return None

        # Format: notify<event_type> <key=value key=value ...>
        parts = line.split(" ", 1)
        event_type = parts[0].removeprefix("notify")
        data_str = parts[1] if len(parts) > 1 else ""

        event = SQEvent(
            event_type=event_type,
            data=parse_record(data_str) if data_str else {},
        )

        logger.debug("Event: %s data=%s", event_type, event.data)
        return asyncio.create_task(self.emit(event))
