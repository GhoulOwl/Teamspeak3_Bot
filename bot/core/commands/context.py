"""Command context — wraps invocation metadata and provides reply helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from bot.core.commands.parser import ParsedCommand

if TYPE_CHECKING:
    from bot.core.serverquery.client import AsyncServerQueryClient

# TS3 ServerQuery sendtextmessage msg parameter limit is 8192 bytes.
# Use a slightly lower value to leave headroom for command framing.
_TS3_MAX_MSG_BYTES = 8000


class CommandContext:
    """Context for a command invocation.

    Provides metadata about who invoked the command and convenience
    methods for sending replies.
    """

    def __init__(self, cmd: ParsedCommand, sq_client: AsyncServerQueryClient) -> None:
        self.cmd = cmd
        self.sq = sq_client

    @property
    def name(self) -> str:
        return self.cmd.name

    @property
    def args(self) -> list[str]:
        return self.cmd.args

    @property
    def raw_args(self) -> str:
        return self.cmd.raw_args

    @property
    def invoker_clid(self) -> int:
        return self.cmd.invoker_clid

    @property
    def invoker_uid(self) -> str:
        return self.cmd.invoker_uid

    @property
    def invoker_name(self) -> str:
        return self.cmd.invoker_name

    @property
    def target_mode(self) -> int:
        return self.cmd.target_mode

    async def reply(self, message: str) -> None:
        """Reply to the invoker (private message)."""
        await self.sq.reply_to_client(self.invoker_clid, message)

    async def reply_channel(self, message: str) -> None:
        """Send a message to the current channel."""
        await self.sq.reply_to_channel(message)

    async def reply_same(self, message: str) -> None:
        """Reply in the same context the command was sent in.

        Private commands get private replies, channel/server commands
        get channel replies.
        """
        if self.target_mode == 1:
            await self.reply(message)
        else:
            await self.reply_channel(message)

    async def reply_long(self, message: str) -> None:
        """Reply with automatic message splitting for long text.

        If *message* exceeds the TS3 single-message byte limit it is
        split into multiple messages at newline boundaries so that no
        content is lost or rejected by the server.
        """
        chunks = _split_message(message, _TS3_MAX_MSG_BYTES)
        for chunk in chunks:
            await self.reply_same(chunk)


def _split_message(text: str, max_bytes: int) -> list[str]:
    """Split *text* into chunks that each fit within *max_bytes* (UTF-8).

    Splitting prefers newline boundaries; if a single line is itself
    longer than *max_bytes* it is split at character boundaries as a
    fallback.
    """
    if len(text.encode("utf-8")) <= max_bytes:
        return [text]

    chunks: list[str] = []
    lines = text.split("\n")
    current: list[str] = []
    current_len = 0

    for line in lines:
        line_len = len((line + "\n").encode("utf-8"))
        if current_len + line_len > max_bytes and current:
            chunks.append("\n".join(current))
            current = []
            current_len = 0

        # Handle a single line that exceeds max_bytes on its own
        if len((line + "\n").encode("utf-8")) > max_bytes:
            # Flush any pending lines first
            if current:
                chunks.append("\n".join(current))
                current = []
                current_len = 0
            # Split the long line by characters
            buf = ""
            for ch in line:
                test = buf + ch
                if len(test.encode("utf-8")) >= max_bytes:
                    chunks.append(buf)
                    buf = ch
                else:
                    buf = test
            if buf:
                current.append(buf)
                current_len = len(buf.encode("utf-8"))
        else:
            current.append(line)
            current_len += line_len

    if current:
        chunks.append("\n".join(current))

    return chunks
