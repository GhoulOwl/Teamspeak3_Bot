"""Command context — wraps invocation metadata and provides reply helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from bot.core.commands.parser import ParsedCommand

if TYPE_CHECKING:
    from bot.core.serverquery.client import AsyncServerQueryClient


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
