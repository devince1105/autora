# 本機運作手冊

如何在 localhost 上安裝、測試與實際運作 Autora。內容對應階段 2 完成時（2026-09-18）的狀態；之後新增的程序或指令請同步更新本文件。

## 需要的工具

| 工具 | 版本 | 用途 |
|---|---|---|
| Python | 3.12 | 後端、測試（`python3.12` 需在 PATH 上，或執行 `make PYTHON=/path/to/python3.12 setup`） |
| Docker | Compose v2 | Postgres 16 + pgvector 容器 |
| Node | 22 以上 | 前端與事件契約測試 |
| pnpm | 9 以上 | 前端相依套件 |

> **注意 Node 版本**：本機 shell 預設的 Node 是 v12，執行前端指令會出現 `internal/modules/cjs/loader.js` 之類的錯誤。先執行 `nvm use 22`。

## 一、第一次安裝

1. 安裝 Python 虛擬環境（`.venv/`）、後端套件與前端相依套件；若沒有 `.env` 會從 `.env.example` 複製一份。

   ```bash
   make setup
   ```

2. 啟動 Postgres 容器（localhost:5434，避免和其他本機 Postgres 衝突），並等待就緒。

   ```bash
   make dev
   ```

3. 套用資料庫遷移。

   ```bash
   make migrate
   ```

之後每次開機只需要執行 `make dev`（遷移有更新時再執行 `make migrate`）。

### `.env` 的重點

| 變數 | 預設 | 說明 |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://autora:autora@localhost:5434/autora` | 開發資料庫 |
| `MODEL_PROVIDER` | `fake` | `fake` 不呼叫任何真實模型、不產生費用；`anthropic` 需要下面三項 |
| `ANTHROPIC_API_KEY`、`FRONTIER_MODEL_ID`、`MODEL_PRICES` | 空 | 使用真實模型時才需要；`MODEL_PRICES` 是 JSON，每個模型 ID 都要有價格 |
| `API_BEARER_TOKEN` | `change-me` | 呼叫 API 時的權杖 |
| `WORKER_*`、`TASK_*` | 見 `.env.example` | 工作程序的並行數、輪詢間隔、租約長度等 |

`.env` 不會被提交到 git，金鑰只放在這裡。

## 二、執行測試

### Python 測試

```bash
AUTORA_REQUIRE_DB=1 .venv/bin/pytest backend -q
```

- 測試會自動建立獨立的 `autora_test` 資料庫，每次從乾淨的資料表開始（執行全部遷移），**不會動到開發資料**。
- 一定要加 `AUTORA_REQUIRE_DB=1`：資料庫沒啟動時測試會直接失敗。不加的話，需要資料庫的測試會被略過，看起來像全部通過。
- 階段 2 完成時共 592 個測試，約 40 秒。

只跑一部分時指定路徑，例如階段 2 驗收與當機恢復測試：

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

### 真實模型測試（選用）

預設不執行。在 `.env` 設好 `ANTHROPIC_API_KEY`、`FRONTIER_MODEL_ID`、`MODEL_PRICES` 之後：

```bash
.venv/bin/pytest backend -m integration
```

這是進入階段 3 前必須通過一次的檢查。

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

## 三、在本機實際運作系統

需要兩個終端機分頁（API 與工作程序）。

1. 建立示範公司、進行中的專案與三位代理（研究員 Rae、分析師 Ana、寫手 Wren）。會印出 `company_id` 與 `project_id`；重複執行只會沿用既有資料。

   ```bash
   .venv/bin/python backend/scripts/seed_echo.py
   ```

2. 第一個分頁：啟動 API（localhost:8000）。

   ```bash
   .venv/bin/uvicorn --app-dir backend/api main:app --port 8000
   ```

3. 第二個分頁：啟動工作程序。

   ```bash
   .venv/bin/python backend/worker/main.py
   ```

4. 啟動一次 EchoWorkflow。把兩個 ID 換成第 1 步印出的值，權杖用 `.env` 的 `API_BEARER_TOKEN`。

   ```bash
   curl -X POST localhost:8000/api/companies/<company_id>/workflows -H "Authorization: Bearer <API_BEARER_TOKEN>" -H "Content-Type: application/json" -d '{"template":"echo.chain_v1","project_id":"<project_id>","params":{"topic":"AI"}}'
   ```

工作程序的日誌會在幾秒內依序出現三行 `completed`（researcher → analyst → writer）。

### 查看結果

瀏覽器打開 http://localhost:8000/docs，右上角 Authorize 填入權杖後即可直接呼叫：

| API | 看什麼 |
|---|---|
| `GET /api/companies/{id}/agents` | 三位代理目前的活動狀態 |
| `GET /api/runs/{run_id}/trace` | 一次執行的完整軌跡（事件與步驟） |
| `GET /api/runs/{run_id}/steps/{seq}/blob` | 某一步的完整提示與回應 |
| `GET /api/events?company_id=...` | 公司的事件流 |
| `GET /api/approvals?company_id=...` | 待審批項目 |
| `POST /api/approvals/{id}/decide` | 核准或駁回 |

`run_id` 可以從工作程序的日誌或 `GET /api/events` 取得。

### 停止

在兩個分頁按 Ctrl+C。工作程序收到訊號後會停止領取新任務，並等進行中的執行最多 30 秒。即使直接強制結束也安全：租約過期後，下次啟動的工作程序會接手重跑。

關閉資料庫容器（資料會保留在 Docker volume）：

```bash
make down
```

### 前端

階段 2 的前端只有骨架頁面，Dashboard 與 3D 辦公室在階段 3、4 實作。要看骨架：

```bash
pnpm -F web dev
```

## 四、以 Docker 跑完整系統（選用）

```bash
make up
```

會建置並啟動 db、api（:8000）、worker、web（:3000）四個容器，讀取根目錄的 `.env`。api 與 worker 共用物件儲存磁碟區。第一次啟動後從主機套用遷移，並建立示範資料：

```bash
make migrate
```

```bash
.venv/bin/python backend/scripts/seed_echo.py
```

查看日誌用 `make logs`，停止用 `make down`。

## 五、常見問題

| 狀況 | 原因與解法 |
|---|---|
| 前端指令出現 `internal/modules/cjs/loader.js` 錯誤 | Node 版本太舊，執行 `nvm use 22` |
| Python 測試大量「skipped」 | 資料庫沒啟動。執行 `make dev`，並加上 `AUTORA_REQUIRE_DB=1` 讓它直接報錯 |
| `database unavailable at ...` | 同上；或 5434 埠被佔用，可在 `.env` 改 `AUTORA_DB_PORT` 與 `DATABASE_URL` |
| 啟動工作流程回 422「project … is PROPOSED」 | 工作流程只能在 ACTIVE 專案中執行；用 `seed_echo.py` 建立的專案即為 ACTIVE |
| 工作流程回 401 | 權杖與 `.env` 的 `API_BEARER_TOKEN` 不一致 |
| 工作程序日誌出現 `NoSimulation` | `MODEL_PROVIDER=fake` 時，只有已提供模擬模型的領域（目前是 echo）能執行 |
| `make db-check` 報告有差異 | 模型改了但沒有遷移，執行 `make migration m="說明"` 產生後檢查內容 |
