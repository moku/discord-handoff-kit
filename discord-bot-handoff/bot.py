"""Runnable reference Discord bot with Claude-handoff escalation.

Flow per message (in a watched channel/thread, from the authorized user):

  1. `detect_switch` — exact one-word match on the message.
       * "claude"/"클로드"  → spawn a tmux Claude Code session bound to this
         thread (via `supervisor.spawn_claude_handoff`) and go silent.
       * "local"/"로컬"    → kill the Claude session, resume answering locally.
     A normal task/question that merely *mentions* the word never flips.

  2. In "claude" mode the bot stays silent — the handoff daemon (from the
     discord-handoff skill, running inside the tmux session) replies in the
     thread directly. If that session has died (crash / host reboot), the next
     message auto-recovers the thread back to "local".

  3. In "local" mode the bot answers with the local LLM (any OpenAI-compatible
     endpoint), carrying a short per-thread history.

This bot brings ONLY the escalation layer + a local-LLM responder. Swap the
LLM endpoint via env (see README). Config comes from `config.Config.from_env`.

Run:  python -X utf8 bot.py
"""

from __future__ import annotations

import asyncio
import logging

import discord

import supervisor
from config import Config
from local_llm import LocalLLM, LocalLLMError
from parser import detect_switch
from store import Store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("discord-bot-handoff")


class HandoffBot(discord.Client):
    def __init__(self, cfg: Config) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self._cfg = cfg
        self._store = Store(cfg.store_path, history_turns=cfg.history_turns)
        self._llm = LocalLLM(
            cfg.llm_base_url,
            cfg.llm_model,
            api_key=cfg.llm_api_key,
            max_tokens=cfg.llm_max_tokens,
            temperature=cfg.llm_temperature,
            timeout_s=cfg.llm_timeout_s,
        )

    async def setup_hook(self) -> None:
        await self._store.init()

    async def on_ready(self) -> None:
        log.info("logged in as %s (watching channels=%s)",
                 self.user, self._cfg.watched_channels or "ALL")

    # ── gating ────────────────────────────────────────────────────────────

    def _is_watched(self, message: discord.Message) -> bool:
        if message.author.bot:
            return False
        if self._cfg.authorized_user_id and message.author.id != self._cfg.authorized_user_id:
            return False
        if self._cfg.watched_channels:
            # Match the channel itself or its parent (thread → parent channel).
            cid = message.channel.id
            parent_id = getattr(message.channel, "parent_id", None)
            if cid not in self._cfg.watched_channels and parent_id not in self._cfg.watched_channels:
                return False
        return True

    # ── main handler ────────────────────────────────────────────────────────

    async def on_message(self, message: discord.Message) -> None:
        if message.author == self.user:
            return
        if not self._is_watched(message):
            return

        thread_id = str(message.channel.id)
        content = message.content.strip()
        if not content:
            return

        # 1. Switch triggers — checked BEFORE anything else. Exact one-word only.
        active = await self._store.get_active(thread_id)
        switch = detect_switch(content, active)
        if switch == "to_claude":
            await self._switch_to_claude(thread_id, message.channel)
            return
        if switch == "to_local":
            await self._switch_to_local(thread_id, message.channel)
            return

        # 2. Claude mode → bot silent unless the session died (auto-recover).
        if active == "claude":
            if await supervisor.responder_alive(thread_id):
                return  # the handoff daemon is replying
            await self._store.set_active(thread_id, "local")
            await message.channel.send(
                "⚠️ Claude 세션이 종료된 것 같아 로컬 모델로 돌아왔어요."
            )
            active = "local"

        # 3. Local mode → answer with the local LLM.
        await self._reply_local(thread_id, message, content)

    async def _reply_local(
        self, thread_id: str, message: discord.Message, content: str
    ) -> None:
        try:
            await message.add_reaction("👀")
        except discord.HTTPException:
            pass
        history = await self._store.get_history(thread_id)
        messages = (
            [{"role": "system", "content": self._cfg.llm_system_prompt}]
            + history
            + [{"role": "user", "content": content}]
        )
        try:
            async with message.channel.typing():
                reply = await self._llm.complete(messages)
        except LocalLLMError as exc:
            log.warning("local LLM failed for thread=%s: %s", thread_id, exc)
            await message.channel.send(
                "⚠️ 로컬 모델 응답에 실패했어요. endpoint 상태를 확인해 주세요.\n"
                f"```{exc}```"
            )
            return
        await self._store.append_turn(thread_id, "user", content)
        await self._store.append_turn(thread_id, "assistant", reply)
        for chunk in _chunk(reply):
            await message.channel.send(chunk)

    # ── switch helpers ────────────────────────────────────────────────────

    async def _switch_to_claude(
        self, thread_id: str, channel: discord.abc.Messageable
    ) -> None:
        await channel.send("🔧 Claude를 부르는 중… (세션 준비에 ~25초)")
        try:
            result = await supervisor.spawn_claude_handoff(thread_id)
        except Exception as exc:  # noqa: BLE001 — surface, don't crash the bot
            log.exception("handoff spawn failed for thread=%s", thread_id)
            await channel.send(
                "⚠️ Claude 세션 생성 중 예기치 못한 오류가 났어요.\n"
                f"```{type(exc).__name__}: {exc}```"
            )
            return

        if result.ok:
            await self._store.set_active(thread_id, "claude")
            await channel.send(
                "🔧 **Claude Code 연결됨.** 잠시 후 Claude가 인사할 거예요. "
                "파일·스크린샷 첨부 가능. `local`이라고만 보내면 로컬 모델로 돌아옵니다."
            )
            return

        if result.status == "login_required":
            await channel.send(
                "🔐 **Claude 로그인이 필요해요.**\n"
                "봇이 도는 호스트에서 직접:\n```\nclaude /login\n```\n"
                "끝나면 다시 `claude` 보내면 연결됩니다."
            )
        elif result.status == "boot_timeout":
            await channel.send(
                "⏱ **Claude가 30초 안에 준비되지 않았어요.**\n"
                "보통 첫 부팅 지연이에요. `claude` 한 번 더 보내면 재시도합니다."
            )
        else:
            await channel.send(
                "⚠️ **Claude 세션을 시작하지 못했어요.**\n"
                f"이유: `{result.reason or '알 수 없음'}`\n"
                "다시 시도하려면 `claude` 보내주세요."
            )

        if result.pane_excerpt and result.status in ("login_required", "boot_timeout"):
            snippet = result.pane_excerpt
            if len(snippet) > 1700:
                snippet = "…\n" + snippet[-1700:]
            await channel.send(f"```\n{snippet}\n```")

    async def _switch_to_local(
        self, thread_id: str, channel: discord.abc.Messageable
    ) -> None:
        await channel.send("💬 로컬 모델로 돌아가는 중…")
        try:
            await supervisor.stop_responder(thread_id)
        except Exception:  # noqa: BLE001
            log.exception("handoff stop failed for thread=%s", thread_id)
        await self._store.set_active(thread_id, "local")
        await channel.send("💬 **로컬 모델로 전환됐어요.** (Claude 세션 종료)")


def _chunk(text: str, limit: int = 1900) -> list[str]:
    """Split a reply into <=limit-char pieces on line boundaries where possible
    (Discord hard-caps a message at 2000 chars)."""
    if len(text) <= limit:
        return [text or "(빈 응답)"]
    out: list[str] = []
    buf = ""
    for line in text.splitlines(keepends=True):
        if len(buf) + len(line) > limit:
            if buf:
                out.append(buf)
            # a single very long line: hard-slice it
            while len(line) > limit:
                out.append(line[:limit])
                line = line[limit:]
            buf = line
        else:
            buf += line
    if buf:
        out.append(buf)
    return out


def main() -> None:
    cfg = Config.from_env()
    HandoffBot(cfg).run(cfg.bot_token, log_handler=None)


if __name__ == "__main__":
    main()
