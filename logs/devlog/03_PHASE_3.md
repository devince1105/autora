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
| T-302 | 投影契約測試（Python 參考 reducer） | 後端 | T-301 | ✅ |
| T-303 | WebSocket 閘道（LISTEN + 輪詢備援、backlog、SNAPSHOT_REQUIRED、心跳、有界佇列） | 後端 | T-301 | ✅ |
| T-304 | 代理執行器的即時進度（AGENT_STEP_PROGRESS，不落表） | 後端 | T-211、T-303 | ✅ |
| T-305 | 前端即時 store 與 reducer（跨語言契約） | 前端 | T-108 | ✅ |
| T-306 | RealtimeClient（重連、補洞、心跳逾時、背景分頁） | 前端 | T-305、T-303 | ✅ |
| T-307 | UI store | 前端 | — | ✅ |
| T-308 | 型別化 API client 與 Query hooks | 前端 | T-110、T-210 | ✅ |
| T-309 | Dashboard 頁 | 前端 | T-305、T-308 | ✅ |
| T-310 | 代理卡片與代理詳情面板 | 前端 | T-305、T-307、T-308 | ✅ |
| T-311 | Trace Viewer | 前端 | T-308 | ⏳ |
| T-312 | 事件時間軸 | 前端 | T-305 | ⏳ |
| T-313 | 審批收件匣 | 前端 | T-206、T-308 | ⏳ |
| T-314 | 簡易 KPI 報表 | 後端 | T-103、T-201 | ✅（與 T-309 一起完成） |
| T-315 | 階段 3 端到端測試（Playwright：兩分頁同步、API 重啟恢復） | 前後端 | T-306、T-309、T-310 | ⏳ |

建議順序：T-301 → T-302 → T-303 → T-314 → T-304（後端完成即時資料來源），接著 T-305 → T-306 → T-307 → T-308 → T-309 ~ T-313，最後 T-315。

### 進入條件與帶過來的待辦

- **進入條件**：路線圖要求真實模型呼叫通過一次才進入階段 3（原為「真實 Anthropic 呼叫」，D-005 改為不限供應者）。✅ 2026-09-18 以 NVIDIA `z-ai/glm-5.3-flash` 跑 `tests/e2e/test_echo_live.py` 通過（見下方 D-005 一節的實測）。
- 階段 2 留下、會在本階段用到的：
  - `GET /api/tasks/{id}`、`GET /api/runs/{id}` 尚未實作（✅ T-308 已補上）；
  - API 尚未設定 CORS，前端的 API 位址設定也還沒有（✅ T-306 已處理）；
  - 活動狀態的 `detail` 含有 `task_name` 與 `links`，這兩項不在事件本身，reducer 需要從任務資料補上（T-302 已處理 `task_name`；`links` 尚無領域提供）。
  - **事件序號是全域的，不是每間公司各自連號**（T-302 發現）：`05` §4 以「`seq > last_seq + 1` 即有缺口」判斷的做法不能照用。✅ T-303 以「閘道保證不斷流」解決，`05` §4 已修訂。

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

---

## 3D 辦公室的人物風格（D-008，2026-09-18）

使用者決定：先完成階段 3 再做 3D；人物用免費授權（CC0）的低多邊形素材，風格參考《動物森友會》，辦公室場景另參考《Good Job!》。記為 D-008，並更新 `3d-office/04` §10 與 `12_RISKS` Q4。**只參考風格**（Q 版比例、圓潤造型、柔和粉彩、明亮俏皮的辦公室），不使用任天堂的任何角色、模型、貼圖或商標，也不做可辨識的仿製；素材來源與授權記入 `LICENSES.md`（階段 4，T-404）。

---

## T-310 · 代理卡片與代理詳細面板

### 做了什麼
- Dashboard 下方加上**代理卡片**：名字、角色、狀態（等待時標出原因：等待審批 / 上游 / 預算）、目前任務、目前工具、已進行時間，以及 Runtime 回報的進度。點卡片 → UI store 選取該代理 → 右側開啟**詳細面板**（Esc 或 ✕ 關閉）。
- 面板分四個分頁（`3d-office/01` §3.7 的兩層資料）：
  - **即時**（即時 store）：目前任務、工具、已進行、進度（只在有回報時）、權杖、產出計數、下一步、本次執行摘要、連結；
  - **步驟**（REST 軌跡）：每一步的種類、摘要、費用；
  - **工具**（由軌跡的 TOOL_* 事件統計）：每個工具的呼叫、成功、失敗、平均耗時；
  - **輸出**（REST 執行明細）：輸出、評估、錯誤的 JSON。依輸出格式顯示的專屬畫面（例如研究筆記的來源列表）留到階段 5。
- `features/agent-panel/model.ts`（純函式）：
  - **產出計數**來自真實的 `TOOL_COMPLETED.produced`（例如 evidence → 「來源」），不是估計；
  - **下一步**：執行完成後用 Runtime 記錄的交接；進行中則是依賴目前任務的下游任務（工作流程範本決定），都不是 LLM 說的；
  - **進度只在 Runtime 回報時顯示**（工具回報或活動狀態的 progress，且屬於同一次執行），否則只顯示已進行時間；不以「步數 / 上限」估算百分比。
- 明細的查詢以 `run_id` 為鍵，由 T-308 的事件對應表自動失效重取。

### 驗證
- `agent-panel.test.tsx`：10 個通過，包含驗收條件——**「來源 37」由兩次工具呼叫共 30 + 7 個 evidence 產出計得**；沒有回報時不顯示進度條；舊執行的即時進度不會顯示在新執行上；下一步的兩種來源；工具統計（含失敗與平均耗時）；卡片點擊、分頁切換、關閉、錯誤訊息。
- web 共 72 個測試通過；`typecheck`、`lint`、`next build` 以結束碼確認通過。
- 瀏覽器窗格：開發伺服器重新編譯無錯誤；從頁面開 WebSocket 連到 API（故意用錯的權杖）得到 ERROR unauthorized 與關閉碼 4401，確認瀏覽器端的即時連線路徑可用。卡片與面板在輸入權杖後才看得到，這一步我沒有做（不在欄位中輸入權杖），畫面內容由元件測試涵蓋。

---

## 前端改用 Tailwind CSS（D-007，2026-09-18）

- 依使用者要求，前端樣式由 CSS Modules 改為 **Tailwind CSS v4**（`@tailwindcss/postcss`）。`globals.css` 以 `@theme` 定義語意顏色（`canvas`、`surface`、`line`、`ink`、`muted`、`accent`、`ok`、`warn`、`danger`…），深色模式只覆寫同一組變數。Dashboard 與權杖畫面改寫為 Tailwind class，刪除兩個 `.module.css`。
- 權杖畫面的說明改寫：使用者問「首頁的 API_BEARER_TOKEN 要輸入什麼、NVIDIA 金鑰不是已經在 .env 了嗎」，代表原本的說明不夠清楚。現在寫明這是 **Autora 自己 API 的密碼**（`.env` 的 `API_BEARER_TOKEN`，開發預設 `change-me`），**不是 NVIDIA 或 Anthropic 的金鑰**——模型金鑰只在後端，瀏覽器永遠看不到。
- 驗證：web 62 個測試、`typecheck`、`lint`、`next build` 以結束碼確認通過；產出的 CSS 含 Tailwind 的 utility（含自訂 grid）；在瀏覽器窗格重新啟動開發伺服器後，權杖畫面以深色模式正確顯示。

---

## T-314 · 簡易 KPI 報表（與 T-309 一起完成）

計畫的順序原本把 T-314 排在 T-303 之後，我漏掉了；做 T-309 時才發現 Dashboard 的金額與今日目標沒有資料來源，「數字來自真實資料、不寫死」的驗收條件無法達成，所以先補上。

### 做了什麼
- `company/reporting_min.py`：`load_kpis()` 以 SQL 直接計算（不經 LLM），「今天」以 UTC 計：
  - **現金** = 注資 + 營收 − 支出 − 撤資 − 所有模型呼叫的費用（公司內部轉帳不計）；
  - **今日支出** = 今天的支出交易 + 今天的模型呼叫費用（另列 `model_cost_today`）；**今日營收**；
  - **今日發布**：文章在階段 5 才有，先回傳 null（畫面顯示「—」），不回傳假的 0；
  - **今日目標**：進行中的 cycle 目標中期限最近的一個。
  - **模型費用只算一次**：`model_calls` 是模型費用的唯一來源（T-207）。帳本在階段 6 才會把模型費用記成 `model_cost` 交易；屆時這裡會略過該類別，不會重複計算。
- `GET /api/companies/{id}/kpis`（階段 3 路線圖列出的 API）。

### 與設計不同
- `05` §5 把 `kpis` 放在即時 store 裡。但模型費用不是事件，只靠事件的 reducer 無法保持 KPI 正確，也會破壞 T-302 的契約（快照的 `kpis` 若有值，reducer 重建不出來）。因此 **KPI 改為伺服器狀態（TanStack Query）**：執行結束、帳務事件、目標事件時失效重取，另每分鐘重取一次；快照的 `kpis` 維持 null。

### 驗證
`tests/company/test_reporting_min.py`：4 個通過（每筆費用只算一次、跨日與未來的交易不算、公司隔離、目標的挑選、API 與 404）。

---

## T-309 · Dashboard 頁

### 做了什麼
- 路由 `/dashboard`（首頁轉址到這裡），`?company=<ID>` 指定公司，否則顯示第一間。
- `features/auth/TokenGate.tsx`：第一次進入輸入操作者權杖，存在 localStorage；API 回 401 時自動回到輸入畫面，切換權杖時清掉用舊權杖取得的快取。
- `features/company/useCompanyStream.ts`：頁面開著時啟動 RealtimeClient、接上分頁可見性與查詢失效，離開時全部停止；切換公司先清空即時 store。
- `features/dashboard/model.ts`：**純函式**決定要顯示的數字——代理與任務來自即時 store（依伺服器校正後的時間計算有效狀態），金額與目標來自 KPI 查詢；KPI 尚未載入時顯示「—」而不是 0。
- `features/dashboard/DashboardView.tsx`：現金、今日營收、今日支出（含模型費用）、工作中代理（x / 總數，並列出等待、暫停、失敗）、進行中任務（執行中、待審批、預算受阻、剛完成）、今日發布、今日目標；右上角連線狀態，非即時時顯示「資料可能已過期 N 秒」，離線時顯示提示但**保留最後的畫面**（`05` §4）。
- 版面：純 CSS 與 CSS Modules（Next 內建，不另加 UI 框架），淺色 / 深色依系統設定，窄螢幕自動換行。介面文字為繁體中文。
- 相依：`@testing-library/react`、`@testing-library/dom`、`jsdom`（元件測試）。
- `.gitignore` 加入 `.claude/`（本機的開發伺服器設定）。

### 驗證
- `dashboard.test.tsx`：8 個通過——模型只用真實資料（以契約檔的真實歷史計算代理與任務數）、KPI 未載入時為空、過期秒數、台灣格式的金額；畫面顯示每個卡片、離線時標示過期但保留數字、KPI 前顯示「—」；權杖輸入與登出。
- web 共 62 個測試通過；`typecheck`、`lint`、`next build` 以結束碼確認通過。
- **瀏覽器實測（部分）**：以瀏覽器窗格開啟真實的 API（8000）、工作程序與 Next 開發伺服器（3000）。首頁正確轉到 `/dashboard` 並顯示權杖輸入畫面；瀏覽器主控台與開發伺服器都沒有錯誤；從 `http://localhost:3000` 發出的 CORS 預檢與實際 KPI 請求都通過。
- **沒有做的**：我沒有把權杖輸入瀏覽器的表單（輸入密碼、金鑰、權杖到任何欄位是我不做的操作），所以輸入權杖之後的畫面沒有在真實瀏覽器中看過；元件測試已涵蓋畫面內容，T-306 已以真實後端驗證即時連線。

---

## T-307 · UI store

- `frontend/web/src/stores/ui.ts`：操作者正在看什麼——選取的代理、面板分頁（live / steps / tools / output）、鏡頭模式（overview / follow / free）、時間軸暫停與篩選。只在瀏覽器本地，不送到伺服器、不由事件推導，與即時 store 分開，所以大量事件不會因為 UI 狀態而讓面板重繪，反之亦然。
- 規則：選另一位代理時面板回到 live 分頁（同一位代理則保留分頁）；只有選了代理才能「跟隨」，取消選取時鏡頭回到 overview。
- 測試 4 個（含「不持有任何領域資料」）。

---

## T-308 · 型別化 API client 與 Query hooks

### 做了什麼
- **型別來自 API 本身**：`backend/scripts/gen_openapi.py` 輸出 FastAPI 的 OpenAPI 文件到 `frontend/web/src/api/openapi.json`，`pnpm -F web gen-api`（openapi-typescript）產生 `schema.gen.ts`。兩者都提交，回應模型的變動會在審查時以型別變動出現。`make gen-api` 一次做完；CI 的 python 工作檢查 OpenAPI 文件是否最新（`make gen-api-check`），web 工作檢查型別是否與文件一致（`gen-api:check`）；兩個檢查都確認過在過期時會失敗。
- `src/api/client.ts`：openapi-fetch 的型別化 client（路徑、參數、回應都有型別），自動加上權杖；失敗時丟出帶有 RFC 7807 內容的 `ApiError`。
- `src/api/auth.ts`：操作者權杖存在瀏覽器 localStorage，**不編進前端程式**（`NEXT_PUBLIC_*` 會被所有下載頁面的人看到）；階段 6 改為 session cookie。
- `src/api/queries.ts`：TanStack Query 的 query options（公司、代理、執行、軌跡、任務、審批）與兩個指令（決定審批、啟動工作流程）；`createQueryClient()` 的預設為「資料保留到事件使其失效」。
- `src/api/invalidation.ts`：`eventToQueryKeys(event)`——一張表決定哪個事件讓哪些查詢過期（執行的任何事件 → 該執行的軌跡；執行狀態變化 → 執行明細；TASK_* → 任務；APPROVAL_* → 審批；代理建立 / 暫停 / 恢復 → 代理列表）。`connectQueryInvalidation` 監聽即時 store，**每次 store 更新只讓每個查詢失效一次**（補 200 個事件的 backlog 也只讓一個軌跡重新取一次），hydrate 不算新事件。
- **後端**：補上 `GET /api/runs/{id}`（執行明細）與 `GET /api/tasks/{id}`（任務與每次嘗試的執行），這是階段 2 路線圖列出、一直沒有對應任務的兩個 API；`GET /api/runs/{id}/trace` 改為回傳型別化的 `Trace`（原本是任意 dict，產生的型別會是 unknown）。

### 型別生成立刻抓到的問題
- 啟動工作流程的 `params` 在 API 有預設值，但產生的型別把它標為必填（openapi-typescript 把有預設值的欄位視為必定存在）。`startWorkflow` 改為一律送出 `params`（沒有時送空物件），與 API 的行為一致。

### 驗證
- web 共 54 個測試通過（新增 UI store 4 個、API 13 個）：型別化 client 的網址 / 權杖 / 回應、problem+json 轉成 `ApiError`、指令的查詢與主體參數、沒有權杖時不送標頭；事件對查詢的對應表（以契約檔中的真實事件）；失效串接只處理新事件、每個查詢只失效一次、重播不觸發。
- 以結束碼確認：`typecheck`、`lint`、`next build`、`gen-api:check` 皆通過（`typecheck` 第一次失敗在測試檔的型別，已修正——這次有檢查結束碼才抓到）。
- 後端：API 測試 29 個通過（新增執行與任務明細）；Python 測試全部通過。

---

## T-306 · RealtimeClient

### 做了什麼
- `frontend/web/src/realtime/client.ts`：`RealtimeClient` 讓即時 store 與一間公司保持同步：
  1. 取 snapshot（`GET .../realtime/snapshot`）→ `store.hydrate`；
  2. 連線 `WS /ws/companies/{id}?token&since=<store 的 lastSeq>`；
  3. EVENTS（補的）與 EVENT（即時）→ `store.applyEvents`；EPHEMERAL → 即時進度；HELLO / HEARTBEAT → 校正伺服器時鐘偏移。
- 異常處理：

| 狀況 | 處理 |
|---|---|
| 連線中斷 | 指數退避重連（1、2、4 … 30 秒，±20% 抖動），從 store 的 `lastSeq` 接續，由伺服器補送漏掉的事件；成功後退避歸零。連續 5 次失敗狀態為 `offline`（給畫面顯示），仍持續重試；畫面保留最後的狀態，不會清空 |
| SNAPSHOT_REQUIRED（落後太多、佇列溢出、since 不明） | 重新取 snapshot，立刻重連 |
| 沒有任何訊息超過 30 秒（2 個心跳週期） | 關閉並重連 |
| 分頁回到前景時已超過 60 秒沒有訊息 | 重新取 snapshot（比補一長串便宜，而且背景時連線可能已默默斷掉） |
| ERROR unauthorized / not_found、關閉碼 4401 / 4404、snapshot 回 401 / 404 | 停止，不再重試（重試不會成功，也避免狂打伺服器） |

  - 每 50 個事件或每 5 秒送一次 ACK（伺服器只記錄）。
  - WebSocket、fetch、亂數都可注入，測試用假連線與假時鐘；`attachToDocument` 把分頁可見性接到 client。
- `src/config.ts`：`NEXT_PUBLIC_API_URL`（預設 `http://localhost:8000`）與 ws 網址轉換。
- `stores/realtime.ts`：連線狀態加上 `unauthorized`、`not_found`。
- API：加上 CORS（`CORS_ORIGINS`，預設允許 `http://localhost:3000`），有測試確認其他來源不被允許。WebSocket 不受 CORS 限制，以查詢字串的權杖驗證。
- **沒有 REST 補洞**：依 T-303 的修訂，同一連線由閘道保證依序且完整，任務拆解中的「gap-fill via REST」因此不需要。

### 真實環境測試找到的問題（我造成的）
- 第一版假設 WebSocket 出錯後「一定會接著觸發 close」。瀏覽器依規格確實如此，但 **Node 22 內建的 WebSocket（undici）在伺服器拒絕連線時只觸發 error、不觸發 close，而且永遠停在 CONNECTING**。結果 API 重啟期間，重連嘗試卡住，狀態一直停在 `reconnecting`；心跳逾時也救不了，因為它同樣是呼叫 `close()` 再等 close 事件。
  - 修正：連線尚未開啟時的 error 直接視為連線失敗；心跳逾時與背景回來重新 hydrate 改為「關閉並立刻自行處理」，不依賴 close 事件；處理可重複呼叫，之後若真的來了 close 會被忽略。
  - 回歸測試：「開啟前的 error 視為失敗」、「`close()` 完全沒有反應的連線仍會被心跳逾時釋放」。
  - 這個問題是用假連線的單元測試抓不到的（假連線照規格行為），靠下面的真實環境測試才發現。

### 驗證
- `client.test.ts`：15 個通過（假連線、假時鐘）——初次 hydrate 與連線網址、backlog 與重疊去重、即時進度、退避時間點與歸零、抖動範圍與上限、5 次後 `offline` 並持續重試、SNAPSHOT_REQUIRED 重新 hydrate 並以新的 since 重連、心跳逾時、分頁回前景（30 秒不重取、61 秒重取）、unauthorized 停止、snapshot 401 / 404 停止、snapshot 失敗退避重試、ACK、上述兩個回歸測試。
- **真實環境測試**（暫時的測試檔，用完刪除；對開發資料庫、真實 API 與工作程序、Node 22 內建 WebSocket）：
  1. client 連上、跟著第一個 EchoWorkflow 即時更新；
  2. 停掉 API 約 20 秒，期間直接在資料庫啟動第二個工作流程並由工作程序完成；
  3. 重啟 API。狀態依序為 connecting → live → reconnecting → **offline**（5 次失敗）→ connecting → live；client 從 324 補到 362（整個停機期間的工作流程），**與伺服器重新取得的 snapshot 完全相同**。
- web 共 38 個測試通過；`typecheck`、`lint`、`next build` 通過；API 測試 28 個通過（含 CORS）。
- （我造成的）第一次推送後 CI 的 web 工作失敗：`client.ts` 有一個未使用的變數（ESLint `no-unused-vars`）。本機其實也會失敗，但我用 `pnpm -r --silent lint | tail` 檢查，`--silent` 隱藏了錯誤、管線又吃掉了結束碼，所以誤以為通過。與階段 2 記下的教訓相同：**檢查要看指令本身的結束碼，不要只看經過管線的輸出**。修正後以結束碼確認 lint、typecheck、測試皆通過。

---

## T-305 · 前端即時 store 與 reducer

### 做了什麼
- `frontend/web/src/realtime/snapshot.ts`：snapshot 的 zod 結構（對應後端 `projection.py`），在邊界驗證，格式不對直接拒絕，不讓錯誤資料進入 store。
- `frontend/web/src/realtime/reducer.ts`：純函式的 reducer，是後端 `reducer.py` 的 TypeScript 對應版本，規則逐條相同：
  - `hydrate(snapshot)`、`applyEvent(state, event)`（被忽略時回傳同一個物件，方便 React 判斷是否需要重繪）、`applyEphemeral(state, message)`、`view(state, now)`；
  - 重複或較舊的事件、其他公司的事件、沒有 seq 的事件一律忽略；非最終的 AGENT_RUN_FAILED / ABORTED 是軌跡資料，不改變狀態；
  - `effectiveState`（COMPLETED 超過 `display_until` 顯示為 IDLE）與 `isTaskVisible`（已完成任務保留 10 分鐘）；
  - **不做缺口偵測**：依 T-303 的修訂，由閘道保證同一連線依序且完整，所以沒有 `05` 原本寫的等待緩衝。
  - 即時進度 `liveProgress` 掛在代理上，不屬於投影；代理的下一個活動屬於不同的執行（或沒有執行）時自動清除。
- `frontend/web/src/stores/realtime.ts`：Zustand store。唯一的修改入口是 `hydrate` / `applyEvent(s)` / `applyEphemeral`（內部呼叫 reducer）；事件先以 `parseEvent`（zod）驗證，不認得的類型或較新的版本記錄後丟棄並計數，不會讓畫面當掉。`applyEvents` 一次套用一批只觸發一次更新。提供 `useRealtime(selector)` 給 React，3D 辦公室則用 `realtimeStore.subscribe`（不觸發 React 重繪，`05` §5）。`serverNow()` 以伺服器時鐘偏移校正「現在」。
- 相依套件：web 加入 `zustand`、`zod` 與 workspace 的 `@autora/event-schema`；vitest 設定 `@/` 路徑別名。

### 驗證
- **跨語言契約**：以 T-302 產生的檔案（真實隨機歷史：第一個快照、148 個事件、24 種類型、最後一個快照），TypeScript 的 `hydrate + applyEvent` 在最後快照的伺服器時間產生的投影，與 Python 產生的快照**完全相同**。
- **變異測試**：故意改壞三條規則，確認契約測試會抓到：
  - 「狀態不變時保留 since」改掉 → 失敗 ✅；
  - `task_name` 改成不填 → 失敗 ✅；
  - 「非最終的 TASK_FAILED 不改狀態」改掉 → 仍通過。這是**等價變異**：非最終失敗之後在同一交易一定接著 TASK_READY，所以提交後的狀態相同，規則只影響交易中途，不是測試的漏洞。
- `reducer.test.ts`（9 個）與 `stores/realtime.test.ts`（5 個）：重複與其他公司的事件、非最終 / 最終中止、時鐘規則、即時進度的附加與清除、snapshot 驗證、批次套用只通知一次、無效事件計數、未 hydrate 前不套用、時鐘偏移。
- web 共 23 個測試通過；`typecheck`、`lint`、`next build` 皆通過。

---

## T-304 · 代理執行器的即時進度

### 做了什麼
- `runtime/progress.py`：`ProgressPublisher`。以 `publish_ephemeral`（T-303）送出 AGENT_STEP_PROGRESS，只走 NOTIFY、不寫入 `events`。
  - **速率**：每次執行最多每 `min_interval`（預設 1 秒）一則（`05` §7）。間隔內的回報合併，只在間隔到時送出最新的一則，所以執行進行中最新的進度不會遺失；執行結束時丟棄尚未送出的（之後的持久化事件已經取代它）。
  - **盡力而為**：送出失敗只記錄，不影響執行。
- 代理執行器在每次模型呼叫後回報（本次執行累計的權杖數），在每次工具呼叫後回報（加上工具自己的進度）。
- `ToolResult` 新增選填的 `progress`（例如「來源 12/40」），工具可以用它回報工作進度；echo 的 `echo_note` 回報「notes 1/1」作為示範。
- 工作程序的組裝（`build_worker`）加入 `ProgressPublisher`。
- `RUNBOOK.md` 的 API 表補上 T-301 的 snapshot 與 T-303 的 WebSocket 端點。

### 限制
- **模型閘道還沒有串流**，所以一次模型呼叫進行中沒有逐字進度；進度出現在步驟之間（模型呼叫結束、工具呼叫結束）。使用真實模型時一次呼叫可能要 10 ~ 30 秒，這段期間畫面上是「思考中」而沒有數字變化。要逐字進度需要在閘道與供應者加上串流，屬於之後的工作。

### 驗證
- `tests/realtime/test_ephemeral.py`：5 個通過，與閘道測試一起連續 3 次皆通過。
  - 速率：連續 10 則回報，立刻送第一則、間隔後只送最新一則，之後不再送；不同執行分開計算；執行結束時丟棄尚未送出的。
  - 經由真實的代理執行器與閘道：3 次執行各 3 則（模型呼叫 150 權杖、工具呼叫附「notes 1/1」、模型呼叫 300 權杖），`events` 表中沒有任何 AGENT_STEP_PROGRESS。
  - 預設速率下，同一執行的兩則進度抵達時間相隔至少 1 秒（以抵達時間計算，而不是事件建立時間：合併後送出的那一則建立得較早）。
  - 送出失敗時，工作流程仍然成功。
- 在開發資料庫手動測試（API、工作程序、WebSocket 客戶端各為獨立程序）：工作程序送出的進度經由資料庫通知、API 轉送到客戶端。模擬的執行不到 1 秒就結束，每次執行只送出第一則，符合速率設計。

---

## T-303 · WebSocket 閘道

### 做了什麼
- `realtime/gateway.py`：
  - `EventHub`：每個 API 程序一個。LISTEN `autora_events`（收到 `公司:序號` 就讀取該公司 `seq > 游標` 的事件，每次最多 500 筆），另每 2 秒輪詢有連線的公司（NOTIFY 遺失或監聽連線中斷時的備援；監聽連線斷掉會自動重連）；LISTEN `autora_ephemeral` 轉送即時事件；每 15 秒送 HEARTBEAT。
  - `Connection`：每條連線一個有界佇列（1000）。一般事件放不下 → 清空佇列、送 SNAPSHOT_REQUIRED{queue_overflow} 後關閉；即時事件與心跳放不下就直接捨棄（本來就可遺失）。
  - 傳輸層抽象（`send_json` / `close`），所以可以用假連線測試；FastAPI 只是轉接。
- `api/.../routers/ws.py`：`WS /ws/companies/{id}?token=...&since=...`。瀏覽器無法在 WebSocket 上帶標頭，權杖放查詢字串（`05` §6）；權杖錯誤或公司不存在時先送 ERROR 訊息再關閉（4401 / 4404），因為瀏覽器讀不到握手被拒的原因。
- API 啟動時建立並啟動 `EventHub`，關閉時停止並關掉所有連線。
- `runtime/events/outbox.py`：新增 `publish_ephemeral()`（即時事件只走 NOTIFY、不落表、上限 7900 位元組），供 T-304 使用。

### 協定

| 伺服器 → 瀏覽器 | 說明 |
|---|---|
| HELLO `{server_time, head_seq, mode}` | 第一則；`mode` 為 live（已是最新）、backlog（要補）、snapshot_required |
| EVENTS `{items}` → BACKLOG_DONE `{head_seq}` | 從 `since` 補到最新，每批 ≤ 200 |
| EVENT `{...envelope}` | 即時，依序、不重複 |
| EPHEMERAL `{event_type, agent_id, run_id, payload, occurred_at}` | 沒有 seq，可能被捨棄 |
| HEARTBEAT `{server_time, head_seq}` | 每 15 秒；收到 PING 也回一則 |
| SNAPSHOT_REQUIRED `{reason}` | gap_too_large（落後超過 5000）、unknown_since（沒帶或大於最新）、queue_overflow；之後關閉連線 |

瀏覽器 → 伺服器：ACK `{last_seq}`（只記錄）、PING。

### 設計決策
- **不以序號算術偵測缺口**（T-302 帶來的問題）：序號是全域的，公司的事件序號本來就不連續。改由閘道保證同一條連線依序且完整：outbox 以公司鎖讓同公司事件依序號提交，閘道以 `seq > 游標` 從資料庫讀，所以讀到的一定是完整的後續；WebSocket 在同一條連線上不會掉訊息。會斷流的只有兩種情況，而且都明確通知（SNAPSHOT_REQUIRED）。前端因此不需要等待緩衝與 REST 補洞，`05` §4 已修訂。
- **先登記、再補 backlog**：新連線先加入公司的連線集合，再讀 backlog；這段期間到的即時事件先進佇列，送出時丟掉序號不大於已送出的部分。這樣 backlog 與即時之間不會漏，也不會重複。
- **公司的游標**在第一條連線時設為目前最新序號，最後一條連線離開時清除；讀取在同一把鎖下進行，避免同時的通知與輪詢重複送出。

### 遇到的問題
- （我造成的）第一版在讀取時一口氣把整批事件放進所有連線的佇列，中間不讓出執行權；佇列較小時，連正常的連線也會被判定溢出。改為每個事件之後讓出一次，溢出才真正代表「這條連線送不出去」。由佇列溢出測試（一快一慢兩條連線）發現。
- （我造成的）停止時只關閉連線、沒有取消各連線的送出工作，測試結束時出現「Task was destroyed but it is pending」。改為停止時逐一中斷連線。
- （我造成的）第一條連線登記前若剛好有讀取在進行，舊版會清掉游標，之後從序號 0 重讀該公司全部歷史。改為游標只在讀取時於鎖內建立、最後一條連線離開時才清除。

### 驗證
- `tests/realtime/test_gateway.py`：10 個通過，連續 5 次皆通過。包含 `05` §8 的四個測試：
  - backlog 後轉即時（分批、BACKLOG_DONE、順序與不重複）；
  - SNAPSHOT_REQUIRED（落後太多、沒帶 since、since 大於最新；剛好在上限內則正常補）；
  - 完全關閉 LISTEN 時由輪詢送達；
  - 佇列溢出：慢的連線收到 SNAPSHOT_REQUIRED 並被關閉，同公司快的連線 10 個事件全部收到。
  - 另有：即時通知不必等輪詢、公司隔離、即時事件只送到所屬公司、心跳與 PING / ACK，以及**以真實 uvicorn 伺服器與真實 WebSocket 客戶端**測端點（錯誤權杖 4401、不存在的公司、HELLO、即時事件、PING）。
- 在開發資料庫手動測試：API + 工作程序（假模型）+ WebSocket 客戶端。快照的 `last_seq` 與 HELLO 的 `head_seq` 一致（48），啟動 EchoWorkflow 後 40 個事件依序即時送達，最後是三個 AGENT_RUN_COMPLETED；兩個程序正常結束、日誌沒有錯誤。
- Python 測試共 643 個通過。

---

## T-302 · 投影契約測試

### 做了什麼
- `realtime/reducer.py`：Python 參考 reducer，是「快照 + 事件 → 快照」的可執行規格。`RealtimeState.from_snapshot()` 由快照建立狀態，`apply(event)` 套用一個事件，`view(now)` 產生與 `load_snapshot(now=...)` 相同形狀的投影；`canonical()` 定義比較哪些內容（以 ID 為鍵、JSON 值、不含 `server_time`）。規則逐條對應寫入資料列的程式（活動服務、任務管理員）。
- `tests/realtime/test_contract.py`：**以真實程式產生的隨機歷史驗證**。每個種子建立一間公司（三個角色各兩位代理），隨機執行 120 次操作，每次操作一個提交的交易：啟動工作流程（echo 與含人工審批節點的測試範本）、領取、工作中的活動變化、成功 / 可重試失敗 / 最終失敗 / 預算 / 政策 / 人為中止、請求審批、核准或駁回、人工節點、取消、暫停與恢復、回收過期租約、釋放受阻任務。每次操作後有 25% 機率取快照。
  - 驗證：**每個快照加上之後的事件，重建出下一個快照與最後一個快照**；另外從第一個快照重建 11 分鐘後的投影（時鐘規則）。
  - 8 個種子，每個約 45 次有效操作、25 ~ 35 個快照，並檢查每個歷史至少涵蓋 10 種操作，避免「全部略過也通過」。
- 跨語言契約檔：`make realtime-fixture` 產生 `frontend/web/src/realtime/__fixtures__/contract.json`（第一個快照、148 個事件、24 種事件類型、最後一個快照）。T-305 的 TypeScript reducer 必須由它重建出相同結果。

### 事件的變更（事件結構已重新產生）
- `AGENT_CREATED` 新增 `avatar_key`：reducer 需要它才能只靠事件加入新代理；`hire_agent` 也可以指定頭像。
- `AGENT_RUN_ABORTED` 新增 `final`（預設 true）：租約被回收但仍有嘗試次數、以及預算中止，這兩種中止只是軌跡資料，代理的狀態由接下來的事件決定；現在標記為 `final=false`，活動服務拒絕以它改變狀態，reducer 也忽略它。與既有的 `AGENT_RUN_FAILED.final` 一致。

### 契約測試找到的問題
- **（真實錯誤）暫停中的代理會讓任務管理員出錯**：操作者在代理執行途中暫停它，之後任務管理員要結束執行（成功、失敗、中止、取消、回收租約）時，寫入活動狀態會因「PAUSED 只能恢復」而拋出例外。最嚴重的是回收租約：它在同一個交易處理一批任務，一位暫停的代理會讓**所有公司**的過期租約都無法回收。
  - 修正：新增 `set_activity_unless_paused`，任務管理員的活動寫入一律使用它；代理維持 PAUSED（不寫入、不發事件），任務與執行照常推進。代理執行器自己的「思考 / 工作」寫入仍會被拒絕，該次執行因此失敗並依規則重試；工作程序不會把新任務派給暫停的代理。
  - 回歸測試：`test_paused_agent_does_not_block_its_run_from_ending`（回收、失敗、成功、預算中止、取消都在代理暫停時正常完成，代理維持 PAUSED）。
- **事件序號是全域的**：`events.seq` 是整張表共用的遞增序號（`07` §6 決定不做每間公司的序號），所以一間公司的事件序號中間本來就會有空號。`05` §4 用「`seq > last_seq + 1` 就是漏了事件」來補洞，照做會把正常的空號當成缺口。T-302 的 reducer 只以 `seq <= last_seq` 去重；缺口偵測留給 T-303（閘道保證依序、完整送出）與 T-306 決定，可能的做法是事件附上同公司的前一個序號（在寫入事件時已持有公司鎖，可以取得）。

### 生成器本身的修正（我造成的）
- 第一版操作權重不當：代理暫停後很少恢復、租約回收太頻繁，多數操作被略過（有一個種子只套用 22 次），但比對依然「通過」。加上「有效操作數」與「操作種類數」兩個下限後才看出問題；改為加權抽選、每個角色兩位代理、領取時像工作程序一樣依序嘗試所有代理、沒有作用的操作不計入。

### 驗證
- `tests/realtime/test_contract.py`：9 個通過（8 個種子 + 重複 / 其他公司事件被忽略）。
- Python 測試共 633 個通過；事件結構檔已更新；階段 1 驗收仍通過。

---

## 模型供應者：NVIDIA Build（D-005，2026-09-18）

使用者決定改用 NVIDIA Build 的 `z-ai/glm-5.3` 為主要模型，並保留 Anthropic 可隨時切換。

### 選擇過程
依代理的三個必要能力（呼叫工具、依格式輸出 JSON、繁體中文）比對 NVIDIA 模型頁：
- `z-ai/glm-5.3`：頁面明列函式呼叫、結構化輸出、推理皆支援 → 主要模型；
- `z-ai/glm-5.3-flash`：同系列、支援工具呼叫、較快 → 備援；
- `moonshotai/kimi-k3`：主打代理，但頁面未列函式呼叫與結構化輸出 → 留待實測比較；
- `deepseek-v4-flash-0731`：標示 5 天後下架 → 不採用。

### 做了什麼
- `runtime/models/providers/openai_compat.py`：`OpenAICompatibleProvider`，以 httpx 呼叫 `POST {base_url}/chat/completions`，不依賴廠商 SDK；以名稱與網址參數化，自架 NIM 或其他 OpenAI 相容服務也能用。
  - 工具呼叫對應 `tools` / `tool_calls`，工具結果為 `role: tool` 訊息；模型產生的參數不是合法 JSON 時原樣轉交，由工具登錄表拒絕並回饋給模型。
  - **結構化輸出分兩種**：沒有提供工具時用伺服器端 `response_format: json_schema` 強制；有工具時改把 schema 寫進系統提示，因為這類伺服器的受限解碼可能讓模型無法呼叫工具。兩種情況都由閘道驗證、執行器修補。
  - 推理內容（`reasoning_content`）保留在軌跡中，不送回下一輪。
  - 快取的提示權杖記為 `cache_read_tokens`，不重複計入輸入。
  - 429 與 5xx 依 `Retry-After` 等待後重試兩次（免費端點每分鐘限流），仍失敗才交給路由的備援；其他 4xx 不備援。
- 設定：`MODEL_PROVIDER` 新增 `nvidia`；`NVIDIA_API_KEY`、`NVIDIA_BASE_URL`。兩把金鑰可同時存在，`MODEL_PROVIDER` 決定用哪一個；路由器對兩者使用同一套 frontier / fast 規則。
- `httpx` 由開發相依改為執行相依。
- echo 代理的輸出上限由 1024 提高到 4096 權杖：真實模型會先推理再回答，1024 容易被截斷。
- 文件：`DECISIONS.md` 新增 D-005；`.env.example` 與 `RUNBOOK.md` 第六節改寫（NVIDIA 串接、Anthropic 切換、驗證方式、常見錯誤）。

### 驗證
- `tests/runtime/models/test_openai_compat.py`：22 個通過（請求與回應對應、兩種結構化輸出、工具迴圈歷史、推理不回送、停止原因、限流重試、錯誤對應、設定與切換）。
- 真實呼叫測試（標記 `integration`，沒有金鑰時略過）：
  - `test_nvidia_live.py`：glm-5.3 的結構化輸出（中英標題）與兩輪工具迴圈；
  - `tests/e2e/test_echo_live.py`：用目前選的真實供應者完整跑 EchoWorkflow，作為階段 3 進入條件的檢查。

### 實測（2026-09-18，使用者的 NVIDIA 金鑰）
- 使用者把金鑰放進 `.env` 後，`.env` 中生效的四行仍是 `fake` 與 Claude Haiku（範例區塊是註解），改為 NVIDIA 設定（修改前已備份）。
- **只執行 NVIDIA 的實測**：`.env` 也有 Anthropic 金鑰，`-m integration` 會連 Claude 測試一起跑並實際計費，因此排除。
- **`z-ai/glm-5.3` 在免費端點上太慢**：只要求回答一個字，第一次 120 秒內沒有回應，第二次花了 **224 秒**。`z-ai/glm-5.3-flash` 同樣請求 4.4 秒。第一次跑測試因此卡住超過 10 分鐘，後來手動停止。
- 為了驗證串接本身，以環境變數臨時改用 glm-5.3-flash（不改 `.env`）：

| 測試 | 結果 | 時間 |
|---|---|---|
| `test_nvidia_live.py::test_structured_output_live` | ✅ 繁體中文標題「全球首部AI專法上路 歐盟AI法案確立風險分級管制」與英文標題，合法 JSON | — |
| `test_nvidia_live.py::test_tool_loop_live` | ✅ 工具呼叫 → 工具結果 → 最終 JSON | 兩個合計 96 秒 |
| `tests/e2e/test_echo_live.py` | ✅ 三位代理各 2 次呼叫、皆第一次嘗試成功、無需修補；內容逐棒延續 | 186 秒 |

- **階段 3 進入條件「真實模型呼叫通過一次」已滿足**（glm-5.3-flash）。

### 實測發現的問題與修正
- （我造成的）逾時後仍重試兩次，每次等 180 秒：以 glm-5.3 為主要模型時，代理每次呼叫最多卡 9 分鐘才改用備援。改為**逾時不重試、立刻交給備援模型**，並新增 `NVIDIA_TIMEOUT_SECONDS`（預設 180）。限流與 5xx 仍會依 `Retry-After` 短暫重試。
- 使用者決定主要模型改用 glm-5.3-flash、不設備援（**D-006**），`.env` 已更新並驗證設定可載入。glm-5.3 可日後再測或改用付費端點。

---

## 提交紀錄

| 提交 | 日期 | 內容 | 持續整合 |
|---|---|---|---|
| `159055f` | 2026-09-18 | D-007 改用 Tailwind CSS v4；權杖說明改寫 | ✅ 執行編號 `35331292362` |
| `2a9ca73` | 2026-09-18 | T-314 簡易 KPI、T-309 Dashboard 頁 | ✅ 執行編號 `35330711028`（python 2 分 7 秒、web 41 秒） |
| `1b3d46f` | 2026-09-18 | T-307 UI store、T-308 型別化 API client 與 Query hooks、執行 / 任務明細 API | ✅ 執行編號 `35329218379`（python 1 分 55 秒、web 37 秒，含兩個新的型別生成檢查） |
| `20563b7` | 2026-09-18 | T-306 修正 lint | ✅ 執行編號 `35325556095`（python 1 分 56 秒、web 31 秒） |
| `a744452` | 2026-09-18 | T-306 RealtimeClient 與 CORS | ❌ 執行編號 `35325294400`：web 工作 lint 失敗（未使用的變數），由 `20563b7` 修正 |
| `48270ad` | 2026-09-18 | T-305 前端即時 store 與 reducer | ✅ 執行編號 `35323678762`（python 2 分 1 秒、web 35 秒） |
| `1c4bb99` | 2026-09-18 | T-304 代理即時進度 | ✅ 執行編號 `35322357037`（python 2 分 4 秒、web 35 秒） |
| `85e3e8c` | 2026-09-18 | T-303 WebSocket 閘道 | ✅ 執行編號 `35320982169`（python 1 分 48 秒、web 25 秒） |
| `dab9998` | 2026-09-18 | T-302 投影契約測試；修正暫停代理阻擋任務管理員 | ✅ 執行編號 `35319977479`（python 1 分 43 秒、web 25 秒） |
| `20e143a` | 2026-09-18 | D-006 主要模型改為 glm-5.3-flash | — |
| `0988547` | 2026-09-18 | NVIDIA 實測通過（glm-5.3-flash）；逾時直接改用備援模型 | ✅ 執行編號 `35318278889` |
| `24e3adf` | 2026-09-18 | D-005 NVIDIA Build 供應者（glm-5.3），保留 Anthropic 切換 | ✅ 執行編號 `35315842552` |
| `0bed143` | 2026-09-18 | T-301 即時投影與 snapshot API；階段 3 開發計畫 | ✅ 執行編號 `35306750129`（python 1 分 25 秒、web 26 秒） |
