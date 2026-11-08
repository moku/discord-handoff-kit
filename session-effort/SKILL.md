---
name: session-effort
description: 실행 중인 Claude Code 세션의 노력(effort) 수준을 **그 세션에만** 바꾸거나 확인한다. 기본값(settings.json)은 건드리지 않는다. 자기 세션이든 다른 세션(디스코드 브리지 스레드 등)이든 tmux pane으로 조작한다. Trigger — "이 세션 노력 xhigh로", "노력 high로 바꿔줘", "effort 올려줘/내려줘", "X 스레드 세션 노력 바꿔줘", "지금 노력 뭐야", "노력 확인해줘".
---

# session-effort

`/effort <level>`처럼 값을 바로 주면 **기본값까지 저장된다** ("saved as your default for new sessions"). 이 스킬은 대신 인자 없는 `/effort` 선택 화면을 열고, 화살표로 수준을 고른 뒤 `s`("s for this session only")를 눌러 **그 세션에만** 적용한다.

사용자가 "기본값을 바꿔"라고 명시하지 않는 한 settings 파일은 손대지 않는다.

## 사용

```bash
S=~/.claude/skills/session-effort/set_effort.py
python3 $S xhigh                         # 이 세션 (현재 tmux pane)
python3 $S high --target 123456789012345678    # 디스코드 스레드 id → tmux 세션 agent-<id>
python3 $S medium --target mysession:0.0       # 임의 tmux target (세션명, pane id %N 등)
python3 $S --show [--target ...]         # 현재 수준만 읽기 (선택 화면 열고 Esc)
```

수준: `low` `medium` `high` `xhigh` `max`.

## 반드시 지킬 것

- **자기 세션을 바꿀 때는 Bash `run_in_background: true`로 실행하고 턴을 끝낸다.** 스크립트는 pane이 쉴 때까지(상태줄에 "esc to interrupt"가 없을 때까지) 기다린 뒤 입력한다. 턴 안에서 foreground로 돌리면 자기 답이 끝나지 않아 끝까지 기다리다 시간 초과로 끝난다. 완료 알림이 오면 출력의 `RESULT` 줄을 읽고 사용자에게 알린다.
- 다른 세션을 바꿀 때도 그 세션이 답하는 중이면 끝날 때까지 기다린다(기본 최대 900초, `--timeout`). 오래 걸릴 수 있으니 background로 돌린다.
- 입력창에 쓰다 만 글이 있으면 스크립트는 입력하지 않고 `ERROR`로 끝난다. 사용자의 초안을 덮어쓰지 않기 위해서다. 사용자에게 알리고 다시 시도한다. 흐리게(dim) 보이는 "다음 입력 제안" 문구는 초안이 아니므로 무시한다(`capture-pane -e`로 구분). 그 제안 문구를 사용자 요청으로 읽지도 마라.
- 성공 판정: 선택 화면이 닫혔고, 상태줄 `◉ <level>`이 목표와 같거나 보이지 않으며, `~/.claude/settings.json`과 그 pane의 cwd 아래 `.claude/settings*.json`의 해시가 바뀌지 않았다. 해시가 바뀌었으면 `ERROR`와 `settings_changed`가 나온다. 이때는 사용자에게 알리고, 사용자가 원하면 그때 되돌린다.
- `/effort <level>`(인자 있는 형태)을 tmux에 직접 보내지 마라. 기본값이 바뀐다.
- 새 세션을 띄울 때 수준을 정하려면 `claude --effort <level>`을 쓴다. 이것도 그 세션에만 적용된다.

## 참고

- discord-bot-handoff 봇이 띄운 세션의 tmux 세션 이름은 `agent-<thread_id>`이다. 목록은 `tmux list-panes -a -F '#{session_name} #{pane_id} #{pane_title}'`로 본다.
- 출력 예: `RESULT {"pane": "%338", "before": "high", "after": "xhigh", "status_line": "xhigh", "message": "...", "settings_changed": []}`
