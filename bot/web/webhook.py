"""Webhook receiver — FastAPI endpoint for external notifications."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

if TYPE_CHECKING:
    from bot.app import BotApplication

logger = logging.getLogger(__name__)

app = FastAPI(title="TS3 Bot Webhook", docs_url=None, redoc_url=None)


class WebhookMessage(BaseModel):
    message: str
    channel_id: int = 0


class StatusResponse(BaseModel):
    status: str
    connected: bool
    current_song: str | None = None
    queue_length: int = 0
    uptime: int = 0


def create_webhook_app(bot_app: BotApplication, secret: str = "") -> FastAPI:
    """Create the FastAPI webhook app with bot context."""

    async def verify_secret(authorization: str = Header(default="")) -> None:
        if secret and authorization != f"Bearer {secret}":
            raise HTTPException(status_code=403, detail="Invalid secret")

    @app.post("/webhook")
    async def webhook(
        msg: WebhookMessage,
        _: None = Depends(verify_secret),
    ) -> dict:
        try:
            await bot_app.sq.reply_to_channel(msg.message)
            return {"status": "ok"}
        except Exception as e:
            logger.exception("Webhook send failed")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/status")
    async def status(
        _: None = Depends(verify_secret),
    ) -> StatusResponse:
        import time

        from bot.core.commands.handlers.debug import _start_time

        current_song = None
        if bot_app.music_queue.current:
            current_song = bot_app.music_queue.current.song.display_name

        return StatusResponse(
            status="running",
            connected=bot_app.sq.connected,
            current_song=current_song,
            queue_length=bot_app.music_queue.length,
            uptime=int(time.time() - _start_time),
        )

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    return app
