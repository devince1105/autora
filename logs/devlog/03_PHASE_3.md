# 開發紀錄 03 — 階段 3 即時連線與 Dashboard

- 期間：2026-09-18 起（進行中）
- 目標：瀏覽器可以即時看到執行環境的狀態；斷線重連後資料正確；Dashboard 顯示真實數字。
- 驗收條件（`logs/3d-office/09_DEVELOPMENT_ROADMAP.md` 階段 3）：兩個瀏覽器分頁開 Dashboard，啟動 EchoWorkflow，兩者的代理卡片同步變化；關閉 API 20 秒再啟動，分頁自動恢復，`last_seq` 與伺服器一致，Trace 無缺漏。
- 設計依據：`3d-office/05_REALTIME_ARCHITECTURE.md`（協定、hydration、排序、去重、前端狀態邊界）、`3d-office/07_DATABASE_MODEL.md` §4（即時查詢）、`3d-office/02_AGENT_STATE_MODEL.md`（活動狀態與投影規則）。
- 使用模型：Claude Opus 5（未另行指定前）。

## 開發計畫

任務來自 `3d-office/10_TASK_BREAKDOWN.md`。後端 T-301 ~ T-304 與前端 T-305 ~ T-308 可以平行，但前端的 reducer 必須與後端的投影一致（T-302 的契約），所以先定下投影的形狀。

| 任務 | 內容 | 端 | 依賴 | 狀態 |
|---|---|---|---|---|
| T-301 | 即時投影與 snapshot API | 後端 | 階段 1、2 | ✅ |
| T-302 | 投影契約測試（Python 參考 reducer） | 後端 | T-301 | ⏳ |
| T-303 | WebSocket 閘道（LISTEN + 輪詢備援、backlog、SNAPSHOT_REQUIRED、心跳、有界佇列） | 後端 | T-301 | ⏳ |
| T-304 | 代理執行器的即時進度（AGENT_STEP_PROGRESS，不落表） | 後端 | T-211、T-303 | ⏳ |
| T-305 | 前端即時 store 與 reducer（跨語言契約） | 前端 | T-108 | ⏳ |
| T-306 | RealtimeClient（重連、補洞、心跳逾時、背景分頁） | 前端 | T-305、T-303 | ⏳ |
| T-307 | UI store | 前端 | — | ⏳ |
| T-308 | 型別化 API client 與 Query hooks | 前端 | T-110、T-210 | ⏳ |
| T-309 | Dashboard 頁 | 前端 | T-305、T-308 | ⏳ |
| T-310 | 代理卡片與代理詳情面板 | 前端 | T-305、T-307、T-308 | ⏳ |
| T-311 | Trace Viewer | 前端 | T-308 | ⏳ |
| T-312 | 事件時間軸 | 前端 | T-305 | ⏳ |
| T-313 | 審批收件匣 | 前端 | T-206、T-308 | ⏳ |
| T-314 | 簡易 KPI 報表 | 後端 | T-103、T-201 | ⏳ |
| T-315 | 階段 3 端到端測試（Playwright：兩分頁同步、API 重啟恢復） | 前後端 | T-306、T-309、T-310 | ⏳ |

建議順序：T-301 → T-302 → T-303 → T-314 → T-304（後端完成即時資料來源），接著 T-305 → T-306 → T-307 → T-308 → T-309 ~ T-313，最後 T-315。

### 進入條件與帶過來的待辦

- **進入條件尚缺一項**：路線圖要求「真實 Anthropic 呼叫通過一次」才進入階段 3。目前沒有 API 金鑰，這項尚未完成；依使用者指示先開始階段 3，此項保留為待辦，設定方式見 `logs/RUNBOOK.md` 第六節。
- 階段 2 留下、會在本階段用到的：
  - `GET /api/tasks/{id}`、`GET /api/runs/{id}` 尚未實作（T-308、T-310 需要時補上）；
  - API 尚未設定 CORS，前端的 API 位址設定也還沒有（T-306 / T-308 時處理）；
  - 活動狀態的 `detail` 含有 `task_name` 與 `links`，這兩項不在事件本身，reducer 需要從任務資料補上（T-302 / T-305 處理）。

---

## T-301 · 即時投影與 snapshot API

### 做了什麼
- `realtime/projection.py`：`load_snapshot(session, company_id)` 回傳瀏覽器 hydrate 所需的公司狀態：

| 欄位 | 內容 |
|---|---|
| `last_seq` | 這份狀態已反映的最新事件序號；前端以 `since=last_seq` 連線，再套用之後的事件 |
| `server_time` | 伺服器時間，前端用來校正時鐘 |
| `agents` | 每位未退休的代理：角色、名字、頭像鍵，以及活動狀態（`state` 為**有效狀態**、`stored_state` 為資料列原值、`detail`、`since`、`run_id`、`task_id`、`last_event_seq`） |
| `tasks` | 未完成的任務，加上最後一個 TASK_* 事件在 10 分鐘內的已完成任務；含依賴、所屬工作流程、嘗試次數、最近一次執行與代理、`since` 與 `last_event_seq` |
| `recent_events` | 最新 100 個持久化事件，舊到新 |
| `kpis`、`cycle` | 先固定為 `null`：KPI 由 T-314 提供，週期在階段 6 才有資料表 |

- `GET /api/companies/{id}/realtime/snapshot`（需要權杖；公司不存在 404）。

### 設計決策
- **一致的讀取**：所有查詢在同一個 REPEATABLE READ 交易中執行（`begin_consistent_read`）。因為同一公司的事件依序號提交（T-106 的公司鎖），這個交易看得到的事件恰好是 `seq <= last_seq` 的那些，`last_seq` 才能準確描述回傳的資料列。有測試：快照交易開著時，另一個連線提交了狀態變更，同一交易再讀一次仍是原本的樣子。
- **只用事件可以重建的欄位**：任務的 `since` 與 `last_event_seq` 取自該任務最後一個 TASK_* 事件，而不是 `tasks.updated_at`（資料庫時間與事件時間不同），這樣 T-302 的 reducer 只看事件也能算出相同的值。
- **投影規則與時鐘有關的兩條**：COMPLETED 超過 `display_until` 顯示為 IDLE（`3d-office/02` §5）；已完成的任務保留 10 分鐘。兩條都以 `now` 參數計算，reducer 以相同規則在前端套用。
- **帶到 T-302 的問題**：活動狀態的 `detail` 含 `task_name`（任務顯示名稱）與 `links`，這兩項不在事件內容中；reducer 需要從任務資料補上 `task_name`。
- 未列入的欄位：`tasks.progress`（目前沒有任何程式寫入，T-304 決定）、`agents.status`（沒有事件會改變它，暫停以活動狀態 PAUSED 表示）。

### 驗證
- `tests/realtime/test_projection.py`（6 個）與 `tests/api/test_realtime_api.py`（1 個）：
  - 啟動 EchoWorkflow 前後的快照內容（代理狀態、任務狀態與依賴、`last_seq` 等於最大序號、交接）；
  - 時鐘規則：5 秒後仍是 COMPLETED、11 分鐘後有效狀態為 IDLE 且已完成任務消失，`last_seq` 不變；
  - 最新 100 個事件、空公司、一致讀取、API 的 200 / 404 / 401。
- **效能**（`3d-office/07` §4 目標：本機 < 50 ms）：12 位代理、300 個任務、20,000 個事件，快照中位數約 **21 ms**。CI 的共用機器較慢，測試在 CI 上以 150 ms 為上限。
- 以真實伺服器對開發資料庫呼叫：第一次 164 ms（建立連線），之後約 9 ms；示範公司的已完成狀態正確顯示為 IDLE；之後的請求不受隔離等級影響。
- Python 測試共 600 個通過。
