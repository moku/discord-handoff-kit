# discord-handoff-kit

Continue a **Claude Code** session from Discord (phone, laptop, anywhere).

Three pieces. `thread-context` builds on `discord-handoff`; the bot needs both for its context features:

| Folder | What it does | Needs |
|---|---|---|
| [`discord-handoff/`](discord-handoff/) | Claude Code **skill**: create a Discord thread for the current task, post progress, and receive your replies (listener daemon or bot-injected input). | Python 3.10+, `discord.py`, a Discord bot token |
| [`discord-bot-handoff/`](discord-bot-handoff/) | Runnable **Discord bot**: answers with any OpenAI-compatible local LLM; type `claude` in a thread to escalate it to a tmux Claude Code session (which then uses `discord-handoff`). `local` switches back. | the above + `tmux`, `claude` CLI, an LLM endpoint |
| [`thread-context/`](thread-context/) | Claude Code **skill**: read another Discord thread, or ask that thread's live Claude session directly. Read-only: it never posts there or gives that session work. | `discord-handoff`, plus `DISCORD_GUILD_ID` for title search |

When the bot switches a thread to Claude, it also passes the thread title and earlier messages to the new session, so you can pick up mid-conversation. Only your own and the bot's messages are passed, and only when `DISCORD_AUTHORIZED_USER_ID` is set.

## Quick start

1. **Create your own Discord bot** (Developer Portal → New Application → Bot →
   Reset Token, enable *MESSAGE CONTENT INTENT*, invite it to your server).
   Details: [`discord-handoff/README.md`](discord-handoff/README.md#setup).
2. **Install the skill** so Claude Code can find it:
   ```bash
   git clone <this-repo> ~/discord-handoff-kit
   mkdir -p ~/.claude/skills
   ln -s ~/discord-handoff-kit/discord-handoff ~/.claude/skills/discord-handoff
   ln -s ~/discord-handoff-kit/discord-bot-handoff ~/.claude/skills/discord-bot-handoff   # optional
   ln -s ~/discord-handoff-kit/thread-context ~/.claude/skills/thread-context             # optional
   pip install -r ~/discord-handoff-kit/discord-handoff/requirements.txt
   ```
3. **Credentials** — env vars only, never commit them:
   ```bash
   export DISCORD_BOT_TOKEN=...            # your bot token
   export DISCORD_AUTHORIZED_USER_ID=...   # your Discord user id (Developer Mode → Copy User ID)
   export DISCORD_GUILD_ID=...             # your server id, for thread-context title search
   ```
   For the bot, copy `discord-bot-handoff/discord.env.example` to
   `~/.config/discord-bot-handoff/discord.env` (chmod 600) and fill it in.
4. **Smoke test**
   ```bash
   python3 ~/.claude/skills/discord-handoff/handoff_lib.py send --target <channel_id> --text "test"
   ```
5. In Claude Code, say e.g. "핸드오프 해줘" / "hand this off to Discord", or run
   the bot (see [`discord-bot-handoff/README.md`](discord-bot-handoff/README.md)).

## Security

- Each user runs **their own bot** with their own token.
- `DISCORD_AUTHORIZED_USER_ID` restricts who the bot/listener obeys — set it.
- The handed-off Claude session can run shell commands on your machine; only
  authorize yourself.
