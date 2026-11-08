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

  3. In "local" mode the bot answers with the LLM (any OpenAI-compatible
     endpoint, local or cloud), carrying a short per-thread history.

  Claude-only mode: with `LOCAL_LLM_BASE_URL` left blank there is no LLM, so
  any message in a thread without a live session spawns one and hands that
  message to it.

This bot brings ONLY the escalation layer + an LLM responder. Swap the
LLM endpoint via env (see README). Config comes from `config.Config.from_env`.

Run:  python -X utf8 bot.py
"""

from __future__ import annotations

import asyncio
import logging

import discord

import supervisor
import thread_context
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
        ) if cfg.llm_enabled else None

    async def setup_hook(self) -> None:
        await self._store.init()

    async def on_ready(self) -> None:
        log.info("logged in as %s (watching channels=%s, llm=%s)",
                 self.user, self._cfg.watched_channels or "ALL",
                 self._cfg.llm_base_url or "none (claude-only)")

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
            if self._llm is None:
                await message.channel.send("⚠️ Claude 세션이 종료된 것 같아 다시 연결할게요.")
                await self._switch_to_claude(thread_id, message.channel, pending=message)
                return
            await message.channel.send(
                "⚠️ Claude 세션이 종료된 것 같아 로컬 모델로 돌아왔어요."
            )
            active = "local"

        # 3. Local mode → answer with the LLM, or with Claude when there is none.
        if self._llm is None:
            await self._switch_to_claude(thread_id, message.channel, pending=message)
            return
        await self._reply_local(thread_id, message, content)

    async def _reply_local(
        self, thread_id: str, message: discord.Message, content: str
    ) -> None:
        assert self._llm is not None
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
        self,
        thread_id: str,
        channel: discord.abc.Messageable,
        pending: discord.Message | None = None,
    ) -> None:
        """Spawn the thread's Claude session; `pending` is a message it should answer."""
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
            back = (
                "`local`이라고만 보내면 로컬 모델로 돌아옵니다."
                if self._llm is not None
                else "`local`이라고만 보내면 세션을 끝냅니다."
            )
            await channel.send(
                "🔧 **Claude Code 연결됨.** 잠시 후 Claude가 인사할 거예요. "
                f"파일·스크린샷 첨부 가능. {back}"
            )
            if not result.reused:
                await self._inject_thread_context(thread_id, channel, exclude=pending)
            if pending is not None:
                await self._inject_pending(thread_id, channel, pending)
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

    async def _inject_pending(
        self, thread_id: str, channel: discord.abc.Messageable, message: discord.Message
    ) -> None:
        """Hand the message that started the session to Claude as a normal request."""
        lines = [f"[discord thread={thread_id} msg={message.id}]", message.content]
        lines += [f"(첨부: {item.filename} {item.url})" for item in message.attachments]
        if not await supervisor.inject_message(thread_id, "\n".join(lines)):
            await channel.send("⚠️ 방금 메시지를 Claude 세션에 넘기지 못했어요. 한 번 더 보내 주세요.")

    async def _inject_thread_context(
        self,
        thread_id: str,
        channel: discord.abc.Messageable,
        exclude: discord.Message | None = None,
    ) -> None:
        """Hand the thread's title and earlier messages to the new session."""
        authorized = self._cfg.authorized_user_id
        if authorized is None:
            await channel.send(
                "⚠️ DISCORD_AUTHORIZED_USER_ID 가 없어 이전 대화를 Claude 세션에 넘기지 않았어요."
            )
            return
        try:
            starter = await self._fetch_starter(channel, thread_id)
            recent = [
                self._to_context_message(message)
                async for message in channel.history(limit=thread_context.CONTEXT_FETCH_LIMIT)
                if exclude is None or message.id != exclude.id
            ]
            ctx = thread_context.build_thread_context(
                thread_id=thread_id,
                title=getattr(channel, "name", ""),
                starter=self._to_context_message(starter) if starter else None,
                recent=recent,
                bot_user_id=self.user.id,
                authorized_user_id=authorized,
            )
            injected = await supervisor.inject_message(thread_id, ctx.body)
        except Exception as exc:  # noqa: BLE001 — the session still works without context
            log.exception("thread context failed for thread=%s", thread_id)
            await channel.send(f"⚠️ 이전 대화를 Claude 세션에 넘기지 못했어요({exc}).")
            return
        if not injected:
            await channel.send("⚠️ 이전 대화를 Claude 세션에 넘기지 못했어요(tmux).")
            return
        await channel.send(f"{thread_context.CONTEXT_POST_PREFIX} 맥락 전달: 메시지 {ctx.message_count}개")

    @staticmethod
    async def _fetch_starter(
        channel: discord.abc.Messageable, thread_id: str
    ) -> discord.Message | None:
        """A forum thread's first post shares its id; other threads keep it on the parent."""
        for source in (channel, getattr(channel, "parent", None)):
            fetch_message = getattr(source, "fetch_message", None)
            if fetch_message is None:
                continue
            try:
                return await fetch_message(int(thread_id))
            except discord.HTTPException:
                continue
        return None

    @staticmethod
    def _to_context_message(message: discord.Message) -> thread_context.ContextMessage:
        return thread_context.ContextMessage(
            message_id=str(message.id),
            author_id=message.author.id,
            author_is_bot=message.author.bot,
            created_at=message.created_at,
            content=message.content,
            attachments=tuple((item.filename, item.url) for item in message.attachments),
        )

    async def _switch_to_local(
        self, thread_id: str, channel: discord.abc.Messageable
    ) -> None:
        await channel.send(
            "💬 로컬 모델로 돌아가는 중…" if self._llm is not None else "💬 Claude 세션을 끝내는 중…"
        )
        try:
            await supervisor.stop_responder(thread_id)
        except Exception:  # noqa: BLE001
            log.exception("handoff stop failed for thread=%s", thread_id)
        await self._store.set_active(thread_id, "local")
        if self._llm is None:
            await channel.send(
                "💬 **Claude 세션을 끝냈어요.** LLM endpoint가 없어서 "
                "다음 메시지를 보내면 Claude를 다시 연결합니다."
            )
            return
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
