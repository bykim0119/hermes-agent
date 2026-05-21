# Coder subagent — end-to-end live walkthrough

Manual scenarios for Task 13. Run each scenario on Discord against the
main hermes gateway, record PASS/FAIL with date + branch SHA. The point
isn't to re-verify per-unit behavior (already covered by automated
tests) — it's to catch integration regressions where the wiring crosses
process boundaries (codex CLI, Discord platform, gateway runtime).

Branch under test: `feature/coder-subagent`
Last commit at test time: `4504f889c` (Task 12 — config + auth pre-check)
Test runner: bukim (operator)

## Scenarios

### 1. Golden path — natural-language delegation — ⚠️ PARTIAL (2026-05-21)
- [x] Mentioned `@Hermes ` with `/tmp/scratch_e2e.py에 hello world print 한 줄짜리 main 함수 추가하고 호출도 해줘`
- Observed: parent agent picked `write_file` directly and produced the one-liner in-turn. No coder thread spawned.
- Diagnosis: not a coder-pipeline regression — the LLM judged the task too trivial to delegate. AGENTS.md V1 nudges toward `delegate_task_background` but the parent still exercises judgement, and "single print line" is below its threshold.
- Follow-up: AGENTS.md V2 (separate task) — strengthen the rule that *any* file-writing task in a workspace context goes through the coder, or remove `delegate_task` from the parent toolset so the only delegation surface is the background variant.

### 2. `/code` slash command — ✅ PASS (2026-05-21)
- [x] `/code /tmp/cfg-sanity.py에 "config wins" print 한 줄 만들어줘`
- Thread spawned, command_execution + agent_message + ✅ 완료 (153 out tokens), file written.

### 3. CJK progress messages — ✅ (carryover, Task 9 smoke15)
- Korean spacing in thread progress messages confirmed during Task 9 AGENTS.md guide work; not re-verified on this build (low regression risk — formatter unchanged).

### 4. Concurrent coders — ✅ PASS (2026-05-21)
- [x] Spawned `/code /tmp/long_a.log ...` and `/code /tmp/long_b.log ...` in quick succession (10s bash sleep loops)
- Both threads active simultaneously, progress events did not cross-contaminate.

### 5. Thread follow-up via `codex exec resume` — ✅ (carryover, Task 10 commit `981ea0209`)
- Live-verified during Task 10 ("제곱도 추가해줘" follow-up landed in same thread, codex resumed with prior workspace).

### 6. Cancellation via `!cancel` — ✅ PASS (2026-05-21)
- [x] `/code 30초간 매 초마다 sleep하고 hello 출력` → coder thread → `!cancel`
- Thread received `❌ 취소됨`, `pgrep -af codex` returned no results (process-group SIGTERM cleared bash children).

### 7. max_concurrent saturation — ✅ PASS (2026-05-21)
- [x] After 3 active coders, 4th `/code` call landed
- Parent channel posted `⚠️ max_concurrent (3) coder sessions active. Wait for one to finish or cancel an existing thread.`
- Note V1 quirk: the rejected attempt still creates an empty thread (bind() raises *after* the Discord API thread is provisioned), which auto-archives at 1440min. Cosmetic — does not block usage.

### 8. OAuth missing pre-check — ✅ PASS (2026-05-21)
- [x] `mv ~/.codex/auth.json ~/.codex/auth.json.bak` → `/code test auth check` → ephemeral `❌ Codex auth.json 없음 — codex login...`
- No thread created. auth.json restored, subsequent spawns succeed.

## Run log

| Date (UTC) | Branch SHA | Scenarios run                | Result               |
|-----------:|:-----------|:-----------------------------|:---------------------|
| 2026-05-21 | 4504f889c  | 1, 2, 4, 6, 7, 8             | 5 PASS, 1 PARTIAL (1) |

## Known follow-up

* Scenario 1's PARTIAL is the trigger for **AGENTS.md V2**: parent's `delegate_task_background` use is not deterministic enough for trivial coding tasks. V2 will either (a) tighten the AGENTS.md rule to mandate background delegation for *all* code-modifying intents, or (b) remove the in-turn `delegate_task` tool from the parent's toolset so the only path is the coder.
