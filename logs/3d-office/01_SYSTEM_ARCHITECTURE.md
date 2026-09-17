# 01 — System Architecture: 3D Office as the Visual Operating Environment

> 範圍：這份文件說明 3D Office 在 Autora 整體系統中的位置、與 Agent Runtime 的關係、
> 以及十個核心整合問題的答案。平台層（Runtime、Company Engine、Permission、Memory、Model Gateway）
> 的細節見 `logs/platform/`，本文件只引用，不重複。

---

## 1. 一句話

**3D Office 是 Autonomous Company Runtime 的「Presence Layer」：一個唯讀的、事件驅動的投影。
它讓人看到公司正在自己工作；所有「做事」的能力都在 Runtime，所有「操作」的入口都在 Dashboard / Command API。**

---

## 2. 分層與資料流

```
┌────────────────────────────────────────────────────────────────────────┐
│ Presence Layer        3D Office (R3F)   ◀── 只讀 store，只送 UI 意圖     │
│ Control Layer         Dashboard / Detail Panels / Approval Inbox        │
├────────────────────────────────────────────────────────────────────────┤
│ Client State          Zustand realtime store  +  TanStack Query (server) │
├────────────────────────────────────────────────────────────────────────┤
│ Realtime Transport    WebSocket /ws/companies/{id}   (events, seq-ordered)│
│ Command Transport     REST  /api/...                 (approve, start…)    │
├────────────────────────────────────────────────────────────────────────┤
│ API Process (FastAPI) WS Gateway ── Postgres LISTEN ── Realtime Projection│
├────────────────────────────────────────────────────────────────────────┤
│ Company State         PostgreSQL: agents, agent_activity, tasks, runs,   │
│                       events(seq), articles, ...                          │
├────────────────────────────────────────────────────────────────────────┤
│ Worker Process        Scheduler · TaskManager · AgentRunner · Tools ·     │
│                       ModelGateway · PolicyEngine · Domains(newsroom)     │
└────────────────────────────────────────────────────────────────────────┘

正向流（唯一合法方向）：
  AgentRunner 做事 → 在同一個 DB transaction 內：更新狀態表 + 更新 agent_activity + 寫 events(seq)
  → NOTIFY → API 的 WS Gateway 讀出 events → 推送 → 前端 store reducer → 3D 動畫

反向流（使用者操作）：
  3D 點擊 → UI store(selectedAgentId) → Detail Panel 用 REST 讀資料
  Approve / Start Task 按鈕 → REST Command → PolicyEngine → 狀態變更 → 產生 events → 回到正向流
```

**硬規則（CI 可檢查）**：
1. `apps/web/src/office3d/**` 不得 import 任何 API client、不得使用 fetch/WebSocket；只能訂閱 store。
2. 前端沒有任何「推測狀態」的程式碼：Agent 的 `activity.state` 永遠來自後端 `agent_activity`（snapshot 或 event）。
3. WebSocket 是單向（server → client）資料通道；client → server 只有 `hello / ack / ping`。所有操作走 REST Command。
4. 3D 動畫的時間長度是視覺參數（走路 2 秒），永遠不會反向影響任何狀態。

---

## 3. 十個核心問題

### 3.1 3D Office 在整體系統中的位置
- 它是 `apps/web` 的一個 route（`/office`），與 Dashboard（`/dashboard`）、Newsroom 內容（`/newsroom/*`）、Trace（`/trace/*`）並列。
- 它與 Dashboard 共用同一個 realtime store 與同一份 server snapshot；差別只在渲染方式（3D 場景 vs 表格卡片）。
- 它不擁有資料，也不擁有任何 domain 邏輯。刪掉 `office3d/` 目錄，系統功能完全不變。

### 3.2 Agent Runtime 如何通知前端
- Runtime 不「通知前端」。Runtime 只做一件事：在狀態變更的同一個 transaction 內寫 `events`（含單調遞增 `seq`）並 `NOTIFY autora_events, '<company_id>:<seq>'`。
- API process 的 **WS Gateway** 有一個 listener coroutine：收到 NOTIFY → 從 `events` 撈 `seq > cursor` → 依 company 廣播給訂閱的 sockets → 更新 cursor。
- 這條路徑不引入 Redis / message broker。多個 API process 時 Postgres NOTIFY 天然 fan-out。（Phase 6 以後若 API 水平擴展到 >3 個 process 再評估 Redis pub/sub。）

### 3.3 Company State 如何同步
- **Snapshot + Delta**。連線時（或重連 gap 過大時）前端呼叫 `GET /api/companies/{id}/realtime/snapshot`，得到 **Realtime Projection**：`{last_seq, agents[] (含 activity), active_tasks[], recent_events[], kpis}`。
- 之後只吃 `seq > last_seq` 的 events，用同一套 **reducer** 更新 projection。
- 關鍵設計：**Snapshot 與 reducer 的投影規則在後端定義一次**（`realtime/projection.py`），前端 reducer 只是它的鏡像，並有 contract test 驗證「snapshot ⊕ events == 下一次 snapshot」。

### 3.4 Task 如何同步
- Task 是 Runtime 的一等公民（`tasks` 表，FSM）。前端關心的是 **active tasks**（READY / RUNNING / WAITING_APPROVAL / BLOCKED）與最近完成的。
- Task 事件：`TASK_CREATED / TASK_READY / TASK_STARTED / TASK_WAITING / TASK_SUCCEEDED / TASK_FAILED / TASK_CANCELLED`，payload 含 `required_role, depends_on, workflow_run_id, name, progress?`。
- 前端 store 維護 `tasks: Map<task_id, TaskView>`；完成的 task 保留 N 分鐘後清出（純顯示行為）。

### 3.5 Agent State 如何同步
- 後端新增 **`agent_activity`** 表（每個 agent 一列）：`state, detail, run_id, task_id, tool, since`。Runtime 在每次狀態轉換的同一 transaction 更新它並發出 `AGENT_*` 事件。
- 因此 snapshot（讀表）與 event（推播）永遠一致；前端從不推導。
- Agent Activity 的 8 個狀態與 Runtime 內部 FSM 的關係見 `02_AGENT_STATE_MODEL.md`。

### 3.6 Event 如何轉換成 3D Animation
- 前端有一個 **Animation Director**（純函式 + cue queue）：`(event, currentProjection) → VisualCue[]`。
- VisualCue 例：`{kind:"set_pose", agentId, pose:"typing"}`、`{kind:"walk", agentId, from:deskA, to:deskB, carry:"document", then:"return"}`、`{kind:"flash", agentId, color:"red"}`。
- 3D 層只消費 cue，不看 event。Cue 是可壓縮的：同一 agent 在 200ms 內的多個 `set_pose` 只留最後一個；`walk` 若目標 agent 已開始工作則縮短。
- 「Agent-to-Agent 溝通」的 cue 來自兩個真實事件的組合：`TASK_SUCCEEDED(A)` 之後的 `TASK_READY(B, depends_on ∋ A)` → `walk(agent_of(A) → desk_of(role(B)))`。沒有任何 timer 觸發的動畫。

### 3.7 使用者點擊 Agent 後如何取得詳細資訊
- Raycast 命中 avatar → `uiStore.selectAgent(agentId)`。
- Detail Panel 的資料分兩層：
  - **即時層**（來自 realtime store）：state、current task、current tool、since。
  - **明細層**（REST，TanStack Query，key 帶 `run_id`）：`GET /api/runs/{run_id}` → steps 摘要、tool 統計、產出計數（sources、claims）、`next_step`（由 workflow template 的下游 task 決定，不是 LLM 說的）。
- Progress 百分比 = 該 run 已完成的 steps / `max_steps`？不。這會是假數字。**Progress 只在 task 有可量化子目標時顯示**（例：Research task `target_sources=40, found=37` → 92%），否則顯示 elapsed time。這是 Runtime 在 payload 提供的 `progress` 欄位，前端不計算。

### 3.8 如何查看 Agent Trace
- `GET /api/runs/{run_id}/trace` = `events WHERE run_id = ? ORDER BY seq` **join** `agent_steps`（每個 step 的 tool calls、cost、blob 連結）。
- Trace Viewer 顯示的每一行都是一筆真實 event 或 step；沒有前端合成的行。
- 使用者也可以從 Task、Article、Cycle 進入同一個 viewer（過濾條件不同：`task_id`、`correlation_id`）。

### 3.9 如何查看 Agent Output
- 每個 `agent_runs.output` 是符合 task `output_schema` 的 JSON。Detail Panel 的「Output」tab 用 schema-aware renderer 顯示（ResearchNote → sources/claims 列表；ArticleDraft → 文章預覽）。
- 大型 payload（完整 prompt/response）在 BlobStore，透過 `GET /api/runs/{id}/steps/{seq}/blob` 取用（只有 admin）。

### 3.10 如何從 3D Office 跳到實際 Newsroom Content
- 每個 Agent 的 Detail Panel 依角色提供 **domain deep link**（由後端 `agent_activity.detail.links[]` 給出，前端不硬編碼路徑）：
  - Researcher → `/newsroom/stories/{story_id}`（sources、evidence）
  - Analyst → `/newsroom/stories/{story_id}#claims`
  - Writer → `/newsroom/articles/{article_id}/versions/{version_id}`（draft）
  - Editor → 同上 + review issues
  - Marketing → `/newsroom/articles/{article_id}#distribution`
  - CEO → `/dashboard/cycles/{cycle_id}`
- 內容頁面本身是 Newsroom domain 的一般頁面，與 3D 無關。

---

## 4. 產品定位（回答「3D Office 應該是什麼」）

| 階段 | 定位 | 說明 |
|---|---|---|
| **MVP** | **D. Demo showcase，但建立在真實 Runtime 之上**（不是假的 demo） | 目的：驗證「事件驅動可視化」這條管線端到端可行，並讓人在 30 秒內理解這是一家會自己工作的公司。範圍：6 agents、一間辦公室、Research → Analyst → Writer → Editor → Publish 一條線。 |
| **正式產品** | **C. Digital Twin / Visual Operating System 的 Presence 層 + A. Dashboard 是 Control 層** | 3D Office 回答「公司現在在做什麼、誰在等誰、哪裡卡住」；Dashboard 回答「數字如何、要批准什麼、要調什麼」。兩者是同一個 store 的兩種投影，互相深連結。3D 不是主 UI，但它是「進門第一眼」與「異常的視覺化入口」（紅燈桌子 = 有 FAILED / 待審批）。 |
| **Demo（對外）** | **B/D. 主要 UI + showcase**，使用 deterministic simulation | 用 `FakeModelProvider + FakeTools` 跑真實 Runtime，讓每次 demo 的劇本可重現、成本為零；但事件、狀態、trace 全部真實產生。切換到真模型只改 provider 設定。 |

結論：**不選單一答案。3D Office 的本質是 C（Digital Twin 的 Presence 層），MVP 以 D 的規模建造，正式產品中與 A 分工，對外 Demo 時它扮演 B。** 決定性的設計後果：3D 層從第一天就只吃 store，這樣它在四種定位間切換不需要重寫。

---

## 5. 與平台層的關係（引用）

| 平台元件（`logs/platform/`） | 本文件的用法 |
|---|---|
| TaskManager / AgentRunner / FSM | 事件與 `agent_activity` 的唯一寫入者 |
| EventBus (Postgres outbox) | 增加 `seq`、`agent_id/task_id/run_id` 索引欄位、NOTIFY |
| PolicyEngine / Approvals | Approval 的 UI 入口在 Dashboard；3D 只顯示「等待審批」燈號 |
| Cycle loop | CEO 的 3D 行為對應 PLANNING / REVIEWING 兩個階段 |
| Newsroom domain | 6 個 MVP agents 的 workflow template `story_to_article_v2`（含 Analyst 與 Marketing） |

---

## 6. 明確不做（MVP）

- 3D 內的操作按鈕（approve / start）— 全部在側邊 panel（HTML），不做 3D 按鈕。
- 多樓層、多公司同場景。
- 語音 / 聊天泡泡顯示 LLM 原文（trace viewer 才顯示）。
- 動畫過場劇本（cinematic camera）。
