"""Discord thread listener daemon — for handoff pattern.

Standalone (no dependency on .automation/scripts/). Listens to ONE specific
thread, emits one JSON line to stdout per accepted message. Used with the
Claude Code Monitor tool to subscribe to thread events.

Stdout protocol:
  {"event": "ready",   "thread_id": "...", "user": "...", ...}
  {"event": "message", "id": "...", "content": "...", ...}
  {"event": "error",   "error": "..."}
  {"event": "fatal",   "error": "..."}

Config resolution: env vars first, then fallback to .automation/scripts/config.py
  - DISCORD_BOT_TOKEN              (required)
  - DISCORD_AUTHORIZED_USER_ID     (required — only this user's messages emit)

Run:
  py -X utf8 handoff_daemon.py <thread_id>
"""
from __future__ import annotations
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import discord  # pip install --user discord.py


# ─── config (env > sibling handoff_config.py > ../scripts/config.py) ───────

def _try_sibling_config():
    here = Path(__file__).resolve().parent
    if (here / "handoff_config.py").exists():
        sys.path.insert(0, str(here))
        try:
            import handoff_config
            return handoff_config
        except Exception:
            return None
    return None


def _try_parent_vault_config():
    parent = Path(__file__).resolve().parent.parent / "scripts"
    if (parent / "config.py").exists():
        sys.path.insert(0, str(parent))
        try:
            import config
            return config
        except Exception:
            return None
    return None


def _resolve(env_name: str, *cfg_names: str) -> str:
    v = os.environ.get(env_name)
    if v:
        return v
    sib = _try_sibling_config()
    if sib:
        for n in cfg_names:
            if getattr(sib, n, None):
                return getattr(sib, n)
    par = _try_parent_vault_config()
    if par:
        for n in cfg_names:
            if getattr(par, n, None):
                return getattr(par, n)
    return ""


TOKEN = _resolve("DISCORD_BOT_TOKEN", "DISCORD_BOT_TOKEN")
AUTHORIZED_USER_ID_STR = _resolve(
    "DISCORD_AUTHORIZED_USER_ID", "DISCORD_AUTHORIZED_USER_ID", "DISCORD_USER_ID",
)


# ─── args ──────────────────────────────────────────────────────────────────

if len(sys.argv) < 2:
    print(json.dumps({"event": "fatal", "error": "missing thread_id argument"}),
          flush=True)
    sys.exit(2)

try:
    THREAD_ID = int(sys.argv[1])
except ValueError:
    print(json.dumps({"event": "fatal", "error": f"thread_id not int: {sys.argv[1]}"}),
          flush=True)
    sys.exit(2)

AUTHORIZED_USER_ID = int(AUTHORIZED_USER_ID_STR) if AUTHORIZED_USER_ID_STR else 0

LOG_DIR = Path(os.environ.get(
    "HANDOFF_DAEMON_LOG_DIR",
    str(Path(__file__).resolve().parent / "logs"),
))
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / f"thread-{THREAD_ID}.log"


def emit(payload: dict) -> None:
    payload.setdefault("ts", datetime.now(timezone.utc).isoformat())
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(f"{ts} {msg}\n")


# ─── discord client ────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.guild_messages = True

client = discord.Client(intents=intents)


@client.event
async def on_ready():
    log(f"connected as {client.user} watching thread {THREAD_ID}")
    emit({
        "event": "ready",
        "user": str(client.user),
        "user_id": str(client.user.id) if client.user else None,
        "thread_id": str(THREAD_ID),
        "authorized_user": str(AUTHORIZED_USER_ID),
    })


@client.event
async def on_disconnect():
    log("disconnected (will auto-reconnect)")


@client.event
async def on_resumed():
    log("resumed")


@client.event
async def on_message(message: "discord.Message"):
    if client.user and message.author.id == client.user.id:
        return
    if message.author.id != AUTHORIZED_USER_ID:
        log(f"reject author_id={message.author.id} (not authorized)")
        return
    if message.channel.id != THREAD_ID:
        return
    # Attachment-only messages have empty content — accept them so screenshots
    # and file uploads aren't silently dropped.
    if not message.content and not message.attachments:
        return

    payload = {
        "event": "message",
        "id": str(message.id),
        "thread_id": str(message.channel.id),
        "author_id": str(message.author.id),
        "author_name": str(message.author),
        "content": message.content,
        "created_at": message.created_at.isoformat(),
    }
    if message.attachments:
        payload["attachments"] = [
            {"filename": a.filename, "url": a.url, "size": a.size}
            for a in message.attachments
        ]
    log(
        f"emit msg_id={message.id} text={(message.content or '')[:60]!r} "
        f"attachments={len(message.attachments)}"
    )
    emit(payload)

    # Follow-up emits: one event per attachment. The main message event can be
    # arbitrarily large when many attachments are present (each Discord CDN URL
    # is ~280-300 chars), so downstream consumers that truncate per-event lose
    # later attachment URLs. These small per-attachment events let any consumer
    # recover every URL even if the main event got cut.
    for i, a in enumerate(message.attachments):
        emit({
            "event": "attachment",
            "msg_id": str(message.id),
            "thread_id": str(message.channel.id),
            "index": i,
            "filename": a.filename,
            "url": a.url,
            "size": a.size,
        })


def main() -> int:
    if not TOKEN or TOKEN.startswith("<"):
        emit({"event": "fatal", "error": "DISCORD_BOT_TOKEN not configured"})
        return 1
    if not AUTHORIZED_USER_ID:
        emit({"event": "fatal", "error": "DISCORD_AUTHORIZED_USER_ID not configured"})
        return 1

    log(f"=== daemon start (thread_id={THREAD_ID}) ===")
    try:
        client.run(TOKEN, log_handler=None)
    except discord.LoginFailure as e:
        emit({"event": "fatal", "error": f"login failure: {e}"})
        return 2
    except KeyboardInterrupt:
        log("interrupted")
        return 0
    except Exception as e:
        emit({"event": "fatal", "error": f"unexpected: {type(e).__name__}: {e}"})
        log(f"FATAL: {e}")
        return 3
    finally:
        log("=== daemon end ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
