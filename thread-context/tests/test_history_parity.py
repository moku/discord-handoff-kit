import argparse
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import thread_lookup


def test_history_matches_handoff_and_keeps_default_limit(monkeypatch, capsys):
    handoff_lib = thread_lookup.handoff_lib
    assert Path(handoff_lib.__file__).resolve() == (
        Path(__file__).resolve().parents[2] / "discord-handoff/handoff_lib.py"
    ).resolve()

    messages = [
        {
            "id": "2", "timestamp": "2026-11-01T12:35:00Z", "content": "가" * 600,
            "author": {"username": "bot", "global_name": "Helper", "bot": True},
            "attachments": [{"filename": "report.txt", "url": "https://cdn.example/report.txt"}],
        },
        {
            "id": "1", "timestamp": "2026-11-01T12:34:00Z", "content": "first",
            "author": {"username": "writer", "global_name": None, "bot": False},
            "attachments": [],
        },
    ]
    urls = []

    def urlopen(request, timeout):
        urls.append(request.full_url)
        return io.BytesIO(json.dumps(messages).encode("utf-8"))

    monkeypatch.setattr(handoff_lib.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(handoff_lib, "_resolve_token", lambda: "test-token")

    cli_args = argparse.Namespace(target="X", limit=5, before=None, max_chars=500)
    assert handoff_lib._cli_history(cli_args) == 0
    expected = capsys.readouterr().out.splitlines()

    lookup_args = argparse.Namespace(thread_id="X", limit=5, before=None, max_chars=500)
    thread_lookup.cmd_history(lookup_args)
    actual = capsys.readouterr().out.splitlines()
    assert actual == expected
    assert actual[0].endswith("msg=1: first")
    assert "Helper (bot)" in actual[1]
    assert "…(+100자)" in actual[1]
    assert "report.txt (https://cdn.example/report.txt)" in actual[1]
    assert urls == [f"{handoff_lib.API_BASE}/channels/X/messages?limit=5"] * 2

    monkeypatch.setattr(sys, "argv", [thread_lookup.__file__, "history", "X"])
    thread_lookup.main()
    default_lines = capsys.readouterr().out.splitlines()
    assert len(default_lines) == 2
    assert "가" * 500 + "…(+100자)" in default_lines[1]
    assert urls[-1] == f"{handoff_lib.API_BASE}/channels/X/messages?limit=20"


def test_find_and_sessions_remain_subcommands(monkeypatch):
    calls = []
    monkeypatch.setattr(thread_lookup, "cmd_find", lambda args: calls.append(("find", args.query)))
    monkeypatch.setattr(thread_lookup, "cmd_sessions", lambda args: calls.append(("sessions", None)))

    monkeypatch.setattr(sys, "argv", [thread_lookup.__file__, "find", "topic"])
    thread_lookup.main()
    monkeypatch.setattr(sys, "argv", [thread_lookup.__file__, "sessions"])
    thread_lookup.main()
    assert calls == [("find", "topic"), ("sessions", None)]


def test_history_error_exits(monkeypatch, capsys):
    monkeypatch.setattr(thread_lookup.handoff_lib, "fetch_history", lambda *args, **kwargs: {
        "ok": False, "error": "history failed",
    })
    monkeypatch.setattr(sys, "argv", [thread_lookup.__file__, "history", "X"])
    with pytest.raises(SystemExit) as exc:
        thread_lookup.main()
    assert exc.value.code == "history failed"
    assert capsys.readouterr().out == ""
