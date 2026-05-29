"""Tests for music queue manager."""

from datetime import timedelta

from bot.services.netease.models import Song
from bot.services.queue.manager import MusicQueue, RepeatMode


def _make_song(id: int, title: str = "Song") -> Song:
    return Song(id=id, title=f"{title}{id}", artist="Artist", duration=timedelta(minutes=3))


class TestMusicQueue:
    def test_empty(self):
        q = MusicQueue()
        assert q.is_empty
        assert q.length == 0
        assert q.current is None

    def test_add(self):
        q = MusicQueue()
        q.add(_make_song(1), 5, "User1")
        assert q.length == 1
        assert not q.is_empty

    def test_next(self):
        q = MusicQueue()
        q.add(_make_song(1), 5, "User1")
        q.add(_make_song(2), 6, "User2")

        entry = q.next()
        assert entry is not None
        assert entry.song.id == 1
        assert q.current is not None

        entry2 = q.next()
        assert entry2 is not None
        assert entry2.song.id == 2

    def test_next_empty(self):
        q = MusicQueue()
        assert q.next() is None

    def test_peek(self):
        q = MusicQueue()
        q.add(_make_song(1), 5, "User1")
        q.add(_make_song(2), 6, "User2")

        peeked = q.peek(1)
        assert len(peeked) == 1
        assert q.length == 2  # Queue unchanged

    def test_remove(self):
        q = MusicQueue()
        q.add(_make_song(1), 5, "User1")
        q.add(_make_song(2), 6, "User2")

        removed = q.remove(0)
        assert removed is not None
        assert removed.song.id == 1
        assert q.length == 1

    def test_remove_invalid(self):
        q = MusicQueue()
        assert q.remove(5) is None

    def test_clear(self):
        q = MusicQueue()
        q.add(_make_song(1), 5, "User1")
        q.add(_make_song(2), 6, "User2")

        count = q.clear()
        assert count == 2
        assert q.is_empty

    def test_shuffle(self):
        q = MusicQueue()
        for i in range(20):
            q.add(_make_song(i), 5, "User")

        original = [e.song.id for e in q.peek(20)]
        q.shuffle()
        shuffled = [e.song.id for e in q.peek(20)]

        # Very unlikely to be same order after shuffle
        assert len(shuffled) == 20
        assert set(shuffled) == set(original)

    def test_skip_voting(self):
        q = MusicQueue()
        # threshold = max(1, 5//2 + 1) = 3
        assert not q.vote_skip(10, 5)  # 1 vote
        assert not q.vote_skip(11, 5)  # 2 votes
        assert q.vote_skip(12, 5)      # 3 votes = threshold

    def test_skip_votes_cleared_on_next(self):
        q = MusicQueue()
        q.add(_make_song(1), 5, "User")
        q.add(_make_song(2), 6, "User")
        q.next()
        q.vote_skip(10, 3)
        q.next()  # Should clear votes
        assert len(q._skip_votes) == 0

    def test_repeat_one(self):
        q = MusicQueue()
        q.add(_make_song(1), 5, "User")
        q.repeat_mode = RepeatMode.ONE

        entry1 = q.next()
        entry2 = q.next()
        assert entry1.song.id == entry2.song.id

    def test_repeat_all(self):
        q = MusicQueue()
        q.add(_make_song(1), 5, "User")
        q.add(_make_song(2), 6, "User")
        q.repeat_mode = RepeatMode.ALL

        entry1 = q.next()  # Play song 1
        assert entry1.song.id == 1

        entry2 = q.next()  # Song 1 re-queued, then song 2 played
        assert entry2.song.id == 2

        entry3 = q.next()  # Song 2 re-queued, song 1 plays again (repeat-all)
        assert entry3.song.id == 1

    def test_toggle_repeat(self):
        q = MusicQueue()
        assert q.repeat_mode == RepeatMode.OFF
        q.toggle_repeat()
        assert q.repeat_mode == RepeatMode.ONE
        q.toggle_repeat()
        assert q.repeat_mode == RepeatMode.ALL
        q.toggle_repeat()
        assert q.repeat_mode == RepeatMode.OFF

    def test_format_queue(self):
        q = MusicQueue()
        display = q.format_queue()
        assert "空" in display

        q.add(_make_song(1, "TestSong"), 5, "User1")
        q.next()
        q.add(_make_song(2, "NextSong"), 6, "User2")
        display = q.format_queue()
        assert "TestSong" in display
        assert "NextSong" in display

    def test_format_now_playing(self):
        q = MusicQueue()
        assert "没有" in q.format_now_playing()

        q.add(_make_song(1, "PlayingSong"), 5, "User1")
        q.next()
        display = q.format_now_playing()
        assert "PlayingSong" in display
