"""Conversation context buffer for AI chat."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass
class Message:
    """A single chat message."""

    role: str  # "user" or "assistant"
    content: str
    username: str = ""


class ConversationBuffer:
    """Sliding window conversation buffer.

    Keeps the last N messages to provide context for AI chat.
    """

    def __init__(self, max_messages: int = 20) -> None:
        self._messages: deque[Message] = deque(maxlen=max_messages)
        self._max_messages = max_messages

    def add(self, role: str, content: str, username: str = "") -> None:
        """Add a message to the buffer."""
        self._messages.append(Message(role=role, content=content, username=username))

    def get_messages(self, system_prompt: str = "") -> list[dict[str, str]]:
        """Get messages in OpenAI API format."""
        messages: list[dict[str, str]] = []

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        for msg in self._messages:
            # Include username for user messages to help AI distinguish speakers
            if msg.role == "user" and msg.username:
                content = f"[{msg.username}]: {msg.content}"
            else:
                content = msg.content
            messages.append({"role": msg.role, "content": content})

        return messages

    def clear(self) -> None:
        """Clear all messages."""
        self._messages.clear()

    @property
    def length(self) -> int:
        return len(self._messages)

    def estimate_tokens(self) -> int:
        """Rough token estimate (~4 chars per token for Chinese)."""
        total_chars = sum(len(m.content) for m in self._messages)
        return total_chars // 3  # Chinese text is more token-dense

    def trim_to_tokens(self, max_tokens: int = 3000) -> None:
        """Remove oldest messages if token estimate exceeds limit."""
        while self.estimate_tokens() > max_tokens and self._messages:
            self._messages.popleft()


class ContextManager:
    """Manages conversation buffers per channel or user."""

    def __init__(self, max_messages: int = 20, per_channel: bool = True) -> None:
        self._buffers: dict[str, ConversationBuffer] = {}
        self._max_messages = max_messages
        self._per_channel = per_channel

    def get_buffer(self, channel_id: int, user_uid: str) -> ConversationBuffer:
        """Get or create a conversation buffer."""
        if self._per_channel:
            key = f"channel:{channel_id}"
        else:
            key = f"user:{user_uid}"

        if key not in self._buffers:
            self._buffers[key] = ConversationBuffer(max_messages=self._max_messages)

        return self._buffers[key]

    def clear(self, channel_id: int, user_uid: str) -> None:
        """Clear a specific buffer."""
        if self._per_channel:
            key = f"channel:{channel_id}"
        else:
            key = f"user:{user_uid}"

        if key in self._buffers:
            self._buffers[key].clear()

    def clear_all(self) -> None:
        """Clear all buffers."""
        self._buffers.clear()
