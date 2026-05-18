# Codex CLI 인터페이스 스파이크 결과 (2026-05-18)

목적: hermes-coder-subagent plan Task 1. Codex CLI를 자식 프로세스로 spawn할 때 어떤 인터페이스를 쓸지(A: ACP wire-protocol via copilot_acp_client 재사용 / A1: codex exec --json 신규 client) 결정.

환경: codex-cli 0.121.0, GCP VM (Linux 6.17.0-1013-gcp), auth_mode=chatgpt.

## 경로 A (ACP wire-protocol via copilot_acp_client)

- 시도 결과: **FAIL** — Codex는 ACP를 직지원 안 함.
- 증거:
  - `codex app-server` (stdio JSON-RPC) — 자체 스키마(`thread/start`, `turn/start`) 사용, ACP의 `session/new`/`session/prompt`와 불일치. 시도 시 부팅 단계에서 bubblewrap 경고 후 응답 없음.
  - `codex exec-server` — websocket 전용 (`ws://127.0.0.1:44385` URL 반환), stdio 아님. hermes의 `copilot_acp_client`는 stdio subprocess 전제라 비호환.
  - `codex mcp-server` — MCP 도구 서버용. agent loop를 자체 turn으로 돌리지 못함 (코더 자식이 아니라 도구 한 줄 호출용).
- 결정: **미사용**.

## 경로 A1 (codex exec --json)

- 동작: **확인됨** (단 두 가지 환경 제약 있음 — 아래 참조).
- 명령: `codex exec --json --skip-git-repo-check --sandbox <MODE> "<goal>"`
- 관측된 NDJSON 이벤트 타입:
  - `thread.started` (필드: `thread_id`)
  - `turn.started`
  - `item.started` (필드: `item.id`, `item.type`, type별 추가 필드)
  - `item.completed` (필드: `item.id`, `item.type`, 결과 필드)
  - `turn.completed` (필드: `usage.input_tokens`, `usage.cached_input_tokens`, `usage.output_tokens`)
- 관측된 `item.type`:
  - `agent_message` (필드: `text`) — 코더의 plan/intent/요약 텍스트
  - `command_execution` (필드: `command`, `aggregated_output`, `exit_code`, `status`)
  - (예상되지만 미관측) `file_read`, `file_edit` — 실제 코딩 작업에서 발화될 것으로 예상, 실제 매핑은 Task 5 formatter 구현 시 추가 검증
- 결정: **primary 채택**.

### 환경 제약 #1: trusted directory 필요
`--skip-git-repo-check` 없이 실행하면 `Not inside a trusted directory and --skip-git-repo-check was not specified.` 에러. coder 자식 spawn 시 항상 `--skip-git-repo-check` 플래그 필요. (또는 cwd를 git repo로 강제.)

### 환경 제약 #2: 본 VM의 sandbox 호환성
default sandbox (bubblewrap network namespace)가 GCP VM에서 실패:
`bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`
→ 명시적 `--sandbox` 플래그 필수. 가능 값:
  - `read-only` — 너무 제한적 (파일 수정/명령 실행 불가)
  - `workspace-write` — workspace 내 쓰기 허용 (가장 균형), **권장 default**
  - `danger-full-access` — sandbox 없음. 본 spike에서 동작 확인용으로 사용. hermes 운영에서는 `delegate_tool.py`의 `_subagent_auto_deny`가 별도 게이트 — sandbox와 무관하게 dangerous 명령은 거부됨.

## 최종 채택 경로

- **A1 (codex exec --json) — primary**
- 채택 사유: A는 Codex가 ACP wire-protocol 직지원 안 함. A1은 동작 확인 + 충분히 풍부한 NDJSON 이벤트 (turn/item/command_execution 모두 캡처 가능).

## 후속 task 영향

- **Task 2 분기**: **분기 2B 진행** (Codex_exec_client.py 신규 작성). 분기 2A (env-only로 copilot_acp_client 재사용)는 폐기.
- **env vars 권장값** (`~/.hermes/.env`):
  ```
  HERMES_CODER_COMMAND=codex
  HERMES_CODER_ARGS=exec --json --skip-git-repo-check --sandbox workspace-write
  ```
  *주의*: `HERMES_COPILOT_ACP_COMMAND`/`HERMES_COPILOT_ACP_ARGS`는 GitHub Copilot CLI 경로 전용 — 본 통합에 사용하지 않음. 새 env var 이름(`HERMES_CODER_*`)으로 분리.
- **`copilot-acp` provider 미사용**: `delegate_task_background`의 provider 인자는 새 provider 이름 (예: `codex-exec`) 사용. Task 3 구현 시 명시.
- **Task 5 formatter**: `item.type` (`command_execution`, `agent_message`, 그 외 plan 단계에 미관측된 type들)에 대한 emoji mapping이 spec 5.3 표와 1:1로 매칭되지 않음. 실제 첫 라이브 시 추가 type 발견하면 formatter에 추가.
- **Task 12 config**:
  ```yaml
  delegation:
    coder:
      command: codex
      args: ["exec", "--json", "--skip-git-repo-check", "--sandbox", "workspace-write"]
      sandbox: workspace-write   # 또는 danger-full-access (opt-in)
  ```
- **취소(Task 11)**: Codex exec는 ACP session resume이 없음 → 취소는 subprocess SIGTERM/SIGKILL로 처리. spec 5.6의 `interrupt_subagent` 재사용은 자식 등록만 받쳐주면 동작.
- **Follow-up(Task 10)**: spec V1 단순화 (새 spawn + thread 재바인딩) 유지. Codex `--resume` 모드는 있지만 `--json`과 조합 시 동작 미검증, V2 검토.
