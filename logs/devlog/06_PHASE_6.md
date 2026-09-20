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
| T-600 | **組織模型（v2）**：`business_units`、`departments`、`roles`、`products` 四張表；代理歸屬部門與職務；事業歸屬；種子資料與 API | `ARCHITECTURE_V2.md` | 🚧 |
| T-601 | Cycle FSM + stage runner + deadlines（`cycles` 表、五階段、逾時強制推進） | T-212、T-104 | ✅ |
| T-602 | Ledger：`model_calls` → expense 結算、餘額與已花額度 | T-209、T-103 | ✅ |
| T-603 | Reporting + CompanySnapshot（token 上限）、`kpi_snapshots` | T-602、T-516 | ✅ |
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

建議順序（2026-09-20 依 `ARCHITECTURE_V2.md` 修訂，插入 T-600、T-605 拆成兩個）：
T-601 → T-602 → **T-600** → T-603 → T-604 → T-609 → T-605a（CEO）→ T-605b（總編輯）→ T-606 → T-607 → T-608 → T-610。
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

## T-600 · 組織模型（第一批：表、服務、即時契約）

依 `ARCHITECTURE_V2.md` 實作。這一批做「組織存在，而且代理住在裡面」，事業歸屬的欄位一起建好但還沒有人填。

### 四張表（migration 0024）
`business_units`、`departments`、`roles`、`products`。每張表只回答一個問題：做哪門生意 / 用哪類職能 / 職務是什麼 / 賣什麼給市場。

兩個設計照 v2 執行：
- **`departments.business_unit_id` 可空**——同一張表同時容納公司層級共用職能（Executive、Finance）與事業自有職能（Newsroom 屬於 AI Media）。
- **團隊不是第五張表**——`parent_department_id` 自我參照，深度由資料決定。Newsroom 底下的 Research 就是子部門。

**`agents.role` 一個字都沒動**。新增的 `role_id` 只是把那個字串解析到組織位置；`department_id` 說它住哪。兩個都可空，所以在組織出現之前就存在的代理照常工作，只是不在組織圖上。

事業歸屬欄位（全部可空）：`projects.business_unit_id`、`budgets.business_unit_id`、`transactions.business_unit_id` 與 `product_id`、`company_goals.business_unit_id`。`budgets` 的唯一鍵跟著從 `(company, project, period)` 變成 `(company, business_unit, project, period)`——預算層級從三層變四層。

### `company/organization.py`：跨列的規則住在這裡
資料庫約束管不到的事，由這個模組負責，而且每一條都有測試：
- 子部門**繼承**母部門的事業，而且不能牴觸（Newsroom 底下的團隊就是替 AI Media 工作，傳別的事業進來會被拒絕）；
- key 在公司內唯一，跨公司可重複；
- 部門不能是自己的母部門（這條由資料庫 CHECK 擋）；
- key 的格式與 `agents.role` 相同（小寫、**不能有點**——model router 的路由鍵是 `role.capability`，多一個點就是設定錯誤）；
- **代理的三種說法必須一致**：執行層認的 `role` 字串、它的職務、它的部門。`hire_agent(position=...)` 與 `assign()` 是唯二能設定它們的地方，而且一起設定。角色字串與職務不符會被拒絕。

`org_chart()` 把整張組織圖組起來：共用職能、各事業與其部門與產品、以及**沒有歸屬的代理**（不是丟掉，是照實列出來）。四個查詢，不用遞迴 SQL——組織圖是幾十列，在 Python 組裝反而看得懂。

### 即時契約：一個必需品
`ARCHITECTURE_V2` §16.1 指出的那件事做掉了：reducer 重建代理的唯一來源是 `AGENT_CREATED`，所以
1. `AGENT_CREATED` 現在**帶部門**（`department_id`、`department_key`）；
2. 新增 **`AGENT_ASSIGNED`** 事件——代理調部門時發出。沒有它，畫面上的 avatar 永遠不會換房間，只會在下次整份快照重載時跳過去。

Python 與 TypeScript 兩個 reducer 同步改，投影契約 fixture 重新產生，契約測試通過。另外新增三個組織事件：`BUSINESS_UNIT_CREATED`、`DEPARTMENT_CREATED`、`PRODUCT_CREATED`。

### 驗證
- `pytest tests/company/test_organization.py`：26 個測試。涵蓋兩種部門、團隊繼承事業、跨事業的團隊被拒、key 的唯一性與格式、職務與部門的歸屬、**雇用時三種說法一致**（不一致就拒絕）、沒有職務也能雇用（向後相容）、`AGENT_CREATED` 帶部門、調動同時改三者並發事件、調到同一個職務不發事件、產品歸事業、組織圖的完整形狀（含未歸屬與已退休的代理）。
- 後端 940 個測試通過；web 254 個通過；事件契約 8 個通過；`ruff`、`lint-imports`、`db-check`、`gen-schema-check`、`gen-api-check` 通過。

### 這一批還沒做
種子資料還沒建立組織（示範公司目前仍然是 6 個沒有部門的代理）、`/api/org` 還沒開、3D 辦公室還沒改成可進入的部門。下一批做。

### 第二批：種子資料建出組織、`/api/companies/{id}/org`

**新聞室自己宣告它的位置**（`domains/newsroom/organization.py`）。這是分層的自然結果：`domains` 可以 import `company`，所以新聞室直接呼叫 Core 的 `add_business_unit` / `add_department` / `add_role` / `add_product` 把自己安裝進公司，Core 不需要知道有新聞室這回事。建出來的形狀：

```
Company
  Executive                    （不屬於任何事業：它決定公司要做哪些事業）→ ceo（職務已定義，還沒有人）
  AI Media                     事業（ACTIVE，帶 kill criteria）
    Newsroom                   部門 → editor_in_chief（職務已定義，還沒有人，T-605b）
      Research                 團隊 → researcher、analyst
      Writing                  團隊 → writer
      Editing                  團隊 → editor（= Copy Editor）
      Audience                 團隊 → marketing
    Daily English World        產品（LIVE，`/news/zh-TW`）
```

**兩把空椅子是誠實的狀態**：`ceo` 與 `editor_in_chief` 的職務定義好了但沒有人擔任——今天仍然是人去啟動整條線，沒有誰在選題。它們的代理分別是 T-605a 與 T-605b。

`staff_newsroom()` 改成**先建組織、再把代理雇進職務**（`hire_agent(position=...)`），所以每個代理一出生就有部門與職務的預設值，不再是一個裸的角色字串。仍然依 key 冪等：種子跑兩次只會有一個組織。示範專案也掛到 AI Media 底下（舊資料會被補上）。

`bootstrap_executive()` 放在 Core：一間公司至少要有一個不屬於任何事業的部門——決定要做哪些事業的那個。

**`GET /api/companies/{id}/org`** 一次回傳整張組織圖（共用職能、各事業與其部門 / 團隊 / 職務 / 人、產品、未歸屬的代理、總人數）。每個職務帶 `held_by`，所以「這張椅子沒有人坐」在 API 層就看得出來。`AgentOut` 也跟著帶 `department_id` / `department_key`。

**驗證**：`pytest tests/api/test_org_api.py` 8 個測試（公司職能與事業分開、新聞室底下的四個團隊、人數只算一次、職務說得出誰擔任、代理名冊也帶部門、沒有組織的公司也能回答、未知公司 404、種子跑兩次只有一個組織）。後端 948 個測試通過；web 254、事件契約 8 通過；`ruff`、`typecheck`、`lint-imports`、`db-check`、`gen-schema-check`、`gen-api-check` 通過。

**順手修掉自己測試裡的一個洞**：`test_organization.py` 有兩個查事件的斷言沒有篩公司。單獨跑看不出來，但新的 API 測試會 commit 資料，一跑全套就撞上。已補上公司條件——這種測試在單獨跑時會騙人。

**這一批還沒做**：3D 辦公室還沒改成可進入的部門（`layout.ts` 的 `SLOTS`、`palette.ts` 的封閉 union、導覽、相機、信差路徑、2D 備援都要動，見 `ARCHITECTURE_V2.md` §14.7），留給第三批。

---

## T-603 · Reporting（第一批：KPI 與領域掛鉤）

### 核心只數錢，領域數自己的東西
這是 `ARCHITECTURE_V2_1.md` §9 指出的那個真耦合的修法。原本 `company/reporting_min.py` 直接數 `ARTICLE_PUBLISHED` 事件，欄位還叫 `published_today`——**import-linter 攔不到它，因為那是一個字串**。

現在：領域註冊一個 KPI 掛鉤，公司層只存數字。

- 公司層自己的指標**不帶前綴**：`cost_usd`、`model_cost_usd`、`revenue_usd`、`profit_usd`、`model_calls`。
- 領域的指標**帶自己的名字**：`newsroom.published_articles`、`newsroom.views`、`newsroom.cost_per_published_article`。
- 於是**讀一個指標就知道是誰算的**，而且刪掉一個領域只會少掉它的指標，報表其他部分照常成立。
- 一個掛鉤爆炸只會失去它自己的數字（記在 log），不會讓整份報表消失。

**掛鉤會拿到公司層剛算好的數字**（第三個參數）。理由是像「每篇文章成本」這種比率**一邊是領域的單位、一邊是公司的錢**，兩邊必須來自同一次量測，不能各查一次然後互相矛盾。AI Media 的 kill criteria 正是寫在 `cost_per_published_article` 上，所以它只能有一個意思。

`reporting_min` 的 `published_today` 欄位改成通用的 `domain_metrics`；儀表板那塊磚改讀 `newsroom.published_articles`——**「知道有新聞室」這件事搬到 UI，不再留在 Core**。

### `kpi_snapshots`（migration 0025）
每輪量測三種範圍：公司、每個事業、每個進行中的專案。唯一鍵是 `(cycle, scope, business_unit, project)` 且 NULL 視為相等，所以 **MEASURING 重跑會就地更新，不會寫出第二份**。掛在 cycle 的 MEASURING，**排在帳本之後**——先結算，再量測。

### 一個順手修掉的錯誤定義
修訂數（`revisions_requested`）本來想用 `articles.revision_count` 搭配 `updated_at` 篩視窗。那是錯的：計數器說的是「這篇文章總共被退回幾次」，而 `updated_at` 會因為無關的原因跳動，**兩者都答不出「什麼時候被退回」**。改成數 `ARTICLE_REVISION_REQUESTED` 事件——事件知道時間。

### 驗證
- `pytest tests/company/test_reporting.py`：17 個（公司層只數錢、結算過的模型成本不重複計、視窗外的不算、事業透過專案量測、領域指標帶前綴、掛鉤拿得到公司數字、壞掉的掛鉤只失去自己、註冊一次、**沒有任何領域也能產出完整報表**、三種範圍、重跑就地更新、事件只發一次、結束的 cycle 量到結束時間、KILLED 的專案不再量測、讀取最新與歷史）。
- `pytest tests/newsroom/test_kpis.py`：6 個（發布數與讀者數、比率與公司成本同源、沒發布就不報每篇成本（回 0 或無限大都會被讀成新聞）、別輪的文章不算、**另一個事業拿不到新聞室的數字**、專案只看自己的題材）。
- 後端 971 個測試通過；web 254、事件契約 8 通過；`ruff`、`typecheck`、`lint-imports`、`db-check`、`gen-schema-check`、`gen-api-check` 通過。

### 這一批還沒做
CompanySnapshot（CEO 的輸入）與它的 token 上限、`GET /api/companies/{id}/snapshot`，下一批做。

### 第二批：CompanySnapshot

**CEO 不查資料庫。** 它每輪只拿到一份 JSON，**不在裡面的東西就影響不了決定**。所以這份文件的形狀就是決策的形狀。

**以事業組合為主軸**（依 v2）：portfolio 是脊椎，因為這份文件存在的理由就是「這門生意要不要繼續投錢、那門要不要暫停、要不要試新的」。不屬於任何事業的工作（平台、探索）放在 portfolio **旁邊**，不是塞進去。

欄位：`period`、`capital`（餘額、日上限、今日已花）、`goals`（含 `trend_7d`）、`portfolio`（每門生意的 KPI、趨勢、kill criteria、產品、專案）、`company_work`、`last_cycle`（含壞掉的任務與待審批數）、`domains`（各領域放到決策面前的東西）、`strategy_summary`、`human_notes`。

### 有上限，而且會說自己被裁掉了什麼
一份會隨公司長大的快照，遲早比它要支援的決定還貴；而**把有趣的部分默默擠出 context 視窗，是最糟的遺失方式**。所以文件會量自己的大小，按**固定順序**裁剪，每一刀都記在 `trimmed` 裡——**CEO 被告知它看到的是局部，而不是被留著以為自己看到了全部**。

裁剪順序（從最能失去的到最不能）：領域的補充 → 趨勢 → 非運作中事業的細節 → 專案 KPI → 上一輪壞掉的任務（留 3 筆）→ 所有專案。

**永遠不裁**：`period`、`capital`、每門生意的名字與狀態。少了這些就不是決定了。因此文件有一個**不可壓縮的底線**——預算低於它不會讓文件變小，只會讓那個預算是錯的，而 `trimmed` 會說出這件事（有測試）。

`human_notes` 與 `strategy_summary` 來自公司政策，**只讀不編**。

`GET /api/companies/{id}/snapshot` 回傳同一份文件——**人看到的跟 CEO 看到的是同一份**，可以用 `?tokens=` 試不同預算。新聞室透過 snapshot 掛鉤提供 `candidates`（依分數排序的題材，上限 8 筆）——**放到決策面前，不是替它決定**。

### 驗證
- `pytest tests/company/test_snapshot.py`：18 個（組合是脊椎、資本、目標的趨勢會跨前綴對上、沒人量的目標會說出來、上一輪與壞掉的任務、專案剩餘預算、人寫的字只讀不編、領域補充、壞掉的掛鉤只失去自己那一段、小公司不裁、**裁剪會說出裁了什麼**、固定順序、底線存在且穩定、不可失去的永不被裁、公司可自訂預算、空公司也產得出文件、未知公司、KILLED 專案不出現）。
- `pytest tests/api/test_org_api.py`：新增 4 個（組合、新聞室的候選題材、緊預算會回報、未知公司 404）。
- 後端 993 個測試通過；web 254、事件契約 8 通過；六項 check 全過。

---

## 提交紀錄

| 提交 | 日期 | 內容 | 持續整合 |
|---|---|---|---|
| `c6c8dd4` | 2026-09-20 | T-603 第一批：KPI 與領域掛鉤、`kpi_snapshots`、修掉 `ARTICLE_PUBLISHED` 耦合 | ✅ 執行編號 `35492557724`（web 59 秒、python 2 分 50 秒、e2e 7 分 11 秒） |
| `278f802` | 2026-09-20 | T-600 第二批：新聞室的組織（AI Media 事業、部門與團隊、產品）、`/api/companies/{id}/org` | ✅ 執行編號 `35491545739`（e2e 6 分 26 秒、python 2 分 49 秒、web 1 分 16 秒） |
| `eb29c3a` | 2026-09-20 | 修 web typecheck（測試 fixture 缺欄位）；`make lint-web` 補上 typecheck | ✅ 執行編號 `35491126802` |
| `1fa692b` | 2026-09-20 | T-600 第一批：組織四張表、服務、`AGENT_ASSIGNED`、即時契約 | ❌ 執行編號 `35490732378`（python、e2e 通過；web typecheck 失敗）→ 見上一列 |
| `8bc0ef7` | 2026-09-20 | T-602 帳本（一輪一專案一筆結算）、補-1 `model_calls` 連到 workflow 與 cycle | ✅ 執行編號 `35489615243`（e2e 4 分 55 秒、python 2 分 33 秒、web 1 分 3 秒） |
| `e6d004c` | 2026-09-20 | T-601 cycle 狀態機、stage runner、時限；cycle 預算視窗 | ✅ 執行編號 `35489042649`（python 2 分 33 秒、e2e 7 分 31 秒、web 1 分 13 秒） |
