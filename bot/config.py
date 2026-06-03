from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, SecretStr


def _interpolate_env(value: str) -> str:
    """Replace ${ENV_VAR} patterns with environment variable values."""

    def _replace(match: re.Match) -> str:
        var_name = match.group(1)
        return os.environ.get(var_name, match.group(0))

    return re.sub(r"\$\{(\w+)\}", _replace, value)


def _walk_interpolate(obj):
    """Recursively interpolate env vars in a nested structure."""
    if isinstance(obj, str):
        return _interpolate_env(obj)
    if isinstance(obj, dict):
        return {k: _walk_interpolate(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk_interpolate(item) for item in obj]
    return obj


# ── Config Models ────────────────────────────────


class TS3Config(BaseModel):
    host: str = "localhost"
    query_port: int = 10011
    voice_port: int = 9987
    username: str = "serveradmin"
    password: SecretStr = SecretStr("")
    virtual_server_id: int = 1
    nickname: str = "MusicBot"
    default_channel_id: int = 0
    command_prefix: str = "!"


class AudioConfig(BaseModel):
    pulse_sink_name: str = "ts3bot_music"
    default_volume: int = Field(default=70, ge=0, le=100)
    fade_duration_ms: int = 500
    ffmpeg_path: str | None = None  # Auto-detect if None
    cache_dir: str = "/data/cache"
    cache_max_mb: int = 500


class NeteaseConfig(BaseModel):
    api_base_url: str = "http://localhost:3000"
    search_limit: int = 10
    audio_quality: Literal["standard", "higher", "exhigh", "lossless"] = "exhigh"


class ChatConfig(BaseModel):
    api_base_url: str = "https://api.openai.com/v1"
    api_key: SecretStr = SecretStr("")
    model: str = "gpt-4o-mini"
    temperature: float = 0.8
    max_tokens: int = 512
    context_window: int = 20
    default_persona: str = "gamer_friend"
    per_channel_context: bool = True


class WelcomeConfig(BaseModel):
    enabled: bool = True
    message: str = "欢迎 {username} 来到我们的游戏频道！输入 !help 查看可用命令"
    delay_seconds: int = 2
    mode: Literal["channel", "private"] = "channel"


class AutoGroupConfig(BaseModel):
    enabled: bool = False
    group_ids: list[int] = []


class FollowModeConfig(BaseModel):
    enabled: bool = False
    channel_ids: list[int] = []
    leave_when_empty: bool = True
    debounce_seconds: float = 1.5


class AutomationConfig(BaseModel):
    welcome: WelcomeConfig = WelcomeConfig()
    auto_groups: AutoGroupConfig = AutoGroupConfig()
    follow_mode: FollowModeConfig = FollowModeConfig()


class ReminderConfig(BaseModel):
    name: str
    cron: str
    message: str
    channel_id: int = 0


class SchedulerConfig(BaseModel):
    reminders: list[ReminderConfig] = []


class WebhookConfig(BaseModel):
    enabled: bool = False
    host: str = "0.0.0.0"
    port: int = 8080
    secret: SecretStr = SecretStr("")


class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: Literal["text", "json"] = "text"
    file: str | None = "/data/logs/bot.log"
    max_bytes: int = 10_485_760
    backup_count: int = 5


class BotConfig(BaseModel):
    ts3: TS3Config = TS3Config()
    audio: AudioConfig = AudioConfig()
    netease: NeteaseConfig = NeteaseConfig()
    chat: ChatConfig = ChatConfig()
    automation: AutomationConfig = AutomationConfig()
    scheduler: SchedulerConfig = SchedulerConfig()
    webhook: WebhookConfig = WebhookConfig()
    logging: LoggingConfig = LoggingConfig()


def load_config(config_path: str | Path | None = None) -> BotConfig:
    """Load config from YAML file with environment variable interpolation."""
    if config_path is None:
        # Search in common locations
        candidates = [
            Path("config/config.yaml"),
            Path("/opt/bot/config/config.yaml"),
        ]
        for candidate in candidates:
            if candidate.exists():
                config_path = candidate
                break

    if config_path is None or not Path(config_path).exists():
        # Return default config (env vars still used for secrets)
        return BotConfig()

    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    # Interpolate environment variables
    raw = _walk_interpolate(raw)

    return BotConfig(**raw)
