---
name: discord-bot-handoff
description: 로컬 LLM으로 응답하는 Discord 봇에 "claude 한 단어 → Claude Code tmux 세션으로 에스컬레이션" 기능을 더한 실행 가능한 레퍼런스 봇. 다른 환경(OpenAI 호환 endpoint만 있으면)으로 이식 가능. "디스코드 봇 띄워줘", "핸드오프 봇 실행", "run the handoff bot", "로컬 LLM 디스코드 봇" 같은 발화에서 셋업/실행. discord-handoff 스킬 위에 얹혀 동작.
---

# discord-bot-handoff

로컬 LLM(OpenAI 호환 endpoint)으로 대화하는 최소 Discord 봇 + "특정 스레드를
Claude Code 세션으로 넘기는" 에스컬레이션 레이어. [`discord-handoff`](../discord-handoff/)
스킬과 짝을 이룬다 (그쪽이 넘겨받은 tmux 세션 안에서 실제 대화를 처리).

## 동작 요약

- 감시 채널/스레드에서 인증 유저가 메시지 → 로컬 LLM이 응답 (스레드별 최근 N턴 기억)
- 정확히 한 단어 `claude` / `클로드` → 그 스레드에 tmux Claude 세션 스폰, 봇은 침묵
- 정확히 한 단어 `local` / `로컬` → Claude 세션 종료, 로컬 모델 복귀
- Claude 세션이 죽으면 다음 메시지에서 자동으로 로컬 복귀
- 봇이 세션 pane 에 메시지를 넣는 방식(주입 모드)은 discord-handoff SKILL STEP 3 의 주입 모드 갈래와 표지 `[handoff-inject-mode]` 로 지원되며, 이 참조 봇은 구현하지 않는다 (스폰된 세션은 STEP 3 Monitor 갈래로 청취)

## 전제 조건

- `tmux`, `claude` CLI (PATH 또는 `CLAUDE_BIN`)
- `~/.claude/skills/discord-handoff/` 설치 (짝 스킬)
- OpenAI 호환 chat-completions endpoint 하나 (vLLM / mlx_lm.server / Ollama /
  LM Studio / llama.cpp / OpenAI 등 — README "Swapping the LLM endpoint" 참고)
- Discord 봇 토큰 + 인증 유저 id

## 셋업 & 실행

```bash
cd discord-bot-handoff
pip install --user -r requirements.txt

mkdir -p ~/.config/discord-bot-handoff
cp discord.env.example ~/.config/discord-bot-handoff/discord.env
chmod 600 ~/.config/discord-bot-handoff/discord.env
# → 토큰 / 유저ID / LOCAL_LLM_BASE_URL·MODEL 채우기

python -X utf8 bot.py
```

## 파일

| File | 역할 |
|---|---|
| `bot.py` | 메인 봇. on_message 라우팅(스위치 감지 → 로컬 응답 / Claude 에스컬레이션). 진입점. |
| `local_llm.py` | OpenAI 호환 `/chat/completions` 클라이언트 (aiohttp). |
| `supervisor.py` | `spawn_claude_handoff` / `responder_alive` / `stop_responder`. tmux + claude CLI만 의존, 내부 import 없음. |
| `parser.py` | `detect_switch(content, active)` — 정확한 한 단어 매칭. LLM 아님. |
| `store.py` | active 상태(local/claude) + 대화 이력 (aiosqlite). |
| `config.py` | env 파일 로더 + `Config`. |
| `discord.env.example` | 토큰·endpoint 템플릿. |

## HARD RULES

- `--dangerously-skip-permissions`가 기본 — Claude 세션이 임의 셸을 실행할 수 있으니
  신뢰된 단일 사용자 환경에서만.
- 토큰은 env 파일(mode 600) 또는 환경변수에만. 코드/README/frontmatter에 절대 박지 말 것.
- 스위치 트리거는 **정확히 한 단어**만 — "claude 써서 정리해줘" 같은 문장은 절대 전환 안 됨.
