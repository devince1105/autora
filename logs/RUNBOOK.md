# 本機運作手冊

如何在 localhost 上分別運作資料庫、後端與前端，如何設定 AI 代理的模型串接，以及如何執行測試。內容對應階段 2 完成時（2026-09-18）的狀態；之後新增的程序、設定或 API 請同步更新本文件。

## 目錄

1. 系統組成與啟動順序
2. 第一次安裝
3. 資料庫
4. 後端：API 與工作程序
5. 前端
6. AI 代理的串接設定
7. 執行測試
8. 以 Docker 跑完整系統
9. 常見問題

---

## 一、系統組成與啟動順序

| 元件 | 位置 | 本機埠 | 依賴 |
|---|---|---|---|
| 資料庫 | Postgres 16 + pgvector（Docker 容器） | 5434 | — |
| API | `backend/api`（FastAPI） | 8000 | 資料庫 |
| 工作程序（worker） | `backend/worker/main.py` | — | 資料庫、模型供應者 |
| 前端 | `frontend/web`（Next.js） | 3000 | API（階段 3 起） |

```
資料庫  ──▶  套用遷移  ──▶  API ─────────┐
                      └─▶  工作程序（可多個） ├─▶  前端（階段 3 起才連 API）
```

- **API 與工作程序互相獨立**，只透過資料庫溝通。可以只開 API（查資料、啟動工作流程）而不開工作程序（任務會停在 READY），或反過來。
- 工作程序可以同時開好幾個，資料庫的鎖保證同一個任務不會被重複執行。
- 需要用到的工具：

| 工具 | 版本 | 用途 |
|---|---|---|
| Python | 3.12 | 後端（`python3.12` 需在 PATH 上，或執行 `make PYTHON=/path/to/python3.12 setup`） |
| Docker | Compose v2 | 資料庫容器 |
| Node | 22 以上 | 前端 |
| pnpm | 9 以上 | 前端相依套件 |

> **注意 Node 版本**：本機 shell 預設的 Node 是 v12，執行前端指令會出現 `internal/modules/cjs/loader.js` 之類的錯誤。先執行 `nvm use 22`。

---

## 二、第一次安裝

安裝 Python 虛擬環境（`.venv/`）、後端套件（可編輯模式）與前端相依套件；若沒有 `.env` 會從 `.env.example` 複製一份：

```bash
make setup
```

`.env` 是所有程序共用的設定檔（API、工作程序、Alembic、測試都讀它），不會被提交到 git，金鑰只放在這裡。各區塊的說明在下面對應的章節。

---

## 三、資料庫

### 啟動

啟動 Postgres 容器並等待就緒（只有資料庫，不含其他服務）：

```bash
make dev
```

第一次啟動、或拉到新的遷移之後，套用遷移：

```bash
make migrate
```

### 設定

| 變數 | 預設 | 說明 |
|---|---|---|
| `AUTORA_DB_PORT` | `5434` | 容器對外的埠（避開其他本機 Postgres 常用的 5432） |
| `DATABASE_URL` | `postgresql+asyncpg://autora:autora@localhost:5434/autora` | 必須是 `postgresql+asyncpg://` 開頭 |

要改用自己的 Postgres 也可以：需要 Postgres 16 並安裝 pgvector 擴充（遷移 0001 會執行 `CREATE EXTENSION vector`），再把 `DATABASE_URL` 指過去。

### 連進資料庫查資料

```bash
docker compose -f infra/docker-compose.yml exec db psql -U autora -d autora
```

常用查詢：

```sql
SELECT role, display_name, status FROM agents;
SELECT agent_id, state, detail FROM agent_activity;
SELECT name, state, attempt FROM tasks ORDER BY created_at DESC LIMIT 10;
SELECT seq, event_type, payload FROM events ORDER BY seq DESC LIMIT 20;
SELECT role, alias, model_id, tokens_in, tokens_out, cost_usd FROM model_calls ORDER BY created_at DESC LIMIT 10;
```

### 其他

- **測試用資料庫**是同一個容器裡的 `autora_test`，由測試自動建立並每次清空重建，不會影響開發資料。
- 檢查模型與遷移是否一致：`make db-check`。改了資料表之後產生遷移：`make migration m="說明"`，產生後要人工檢查內容。
- 停止容器：`make down`（資料保留在 Docker volume）。
- **完全清空開發資料**（無法復原，會刪掉所有公司、任務、事件）：

  ```bash
  docker compose -f infra/docker-compose.yml down -v
  ```

  之後重新 `make dev` 與 `make migrate`。

---

## 四、後端：API 與工作程序

兩者都需要資料庫已啟動並套用遷移。各開一個終端機分頁。

### API

```bash
.venv/bin/uvicorn --app-dir backend/api main:app --port 8000 --reload
```

- `--reload`：修改程式碼後自動重啟，開發時使用。
- 互動式文件：http://localhost:8000/docs 。右上角 Authorize 填入 `API_BEARER_TOKEN` 後可直接呼叫。
- 健康檢查：http://localhost:8000/health （不需要權杖）。

| 變數 | 預設 | 說明 |
|---|---|---|
| `API_BEARER_TOKEN` | `change-me` | 所有 `/api/*` 都要帶 `Authorization: Bearer <權杖>`。`AUTORA_ENV=prod` 時不允許預設值 |
| `AUTORA_ENV` | `dev` | `dev` / `test` / `prod` |
| `BLOB_STORE_DIR` | `<repo>/data/blobs` | 步驟的完整提示與回應。**API 與工作程序必須指向同一個目錄** |

目前的 API：

| API | 用途 |
|---|---|
| `GET /api/companies`、`POST /api/companies`、`GET /api/companies/{id}` | 公司 |
| `GET /api/companies/{id}/agents` | 代理與其目前的活動狀態 |
| `POST /api/companies/{id}/workflows` | 啟動工作流程 |
| `GET /api/events?company_id=...&after=<seq>` | 公司的事件流 |
| `GET /api/runs/{run_id}/trace` | 一次執行的完整軌跡（事件與步驟） |
| `GET /api/runs/{run_id}/steps/{seq}/blob` | 某一步的完整提示與回應 |
| `GET /api/approvals?company_id=...` | 待審批項目 |
| `POST /api/approvals/{id}/decide` | 核准或駁回 |

建立專案、雇用代理、調整預算與政策目前**還沒有 API**，請用下面第六節的腳本或 SQL。

### 工作程序（worker）

```bash
.venv/bin/python backend/worker/main.py
```

工作程序負責：為每位啟用中的代理領取任務並執行（呼叫模型、執行工具、評估）；每隔一段時間回收過期租約、讓逾期審批過期、觸發排程。

| 變數 | 預設 | 說明 |
|---|---|---|
| `WORKER_ID` | `<主機名>-<pid>` | 日誌與租約上顯示的名字；同時開多個時可自行命名 |
| `WORKER_CONCURRENCY` | `4` | 同時執行的代理數 |
| `WORKER_POLL_SECONDS` | `1` | 多久檢查一次有沒有新任務 |
| `WORKER_MAINTENANCE_SECONDS` | `15` | 多久做一次維護（回收租約、審批過期、排程） |
| `WORKER_COMPANY_IDS` | `[]` | JSON 陣列；只處理這些公司的代理，空陣列表示全部 |
| `TASK_LEASE_SECONDS` | `300` | 任務租約長度；工作程序當掉後，最晚這麼久會被其他工作程序接手 |
| `TASK_RETRY_BASE_SECONDS` | `10` | 失敗重試的退避基準（10 秒、20 秒、40 秒…，上限 5 分鐘） |

停止：按 Ctrl+C（或送 SIGTERM）。工作程序停止領取新任務，進行中的執行最多等 30 秒。直接強制結束也安全：租約過期後會由下一個工作程序重跑（有自動化測試證明不會重複產生結果）。

### 試跑一次工作流程

1. 建立示範公司、進行中的專案與三位代理（研究員 Rae、分析師 Ana、寫手 Wren）。會印出 `company_id` 與 `project_id`；重複執行只會沿用既有資料。

   ```bash
   .venv/bin/python backend/scripts/seed_echo.py
   ```

2. 啟動 EchoWorkflow（把兩個 ID 與權杖換成自己的值）：

   ```bash
   curl -X POST localhost:8000/api/companies/<company_id>/workflows -H "Authorization: Bearer <API_BEARER_TOKEN>" -H "Content-Type: application/json" -d '{"template":"echo.chain_v1","project_id":"<project_id>","params":{"topic":"AI"}}'
   ```

3. 工作程序的日誌會依序出現 researcher、analyst、writer 三行 `completed`。`run_id` 可從日誌或 `GET /api/events` 取得，再用 `GET /api/runs/{run_id}/trace` 查看每一步。

---

## 五、前端

```bash
nvm use 22
```

```bash
pnpm -F web dev
```

打開 http://localhost:3000 。

- **階段 2 的前端只有骨架頁面，還沒有連接 API。** Dashboard、代理面板、軌跡檢視器與即時更新在階段 3 實作，3D 辦公室在階段 4。
- 階段 3 需要補上的設定：前端的 API 位址（`NEXT_PUBLIC_API_URL`，Docker 設定裡已預留）與 API 端的 CORS（目前未設定，瀏覽器從 :3000 呼叫 :8000 會被擋）。
- 前端使用的事件型別由後端自動產生（`frontend/event-schema`）。後端事件有變動時執行 `make gen-schema`，不要手改產生的檔案。

---

## 六、AI 代理的串接設定

### 1. 選擇模型供應者

| `MODEL_PROVIDER` | 行為 | 何時用 |
|---|---|---|
| `fake`（預設） | 不呼叫任何真實模型、不產生費用。由各領域的模擬模型回答（目前只有 echo 領域有模擬） | 開發、示範、自動化測試 |
| `anthropic` | 呼叫 Claude API | 真實運作 |

自動化測試永遠使用 fake，不受 `.env` 影響（真實呼叫測試除外，見第七節）。

### 2. 串接 Claude API

在 `.env` 設定：

```
MODEL_PROVIDER=anthropic
ANTHROPIC_API_KEY=<你的 API 金鑰>
FRONTIER_MODEL_ID=claude-fable-5-1
FAST_MODEL_ID=claude-sonnet-5
MODEL_PRICES={"claude-fable-5-1":{"input":<價格>,"output":<價格>,"cache_read":<價格>,"cache_write":<價格>},"claude-sonnet-5":{"input":<價格>,"output":<價格>,"cache_read":<價格>,"cache_write":<價格>}}
ANTHROPIC_SERVER_FALLBACKS=true
```

| 變數 | 說明 |
|---|---|
| `ANTHROPIC_API_KEY` | 從 Anthropic Console 取得。只放在 `.env` |
| `FRONTIER_MODEL_ID` | 必填。所有代理預設使用的模型 |
| `FAST_MODEL_ID` | 選填。與 frontier 不同時，frontier 服務中斷、過載或限流時自動改用它；不設定則沒有備援 |
| `MODEL_PRICES` | 必填，一行 JSON。每個設定的模型 ID 都要有價格，單位是**每百萬權杖美元**；`cache_read` / `cache_write` 可省略（視為 0）。缺少價格時程序會拒絕啟動 |
| `ANTHROPIC_SERVER_FALLBACKS` | 預設開啟。模型拒答時由 API 端自動改用備援模型重試；成本依實際回覆的模型價格計算 |

可用的模型 ID：

| 模型 | ID | 說明 |
|---|---|---|
| Claude Fable 5.1 | `claude-fable-5-1` | 目前能力最強的正式版模型 |
| Claude Opus 5 | `claude-opus-5` | |
| Claude Sonnet 5 | `claude-sonnet-5` | 較快、較便宜，適合當 fast |
| Claude Haiku 4.5 | `claude-haiku-4-5-20251001` | 最快、最便宜 |

> **價格請以 Anthropic 官方定價頁為準**填入 `MODEL_PRICES`，本文件不列數字以免過期。價格填錯會讓成本紀錄與預算控管失準。

程式碼中不寫任何模型 ID（有測試強制），換模型只要改 `.env` 並重啟工作程序。

### 3. 確認串接成功

設定好後執行真實呼叫測試（會產生少量費用，共三次小型呼叫）：

```bash
.venv/bin/pytest backend -m integration -v
```

兩個測試都通過代表：金鑰有效、模型 ID 正確、結構化輸出可用、思考區塊可在工具迴圈中正確往返。這也是進入階段 3 前必須通過一次的檢查。

之後重啟工作程序，照第四節試跑 EchoWorkflow，三位代理就會由真實模型執行。每次呼叫的模型、權杖與成本記錄在 `model_calls` 資料表。

### 4. 目前的路由規則

- 所有角色、所有能力都使用 `frontier`；`fast` 只作為服務中斷時的備援。
- `agents.model_policy` 欄位目前**尚未生效**。依角色指定不同模型（例如行銷用 fast）屬於之後的工作。

### 5. 代理的其他設定（目前以 SQL 調整）

以下設定目前沒有 API，請連進資料庫（第三節）後以 SQL 修改。範例以示範公司 `echo-demo` 為例，已在開發資料庫驗證可執行。

**單次執行的預算上限**（超過即中止，任務進入 BLOCKED_BUDGET，代理顯示「等待預算」）：

```sql
UPDATE agents SET budget = '{"per_run_usd": 0.5}'
WHERE company_id = (SELECT id FROM companies WHERE slug = 'echo-demo') AND role = 'researcher';
```

**公司每日預算**（`period` 可為 `day` / `month` / `cycle`；`project_id` 填專案 ID 則為專案預算；`hard_cap = false` 只用於報表、不阻擋）：

```sql
INSERT INTO budgets (id, company_id, project_id, period, amount, hard_cap)
VALUES (gen_random_uuid(), (SELECT id FROM companies WHERE slug = 'echo-demo'), NULL, 'day', 10, true);
```

**收緊某角色的權限**（政策只能收緊、不能放寬；可設為 `needs_approval` 或 `deny`）。下例讓寫手每次寫筆記都需要人核准，可用來試用審批流程：

```sql
INSERT INTO company_policies (id, company_id, key, value)
VALUES (gen_random_uuid(), (SELECT id FROM companies WHERE slug = 'echo-demo'),
        'policy.overrides', '{"echo_note": {"writer": "needs_approval"}}')
ON CONFLICT (company_id, key) DO UPDATE SET value = EXCLUDED.value;
```

設定後啟動一次工作流程，寫手會停在「等待審批」（此流程有自動化測試）；用 `GET /api/approvals?company_id=...` 找到審批，再以 `POST /api/approvals/{id}/decide`（`{"decision": "approve"}`）核准，寫手會接續同一次執行。

**限制代理可用的工具**（空陣列表示使用該角色行為定義的全部工具）：

```sql
UPDATE agents SET tools = '{echo_note}'
WHERE company_id = (SELECT id FROM companies WHERE slug = 'echo-demo') AND role = 'writer';
```

**暫停代理**（工作程序不會再替它領取任務；改回 `active` 即恢復）：

```sql
UPDATE agents SET status = 'paused'
WHERE company_id = (SELECT id FROM companies WHERE slug = 'echo-demo') AND role = 'writer';
```

政策與預算在每次執行開始時讀取，修改後不需要重啟工作程序。

### 6. 代理的提示、輸出格式與工具在哪裡

代理的「工作內容」是程式碼，由各領域提供，並在 `backend/autora/app.py` 註冊：

| 內容 | 位置（以 echo 領域為例） |
|---|---|
| 系統提示、輸出格式、驗證器、可用工具、步數與修補上限 | `backend/autora/domains/echo/workflow.py` 的 `register_behaviors` |
| 工具 | 同檔案的 `register_tools` |
| 工作流程範本（哪些步驟、誰做、依賴關係） | 同檔案的 `TEMPLATE` |
| 權限規則 | `backend/autora/domains/echo/policy.py` |
| fake 模式下的模擬模型 | `backend/autora/domains/echo/simulation.py` |

新聞編輯部的代理（研究、分析、寫作、編輯、行銷）與網路搜尋（Tavily，`TOOLS_PROFILE=live` 與 `TAVILY_API_KEY`）在階段 5 實作；目前這兩個設定**尚未生效**。

---

## 七、執行測試

### Python 測試

```bash
AUTORA_REQUIRE_DB=1 .venv/bin/pytest backend -q
```

- 需要資料庫已啟動（`make dev`）。測試使用獨立的 `autora_test` 資料庫。
- 一定要加 `AUTORA_REQUIRE_DB=1`：資料庫沒啟動時測試會直接失敗。不加的話，需要資料庫的測試會被略過，看起來像全部通過。
- 階段 2 完成時共 592 個測試，約 40 秒。

只跑一部分時指定路徑：

```bash
AUTORA_REQUIRE_DB=1 .venv/bin/pytest backend/tests/e2e -v
```

| 路徑 | 內容 |
|---|---|
| `backend/tests/acceptance/` | 階段 1 驗收 |
| `backend/tests/e2e/test_echo_workflow.py` | 階段 2 驗收：EchoWorkflow 端到端 |
| `backend/tests/e2e/test_recovery.py` | 當機恢復：真實工作程序被 SIGKILL 後由另一個接手 |
| `backend/tests/runtime/` | 執行環境各元件（任務管理員、代理執行器、政策、審批…） |
| `backend/tests/api/` | HTTP API |

### 前端測試

```bash
nvm use 22
```

```bash
pnpm test
```

### 真實模型測試

見第六節第 3 點。預設不執行，沒有設定金鑰時會顯示為略過。

### 提交前的檢查（與 CI 相同）

```bash
make lint
```

```bash
make db-check
```

```bash
make gen-schema-check
```

```bash
make phase1-acceptance
```

`make phase1-acceptance` 會重新產生 `frontend/event-schema/test/fixtures/phase1-events.json`，內容只有 ID 與時間不同，不需要提交：

```bash
git checkout -- frontend/event-schema/test/fixtures/phase1-events.json
```

---

## 八、以 Docker 跑完整系統

不想分開開終端機時，可以一次啟動資料庫、API、工作程序與前端四個容器：

```bash
make up
```

- 讀取根目錄的 `.env`（容器內的 `DATABASE_URL` 會自動改指向容器內的資料庫）。
- api 與 worker 共用物件儲存磁碟區；worker 停止時有 40 秒寬限。
- 第一次啟動後從主機套用遷移並建立示範資料：

  ```bash
  make migrate
  ```

  ```bash
  .venv/bin/python backend/scripts/seed_echo.py
  ```

- 查看日誌：`make logs`；停止：`make down`。

---

## 九、常見問題

| 狀況 | 原因與解法 |
|---|---|
| 前端指令出現 `internal/modules/cjs/loader.js` 錯誤 | Node 版本太舊，執行 `nvm use 22` |
| Python 測試大量「skipped」 | 資料庫沒啟動。執行 `make dev`，並加上 `AUTORA_REQUIRE_DB=1` 讓它直接報錯 |
| `database unavailable at ...` | 同上；或 5434 埠被佔用，可在 `.env` 改 `AUTORA_DB_PORT` 與 `DATABASE_URL` |
| 任何程序啟動時出現 `Invalid configuration` | `.env` 不完整。例如 `MODEL_PROVIDER=anthropic` 卻缺少金鑰、模型 ID 或 `MODEL_PRICES`；訊息會列出缺哪一項 |
| 工作流程啟動了但任務一直是 READY | 工作程序沒開，或 `WORKER_COMPANY_IDS` 沒有包含這間公司，或代理被暫停 |
| 啟動工作流程回 422「project … is PROPOSED」 | 工作流程只能在 ACTIVE 專案中執行；用 `seed_echo.py` 建立的專案即為 ACTIVE |
| API 回 401 | 權杖與 `.env` 的 `API_BEARER_TOKEN` 不一致 |
| 工作程序日誌出現 `NoSimulation` | `MODEL_PROVIDER=fake` 時，只有提供模擬模型的領域（目前是 echo）能執行 |
| 工作程序日誌出現 `BudgetExceeded`、代理顯示等待預算 | 超過代理、任務、專案或公司預算。目前調高預算後**不會自動重新排入佇列**：任務管理員已有釋放受阻任務的功能，但還沒接到 API 或工作程序 |
| 軌跡 API 讀不到步驟內容（blob） | API 與工作程序的 `BLOB_STORE_DIR` 不一致 |
| `make db-check` 報告有差異 | 模型改了但沒有遷移，執行 `make migration m="說明"` 產生後檢查內容 |
