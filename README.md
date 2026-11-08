# discord-handoff-kit

Continue a **Claude Code** session from Discord (phone, laptop, anywhere).

Four pieces. `thread-context` builds on `discord-handoff`; the bot needs both for its context features:

| Folder | What it does | Needs |
|---|---|---|
| [`discord-handoff/`](discord-handoff/) | Claude Code **skill**: create a Discord thread for the current task, post progress, and receive your replies (listener daemon or bot-injected input). | Python 3.10+, `discord.py`, a Discord bot token |
| [`discord-bot-handoff/`](discord-bot-handoff/) | Runnable **Discord bot**: answers with any OpenAI-compatible LLM (local or cloud); type `claude` in a thread to escalate it to a tmux Claude Code session (which then uses `discord-handoff`). `local` switches back. With no LLM configured, every thread goes straight to Claude. | the above + `tmux`, `claude` CLI; an LLM endpoint is optional |
| [`thread-context/`](thread-context/) | Claude Code **skill**: read another Discord thread, or ask that thread's live Claude session directly. Read-only: it never posts there or gives that session work. | `discord-handoff`, plus `DISCORD_GUILD_ID` for title search |
| [`session-effort/`](session-effort/) | Claude Code **skill**: change a running session's effort level (`low` … `max`) for that session only, without touching your saved default. Works on the current session or on a bot-spawned thread session. | `tmux`, Python 3.10+ |

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
   ln -s ~/discord-handoff-kit/session-effort ~/.claude/skills/session-effort             # optional
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
5. Continue with [Usage](#usage).

## What you can do

- **Move a running session to Discord.** Claude Code keeps working on your machine, posts progress to a thread, and acts on what you reply from your phone or laptop.
- **Send files both ways.** Claude can attach reports, logs and screenshots. Files you attach in the thread are downloaded so Claude can read them.
- **Start Claude from a chat.** Run the optional bot: an LLM of your choice answers by default, and typing `claude` in a thread starts a Claude Code session there with the thread's earlier messages as context. No LLM? Leave it unset and every thread talks to Claude directly.
- **Reuse another thread's context.** Find a thread by title, mention or link. If its Claude session is still running, ask that session; otherwise read the message history.
- **Change effort without changing your default.** Ask a running session to switch to `high` or `low`; only that session changes.

## Usage

### Hand off the current session

In a running Claude Code session, say:

```
hand this off to Discord, parent channel <channel_id>
```

Claude creates a thread in that channel (text, announcement, forum and media channels all work), posts a summary, and starts listening. From then on, write in the thread as you would in the terminal:

```
how far along are you?
also add tests for the parser
stop for now
show me the diff
```

To go back to a thread you already have, give its id instead: `resume Discord handoff in thread <thread_id>`.

Claude Code has to stay running on your machine. Closing the session ends the conversation, though the thread stays in Discord.

### Escalate from the bot

Start the bot (setup: [`discord-bot-handoff/README.md`](discord-bot-handoff/README.md)), then in a watched channel or thread:

| You type | What happens |
|---|---|
| any message | The LLM answers. |
| `claude` or `클로드` | A tmux Claude Code session starts for this thread and takes over. The bot goes quiet. |
| `local` or `로컬` | The Claude session is stopped and the LLM answers again. |

Only an exact one-word message switches modes, so mentioning "claude" in a sentence does nothing. If the Claude session dies, the next message returns the thread to the LLM.

Without an LLM (`LOCAL_LLM_BASE_URL` left blank), any message in a thread starts a Claude session and is handed to it; `local` just ends the session, and the next message starts a new one.

### Use another thread's context

From any Claude Code session with `thread-context` installed:

```
what did we decide in the "search rewrite" thread?
read <#1234567890> and use it for this task
```

It only reads. It never posts in the other thread or gives that session work. Searching by title needs `DISCORD_GUILD_ID`; mentions and links don't.

### Change effort mid-conversation

With `session-effort` installed, tell the Claude session in a thread:

```
set effort to high
what effort are you on?
```

It opens the `/effort` picker in that session's tmux pane and presses `s` (this session only), so your default for new sessions stays as it is. Typing `/effort high` directly would save it as the default. The script waits until the session is idle and refuses to type if a draft is in the input box. It reads Claude Code's on-screen picker, so a CLI update that changes that screen can break it.

From the command line: `python3 session-effort/set_effort.py high --target <thread_id>` (or `--show`).

### Command line

The skills call these for you, and you can run them directly from `discord-handoff/`:

```bash
python3 handoff_lib.py send --target <id> --text "done" --file report.md   # --file repeats, max 10
python3 handoff_lib.py create-thread --parent <channel_id> --name "task" --starter "summary"
python3 handoff_lib.py history --target <id> --limit 20 [--before <msg_id>] [--max-chars 500]
python3 handoff_lib.py fetch-attachment --url <discord_cdn_url>
```

## Security

- Each user runs **their own bot** with their own token.
- `DISCORD_AUTHORIZED_USER_ID` restricts who the bot/listener obeys — set it.
- The handed-off Claude session can run shell commands on your machine; only
  authorize yourself.

## Language

Skills, bot messages and session instructions default to **Korean**. To use another language, change these (or ask your coding agent to):

- `discord-handoff/SKILL.md`, `discord-bot-handoff/SKILL.md`, `thread-context/SKILL.md`, `session-effort/SKILL.md`: descriptions, trigger phrases and instructions. Translate these first, because English requests may not trigger the skills until you do.
- `discord-bot-handoff/supervisor.py`: instructions given to the Claude session
- `discord-bot-handoff/bot.py`, `discord-bot-handoff/thread_context.py`: messages and context markers
- `discord-handoff/handoff_lib.py`: the truncation notice

`discord-bot-handoff/tests/test_thread_context.py` and `thread-context/tests/test_history_parity.py` contain Korean text, so update any that assert on strings you change. The English keyword `claude` already works alongside `클로드`.
