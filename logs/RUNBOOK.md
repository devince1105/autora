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
| `CORS_ORIGINS` | `["http://localhost:3000"]` | JSON 陣列：允許從瀏覽器呼叫 API 的來源（前端的網址）。前端改用其他埠或網域時要加上 |
| `AUTORA_ENV` | `dev` | `dev` / `test` / `prod` |
| `BLOB_STORE_DIR` | `<repo>/data/blobs` | 步驟的完整提示與回應。**API 與工作程序必須指向同一個目錄** |

目前的 API：

| API | 用途 |
|---|---|
| `GET /api/companies`、`POST /api/companies`、`GET /api/companies/{id}` | 公司 |
| `GET /api/companies/{id}/agents` | 代理與其目前的活動狀態 |
| `POST /api/companies/{id}/workflows` | 啟動工作流程 |
| `GET /api/events?company_id=...&after=<seq>` | 公司的事件流 |
| `GET /api/runs/{run_id}` | 一次執行的明細：輸入、輸出、評估、錯誤、成本、權杖 |
| `GET /api/tasks/{task_id}` | 任務明細與每一次嘗試的執行 |
| `GET /api/runs/{run_id}/trace` | 一次執行的完整軌跡（事件與步驟） |
| `GET /api/runs/{run_id}/steps/{seq}/blob` | 某一步的完整提示與回應 |
| `GET /api/approvals?company_id=...` | 待審批項目 |
| `POST /api/approvals/{id}/decide` | 核准或駁回 |
| `GET /api/companies/{id}/kpis` | Dashboard 的 KPI：現金、今日營收與支出（含模型費用）、今日目標 |
| `GET /api/companies/{id}/realtime/snapshot` | 即時畫面的起始狀態：代理、進行中任務、最近 100 個事件與 `last_seq` |
| `WS /ws/companies/{id}?token=<權杖>&since=<last_seq>` | 即時事件串流（WebSocket）：先補 `since` 之後的事件，再即時推送；另有代理的即時進度（不落表） |

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

4. **試審批流程**：打開開關後，每次執行都會停在寫手（writer）的 `echo_note`，等操作者在前端的審批收件匣（`/approvals`）核准或駁回。用的是公司政策的收緊設定（`policy.overrides`：`echo_note` / `writer` = `needs_approval`），不是特別寫的測試路徑。

   ```bash
   .venv/bin/python backend/scripts/seed_echo.py --approval on
   ```

   試完關掉，回到全自動：

   ```bash
   .venv/bin/python backend/scripts/seed_echo.py --approval off
   ```

   核准後寫手會接著執行（同一次嘗試）；駁回則取消寫手的任務。注意：同時開兩個工作程序也能運作，但日誌會分散在兩邊。

### 試跑新聞室（模擬模式，T-518）

整條新聞線（來源 → 題材 → 研究 → 分析 → 撰稿 → 審稿 → 人核准 → 發布 → 推廣）可以完全離線跑：模型用模擬（`MODEL_PROVIDER=fake`），搜尋與抓網頁用虛構的流明市 fixture（`TOOLS_PROFILE=fixture`），embedding 用確定性的雜湊向量（`EMBED_PROVIDER=fake`）。除了模型的決定，其他全是真的（工具、證據、事實查核、權限、審批、發布、事件）。

1. 在 `.env` 設 `MODEL_PROVIDER=fake`、`TOOLS_PROFILE=fixture`、`EMBED_PROVIDER=fake`（或在指令前加上這三個環境變數；embedding 不設成 fake 的話，分群與證據切段仍會呼叫 NVIDIA），啟動 API、工作程序與前端。
2. 建立示範新聞室（公司「流明日報（示範）」、專案、五位代理 Rae / Ana / Wren / Eli / Mika、兩個 fixture 來源），並開始一則題材：

   ```bash
   MODEL_PROVIDER=fake TOOLS_PROFILE=fixture EMBED_PROVIDER=fake .venv/bin/python backend/scripts/seed_newsroom.py --start --pace 4 --revise
   ```

   - `--start`：立刻讀取來源、把新項目分群成題材、選出標題含 `--story`（預設 `microgrid`）的最佳題材並啟動工作流程；不加就只建立公司與來源（之後排程每 5 分鐘會自動讀取與分群）。
   - `--pace 4`：模擬模型每次回覆至少 4 秒，方便在 3D 辦公室看代理工作。
   - `--revise`：編輯第一次審稿時一定退回（要求導言先交代對居民的意義），寫手修改後第二次審稿接受——示範修訂流程。
   - 重複執行會沿用同一家公司；再加 `--start` 會再開始一則還沒開始的題材（沒有符合的就列出目前的題材）。
3. 工作程序會依序執行研究、分析、撰稿、審稿，之後停在核准：到前端審批收件匣（`/approvals`）核准，發布與推廣接著完成。駁回則文章被駁回、題材放棄。
4. 公司政策 `newsroom.auto_approve_if_fact_check_passed` 設為 `true` 時（D-001，預設 `false`），查核通過就由系統自動核准，不經收件匣。

---

## 五、前端

```bash
nvm use 22
```

```bash
pnpm -F web dev
```

打開 http://localhost:3000 （會轉到 `/dashboard`）。

- **第一次進入要輸入操作者權杖**：填入 `.env` 的 `API_BEARER_TOKEN`。權杖只存在這個瀏覽器（localStorage），不會編進前端程式；API 回 401 時會自動回到輸入畫面。
- **Dashboard**（階段 3，T-309）：現金、今日營收、今日支出（含模型費用）、工作中代理、進行中任務、今日發布（階段 5 前顯示「—」）、今日目標，以及右上角的連線狀態。代理與任務的數字即時更新；金額在執行結束或帳務事件時更新，另每分鐘重新取一次。網址加 `?company=<公司 ID>` 可指定公司，否則顯示第一間。
- 要看到數字變化：API、工作程序與前端都要開著，再用第四節的 curl 啟動一次 EchoWorkflow。
- **代理卡片與面板**（T-310）：Dashboard 下方的卡片，點一下開右側面板（即時、步驟、工具、輸出）。
- **軌跡**（T-311）：`/trace/<run_id>` 列出一次執行的每個事件與步驟，每一步可載入完整提示與回應；`/tasks/<task_id>` 列出任務的每一次嘗試。從代理面板的「完整軌跡」進入，或直接把 `run_id` 放進網址。
- **事件時間軸**（T-312）：`/timeline`（Dashboard 右上角有連結）。最近的事件由新到舊，最多保留 500 則；可暫停（暫停期間的新事件會計數，按「繼續」一次顯示）、依代理與事件類型篩選。
- **審批收件匣**（T-313）：`/approvals`（Dashboard 右上角有連結，旁邊的數字是待審批數）。每筆顯示誰要做什麼、參數、已等待多久、何時過期，可連到執行軌跡與任務；填選填的理由後按「核准」或「駁回」。送出後卡片顯示「等待更新」，由 APPROVAL_* 事件更新列表（即時連線中斷時改為立即重新載入）；已被別人處理（409）會提示並重新載入。另有已核准、已駁回、已過期三個分頁。
- **3D 辦公室**（階段 4）：`/office`（Dashboard 右上角「辦公室」）。上方一列公司概況（與 Dashboard 相同的數字）；點人物（或 2D 看板的卡片）打開右側的代理詳細面板，相機移過去並跟著他（走路時也跟），`Esc` 或點空白處回到總覽；滑鼠拖曳旋轉、滾輪縮放；`1`～`6` 快速選各角色。標頭可切換「自動 / 3D / 2D」，或在網址加 `?view=3d`、`?view=2d`；沒有 WebGL 2 的瀏覽器一律 2D，手機寬度預設 2D。螢幕與桌燈顯示狀態（琥珀閃 = 等待審批 / 預算，紅閃 = 失敗），審批桌的燈在有人等審批時閃。3D 畫面左上角可切換室內設計風格（日式無印、侘寂風、工業風、Google 風、電光風；電光風是夜間的暗色風格），選擇記在這個瀏覽器。入口在右前方，旁邊是接待區（審批櫃台與休息區）；地上寫著各部門名稱。若畫面顯示「3D 暫停」，按「重新建立 3D」。
- 前端的 API 位址：`NEXT_PUBLIC_API_URL`（預設 `http://localhost:8000`，建置時寫入；Docker 設定已提供）。API 端需要在 `CORS_ORIGINS` 允許前端的網址（預設已允許 `http://localhost:3000`）。
- 前端使用的事件型別由後端自動產生（`frontend/event-schema`）。後端事件有變動時執行 `make gen-schema`，不要手改產生的檔案。
- 前端呼叫 REST API 的型別也由後端產生：API 的回應或參數有變動時執行 `make gen-api`（先輸出 OpenAPI 文件，再產生 TypeScript 型別）。CI 會檢查兩者是否最新。
- 呼叫 API 需要的權杖由操作者在瀏覽器輸入，存在 localStorage，不會編進前端程式（任何 `NEXT_PUBLIC_*` 都會被下載頁面的人看到）。

---

## 六、AI 代理的串接設定

### 1. 選擇模型供應者

| `MODEL_PROVIDER` | 行為 | 何時用 |
|---|---|---|
| `fake`（預設） | 不呼叫任何真實模型、不產生費用。由各領域的模擬模型回答（echo 與新聞室，見「試跑新聞室」） | 開發、示範、自動化測試 |
| `nvidia` | 呼叫 NVIDIA Build（OpenAI 相容 API）。**主要使用的供應者**，模型 `z-ai/glm-5.3-flash`（決策 D-005、D-006） | 真實運作 |
| `anthropic` | 呼叫 Claude API，保留作為隨時切換的選項 | 真實運作 |

自動化測試永遠使用 fake，不受 `.env` 影響（真實呼叫測試除外，見第 4 點）。

### 2. 串接 NVIDIA Build（主要）

1. 到 https://build.nvidia.com 登入，按「Generate API Key」取得金鑰（`nvapi-` 開頭）。
2. 在 `.env` 設定：

```
MODEL_PROVIDER=nvidia
NVIDIA_API_KEY=<你的 NVIDIA 金鑰>
FRONTIER_MODEL_ID=z-ai/glm-5.3-flash
FAST_MODEL_ID=
MODEL_PRICES={"z-ai/glm-5.3-flash":{"input":0,"output":0}}
```

| 變數 | 說明 |
|---|---|
| `NVIDIA_API_KEY` | NVIDIA 金鑰，只放在 `.env` |
| `NVIDIA_BASE_URL` | 預設 `https://integrate.api.nvidia.com/v1`。改用自架的 NIM 或其他 OpenAI 相容服務時才需要改 |
| `NVIDIA_TIMEOUT_SECONDS` | 預設 180。單次請求等這麼久沒有回應，就立刻改用 `FAST_MODEL_ID`（不重試） |
| `FRONTIER_MODEL_ID` | 必填，目前用 `z-ai/glm-5.3-flash`（D-006）。**API 用的模型 ID 和網站卡片上的名稱不同**：卡片寫 `glm-5-3-flash`，API 要填 `z-ai/glm-5.3-flash`。每個模型頁面的程式範例中 `model=` 後面就是正確的 ID |
| `FAST_MODEL_ID` | 選填，目前留空。主要模型過載、限流、逾時或服務中斷時改用它；比主要模型還慢的模型不適合當備援 |
| `MODEL_PRICES` | 免費端點填 0。改用付費的合作夥伴端點時，填該端點的每百萬權杖價格 |

注意事項：

- **回應時間**：免費端點的速度差異很大。2026-09-18 實測：`z-ai/glm-5.3` 回答一個字花了 224 秒（另一次 120 秒內沒有回應），`z-ai/glm-5.3-flash` 為 4 ~ 30 秒。主要模型太慢時，代理的每次呼叫都會等到逾時才改用備援。
- **速率限制**：免費端點多數模型約每分鐘 40 次請求。遇到限流時程式會依伺服器指示等待並重試兩次，仍失敗才改用備援模型或讓任務稍後重試。同時執行的代理多時，可調低 `WORKER_CONCURRENCY`。
- **隱私**：NVIDIA 試用條款允許記錄輸入與輸出並用於改善其服務，不要送出機密資料。
- **預算**：價格為 0 時，成本紀錄與預算上限不會擋下任何呼叫，實際的限制只有速率限制。
- **繁體中文**：glm 系列可能預設輸出簡體中文。代理的系統提示要明確要求繁體中文（zh-TW），新聞編輯部的驗證器也會檢查（階段 5）。
- 與 Claude 相比少了：伺服器端拒答備援、思考內容在工具迴圈中往返、提示快取計價。框架其他功能不受影響。

### 3. 串接 Claude API（隨時可切換）

在 `.env` 設定（NVIDIA 的金鑰可以留著）：

```
MODEL_PROVIDER=anthropic
ANTHROPIC_API_KEY=<你的 Anthropic 金鑰>
FRONTIER_MODEL_ID=claude-opus-5
FAST_MODEL_ID=claude-sonnet-5
MODEL_PRICES={"claude-opus-5":{"input":<價格>,"output":<價格>,"cache_read":<價格>,"cache_write":<價格>},"claude-sonnet-5":{"input":<價格>,"output":<價格>,"cache_read":<價格>,"cache_write":<價格>}}
ANTHROPIC_SERVER_FALLBACKS=true
```

可用的模型 ID：

| 模型 | ID | 說明 |
|---|---|---|
| Claude Fable 5.1 | `claude-fable-5-1` | 目前能力最強的正式版模型 |
| Claude Opus 5 | `claude-opus-5` | |
| Claude Sonnet 5 | `claude-sonnet-5` | 較快、較便宜，適合當 fast |
| Claude Haiku 4.5 | `claude-haiku-4-5-20251001` | 最快、最便宜 |

> **價格請以 Anthropic 官方定價頁為準**填入 `MODEL_PRICES`，本文件不列數字以免過期。Claude API 依權杖實際計費，費用記在 Anthropic Console 帳戶，與 Claude 訂閱方案分開；建議在 Console 設定每月花費上限。`ANTHROPIC_SERVER_FALLBACKS` 預設開啟：模型拒答時由 API 端自動改用備援模型重試。

### 切換供應者

兩把金鑰可以同時留在 `.env`。切換只要改三行，再重新啟動工作程序：

| 要改的設定 | NVIDIA | Anthropic |
|---|---|---|
| `MODEL_PROVIDER` | `nvidia` | `anthropic` |
| `FRONTIER_MODEL_ID` | `z-ai/glm-5.3-flash` | 例如 `claude-opus-5` |
| `FAST_MODEL_ID` | 留空 | 例如 `claude-sonnet-5` |

`MODEL_PRICES` 可以一次列出兩邊所有會用到的模型，切換時就不用改。每次呼叫實際用了哪個供應者與模型，都記錄在 `model_calls` 資料表。程式碼中不寫任何模型 ID（有測試強制）。

### 4. 確認串接成功

設定好後執行真實呼叫測試。沒有設定金鑰的測試會顯示為略過：

```bash
.venv/bin/pytest backend -m integration -v -s
```

| 測試 | 需要 | 驗證什麼 |
|---|---|---|
| `test_nvidia_live.py`（2 個） | `NVIDIA_API_KEY` | 目前主要模型的結構化輸出（中英標題）；工具呼叫 → 工具結果 → 最終 JSON 的兩輪迴圈 |
| `test_anthropic_live.py`（2 個） | `ANTHROPIC_API_KEY` | Claude 的結構化輸出；思考內容在工具迴圈中正確往返（會計費，共三次小型呼叫） |
| `tests/e2e/test_echo_live.py`（1 個） | `MODEL_PROVIDER` 設為 `nvidia` 或 `anthropic` 與其金鑰 | 用目前選的真實模型完整跑一次 EchoWorkflow：三位代理各寫一則筆記並回報合法 JSON |
| `tests/newsroom/test_tavily.py`（1 個） | `TAVILY_API_KEY` | 透過 `web_search` 工具做一次真實的 Tavily 搜尋（basic，1 點 credit），確認有結果、成本記在 `TOOL_COMPLETED` 事件、沒有產生 Evidence |
| `tests/newsroom/test_embed_live.py`（1 個） | `EMBED_PROVIDER=nvidia`、`EMBED_MODEL_ID` | 真實 embedding：維度 2048、中文問句能找到相關的英文段落、呼叫記在 `model_calls` |
| `tests/newsroom/test_fetch_live.py`（1 個） | 網路（免費） | `fetch_url` 抓 `https://example.com`：真實的公開位址檢查、下載、抽本文、存成 Evidence 與快照 |

EchoWorkflow 的真實模型測試通過，就滿足進入階段 3 的條件「真實模型呼叫通過一次」。之後照第四節啟動工作程序，三位代理就會由真實模型執行。

### 5. 目前的路由規則

- 所有角色、所有能力都使用 `frontier`；`fast` 只作為服務中斷時的備援。
- `agents.model_policy` 欄位目前**尚未生效**。依角色指定不同模型（例如行銷用 fast）屬於之後的工作。

### 6. 代理的其他設定（目前以 SQL 調整）

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

### 7. 代理的提示、輸出格式與工具在哪裡

代理的「工作內容」是程式碼，由各領域提供，並在 `backend/autora/app.py` 註冊：

| 內容 | 位置（以 echo 領域為例） |
|---|---|
| 系統提示、輸出格式、驗證器、可用工具、步數與修補上限 | `backend/autora/domains/echo/workflow.py` 的 `register_behaviors` |
| 工具 | 同檔案的 `register_tools` |
| 工作流程範本（哪些步驟、誰做、依賴關係） | 同檔案的 `TEMPLATE` |
| 權限規則 | `backend/autora/domains/echo/policy.py` |
| fake 模式下的模擬模型 | `backend/autora/domains/echo/simulation.py` |

新聞編輯部（階段 5）的代理（研究、分析、寫作、編輯、行銷）陸續加入中。

**網路搜尋（`web_search`，T-500、D-003）**：
- `TOOLS_PROFILE=fixture`（預設）：搜尋虛構的離線資料（`backend/autora/domains/newsroom/fixtures/search.json`，網址都在不存在的 `*.fixtures.autora.test`），不連網、不花錢。測試與模擬都用這個。
- `TOOLS_PROFILE=live`：用 Tavily，需要 `TAVILY_API_KEY`（到 tavily.com 申請，填進 `.env`；空白等於沒設，沒有金鑰會拒絕啟動）。每次搜尋的成本（預設 basic 1 點 credit × `TAVILY_COST_PER_CREDIT` 0.008 美元）記在該次工具呼叫的 `TOOL_COMPLETED` 事件。其他可調：`TAVILY_SEARCH_DEPTH`（basic / advanced，advanced 2 點）、`TAVILY_TIMEOUT_SECONDS`（15）、`TAVILY_REQUESTS_PER_MINUTE`（60，單一程序內的上限）。
- 搜尋結果只是**候選**，不是證據：要用某個網頁，代理必須再用 `fetch_url` 抓取並存成快照（T-502）。

**新聞來源（T-501）**：來源可以是 RSS / Atom feed、一組要追蹤的網址、或一個搜尋查詢。工作程序每 5 分鐘檢查一次，各來源依自己的間隔（預設 1 小時）讀取，新的項目寫進 `source_items`，時間軸會出現「讀取來源」事件。連續 5 次讀取失敗的來源會自動暫停（時間軸顯示「來源暫停」）。
- `TOOLS_PROFILE=fixture` 時只讀 `backend/autora/domains/newsroom/fixtures/feeds/` 裡的虛構 feed；`live` 才連網。連網時只會抓公開網址，`localhost`、內網、雲端 metadata 位址一律拒絕。可調：`FETCH_TIMEOUT_SECONDS`（15）、`FETCH_MAX_BYTES`（5000000）。
- 新增來源的頁面與 API 在 T-517 才會加入；目前只能從程式呼叫 `autora.domains.newsroom.sources.add_source`。

**題材（Story，T-504）**：輪詢到的項目每 5 分鐘（比輪詢晚 2 分鐘）自動分群成「題材」：同一網址、或內容夠像（`STORY_MATCH_THRESHOLD`，預設 0.65）的項目歸到同一個題材，不像的開新題材，時間軸顯示「發現題材：標題・分數」。分數看幾個不同來源報導、多新、來源信任度。已經寫過、被忽略或放棄的題材也會吸收同一件事的新報導，不會重複出現。目前中英文報導同一件事時會是兩個題材（跨語言合併會誤併相關報導）。換 embedding 模型時要重新確認門檻（`tests/newsroom/test_embed_live.py` 會檢查）。

**證據（`fetch_url`，T-502）**：代理要用某個網頁，必須用 `fetch_url` 把它存成證據——原始網頁存進 BlobStore（`BLOB_STORE_DIR`，私有，不會公開），抽出的本文存進資料庫，之後的主張（claim）只能引用這些文字。同一網址同一天內容沒變就重用同一份證據。PDF、圖片與需要執行 JavaScript 才有內容的頁面目前無法成為證據。

**證據搜尋（T-503、D-012）**：證據存進來時會切成段落並算出向量，代理可以用 `search_evidence` 依「意思」找段落（中英文可以互相找到）。向量由 `EMBED_PROVIDER` / `EMBED_MODEL_ID` 決定：`fake` 是離線的雜湊向量（測試與模擬用，只看字詞重疊）；`nvidia` 用 `nvidia/nemotron-3-embed-1b`（沿用 `NVIDIA_API_KEY`，免費端點）。每次 embedding 呼叫記在 `model_calls`（alias `embed`）。**換 embedding 模型時，新模型的向量維度必須是 2048**；舊段落的向量不會被新模型的搜尋用到（每段記錄了是哪個模型算的），只能用關鍵字找到。embedding 服務暫時失敗時，證據照樣存下，只是那些段落只能用關鍵字搜尋。

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

### 3D 素材檢查（T-404）

```bash
pnpm -F web check-assets
```

- 檢查 `frontend/web/public/models/` 裡每個 glTF 通過 Khronos 驗證器、人物不超過 3,000 個三角形，並且**每個檔案都列在 `frontend/web/src/office3d/assets/LICENSES.md`**（來源、授權、取得日期）。
- 要新增素材：先確認授權可商用（CC0 最好），把檔案放進 `public/models/`，在 `LICENSES.md` 加一節記錄來源與授權，再跑這個指令。CI 也會跑。

### 瀏覽器端到端測試（Playwright，T-315）

階段 3 的驗收：兩個分頁同步、API 停 20 秒再啟動後自動恢復。需要資料庫在跑（`make dev`）與 Python 虛擬環境；測試會自己啟動另一組 API（埠 8100）、工作程序與前端（埠 3100，正式建置，放在 `.next-e2e`），用獨立的資料庫 `<開發資料庫>_e2e`（每次重建），模型固定用模擬的，**不讀 `.env`**，所以不會用到真的金鑰，也不會動到開發資料。開著的開發伺服器（8000、3000）不受影響，但 8100 與 3100 要空著。

```bash
make e2e
```

- 本機用已安裝的 Google Chrome；CI 另外安裝 Playwright 的 Chromium。
- 一次約 1 分鐘（包含前端建置），其中一個測試會等 API 停機滿 20 秒。
- 失敗時的追蹤檔在 `frontend/web/test-results/`，用 `pnpm -F web exec playwright show-trace <檔案>` 查看。

### 3D 辦公室長時間測試（T-412）

階段 4 驗收的長時間部分：`/office` 開著，每 5 分鐘用模擬模型跑一輪，量記憶體與 FPS。環境與 `make e2e` 相同（埠 8100、3100 要空著），用本機 Chrome 與真的 GPU；**測試期間不要在同一台電腦做吃重的工作**，否則量到的 FPS 會掉。

```bash
make soak
```

- 預設 120 分鐘；`SOAK_MINUTES=40 make soak` 改長度，環境變數 `SOAK_ROUND_MINUTES`（每幾分鐘一輪，預設 5）、`SOAK_SAMPLE_SECONDS`（取樣間隔，預設 60）。
- 通過條件：沒有頁面錯誤、回收後 heap 的成長 < 50 MB、FPS 第 10 百分位 ≥ 30。每筆取樣寫在 `frontend/web/test-results/` 下的 `office-soak.json`；要留存就複製到 `logs/perf/`。
- 不在 `make e2e` 與 CI 內（沒有設定 `SOAK_MINUTES` 時跳過）。

### 真實模型測試

見第六節第 4 點。預設不執行，沒有設定金鑰時會顯示為略過。

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
| 任何程序啟動時出現 `Invalid configuration` | `.env` 不完整。例如 `MODEL_PROVIDER=nvidia` 卻缺少 `NVIDIA_API_KEY`、模型 ID 或 `MODEL_PRICES`；訊息會列出缺哪一項 |
| 工作程序日誌出現 `RateLimited` | NVIDIA 免費端點限流。程式已自動重試；經常發生時調低 `WORKER_CONCURRENCY` |
| 工作程序日誌出現 `NotFound` 或 `BadRequest` 且提到模型 | 模型 ID 寫錯。NVIDIA 要用頁面程式範例中的 ID（例如 `z-ai/glm-5.3`），不是卡片名稱 |
| 工作流程啟動了但任務一直是 READY | 工作程序沒開，或 `WORKER_COMPANY_IDS` 沒有包含這間公司，或代理被暫停 |
| 啟動工作流程回 422「project … is PROPOSED」 | 工作流程只能在 ACTIVE 專案中執行；用 `seed_echo.py` 建立的專案即為 ACTIVE |
| API 回 401 | 權杖與 `.env` 的 `API_BEARER_TOKEN` 不一致 |
| 工作程序日誌出現 `NoSimulation` | `MODEL_PROVIDER=fake` 時，只有提供模擬模型的領域（目前是 echo）能執行 |
| 工作程序日誌出現 `BudgetExceeded`、代理顯示等待預算 | 超過代理、任務、專案或公司預算。目前調高預算後**不會自動重新排入佇列**：任務管理員已有釋放受阻任務的功能，但還沒接到 API 或工作程序 |
| 軌跡 API 讀不到步驟內容（blob） | API 與工作程序的 `BLOB_STORE_DIR` 不一致 |
| `make db-check` 報告有差異 | 模型改了但沒有遷移，執行 `make migration m="說明"` 產生後檢查內容 |
