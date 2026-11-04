"""Deterministic switch-trigger detector.

`detect_switch(content, active)` fires only when the WHOLE stripped message is
exactly one of the trigger words. So a normal task/question — even one that
mentions the other agent — can never flip the session.

Triggers are model-agnostic labels:
  * "claude" / "클로드" → switch from local model to Claude session
  * "local"  / "로컬"   → switch from Claude session back to local model

This is intentionally not an LLM classifier — predictable, debuggable, zero
false positives, no latency.
"""

from __future__ import annotations

_TO_CLAUDE_EXACT = {"클로드", "claude"}
_TO_LOCAL_EXACT = {"local", "로컬"}


def detect_switch(content: str, active: str) -> str | None:
    """Return 'to_claude' / 'to_local' / None.

    `active` is the current state for this thread, typically the result of
    a key lookup in your conversation store ("claude" or "local").
    """
    n = content.strip().lower()
    if active != "claude" and n in _TO_CLAUDE_EXACT:
        return "to_claude"
    if active == "claude" and n in _TO_LOCAL_EXACT:
        return "to_local"
    return None
