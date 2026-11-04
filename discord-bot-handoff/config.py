"""Env-based configuration for the reference bot.

Resolution order for every value:
  1. process environment (already-exported vars win)
  2. the env file (`HANDOFF_ENV_FILE`, default
     `~/.config/discord-bot-handoff/discord.env`) — loaded here without
     clobbering anything already in the environment.

The same env file is the one `supervisor.py` sources into the spawned Claude
session, so bot secrets live in exactly one place. Keep it mode 600.

Stdlib only — no python-dotenv dependency. The loader understands simple
`KEY=value` lines, `#` comments, blank lines, optional `export ` prefix, and
quoted values.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_ENV_FILE = os.path.expanduser("~/.config/discord-bot-handoff/discord.env")


def _env_file_path() -> Path:
    return Path(os.environ.get("HANDOFF_ENV_FILE", DEFAULT_ENV_FILE)).expanduser()


def load_env_file(path: Path | None = None) -> None:
    """Load `KEY=value` lines from the env file into os.environ.

    Never overrides a variable already present in the environment (exported
    vars win). Silently no-ops if the file is missing.
    """
    p = path or _env_file_path()
    try:
        text = p.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _int_set(raw: str) -> set[int]:
    out: set[int] = set()
    for chunk in raw.replace(",", " ").split():
        try:
            out.add(int(chunk))
        except ValueError:
            continue
    return out


@dataclass(frozen=True)
class Config:
    # ── Discord ──
    bot_token: str
    authorized_user_id: int | None       # only respond to this user (0/empty → anyone)
    watched_channels: set[int] = field(default_factory=set)  # empty → any watched-elsewhere channel is ignored

    # ── Local LLM (OpenAI-compatible) ──
    llm_base_url: str = "http://127.0.0.1:8000/v1"
    llm_model: str = "local-llm"
    llm_api_key: str = ""                 # bearer token if the endpoint needs one
    llm_system_prompt: str = "You are a concise, helpful assistant. Reply in the user's language."
    llm_max_tokens: int = 1024
    llm_temperature: float = 0.3
    llm_timeout_s: float = 120.0

    # ── State ──
    store_path: str = os.path.expanduser("~/.local/share/discord-bot-handoff/state.db")
    history_turns: int = 8                # recent (user+assistant) turns fed to the LLM

    @classmethod
    def from_env(cls) -> "Config":
        """Build a Config from env (loading the env file first)."""
        load_env_file()
        token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError(
                "DISCORD_BOT_TOKEN is not set. Put it in the env file "
                f"({_env_file_path()}) or export it before starting the bot."
            )
        auth_raw = os.environ.get("DISCORD_AUTHORIZED_USER_ID", "").strip()
        auth_id = int(auth_raw) if auth_raw.isdigit() else None
        return cls(
            bot_token=token,
            authorized_user_id=auth_id,
            watched_channels=_int_set(os.environ.get("WATCHED_CHANNELS", "")),
            llm_base_url=os.environ.get("LOCAL_LLM_BASE_URL", cls.llm_base_url).rstrip("/"),
            llm_model=os.environ.get("LOCAL_LLM_MODEL", cls.llm_model),
            llm_api_key=os.environ.get("LOCAL_LLM_API_KEY", cls.llm_api_key),
            llm_system_prompt=os.environ.get("LOCAL_LLM_SYSTEM_PROMPT", cls.llm_system_prompt),
            llm_max_tokens=int(os.environ.get("LOCAL_LLM_MAX_TOKENS", cls.llm_max_tokens)),
            llm_temperature=float(os.environ.get("LOCAL_LLM_TEMPERATURE", cls.llm_temperature)),
            llm_timeout_s=float(os.environ.get("LOCAL_LLM_TIMEOUT_S", cls.llm_timeout_s)),
            store_path=os.environ.get("STORE_PATH", cls.store_path),
            history_turns=int(os.environ.get("HISTORY_TURNS", cls.history_turns)),
        )
