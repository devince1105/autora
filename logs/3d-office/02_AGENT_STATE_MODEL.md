# 02 — Agent State Model: Runtime State vs Visual State

---

## 1. 四層狀態，只有前兩層是 Domain State

```
Layer 0  Task FSM          PENDING → READY → RUNNING → WAITING_APPROVAL → SUCCEEDED / FAILED / CANCELLED
         (tasks 表)         「這件工作」的生命週期。平台層已定義。

Layer 1  AgentRun FSM      CREATED → RUNNING → EVALUATING → REPAIRING → COMPLETED / FAILED / ABORTED
         (agent_runs 表)    「這次執行」的生命週期。平台層已定義。

Layer 2  Agent Activity    IDLE / THINKING / WORKING / WAITING / REVIEWING / COMPLETED / FAILED / PAUSED
         (agent_activity)   「這個人現在在幹嘛」。★ 本文件新增。由 Runtime 寫入，是 Domain State。

Layer 3  Visual State      pose, screen_on, desk_light, badge, walking_to, bubble
         (前端記憶體)        「畫面上怎麼呈現」。純函式 f(activity, role, tool)。永不持久化、永不回傳後端。
```

Layer 2 存在的理由：3D Office 與 Dashboard 需要的是「人」的狀態，不是「執行」的狀態。
一個 agent 可能沒有 run（IDLE）、可能有 PENDING task 等上游（WAITING）、可能被人暫停（PAUSED）——
這些都不是 AgentRun FSM 能表達的。把它做成 Runtime 維護的表，而不是前端從 tasks + runs 推導，
是為了讓 snapshot 與 event 永遠一致、讓前端沒有推測邏輯。

---

## 2. Agent Activity State（8 個）

| State | 意義 | 進入條件（Runtime 事件） | 離開條件 |
|---|---|---|---|
| **IDLE** | 沒有進行中的 run，沒有被指派但被阻塞的 task | 初始；`COMPLETED` / `FAILED` 顯示期結束；`PAUSED` 被恢復 | 任何 run 開始或 WAITING 條件成立 |
| **THINKING** | run 進入「think」步驟：model call 進行中且本步驟不含 tool call（規劃、推理、產出最終結構化結果） | `AGENT_STEP_STARTED{kind:"think"}` | 步驟結束 |
| **WORKING** | run 進入「act」步驟：正在呼叫或等待 tool（搜尋、抓取、寫入 draft…） | `TOOL_CALLED` | `TOOL_COMPLETED / TOOL_FAILED` 後下一步驟決定 |
| **WAITING** | run 或 task 被外部條件阻塞：human approval、預算、上游 task 未完成 | `TASK_WAITING{reason}` 或 `AGENT_WAITING{reason}` | 阻塞解除 |
| **REVIEWING** | run 進入 evaluate / repair：檢查自己的輸出（schema、validators）並修正 | `AGENT_STEP_STARTED{kind:"evaluate"|"repair"}` | 評估通過 → COMPLETED；失敗且用盡 → FAILED |
| **COMPLETED** | run 剛成功結束（短暫，Runtime 設定 `display_until`，預設 20 秒） | `AGENT_RUN_COMPLETED` | `display_until` 到 → IDLE（由 Runtime 在下一次 tick 或前端本地時鐘處理；見 §5） |
| **FAILED** | run 最終失敗（用盡 attempts）或被 ABORTED（預算/policy） | `AGENT_RUN_FAILED` / `AGENT_RUN_ABORTED`（最終） | 人 ack（REST）或新 run 開始 → 對應狀態 |
| **PAUSED** | Agent 定義被人或治理規則暫停（不接新 task） | `AGENT_PAUSED{by, reason}` | `AGENT_RESUMED` |

**關於 PLANNING**：使用者原始清單有 PLANNING。在 Runtime 中「plan」是 think 步驟的產物（結構化 plan），
不是獨立階段；獨立出來只會增加一個沒有新資訊的轉換。因此 **PLANNING 不是狀態**，而是
`THINKING.detail.phase = "plan"`。前端可以顯示「Planning…」文字，但它不是 FSM 節點。

**關於 SEARCHING / ANALYZING / WRITING**：同理，這些是 **WORKING 的 detail**（`detail.tool = "web_search"`），
不是狀態。Researcher 的 `WORKING + tool=web_search` 顯示為「Searching」，`WORKING + tool=extract_evidence`
顯示為「Extracting」。新增一個工具不需要新增狀態。

---

## 3. 狀態轉換圖

```
                    ┌────────────────────────────────────────────┐
                    ▼                                            │
   PAUSED ◀──▶   IDLE ──(run start: step think)──▶ THINKING       │
                    ▲                                 │           │
                    │                    ┌────────────┼───────────┤
                    │                    ▼            ▼           │
                    │                WORKING ◀──▶ THINKING        │
                    │                    │            │           │
                    │                    ▼            ▼           │
                    │                WAITING ──▶ (resume to prior)│
                    │                    │                        │
                    │                    ▼                        │
                    │                REVIEWING ─(pass)─▶ COMPLETED ┘
                    │                    │
                    │                    └─(exhausted/abort)─▶ FAILED ──(ack / new run)──▶ IDLE
                    │
                    └── (task READY assigned to this role, deps unmet) ──▶ WAITING{reason:"upstream"}
```

守則：
- 只有 Runtime（`AgentRunner`、`TaskManager`、`Governance`）可以寫 `agent_activity`。
- 每次寫入伴隨一個 `AGENT_*` 事件，且在同一 transaction。
- `agent_activity.since` 是狀態進入時間；`detail` 是 jsonb，schema 依 state 而定（見 §4）。

---

## 4. `agent_activity.detail` Schema

```jsonc
// THINKING
{ "phase": "plan" | "reason" | "finalize", "step_seq": 3 }

// WORKING
{ "tool": "web_search", "tool_call_id": "…", "step_seq": 5,
  "progress": { "label": "sources", "current": 37, "target": 40 } }   // 可選；由 tool/agent 提供

// WAITING
{ "reason": "approval" | "budget" | "upstream" | "rate_limit",
  "approval_id": "…",            // reason=approval
  "blocked_task_id": "…",        // reason=upstream
  "waiting_on_roles": ["researcher"] }

// REVIEWING
{ "phase": "evaluate" | "repair", "attempt": 1, "issues_count": 2 }

// COMPLETED
{ "run_id": "…", "task_id": "…", "summary": "…", "display_until": "2026-09-16T08:12:00Z",
  "handoff": { "to_role": "analyst", "task_id": "…" } }             // 由 workflow 下游決定，非 LLM

// FAILED
{ "run_id": "…", "error_class": "BudgetExceeded" | "EvaluationFailed" | "ToolError" | "ProviderError", "message": "…" }

// PAUSED
{ "by": "human" | "governance", "reason": "…" }

// 所有狀態共用（可選）
{ "task_id": "…", "task_name": "…", "run_id": "…", "workflow_run_id": "…",
  "links": [ { "label": "Story", "href": "/newsroom/stories/…" } ] }
```

`links[]` 由 domain 的 `activity_links(task)` hook 提供（newsroom 提供 story / article 連結），
前端不硬編碼路徑。

---

## 5. COMPLETED 的短暫顯示

「剛完成」是一個真實資訊（使用者要看「最近完成什麼」），但讓 Runtime 為了 20 秒後回 IDLE
而排一個 job 是浪費。決定：
- Runtime 寫入 `COMPLETED` 並附 `display_until`。
- **若在 `display_until` 前有新 run 開始，狀態自然覆蓋。**
- 否則 Runtime **不主動**寫回 IDLE；snapshot 端點在讀取時若 `state=COMPLETED AND display_until < now()` 則回傳 `IDLE`（投影規則），前端 reducer 也套用同一規則（本地時鐘 + server offset）。
- 這是唯一允許前端「基於時間」改變顯示的地方，且它是投影規則的鏡像，不是推測。

---

## 6. 各角色的典型序列（真實事件對應）

### Researcher
```
IDLE
→ THINKING{phase:plan}          AGENT_RUN_STARTED, AGENT_STEP_STARTED(think)
→ WORKING{tool:web_search}      TOOL_CALLED(web_search)
→ WORKING{tool:fetch_url}       TOOL_CALLED(fetch_url) ×N        → 畫面文字 "Extracting sources (12/40)"
→ THINKING{phase:finalize}      AGENT_STEP_STARTED(think)
→ REVIEWING{evaluate}           AGENT_STEP_STARTED(evaluate)
→ COMPLETED{handoff:analyst}    AGENT_RUN_COMPLETED, TASK_SUCCEEDED
→ IDLE
```

### Analyst
```
IDLE → WAITING{upstream:research}   TASK_CREATED(analysis, depends_on research)  ← 「等 Researcher」
→ THINKING → WORKING{tool:read_evidence} → WORKING{tool:create_claim} ×M → REVIEWING → COMPLETED{handoff:writer}
```

### Writer
```
WAITING{upstream:analyst} → THINKING{plan} → WORKING{tool:write_draft, progress:{label:"sections", 3/5}}
→ REVIEWING → COMPLETED{handoff:editor}
```

### Editor
```
WAITING{upstream:writer} → THINKING → WORKING{tool:read_draft} → WORKING{tool:run_fact_check}
→ THINKING{finalize} → REVIEWING → COMPLETED{handoff: (approval) }
   若 request_revision → COMPLETED{handoff:writer}（真實：新 task 建立給 writer）
```

### CEO
```
IDLE → THINKING{plan}(cycle PLANNING) → WORKING{tool:submit_command(instantiate_workflow)} → COMPLETED
… 一天中大部分時間 IDLE …
→ THINKING{reason}(cycle REVIEWING) → COMPLETED
```

### Marketing
```
WAITING{upstream:publish} → THINKING → WORKING{tool:create_distribution} → COMPLETED
```

---

## 7. Visual State（前端）

Visual State 是純函式：`visual(activity, role) → VisualState`。它**不儲存、不傳回後端、不參與任何判斷**。

```ts
type VisualState = {
  pose: "sit_idle" | "sit_think" | "sit_type" | "sit_read" | "stand" | "walk" | "slump";
  screen: "off" | "dim" | "active" | "alert";
  deskLight: "off" | "on" | "blink_amber" | "blink_red";
  badge: { text: string; tone: "muted" | "info" | "active" | "warn" | "error" | "success" };
  bubble?: string;                    // 短文字，如 "Searching…"
  walkTo?: { targetAgentId: string; carry: "document" | "none"; returnAfter: boolean };
};
```

### 對照表

| Activity | Role 例外 | pose | screen | deskLight | badge | bubble |
|---|---|---|---|---|---|---|
| IDLE | — | sit_idle | dim | off | Idle / muted | — |
| THINKING | CEO 用 sit_think + screen active | sit_think | active | on | Thinking / info | phase 文字 |
| WORKING | Editor: tool=read_* → sit_read；其餘 sit_type | sit_type | active | on | Working / active | tool label + progress |
| WAITING{approval} | — | sit_idle | alert | blink_amber | Needs approval / warn | "Waiting for approval" |
| WAITING{upstream} | — | sit_idle | dim | on | Waiting for {role} / info | — |
| WAITING{budget} | — | sit_idle | alert | blink_amber | Budget blocked / warn | — |
| REVIEWING | — | sit_read | active | on | Reviewing / info | "Checking output" |
| COMPLETED | 若 detail.handoff → walk cue | stand→walk→sit_idle | active | on | Done / success | summary |
| FAILED | — | slump | alert | blink_red | Failed / error | error_class |
| PAUSED | — | sit_idle | off | off | Paused / muted | — |

**規則**：
- 對照表是前端唯一的「狀態→畫面」知識；它在 `office3d/visual/mapping.ts`，有單元測試。
- Handoff 的 walk 是 cue，不是狀態：agent 的 activity 已是 IDLE 時它可能還在走路回座位——這是允許的視覺延遲。
- 沒有任何 visual 值會被寫回 store 的 domain 區塊。

**實作註記（T-403，2026-09-19）**：`office3d/visual/mapping.ts` 依上表實作，差異如下——
- 標籤與泡泡用 zh-TW（D-002），用詞與 2D 代理卡片相同（`src/labels.test.ts` 檢查兩邊一致）；WAITING{upstream} 顯示「等待研究員」等實際角色。
- 事件中的 `handoff` 是**陣列**（`AGENT_RUN_COMPLETED.handoff: Handoff[]`），不是 §4 寫的單一物件；mapping 取第一個有效的交接。
- `walkTo` 用 `targetRole`（加上 `taskId`），不是 `targetAgentId`：mapping 只看（activity, role），角色 → 桌位由 layout / Courier（T-402、T-408）決定。COMPLETED 的 pose 為 `stand`，走路由 cue 執行。
- 多一列 WAITING{rate_limit}（事件定義有此原因，上表沒有）：與 upstream 相同的外觀，標籤「等待限流解除」。
- FAILED 來自 `AGENT_RUN_ABORTED` 時沒有 `error_class`，泡泡改顯示中止原因（預算用盡 / 政策拒絕 / 逾時 / 人工中止）。
- CEO 的 THINKING 例外（sit_think + active）與其他角色相同，因此沒有特別分支。

---

## 8. 不變式（測試會驗證）

1. `agent_activity` 的每一次變更都有對應的 `AGENT_*` event，且 `event.seq` 單調。
2. 對任一時間點 T：`project(snapshot(T0), events(T0..T)) == snapshot(T)`（agents 部分）。
3. 前端 reducer 對未知 `event_type` 的行為是「忽略並記錄」，不會產生任何狀態變更。
4. 前端不存在任何從 `tasks` / `runs` 推導 `activity.state` 的程式碼路徑（lint rule：`office3d/**` 與 `realtime/**` 禁止 import `tasks` 相關 selector 來計算 agent state）。
