"""Tests for command parser."""

from bot.core.commands.parser import parse_command


class TestParseCommand:
    def test_basic(self):
        cmd = parse_command("!play test", "!")
        assert cmd is not None
        assert cmd.name == "play"
        assert cmd.args == ["test"]

    def test_multiple_args(self):
        cmd = parse_command("!play 周杰伦 晴天", "!")
        assert cmd is not None
        assert cmd.name == "play"
        assert cmd.args == ["周杰伦", "晴天"]

    def test_quoted_args(self):
        cmd = parse_command('!play "some song name"', "!")
        assert cmd is not None
        assert cmd.args == ["some song name"]

    def test_no_prefix(self):
        assert parse_command("hello world", "!") is None

    def test_empty_after_prefix(self):
        assert parse_command("!", "!") is None

    def test_different_prefix(self):
        cmd = parse_command("/play test", "/")
        assert cmd is not None
        assert cmd.name == "play"

    def test_case_insensitive_name(self):
        cmd = parse_command("!PLAY test", "!")
        assert cmd is not None
        assert cmd.name == "play"

    def test_raw_args(self):
        cmd = parse_command("!chat hello world", "!")
        assert cmd is not None
        assert cmd.raw_args == "hello world"

    def test_metadata(self):
        cmd = parse_command(
            "!test",
            "!",
            invoker_clid=5,
            invoker_uid="abc123",
            invoker_name="TestUser",
            target_mode=2,
        )
        assert cmd is not None
        assert cmd.invoker_clid == 5
        assert cmd.invoker_uid == "abc123"
        assert cmd.invoker_name == "TestUser"
        assert cmd.target_mode == 2

    def test_no_args(self):
        cmd = parse_command("!ping", "!")
        assert cmd is not None
        assert cmd.name == "ping"
        assert cmd.args == []
