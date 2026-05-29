"""Command registry with decorator-based registration."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

from bot.core.commands.context import CommandContext

logger = logging.getLogger(__name__)

# Type for command handler functions
CommandHandler = Callable[[CommandContext], Coroutine[Any, Any, None]]


@dataclass
class CommandInfo:
    """Metadata for a registered command."""

    name: str
    handler: CommandHandler
    aliases: list[str] = field(default_factory=list)
    help_text: str = ""
    admin_only: bool = False


class CommandRegistry:
    """Registry for bot commands with decorator-based registration."""

    def __init__(self) -> None:
        self._commands: dict[str, CommandInfo] = {}
        self._alias_map: dict[str, str] = {}

    def command(
        self,
        name: str,
        aliases: list[str] | None = None,
        help: str = "",
        admin_only: bool = False,
    ) -> Callable[[CommandHandler], CommandHandler]:
        """Decorator to register a command handler.

        Usage:
            @registry.command("play", aliases=["p"], help="Play a song")
            async def handle_play(ctx: CommandContext):
                ...
        """

        def decorator(func: CommandHandler) -> CommandHandler:
            cmd_info = CommandInfo(
                name=name,
                handler=func,
                aliases=aliases or [],
                help_text=help,
                admin_only=admin_only,
            )
            self._commands[name] = cmd_info

            for alias in cmd_info.aliases:
                self._alias_map[alias] = name

            logger.debug("Registered command: %s (aliases: %s)", name, aliases)
            return func

        return decorator

    def get(self, name: str) -> CommandInfo | None:
        """Get command info by name or alias."""
        # Direct match
        if name in self._commands:
            return self._commands[name]

        # Alias match
        if name in self._alias_map:
            return self._commands.get(self._alias_map[name])

        return None

    def get_all(self) -> list[CommandInfo]:
        """Get all registered commands."""
        return list(self._commands.values())

    def format_help(self, prefix: str) -> str:
        """Format all commands into a help message."""
        lines = ["[B]可用命令:[/B]"]

        for cmd in sorted(self._commands.values(), key=lambda c: c.name):
            aliases = f" ({', '.join(prefix + a for a in cmd.aliases)})" if cmd.aliases else ""
            admin = " [管理员]" if cmd.admin_only else ""
            lines.append(f"  {prefix}{cmd.name}{aliases} - {cmd.help_text}{admin}")

        return "\n".join(lines)
