"""AI chat service — OpenAI-compatible API integration."""

from __future__ import annotations

import logging

from openai import AsyncOpenAI

from bot.services.chat.context import ContextManager
from bot.services.chat.personas import PERSONAS, get_persona

logger = logging.getLogger(__name__)


class ChatService:
    """AI chat service using OpenAI-compatible API.

    Features:
    - Multiple personas with different system prompts
    - Per-channel/user conversation context
    - Token-aware context trimming
    """

    def __init__(
        self,
        api_base_url: str = "https://api.openai.com/v1",
        api_key: str = "",
        model: str = "gpt-4o-mini",
        temperature: float = 0.8,
        max_tokens: int = 512,
        context_window: int = 20,
        default_persona: str = "gamer_friend",
        per_channel_context: bool = True,
    ) -> None:
        self._client = AsyncOpenAI(base_url=api_base_url, api_key=api_key or "no-key")
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._default_persona = default_persona
        self._context = ContextManager(
            max_messages=context_window,
            per_channel=per_channel_context,
        )
        # Per-channel persona overrides
        self._channel_personas: dict[int, str] = {}

    async def close(self) -> None:
        await self._client.close()

    def set_persona(self, channel_id: int, persona_key: str) -> bool:
        """Set the persona for a channel. Returns False if persona doesn't exist."""
        if persona_key not in PERSONAS:
            return False
        self._channel_personas[channel_id] = persona_key
        return True

    def get_persona_key(self, channel_id: int) -> str:
        """Get the active persona key for a channel."""
        return self._channel_personas.get(channel_id, self._default_persona)

    async def chat(
        self,
        message: str,
        channel_id: int,
        user_uid: str,
        username: str = "",
    ) -> str:
        """Process a chat message and return the AI response.

        Args:
            message: The user's message
            channel_id: Channel ID (for context isolation)
            user_uid: User unique identifier
            username: Display name of the user

        Returns:
            AI response text
        """
        persona_key = self.get_persona_key(channel_id)
        persona = get_persona(persona_key)
        system_prompt = persona["system_prompt"] if persona else ""

        buffer = self._context.get_buffer(channel_id, user_uid)

        # Trim context if needed
        buffer.trim_to_tokens(max_tokens=3000)

        # Add user message
        buffer.add("user", message, username=username)

        # Build messages
        messages = buffer.get_messages(system_prompt=system_prompt)

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )
        except Exception as e:
            logger.exception("AI chat API error")
            return f"AI 回复出错了: {e}"

        reply = response.choices[0].message.content or "..."

        # Add assistant response to context
        buffer.add("assistant", reply)

        return reply

    def clear_context(self, channel_id: int, user_uid: str) -> None:
        """Clear conversation context."""
        self._context.clear(channel_id, user_uid)
