"""Tests for TS3 ServerQuery protocol."""

from bot.core.serverquery.protocol import (
    build_command,
    parse_record,
    parse_record_list,
    parse_response,
    ts3_escape,
    ts3_unescape,
)


class TestEscape:
    def test_space(self):
        assert ts3_escape("hello world") == "hello\\sworld"

    def test_pipe(self):
        assert ts3_escape("a|b") == "a\\pb"

    def test_backslash(self):
        assert ts3_escape("a\\b") == "a\\\\b"

    def test_newline(self):
        assert ts3_escape("a\nb") == "a\\nb"

    def test_roundtrip(self):
        original = "Hello World | Test / Path \\ backslash"
        assert ts3_unescape(ts3_escape(original)) == original

    def test_empty(self):
        assert ts3_escape("") == ""
        assert ts3_unescape("") == ""

    def test_no_special_chars(self):
        assert ts3_escape("hello") == "hello"
        assert ts3_unescape("hello") == "hello"


class TestUnescape:
    def test_space(self):
        assert ts3_unescape("hello\\sworld") == "hello world"

    def test_pipe(self):
        assert ts3_unescape("a\\pb") == "a|b"

    def test_backslash(self):
        assert ts3_unescape("a\\\\b") == "a\\b"

    def test_unknown_escape(self):
        # Unknown escape sequences should be left as-is
        assert ts3_unescape("a\\zb") == "a\\zb"


class TestParseRecord:
    def test_simple(self):
        record = parse_record("clid=5 client_nickname=Test")
        assert record["clid"] == "5"
        assert record["client_nickname"] == "Test"

    def test_escaped_value(self):
        record = parse_record("msg=Hello\\sWorld")
        assert record["msg"] == "Hello World"

    def test_empty(self):
        record = parse_record("")
        assert record == {}


class TestParseRecordList:
    def test_single(self):
        records = parse_record_list("cid=1 channel_name=Default")
        assert len(records) == 1
        assert records[0]["cid"] == "1"

    def test_multiple(self):
        records = parse_record_list("cid=1 channel_name=A|cid=2 channel_name=B")
        assert len(records) == 2
        assert records[1]["channel_name"] == "B"

    def test_empty(self):
        assert parse_record_list("") == []


class TestParseResponse:
    def test_success(self):
        resp = parse_response("clid=5\nerror id=0 msg=ok")
        assert resp.ok
        assert resp.error_id == 0
        assert resp.data[0]["clid"] == "5"

    def test_error(self):
        resp = parse_response("error id=256 msg=no\\spermissions")
        assert not resp.ok
        assert resp.error_id == 256
        assert resp.error_msg == "no permissions"

    def test_with_banner(self):
        raw = "TS3\nWelcome\nclid=5\nerror id=0 msg=ok"
        resp = parse_response(raw)
        assert resp.ok
        assert resp.data[0]["clid"] == "5"


class TestBuildCommand:
    def test_simple(self):
        cmd = build_command("whoami")
        assert cmd == "whoami"

    def test_with_params(self):
        cmd = build_command("sendtextmessage", {"targetmode": 1, "msg": "Hello World"})
        assert "sendtextmessage" in cmd
        assert "targetmode=1" in cmd
        assert "msg=Hello\\sWorld" in cmd

    def test_with_opts(self):
        cmd = build_command("clientlist", opts=["-uid", "-away"])
        assert cmd == "clientlist -uid -away"
