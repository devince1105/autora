# 03 — Event Model

> Event 是「已發生的事實」。它是 Runtime 的審計軌跡、跨模組通知、也是 3D Office 與 Dashboard 的唯一即時資料來源。
> 一套 schema，三種用途；沒有「給前端用的假事件」。

---

## 1. Envelope（統一 Schema）

```jsonc
{
  "event_id":       "01J7…",            // ULID，全域唯一，可排序（產生時間）
  "seq":            184233,             // ★ 每個 company 內單調遞增的 bigint；排序、去重、replay 的唯一依據
  "event_type":     "AGENT_WORKING",    // 見 Catalog；UPPER_SNAKE
  "schema_version": 1,                  // payload schema 版本
  "company_id":     "…",
  "occurred_at":    "2026-09-16T06:12:31.204Z",   // Runtime 寫入時間（server clock）

  // 關聯鍵（可為 null；有值就建索引）
  "aggregate_type": "agent_run",        // 事件主體的類型
  "aggregate_id":   "…",
  "agent_id":       "…",
  "task_id":        "…",
  "run_id":         "…",
  "workflow_run_id":"…",
  "cycle_id":       "…",
  "correlation_id": "…",                // = workflow_run_id（newsroom）或 cycle_id（company loop）
  "causation_id":   "…",                // 觸發此事件的 event_id 或 command_id

  "actor":          { "kind": "agent" | "system" | "human", "id": "…" },
  "payload":        { }                 // 依 event_type 的 typed schema
}
```

### 欄位語意

| 欄位 | 誰產生 | 用途 |
|---|---|---|
| `seq` | Postgres（`events.seq bigserial`，配合 per-company 邏輯序：見 §6） | 前端 `last_seq`、gap 偵測、replay |
| `event_id` | Runtime | 冪等鍵（handler 去重）、causation 連結 |
| `occurred_at` | Runtime | 顯示時間、stale 判斷（只作顯示；排序一律用 seq） |
| `aggregate_*` | Runtime | Trace / audit 查詢 |
| `agent_id / task_id / run_id` | Runtime | 3D Office 的路由鍵（哪個 avatar、哪個 task 卡片） |
| `correlation_id` | Runtime | 「這篇文章的整條生產線」查詢 |
| `actor` | Runtime | 區分 agent 行為、系統行為（scheduler、governance）、人類操作 |

### Schema 定義的唯一來源
- pydantic models 在 `packages/autora/runtime/events/schema.py`（每個 `event_type` 一個 payload class）。
- CI 產生 JSON Schema → `packages/event-schema/`（TS types + zod validators）。前端 reducer 以 zod 驗證，驗證失敗的事件記錄並丟棄（不 crash）。

---

## 2. Event Catalog

圖例：**P** = 持久化（`events` 表，永不刪除）；**E** = ephemeral（只經 WS 推送，不落表）；**R** = 可 replay（用於重建投影）。

### 2.1 Agent（Layer 2 activity 與 run 生命週期）

| event_type | P/E | R | payload | 觸發者 |
|---|---|---|---|---|
| AGENT_CREATED | P | R | `{role, name, capabilities[]}` | Command |
| AGENT_PAUSED / AGENT_RESUMED | P | R | `{by, reason}` | Command / Governance |
| AGENT_RUN_STARTED | P | R | `{attempt, task_name, required_role, input_summary}` | AgentRunner |
| AGENT_THINKING | P | R | `{phase, step_seq}` | AgentRunner（think 步驟開始） |
| AGENT_WORKING | P | R | `{tool, tool_call_id, step_seq, progress?}` | AgentRunner（第一個 tool call 時；與 TOOL_CALLED 同 TX） |
| AGENT_WAITING | P | R | `{reason, approval_id?, blocked_task_id?, waiting_on_roles?}` | AgentRunner / TaskManager |
| AGENT_REVIEWING | P | R | `{phase, attempt, issues_count}` | AgentRunner（evaluate/repair） |
| AGENT_IDLE | P | R | `{reason: failure_acknowledged\|waiting_cleared\|initialized}` | ActivityService（明確回到 IDLE；COMPLETED 顯示期滿不發此事件） |
| AGENT_RUN_COMPLETED | P | R | `{output_summary, cost_usd, steps, duration_ms, handoff[], display_until?}` | AgentRunner |
| AGENT_RUN_FAILED | P | R | `{error_class, message, attempt, final: bool}` | AgentRunner |
| AGENT_RUN_ABORTED | P | R | `{reason: "budget"|"policy"|"timeout"|"human"}` | AgentRunner / CostGuard |
| AGENT_STEP_PROGRESS | **E** | — | `{step_seq, tokens_so_far, progress?}` | AgentRunner（streaming 進度；最多 1/秒） |
| AGENT_HEARTBEAT | **E** | — | `{run_id, alive_at}` | Worker |

> `AGENT_THINKING / WORKING / WAITING / REVIEWING` 就是 `agent_activity` 的轉換事件。
> 為什麼持久化：它們是 Trace 的骨幹（「14:22 開始搜尋」），也是重連 replay 的依據。每個 run 約 10–40 筆，成本可接受。

### 2.2 Tool

| event_type | P/E | R | payload |
|---|---|---|---|
| TOOL_CALLED | P | R | `{tool, tool_call_id, args_summary, side_effect, step_seq}` |
| TOOL_COMPLETED | P | R | `{tool, tool_call_id, duration_ms, result_summary, cost_usd?, produced?: [{type, id}]}` |
| TOOL_FAILED | P | R | `{tool, tool_call_id, error_class, message, will_retry}` |
| TOOL_DENIED | P | R | `{tool, decision: "DENY"|"NEEDS_APPROVAL", rule_id, approval_id?}` |

`produced[]` 是 domain 產物的引用（`{type:"evidence", id}`、`{type:"claim", id}`），Detail Panel 的
「Sources 37 / Claims 12」由此累計（前端計數的是真實產物事件，不是估計）。

### 2.3 Task / Workflow

| event_type | P/E | R | payload |
|---|---|---|---|
| WORKFLOW_RUN_CREATED | P | R | `{template, params, project_id, task_ids[]}` |
| WORKFLOW_RUN_COMPLETED / FAILED | P | R | `{duration_ms, outcome}` |
| WORKFLOW_RUN_EXTENDED | P | R | `{reason, round, task_ids[]}`（T-514：迴圈再加一輪任務，例如編輯要求修訂後的撰稿 + 審稿） |
| TASK_CREATED | P | R | `{name, required_role, depends_on[], workflow_run_id, budget_usd}` |
| TASK_READY | P | R | `{}` |
| TASK_STARTED | P | R | `{run_id, agent_id, attempt}` |
| TASK_WAITING | P | R | `{reason, approval_id?}` |
| TASK_SUCCEEDED | P | R | `{run_id?, output_ref, unlocks: [{task_id, required_role}]}` |
| TASK_FAILED | P | R | `{run_id, final, error_class}` |
| TASK_CANCELLED | P | R | `{reason}` |
| TASK_BLOCKED | P | R | `{reason: "budget"}` |

`unlocks[]`（由 DAG 計算）讓前端不必自己比對 depends_on 來找 handoff 目標。

### 2.4 Company / Cycle / Approval / Governance

| event_type | P/E | R | payload |
|---|---|---|---|
| CYCLE_STARTED / CYCLE_STAGE_CHANGED / CYCLE_COMPLETED | P | R | `{seq, stage, deadline}` |
| GOAL_CREATED / GOAL_UPDATED | P | R | `{level, metric, target, current}` |
| PROJECT_* (PROPOSED/APPROVED/PAUSED/RESUMED/KILL_PROPOSED/KILLED) | P | R | `{name, by, reason?}` |
| APPROVAL_REQUESTED / APPROVED / REJECTED / EXPIRED | P | R | `{kind, ref_type, ref_id, summary}` |
| POLICY_DENIED | P | — | `{actor, action, rule_id}` |
| BUDGET_ALLOCATED / BUDGET_EXHAUSTED / EXPENSE_RECORDED / REVENUE_RECORDED / PAYMENT_RECEIVED | P | R | 金額、project_id、category |
| KPI_SNAPSHOT_CREATED | P | R | `{cycle_id, metrics}` |

### 2.5 Newsroom（Business）

| event_type | P/E | R | payload |
|---|---|---|---|
| SOURCE_POLLED / SOURCE_ITEM_DISCOVERED | P（ITEM 可歸檔） | — | `{source_id, count}` / `{item_id, url, title}` |
| EVIDENCE_CAPTURED | P | R | `{evidence_id, url, story_id?}` |
| STORY_DISCOVERED / STORY_SELECTED / STORY_DROPPED | P | R | `{story_id, title, score}` |
| CLAIM_CREATED / CLAIM_VERIFIED / CLAIM_REJECTED | P | R | `{claim_id, story_id, claim_type, evidence_ids[]}` |
| ARTICLE_CREATED (draft v1) | P | R | `{article_id, story_id, version_id, lang[]}` |
| ARTICLE_REVISION_REQUESTED | P | R | `{article_id, version_id, issues_count, by_role, revision}`（T-511：第幾次修訂，最多 2 次） |
| ARTICLE_REVIEWED | P | R | `{article_id, version_id, verdict, fact_check_passed, by_role}`（T-511：`verdict` = accept / revise） |
| ARTICLE_APPROVED / ARTICLE_REJECTED | P | R | `{article_id, by}` |
| ARTICLE_PUBLISHED | P | R | `{article_id, slug, langs[], url}` |
| DISTRIBUTION_CREATED | P | R | `{article_id, channel, status}` |
| ANALYTICS_DAILY_UPDATED | P | R | `{article_id, date, views}` |（原始 beacon 不是 event）

### 2.6 Realtime 控制訊息（不是 Event；只存在於 WS 通道）

`HELLO`、`SNAPSHOT_REQUIRED`、`HEARTBEAT`、`ERROR` — 見 `05_REALTIME_ARCHITECTURE.md`。它們沒有 `seq`。

---

## 3. 持久化與 Replay 原則

- **持久化 = 進 `events` 表**，同一個 transaction 內與狀態變更一起 commit（outbox）。
- **Ephemeral** 只有高頻進度與心跳；它們透過 API process 的記憶體 fan-out，不經 DB（見 05 §7）。
- **Replay** 指「重建投影」（agent_activity 視圖、active tasks、trace、KPI），**不是**重建 domain state。
  Domain state 的真相永遠在狀態表；`events` 是它的影子。因此系統**不是 event-sourced**，也不需要 CQRS。
- 保留策略：`events` 永不刪除；`SOURCE_ITEM_DISCOVERED`、`AGENT_STEP_PROGRESS`（若日後決定持久化）可以按月歸檔到 Object Storage。

---

## 4. Trace 的定義

```
trace(run_id)  = events WHERE run_id = :run_id ORDER BY seq
               ⋈ agent_steps WHERE run_id = :run_id   (step 級細節：tool args、result、cost、blob)

trace(task_id) = ∪ trace(run_id) for runs of task
trace(article) = events WHERE correlation_id = article.workflow_run_id ORDER BY seq
```

使用者在 Trace Viewer 看到的每一行都是這個查詢的一列。範例（真實事件）：

```
06:21:03  TASK_STARTED          Find today's AI stories        run#a1 attempt 1
06:21:04  AGENT_THINKING        phase=plan
06:21:09  TOOL_CALLED           web_search  q="AI regulation EU September 2026"
06:21:11  TOOL_COMPLETED        web_search  12 results  0.9s
06:21:12  AGENT_WORKING         tool=fetch_url  progress 1/12
06:21:14  TOOL_COMPLETED        fetch_url   produced evidence:ev_7f…
…
06:24:40  AGENT_REVIEWING       evaluate  issues=0
06:24:41  AGENT_RUN_COMPLETED   cost $0.184  steps 19  handoff→analyst
06:24:41  TASK_SUCCEEDED        unlocks [task_analysis_…]
```

---

## 5. Handoff（Agent-to-Agent）如何從事件推導

Runtime 沒有「handoff」這個動作；它只有 DAG。但 3D 需要「Researcher 走到 Analyst」。

```
TASK_SUCCEEDED{task_id: A, run_id, unlocks:[B]}      → 事件 1
TASK_READY{task_id: B}  (payload 含 required_role)   → 事件 2（同 TX，seq 相鄰）
```

前端 Animation Director 規則：`on TASK_SUCCEEDED with unlocks → for each B: walk(agent_of_run(run_id) → desk_of_role(B.required_role), carry:"document")`。
`AGENT_RUN_COMPLETED.payload.handoff` 提供同樣資訊（給 Detail Panel 的「Next Step」用）。

若 B 的 role 是 `human`（approval）：walk 目標是「Approval 桌」（一個固定場景物件）。

---

## 6. `seq` 的實作註記

- MVP：`events.seq` 為全域 `bigserial`。單一公司時就是 per-company 單調。
- 多公司（Phase 6）：仍用全域 seq，前端只看自己公司的事件，`last_seq` 語意不變（gap 判斷改為「查詢 `seq > last_seq AND company_id = ?`」）。不需要 per-company sequence 物件。
- 注意 `bigserial` 在 transaction rollback 時會留空號：這是正常的，前端 gap 偵測用「查詢有無事件」而不是「數字連續」。

---

## 7. 版本策略

- 新增 payload 欄位：不升版（前端忽略未知欄位）。
- 移除/改語意：`schema_version + 1`，前端 zod schema 以 `discriminatedUnion(event_type, schema_version)` 處理，舊版事件仍可在 Trace Viewer 顯示。
- 新增 event_type：前端 reducer 預設忽略；Trace Viewer 以 raw 顯示。

---

## 8. 實作註記（T-105，2026-09-16）

- **Catalog 依層擁有**：runtime 事件在 `autora/runtime/events/catalog.py`；company 事件在 `autora/company/events.py`；newsroom 事件（§2.5）由 `autora/domains/newsroom` 在 Phase 5 註冊。Runtime 不定義 domain 事件。`PAYMENT_RECEIVED` 歸 business domain（Phase 7）。
- **`event_id` 使用 UUIDv7**（非 ULID）：同樣可依時間排序，且與所有主鍵共用原生 `uuid` 欄位型別。
- **新增 `AGENT_IDLE`**：activity 的 8 個狀態各自需要一個進入事件；原目錄缺 IDLE。
- **`handoff` 改為 list、`unlocks` 帶 `required_role`**：一個 task 可解鎖多個下游；前端不需再查表找角色。
- Actor 已在 envelope 上，project 類 payload 不再重複 `by`。
- 角色名稱統一為人稱名詞：`ceo, researcher, analyst, writer, editor, marketing`（另有 `human`、`system` 用於 task.required_role）。
- **`BUDGET_EXHAUSTED` 屬於執行環境層**（T-209）：由執行環境的成本守門員觸發；執行環境不可匯入公司層。payload 另含 `requested`（被拒呼叫的估計成本）。
- **`AGENT_IDLE` 新增 reason `run_ended`**（T-202）：run 以非最終失敗、租約回收或任務取消結束，agent 重新可用；非最終的 `AGENT_RUN_FAILED` / `AGENT_RUN_ABORTED` 仍持久化供 trace 使用但不改變 activity。
