#!/usr/bin/env python3
"""Set a running Claude Code session's effort level for that session only, by driving the /effort picker in its tmux pane.

The picker's "s" key applies the level to the session only; `/effort <level>` would also save it as the default, so it is never used here.
Waits until the pane is idle (no "esc to interrupt") and its input box is empty, so it never mixes into a reply or a draft.
Exit 0 and a RESULT line on success; non-zero and an ERROR line otherwise. Settings files are hashed before and after to prove the default is untouched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

LEVELS = ["low", "medium", "high", "xhigh", "max"]
POLL = 3.0


def tmux(*a: str) -> str:
    return subprocess.run(["tmux", *a], capture_output=True, text=True, check=True).stdout


def screen(pane: str) -> list[str]:
    return tmux("capture-pane", "-p", "-t", pane).splitlines()


def tail(lines: list[str], n: int = 8) -> list[str]:
    return [l for l in lines if l.strip()][-n:]


def busy(lines: list[str]) -> bool:
    return any("esc to interrupt" in l for l in tail(lines, 6))


def prompt_line(lines: list[str]) -> str | None:
    for l in reversed(lines):
        if l.lstrip().startswith("❯"):
            return l
    return None


DIM = re.compile(r"\x1b\[2m.*?(?=\x1b\[(?:0|22)?m|$)"); SGR = re.compile(r"\x1b\[[0-9;]*m")


def prompt_empty(pane: str) -> bool:
    """input box holds no typed text; dim text is Claude Code's suggested next prompt, not a draft"""
    raw = tmux("capture-pane", "-p", "-e", "-t", pane).splitlines()
    l = prompt_line([SGR.sub("", DIM.sub("", x)) for x in raw])
    return l is not None and l.strip() == "❯"


def picker_level(lines: list[str]) -> str | None:
    """level under the ▲ marker of the open /effort picker, or None if the picker is not shown"""
    if not any("for this session only" in l for l in lines):
        return None
    lab = next((l for l in lines if all(re.search(rf"\b{v}\b", l) for v in LEVELS)), None)
    mark = next((l for l in reversed(lines) if "▲" in l), None)
    if lab is None or mark is None:
        return None
    col = mark.index("▲")
    centers = {v: m.start() + len(v) / 2 for v in LEVELS for m in [re.search(rf"\b{v}\b", lab)]}
    return min(LEVELS, key=lambda v: abs(centers[v] - col))


def status_level(lines: list[str]) -> str | None:
    for l in reversed(lines):
        m = re.search(r"◉\s+(low|medium|high|xhigh|max)\b", l)
        if m:
            return m.group(1)
    return None


def digest(paths: list[Path]) -> dict[str, str]:
    return {str(p): (hashlib.sha256(p.read_bytes()).hexdigest()[:16] if p.exists() else "absent") for p in paths}


def resolve(target: str | None) -> str:
    if not target:
        target = os.environ.get("TMUX_PANE")
        if not target:
            sys.exit("ERROR no --target and not inside tmux")
    elif target.isdigit():
        target = f"agent-{target}"  # Discord thread id -> bridge session name
    return tmux("display-message", "-p", "-t", target, "#{pane_id}").strip()


def wait_idle(pane: str, timeout: float) -> list[str]:
    t0 = time.time(); calm = 0
    while time.time() - t0 < timeout:
        s = screen(pane)
        calm = calm + 1 if not busy(s) else 0
        if calm >= 2:
            if not prompt_empty(pane):
                sys.exit("ERROR input box is not empty (a draft is typed); refusing to type over it")
            return s
        time.sleep(POLL)
    sys.exit(f"ERROR pane stayed busy for {timeout:.0f}s")


def open_picker(pane: str) -> list[str]:
    tmux("send-keys", "-t", pane, "-l", "/effort"); time.sleep(1.0); tmux("send-keys", "-t", pane, "Enter")
    for _ in range(10):
        time.sleep(0.7); s = screen(pane)
        if picker_level(s):
            return s
    tmux("send-keys", "-t", pane, "Escape")
    sys.exit("ERROR /effort picker did not appear")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("level", nargs="?", choices=LEVELS, help="target level; omit with --show")
    ap.add_argument("--target", help="tmux pane/session, or a Discord thread id (-> agent-<id>); default: this pane")
    ap.add_argument("--show", action="store_true", help="only read the current level, change nothing")
    ap.add_argument("--timeout", type=float, default=900)
    a = ap.parse_args()
    if not a.show and not a.level:
        ap.error("give a level or --show")
    pane = resolve(a.target)
    cwd = Path(tmux("display-message", "-p", "-t", pane, "#{pane_current_path}").strip())
    files = [Path.home() / ".claude/settings.json", cwd / ".claude/settings.json", cwd / ".claude/settings.local.json"]
    before = digest(files)
    wait_idle(pane, a.timeout)
    s = open_picker(pane); cur = picker_level(s)
    if a.show:
        tmux("send-keys", "-t", pane, "Escape")
        print("RESULT", json.dumps(dict(pane=pane, current=cur))); return
    d = LEVELS.index(a.level) - LEVELS.index(cur)
    for _ in range(abs(d)):
        tmux("send-keys", "-t", pane, "Right" if d > 0 else "Left"); time.sleep(0.3)
    time.sleep(0.5); now = picker_level(screen(pane))
    if now != a.level:
        tmux("send-keys", "-t", pane, "Escape")
        sys.exit(f"ERROR picker shows {now} after moving, expected {a.level}; cancelled")
    tmux("send-keys", "-t", pane, "s"); time.sleep(2.0)
    s = screen(pane); msg = next((l.strip(" ⎿") for l in reversed(s) if "Set effort level" in l or "effort" in l.lower() and "session" in l.lower()), "")
    after = digest(files); changed = [k for k in files if before[str(k)] != after[str(k)]]
    ok = status_level(s) in (a.level, None) and not changed and picker_level(s) is None
    print("RESULT" if ok else "ERROR", json.dumps(dict(pane=pane, before=cur, after=a.level, status_line=status_level(s), message=msg, settings_changed=[str(c) for c in changed]), ensure_ascii=False))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
