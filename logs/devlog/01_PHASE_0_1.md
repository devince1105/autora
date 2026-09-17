# 開發紀錄 01 — 階段 0 架構設計與階段 1 公司與代理核心

- 期間：2026-09-16 ~ 2026-09-17
- 提交：`c894a3b`（`main` 分支的初始提交，135 個檔案，新增 15,441 行）
- 儲存庫：https://github.com/vince115/autora（私人）
- 持續整合：執行編號 `35213310091`，全部通過
- 使用模型：設計階段為 Claude Fable 5.1；階段 1 實作為 Claude Opus 5

---

## 1. 時間線

| 日期 | 事件 |
|---|---|
| 09-16 | 階段 0 第一輪：平台架構設計（公司模型、執行環境、權限、記憶、模型閘道、資料庫、事件、開發路線圖） |
| 09-16 | 階段 0 第二輪：3D AI 辦公室與即時同步架構，12 份文件寫入 `logs/3d-office/` |
| 09-16 | 第一輪的 16 份平台文件寫入 `logs/platform/`，並與第二輪的決定對齊 |
| 09-16 | P0 決策拍板（`logs/DECISIONS.md`）：D-001 發布審批先由人核准、D-002 主語言為繁體中文（zh-TW）、D-003 網路搜尋使用 Tavily |
| 09-16 | T-101 單一儲存庫（monorepo）建置；T-102 設定檔、資料庫連線與 Alembic 遷移 |
| 09-16 | 模型評估：規格明確的實作使用 Opus 5；涉及併發與核心迴圈的任務（T-202、T-211、T-301/302）建議使用 Fable 5.1。切換為 Opus 5 |
| 09-16 ~ 17 | T-103 ~ T-105：資料模型、通用狀態機、事件結構 |
| 09-17 | T-106 ~ T-110：事件寄件匣、代理活動狀態、型別產生器、匯入邊界、REST API；階段 1 驗收通過 |
| 09-17 | GitHub：建立初始提交、排除帳號與權杖權限問題、推送、首次持續整合全部通過；儲存庫改為私人 |

---

## 2. 階段 0 — 架構設計產出

| 位置 | 內容 |
|---|---|
| `logs/platform/01–16` | 平台層：公司如何運作 |
| `logs/3d-office/01–12` | 3D 辦公室、即時同步、代理狀態；**實際執行依據的路線圖（09）與任務拆解（10）** |
| `logs/DECISIONS.md` | D-001 ~ D-003，以及尚未決定的 P0 項目與暫用預設值 |
| `logs/README.md` | 文件索引，以及各主題以哪份文件為準 |

設計階段修正的主要架構矛盾（詳見 `platform/01 §7`）：
- 執行長代理（CEO）不控制流程，流程由週期狀態機控制；
- 需要判斷的工作才交給代理，其餘是確定性服務；
- 財務代理不可寫入財務資料表；
- 事實查核分為三層；
- 代理本身不保存狀態；
- 工作流程圖為宣告式範本；
- 向量檢索（pgvector）只用在三個有明確效益的地方。

3D 辦公室的核心原則：Three.js 是視覺化層，不是執行環境。3D 層只讀取狀態儲存、不做任何網路存取、不推測代理狀態。

---

## 3. 階段 1 — 各任務實作紀錄

### T-101 · 單一儲存庫建置
- 建立 `apps/{web,api,worker}`、`packages/autora`（唯一的 Python 套件）、`packages/event-schema`、`infra/`（Docker Compose 與 3 個 Dockerfile）、Makefile、持續整合設定。
- Next.js 採用 **16.3.5**。規格原寫 15 版，但 15.5.4 已被標示為不建議使用。
- 驗證：`make setup`、`make dev`、`make test`、`docker compose build api worker`。

### T-102 · 設定檔與資料庫連線
- `infra/settings.py`：缺少必要設定或設定互相矛盾時（例如 `MODEL_PROVIDER=anthropic` 卻沒有金鑰），啟動即失敗並顯示可讀的錯誤訊息；機密值不會出現在日誌中。
- `db/session.py`：非同步資料庫引擎與 `session_scope()`。Alembic 以非同步方式執行，連線字串只從設定檔取得。
- 測試一律使用 `autora_test` 資料庫：自動建立，每次測試開始時重建結構並執行 `alembic upgrade head`，不會動到開發用資料庫。
- 修正（我造成的錯誤）：
  - 尋找 `.env` 時目錄層數算錯（`parents[3]` 應為 `[4]`）；
  - 測試框架中，資料庫引擎與測試使用不同的事件迴圈。

### T-103 · 公司與代理資料模型（遷移 0002）
- 資料表：`companies`、`company_goals`、`company_policies`、`agents`、`projects`、`budgets`、`transactions`。
- 由資料庫本身保證的規則：
  - `transactions` 只能新增：觸發器拒絕任何修改與刪除，更正必須新增一筆沖銷紀錄；
  - `projects`：專案一旦核准，必須帶有結構化的終止條件（`kill_criteria`）；
  - `transactions.idempotency_key` 唯一：重複寫入時回傳既有紀錄，重試不會重複入帳；
  - `budgets`：同一範圍、同一週期只能有一筆預算（包含公司層級，也就是沒有指定專案的情況）。
- 主鍵使用 UUIDv7（`infra/ids.py`；Python 3.12 沒有內建）。
- 修正（我造成的錯誤）：
  - 唯一約束的命名規則只取第一個欄位，導致 `(company_id, display_name)` 被命名為 `uq_agents_company_id`；
  - `agents` 與 `budgets` 有多餘的索引；
  - `alembic.ini` 使用相對路徑，只能在 `packages/autora` 目錄下執行。

### T-104 · 通用狀態機
- `runtime/fsm.py`：宣告狀態機時即檢查未知狀態、自我轉換，以及掛在不存在的轉換上的守衛條件。
- `transition()` 依序驗證轉換、執行守衛條件、更新實體，並寫入 `state_transitions`（只能新增）。任何一步失敗時不寫入任何資料。

### T-105 · 事件結構
- 統一的事件外框加上負載登錄表，以「事件類型 + 結構版本」為索引，共 59 種事件。
- 與設計文件不同之處（已回寫 `3d-office/03 §8`）：
  - 事件目錄**依所屬層級分開定義**：執行環境與公司層各自定義，新聞編輯室的事件於階段 5 由該領域註冊；
  - 新增 `AGENT_IDLE`；
  - `handoff` 改為清單；
  - `unlocks` 帶上 `required_role`；
  - `event_id` 使用 UUIDv7。

### T-106 · 事件資料表與寄件匣（遷移 0003）
- `emit()` 與狀態變更在同一個交易中寫入；`NOTIFY autora_events` 只在交易提交後送出。
- **關鍵決策**：`seq` 在寫入時分配，兩個並行交易可能不依 `seq` 順序提交。以 `seq > last_seq` 追蹤事件的讀取端會因此永久漏掉較小的 `seq`。解法是 `emit()` 取得「每間公司一把、交易結束即釋放」的諮詢鎖，讓同一公司的提交順序等於 `seq` 順序。已有測試證明第二個寫入者會等待第一個提交。
- `events` 資料表只能新增，唯一允許的修改是設定一次 `processed_at`（由觸發器保證）。

### T-107 · 代理活動狀態
- 活動狀態**由事件負載的型別推導**，呼叫端不能另外指定狀態，因此資料列與事件不可能互相矛盾。
- 只拒絕會遺失資訊的轉換：暫停中未恢復就繼續工作、沒有失敗卻確認失敗、沒有等待卻解除等待。
- 「已完成」顯示期滿後視為「閒置」，這是投影規則 `effective_state()`，不另外寫事件。
- 修正（我造成的錯誤）：檢查順序寫錯，導致「已經暫停」這個錯誤永遠不會觸發。

### T-108 · 型別產生流程
- 自行撰寫產生器 `runtime/events/codegen.py`，由 `scripts/gen_event_schema.py` 執行：讀取 pydantic 輸出的 JSON Schema，產生 zod v4 驗證器、TypeScript 型別與 `events.catalog.json`。
- 理由：pydantic 只使用很小一部分的 JSON Schema，自行產生的輸出可預期、容易比對差異；遇到不支援的結構時直接報錯，不會產生錯誤的驗證器。
- 前端容錯：伺服器新增的欄位會被忽略；未知的事件類型會解析失敗，但不會拋出例外。
- `make gen-schema-check` 與 pytest 都會檢查產生的檔案是否過期。

### T-109 · 匯入邊界
- `.importlinter`：層級為 `app > domains|realtime > company > runtime > db > infra`；runtime、company、db 不得依賴 fastapi 或 starlette。
- `apps/web/eslint.config.mjs`：
  - `office3d` 不得匯入 api、realtime、features、app，也不得使用 fetch、WebSocket；
  - `realtime` 不得依賴介面層；
  - `features` 只能透過 `OfficeCanvas` 使用 3D 辦公室。
- `src/boundaries.test.ts` 以 ESLint 程式介面證明規則確實生效；刻意加入一個 `runtime → company` 的匯入時，import-linter 回報違規。
- 修正（我造成的錯誤）：ESLint 規則中直接排除 `@/office3d` 目錄，導致 `OfficeCanvas` 的例外失效。依 gitignore 的比對規則，父目錄被排除後，其中的檔案無法再被納入。

### T-110 · 最小 REST API
- `apps/api/autora_api/` 提供：
  - `GET/POST /api/companies`、`GET /api/companies/{id}`；
  - `GET /api/companies/{id}/agents`（含目前活動狀態）；
  - `GET /api/events`（回傳 `{items, next_after, has_more}`）。
- 使用 Bearer 權杖驗證（MVP 只有單一操作者）。`AUTORA_ENV=prod` 時拒絕使用預設權杖。所有錯誤回應皆為 problem+json 格式。
- 以真實 uvicorn 伺服器實測：健康檢查回 200、未帶權杖回 401、建立公司回 201 並產生 `seq` 為 1 的 `COMPANY_CREATED`、重複建立回 409。

### 階段 1 驗收
- `make phase1-acceptance`：Python 端在 Postgres 中讓一個代理依序經過全部 8 種活動狀態（每次恰好產生一個 AGENT_* 事件、`seq` 遞增、資料列與最後一個事件一致），並輸出這些真實事件；TypeScript 端以產生的 zod 驗證器全部解析成功，且負載型別能依事件類型正確收窄。

---

## 4. 最終驗證結果

| 檢查項目 | 本機 | 持續整合（執行編號 35213310091） |
|---|---|---|
| ruff 與 import-linter | ✅ 2 條邊界規則全數遵守 | ✅ |
| 資料庫遷移與 `alembic check` | ✅ 模型與遷移一致 | ✅（使用全新的 Postgres 容器） |
| `gen-schema-check` | ✅ | ✅ |
| Python 測試 | ✅ 123 個通過（`AUTORA_REQUIRE_DB=1`） | ✅ 123 個通過，0 個略過 |
| TypeScript 測試 | ✅ event-schema 8 個、web 9 個 | ✅ |
| 前端 lint 與型別檢查 | ✅ | ✅ |

規模：Python 約 5,500 行（`autora` 套件約 3,300 行、測試約 1,800 行）。

---

## 5. 環境與操作紀錄

| 項目 | 狀況與處理 |
|---|---|
| Node | 終端機預設的 nvm Node 12 會讓 pnpm 無法執行；改用 `/opt/homebrew/bin` 的 Node 22 與 pnpm 9.15.9，並加入 `.nvmrc` |
| Python | 使用 `/Library/Frameworks/Python.framework/Versions/3.12`（`make PYTHON=... setup`） |
| Postgres | 5432 埠已被 `helloworld-postgres` 佔用；Autora 對外使用 **5434**（映像 `pgvector/pgvector:pg16`，pgvector 0.8.6） |
| 開發資料庫殘留資料 | 實測時留下公司 `smoke-1789637509`；因事件只能新增而無法刪除，需要乾淨的資料庫時請重建資料卷 |
| GitHub 命令列工具 | 版本為 2.4.0（2021 年），不支援 `--git-protocol` 與多帳號；建議執行 `brew upgrade gh` |
| GitHub 帳號 | 原本登入 `devince1105`，推送被拒（403）；改為登入 `vince115`（取代原帳號） |
| 權杖權限 | 推送包含 `.github/workflows/` 的檔案需要 `workflow` 權限，已執行 `gh auth refresh --scopes workflow` |
| 儲存庫可見性 | 首次推送時為公開，2026-09-17 改為**私人** |
| 持續整合警告 | `actions/checkout@v4`、`setup-node@v4`、`pnpm/action-setup@v4` 以已停止支援的 Node 20 為目標，目前被強制以 Node 24 執行；可升級版本，不影響結果 |

---

## 6. 尚未決定與待辦事項

- 尚未決定的 P0 項目（目前使用暫用預設值）：向量嵌入的供應商、每日預算規模、發布通路（見 `DECISIONS.md`）。
- 升級持續整合使用的 GitHub Actions 版本（Node 20 停止支援警告）。
- 升級 GitHub 命令列工具；如有需要，重新加回 `devince1105` 帳號。

## 7. 下一步

階段 2：執行環境、任務與事件（T-201 ~ T-215），紀錄於 `02_PHASE_2.md`。
