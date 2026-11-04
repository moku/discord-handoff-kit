"""tmux supervisor for per-thread Claude Code sessions bound to a Discord thread
via the discord-handoff skill.

`spawn_claude_handoff(thread_id)` does the heavy lifting:
  1. Opens a tmux session named `agent-<thread_id>`.
  2. Sources an env file (`HANDOFF_ENV_FILE`, default
     `~/.config/discord-bot-handoff/discord.env`) for `DISCORD_BOT_TOKEN` and
     `DISCORD_AUTHORIZED_USER_ID` — so secrets stay out of process args.
  3. Boots `claude --dangerously-skip-permissions` inside the session.
  4. Polls the tmux pane until claude's input prompt is ready (handling the
     trust-folder dialog on the way). If the pane shows a login screen instead,
     surfaces ``login_required`` so the caller can tell the user; if neither
     ready nor login markers appear in the timeout window, surfaces a pane
     snapshot for diagnosis.
  5. On ready: types HANDOFF_PROMPT that tells the Claude session to follow
     ``~/.claude/skills/discord-handoff/SKILL.md`` in RESUME mode for this
     thread and start its handoff daemon.

Bring-your-own conversation store: `responder_alive()` only checks if the tmux
session is live. Pair with a small SQLite/JSON state to remember active="claude"
vs active="local" per thread (see `store.py` for a reference).

Requires: tmux + claude CLI on PATH (or override CLAUDE_BIN env).
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

# ─── config ──────────────────────────────────────────────────────────────────

# Working dir for the spawned Claude session. Defaults to the cwd of the bot
# process; override via HANDOFF_CWD env if you want a different root (e.g. a
# project repo so Claude's Read/Edit start there).
CWD = Path(os.environ.get("HANDOFF_CWD", os.getcwd())).resolve()

# Env file the spawned shell sources before exec'ing claude. Holds bot secrets
# (DISCORD_BOT_TOKEN, DISCORD_AUTHORIZED_USER_ID). The handoff daemon and lib
# pick those up from env at process start.
ENV_FILE = os.environ.get(
    "HANDOFF_ENV_FILE",
    os.path.expanduser("~/.config/discord-bot-handoff/discord.env"),
)

# Claude binary. systemd --user services have a minimal PATH (no ~/.local/bin),
# so resolve to an absolute path or the tmux command silently dies. Override
# with CLAUDE_BIN env if needed (e.g. brew install: /opt/homebrew/bin/claude).
CLAUDE_BIN = os.environ.get("CLAUDE_BIN") or next(
    (p for p in (os.path.expanduser("~/.local/bin/claude"), shutil.which("claude") or "")
     if p and os.path.exists(p)),
    "claude",
)

# Prompt the spawned Claude session receives once it boots. It tells the session
# to follow the discord-handoff skill in RESUME mode (no thread create) for this
# specific thread_id. Override the whole thing with the HANDOFF_PROMPT env var
# (must contain a `{thread_id}` placeholder) to change language/instructions.
_DEFAULT_HANDOFF_PROMPT = (
    "너는 디스코드 포럼-Claude 브리지다. "
    "~/.claude/skills/discord-handoff/SKILL.md 를 읽고 RESUME 모드로 따라줘: "
    "thread_id={thread_id} 는 이미 존재하는 포럼 스레드이니 STEP 1(스레드 생성)은 건너뛰고 "
    "STEP 3부터 시작해 — STEP 3 의 Monitor 갈래대로 `python3 -X utf8 handoff_daemon.py {thread_id}` 를 "
    "Monitor 로 띄워. 명령에 필터 파이프(| grep 등)를 붙이지 말고, 만료 알림을 받으면 그 턴 안에 "
    "같은 명령으로 다시 띄워. "
    "봇 토큰과 인증 유저ID는 환경변수에 이미 설정돼 있어. ready 이벤트가 뜨면 한국어로 짧게 인사해. "
    "이후 사용자 메시지(첨부·스크린샷 포함 — attachments의 url을 내려받아 Read로 확인)에 대화형으로 "
    "응답하고, 필요하면 셸/도구로 실제 작업(설치·빌드·실행 등)을 끝까지 진행해. "
    "세션이 종료될 때까지 계속 청취해. "
    "사용자가 다른 스레드를 제목·멘션·링크로 가리키면 ~/.claude/skills/thread-context/SKILL.md 를 읽고 "
    "그 절차(찾기 → 살아 있는 세션에 묻기 → 없으면 기록 읽기)를 따라."
)
HANDOFF_PROMPT = os.environ.get("HANDOFF_PROMPT", _DEFAULT_HANDOFF_PROMPT)


# Pane content markers used to classify what claude is showing us. Tuned by
# observation; keep this list short and string-y rather than regex so noise
# in the TUI redraw doesn't make matching flaky.
_READY_MARKERS: tuple[str, ...] = (
    # the input prompt cursor
    "❯",
    # bottom-of-screen hints that only show after the input box is up
    "for shortcuts",
    "auto-accept",
    "shift+tab",
)
_TRUST_MARKER = "Do you trust"
# When claude has no usable credentials it drops you into an auth-method
# picker. The most reliable strings — both appear on that screen but neither
# appears on a normal welcome.
_LOGIN_MARKERS: tuple[str, ...] = (
    "Select login method",
    "Welcome to Claude Code",  # the auth-method picker reuses this header
    "Anthropic Console",
    "/login",
    "Please log in",
    "Login required",
    "auth login",
)


@dataclass(frozen=True)
class SpawnResult:
    """Outcome of `spawn_claude_handoff`.

    The bot uses ``status`` to drive the user-visible Discord message; the
    ``pane_excerpt`` is attached on non-ok outcomes so the user can see what
    Claude is actually showing on the host without having to ssh in.
    """

    ok: bool
    status: Literal["ok", "login_required", "boot_timeout", "spawn_failed"]
    reason: str = ""
    reused: bool = False  # True when an already-running session was kept
    pane_excerpt: str = ""


# ─── tmux primitives ─────────────────────────────────────────────────────────

def _session_name(thread_id: str) -> str:
    return f"agent-{thread_id}"


async def _send_keys(name: str, *keys: str) -> bool:
    proc = await asyncio.create_subprocess_exec(
        "tmux", "send-keys", "-t", name, *keys,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    return await proc.wait() == 0


async def _capture_pane(name: str, lines: int = 80) -> str:
    """Return the last ``lines`` lines of the tmux pane ('' on failure)."""
    proc = await asyncio.create_subprocess_exec(
        "tmux", "capture-pane", "-t", name, "-p", "-S", f"-{lines}",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    if proc.returncode != 0:
        return ""
    return out.decode("utf-8", errors="replace")


def _excerpt(pane: str, max_lines: int = 30, max_chars: int = 1400) -> str:
    """Trim a pane capture down to something postable in Discord without
    blowing the 2000-char limit. Keeps the tail (most relevant) and strips
    blank lines / leading whitespace runs."""
    lines = [ln.rstrip() for ln in pane.splitlines() if ln.strip()]
    excerpt = "\n".join(lines[-max_lines:])
    if len(excerpt) > max_chars:
        excerpt = "…\n" + excerpt[-max_chars:]
    return excerpt


async def inject_message(thread_id: str, body: str) -> bool:
    """Paste multi-line text into the thread's session as one input, then submit.

    A bracketed paste keeps newlines from submitting the message line by line,
    which is what plain `send-keys` would do.
    """
    name = _session_name(thread_id)
    buffer_name = f"handoff-inject-{name}-{time.monotonic_ns()}"
    load = await asyncio.create_subprocess_exec(
        "tmux", "load-buffer", "-b", buffer_name, "-",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await load.communicate(body.encode("utf-8"))
    if load.returncode != 0:
        return False
    paste = await asyncio.create_subprocess_exec(
        "tmux", "paste-buffer", "-p", "-d", "-b", buffer_name, "-t", name,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    if await paste.wait() != 0:
        return False
    await asyncio.sleep(1.5)  # submit separately, for the same reason as the boot prompt
    return await _send_keys(name, "Enter")


async def responder_alive(thread_id: str) -> bool:
    """True if a tmux session for this thread is currently running."""
    name = _session_name(thread_id)
    proc = await asyncio.create_subprocess_exec(
        "tmux", "has-session", "-t", name,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    return await proc.wait() == 0


async def stop_responder(thread_id: str) -> None:
    """Kill the tmux session for this thread AND reap its handoff daemon.

    `tmux kill-session` alone is not enough: the handoff_daemon runs as a
    Monitor child of the in-pane Claude process, but in its own process group,
    so it survives the pane being killed. The orphaned daemon keeps a Discord
    gateway connection open under the shared bot token. Repeated local↔claude
    toggles then pile up zombie daemons that contend for the bot's limited
    concurrent gateway sessions, which silently drops inbound messages to the
    *live* responder (observed as "this thread stopped replying"). So after
    killing the pane, explicitly pkill the daemon bound to this thread_id.
    """
    name = _session_name(thread_id)
    try:
        proc = await asyncio.create_subprocess_exec(
            "tmux", "kill-session", "-t", name,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
    except Exception:
        pass
    # Reap the orphaned daemon. The full-cmdline pattern ends with the exact
    # thread_id (snowflakes are fixed-width, never a prefix of one another), so
    # this never matches another thread's daemon. Also catches the shell wrapper
    # that launched it.
    try:
        proc = await asyncio.create_subprocess_exec(
            "pkill", "-f", f"handoff_daemon.py {thread_id}",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
    except Exception:
        return


# ─── spawn ───────────────────────────────────────────────────────────────────

async def _wait_for_ready(
    name: str,
    timeout: float = 30.0,
    interval: float = 1.5,
) -> tuple[Literal["ok", "login_required", "timeout"], str]:
    """Poll the pane until ready / login / timeout.

    Returns ``(status, pane_excerpt)``. ``status``:
      - ``"ok"``: input prompt is up (HANDOFF_PROMPT is safe to send)
      - ``"login_required"``: claude is showing the auth-method picker
      - ``"timeout"``: neither marker matched within ``timeout`` seconds

    Trust-folder dialog ("Do you trust...?") is auto-confirmed with Enter on
    the way — it has nothing to do with auth so we don't bubble it up.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    last_pane = ""
    trust_sent = False
    while loop.time() < deadline:
        pane = await _capture_pane(name)
        if pane:
            last_pane = pane
        if _TRUST_MARKER in pane and not trust_sent:
            await _send_keys(name, "Enter")
            trust_sent = True
            # give the TUI a beat to redraw past the dialog
            await asyncio.sleep(interval)
            continue
        # Ready beats login — if we see "❯" but also "/login" in some other
        # hint, the prompt is up.
        if any(m in pane for m in _READY_MARKERS):
            return "ok", _excerpt(pane)
        if any(m in pane for m in _LOGIN_MARKERS):
            return "login_required", _excerpt(pane)
        await asyncio.sleep(interval)
    return "timeout", _excerpt(last_pane)


async def spawn_claude_handoff(thread_id: str) -> SpawnResult:
    """Spawn a Claude session bound to this Discord thread via the
    discord-handoff skill.

    Returns a :class:`SpawnResult`. On non-``ok`` outcomes the tmux session
    is torn down so the next ``"claude"`` trigger from the user gets a clean
    boot (no stale auth screen lingering).
    """
    name = _session_name(thread_id)
    try:
        if await responder_alive(thread_id):
            # Already running — treat as success. (Caller can stop_responder
            # first if they want a fresh boot.)
            return SpawnResult(ok=True, status="ok", reason="already running", reused=True)

        # Source env file (secrets) into the session, then exec claude. Keeps
        # secrets out of argv — `tmux new-session -e VAR=value` would expose
        # them in `ps`. The bash login shell + source pattern avoids that.
        bootstrap = (
            f'set -a; . "{ENV_FILE}"; set +a; '
            f'exec {CLAUDE_BIN} --dangerously-skip-permissions'
        )
        proc = await asyncio.create_subprocess_exec(
            "tmux", "new-session", "-d", "-s", name, "-c", str(CWD),
            "bash", "-lc", bootstrap,
        )
        if await proc.wait() != 0:
            log.error("failed to create handoff tmux session %s", name)
            return SpawnResult(
                ok=False, status="spawn_failed",
                reason="tmux new-session returned non-zero",
            )

        # Wait for claude to settle into either the input prompt or the
        # auth-method picker. Polling the pane is more reliable than a fixed
        # sleep because cold start time varies 4–15s.
        status, pane = await _wait_for_ready(name)
        if status == "login_required":
            log.warning("claude needs login for thread=%s; killing tmux session", thread_id)
            await stop_responder(thread_id)
            return SpawnResult(
                ok=False, status="login_required",
                reason="claude auth-method picker detected — run `claude /login` on the host",
                pane_excerpt=pane,
            )
        if status == "timeout":
            log.error("claude did not reach ready within timeout for thread=%s", thread_id)
            await stop_responder(thread_id)
            return SpawnResult(
                ok=False, status="boot_timeout",
                reason="no ready/login markers in pane within 30s",
                pane_excerpt=pane,
            )

        # Type the prompt, then submit with a SEPARATE Enter — sending the
        # text and Enter in one call lands the text but the Enter often
        # doesn't register while claude is still rendering.
        if not await _send_keys(name, HANDOFF_PROMPT.format(thread_id=thread_id)):
            log.error("failed to type handoff prompt to tmux session %s", name)
            await stop_responder(thread_id)
            return SpawnResult(
                ok=False, status="spawn_failed",
                reason="tmux send-keys (prompt body) failed",
            )
        await asyncio.sleep(1.5)
        await _send_keys(name, "Enter")
        return SpawnResult(ok=True, status="ok")
    except Exception as exc:
        log.exception("failed to spawn handoff claude for thread_id=%s", thread_id)
        return SpawnResult(
            ok=False, status="spawn_failed",
            reason=f"unexpected exception: {type(exc).__name__}: {exc}",
        )
