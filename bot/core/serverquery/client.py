"""Async TeamSpeak 3 ServerQuery client."""

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


class ServerQueryError(Exception):
    """Raised when a ServerQuery command fails."""

    def __init__(self, error_id: int, error_msg: str) -> None:
        self.error_id = error_id
        self.error_msg = error_msg
        super().__init__(f"SQ Error {error_id}: {error_msg}")


class AsyncServerQueryClient:
    """Async telnet client for TeamSpeak 3 ServerQuery.

    Manages a persistent connection with:
    - Command queue for serialized execution
    - Background reader for response/event routing
    - Keepalive heartbeat
    - Auto-reconnection with exponential backoff
    """

    def __init__(
        self,
        host: str,
        port: int = 10011,
        username: str = "serveradmin",
        password: str = "",
        virtual_server_id: int = 1,
        nickname: str = "MusicBot",
        dispatcher: EventDispatcher | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._virtual_server_id = virtual_server_id
        self._nickname = nickname

        self.dispatcher = dispatcher or EventDispatcher()

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

        # Command queue: each entry is (command_string, Future for response)
        self._cmd_queue: asyncio.Queue[tuple[str, asyncio.Future[SQResponse]]] = asyncio.Queue()
        self._reader_task: asyncio.Task | None = None
        self._writer_task: asyncio.Task | None = None
        self._keepalive_task: asyncio.Task | None = None

        self._connected = False
        self._client_id: int = 0
        self._reconnect_delay = 1.0
        self._max_reconnect_delay = 60.0
        self._should_run = False

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def client_id(self) -> int:
        return self._client_id

    # ── Connection lifecycle ──────────────────────

    async def start(self) -> None:
        """Start the client: connect, login, register events, start background tasks."""
        self._should_run = True
        await self._connect_and_setup()
        self._start_background_tasks()

    async def stop(self) -> None:
        """Gracefully stop the client."""
        self._should_run = False

        # Cancel background tasks
        for task in [self._keepalive_task, self._writer_task, self._reader_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Try to send quit command
        if self._writer and not self._writer.is_closing():
            try:
                self._writer.write(b"quit\n")
                await self._writer.drain()
            except Exception:
                pass

        self._close_connection()
        logger.info("ServerQuery client stopped")

    async def _connect_and_setup(self) -> None:
        """Connect to ServerQuery and perform login sequence."""
        logger.info("Connecting to ServerQuery at %s:%d", self._host, self._port)

        self._reader, self._writer = await asyncio.open_connection(self._host, self._port)

        # Read welcome banner (2 lines: "TS3" and "Welcome...")
        banner = []
        for _ in range(2):
            line = await asyncio.wait_for(self._reader.readline(), timeout=10.0)
            banner.append(line.decode("utf-8", errors="replace").strip())
        logger.debug("ServerQuery banner: %s", banner)

        self._connected = True
        self._reconnect_delay = 1.0

        # Login
        await self._raw_send_and_wait(f"login {self._username} {self._password}")
        logger.info("Logged in as %s", self._username)

        # Use virtual server
        await self._raw_send_and_wait(f"use {self._virtual_server_id}")
        logger.info("Using virtual server %d", self._virtual_server_id)

        # Update nickname
        await self._raw_send_and_wait(
            f"clientupdate client_nickname={self._escape(self._nickname)}"
        )

        # Get our client ID
        resp = await self._raw_send_and_wait("whoami")
        if resp.ok and resp.data:
            self._client_id = int(resp.data[0].get("client_id", "0"))
            logger.info("ServerQuery client ID: %d", self._client_id)

        # Register for events
        await self._register_events()

        logger.info("ServerQuery client connected and ready")

    async def _register_events(self) -> None:
        """Register for server events."""
        events = [
            "servernotifyregister event=server",
            "servernotifyregister event=textserver",
            "servernotifyregister event=textchannel",
            "servernotifyregister event=textprivate",
        ]
        for event_cmd in events:
            try:
                await self._raw_send_and_wait(event_cmd)
            except ServerQueryError as e:
                logger.warning("Failed to register event '%s': %s", event_cmd, e)

    def _start_background_tasks(self) -> None:
        """Start reader, writer, and keepalive background tasks."""
        self._reader_task = asyncio.create_task(self._reader_loop(), name="sq-reader")
        self._writer_task = asyncio.create_task(self._writer_loop(), name="sq-writer")
        self._keepalive_task = asyncio.create_task(self._keepalive_loop(), name="sq-keepalive")

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

    async def _reconnect(self) -> None:
        """Attempt to reconnect with exponential backoff."""
        while self._should_run and not self._connected:
            logger.info("Reconnecting in %.1fs...", self._reconnect_delay)
            await asyncio.sleep(self._reconnect_delay)

            try:
                self._close_connection()
                await self._connect_and_setup()
                self._start_background_tasks()
                logger.info("Reconnected successfully")
                return
            except Exception:
                logger.exception("Reconnection failed")
                self._reconnect_delay = min(self._reconnect_delay * 2, self._max_reconnect_delay)

    # ── Background tasks ──────────────────────────

    async def _reader_loop(self) -> None:
        """Read lines from ServerQuery, routing events and responses."""
        response_buffer: list[str] = []
        pending_future: asyncio.Future[SQResponse] | None = None

        try:
            while self._should_run and self._reader:
                try:
                    line_bytes = await asyncio.wait_for(self._reader.readline(), timeout=300.0)
                except asyncio.TimeoutError:
                    # ServerQuery timeout (300s default), send keepalive
                    continue

                if not line_bytes:
                    # Connection closed
                    logger.warning("ServerQuery connection closed by server")
                    break

                line = line_bytes.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                # Route: notify* → event dispatcher, else → response buffer
                if line.startswith("notify"):
                    self.dispatcher.parse_and_emit(line)
                elif line.startswith("error "):
                    # End of response — parse and resolve
                    response_buffer.append(line)
                    raw_response = "\n".join(response_buffer)
                    response = parse_response(raw_response)

                    if pending_future and not pending_future.done():
                        pending_future.set_result(response)

                    response_buffer.clear()
                    pending_future = None
                else:
                    # Data line — accumulate
                    response_buffer.append(line)

        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Reader loop error")

        # Connection lost
        self._connected = False
        if pending_future and not pending_future.done():
            pending_future.set_exception(ConnectionError("ServerQuery connection lost"))

        # Trigger reconnect
        if self._should_run:
            asyncio.create_task(self._reconnect())

    async def _writer_loop(self) -> None:
        """Dequeue commands and send them to ServerQuery."""
        try:
            while self._should_run:
                cmd_str, future = await self._cmd_queue.get()

                if not self._connected or not self._writer:
                    future.set_exception(ConnectionError("Not connected"))
                    continue

                try:
                    raw = await self._raw_send_and_wait(cmd_str)
                    if not future.done():
                        future.set_result(raw)
                except Exception as e:
                    if not future.done():
                        future.set_exception(e)

        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Writer loop error")

    async def _keepalive_loop(self) -> None:
        """Send periodic whoami to keep connection alive."""
        try:
            while self._should_run:
                await asyncio.sleep(240)
                if self._connected:
                    try:
                        await self.send("whoami")
                    except Exception:
                        logger.warning("Keepalive failed")
        except asyncio.CancelledError:
            return

    # ── Low-level send/receive ────────────────────

    async def _raw_send_and_wait(self, command: str) -> SQResponse:
        """Send a raw command and wait for the response (bypasses command queue).

        Used during connection setup before background tasks are started.
        """
        if not self._writer or not self._reader:
            raise ConnectionError("Not connected")

        self._writer.write(f"{command}\n".encode("utf-8"))
        await self._writer.drain()

        # Read response lines until we get an error line
        response_lines: list[str] = []
        while True:
            line_bytes = await asyncio.wait_for(self._reader.readline(), timeout=30.0)
            line = line_bytes.decode("utf-8", errors="replace").strip()

            if not line:
                continue

            # Skip notify events during setup
            if line.startswith("notify"):
                self.dispatcher.parse_and_emit(line)
                continue

            response_lines.append(line)

            if line.startswith("error "):
                break

        return parse_response("\n".join(response_lines))

    # ── Public API ────────────────────────────────

    async def send(self, command: str) -> SQResponse:
        """Send a command and wait for the response.

        Commands are queued and executed sequentially.
        """
        future: asyncio.Future[SQResponse] = asyncio.get_event_loop().create_future()
        await self._cmd_queue.put((command, future))

        response = await asyncio.wait_for(future, timeout=30.0)

        if not response.ok:
            raise ServerQueryError(response.error_id, response.error_msg)

        return response

    async def send_text_message(self, target_mode: int, target_id: int, message: str) -> None:
        """Send a text message.

        Args:
            target_mode: 1=private, 2=channel, 3=server
            target_id: Client ID (private), channel ID (channel), or 0 (server)
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
        """Send a message to the bot's current channel."""
        # target_mode=2 with target=0 sends to the bot's own channel
        await self.send_text_message(2, 0, message)

    async def client_list(self) -> list[dict[str, str]]:
        """Get list of connected clients."""
        resp = await self.send("clientlist -uid")
        return resp.data

    async def channel_list(self) -> list[dict[str, str]]:
        """Get list of channels."""
        resp = await self.send("channellist")
        return resp.data

    async def client_info(self, clid: int) -> dict[str, str]:
        """Get detailed info about a client."""
        resp = await self.send(f"clientinfo clid={clid}")
        return resp.data[0] if resp.data else {}

    async def client_move(self, clid: int, channel_id: int) -> None:
        """Move a client to a channel."""
        await self.send(f"clientmove clid={clid} cid={channel_id}")

    async def server_group_add_client(self, sgid: int, cldbid: str) -> None:
        """Add a client to a server group."""
        await self.send(f"servergroupaddclient sgid={sgid} cldbid={cldbid}")

    async def server_group_del_client(self, sgid: int, cldbid: str) -> None:
        """Remove a client from a server group."""
        await self.send(f"servergroupdelclient sgid={sgid} cldbid={cldbid}")

    async def whoami(self) -> dict[str, str]:
        """Get current connection info."""
        resp = await self.send("whoami")
        return resp.data[0] if resp.data else {}

    async def poke(self, clid: int, message: str) -> None:
        """Poke a client."""
        await self.send(f"clientpoke clid={clid} msg={self._escape(message)}")

    @staticmethod
    def _escape(text: str) -> str:
        """Shortcut for protocol.ts3_escape."""
        from bot.core.serverquery.protocol import ts3_escape
        return ts3_escape(text)
