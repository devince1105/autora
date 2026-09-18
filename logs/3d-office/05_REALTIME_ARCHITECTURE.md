# 05 — Realtime Architecture

Backend: Python / FastAPI · Frontend: Next.js / TypeScript · Transport: WebSocket · Truth: PostgreSQL

---

## 1. 端到端管線

```
Worker (AgentRunner)
  │  BEGIN
  │    UPDATE agent_activity …
  │    INSERT INTO events (…)  RETURNING seq
  │    NOTIFY autora_events, '<company_id>:<seq>'     -- NOTIFY 在 COMMIT 後才投遞
  │  COMMIT
  ▼
API process (FastAPI)  — 每個 process 一個 EventListener coroutine
  │  LISTEN autora_events
  │  on notify(company_id, seq):
  │     rows = SELECT * FROM events WHERE company_id=? AND seq > cursor[company] ORDER BY seq LIMIT 500
  │     broadcast(company_id, rows); cursor[company] = max(seq)
  │  (fallback poll 每 2s：防 NOTIFY 遺失)
  ▼
WS Gateway  — ConnectionManager: company_id → set[Socket{last_acked_seq, queue}]
  │  每 socket 一個 send queue（bounded, 1000）；滿了 → 送 SNAPSHOT_REQUIRED 並清空
  ▼
Browser
  RealtimeClient ──▶ realtimeStore.applyEvent (reducer) ──▶ selectors ──▶ Dashboard / 3D / Panels
```

Ephemeral 事件（`AGENT_STEP_PROGRESS`、`AGENT_HEARTBEAT`）不走 DB：worker 透過 NOTIFY 的 payload 直接攜帶（<8KB），Gateway 直接 broadcast，`seq` 為 null。前端不對它們做排序/去重（只更新對應 agent 的 `liveProgress`）。

---

## 2. WebSocket 協定

端點：`GET /ws/companies/{company_id}?token=…&since=<last_seq|omit>`

### Server → Client

| type | 內容 | 說明 |
|---|---|---|
| `HELLO` | `{server_time, head_seq, mode: "live"|"backlog"|"snapshot_required"}` | 連線後第一則 |
| `EVENT` | `{...envelope}` | 一則持久化事件（含 seq） |
| `EVENTS` | `{items: [envelope…]}` | backlog 批次（≤200/批） |
| `EPHEMERAL` | `{event_type, agent_id, run_id, payload}` | 無 seq |
| `BACKLOG_DONE` | `{head_seq}` | backlog 送完，之後為 live |
| `SNAPSHOT_REQUIRED` | `{reason: "gap_too_large"|"queue_overflow"|"unknown_since"}` | client 需重新 hydrate |
| `HEARTBEAT` | `{server_time, head_seq}` | 每 15s |
| `ERROR` | `{code, message}` | 認證失敗等；隨後關閉 |

### Client → Server

| type | 內容 |
|---|---|
| `ACK` | `{last_seq}`（每 5s 或每 50 則）— 只用於 server 端 metrics 與 queue 修剪，不影響狀態 |
| `PING` | `{}` |

WS **沒有** command 訊息。任何操作都是 REST。

---

## 3. 初始 Hydration

```
1. GET /api/companies/{id}/realtime/snapshot
   → { last_seq, server_time, agents[{id, role, name, activity}], tasks[active], recent_events[≤100],
       kpis{...}, cycle{id, stage} }
2. realtimeStore.hydrate(snapshot)          // 覆蓋整個 domain 區塊；保留 ui 區塊
3. connect WS with since=last_seq
4. server: head_seq - since ≤ BACKLOG_MAX(5000) 且 events 未歸檔 → mode=backlog → 串流 → BACKLOG_DONE → live
           否則 → SNAPSHOT_REQUIRED → client 回到 1
```

Snapshot 的投影函式在後端 `realtime/projection.py`；**snapshot 端點與 reducer 遵守同一份規則**（例：COMPLETED 過期 → IDLE）。契約測試：對隨機事件序列，`hydrate(S0) + apply(e1..en) == snapshot(Sn)`。

---

## 4. 重連、排序、去重、過期

| 問題 | 處理 |
|---|---|
| **Reconnect** | 指數退避 1s → 2s → 4s → … → 30s，±20% jitter。每次重連帶 `since=last_seq`。連續 5 次失敗顯示 offline banner（3D 內的「LIVE」牌變灰）。 |
| **Event ordering** | 唯一依據 `seq`。Reducer 只接受 `seq > last_seq`。**（T-303 實作修訂）`seq` 是全域序號，一間公司的事件序號本來就不連續，不能以 `seq > last_seq + 1` 判斷缺口。** 改由 Gateway 保證：同一條連線上的事件依序且完整（outbox 以公司鎖讓同公司事件依 seq 提交，Gateway 以 `seq > cursor` 從 DB 讀取），唯一會斷流的情況（佇列溢出、落後太多）一律送 `SNAPSHOT_REQUIRED` 並關閉連線。前端因此不需要 pending buffer 與 REST 補洞；重連時以 `since=last_seq` 由 backlog 補齊。 |
| **Duplicate events** | `seq ≤ last_seq` 直接丟棄（O(1)）。backlog 與 live 交界處可能重複，此規則涵蓋。 |
| **Stale events** | 「stale」只影響**動畫**，不影響狀態：director 對 `occurred_at < now - 10s` 不產生 cue（狀態照套用）。前端用 `HELLO.server_time` 校正時鐘偏移。 |
| **Connection failure** | WS 關閉 → store.connection = "reconnecting"；UI 保留最後狀態並標示「資料可能過期 Ns」。不清空畫面。 |
| **Heartbeat 遺失** | 2 個 HEARTBEAT 週期（30s）沒收到 → 主動關閉重連。 |
| **Server 端 queue 溢出** | 該 socket 收到 SNAPSHOT_REQUIRED；不影響其他 socket。 |
| **Tab 背景長時間** | 回前景時若 `now - last_event_at > 60s` → 直接 rehydrate（比補洞便宜）。 |
| **Reconnect 後同步 Company State** | 同 §3：小 gap 用 backlog，大 gap 用 snapshot。兩者結果相同（契約測試保證）。 |

---

## 5. 前端 State 邊界

```
┌────────────────────────────────────────────────────────────────────────┐
│ Server State (TanStack Query)   ── 拉取式、可快取、可失效                │
│   agent detail, run detail, trace, article, story, kpis(history),      │
│   approvals list, cycle detail                                          │
│   失效策略：收到相關 event → queryClient.invalidateQueries(key)          │
├────────────────────────────────────────────────────────────────────────┤
│ Realtime Store (Zustand, domain projection)  ── 推播式、單一 reducer     │
│   connection {status, last_seq, server_offset}                          │
│   agents: Record<id, {id, role, name, activity, liveProgress?}>          │
│   tasks:  Record<id, TaskView>   (active + 最近 10 分鐘完成)              │
│   cycle:  {id, stage, deadline}                                          │
│   kpis:   {cash, revenue_today, expenses_today, published_today, goal}   │
│   recentEvents: RingBuffer<Envelope>(500)                                │
│   ★ 不含任何 visual 欄位；不含 selectedAgent                              │
├────────────────────────────────────────────────────────────────────────┤
│ UI State (Zustand, ephemeral)                                            │
│   selectedAgentId, panelTab, cameraMode, filters, timelinePaused         │
├────────────────────────────────────────────────────────────────────────┤
│ 3D Visual State (refs / Three objects, 不在 React state)                 │
│   mixer, current pose, position tween, cueQueue, highlight               │
└────────────────────────────────────────────────────────────────────────┘

例：
  Server:   Researcher 的 current task 全文、steps、output       → GET /api/runs/{id}
  Realtime: AGENT_WORKING{tool:web_search}                       → agents[r].activity
  UI:       selectedAgentId = researcher                          → 開右側 panel
  3D:       pose = sit_type, bubble = "Searching…"                → mixer.crossFade
```

規則：
- Realtime store 的 `applyEvent` 是唯一 mutation 入口；它是純 reducer（可單元測試），從 `packages/event-schema` 取型別。
- Server state 不複製進 realtime store；panel 顯示「即時 + 明細」時兩者並列渲染。
- Query invalidation 由一個 `eventToQueryKeys(event)` 對照表決定（例：`TASK_SUCCEEDED` → `["run", run_id]`, `["task", task_id]`）。
- 3D 層對 store 的訂閱走 `subscribe(selector, listener)`，不觸發 React render。

---

## 6. 安全與範圍

- WS 認證：query token（MVP 單一 operator bearer）；Phase 6 改為 session cookie + per-company 授權。
- 一個 socket 只綁一個 company；跨公司事件不會送達（Gateway 按 company 路由）。
- 事件 payload 不含 prompt 全文、API key、原始 HTML；這些只在 REST（admin）取用。
- 公開站（讀者）不連 WS。

---

## 7. 容量與退化

- MVP 負載：每日 < 5k 持久化事件、< 10 個並發 socket。單 API process 綽綽有餘。
- Ephemeral 進度事件限速：每 run 每秒 ≤ 1。
- `events` 表索引：`(company_id, seq)`、`(run_id, seq)`、`(task_id)`、`(correlation_id, seq)`。
- 若未來 API 水平擴展：每個 process 自己 LISTEN，Postgres fan-out；> 3 個 process 或 > 100 並發 socket 時再評估 Redis pub/sub。

---

## 8. 測試

- 後端：`test_gateway_backlog_then_live`（since 小 gap）、`test_gateway_snapshot_required`（大 gap）、`test_notify_fallback_poll`（模擬 NOTIFY 遺失）、`test_queue_overflow`。
- 契約：`test_projection_contract`（隨機事件序列，snapshot ⊕ events == snapshot）。
- 前端：reducer 單元測試（ordering / dup / gap / hydrate）、`RealtimeClient` 用 mock WS 測 reconnect 與 gap fill。
- E2E：Playwright 開 `/office`，後端跑 simulation，斷言 6 個 avatar 的 badge 依序變化，且 `kill` WS 後 30s 內恢復且無重複事件。
