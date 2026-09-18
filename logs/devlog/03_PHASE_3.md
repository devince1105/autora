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

- **進入條件**：路線圖要求真實模型呼叫通過一次才進入階段 3（原為「真實 Anthropic 呼叫」，D-005 改為不限供應者）。✅ 2026-09-18 以 NVIDIA `z-ai/glm-5.3-flash` 跑 `tests/e2e/test_echo_live.py` 通過（見下方 D-005 一節的實測）。
- 階段 2 留下、會在本階段用到的：
  - `GET /api/tasks/{id}`、`GET /api/runs/{id}` 尚未實作（T-308、T-310 需要時補上）；
  - API 尚未設定 CORS，前端的 API 位址設定也還沒有（T-306 / T-308 時處理）；
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
| `dab9998` | 2026-09-18 | T-302 投影契約測試；修正暫停代理阻擋任務管理員 | ✅ 執行編號 `35319977479`（python 1 分 43 秒、web 25 秒） |
| `20e143a` | 2026-09-18 | D-006 主要模型改為 glm-5.3-flash | — |
| `0988547` | 2026-09-18 | NVIDIA 實測通過（glm-5.3-flash）；逾時直接改用備援模型 | ✅ 執行編號 `35318278889` |
| `24e3adf` | 2026-09-18 | D-005 NVIDIA Build 供應者（glm-5.3），保留 Anthropic 切換 | ✅ 執行編號 `35315842552` |
| `0bed143` | 2026-09-18 | T-301 即時投影與 snapshot API；階段 3 開發計畫 | ✅ 執行編號 `35306750129`（python 1 分 25 秒、web 26 秒） |
