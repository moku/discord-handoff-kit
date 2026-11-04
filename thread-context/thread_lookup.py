#!/usr/bin/env python3
"""Find Discord threads by title and read their recent messages.

Read-only helper for the thread-context skill. Reuses the discord-handoff
token resolution (env DISCORD_BOT_TOKEN, then sibling config).

  thread_lookup.py find "<title words>" [--guild <id>] [--limit 8]
  thread_lookup.py history <thread_id> [--limit 20] [--before <msg_id>] [--max-chars 500]
  thread_lookup.py sessions              # live Claude sessions bound to threads (tmux agent-<tid>)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path

_here = Path(__file__).resolve().parent
_sibling_handoff = _here.parent / "discord-handoff"
sys.path.insert(0, str(_sibling_handoff if (_sibling_handoff / "handoff_lib.py").is_file()
                       else Path.home() / ".claude/skills/discord-handoff"))
import handoff_lib  # noqa: E402

API = "https://discord.com/api/v10"
# Server to search by title. A <#id> mention or link needs no server id.
DEFAULT_GUILD = os.environ.get("DISCORD_GUILD_ID", "")
_THREAD_TYPES = {10, 11, 12}


class DiscordError(RuntimeError):
    pass


def _get(path: str, _retries: int = 3) -> object:
    token = handoff_lib._resolve_token()
    req = urllib.request.Request(
        f"{API}{path}",
        headers={"Authorization": f"Bot {token}", "User-Agent": "thread-context (1.0)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        if e.code == 429 and _retries > 0:
            try:
                wait = float(json.loads(body).get("retry_after", 1.0))
            except ValueError:
                wait = 1.0
            time.sleep(min(wait, 10.0) + 0.1)
            return _get(path, _retries - 1)
        raise DiscordError(f"discord GET {path} failed: {e.code} {body[:200]}") from e


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def _score(query: str, title: str) -> float:
    q, t = _norm(query), _norm(title)
    if not q:
        return 0.0
    if q in t:
        return 1.0 + len(q) / max(len(t), 1)
    words = [w for w in query.lower().split() if w]
    hit = sum(1 for w in words if w in title.lower()) / max(len(words), 1)
    return max(hit * 0.9, SequenceMatcher(None, q, t).ratio())


def _all_threads(guild: str) -> list[dict]:
    active = _get(f"/guilds/{guild}/threads/active")
    threads = list(active.get("threads", [])) if isinstance(active, dict) else []
    parents = {t.get("parent_id") for t in threads if t.get("parent_id")}
    channels = _get(f"/guilds/{guild}/channels")
    if isinstance(channels, list):
        parents |= {c["id"] for c in channels if c.get("type") in (0, 5, 15, 16)}
    seen = {t["id"] for t in threads}

    def archived(pid: str) -> list[dict]:
        try:
            arch = _get(f"/channels/{pid}/threads/archived/public?limit=100")
        except DiscordError:
            return []  # bot lacks access to this parent channel
        return list(arch.get("threads", [])) if isinstance(arch, dict) else []

    with ThreadPoolExecutor(max_workers=8) as pool:
        for batch in pool.map(archived, sorted(parents)):
            for t in batch:
                if t["id"] not in seen:
                    seen.add(t["id"])
                    threads.append(t)
    return threads


def _live_sessions() -> dict[str, str]:
    """thread_id -> tmux session name for live handoff sessions."""
    try:
        out = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}\t#{session_activity}"],
            capture_output=True, text=True, timeout=5, check=False,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    live: dict[str, str] = {}
    for line in out.splitlines():
        name = line.split("\t", 1)[0]
        m = re.fullmatch(r"agent-(\d+)", name)
        if m:
            live[m.group(1)] = name
    return live


def cmd_find(args: argparse.Namespace) -> None:
    mention = re.search(r"<#(\d+)>|channels/\d+/(\d+)", args.query)
    live = _live_sessions()
    if mention:
        tid = mention.group(1) or mention.group(2)
        ch = _get(f"/channels/{tid}")
        rows = [ch] if isinstance(ch, dict) else []
    else:
        if not args.guild:
            sys.exit("set DISCORD_GUILD_ID or pass --guild to search threads by title")
        threads = _all_threads(args.guild)
        rows = sorted(threads, key=lambda t: _score(args.query, t.get("name", "")), reverse=True)
        exact = [t for t in rows if _score(args.query, t.get("name", "")) >= 1.0]
        rows = exact or [t for t in rows if _score(args.query, t.get("name", "")) >= 0.5]
        rows = rows[: args.limit]
    for t in rows:
        meta = t.get("thread_metadata") or {}
        print(json.dumps({
            "id": t["id"],
            "title": t.get("name"),
            "archived": meta.get("archived", False),
            "last_message_id": t.get("last_message_id"),
            "live_session": live.get(t["id"]),
        }, ensure_ascii=False))
    if not rows:
        print("no matching thread", file=sys.stderr)


def cmd_history(args: argparse.Namespace) -> None:
    result = handoff_lib.fetch_history(args.thread_id, limit=args.limit,
                                       before=args.before, max_chars=args.max_chars)
    if not result["ok"]:
        sys.exit(result["error"])
    for line in result["lines"]:
        print(line)


def cmd_sessions(_: argparse.Namespace) -> None:
    for tid, name in sorted(_live_sessions().items()):
        print(f"{tid}\t{name}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find")
    f.add_argument("query")
    f.add_argument("--guild", default=DEFAULT_GUILD)
    f.add_argument("--limit", type=int, default=8)
    f.set_defaults(fn=cmd_find)
    h = sub.add_parser("history")
    h.add_argument("thread_id")
    h.add_argument("--limit", type=int, default=20)
    h.add_argument("--before")
    h.add_argument("--max-chars", type=int, default=500)
    h.set_defaults(fn=cmd_history)
    s = sub.add_parser("sessions")
    s.set_defaults(fn=cmd_sessions)
    args = p.parse_args()
    try:
        args.fn(args)
    except DiscordError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
