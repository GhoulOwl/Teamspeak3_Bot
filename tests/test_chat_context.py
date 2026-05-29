"""Tests for chat context management."""

from bot.services.chat.context import ContextManager, ConversationBuffer


class TestConversationBuffer:
    def test_add_and_get(self):
        buf = ConversationBuffer(max_messages=10)
        buf.add("user", "Hello")
        buf.add("assistant", "Hi there")

        messages = buf.get_messages()
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hello"

    def test_system_prompt(self):
        buf = ConversationBuffer()
        buf.add("user", "Hello")
        messages = buf.get_messages(system_prompt="You are a bot")
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "You are a bot"
        assert len(messages) == 2

    def test_max_messages(self):
        buf = ConversationBuffer(max_messages=3)
        for i in range(5):
            buf.add("user", f"msg{i}")

        assert buf.length == 3
        messages = buf.get_messages()
        assert messages[0]["content"] == "msg2"  # Oldest kept

    def test_clear(self):
        buf = ConversationBuffer()
        buf.add("user", "Hello")
        buf.clear()
        assert buf.length == 0

    def test_username_in_messages(self):
        buf = ConversationBuffer()
        buf.add("user", "Hello", username="Alice")
        messages = buf.get_messages()
        assert "[Alice]: Hello" in messages[0]["content"]

    def test_estimate_tokens(self):
        buf = ConversationBuffer()
        buf.add("user", "你好世界")  # 4 chars ~ 1-2 tokens
        assert buf.estimate_tokens() > 0

    def test_trim_to_tokens(self):
        buf = ConversationBuffer(max_messages=100)
        for i in range(50):
            buf.add("user", "x" * 100)  # ~33 tokens each

        buf.trim_to_tokens(max_tokens=100)
        assert buf.estimate_tokens() <= 100


class TestContextManager:
    def test_per_channel(self):
        mgr = ContextManager(per_channel=True)
        buf1 = mgr.get_buffer(channel_id=1, user_uid="user1")
        buf2 = mgr.get_buffer(channel_id=2, user_uid="user1")
        buf3 = mgr.get_buffer(channel_id=1, user_uid="user2")

        buf1.add("user", "Channel 1 msg")
        assert buf1.length == 1
        assert buf2.length == 0
        assert buf3.length == 1  # Same buffer as buf1

    def test_per_user(self):
        mgr = ContextManager(per_channel=False)
        buf1 = mgr.get_buffer(channel_id=1, user_uid="user1")
        buf2 = mgr.get_buffer(channel_id=2, user_uid="user1")
        buf3 = mgr.get_buffer(channel_id=1, user_uid="user2")

        buf1.add("user", "User1 msg")
        assert buf1.length == 1
        assert buf2.length == 1  # Same buffer (same user)
        assert buf3.length == 0  # Different user

    def test_clear(self):
        mgr = ContextManager()
        buf = mgr.get_buffer(channel_id=1, user_uid="user1")
        buf.add("user", "test")
        mgr.clear(channel_id=1, user_uid="user1")
        assert buf.length == 0

    def test_clear_all(self):
        mgr = ContextManager()
        mgr.get_buffer(1, "u1").add("user", "a")
        mgr.get_buffer(2, "u2").add("user", "b")
        mgr.clear_all()
        # New buffers should be empty
        assert mgr.get_buffer(1, "u1").length == 0
