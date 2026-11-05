# Discord Handoff Skill for Claude Code

Generalized **Discord thread handoff** pattern for Claude Code: spin up an
ad-hoc Discord thread for any task, then continue the conversation asynchronously
from any device. Claude Code stays in your desktop session doing real work and
posts progress to the thread; you reply from your phone/laptop and it responds
in context.

This is a **standalone version** of the handoff skill — only the thread
listener + helper lib + SKILL prompt. No dependency on the rest of any
automation system.

## What's in here

```
.
├── handoff_lib.py             # send + create-thread (stdlib HTTPS, no deps)
├── handoff_daemon.py          # WebSocket listener for ONE thread (uses discord.py)
├── SKILL.md                   # Claude Code skill prompt (frontmatter + body)
├── handoff_config.py          # reads token + user_id from env vars (no secrets in repo)
├── requirements.txt           # just discord.py
├── .gitignore
└── README.md                  # this file
```

## Setup

### 1. Discord side (one-time)

1. https://discord.com/developers/applications → **New Application**
2. Side panel → **Bot** → **Reset Token** → save the token
3. Enable **MESSAGE CONTENT INTENT** (Privileged Gateway Intents), Save
4. **OAuth2** → **URL Generator** → Scopes: ☑ `bot`, Permissions: ☑ `Administrator`
   (or specific: View Channel + Send Messages + Read Message History + Create Public Threads)
5. Open the generated URL → invite the bot to your personal server
6. In Discord settings: Advanced → **Developer Mode** ON
7. Right-click your own name → **Copy User ID**
8. Decide which channel will be the **thread parent** (where new task threads
   spawn). Right-click that channel → **Copy Channel ID** — you'll pass it when
   invoking the skill.

### 2. Local install

```bash
git clone <this-repo> discord-handoff-skill
cd discord-handoff-skill

# Python 3.11+ recommended, stdlib only for handoff_lib; discord.py for daemon
pip install --user -r requirements.txt   # or: py -m pip install --user discord.py

# Credentials: export env vars (e.g. in your shell rc):
#   export DISCORD_BOT_TOKEN=...
#   export DISCORD_AUTHORIZED_USER_ID=...
# Never commit the token.
```

### 3. Smoke test

```bash
python3 handoff_lib.py send --target <parent_channel_id> --text "✅ test"
```
Message should appear in Discord.

## Usage from Claude Code

With the skill installed, say this in Claude Code:

```
hand this off to Discord, parent channel <YOUR_CHANNEL_ID>
```

Optional extras:
- `task_title: ...` (else auto-derived from current context)
- `initial_summary: ...` (else auto-drafted)
- `thread_id: ...` (to resume an existing thread instead of creating new)

Claude will:
1. Create a thread in the channel
2. Post a starter message
3. Choose how messages arrive — Monitor branch: start the daemon under Monitor
   (re-start it when Monitor expires); inject mode (the session prompt contains
   `[handoff-inject-mode]`): no listener, the bot types messages into the
   session. Details: SKILL.md STEP 3
4. Continue doing real work + post milestone updates to the thread
5. Respond to your replies in the thread, contextually

You can then close the desktop window confidently — as long as Claude Code is
running and the session is alive, the listener keeps going (Monitor branch;
in inject mode the bot keeps delivering into the live session).

## Files explained

**`handoff_lib.py`** — Two functions + CLI:
- `send(target_id, content)` — post a message to a channel or thread
- `create_thread(parent_id, name, starter)` — create a public thread (optionally
  anchored to a starter message)
- CLI: `send` subcommand and `create-thread` subcommand
- Stdlib only (no `requests`, no `discord.py`). Why? Sending is just one HTTP POST.

**`handoff_daemon.py`** — Long-running WebSocket listener for ONE thread:
- Connects to Discord Gateway via `discord.py`
- Filters to authorized user + the specific thread_id passed as argv
- Emits each accepted message as a JSON line to stdout (Monitor stream; used
  in the Monitor branch only)
- Auto-reconnects on transient disconnects (handled by discord.py)

**`SKILL.md`** — The SKILL prompt. Tells Claude Code how to:
- Create the thread, post intro, pick the receive branch (Monitor branch starts
  the daemon under Monitor; inject mode starts no listener)
- Process each user message as open-ended conversation (NOT fixed commands)
- Post proactive milestone updates between user messages
- Handle daemon lifecycle in the Monitor branch (fatal → restart once, then give up)

## Lifecycle notes

- In the Monitor branch the listener runs inside a Claude Code session.
  **Don't close the session** or the daemon dies with it.
- PC sleep / restart: daemon dies, but Discord buffers messages briefly during
  reconnect. After PC wakes, re-invoke the skill (with `thread_id: <existing>`)
  to resume that thread's listener.
- Thread auto-archives after Discord's configured idle interval (default 1 day).
  Auto-archived threads still receive new messages (they auto-unarchive).

## Why not webhooks?

Webhooks (push from Discord to your server) need a public HTTPS endpoint —
either a VPS or a tunnel (ngrok/Cloudflare). The Gateway WebSocket approach
used here:
- No public endpoint → no port forwarding, no certificates
- Outbound connection only (firewall-friendly)
- Same UX as webhooks (push-like real-time)

The downside is "one process must be running 24/7" — but if you're using
Claude Code regularly anyway, the listener session is just one extra window.

## Security

Token has full bot privileges in your server. Keep it in env vars (or an env
file outside the repo, chmod 600) — never commit it. `handoff_config.py` only
reads the env vars.

To revoke: Discord Developer Portal → your app → Bot → Reset Token.

## Adapting to other workflows

The SKILL is intentionally generic. You can use it for:
- Async coding tasks ("build feature X, ping me when done")
- Long-running analyses ("scrape this dataset and post insights periodically")
- Pair-programming-by-message
- Any task that benefits from "go do work and update me as you go"

For the fixed-command bot pattern (slash-like commands), build a different
skill — handoff is for open conversation.
