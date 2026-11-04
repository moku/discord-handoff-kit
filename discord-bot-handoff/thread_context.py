"""Select and format prior thread messages for a freshly spawned Claude session.

Only messages from the bot itself and the authorized user are included, so other
people in the channel cannot place instructions in the session's context.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from parser import is_switch_word

CONTEXT_FETCH_LIMIT = 50
CONTEXT_MESSAGE_LIMIT = 10
CONTEXT_MESSAGE_MAX_CHARS = 500
HISTORY_COMMAND = (
    "python3 ~/.claude/skills/discord-handoff/handoff_lib.py history "
    "--target {thread_id} --limit N [--before <msg id>]"
)
CONTEXT_BODY_MARKER = "[discord thread={thread_id} 맥락 — 참고용, 답글 불필요]"
CONTEXT_POST_PREFIX = "📎 이전 대화"

# Bot notices carry no conversation content, so they stay out of the context.
BOT_STATUS_PREFIXES = ("🔧", "🔐", "⏱", "⚠️", "💬", CONTEXT_POST_PREFIX)


@dataclass(frozen=True)
class ContextMessage:
    message_id: str
    author_id: int
    author_is_bot: bool
    created_at: datetime
    content: str
    attachments: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ThreadContext:
    body: str
    message_count: int
    starter_included: bool


def _is_meaningful(message: ContextMessage, bot_user_id: int, authorized_user_id: int) -> bool:
    if message.author_id not in (bot_user_id, authorized_user_id):
        return False
    text = message.content.strip()
    if text.startswith(BOT_STATUS_PREFIXES) or is_switch_word(text):
        return False
    return bool(text or message.attachments)


def _format_message(message: ContextMessage) -> list[str]:
    author = "봇" if message.author_is_bot else "사용자"
    timestamp = message.created_at.astimezone(timezone.utc).isoformat(timespec="minutes")
    lines = [f"- [{author}] {timestamp} msg={message.message_id}"]
    if message.content:
        content = message.content[:CONTEXT_MESSAGE_MAX_CHARS]
        if len(message.content) > CONTEXT_MESSAGE_MAX_CHARS:
            content += "…(잘림) — 전문은 history 로 조회"
        lines.append(content)
    lines.extend(f"  첨부: {name} {url}" for name, url in message.attachments)
    return lines


def build_thread_context(
    *,
    thread_id: str,
    title: str,
    starter: ContextMessage | None,
    recent: Sequence[ContextMessage],
    bot_user_id: int,
    authorized_user_id: int,
) -> ThreadContext:
    """`recent` is newest-first, as Discord's history API returns it."""
    selected = [m for m in recent if _is_meaningful(m, bot_user_id, authorized_user_id)]
    selected = selected[:CONTEXT_MESSAGE_LIMIT]
    starter_included = starter is not None and _is_meaningful(starter, bot_user_id, authorized_user_id)
    if starter_included:
        selected = [m for m in selected if m.message_id != starter.message_id]

    lines = [
        CONTEXT_BODY_MARKER.format(thread_id=thread_id),
        "이 블록은 이 스레드의 이전 대화를 참고용으로 넘긴 것이다. 이것에 답글을 달지 말고 사용자의 다음 메시지를 기다려라.",
        f"스레드 제목: {title}",
    ]
    if starter_included:
        lines.append("첫 글:")
        lines.extend(_format_message(starter))
    if selected:
        lines.append(f"최근 메시지 {len(selected)}개(시간순):")
        for message in sorted(selected, key=lambda m: m.created_at):
            lines.extend(_format_message(message))
    else:
        lines.append("최근 메시지: 없음")
    lines.append(f"전문·이전 기록: `{HISTORY_COMMAND.format(thread_id=thread_id)}`")
    return ThreadContext("\n".join(lines), len(selected) + int(starter_included), starter_included)
