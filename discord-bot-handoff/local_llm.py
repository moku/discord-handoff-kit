"""Minimal async client for an OpenAI-compatible chat-completions endpoint.

Works against anything that speaks the `/chat/completions` shape: vLLM,
mlx_lm.server, Ollama (`/v1`), LM Studio, llama.cpp server, LiteLLM, or the
real OpenAI API. Point it at a different backend by changing only
`LOCAL_LLM_BASE_URL` / `LOCAL_LLM_MODEL` (+ `LOCAL_LLM_API_KEY` if required) —
see README "Swapping the LLM endpoint".

Uses aiohttp, which is already a transitive dependency of discord.py, so no
extra package is needed.
"""

from __future__ import annotations

import logging

import aiohttp

log = logging.getLogger(__name__)


class LocalLLMError(RuntimeError):
    """Raised when the endpoint is unreachable or returns a non-2xx / bad body."""


class LocalLLM:
    """Thin wrapper over one OpenAI-compatible endpoint."""

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str = "",
        max_tokens: int = 1024,
        temperature: float = 0.3,
        timeout_s: float = 120.0,
    ) -> None:
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._api_key = api_key
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._timeout = aiohttp.ClientTimeout(total=timeout_s)

    async def complete(self, messages: list[dict[str, str]]) -> str:
        """Send a chat-completions request and return the assistant text.

        `messages` is the standard [{"role": ..., "content": ...}, ...] list.
        Raises `LocalLLMError` on transport / HTTP / parse failure so the bot
        can surface a clean message instead of crashing.
        """
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = {
            "model": self._model,
            "messages": messages,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
            "stream": False,
        }
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.post(self._url, json=payload, headers=headers) as resp:
                    body = await resp.text()
                    if resp.status >= 400:
                        raise LocalLLMError(
                            f"endpoint returned HTTP {resp.status}: {body[:300]}"
                        )
                    data = _parse_json(body)
        except aiohttp.ClientError as exc:
            raise LocalLLMError(f"cannot reach LLM endpoint ({self._url}): {exc}") from exc

        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise LocalLLMError(f"unexpected response shape: {body[:300]}") from exc


def _parse_json(body: str):
    import json

    try:
        return json.loads(body)
    except ValueError as exc:
        raise LocalLLMError(f"response was not JSON: {body[:300]}") from exc
