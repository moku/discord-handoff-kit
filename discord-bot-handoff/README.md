# discord-bot-handoff

A **runnable reference Discord bot** that answers with an LLM and lets you
escalate any thread to a full Claude Code session by typing one word.

## Pick a setup

| You have | Set | What the bot does |
|---|---|---|
| A local model server (vLLM, Ollama, LM Studio, mlx_lm, llama.cpp) | `LOCAL_LLM_BASE_URL` = its `/v1` URL, `LOCAL_LLM_MODEL` | Local model answers; `claude` escalates. |
| A cloud API key (OpenAI, OpenRouter, Gemini, …) | `LOCAL_LLM_BASE_URL` = provider's OpenAI-compatible URL, `LOCAL_LLM_MODEL`, `LOCAL_LLM_API_KEY` | Cloud model answers; `claude` escalates. |
| Only a Claude Code subscription | `LOCAL_LLM_BASE_URL=` (blank) | **Claude-only mode**: every thread goes straight to a Claude session. |

The variables keep the `LOCAL_LLM_` prefix for compatibility, but any
OpenAI-compatible endpoint works, local or cloud. See
[Swapping the LLM endpoint](#swapping-the-llm-endpoint) for exact values.

Type `claude` (or `클로드`) in a watched thread and the bot:

1. Spawns a tmux Claude Code session for that thread.
2. The session follows [`../discord-handoff/`](../discord-handoff/)`SKILL.md` in
   RESUME mode → runs `handoff_daemon.py` and converses in the thread directly
   (attachments / screenshots / shell tools all available).
3. The bot stays silent while Claude is active; the exact word `local` (or
   `로컬`) flips back to the local model.

Having the bot type messages into the session pane instead (inject mode) is
supported by the `discord-handoff` SKILL STEP 3 inject-mode branch and the
marker `[handoff-inject-mode]`; this reference bot does not implement it.

Everything else is answered by your **LLM** — any OpenAI-compatible
endpoint, local or cloud. No MAF / graph service / project framework required;
the only backend assumption is one `/chat/completions` URL, and even that is
optional (see Claude-only mode below).

```
┌────────── Discord thread ──────────┐
│ user: "요약 좀 해줘"                │──▶ local LLM (/chat/completions)  ──▶ reply
│ user: "claude"                     │──▶ supervisor.spawn → tmux claude ──▶ (bot silent)
│ user: (chats with Claude…)         │◀── discord-handoff daemon replies
│ user: "local"                      │──▶ supervisor.stop → back to local LLM
└────────────────────────────────────┘
```

## Prerequisites

- `tmux` and the `claude` CLI (on PATH, or set `CLAUDE_BIN`)
- The [discord-handoff](../discord-handoff/) skill installed at
  `~/.claude/skills/discord-handoff/`
- Python 3.10+
- Optionally, one OpenAI-compatible chat endpoint, local or cloud (see below)
- A Discord bot token + your Discord user id

## Setup

```bash
cd discord-bot-handoff
pip install --user -r requirements.txt

mkdir -p ~/.config/discord-bot-handoff
cp discord.env.example ~/.config/discord-bot-handoff/discord.env
chmod 600 ~/.config/discord-bot-handoff/discord.env
$EDITOR ~/.config/discord-bot-handoff/discord.env   # fill token / user id / endpoint

python -X utf8 bot.py
```

Enable **Message Content Intent** for the bot in the Discord Developer Portal
(Bot → Privileged Gateway Intents), otherwise `message.content` arrives empty.

## Configuration

All config is read from the env file (`HANDOFF_ENV_FILE`, default
`~/.config/discord-bot-handoff/discord.env`) or the process environment
(exported vars win). See `discord.env.example` for the full list.

| Var | Required | Meaning |
|---|:---:|---|
| `DISCORD_BOT_TOKEN` | ✅ | Bot token. |
| `DISCORD_AUTHORIZED_USER_ID` | | Only respond to this user. Blank = anyone. |
| `WATCHED_CHANNELS` | | Comma/space-separated channel or parent ids. Blank = all visible. |
| `LOCAL_LLM_BASE_URL` | | Endpoint base (default `http://127.0.0.1:8000/v1`). Set it blank for Claude-only mode. |
| `LOCAL_LLM_MODEL` | | Model name the endpoint expects. |
| `LOCAL_LLM_API_KEY` | | Bearer token if the endpoint needs one. |
| `LOCAL_LLM_SYSTEM_PROMPT` | | System prompt for local replies. |
| `LOCAL_LLM_MAX_TOKENS` / `_TEMPERATURE` / `_TIMEOUT_S` | | Generation tuning. |
| `CLAUDE_BIN` | | Absolute path to `claude` if not on PATH. |
| `HANDOFF_CWD` | | Working dir for the spawned Claude session. |
| `HANDOFF_PROMPT` | | Override the boot prompt (keep the `{thread_id}` placeholder). |
| `STORE_PATH` / `HISTORY_TURNS` | | State DB path / remembered turns. |

## Swapping the LLM endpoint

The bot only speaks the OpenAI `/chat/completions` shape, so you retarget it by
changing **three** vars — no code edits. Set `LOCAL_LLM_BASE_URL` to the
endpoint's OpenAI-compatible base (the part ending in `/v1`), `LOCAL_LLM_MODEL`
to whatever that server advertises, and `LOCAL_LLM_API_KEY` only if it requires
auth.

| Backend | `LOCAL_LLM_BASE_URL` | `LOCAL_LLM_MODEL` | `LOCAL_LLM_API_KEY` |
|---|---|---|---|
| **vLLM** (`vllm serve … --served-model-name local-llm`) | `http://127.0.0.1:8000/v1` | `local-llm` | only if `--api-key` set |
| **mlx_lm.server** (`python -m mlx_lm.server --port 8080`) | `http://127.0.0.1:8080/v1` | the HF repo id you served | — |
| **Ollama** (`ollama serve`) | `http://127.0.0.1:11434/v1` | e.g. `llama3.1`, `qwen2.5` | — |
| **LM Studio** (local server) | `http://127.0.0.1:1234/v1` | model id shown in LM Studio | — |
| **llama.cpp** (`llama-server`) | `http://127.0.0.1:8080/v1` | any string | — |
| **LiteLLM proxy** | `http://127.0.0.1:4000/v1` | the alias you configured | your proxy key |
| **OpenAI (cloud)** | `https://api.openai.com/v1` | `gpt-4o-mini`, … | your OpenAI key |
| **OpenRouter (cloud)** | `https://openrouter.ai/api/v1` | e.g. `openai/gpt-4o-mini` | your OpenRouter key |
| **Gemini (cloud)** | `https://generativelanguage.googleapis.com/v1beta/openai` | a Gemini model id | your Gemini API key |
| **None** | *(blank)* | — | — |

Quick sanity check that a chosen endpoint works:

```bash
curl "$LOCAL_LLM_BASE_URL/chat/completions" \
  -H "Content-Type: application/json" \
  ${LOCAL_LLM_API_KEY:+-H "Authorization: Bearer $LOCAL_LLM_API_KEY"} \
  -d "{\"model\":\"$LOCAL_LLM_MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"한국의 수도는?\"}]}"
```

If that returns a JSON body with `choices[0].message.content`, the bot will work.

## Claude-only mode

Leave `LOCAL_LLM_BASE_URL` blank (`LOCAL_LLM_BASE_URL=` in the env file) when
you have no LLM endpoint. Then:

- Any message in a thread without a live session starts a Claude session, and
  that message is handed to it as the first request (after the thread context).
- If the session dies, the next message starts a new one instead of falling
  back.
- `local` / `로컬` ends the session; the next message starts a fresh one.

`vllm_watchdog.py` is only for people serving their own vLLM; skip it
otherwise.

## Running as a service

- **launchd (macOS)** / **systemd --user (Linux)**: run `python -X utf8 bot.py`
  with the env file path in `HANDOFF_ENV_FILE`. Service managers have a minimal
  PATH — set `CLAUDE_BIN` to the absolute `claude` path so the handoff spawn
  doesn't silently fail.
- Keep the bot on **one** machine (the one with `tmux` + `claude`, and the
  local endpoint if you use one). The spawned Claude sessions run on that same host.

## Embedding the handoff layer in an existing bot

If you already have a bot and only want the escalation feature, import three
functions from `supervisor.py` and the `detect_switch` parser — they have no
internal dependencies:

```python
from parser import detect_switch
import supervisor  # spawn_claude_handoff / responder_alive / stop_responder

active = my_store.get_active(thread_id)          # "claude" | "local"
switch = detect_switch(message.content, active)
if switch == "to_claude":
    result = await supervisor.spawn_claude_handoff(thread_id)
    if result.ok:
        my_store.set_active(thread_id, "claude")
    return
if switch == "to_local":
    await supervisor.stop_responder(thread_id)
    my_store.set_active(thread_id, "local")
    return
if active == "claude":
    if not await supervisor.responder_alive(thread_id):
        my_store.set_active(thread_id, "local")   # auto-recover
    else:
        return                                    # silent: Claude is replying
# else: your own local-model path
```

`bot.py` is the full worked example of exactly this flow.

## Notes & safety

- `--dangerously-skip-permissions` is the default for spawned sessions — only
  run in trusted, single-user environments. The Claude session can run arbitrary
  shell.
- Secrets come from the env file at process start (no `tmux -e`, so they don't
  show in `ps`). Keep the file mode `600`.
- Multiple gateway connections under one bot token coexist fine — the main bot
  and the handoff daemons share the token without kicking each other.
- The switch trigger is **exact one-word match** only. A task that just mentions
  "claude" or "local" in passing never flips the session.
- Local-mode conversation history is per-thread and lives in `STORE_PATH`. The
  Claude-mode conversation lives only in Discord (not fed back to the local
  model on switch-back) — fine for v1.
