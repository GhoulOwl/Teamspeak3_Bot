"""Layered command router — routes commands based on message source.

Two independent ``CommandRegistry`` instances are maintained:

* **channel_registry** — commands received via channel text messages
  (``target_mode=2``).  This is the "voice client" perspective: everyday
  interaction commands available to all users.

* **private_registry** — commands received via private text messages
  (``target_mode=1``).  This is the "ServerQuery" perspective: admin /
  management commands that require elevated privileges.

Commands that should work in both contexts (e.g. ``!ping``, ``!help``)
are registered in both registries.
"""

from __future__ import annotations

import logging

from bot.core.commands.registry import CommandInfo, CommandRegistry

logger = logging.getLogger(__name__)


class CommandRouter:
    """Dual-registry command router with source-based dispatch."""

    def __init__(self) -> None:
        self._channel = CommandRegistry()
        self._private = CommandRegistry()

    # ── Registry access ───────────────────────────

    @property
    def channel_registry(self) -> CommandRegistry:
        """Registry for channel (voice client) commands."""
        return self._channel

    @property
    def private_registry(self) -> CommandRegistry:
        """Registry for private (ServerQuery admin) commands."""
        return self._private

    # ── Routing ───────────────────────────────────

    def resolve(self, name: str, target_mode: int) -> CommandInfo | None:
        """Look up a command by name, routing based on message source.

        Args:
            name: Command name or alias.
            target_mode: TS3 target mode — 1 = private, 2 = channel,
                3 = server.

        Returns:
            Matching ``CommandInfo``, or ``None`` if the command is not
            registered for the given source.
        """
        if target_mode == 1:
            return self._private.get(name)
        # channel (2) and server (3) messages both route to channel registry
        return self._channel.get(name)

    # ── Help formatting ───────────────────────────

    def format_merged_help(self, prefix: str) -> str:
        """Format a unified help listing from both registries.

        Commands present in both registries (e.g. ``!ping``) are
        de-duplicated so they appear only once.
        """
        seen: set[str] = set()
        commands: list[CommandInfo] = []

        # Channel commands first
        for cmd in self._channel.get_all():
            seen.add(cmd.name)
            commands.append(cmd)

        # Add private-only commands (not already in channel)
        for cmd in self._private.get_all():
            if cmd.name not in seen:
                commands.append(cmd)

        lines = ["[B]可用命令:[/B]"]
        for cmd in sorted(commands, key=lambda c: c.name):
            aliases = (
                f" ({', '.join(prefix + a for a in cmd.aliases)})"
                if cmd.aliases
                else ""
            )
            admin = " [管理员]" if cmd.admin_only else ""
            lines.append(f"  {prefix}{cmd.name}{aliases} - {cmd.help_text}{admin}")

        return "\n".join(lines)

    def all_command_count(self) -> int:
        """Total unique command count across both registries."""
        names: set[str] = {c.name for c in self._channel.get_all()}
        names.update(c.name for c in self._private.get_all())
        return len(names)
