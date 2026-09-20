# 開發紀錄 06 — 階段 6 自主營運迴圈（Autonomous Loop）

- 期間：2026-09-20 起（進行中）
- 目標：**不需要人啟動 workflow**。Cycle 每天自動走 PLANNING → EXECUTING → MEASURING → REVIEWING → DONE；CEO 代理讀 CompanySnapshot、產出 CyclePlan 與 CycleReview；治理規則依 kill criteria 自動暫停專案；帳本把 `model_calls` 結算成 expense；3D 辦公室看得到 CEO 在想事情，Today's Goal 來自真實的 cycle。
- 驗收條件（`logs/3d-office/09_DEVELOPMENT_ROADMAP.md` 階段 6）：`11_MVP_ACCEPTANCE.md` 的 AC-11 ～ AC-14；連續 7 個 cycle（可加速）無人工修復（審批除外）、沒有 FAILED 的 cycle。
- 設計依據：`platform/02_COMPANY_MODEL.md`（cycle 五階段的觸發者 / 狀態變化 / 事件 / 權限 / 失敗恢復、十道防暴衝閘門、CompanySnapshot 欄位、kill criteria）、`platform/06_BUSINESS_REVENUE.md`（命令管線、Ledger、Budget / Transaction / KPI）、`platform/04_AGENT_SPEC.md`（CEO 代理）、`platform/07_PERMISSION_MODEL.md`（權限矩陣、非 LLM 治理規則）、`platform/08_MEMORY_ARCHITECTURE.md`（agent_memory、context 組裝順序與 token 上限）、`platform/11_EVENT_CATALOG.md`、`3d-office/02_AGENT_STATE_MODEL.md`（CEO 的 3D 行為）。
- 相關決策：D-001（人審批 24 小時逾時不取消）、Q-budget 預設（`company.daily_cap_usd = 10`、`max_workflows_per_cycle = 5`）。
- 使用模型：Claude Opus 5（未另行指定前）。

## 起點盤點（2026-09-20）

進階段 6 之前先盤了一次現況，免得重做已經有的東西：

**已經在的**
- `company/policy.py` 已經把 12 個公司動作與 CEO 的權限規則全部註冊好了（`instantiate_workflow` 的 `LIMITED(≤ max_workflows)`、`allocate_budget` 超過門檻轉人審、`kill_project` / `update_strategy` / `create_project` 一律人審、`pause_project` 允許 `ceo` 與 `system`——`system` 就是留給 kill criteria 自動暫停的）。**命令管線的權限那一半已經做完**，缺的是它守的處理函式。
- `company/events.py` 的事件**全部定義好了**（`CYCLE_*`、`GOAL_*`、`PROJECT_*`、`BUDGET_ALLOCATED`、`EXPENSE_RECORDED`、`REVENUE_RECORDED`、`KPI_SNAPSHOT_CREATED`），前端的 zod 契約也已經含這些型別。缺的是**發出它們的程式**。`PROJECT_PAUSED` 甚至已經帶 `trigger: human|ceo|kill_criteria`。
- `projects.kill_criteria` 有欄位，而且資料庫層的 CHECK 已經強制「核准後必須有 kill criteria」；`override_reason`、`approved_by`、`created_by_run_id` 也都在。
- `transactions` 由資料庫 trigger 強制 append-only、`idempotency_key` 唯一、`amount > 0`——帳本要的冪等保證在資料庫層就有了。
- `company/workflows.py` 的 `start_workflow` 已經接受 `role` 與 `facts`，就是 CEO 的 `instantiate_workflow` 要呼叫的那個函式。
- `runtime/approvals` 的第三種模式（standalone `request`：命令需要人核准，發起者聽 `APPROVAL_APPROVED` / `APPROVAL_REJECTED`）正是 `kill_project`、`create_project`、`update_strategy` 要用的。

**還沒有的**：`cycles`、`kpi_snapshots`、`agent_memory`、`commands_log`、`documents` 五張表；`company/` 下的 cycle、ledger、reporting/snapshot、commands、governance、`agents/ceo`；`/api/cycles`、`/api/projects`、`/api/budgets`、`/api/companies/{id}/snapshot` 四組 API；前端的 Cycle 頁。

**三個現成的坑**（開工前先記下來，做到的時候處理）
1. `tasks.cycle_id`、`workflow_runs.cycle_id`、`events.cycle_id` 三個欄位都在，但**沒有外鍵**，因為 `cycles` 表當初被延後了。T-601 的 migration 要一起把外鍵補上，否則 P-10 的稽核鏈無法強制。
2. `runtime/cost/guard.py` 自己註明：**`cycle` 週期的預算目前當成「日」處理**，因為沒有 cycles 表。T-601 之後要改掉，不然 AC-13 量到的是錯的視窗。
3. `RUNBOOK.md` 記著：**加了預算不會自動把 `BLOCKED_BUDGET` 的任務放回佇列**——TaskManager 有釋放的函式，但沒有人呼叫。AC-13 的「加預算後恢復」要靠這個。

## 開發計畫

任務來自 `3d-office/10_TASK_BREAKDOWN.md`。CEO 屬於 `company` 層（不是 domain），提示與輸出 schema 放在 `company/agents/ceo/`，`runtime` 不知道它的內容；CompanySnapshot 裡屬於新聞室的部分（`candidates`）必須由領域**掛鉤**提供，不能 import（import-linter 擋著 `company → domains`）。

| 任務 | 內容 | 依賴 | 狀態 |
|---|---|---|---|
| T-601 | Cycle FSM + stage runner + deadlines（`cycles` 表、五階段、逾時強制推進） | T-212、T-104 | ✅ |
| T-602 | Ledger：`model_calls` → expense 結算、餘額與已花額度 | T-209、T-103 | ✅ |
| T-603 | Reporting + CompanySnapshot（token 上限）、`kpi_snapshots` | T-602、T-516 | ⬜ |
| T-604 | 命令管線（`submit_command`：instantiate_workflow、pause/kill、allocate_budget、update_strategy）、`commands_log` | T-205 | ⬜ |
| T-609 | `agent_memory`（最近 N 次執行的摘要進 context，TTL 30 天、每個代理 ≤ 50 列） | T-211 | ⬜ |
| T-605 | CEO 代理（`CyclePlan` / `CycleReview` schema、validators、提示、模擬回應） | T-211、T-603、T-604 | ⬜ |
| T-606 | 治理：kill criteria 自動暫停、kill 提案 → 人審、代理連續失敗 N 次自動暫停 | T-603、T-604 | ⬜ |
| T-607 | Fallback plan + 每日摘要文件（`documents`） | T-601、T-605 | ⬜ |
| T-608 | Cycle 頁面 + Today's Goal 改成來自 `cycles.plan` | T-309、T-601 | ⬜ |
| T-610 | 7 個 cycle 的 soak（加速時鐘） | 全部 | ⬜ |

階段 5 帶過來的缺口，排進這一期：

| 編號 | 內容 | 併入 |
|---|---|---|
| 補-1 | `model_calls` 連到 workflow（每篇文章的總成本，AC-S8）——同時也是 T-602 按專案結算的前提 | T-602 ✅ |
| 補-2 | 人可從收件匣重新啟動失敗的 workflow（AC-9 的最後一句） | T-604（一個命令） |
| 補-3 | 加預算後自動放回 `BLOCKED_BUDGET` 的任務（AC-13、P-5） | T-604 |
| 補-4 | CEO 代理讓辦公室有第 6 個 avatar（AC-1） | T-605 |
| 補-5 | AC-S4 的崩潰恢復改測寫手；AC-S6 的靜態掃描；AC-S7 的 WS p95 | T-610 |

建議順序（先有資料與帳，再有命令，最後才讓 CEO 動手）：
T-601 → T-602 → T-603 → T-604 → T-609 → T-605 → T-606 → T-607 → T-608 → T-610。
理由：CEO 的輸入是 CompanySnapshot（T-603），輸出是命令（T-604），兩邊都到齊之前寫 CEO 只能寫假的；T-609 先做，CEO 的 context 才有「最近幾次做了什麼」可放。

---

## T-601 · Cycle FSM + stage runner + deadlines

### 做了什麼
- **`cycles` 表（migration 0022）**：`seq`（每家公司從 1 開始，唯一）、`stage`、`plan`、`review`、`started_at`、`stage_deadline`、`ended_at`。兩個資料庫層的約束：`seq >= 1`、**`(stage = 'DONE') = (ended_at IS NOT NULL)`**——「結束了」與「有結束時間」在資料庫裡是同一件事，不靠程式自律。
- **補上兩個當初延後的外鍵**：`tasks.cycle_id`、`workflow_runs.cycle_id` → `cycles.id`。稽核鏈 cycle → workflow → task 從此由資料庫保證（P-10）。`events.cycle_id` 刻意不加——那張表的其他 id 欄位（`run_id`、`workflow_run_id`）也都沒有，事件記錄本來就要比它指到的東西活得久。
- **`company/cycle.py`**：
  - `CYCLE_FSM`：PLANNING → EXECUTING → MEASURING → REVIEWING → DONE，**單向、無分支**。失敗不會讓 cycle 倒退或轉彎（platform/02：連 review 失敗，cycle 仍然走到 DONE，下一份 snapshot 會說「上次的 review 不見了」）。每一步都經過 `state_transitions` 稽核。
  - `CycleRunner`：`start()` 開新的一輪（前一輪還沒 DONE 就拒絕——一家公司一次只跑一輪，卡住的 cycle 要被看見，不能被無聲地套圈）；`tick()` 盡可能往前推，兩者都用 `FOR UPDATE` 鎖住該列，所以兩個 worker 不會把同一輪推兩次。
  - **兩個擴充點**：`when_entering(stage, hook)`（進入某階段時要做的事——T-605 的 CEO 規劃、T-602 的帳本結算都掛這裡，在推進的那個交易裡執行）與 `finishes_when(stage, check)`（這個階段的工作做完了沒）。
  - **時限**：每個階段都有 deadline，到了就推進，還沒做完的任務寫進 `CYCLE_STAGE_TIMEOUT`（防暴衝閘門 6）。預設 `{PLANNING: 60, EXECUTING: 780, MEASURING: 60, REVIEWING: 60}` 分鐘——從 06:00 開始算，工作到 20:00，正是 platform/02 寫的執行時限。公司可用政策 `company.cycle_stage_minutes` 縮短；看不懂的值只記警告、沿用預設，不讓一個打錯的設定擋住整家公司。
  - 逾時**不取消**任務：還握著租約的工作會跑進下一階段自己結束，事件負責讓這件事被看見。

### 一個設計決定：「沒有檢查」等於「做完了」
本來寫成「沒註冊完成檢查的階段就等到 deadline」，結果空公司要坐滿三小時才跑完一輪。改成**沒有檢查就是一進去就完成**——因為那個階段該做的事已經在 `on_enter` 掛鉤裡、在同一個交易裡做完了。需要等別人（PLANNING 等 CEO 的任務、EXECUTING 等當天的 workflow）的階段才註冊檢查，被擋在那裡直到檢查通過或 deadline 到。於是**沒有 CEO 的空公司照樣一輪一輪地走完**，這也正是 T-607 fallback 的行為。

### 怎麼推進：排程 + 維護迴圈，不是事件
`runtime/worker.py` 自己註明「還沒有事件分派器」，所以階段推進沒有非同步觸發來源。兩個來源：
1. **每日排程** `company.cycle_start`（cron `0 6 * * *`）→ `CycleRunner.start`。`ensure_cycle_schedule()` 給公司裝上這個排程，**AC-11 的「不用人介入」從這裡開始**。排程當天還沒跑完也不算錯，記一筆警告跳過，維持節奏。
2. **worker 的維護迴圈**：`Worker` 新增 `maintenance_jobs`（`(名稱, 工作)` 清單，由 `app.py` 註冊）。runtime 不需要知道什麼是 cycle——它只是定期跑別人給的工作。每家公司在自己的 savepoint 裡推進，一家公司的掛鉤爆炸不會擋住其他家。

### 順手修掉一個舊的將就
`runtime/cost/guard.py` 一直註明「`cycle` 週期的預算暫時當成『日』處理，因為沒有 cycles 表」。現在改成**真的用該公司未結束 cycle 的 `started_at` 當視窗起點**：一筆在 cycle 開始前、但同一天發生的呼叫，算在上一輪，不會吃掉這一輪的預算。沒有進行中的 cycle 時退回日視窗。兩個測試蓋住這兩條路。

### 驗證
- `pytest tests/company/test_cycle.py`：21 個測試。涵蓋開始、拒絕重疊、排程跳過、空公司一次走完、工作做完提前結束、逾時推進並記下留下什麼（任務不被取消）、每一步都有稽核且 DONE 之後不能再動、政策縮短階段、掛鉤執行順序、掛鉤失敗整個 tick 回滾、維護工作涵蓋所有啟用中的公司（封存的不動）、一家壞掉不影響其他家、可限定本 worker 的公司、**連續 7 輪**（AC-14 的縮小版）、以及四個資料庫約束（重複 seq、DONE 必須有結束時間、公司必須存在、任務的 cycle 必須存在）。
- `pytest tests/runtime/test_cost_guard.py`：14 個（新增 2 個 cycle 視窗）。
- 後端 894 個測試通過；`ruff`、`lint-imports`、`db-check`、`gen-schema-check`、`gen-api-check` 通過。

### 還沒接上的
`ensure_cycle_schedule` 還沒有人呼叫——示範公司要不要自動開始每天跑，等 CEO 代理（T-605）在了再接，否則現在只會每天空轉一輪。

---

## T-602 · Ledger：把用量變成錢

### 先補上「補-1」：model_calls 連到 workflow 與 cycle
階段 5 留下的缺口，也是這個任務的前提。一筆模型呼叫本來只知道自己的任務，於是「這篇文章總共花了多少」（AC-S8）與「這一輪花了多少」都答不出來。migration 0023 給 `model_calls` 加上 `workflow_run_id` 與 `cycle_id`（都有外鍵與索引），從 `CallContext` 一路傳下來：代理執行器從任務身上讀、工具呼叫也帶（`ToolContext` 跟著加 `cycle_id`），所以工具裡做的 embedding 也算得到正確的一輪。舊資料維持 NULL——它們產生時還沒有東西可以指。

### 帳本
- **`company/ledger.py` 是唯一會寫 `transactions` 的模組**（platform/06）。錢的方向在 `kind`，金額永遠是正的；收入與支出都是同一張表的視圖，所以「餘額是多少」只有一個答案，沒有人能從別的地方數出不同的數字。
- **結算一輪**：模型呼叫發生時就記在 `model_calls`——那是**電錶**。cycle 進入 MEASURING 時讀錶，變成錢：**每個專案一筆支出**，金額是該專案在這一輪的呼叫總和。兩件事讓它可以重複執行（維護迴圈一定會重複執行）：
  1. 冪等鍵是 `cycle:<id>:project:<id>:model_cost`，資料庫有唯一索引，第二次結算什麼都不寫；
  2. 金額是**從呼叫重新算出來的**，不是往累計值上加。
- **為什麼不是一筆呼叫一筆帳？** 因為一次呼叫不是一個決定。一天幾百筆會把帳本埋掉，而 P-7 要的只是「這一輪的支出 = 這一輪的模型呼叫」——一輪一專案一筆剛好滿足，而且透過 `ref_type`/`ref_id` 仍能回溯到個別呼叫。
- 沒有專案的呼叫（CEO 規劃整輪）結算成一筆公司層級的支出；失敗的呼叫與免費端點的 0 元呼叫不產生任何一筆帳。
- 讀的那一面：`balance()`（可指定時間點）、`spent()`（可限定專案 / 期間 / 類別）、`cost_of(workflow_run_id)`（**一篇文章的總成本**，直接讀電錶，因為 workflow 比 cycle 細）、`metered(cycle_id)`（這一輪電錶上的數字）。
- 掛在 cycle 的 MEASURING 進入掛鉤上——**CycleRunner 不知道帳本存在**，它只是進入一個階段。

### 驗證
- `pytest tests/company/test_ledger.py`：19 個測試。重點是 **P-7 用兩邊獨立計算再比對**（電錶 `metered` = 帳本 `spent` = 結算回傳的總額），以及 **AC-S8**（一篇文章的成本是它那條 workflow 的總和，不是那一天的）。其餘涵蓋一輪一專案一筆、重複結算不重複寫、結算後才發生的同輪呼叫會被指出（帳與電錶不再相等，測試明講這件事）、失敗與免費呼叫不記帳、無專案的呼叫記在公司層、結算日期用 cycle 的結束時間、不會撈到別輪的呼叫、餘額與支出的各種切法、同一把冪等鍵只記一次、金額必須大於零、每一筆都發事件（`EXPENSE_RECORDED` / `REVENUE_RECORDED`）、以及**資料庫的 append-only trigger 確實擋下修改**。
- `pytest tests/company/test_cycle.py`：新增一個端到端測試——cycle 走過 MEASURING 時自己把當天的呼叫結算成一筆支出。
- 後端 914 個測試通過；`ruff`、`lint-imports`、`db-check`、`gen-schema-check`、`gen-api-check` 通過。

### 還沒做的
工具的花費還沒進帳本（工具目前不記錄自己的成本）；`settle_cycle` 已經按類別分組，等工具開始記錄就能一起結算。

---

## 提交紀錄

| 提交 | 日期 | 內容 | 持續整合 |
|---|---|---|---|
| `e6d004c` | 2026-09-20 | T-601 cycle 狀態機、stage runner、時限；cycle 預算視窗 | ✅ 執行編號 `35489042649`（python 2 分 33 秒、e2e 7 分 31 秒、web 1 分 13 秒） |
