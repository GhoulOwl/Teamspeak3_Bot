"""Shared test fixtures."""

from __future__ import annotations

import pytest

from bot.config import BotConfig
from bot.core.serverquery.events import EventDispatcher


@pytest.fixture
def config() -> BotConfig:
    return BotConfig()


@pytest.fixture
def dispatcher() -> EventDispatcher:
    return EventDispatcher()
