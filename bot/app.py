"""BotApplication — top-level orchestrator that wires all modules together."""

from __future__ import annotations

import asyncio
import logging
import signal

from bot.config import BotConfig, load_config
from bot.core.audio.controller import AudioController
from bot.core.commands.context import CommandContext
from bot.core.commands.parser import parse_command
from bot.core.commands.registry import CommandRegistry
from bot.core.serverquery.client import AsyncServerQueryClient
from bot.core.serverquery.events import SQEvent, TextMessageEvent
from bot.services.automation.follow import FollowMode
from bot.services.automation.groups import GroupAssigner
from bot.services.automation.welcome import WelcomeService
from bot.services.chat.service import ChatService
from bot.services.netease.client import NeteaseAPIClient
from bot.services.queue.manager import MusicQueue
from bot.services.scheduler.jobs import SchedulerService
from bot.services.tracking.voice_tracker import VoiceClientTracker

logger = logging.getLogger(__name__)


class BotApplication:
    """Main application that owns and orchestrates all bot components.

    Lifecycle:
        1. Load config
        2. Create all service instances
        3. Connect to ServerQuery
        4. Register command handlers and event listeners
        5. Start background services (scheduler, webhook, auto-play)
        6. Run until interrupted
    """

    def __init__(self, config: BotConfig | None = None) -> None:
        self.config = config or load_config()
        self._setup_logging()

        # Core components
        self.sq = AsyncServerQueryClient(
            host=self.config.ts3.host,
            port=self.config.ts3.query_port,
            username=self.config.ts3.username,
            password=self.config.ts3.password.get_secret_value(),
            virtual_server_id=self.config.ts3.virtual_server_id,
            nickname=self.config.ts3.nickname,
        )

        self.registry = CommandRegistry()
        self.audio = AudioController(
            ffmpeg_path=self.config.audio.ffmpeg_path or None,
            pulse_sink=self.config.audio.pulse_sink_name,
            default_volume=self.config.audio.default_volume,
            fade_duration_ms=self.config.audio.fade_duration_ms,
        )

        self.music_queue = MusicQueue()

        self.netease = NeteaseAPIClient()

        self.chat_service = ChatService(
            api_base_url=self.config.chat.api_base_url,
            api_key=self.config.chat.api_key.get_secret_value(),
            model=self.config.chat.model,
            temperature=self.config.chat.temperature,
            max_tokens=self.config.chat.max_tokens,
            context_window=self.config.chat.context_window,
            default_persona=self.config.chat.default_persona,
            per_channel_context=self.config.chat.per_channel_context,
        )

        # Automation services
        self.welcome_service = WelcomeService(
            app=self,
            enabled=self.config.automation.welcome.enabled,
            message=self.config.automation.welcome.message,
            delay_seconds=self.config.automation.welcome.delay_seconds,
            mode=self.config.automation.welcome.mode,
        )

        self.group_assigner = GroupAssigner(
            app=self,
            enabled=self.config.automation.auto_groups.enabled,
            group_ids=self.config.automation.auto_groups.group_ids,
        )

        self.follow_mode = FollowMode(
            app=self,
            enabled=self.config.automation.follow_mode.enabled,
            channel_ids=self.config.automation.follow_mode.channel_ids,
            leave_when_empty=self.config.automation.follow_mode.leave_when_empty,
            debounce_seconds=self.config.automation.follow_mode.debounce_seconds,
            default_channel_id=self.config.ts3.default_channel_id,
        )

        self.scheduler = SchedulerService(app=self)

        # Voice client tracker: keeps ServerQuery in the same channel
        # as the TS3 voice client so commands work in any channel
        self.voice_tracker = VoiceClientTracker(
            app=self,
            voice_nickname=self.config.ts3.nickname,
        )

        # Webhook
        self._webhook_server = None
        self._webhook_task = None

        # Audio auto-play callback
        self.audio.set_callbacks(
            on_stopped=self._on_playback_stopped,
            on_error=self._on_playback_error,
        )

    def _setup_logging(self) -> None:
        """Configure logging based on config."""
        cfg = self.config.logging
        level = getattr(logging, cfg.level.upper(), logging.INFO)

        handlers: list[logging.Handler] = [logging.StreamHandler()]

        if cfg.file:
            try:
                from pathlib import Path
                Path(cfg.file).parent.mkdir(parents=True, exist_ok=True)
                from logging.handlers import RotatingFileHandler
                handlers.append(
                    RotatingFileHandler(
                        cfg.file,
                        maxBytes=cfg.max_bytes,
                        backupCount=cfg.backup_count,
                    )
                )
            except Exception:
                logger.warning("Could not set up file logging")

        logging.basicConfig(
            level=level,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            handlers=handlers,
        )

    def _register_commands(self) -> None:
        """Register all command handlers."""
        from bot.core.commands.handlers import admin, chat, debug, music, volume

        debug.register(self.registry)
        music.register(self.registry, self)
        volume.register(self.registry, self)
        chat.register(self.registry, self)
        admin.register(self.registry, self, self.config.ts3.command_prefix)

        logger.info("Registered %d commands", len(self.registry.get_all()))

    def _setup_event_handlers(self) -> None:
        """Wire up ServerQuery event handlers."""
        # Command routing: text message → command parser → handler
        self.sq.dispatcher.subscribe("textmessage", self._on_text_message)

        # Automation services
        self.welcome_service.subscribe()
        self.group_assigner.subscribe()
        self.follow_mode.subscribe()

        # Voice client tracker (subscribes to cliententerview/clientmoved)
        self.voice_tracker.subscribe()

    async def _on_text_message(self, event: SQEvent) -> None:
        """Route text messages to the command system."""
        msg_event = TextMessageEvent.from_event(event)

        # Ignore messages from ourselves
        if msg_event.invoker_clid == self.sq.client_id:
            return

        # Parse command
        cmd = parse_command(
            text=msg_event.message,
            prefix=self.config.ts3.command_prefix,
            invoker_clid=msg_event.invoker_clid,
            invoker_uid=msg_event.invoker_uid,
            invoker_name=msg_event.invoker_name,
            target_mode=msg_event.target_mode,
        )

        if cmd is None:
            return

        # Look up command
        cmd_info = self.registry.get(cmd.name)
        if cmd_info is None:
            return

        # Execute command
        ctx = CommandContext(cmd, self.sq)
        try:
            await cmd_info.handler(ctx)
        except Exception:
            logger.exception("Error executing command '%s'", cmd.name)
            try:
                await ctx.reply(f"命令执行出错: {cmd.name}")
            except Exception:
                pass

    async def _on_playback_stopped(self) -> None:
        """Called when a song finishes — play next from queue."""
        entry = self.music_queue.next()
        if not entry:
            logger.info("Queue empty, playback stopped")
            return

        if entry.is_url:
            # Download audio via yt-dlp (CDN URLs expire mid-stream)
            original_url = getattr(entry, "_url", "")
            if original_url:
                try:
                    downloaded = await self.netease.download_url(original_url)
                    if downloaded:
                        await self.audio.play(
                            downloaded.path,
                            temp_file=downloaded.path,
                            duration=downloaded.duration,
                        )
                        await self.sq.reply_to_channel(
                            f"正在播放: {entry.song.display_name} - 点歌: {entry.requester_name}"
                        )
                        return
                except Exception:
                    logger.exception("Failed to download audio")
            await self.sq.reply_to_channel("链接解析失败，跳过")
            await self._on_playback_stopped()
            return

        # Download audio to local temp file via yt-dlp (Netease)
        try:
            downloaded = await self.netease.download_song(entry.song.id)
        except Exception:
            logger.exception("Failed to download song")
            downloaded = None

        # Fallback: try YouTube/Bilibili when Netease download fails
        if not downloaded:
            from bot.core.commands.handlers.music import _try_fallback_download
            downloaded = await _try_fallback_download(
                entry.song.title, entry.song.artist, self,
            )

        if not downloaded:
            await self.sq.reply_to_channel(f"歌曲不可用: {entry.song.display_name}")
            await self._on_playback_stopped()
            return

        try:
            await self.audio.play(
                downloaded.path,
                temp_file=downloaded.path,
                duration=downloaded.duration,
            )
            await self.sq.reply_to_channel(
                f"正在播放: {entry.song.display_name} - 点歌: {entry.requester_name}"
            )
        except Exception:
            logger.exception("Failed to play song")
            await self._on_playback_stopped()

    async def _on_playback_error(self) -> None:
        """Called when playback encounters an error."""
        logger.warning("Playback error, trying next song")
        current = self.music_queue.current
        if current:
            await self.sq.reply_to_channel(f"播放出错: {current.song.display_name}，尝试下一首")
        await self._on_playback_stopped()

    async def _start_webhook(self) -> None:
        """Start the webhook FastAPI server if enabled."""
        if not self.config.webhook.enabled:
            return

        import uvicorn

        from bot.web.webhook import create_webhook_app

        webhook_app = create_webhook_app(
            self,
            secret=self.config.webhook.secret.get_secret_value(),
        )

        config = uvicorn.Config(
            webhook_app,
            host=self.config.webhook.host,
            port=self.config.webhook.port,
            log_level="info",
        )
        self._webhook_server = uvicorn.Server(config)
        self._webhook_task = asyncio.create_task(self._webhook_server.serve())
        logger.info(
            "Webhook server started on %s:%d",
            self.config.webhook.host,
            self.config.webhook.port,
        )

    async def start(self) -> None:
        """Start the bot application."""
        logger.info("TS3 Bot starting...")

        # Register commands and event handlers
        self._register_commands()
        self._setup_event_handlers()

        # Connect to ServerQuery
        await self.sq.start()

        # Sync ServerQuery to voice client's channel (runs in background)
        asyncio.create_task(self.voice_tracker.initial_sync())

        # Start scheduler
        self.scheduler.start()
        self.scheduler.load_reminders(self.config.scheduler.reminders)

        # Start webhook
        await self._start_webhook()

        logger.info("TS3 Bot started and ready!")

    async def stop(self) -> None:
        """Gracefully stop the bot application."""
        logger.info("TS3 Bot shutting down...")

        # Stop webhook
        if self._webhook_server:
            self._webhook_server.should_exit = True

        # Stop scheduler
        self.scheduler.stop()

        # Fade out and stop audio
        await self.audio.fade_out_and_stop()

        # Send goodbye message
        try:
            await self.sq.reply_to_channel("Bot 已下线")
        except Exception:
            pass

        # Close services
        await self.netease.close()
        await self.chat_service.close()

        # Disconnect ServerQuery
        await self.sq.stop()

        logger.info("TS3 Bot stopped")

    async def run(self) -> None:
        """Run the bot until interrupted."""
        await self.start()

        # Wait until cancelled
        stop_event = asyncio.Event()

        def _signal_handler():
            stop_event.set()

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)

        await stop_event.wait()
        await self.stop()
