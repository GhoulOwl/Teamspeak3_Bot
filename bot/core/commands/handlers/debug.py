"""Debug/utility commands: !ping, !status."""

from __future__ import annotations

import time

from bot.core.commands.context import CommandContext
from bot.core.commands.registry import CommandRegistry

_start_time = time.time()


def register(registry: CommandRegistry) -> None:
    """Register debug commands."""

    @registry.command("ping", help="响应测试")
    async def handle_ping(ctx: CommandContext) -> None:
        await ctx.reply_same("pong!")

    @registry.command("status", help="Bot 状态信息")
    async def handle_status(ctx: CommandContext) -> None:
        uptime = int(time.time() - _start_time)
        hours, remainder = divmod(uptime, 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime_str = f"{hours}h {minutes}m {seconds}s"

        connected = "已连接" if ctx.sq.connected else "未连接"
        client_id = ctx.sq.client_id

        status = (
            f"[B]Bot 状态[/B]\n"
            f"  连接: {connected}\n"
            f"  ClientID: {client_id}\n"
            f"  运行时间: {uptime_str}"
        )
        await ctx.reply_same(status)
