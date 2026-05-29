"""AI chat command handlers: !chat, !persona, !clear."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from bot.core.commands.context import CommandContext
from bot.core.commands.registry import CommandRegistry
from bot.services.chat.personas import list_personas

if TYPE_CHECKING:
    from bot.app import BotApplication

logger = logging.getLogger(__name__)


def register(registry: CommandRegistry, app: BotApplication) -> None:
    """Register chat commands."""

    @registry.command("chat", aliases=["ask", "ai"], help="和 AI 聊天")
    async def handle_chat(ctx: CommandContext) -> None:
        if not ctx.raw_args:
            await ctx.reply_same("用法: !chat <你的消息>")
            return

        # Get channel ID for context isolation
        try:
            info = await app.sq.whoami()
            channel_id = int(info.get("client_channel_id", "0"))
        except Exception:
            channel_id = 0

        reply = await app.chat_service.chat(
            message=ctx.raw_args,
            channel_id=channel_id,
            user_uid=ctx.invoker_uid,
            username=ctx.invoker_name,
        )
        await ctx.reply_same(reply)

    @registry.command("persona", aliases=["personality"], help="切换 AI 人设")
    async def handle_persona(ctx: CommandContext) -> None:
        if not ctx.args:
            # List available personas
            personas = list_personas()
            lines = ["[B]可用 AI 人设:[/B]"]
            current = app.chat_service.get_persona_key(0)
            for key, name, desc in personas:
                marker = " (当前)" if key == current else ""
                lines.append(f"  {key} - {name}: {desc}{marker}")
            lines.append("\n用法: !persona <人设名>")
            await ctx.reply_same("\n".join(lines))
            return

        persona_key = ctx.args[0]

        try:
            info = await app.sq.whoami()
            channel_id = int(info.get("client_channel_id", "0"))
        except Exception:
            channel_id = 0

        if app.chat_service.set_persona(channel_id, persona_key):
            from bot.services.chat.personas import get_persona
            persona = get_persona(persona_key)
            await ctx.reply_same(f"已切换人设: {persona['name']} - {persona['description']}")
        else:
            await ctx.reply_same(f"未知人设: {persona_key}，输入 !persona 查看可用列表")

    @registry.command("clearctx", help="清除 AI 聊天上下文")
    async def handle_clear_ctx(ctx: CommandContext) -> None:
        try:
            info = await app.sq.whoami()
            channel_id = int(info.get("client_channel_id", "0"))
        except Exception:
            channel_id = 0

        app.chat_service.clear_context(channel_id, ctx.invoker_uid)
        await ctx.reply_same("已清除聊天上下文")
