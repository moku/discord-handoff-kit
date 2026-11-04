"""Per-thread state for the bot (SQLite via aiosqlite).

Two things live here:

  * `thread_active` — who owns the thread right now: "local" (default, the bot
    answers with the local LLM) or "claude" (a handoff tmux session is live and
    the bot stays silent). Persisted so a bot restart resumes the prior mode
    instead of silently defaulting.

  * `history` — the last few (user + assistant) turns per thread, so the local
    LLM has short conversational memory. Trimmed to `history_turns` on read.

This is the reference "bring-your-own store" the supervisor docstring mentions.
Swap it for your own persistence if you already have one.
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite


class Store:
    def __init__(self, path: str, history_turns: int = 8) -> None:
        self._path = path
        self._history_turns = history_turns

    async def init(self) -> None:
        Path(self._path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS thread_active ("
                "thread_id TEXT PRIMARY KEY, "
                "active TEXT NOT NULL DEFAULT 'local', "
                "updated_at TEXT NOT NULL DEFAULT (datetime('now')))"
            )
            await db.execute(
                "CREATE TABLE IF NOT EXISTS history ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "thread_id TEXT NOT NULL, "
                "role TEXT NOT NULL, "
                "content TEXT NOT NULL, "
                "created_at TEXT NOT NULL DEFAULT (datetime('now')))"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_history_thread ON history (thread_id, id)"
            )
            await db.commit()

    # ── active mode ──────────────────────────────────────────────────────────

    async def get_active(self, thread_id: str) -> str:
        """Return 'local' (default) or 'claude' for this thread."""
        async with aiosqlite.connect(self._path) as db:
            async with db.execute(
                "SELECT active FROM thread_active WHERE thread_id = ?", (thread_id,)
            ) as cur:
                row = await cur.fetchone()
        return row[0] if row else "local"

    async def set_active(self, thread_id: str, active: str) -> None:
        """Persist active='claude'|'local' for this thread."""
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                "INSERT INTO thread_active (thread_id, active) VALUES (?, ?) "
                "ON CONFLICT(thread_id) DO UPDATE SET "
                "active = excluded.active, updated_at = datetime('now')",
                (thread_id, active),
            )
            await db.commit()

    # ── conversation history ───────────────────────────────────────────────

    async def append_turn(self, thread_id: str, role: str, content: str) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                "INSERT INTO history (thread_id, role, content) VALUES (?, ?, ?)",
                (thread_id, role, content),
            )
            await db.commit()

    async def get_history(self, thread_id: str) -> list[dict[str, str]]:
        """Return the last `history_turns` message pairs as chat messages.

        `history_turns` counts user+assistant pairs, so we fetch up to
        `2 * history_turns` rows and return them oldest-first.
        """
        limit = max(2, self._history_turns * 2)
        async with aiosqlite.connect(self._path) as db:
            async with db.execute(
                "SELECT role, content FROM history WHERE thread_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (thread_id, limit),
            ) as cur:
                rows = await cur.fetchall()
        return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

    async def clear_history(self, thread_id: str) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute("DELETE FROM history WHERE thread_id = ?", (thread_id,))
            await db.commit()
