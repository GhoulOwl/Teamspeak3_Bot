"""Scheduler service — APScheduler-based reminders."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.config import ReminderConfig

if TYPE_CHECKING:
    from bot.app import BotApplication

logger = logging.getLogger(__name__)


class SchedulerService:
    """Manages scheduled reminders using APScheduler."""

    def __init__(self, app: BotApplication) -> None:
        self._app = app
        self._scheduler = AsyncIOScheduler()

    def start(self) -> None:
        self._scheduler.start()
        logger.info("Scheduler started")

    def stop(self) -> None:
        self._scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")

    def load_reminders(self, reminders: list[ReminderConfig]) -> None:
        """Load reminders from config."""
        for reminder in reminders:
            self.add_reminder(
                name=reminder.name,
                cron=reminder.cron,
                message=reminder.message,
                channel_id=reminder.channel_id,
            )

    def add_reminder(
        self,
        name: str,
        cron: str,
        message: str,
        channel_id: int = 0,
    ) -> None:
        """Add a reminder using a cron expression.

        Cron format: minute hour day month day_of_week
        Example: "0 20 * * *" = every day at 20:00
        """
        parts = cron.split()
        if len(parts) != 5:
            logger.warning("Invalid cron expression for reminder '%s': %s", name, cron)
            return

        minute, hour, day, month, dow = parts

        self._scheduler.add_job(
            self._send_reminder,
            "cron",
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=dow,
            args=[message, channel_id],
            id=name,
            replace_existing=True,
        )
        logger.info("Added reminder '%s': cron=%s", name, cron)

    def remove_reminder(self, name: str) -> bool:
        """Remove a reminder by name."""
        try:
            self._scheduler.remove_job(name)
            return True
        except Exception:
            return False

    async def _send_reminder(self, message: str, channel_id: int) -> None:
        """Send a reminder message to the specified channel."""
        try:
            await self._app.sq.reply_to_channel(message)
        except Exception:
            logger.exception("Failed to send reminder")
