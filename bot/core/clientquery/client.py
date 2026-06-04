"""Async TeamSpeak 3 ClientQuery client.

Provides a persistent connection to the TS3 voice client's ClientQuery
API (port 25639) for receiving and sending text messages directly from
the voice client's perspective.

The protocol is compatible with ServerQuery (same escape/parse logic),
but uses different authentication (API key) and connection commands.
"""

from __future__ import annotations

import asyncio
import logging

from bot.core.serverquery.events import EventDispatcher
from bot.core.serverquery.protocol import (
    SQResponse,
    build_command,
    parse_response,
)

logger = logging.getLogger(__name__)


class ClientQueryError(Exception):
    """Raised when a ClientQuery command fails."""

    def __init__(self, error_id: int, error_msg: str) -> None:
        self.error_id = error_id
        self.error_msg = error_msg
        super().__init__(f"ClientQuery Error {error_id}: {error_msg}")


class AsyncClientQueryClient:
    """Async client for the TS3 ClientQuery protocol.

    Manages a persistent connection to the local TS3 voice client's
    ClientQuery API with:
    - Command queue for serialized execution
    - Background reader for response/event routing
    - Keepalive heartbeat
    - Auto-reconnection with exponential backoff
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 25639,
        api_key: str = "",
        server_address: str = "",
        server_port: int = 9987,
        nickname: str = "MusicBot",
        dispatcher: EventDispatcher | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._api_key = api_key
        self._server_address = server_address
        self._server_port = server_port
        self._nickname = nickname

        self.dispatcher = dispatcher or EventDispatcher()

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

        # Command queue
        self._cmd_queue: asyncio.Queue[tuple[str, asyncio.Future[SQResponse]]] = asyncio.Queue()
        self._reader_task: asyncio.Task | None = None
        self._writer_task: asyncio.Task | None = None
        self._keepalive_task: asyncio.Task | None = None

        self._connected = False
        self._client_id: int = 0
        self._channel_id: int = 0
        self._reconnect_delay = 1.0
        self._max_reconnect_delay = 60.0
        self._should_run = False

        # Reader state
        self._response_buffer: list[str] = []
        self._pending_future: asyncio.Future[SQResponse] | None = None

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def client_id(self) -> int:
        return self._client_id

    @property
    def channel_id(self) -> int:
        return self._channel_id

    # ── Connection lifecycle ──────────────────────

    async def start(self) -> None:
        """Start the client: connect, authenticate, register, start tasks."""
        self._should_run = True
        await self._connect_and_setup()
        self._start_background_tasks()

    async def stop(self) -> None:
        """Gracefully stop the client."""
        self._should_run = False

        for task in [self._keepalive_task, self._writer_task, self._reader_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        if self._writer and not self._writer.is_closing():
            try:
                self._writer.write(b"quit\n")
                await self._writer.drain()
            except Exception:
                pass

        self._close_connection()
        logger.info("ClientQuery client stopped")

    async def _connect_and_setup(self) -> None:
        """Connect to ClientQuery and perform authentication sequence."""
        logger.info("Connecting to ClientQuery at %s:%d", self._host, self._port)

        self._reader, self._writer = await asyncio.open_connection(self._host, self._port)

        # Read welcome banner
        banner = []
        for _ in range(3):
            try:
                line = await asyncio.wait_for(self._reader.readline(), timeout=5.0)
                decoded = line.decode("utf-8", errors="replace").strip()
                if decoded:
                    banner.append(decoded)
            except asyncio.TimeoutError:
                break
        logger.debug("ClientQuery banner: %s", banner)

        self._connected = True
        self._reconnect_delay = 1.0

        # Authenticate with API key
        await self._raw_send_and_wait(f"auth {self._api_key}")
        logger.info("ClientQuery authenticated")

        # Connect to TS3 server (may already be connected via entrypoint.sh)
        try:
            await self._raw_send_and_wait(
                f"connect address={self._server_address}:{self._server_port}"
                f" nickname={self._nickname}"
            )
            logger.info(
                "ClientQuery connecting to %s:%d as %s",
                self._server_address, self._server_port, self._nickname,
            )
        except ClientQueryError as e:
            # Error 257 = "already connected" — harmless, voice client
            # was already connected by entrypoint.sh
            if e.error_id == 257:
                logger.info("ClientQuery: voice client already connected")
            else:
                raise

        # Wait briefly for connection to establish
        await asyncio.sleep(2)

        # Get our client info
        resp = await self._raw_send_and_wait("whoami")
        if resp.ok and resp.data:
            self._client_id = int(resp.data[0].get("client_id", "0"))
            self._channel_id = int(resp.data[0].get("client_channel_id", "0"))
            logger.info(
                "ClientQuery client ID: %d, channel: %d",
                self._client_id, self._channel_id,
            )

        # Register for text message events
        await self._register_events()

        logger.info("ClientQuery client connected and ready")

    async def _register_events(self) -> None:
        """Register for text message events."""
        events = [
            "servernotifyregister event=textchannel",
            "servernotifyregister event=textprivate",
        ]
        for event_cmd in events:
            try:
                await self._raw_send_and_wait(event_cmd)
                logger.info("Registered ClientQuery event: %s", event_cmd)
            except ClientQueryError as e:
                logger.warning("Failed to register '%s': %s", event_cmd, e)

    def _start_background_tasks(self) -> None:
        """Start reader, writer, and keepalive background tasks."""
        self._reader_task = asyncio.create_task(self._reader_loop(), name="cq-reader")
        self._writer_task = asyncio.create_task(self._writer_loop(), name="cq-writer")
        self._keepalive_task = asyncio.create_task(self._keepalive_loop(), name="cq-keepalive")

    def _close_connection(self) -> None:
        """Close the TCP connection."""
        self._connected = False
        if self._writer:
            try:
                self._writer.close()
            except Exception:
                pass
            self._writer = None
        self._reader = None
        self._client_id = 0
        self._channel_id = 0

    async def _reconnect(self) -> None:
        """Attempt to reconnect with exponential backoff."""
        while self._should_run and not self._connected:
            logger.info("ClientQuery reconnecting in %.1fs...", self._reconnect_delay)
            await asyncio.sleep(self._reconnect_delay)

            try:
                self._close_connection()
                await self._connect_and_setup()
                self._start_background_tasks()
                logger.info("ClientQuery reconnected successfully")
                return
            except Exception:
                logger.exception("ClientQuery reconnection failed")
                self._reconnect_delay = min(self._reconnect_delay * 2, self._max_reconnect_delay)

    # ── Background tasks ──────────────────────────

    async def _reader_loop(self) -> None:
        """Read lines from ClientQuery, routing events and responses."""
        try:
            while self._should_run and self._reader:
                try:
                    line_bytes = await asyncio.wait_for(self._reader.readline(), timeout=300.0)
                except asyncio.TimeoutError:
                    continue

                if not line_bytes:
                    logger.warning("ClientQuery connection closed")
                    break

                line = line_bytes.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                if line.startswith("notify"):
                    self.dispatcher.parse_and_emit(line)
                elif line.startswith("error "):
                    self._response_buffer.append(line)
                    raw_response = "\n".join(self._response_buffer)
                    response = parse_response(raw_response)

                    if self._pending_future and not self._pending_future.done():
                        self._pending_future.set_result(response)

                    self._response_buffer.clear()
                    self._pending_future = None
                else:
                    self._response_buffer.append(line)

        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("ClientQuery reader loop error")

        self._connected = False
        if self._pending_future and not self._pending_future.done():
            self._pending_future.set_exception(ConnectionError("ClientQuery connection lost"))

        if self._should_run:
            asyncio.create_task(self._reconnect())

    async def _writer_loop(self) -> None:
        """Dequeue commands, send them, and set up pending future."""
        try:
            while self._should_run:
                cmd_str, future = await self._cmd_queue.get()

                if not self._connected or not self._writer:
                    future.set_exception(ConnectionError("Not connected"))
                    continue

                self._pending_future = asyncio.get_event_loop().create_future()

                def _forward(fut: asyncio.Future[SQResponse]) -> None:
                    try:
                        result = fut.result()
                        if not future.done():
                            future.set_result(result)
                    except Exception as e:
                        if not future.done():
                            future.set_exception(e)

                self._pending_future.add_done_callback(_forward)

                try:
                    self._writer.write(f"{cmd_str}\n".encode("utf-8"))
                    await self._writer.drain()
                except Exception as e:
                    if not future.done():
                        future.set_exception(e)

        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("ClientQuery writer loop error")

    async def _keepalive_loop(self) -> None:
        """Send periodic whoami to keep connection alive."""
        try:
            while self._should_run:
                await asyncio.sleep(240)
                if self._connected:
                    try:
                        await self.send("whoami")
                    except Exception:
                        logger.warning("ClientQuery keepalive failed")
        except asyncio.CancelledError:
            return

    # ── Low-level send/receive ────────────────────

    async def _raw_send_and_wait(self, command: str) -> SQResponse:
        """Send a raw command and wait for the response."""
        if not self._writer or not self._reader:
            raise ConnectionError("Not connected")

        self._writer.write(f"{command}\n".encode("utf-8"))
        await self._writer.drain()

        response_lines: list[str] = []
        while True:
            line_bytes = await asyncio.wait_for(self._reader.readline(), timeout=30.0)
            line = line_bytes.decode("utf-8", errors="replace").strip()

            if not line:
                continue

            if line.startswith("notify"):
                self.dispatcher.parse_and_emit(line)
                continue

            response_lines.append(line)

            if line.startswith("error "):
                break

        return parse_response("\n".join(response_lines))

    # ── Public API ────────────────────────────────

    async def send(self, command: str) -> SQResponse:
        """Send a command and wait for the response."""
        future: asyncio.Future[SQResponse] = asyncio.get_event_loop().create_future()
        await self._cmd_queue.put((command, future))
        response = await asyncio.wait_for(future, timeout=30.0)

        if not response.ok:
            raise ClientQueryError(response.error_id, response.error_msg)

        return response

    async def send_text_message(self, target_mode: int, target_id: int, message: str) -> None:
        """Send a text message.

        Args:
            target_mode: 1=private, 2=channel
            target_id: Client ID (private), channel ID (channel), or 0 (channel)
            message: Message text
        """
        cmd = build_command(
            "sendtextmessage",
            {"targetmode": target_mode, "target": target_id, "msg": message},
        )
        await self.send(cmd)

    async def reply_to_client(self, clid: int, message: str) -> None:
        """Send a private message to a specific client."""
        await self.send_text_message(1, clid, message)

    async def reply_to_channel(self, message: str) -> None:
        """Send a message to the voice client's current channel."""
        await self.send_text_message(2, 0, message)

    async def whoami(self) -> dict[str, str]:
        """Get current connection info."""
        resp = await self.send("whoami")
        return resp.data[0] if resp.data else {}

    @staticmethod
    def _escape(text: str) -> str:
        """Shortcut for protocol.ts3_escape."""
        from bot.core.serverquery.protocol import ts3_escape
        return ts3_escape(text)
