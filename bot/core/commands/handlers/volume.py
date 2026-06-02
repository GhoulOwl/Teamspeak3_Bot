"""Volume command handlers: !volume, !vol."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from bot.core.commands.context import CommandContext
from bot.core.commands.registry import CommandRegistry

if TYPE_CHECKING:
    from bot.app import BotApplication

logger = logging.getLogger(__name__)


def register(registry: CommandRegistry, app: BotApplication) -> None:
    """Register volume commands."""

    @registry.command("volume", aliases=["vol", "v", "音量"], help="查看或调节音量 (0-100)")
    async def handle_volume(ctx: CommandContext) -> None:
        current = app.audio.volume

        if not ctx.args:
            # Show current volume
            await ctx.reply_same(f"当前音量: {current}%")
            return

        arg = ctx.args[0]

        # Relative adjustment: +10, -10
        if arg.startswith("+") or arg.startswith("-"):
            try:
                delta = int(arg)
            except ValueError:
                await ctx.reply_same("用法: !volume +10 或 !volume -10")
                return
            target = max(0, min(100, current + delta))
        else:
            # Absolute volume
            try:
                target = int(arg)
            except ValueError:
                await ctx.reply_same("用法: !volume <0-100> 或 !volume +10/-10")
                return

        target = max(0, min(100, target))
        await app.audio.set_volume(target)
        await ctx.reply_same(f"音量已调节: {target}%")
