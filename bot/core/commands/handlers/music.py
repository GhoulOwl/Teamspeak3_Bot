"""Music command handlers: !play, !skip, !pause, !resume, !stop, !queue, !np, !lyrics, etc."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from bot.core.commands.context import CommandContext
from bot.core.commands.registry import CommandRegistry

if TYPE_CHECKING:
    from bot.app import BotApplication

logger = logging.getLogger(__name__)


def register(registry: CommandRegistry, app: BotApplication) -> None:
    """Register music commands."""

    @registry.command("play", aliases=["p", "播放", "点歌"], help="搜索并播放歌曲，支持网易云/YouTube/B站链接")
    async def handle_play(ctx: CommandContext) -> None:
        if not ctx.raw_args:
            await ctx.reply_same(
                "用法: !play <歌曲名> 或 !play <链接>\n"
                "支持: 网易云音乐、YouTube、B站、SoundCloud"
            )
            return

        query = ctx.raw_args

        # URL playback — use yt-dlp to extract audio from any supported platform
        if query.startswith("http://") or query.startswith("https://"):
            await ctx.reply_same("正在解析链接...")

            # Extract audio info via yt-dlp
            info = await app.netease.extract_info(query)
            if not info:
                await ctx.reply_same("无法解析该链接，请检查链接是否正确")
                return

            from datetime import timedelta

            from bot.services.netease.models import Song

            song = Song(
                id=0,
                title=info.title,
                artist=info.source,
                duration=timedelta(seconds=info.duration),
            )
            entry = app.music_queue.add(song, ctx.invoker_clid, ctx.invoker_name, is_url=True)
            entry._url = query  # Store original URL; will be re-extracted at play time

            if app.audio.state.value == "idle":
                app.music_queue.next()
                # Download audio to local file (CDN URLs expire mid-stream)
                downloaded = await app.netease.download_url(query)
                if downloaded:
                    await app.audio.play(
                        downloaded.path,
                        temp_file=downloaded.path,
                        duration=downloaded.duration,
                    )
                    await ctx.reply_channel(
                        f"正在播放: {info.title} ({info.source}) - 点歌: {ctx.invoker_name}"
                    )
                else:
                    await ctx.reply_same("音频下载失败")
            else:
                pos = app.music_queue.length
                await ctx.reply_same(f"已加入队列 (#{pos}): {info.title} ({info.source})")
            return

        # Search Netease
        try:
            songs = await app.netease.search(query, limit=5)
        except Exception as e:
            logger.exception("Search failed")
            await ctx.reply_same(f"搜索失败: {e}")
            return

        if not songs:
            await ctx.reply_same(f"未找到: {query}")
            return

        # Pick the first result
        song = songs[0]
        entry = app.music_queue.add(song, ctx.invoker_clid, ctx.invoker_name)

        if app.audio.state.value == "idle":
            # Play immediately — fetch fresh URL
            await _play_next(app)
            await ctx.reply_channel(
                f"正在播放: {song.display_name} "
                f"- 点歌: {ctx.invoker_name}"
            )
        else:
            pos = app.music_queue.length
            await ctx.reply_same(
                f"已加入队列 (#{pos}): {song.display_name}"
            )

    @registry.command("skip", aliases=["s", "切歌", "下一首"], help="投票跳过当前歌曲")
    async def handle_skip(ctx: CommandContext) -> None:
        if not app.music_queue.current:
            await ctx.reply_same("当前没有在播放")
            return

        # Get channel user count for skip threshold
        try:
            clients = await app.sq.client_list()
            channel_users = len(clients)  # Simplified; ideally filter by channel
        except Exception:
            channel_users = 3  # Default fallback

        if app.music_queue.vote_skip(ctx.invoker_clid, channel_users):
            skipped = app.music_queue.current
            await ctx.reply_channel(f"跳过: {skipped.song.display_name}")
            await _play_next(app)
        else:
            votes = len(app.music_queue._skip_votes)
            needed = max(1, channel_users // 2 + 1)
            await ctx.reply_same(f"已投票跳过 ({votes}/{needed})")

    @registry.command("pause", aliases=["暂停"], help="暂停播放")
    async def handle_pause(ctx: CommandContext) -> None:
        if app.audio.state.value == "playing":
            await app.audio.pause()
            await ctx.reply_same("已暂停")
        else:
            await ctx.reply_same("当前没有在播放")

    @registry.command("resume", aliases=["继续"], help="继续播放")
    async def handle_resume(ctx: CommandContext) -> None:
        if app.audio.state.value == "paused":
            await app.audio.resume()
            await ctx.reply_same("已继续播放")
        else:
            await ctx.reply_same("当前没有暂停")

    @registry.command("stop", aliases=["停止"], help="停止播放", admin_only=True)
    async def handle_stop(ctx: CommandContext) -> None:
        await app.audio.stop()
        app.music_queue._current = None
        await ctx.reply_same("已停止播放")

    @registry.command("queue", aliases=["q", "队列"], help="显示播放队列")
    async def handle_queue(ctx: CommandContext) -> None:
        display = app.music_queue.format_queue()
        await ctx.reply_same(display)

    @registry.command("np", aliases=["正在播放", "当前"], help="显示当前播放信息")
    async def handle_np(ctx: CommandContext) -> None:
        display = app.music_queue.format_now_playing()
        await ctx.reply_same(display)

    @registry.command("lyrics", aliases=["lrc", "歌词"], help="显示当前歌词")
    async def handle_lyrics(ctx: CommandContext) -> None:
        current = app.music_queue.current
        if not current or current.is_url:
            await ctx.reply_same("当前没有在播放网易云歌曲")
            return

        try:
            lyrics = await app.netease.get_lyrics(current.song.id)
        except Exception as e:
            await ctx.reply_same(f"获取歌词失败: {e}")
            return

        if not lyrics:
            await ctx.reply_same("该歌曲没有歌词")
            return

        display = f"[B]{current.song.title}[/B] 歌词:\n{lyrics.format_display()}"
        await ctx.reply_same(display)

    @registry.command("clear", aliases=["清空"], help="清空队列", admin_only=True)
    async def handle_clear(ctx: CommandContext) -> None:
        count = app.music_queue.clear()
        await ctx.reply_same(f"已清空队列 ({count} 首)")

    @registry.command("shuffle", aliases=["随机"], help="随机打乱队列")
    async def handle_shuffle(ctx: CommandContext) -> None:
        app.music_queue.shuffle()
        await ctx.reply_same("已随机打乱队列")

    @registry.command("repeat", aliases=["r", "重复", "循环"], help="切换重复模式 (关闭/单曲/列表)")
    async def handle_repeat(ctx: CommandContext) -> None:
        mode = app.music_queue.toggle_repeat()
        mode_names = {"off": "关闭", "one": "单曲重复", "all": "列表重复"}
        await ctx.reply_same(f"重复模式: {mode_names.get(mode.value, mode.value)}")

    @registry.command("remove", aliases=["rm", "移除", "删除"], help="从队列移除第 N 首")
    async def handle_remove(ctx: CommandContext) -> None:
        if not ctx.args:
            await ctx.reply_same("用法: !remove <序号>")
            return

        try:
            index = int(ctx.args[0]) - 1  # Convert to 0-based
        except ValueError:
            await ctx.reply_same("请输入数字序号")
            return

        entry = app.music_queue.remove(index)
        if entry:
            await ctx.reply_same(f"已移除: {entry.song.display_name}")
        else:
            await ctx.reply_same("无效的序号")


async def _try_fallback_download(song_name: str, artist: str, app: BotApplication):
    """Try to download a song from alternative sources when Netease fails.

    Searches YouTube and Bilibili for the song and returns a DownloadedAudio
    if found, or None if all sources fail.
    """
    query = f"{song_name} {artist}".strip()
    fallback_urls = [
        f"ytsearch1:{query}",  # YouTube search
        f"bilisearch1:{query}",  # Bilibili search
    ]

    for search_url in fallback_urls:
        try:
            info = await app.netease._ytdlp.extract_audio(search_url)
            if not info or info.duration < 30:
                continue
            downloaded = await app.netease.download_url(search_url)
            if downloaded:
                logger.info(
                    "Fallback download succeeded: %s from %s",
                    song_name, info.source,
                )
                return downloaded
        except Exception:
            logger.debug("Fallback search failed for %s via %s", query, search_url)
            continue

    return None


async def _play_next(app: BotApplication) -> None:
    """Play the next song from the queue."""
    entry = app.music_queue.next()
    if not entry:
        await app.sq.reply_to_channel("队列已空，播放结束")
        return

    if entry.is_url:
        # Download audio via yt-dlp (CDN URLs expire mid-stream)
        original_url = getattr(entry, "_url", "")
        if original_url:
            try:
                downloaded = await app.netease.download_url(original_url)
                if downloaded:
                    await app.audio.play(
                        downloaded.path,
                        temp_file=downloaded.path,
                        duration=downloaded.duration,
                    )
                    await app.sq.reply_to_channel(
                        f"正在播放: {entry.song.display_name} - 点歌: {entry.requester_name}"
                    )
                    return
            except Exception:
                logger.exception("Failed to download audio: %s", original_url)

        await app.sq.reply_to_channel("链接解析失败，跳过")
        await _play_next(app)
        return

    # Download audio to local temp file via yt-dlp (Netease)
    try:
        downloaded = await app.netease.download_song(entry.song.id)
    except Exception:
        logger.exception("Failed to download song %d", entry.song.id)
        downloaded = None

    # Fallback: try YouTube/Bilibili when Netease download fails (VIP/region restricted)
    if not downloaded:
        logger.info(
            "Netease download failed for '%s', trying fallback sources...",
            entry.song.display_name,
        )
        downloaded = await _try_fallback_download(
            entry.song.title, entry.song.artist, app,
        )

    if not downloaded:
        await app.sq.reply_to_channel(
            f"歌曲不可用 (VIP或地区限制): {entry.song.display_name}"
        )
        await _play_next(app)
        return

    try:
        await app.audio.play(
            downloaded.path,
            temp_file=downloaded.path,
            duration=downloaded.duration,
        )
        await app.sq.reply_to_channel(
            f"正在播放: {entry.song.display_name} - 点歌: {entry.requester_name}"
        )
    except Exception:
        logger.exception("Failed to play song")
        await _play_next(app)
