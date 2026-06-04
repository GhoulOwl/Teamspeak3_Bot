"""Admin command handlers: !welcome, !follow, !remind, !help.

Registration is split into three functions for the layered routing system:

* ``register_private()`` — admin-only commands (!follow, !welcome) that
  are only accessible via ServerQuery private messages.
* ``register_channel()`` — utility commands (!remind) available in the
  channel context.
* ``register_help()`` — the !help command, registered in both contexts,
  which uses the CommandRouter for a merged command listing.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from bot.core.commands.context import CommandContext
from bot.core.commands.registry import CommandRegistry

if TYPE_CHECKING:
    from bot.app import BotApplication
    from bot.core.commands.router import CommandRouter

logger = logging.getLogger(__name__)


def register_private(registry: CommandRegistry, app: BotApplication) -> None:
    """Register admin-only commands into the private (ServerQuery) registry.

    These commands are only accessible via private messages (target_mode=1).
    """

    @registry.command("follow", aliases=["跟随"], help="开启/关闭房间跟随模式", admin_only=True)
    async def handle_follow(ctx: CommandContext) -> None:
        if not ctx.args:
            state = "开启" if app.follow_mode.enabled else "关闭"
            await ctx.reply_same(f"跟随模式: {state}")
            return

        arg = ctx.args[0].lower()
        if arg in ("on", "开", "true", "1"):
            app.follow_mode.enabled = True
            await app.follow_mode._reconcile()
            await ctx.reply_same("跟随模式已开启")
        elif arg in ("off", "关", "false", "0"):
            app.follow_mode.enabled = False
            await ctx.reply_same("跟随模式已关闭")
        else:
            await ctx.reply_same("用法: !follow on/off")

    @registry.command("welcome", aliases=["欢迎"], help="设置欢迎消息", admin_only=True)
    async def handle_welcome(ctx: CommandContext) -> None:
        if not ctx.raw_args:
            await ctx.reply_same("用法: !welcome <欢迎消息模板>\n支持占位符: {username}, {uid}")
            return

        app.welcome_service._message = ctx.raw_args
        await ctx.reply_same(f"欢迎消息已更新: {ctx.raw_args}")


def register_channel(registry: CommandRegistry, app: BotApplication) -> None:
    """Register utility commands into the channel registry."""

    @registry.command("remind", aliases=["提醒"], help="设置定时提醒: !remind <分钟数> <消息>")
    async def handle_remind(ctx: CommandContext) -> None:
        if len(ctx.args) < 2:
            await ctx.reply_same("用法: !remind <分钟数> <消息>")
            return

        try:
            minutes = int(ctx.args[0])
        except ValueError:
            await ctx.reply_same("请输入分钟数，例如: !remind 30 该休息了")
            return

        message = " ".join(ctx.args[1:])

        # Use a simple delay via asyncio instead of cron for dynamic reminders
        import asyncio

        async def delayed_remind():
            await asyncio.sleep(minutes * 60)
            await app.sq.reply_to_channel(f"提醒: {message}")

        asyncio.create_task(delayed_remind())
        await ctx.reply_same(f"已设置 {minutes} 分钟后的提醒: {message}")


def register_help(router: CommandRouter, prefix: str) -> None:
    """Register !help in both contexts with merged command listing."""

    async def handle_help(ctx: CommandContext) -> None:
        help_text = router.format_merged_help(prefix)
        await ctx.reply_same(help_text)

    router.channel_registry.command("help", aliases=["h", "帮助"], help="显示帮助信息")(handle_help)
    router.private_registry.command("help", aliases=["h", "帮助"], help="显示帮助信息")(handle_help)
