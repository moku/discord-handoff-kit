"""Discord thread handoff — standalone send + create-thread helpers (stdlib only).

Designed for the generalized handoff pattern: spin up an ad-hoc thread, post
messages to it. Portable to a separate repo if you only want this skill.

Config resolution order (first hit wins):
  1. Function args (when called as library)
  2. Env vars: DISCORD_BOT_TOKEN, DISCORD_AUTHORIZED_USER_ID
  3. Sibling `handoff_config.py` in same directory
  4. Fallback to ../scripts/config.py (Vault parent layout — DISCORD_BOT_TOKEN, DISCORD_USER_ID)

Usage as CLI:
  py -X utf8 handoff_lib.py send --target <id> --text "..."
  py -X utf8 handoff_lib.py send --target <id>                    # stdin
  py -X utf8 handoff_lib.py send --target <id> --file foo.md --file bar.png
  py -X utf8 handoff_lib.py create-thread --parent <channel_id> --name "..." --starter "..."
  py -X utf8 handoff_lib.py fetch-attachment --url <cdn_url> [--out path]
  py -X utf8 handoff_lib.py history --target <channel_or_thread_id> [--limit 20] [--before <msg_id>] [--max-chars N]

Usage as library:
  from handoff_lib import send, create_thread, fetch_attachment, fetch_history
"""
from __future__ import annotations
import json
import mimetypes
import os
import sys
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API_BASE = "https://discord.com/api/v10"
MSG_MAX = 1990


def _try_import_sibling_config():
    """Try `handoff_config.py` in this file's directory. Returns module or None."""
    here = Path(__file__).resolve().parent
    if (here / "handoff_config.py").exists():
        sys.path.insert(0, str(here))
        try:
            import handoff_config
            return handoff_config
        except Exception:
            return None
    return None


def _try_import_parent_vault_config():
    """Try `../scripts/config.py` (Vault layout). Returns module or None."""
    parent_scripts = Path(__file__).resolve().parent.parent / "scripts"
    if (parent_scripts / "config.py").exists():
        sys.path.insert(0, str(parent_scripts))
        try:
            import config
            return config
        except Exception:
            return None
    return None


def _resolve_token() -> str:
    v = os.environ.get("DISCORD_BOT_TOKEN")
    if v:
        return v
    sib = _try_import_sibling_config()
    if sib and getattr(sib, "DISCORD_BOT_TOKEN", None):
        return sib.DISCORD_BOT_TOKEN
    par = _try_import_parent_vault_config()
    if par and getattr(par, "DISCORD_BOT_TOKEN", None):
        return par.DISCORD_BOT_TOKEN
    return ""


def _resolve_user_id() -> str:
    v = os.environ.get("DISCORD_AUTHORIZED_USER_ID")
    if v:
        return v
    sib = _try_import_sibling_config()
    if sib:
        for n in ("DISCORD_AUTHORIZED_USER_ID", "DISCORD_USER_ID"):
            if getattr(sib, n, None):
                return getattr(sib, n)
    par = _try_import_parent_vault_config()
    if par and getattr(par, "DISCORD_USER_ID", None):
        return par.DISCORD_USER_ID
    return ""


def _post(path: str, payload: dict, token: str | None = None) -> dict:
    token = token or _resolve_token()
    if not token:
        return {"ok": False, "error": "no DISCORD_BOT_TOKEN available"}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": "Vault-Handoff (0.1)",
        },
    )
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8")
                data = json.loads(raw) if raw else {}
                data["ok"] = True
                return data
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(raw)
            except Exception:
                data = {"error": raw}
            if e.code == 429 and attempt == 0:
                time.sleep(min(float(data.get("retry_after", 1.0)), 5.0))
                continue
            data["_http_status"] = e.code
            data["ok"] = False
            return data


def _post_multipart(path: str, payload_json: dict, files: list[tuple[str, bytes]],
                    token: str | None = None) -> dict:
    """POST with multipart/form-data. `files` is list of (filename, content_bytes).

    Builds the Discord multipart payload: a `payload_json` part plus `files[N]`
    parts. Attaches `attachments` metadata to payload_json so Discord links
    file slots to user-visible filenames.
    """
    token = token or _resolve_token()
    if not token:
        return {"ok": False, "error": "no DISCORD_BOT_TOKEN available"}

    payload = dict(payload_json)
    payload["attachments"] = [
        {"id": i, "filename": fname} for i, (fname, _) in enumerate(files)
    ]

    boundary = f"----handoffBoundary{uuid.uuid4().hex}"
    crlf = b"\r\n"
    body = bytearray()

    body += f"--{boundary}".encode() + crlf
    body += b'Content-Disposition: form-data; name="payload_json"' + crlf
    body += b"Content-Type: application/json" + crlf + crlf
    body += json.dumps(payload, ensure_ascii=False).encode("utf-8") + crlf

    for i, (fname, content) in enumerate(files):
        ctype = mimetypes.guess_type(fname)[0] or "application/octet-stream"
        body += f"--{boundary}".encode() + crlf
        body += (
            f'Content-Disposition: form-data; name="files[{i}]"; filename="{fname}"'
        ).encode("utf-8") + crlf
        body += f"Content-Type: {ctype}".encode() + crlf + crlf
        body += content + crlf

    body += f"--{boundary}--".encode() + crlf

    req = urllib.request.Request(
        f"{API_BASE}{path}",
        data=bytes(body),
        method="POST",
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "Vault-Handoff (0.1)",
        },
    )
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8")
                data = json.loads(raw) if raw else {}
                data["ok"] = True
                return data
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(raw)
            except Exception:
                data = {"error": raw}
            if e.code == 429 and attempt == 0:
                time.sleep(min(float(data.get("retry_after", 1.0)), 5.0))
                continue
            data["_http_status"] = e.code
            data["ok"] = False
            return data


def _get(path: str, token: str | None = None, _retries: int = 3) -> dict:
    token = token or _resolve_token()
    if not token:
        return {"ok": False, "error": "no DISCORD_BOT_TOKEN available"}
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        method="GET",
        headers={
            "Authorization": f"Bot {token}",
            "User-Agent": "Vault-Handoff (0.1)",
        },
    )
    for attempt in range(_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8")
                data = json.loads(raw) if raw else {}
                if isinstance(data, list):
                    return {"ok": True, "items": data}
                data["ok"] = True
                return data
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(raw)
            except Exception:
                data = {"error": raw}
            if e.code == 429 and attempt < _retries:
                try:
                    delay = float(data.get("retry_after", 1.0))
                except (TypeError, ValueError, AttributeError):
                    delay = 1.0
                time.sleep(min(delay, 10.0) + 0.1)
                continue
            data["_http_status"] = e.code
            data["ok"] = False
            return data


def _format_history_line(message: dict, max_chars: int | None = None) -> str:
    author = message["author"]
    text = message.get("content", "")
    if max_chars is not None and len(text) > max_chars:
        text = f"{text[:max_chars]}…(+{len(text) - max_chars}자)"
    line = (f"[{message['timestamp'][:16]}] {author.get('global_name') or author['username']}"
            f"{' (bot)' if author.get('bot') else ''} msg={message['id']}: {text}")
    attachments = message.get("attachments", [])
    if attachments:
        line += " [첨부: " + ", ".join(
            f"{item['filename']} ({item['url']})" for item in attachments
        ) + "]"
    return line


def fetch_history(channel_id: str | int, limit: int = 20, before: str | None = None,
                  max_chars: int | None = None, token: str | None = None) -> dict:
    """Return one page of channel or thread messages in oldest-first readable lines."""
    path = f"/channels/{channel_id}/messages?limit={min(limit, 100)}"
    if before is not None:
        path += f"&before={before}"
    result = _get(path, token=token)
    if not result.get("ok") or "items" not in result:
        reason = result.get("error") or result.get("message")
        if not reason and "_http_status" in result:
            reason = f"HTTP {result['_http_status']}"
        return {"ok": False, "error": str(reason or "response has no items")}
    return {"ok": True, "lines": [
        _format_history_line(message, max_chars) for message in reversed(result["items"])
    ]}


def _cli_history(args) -> int:
    if args.limit > 100:
        print("history: 100건만 조회합니다. 이전 기록은 --before 를 사용하세요.", file=sys.stderr)
    result = fetch_history(args.target, limit=args.limit, before=args.before,
                           max_chars=args.max_chars)
    if not result["ok"]:
        print(result["error"], file=sys.stderr)
        return 1
    if not result["lines"]:
        print("history: no messages", file=sys.stderr)
    for line in result["lines"]:
        print(line)
    return 0


def _split_chunks(text: str, limit: int = MSG_MAX) -> list[str]:
    if len(text) <= limit:
        return [text]
    out, buf = [], ""
    for line in text.splitlines(keepends=True):
        if len(line) > limit:
            if buf:
                out.append(buf); buf = ""
            for i in range(0, len(line), limit):
                out.append(line[i : i + limit])
            continue
        if len(buf) + len(line) > limit:
            out.append(buf); buf = line
        else:
            buf += line
    if buf:
        out.append(buf)
    return out


def send(target_id: str | int, content: str = "",
         files: list[str | Path] | None = None,
         token: str | None = None) -> dict:
    """Send a message to a channel or thread ID.

    - Text-only: auto-splits if > 2000 chars.
    - Files: list of local paths. Each file ≤ 25MB by default (server-dependent).
      Up to 10 files per message (Discord limit).
    - Text + files: if content fits in one message, sends together. If content
      exceeds MSG_MAX, sends leading chunks as plain text and attaches files to
      the final chunk.
    - Files only (no content): sends a multipart message with empty content.
    """
    cid = str(target_id)

    if not files:
        if not content or not str(content).strip():
            return {"ok": False, "error": "empty content"}
        result: dict = {"ok": False}
        for c in _split_chunks(content):
            result = _post(f"/channels/{cid}/messages", {"content": c}, token=token)
            if not result.get("ok"):
                return result
        return result

    loaded: list[tuple[str, bytes]] = []
    for f in files:
        p = Path(f)
        if not p.is_file():
            return {"ok": False, "error": f"file not found: {p}"}
        loaded.append((p.name, p.read_bytes()))
    if len(loaded) > 10:
        return {"ok": False, "error": f"too many files: {len(loaded)} (max 10)"}

    chunks = _split_chunks(content) if content and content.strip() else [""]
    for c in chunks[:-1]:
        r = _post(f"/channels/{cid}/messages", {"content": c}, token=token)
        if not r.get("ok"):
            return r
    return _post_multipart(
        f"/channels/{cid}/messages",
        {"content": chunks[-1]},
        loaded,
        token=token,
    )


def fetch_attachment(url: str, out: str | Path | None = None) -> dict:
    """Download a Discord attachment URL to a local path (no auth needed — CDN URL).

    If `out` is None, saves to <skill_dir>/attachments/<filename-from-url>.
    Returns {"ok": True, "path": ..., "size": ..., "filename": ...}.
    """
    parsed = urllib.parse.urlparse(url)
    if not parsed.scheme.startswith("http"):
        return {"ok": False, "error": f"not an http(s) URL: {url}"}
    filename = urllib.parse.unquote(parsed.path.rsplit("/", 1)[-1]) or "attachment"

    if out is None:
        out_dir = Path(__file__).resolve().parent / "attachments"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / filename
    else:
        out_path = Path(out)
        out_path.parent.mkdir(parents=True, exist_ok=True)

    req = urllib.request.Request(url, headers={"User-Agent": "Vault-Handoff (0.1)"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
        out_path.write_bytes(data)
        return {"ok": True, "path": str(out_path), "size": len(data), "filename": filename}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}: {e.reason}", "_http_status": e.code}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# Discord channel type constants (https://discord.com/developers/docs/resources/channel#channel-object-channel-types)
_CH_GUILD_TEXT = 0
_CH_GUILD_ANNOUNCEMENT = 5
_CH_GUILD_FORUM = 15
_CH_GUILD_MEDIA = 16


def create_thread(parent_channel_id: str | int, name: str,
                  starter_content: str | None = None,
                  auto_archive_minutes: int = 1440,
                  token: str | None = None) -> dict:
    """Create a public thread under `parent_channel_id`.

    Supports both text-style channels (text/announcement) and forum-style
    channels (forum/media). Channel type is detected via GET /channels/{id}.

    - Text/announcement: if `starter_content` is given, posts it in the parent
      channel and anchors the thread to it; otherwise creates a standalone
      type-11 (public) thread.
    - Forum/media: posts are themselves threads and REQUIRE an initial message.
      `starter_content` is used; if missing, falls back to the thread name.

    auto_archive_minutes ∈ {60, 1440, 4320, 10080}.
    Returns Discord thread object with `id` on success.
    """
    pcid = str(parent_channel_id)

    ch = _get(f"/channels/{pcid}", token=token)
    ch_type = ch.get("type") if ch.get("ok") else None

    if ch_type in (_CH_GUILD_FORUM, _CH_GUILD_MEDIA):
        payload = {
            "name": name[:100],
            "auto_archive_duration": auto_archive_minutes,
            "message": {"content": (starter_content or name)[:2000]},
        }
        return _post(f"/channels/{pcid}/threads", payload, token=token)

    if starter_content:
        m = send(pcid, starter_content, token=token)
        if not m.get("ok") or not m.get("id"):
            return m
        return _post(
            f"/channels/{pcid}/messages/{m['id']}/threads",
            {"name": name[:100], "auto_archive_duration": auto_archive_minutes},
            token=token,
        )
    return _post(
        f"/channels/{pcid}/threads",
        {"name": name[:100], "type": 11, "auto_archive_duration": auto_archive_minutes},
        token=token,
    )


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Discord handoff helper (standalone).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_send = sub.add_parser("send", help="Send a message to a channel or thread.")
    p_send.add_argument("--target", required=True, help="Channel or thread ID.")
    p_send.add_argument("--text", help="Message text. If omitted and no --file, read stdin.")
    p_send.add_argument("--file", action="append", default=[],
                        help="Path to attach. Repeatable (max 10).")

    p_thr = sub.add_parser("create-thread", help="Create a new public thread.")
    p_thr.add_argument("--parent", required=True, help="Parent channel ID.")
    p_thr.add_argument("--name", required=True, help="Thread name (≤100 chars).")
    p_thr.add_argument("--starter", help="Optional starter message anchoring the thread.")
    p_thr.add_argument("--archive-minutes", type=int, default=1440,
                       choices=[60, 1440, 4320, 10080])

    p_att = sub.add_parser("fetch-attachment",
                           help="Download a Discord attachment URL to a local path.")
    p_att.add_argument("--url", required=True, help="Discord CDN URL from a message attachment.")
    p_att.add_argument("--out", help="Output path. Default: ./attachments/<filename>.")

    p_hist = sub.add_parser("history", help="Read channel or thread message history.")
    p_hist.add_argument("--target", required=True, help="Channel or thread ID.")
    p_hist.add_argument("--limit", type=int, default=20, help="Messages to fetch (max 100).")
    p_hist.add_argument("--before", help="Fetch messages before this message ID.")
    p_hist.add_argument("--max-chars", type=int, default=None, help="Truncate message text.")

    args = ap.parse_args()

    if args.cmd == "send":
        if args.text is not None:
            text = args.text
        elif args.file:
            text = ""
        else:
            text = sys.stdin.read()
        if not args.file and (not text or not text.strip()):
            print("ERROR: empty message and no files", file=sys.stderr); sys.exit(2)
        res = send(args.target, text, files=args.file or None)
        print(json.dumps(res, ensure_ascii=False))
        sys.exit(0 if res.get("ok") else 1)

    if args.cmd == "create-thread":
        res = create_thread(args.parent, args.name,
                            starter_content=args.starter,
                            auto_archive_minutes=args.archive_minutes)
        print(json.dumps(res, ensure_ascii=False))
        sys.exit(0 if res.get("ok") and res.get("id") else 1)

    if args.cmd == "fetch-attachment":
        res = fetch_attachment(args.url, out=args.out)
        print(json.dumps(res, ensure_ascii=False))
        sys.exit(0 if res.get("ok") else 1)

    if args.cmd == "history":
        sys.exit(_cli_history(args))
