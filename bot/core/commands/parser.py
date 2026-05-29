"""Command text parser."""

from __future__ import annotations

import shlex
from dataclasses import dataclass


@dataclass
class ParsedCommand:
    """A parsed command from a text message."""

    name: str
    args: list[str]
    raw_args: str
    invoker_clid: int
    invoker_uid: str
    invoker_name: str
    target_mode: int


def parse_command(
    text: str,
    prefix: str,
    invoker_clid: int = 0,
    invoker_uid: str = "",
    invoker_name: str = "",
    target_mode: int = 1,
) -> ParsedCommand | None:
    """Parse a text message into a command.

    Returns None if the message doesn't start with the command prefix.
    """
    text = text.strip()

    if not text.startswith(prefix):
        return None

    # Remove prefix
    cmd_text = text[len(prefix):].strip()
    if not cmd_text:
        return None

    # Split into command name and arguments
    try:
        parts = shlex.split(cmd_text)
    except ValueError:
        # Fallback to simple split if shlex fails (e.g., unmatched quotes)
        parts = cmd_text.split()

    if not parts:
        return None

    name = parts[0].lower()
    args = parts[1:]
    raw_args = cmd_text[len(parts[0]):].strip()

    return ParsedCommand(
        name=name,
        args=args,
        raw_args=raw_args,
        invoker_clid=invoker_clid,
        invoker_uid=invoker_uid,
        invoker_name=invoker_name,
        target_mode=target_mode,
    )
