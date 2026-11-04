---
name: discord-handoff
description: 진행 중인 작업을 Discord 스레드로 핸드오프 — thread 생성 + 수신(봇 주입 모드 또는 Gateway daemon + Monitor 청취). Trigger — "디스코드로 옮기자", "핸드오프 해줘", "모바일로 계속할게".
---

Hand off the current task to a Discord thread so the user can continue
asynchronously. Open-ended conversation pattern — between user messages you
keep doing real work and post milestone updates proactively.

Invocation is **flexible**. The user might give you everything, just a channel,
or just say "let's switch to Discord". Fill in what's missing yourself when you
can; ask the user only for what's truly required.

CWD / PATHS:
- This skill's directory holds `handoff_lib.py` and `handoff_daemon.py`. If
  registered at user level via junction/symlink, that's `~/.claude/skills/discord-handoff/`.
  If used standalone from the repo, it's `<repo>/discord-handoff/`. Either way,
  `cd` to this skill's directory before running the helpers.
- All examples below use relative paths.
- Python: always `py -X utf8 <script>` on Windows, `python -X utf8` elsewhere.

════════════════════════════════════════════════════════
INPUTS — handle whatever the user gave
════════════════════════════════════════════════════════
Three pieces matter:

| Input | What it is | If missing |
|---|---|---|
| `parent_channel_id` | Discord channel where the new thread will live. **Text, announcement, forum, media** channels all supported — `handoff_lib.create_thread` auto-detects the type. | **MUST ASK** via `AskUserQuestion`. Suggest common ones if you know them. |
| `task_title` | Short name shown in Discord sidebar (≤100 chars). For forum/media channels this is the post title. | Auto-derive from current conversation context. Show user, confirm in one sentence. |
| `initial_summary` | 2-5 lines: what you're doing + how to interact. For forum/media channels this becomes the post's required initial message. | Auto-draft from context. No need to confirm unless drastically ambiguous. |

Resume mode (rare): if the user provides a `thread_id` explicitly, **skip STEP 1**
and jump straight to STEP 3 with that thread_id.

Don't pedantically demand the user fill a form. If they typed `discord-handoff
in #dev-tasks` and you can guess `#dev-tasks` channel id from earlier context,
go with it. If they typed just `핸드오프 해줘`, ask only for the channel.

════════════════════════════════════════════════════════
STEP 1 — Create thread
════════════════════════════════════════════════════════
```
py -X utf8 handoff_lib.py create-thread \
    --parent <parent_channel_id> \
    --name "<task_title>" \
    --starter "<initial_summary>"
```
Parse JSON. On success, response has `"id": "<thread_id>"` — capture it.
On failure (`ok: false` or no `id`), report error to user and stop.

════════════════════════════════════════════════════════
STEP 2 — Post intro in the new thread
════════════════════════════════════════════════════════
For **text/announcement** channels the starter message lives in the parent
channel (anchoring the thread). For **forum/media** channels the starter is
already the post's first message, so you can skip this step or just add a
short follow-up.
```
py -X utf8 handoff_lib.py send --target <thread_id> --text "🤖 핸드오프 시작
• 작업: {짧은 요약}
• 진행 중 milestone마다 여기에 업데이트 드릴게요
• 메시지 보내시면 바로 반영 (지시/질문/피드백 자유롭게)"
```
Tone/length is flexible — match the task's seriousness level.

════════════════════════════════════════════════════════
STEP 3 — Choose how Discord messages reach you (수신 방식 선택)
════════════════════════════════════════════════════════
Pick exactly one branch. The only test is the marker below — nothing else
(not "a bot started me", not the session name) selects inject mode.

**(1) 주입 모드 (inject mode)** — the prompt that started this session contains
the marker `[handoff-inject-mode]`.
- The bot types each Discord message into this session's input box. You do
  not start Monitor, `handoff_daemon.py`, or any polling of your own — just
  read the injected input and reply.
- Each injected message starts with this header (주입 머리):
  `[discord-handoff thread=<thread_id> msg=<message_id>]`
  followed by the message body. Attachments, if any, come after the body as
  a URL or as a local path the bot already downloaded.
- Reply as in STEP 5 (`handoff_lib.py send --target <thread_id>`).

**(2) Monitor 갈래 (Monitor branch)** — everything else: the skill invoked
directly, sessions started without a bot (e.g. autospawn), and older prompts
without the marker. Call Monitor:
- command: `py -X utf8 handoff_daemon.py <thread_id>`
- description: `Handoff: <task_title>`

Wait for `{"event":"ready", ...}` (~5 sec).
- Monitor expires after 30 minutes. When you get the expiry notice, start
  Monitor again with the same command within that same turn. This re-start is
  routine and does not count as a `fatal` restart in STEP 6.
- Don't append `| grep` or any other filter pipe to the Monitor command —
  take the daemon's stdout as is.

Both branches:
- Don't build ad-hoc listeners (REST polling scripts etc.).
- The marker `[handoff-inject-mode]` and the injected-message header above are
  a contract: any bot that injects messages (and its restart prompts) copies these strings
  verbatim. Don't change them here without changing that side.

════════════════════════════════════════════════════════
STEP 4 — Do the real work
════════════════════════════════════════════════════════
In the Monitor branch, Monitor runs in background — doesn't block other tool
calls (in inject mode nothing runs; messages arrive as input). Between Discord
messages, actually execute the task: write code, run commands, read files, call
MCP tools, whatever.

After each meaningful milestone (file written, test passed, decision made,
problem found), post a brief update:
```
py -X utf8 handoff_lib.py send --target <thread_id> --text "✅ {milestone}
• {detail}
• {next step}"
```
Keep updates 2-5 lines. Use code blocks for snippets:
```
py -X utf8 handoff_lib.py send --target <thread_id> --text "📄 \`<file>\` 작성

\`\`\`python
def example(): ...
\`\`\`"
```

**Attaching files** (markdown, images, logs, etc.) — `--file` is repeatable
(max 10 per message, ≤25MB each by default):
```
py -X utf8 handoff_lib.py send --target <thread_id> \
    --text "📎 보고서 첨부" --file ./report.md --file ./screenshot.png
```
Prefer attachments over pasting >30 lines inline. Files only (no text) is
also OK — just omit `--text`.

════════════════════════════════════════════════════════
STEP 5 — Handle each incoming Discord message
════════════════════════════════════════════════════════
A message arrives in one of two forms, depending on the STEP 3 branch:
- Inject mode: input that starts with the STEP 3 injected-message header
  (주입 머리). `thread=` is the thread to reply to; the body follows; any
  attachments follow the body as a URL or a local path.
- Monitor branch: a daemon `message` event.

Monitor branch payload:
```json
{"event":"message", "id":"...", "thread_id":"...", "content":"...",
 "attachments":[{"filename":"...","url":"...","size":...}, ...]}
```

For each attachment the daemon ALSO emits a separate small event right after
the main one (so consumers that truncate per-event still see every URL):
```json
{"event":"attachment", "msg_id":"...", "thread_id":"...", "index":0,
 "filename":"...", "url":"...", "size":...}
```
Either source is fine — they carry the same URL. Use whichever is convenient.

Respond in context of the ongoing task. Examples:
- "어디까지 했어?" → 진행 상황 짧게
- "X도 추가해줘" → 알았다 → 작업 → 끝나면 update post
- "잠깐 멈춰" → 알았다, 새 작업 중단 (수신은 계속 — Monitor 갈래면 daemon 이 계속 listen)
- "결과 보여줘" → diff / output snippet
- 자유 발화 → 컨텍스트 유지하며 자연 응답

답장은 두 갈래 모두 항상 thread로:
```
py -X utf8 handoff_lib.py send --target <thread_id> --text "..."
```

**Inject mode attachments**: if the attachment after the body is a local path,
`Read` that path directly — no `fetch-attachment`. If it is a URL, use
`fetch-attachment` as below.

**If `attachments` is present** (or an injected URL), download each file locally before reading it
(Discord CDN URLs expire and binary content doesn't fit through WebFetch
cleanly):
```
py -X utf8 handoff_lib.py fetch-attachment --url <attachment_url>
# → {"ok": true, "path": "attachments/foo.md", "size": 1234, "filename": "foo.md"}
```
Then `Read` the returned `path`. For images, the `Read` tool renders them
visually. Default save location is `<skill_dir>/attachments/<filename>`; pass
`--out` for a custom path.

════════════════════════════════════════════════════════
STEP 6 — Daemon lifecycle (Monitor 갈래 한정)
════════════════════════════════════════════════════════
Monitor branch only. In inject mode there is no daemon or listener to manage.
- `ready` → silent ack
- `error` / `poll_error` → continue
- `fatal` → restart daemon ONCE via new Monitor call. Second fatal within 60s →
  post `⚠️ daemon 비정상 종료. 세션 재시작 필요할 수 있어요.` to thread, stop.

════════════════════════════════════════════════════════
STEP 7 — End of task
════════════════════════════════════════════════════════
User signals done (or natural conclusion):
1. Post final summary (deliverables, paths, test status)
2. Ask: "스레드 그대로 둘까요, 닫을까요? (그대로 두면 자동 archive)"
3. Monitor 갈래 한정: either way, TaskStop the Monitor (daemon ends). In inject
   mode there is no listener to stop.

Don't delete the thread. Auto-archive after Discord's configured interval.

════════════════════════════════════════════════════════
STYLE
════════════════════════════════════════════════════════
- Korean (matching user)
- Mobile-first: short, scannable, no walls of text
- Emoji prefix optional: 📄 file / ✅ done / ⚠️ issue / 🚧 progress / 💡 insight / ❓ ask
- Code: ≤30 lines per snippet, or share a path instead
- Don't echo full files; summarize + offer to share details

════════════════════════════════════════════════════════
HARD RULES
════════════════════════════════════════════════════════
- Monitor 갈래 한정: one Monitor subscription per thread.
- Don't post into channels other than the configured thread.
- Don't auto-create threads — always require explicit `parent_channel_id`.
- If user pivots to clearly unrelated work mid-handoff, offer to spawn a new
  thread for it.
- Token never appears in any reply.
