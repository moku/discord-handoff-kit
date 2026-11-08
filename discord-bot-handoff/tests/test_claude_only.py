import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bot as bot_module
import config
import supervisor


class FakeStore:
    def __init__(self, active="local"):
        self.active = active

    async def get_active(self, thread_id):
        return self.active

    async def set_active(self, thread_id, value):
        self.active = value


class FakeChannel:
    def __init__(self):
        self.id = 9
        self.sent = []

    async def send(self, text):
        self.sent.append(text)


def _message(content, mid=5):
    return SimpleNamespace(
        id=mid, content=content, attachments=[], channel=FakeChannel(),
        author=SimpleNamespace(id=2, bot=False),
    )


def _claude_only_bot(store):
    calls = []
    fake = SimpleNamespace(_llm=None, _store=store, user=object())
    fake._is_watched = lambda message: True

    async def switch(thread_id, channel, pending=None):
        calls.append(("spawn", thread_id, pending))

    async def to_local(thread_id, channel):
        calls.append(("stop", thread_id))

    async def reply_local(*_):
        raise AssertionError("no LLM configured")

    fake._switch_to_claude = switch
    fake._switch_to_local = to_local
    fake._reply_local = reply_local
    return fake, calls


def _run(fake, message):
    asyncio.run(bot_module.HandoffBot.on_message(fake, message))


def test_blank_base_url_disables_llm(monkeypatch, tmp_path):
    monkeypatch.setenv("HANDOFF_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "x")
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "")
    assert not config.Config.from_env().llm_enabled
    monkeypatch.delenv("LOCAL_LLM_BASE_URL")
    assert config.Config.from_env().llm_enabled


def test_any_message_spawns_claude_and_hands_it_over():
    fake, calls = _claude_only_bot(FakeStore("local"))
    message = _message("요약 좀 해줘")
    _run(fake, message)
    assert calls == [("spawn", "9", message)]


def test_dead_session_respawns_instead_of_falling_back(monkeypatch):
    async def dead(_):
        return False

    monkeypatch.setattr(supervisor, "responder_alive", dead)
    fake, calls = _claude_only_bot(FakeStore("claude"))
    message = _message("이어서")
    _run(fake, message)
    assert calls == [("spawn", "9", message)]
    assert "다시 연결" in message.channel.sent[0]


def test_live_session_stays_silent(monkeypatch):
    async def alive(_):
        return True

    monkeypatch.setattr(supervisor, "responder_alive", alive)
    fake, calls = _claude_only_bot(FakeStore("claude"))
    _run(fake, _message("이어서"))
    assert calls == []


def test_pending_message_is_injected_with_thread_header(monkeypatch):
    bodies = []

    async def inject(thread_id, body):
        bodies.append(body)
        return True

    monkeypatch.setattr(supervisor, "inject_message", inject)
    channel = FakeChannel()
    asyncio.run(bot_module.HandoffBot._inject_pending(None, "9", channel, _message("요약 좀 해줘", mid=7)))
    assert bodies == ["[discord thread=9 msg=7]\n요약 좀 해줘"]
    assert channel.sent == []
