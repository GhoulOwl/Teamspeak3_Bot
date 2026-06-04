"""Voice command context — wraps ClientQuery client for voice client commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

from bot.core.commands.parser import ParsedCommand

if TYPE_CHECKING:
    from bot.core.clientquery.client import AsyncClientQueryClient

# Reuse the same message splitting logic
from bot.core.commands.context import _split_message

_TS3_MAX_MSG_BYTES = 8000


class VoiceCommandContext:
    """Context for commands received via the voice client (ClientQuery).

    Provides the same reply interface as CommandContext but sends
    messages through the ClientQuery connection instead of ServerQuery.
    """

    def __init__(self, cmd: ParsedCommand, cq_client: AsyncClientQueryClient) -> None:
        self.cmd = cmd
        self.cq = cq_client

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
        """Reply to the invoker (private message via ClientQuery)."""
        await self.cq.reply_to_client(self.invoker_clid, message)

    async def reply_channel(self, message: str) -> None:
        """Send a message to the current channel via ClientQuery."""
        await self.cq.reply_to_channel(message)

    async def reply_same(self, message: str) -> None:
        """Reply in the same context the command was sent in."""
        if self.target_mode == 1:
            await self.reply(message)
        else:
            await self.reply_channel(message)

    async def reply_long(self, message: str) -> None:
        """Reply with automatic message splitting for long text."""
        chunks = _split_message(message, _TS3_MAX_MSG_BYTES)
        for chunk in chunks:
            await self.reply_same(chunk)
