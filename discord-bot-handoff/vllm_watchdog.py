#!/usr/bin/env python3
"""vLLM watchdog — liveness + progress checks, restart only on a real stall.

Motivation
----------
2026-07-03: vLLM v0.21 hit a "silent stuck" state — /v1/models still responded
but /v1/chat/completions never scheduled new requests (no traceback, no error).
The API server + EngineCore stayed alive; only a restart recovered. This daemon
detects that class of failure.

2026-08-19: the original design probed by POSTing a tiny chat request. That
probe queues behind ordinary traffic, so a merely *busy* engine looked exactly
like a *dead* one. It cost us a false restart: during the three probe timeouts
that triggered it, the engine returned 21 successful chat completions. The
restart killed those in-flight requests (500s) and added a ~5 min cold start —
it made the congestion worse, which is the opposite of the job.

Design
------
Every INTERVAL seconds, decide from two signals that a queued request cannot
confuse:

  1. Liveness — GET /health. Unqueued, answers in ~1 ms even under load, so a
     timeout here means the API server itself is gone. Counts as a failure.
  2. Progress — scrape /metrics. If work is in flight (running + waiting > 0),
     the engine is healthy as long as its counters advance. Busy-but-advancing
     is never restarted, no matter how slow.

  A real stall = work in flight AND generated tokens, prompt tokens, and
  finished requests all frozen across STALL_POLLS consecutive polls.

  When the engine is idle the counters cannot prove anything, so we fall back
  to the original tiny-completion probe. With an empty queue it cannot be
  delayed by other traffic, which is exactly the condition the 2026-07-03
  failure appeared under.

Only anomalies are logged (JSON lines to stdout → journalctl):
    startup, slow, probe_failed, stalled, recovered, restart, restart_issued,
    restart_failed, heartbeat, fatal, stop.

Env (all optional except VLLM_API_KEY)
--------------------------------------
    VLLM_API_KEY               required — bot/vllm shared key from discord.env
    VLLM_ENDPOINT              default http://127.0.0.1:8000
    VLLM_SERVICE               default vllm.service
    VLLM_SYSTEMD_SCOPE         "user" (default, systemctl --user) or "system"
                               (system unit; run the watchdog as root)
    VLLM_WATCHDOG_INTERVAL     seconds between checks (default 60)
    VLLM_WATCHDOG_TIMEOUT      per-request HTTP timeout (default 30)
    VLLM_WATCHDOG_FAILS        consecutive liveness failures before restart (default 3)
    VLLM_WATCHDOG_STALL_POLLS  consecutive frozen-counter polls before restart (default 5)
    VLLM_WATCHDOG_COLD_START   seconds to wait after issuing restart (default 480 = 8m)
    VLLM_WATCHDOG_HEARTBEAT    log a heartbeat every N healthy checks (default 60 ≈ hourly)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

ENDPOINT = os.environ.get("VLLM_ENDPOINT", "http://127.0.0.1:8000").rstrip("/")
API_KEY = os.environ.get("VLLM_API_KEY", "").strip()
SERVICE = os.environ.get("VLLM_SERVICE", "vllm.service")
SYSTEMD_SCOPE = os.environ.get("VLLM_SYSTEMD_SCOPE", "user").strip().lower()
INTERVAL = int(os.environ.get("VLLM_WATCHDOG_INTERVAL", "60"))
REQ_TIMEOUT = int(os.environ.get("VLLM_WATCHDOG_TIMEOUT", "30"))
FAIL_THRESHOLD = int(os.environ.get("VLLM_WATCHDOG_FAILS", "3"))
STALL_POLLS = int(os.environ.get("VLLM_WATCHDOG_STALL_POLLS", "5"))
POST_RESTART_WAIT = int(os.environ.get("VLLM_WATCHDOG_COLD_START", "480"))
HEARTBEAT_EVERY = int(os.environ.get("VLLM_WATCHDOG_HEARTBEAT", "60"))
MODEL = os.environ.get("VLLM_MODEL_NAME", "local-llm")
SLOW_FRACTION = 0.5  # elapsed > timeout*fraction → "slow" event
HEALTH_TIMEOUT = 10  # /health is unqueued; anything slower is a real problem

# Counters that must advance while work is in flight. Summed across label sets.
_PROGRESS_METRICS = (
    "vllm:generation_tokens_total",
    "vllm:prompt_tokens_total",
    "vllm:request_success_total",
)
_QUEUE_METRICS = (
    "vllm:num_requests_running",
    "vllm:num_requests_waiting",
)
_SAMPLE_RE = re.compile(r"^(?P<name>vllm:[a-z_]+)(?:\{[^}]*\})? (?P<value>[0-9.eE+-]+)$")


def log(evt: str, **kw) -> None:
    kw.update({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "event": evt,
    })
    sys.stdout.write(json.dumps(kw, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _get(path: str, timeout: int) -> tuple[float, str | None, str]:
    """GET path. Returns (elapsed, error_or_None, body)."""
    req = urllib.request.Request(
        f"{ENDPOINT}{path}",
        headers={"Authorization": f"Bearer {API_KEY}"},
        method="GET",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return time.time() - t0, None, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return time.time() - t0, f"HTTP {e.code}: {e.reason}", ""
    except urllib.error.URLError as e:
        return time.time() - t0, f"URLError: {e.reason}", ""
    except Exception as e:  # pragma: no cover — belt & suspenders
        return time.time() - t0, f"{type(e).__name__}: {e}", ""


def health() -> tuple[float, str | None]:
    """Liveness only. /health does not queue behind inference work."""
    elapsed, err, _ = _get("/health", HEALTH_TIMEOUT)
    return elapsed, err


def scrape() -> dict[str, float] | None:
    """Sum the counters we track. None if /metrics is unavailable."""
    _, err, body = _get("/metrics", REQ_TIMEOUT)
    if err is not None:
        return None
    wanted = _PROGRESS_METRICS + _QUEUE_METRICS
    totals = {name: 0.0 for name in wanted}
    for line in body.splitlines():
        if not line or line[0] == "#":
            continue
        m = _SAMPLE_RE.match(line)
        if m is None:
            continue
        name = m.group("name")
        if name in totals:
            try:
                totals[name] += float(m.group("value"))
            except ValueError:
                continue
    return totals


def completion_probe() -> tuple[float, str | None]:
    """Tiny chat request — only meaningful when the queue is empty."""
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 5,
        "temperature": 0,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{ENDPOINT}/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=REQ_TIMEOUT) as resp:
            _ = resp.read(256)  # small read; we don't need the payload
        return time.time() - t0, None
    except urllib.error.HTTPError as e:
        return time.time() - t0, f"HTTP {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        # timeouts land here as URLError(reason=timeout)
        return time.time() - t0, f"URLError: {e.reason}"
    except Exception as e:  # pragma: no cover — belt & suspenders
        return time.time() - t0, f"{type(e).__name__}: {e}"


def restart_service(reason: str, **detail) -> bool:
    """Return True if restart command succeeded (does not wait for readiness)."""
    log("restart", reason=reason, service=SERVICE, **detail)
    proc = subprocess.run(
        ["systemctl", *(["--user"] if SYSTEMD_SCOPE == "user" else []), "restart", SERVICE],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        log("restart_failed", rc=proc.returncode, stderr=proc.stderr[:400])
        return False
    log("restart_issued", wait_seconds=POST_RESTART_WAIT)
    return True


def main() -> int:
    if not API_KEY:
        log("fatal", error="VLLM_API_KEY not set")
        return 2

    log(
        "startup",
        endpoint=ENDPOINT,
        service=SERVICE,
        interval=INTERVAL,
        timeout=REQ_TIMEOUT,
        threshold=FAIL_THRESHOLD,
        stall_polls=STALL_POLLS,
        cold_start=POST_RESTART_WAIT,
    )

    fails = 0          # consecutive liveness failures
    stalls = 0         # consecutive polls with work in flight and no progress
    ok_streak = 0
    last_progress: tuple[float, ...] | None = None

    while True:
        elapsed, err = health()

        if err is not None:
            fails += 1
            log("probe_failed", check="health", elapsed=round(elapsed, 2), error=err, fails=fails)
            if fails >= FAIL_THRESHOLD:
                restart_service("health_threshold_hit", fails=fails)
                time.sleep(POST_RESTART_WAIT)  # skip cold start, avoid restart loops
                fails = stalls = ok_streak = 0
                last_progress = None
                continue
            time.sleep(INTERVAL)
            continue

        if fails > 0:
            log("recovered", check="health", elapsed=round(elapsed, 2), prev_fails=fails)
            fails = 0
            ok_streak = 0

        metrics = scrape()
        if metrics is None:
            # /health answered but /metrics did not — treat as a soft miss and
            # retry rather than restarting on a scrape hiccup.
            log("probe_failed", check="metrics", error="scrape failed", fails=fails)
            time.sleep(INTERVAL)
            continue

        in_flight = sum(metrics[name] for name in _QUEUE_METRICS)
        progress = tuple(metrics[name] for name in _PROGRESS_METRICS)

        if in_flight > 0:
            advanced = last_progress is None or progress != last_progress
            last_progress = progress
            if advanced:
                if stalls > 0:
                    log("recovered", check="progress", prev_stalls=stalls)
                    stalls = 0
                ok_streak += 1
            else:
                stalls += 1
                log(
                    "stalled",
                    in_flight=int(in_flight),
                    stalls=stalls,
                    generation_tokens=progress[0],
                    prompt_tokens=progress[1],
                    finished=progress[2],
                )
                if stalls >= STALL_POLLS:
                    restart_service("progress_stalled", stalls=stalls, in_flight=int(in_flight))
                    time.sleep(POST_RESTART_WAIT)
                    fails = stalls = ok_streak = 0
                    last_progress = None
                    continue
        else:
            # Idle: counters prove nothing, so ask the scheduler directly. With
            # an empty queue this cannot be delayed by other traffic.
            last_progress = progress
            stalls = 0
            p_elapsed, p_err = completion_probe()
            if p_err is not None:
                fails += 1
                log("probe_failed", check="completion", elapsed=round(p_elapsed, 2), error=p_err, fails=fails)
                if fails >= FAIL_THRESHOLD:
                    restart_service("idle_probe_threshold_hit", fails=fails)
                    time.sleep(POST_RESTART_WAIT)
                    fails = stalls = ok_streak = 0
                    last_progress = None
                    continue
                time.sleep(INTERVAL)
                continue
            if p_elapsed > REQ_TIMEOUT * SLOW_FRACTION:
                log("slow", check="completion", elapsed=round(p_elapsed, 2))
            ok_streak += 1

        if ok_streak >= HEARTBEAT_EVERY:
            log("heartbeat", ok_streak=ok_streak, in_flight=int(in_flight))
            ok_streak = 0

        time.sleep(INTERVAL)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("stop", reason="SIGINT")
        sys.exit(0)
