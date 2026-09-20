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
| T-604 | 命令管線（`submit_command`：instantiate_workflow、pause/kill、allocate_budget、update_strategy）、`commands_log` | T-205 | ✅ |
| T-609 | `agent_memory`（最近 N 次執行的摘要進 context，TTL 30 天、每個代理 ≤ 50 列） | T-211 | ✅ |
| T-605a | CEO 代理（公司層：資本、優先順序、組合） | T-211、T-603、T-604 | ✅ |
| T-605b | 總編輯代理（新聞室：選題、品質、發佈順序） | T-605a | ✅ |
| T-606 | 治理：kill criteria 自動暫停、kill 提案 → 人審、代理連續失敗 N 次自動暫停 | T-603、T-604 | ✅ |
| T-607 | Fallback plan + 每日摘要文件（`documents`） | T-601、T-605 | ⬜ |
| T-608 | Cycle 頁面 + Today's Goal 改成來自 `cycles.plan` | T-309、T-601 | ⬜ |
| T-610 | 7 個 cycle 的 soak（加速時鐘） | 全部 | ⬜ |

階段 5 帶過來的缺口，排進這一期：

| 編號 | 內容 | 併入 |
|---|---|---|
| 補-1 | `model_calls` 連到 workflow（每篇文章的總成本，AC-S8）——同時也是 T-602 按專案結算的前提 | T-602 ✅ |
| 補-2 | 人可從收件匣重新啟動失敗的 workflow（AC-9 的最後一句） | T-604 ✅ |
| 補-3 | 加預算後自動放回 `BLOCKED_BUDGET` 的任務（AC-13、P-5） | T-604 ✅ |
| 補-4 | CEO 代理讓辦公室有第 6 個 avatar（AC-1） | T-605a ✅ |
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

## T-604 · 命令管線

### 一條路，六個關卡
```
submit(name, payload)
  → 解析    payload 變成有型別的命令，不是的話在任何事發生前就被拒
  → 重播    用過的冪等鍵直接回傳第一次的結果
  → 決定    PolicyEngine：allow / deny / needs_approval / limited
  → 檢查    處理函式自己的前提：專案必須 ACTIVE、範本必須存在
  → 變更    一個交易：狀態、命令紀錄、它造成的事件
  → 記錄    誰問了什麼、怎麼決定的、結果如何
```
代理**沒有**直接寫專案 / 預算 / 金流的工具；人按的按鈕走同一條路，只是 `actor=human`。於是「誰可以改什麼」只有一個地方回答，「這間公司試過做什麼」也只有一個地方可讀。

### 三個刻意的決定
**拒絕是結果，不是例外。** 一間公司拒絕它的 CEO 加預算，這件事本身值得被看見；把它丟進例外堆疊會藏起最有趣的決策。所以拒絕也是一列 `commands_log`，`submit` 回傳它而不是拋出。

**核准不等於「現在做」。** 需要人審的命令先記成等待中，payload 存在核准上；人核准時，**同一個處理函式在同一把鑰匙底下執行**。而且核准之後不保證還做得到——測試裡有一條：專案在等待期間自己完成了，核准之後命令被拒，並把原因記在命令上。

**同一把鑰匙不做第二次。** 代理重試工具呼叫、使用者雙擊、webhook 重送，全都落在這裡。連「被拒絕」也會被記住——鑰匙是那次請求的身分，不是那次結果的。

### 九個動詞
`CreateCycleGoal`、`AllocateBudget`、`InstantiateWorkflow`、`CreateProject`、`PauseProject`、`ResumeProject`、`KillProject`、`UpdateStrategy`、`RestartWorkflow`。權限全部沿用 `company/policy.py` 早就註冊好的規則——**這個模組只說「做什麼」，不說「誰可以」**。

順手把兩個階段 5 的缺口做掉：
- **補-3（AC-13、P-5）**：`AllocateBudget` 把預算**調高**時，會把該範圍內 `BLOCKED_BUDGET` 的任務放回佇列。RUNBOOK 一直警告「加了預算不會自動恢復」——錢到了正是被擋住的工作在等的東西。調低則不放（有測試）。
- **補-2（AC-9 的最後一句）**：`RestartWorkflow` 讓失敗的流程**重跑一次**。不是續跑：舊的那一輪保留它的歷史，因為「什麼失敗了、為什麼」正是留著它的理由；題材、證據、文章都還在，所以重跑是重花模型呼叫、但從公司已經知道的東西開始。

`InstantiateWorkflow` 會把新的 workflow 與任務掛上當前 cycle（稽核鏈 cycle → workflow → task），而 `max_workflows_per_cycle` 的事實由 `workflows_this_cycle` 提供——防暴衝閘門 5 這才真的接上。

### 驗證
- `pytest tests/company/test_commands.py`：24 個。涵蓋決定 / 記錄 / 執行三件事、拒絕是結果、payload 不對在決定之前就被拒、公司狀態可以否決政策允許的事、未知命令是錯誤（不是紀錄）、同鑰匙不重做、拒絕也被記住、人審會等、核准後同鑰匙執行、駁回維持原狀、**核准後世界已改變就拒絕**、加預算放行被擋的工作、降預算不放行、預算只能屬於一個範圍、事業自己的信封、暫停 / 恢復、CEO 暫停會標註 `trigger=ceo`、建專案必須有 kill criteria 且要人審、開工計入 cycle 上限、失敗的流程可重跑、進行中的不可重跑、策略核准後下一份 snapshot 就讀得到、每次嘗試都在紀錄裡。
- 後端 1017 個測試通過；web 254、事件契約 8 通過；六項 check 全過。

### 我自己踩的坑（第二次）
`test_commands.py` 有五個查詢沒有篩公司。單獨跑全過，一跑全套就撞上別的測試 commit 的資料。上一批才剛修過同一類問題——**這種測試在單獨跑時會騙人**，我把整個檔案掃過一遍補上條件。

---

## T-609 · 代理的記憶

### 它不是另一份逐字稿
代理**執行中**的記憶已經有了：`agent_steps`，那是一次執行的完整過程。這裡做的是另一半——它帶進**下一次**執行的那幾行。沒有它，每次執行都從零開始，**一個被要求第三次修訂同一份草稿的寫手，沒有辦法知道這是第三次**。

留下的東西刻意很小而且有結構：任務、結果如何、行為本來就會產生的那一行摘要、以及把它退回的問題。**不留逐字稿、不留產出**——那些都在執行紀錄裡，一個 join 之外；複製過來只會讓記憶長到「讀它比它要幫的那次執行還貴」。

### 兩重上限，而且在寫入時就生效
30 天過期、每個代理最多 50 列，**兩個都在 `remember()` 當下執行**。理由很實際：**一個「必須靠清理工作才成立的上限」，在那個工作第一次沒跑到的時候就會無聲失效**。清理工作還是有（掛在 worker 的維護迴圈），但它只是打掃——`recall()` 本來就不會讀過期的列。

### 進 context 的位置與順序
`platform/08` 規定的順序：任務 → **最近幾次執行** → 領域的材料。渲染時**最舊在前**，因為那是日記的讀法，而且最近那次最可能重要，應該離問題最近。字數也有上限（1200 字元）——三段長摘要仍然是長的。

### 驗證
- `pytest tests/runtime/test_agent_memory.py`：15 個（回想最近三次、新代理沒有記憶也不說話、不會讀到別人的記憶、**被退回的原因會被記住**、只留最新的 N 列、上限是每個代理而非每間公司、過期的不會被回想（即使還沒刪）、打掃只刪沒人會讀的、維護工作就是同一件事、渲染像日記、說得出每次的結果、太長會被切、留下的內容本身有上限、順利的執行不帶 issues、預設值就是規格的 30 天 / 50 列 / 3 次）。
- `pytest tests/runtime/test_agent_runner.py`：新增 2 個——**真的跑一次會留下一行**（含 `expires_at`，寫下的當下就有界），以及**被退回的那次會記住原因**。
- 後端 1034 個測試通過；web 254、事件契約 8 通過；六項 check 全過。

### 寫測試時撞到的兩件事
1. 「被退回」那個測試一開始沒過，因為我只給模型劇本一個回合——執行器在修訂時會再問一次，於是它是**餓死**而不是**驗證失敗**，走的是另一條失敗路徑。補上修訂那回合的劇本才是真的在測我要測的東西。
2. 打掃的測試斷言「剛好刪掉 1 列」。但打掃**按設計是全公司的**，而別的測試 commit 過的記憶在假時鐘前進 31 天之後也過期了。斷言改成只看自己的代理——全域的工作不該用全域的計數去斷言。

---

## T-605a · CEO 代理

### 它跟其他代理一模一樣，這是刻意的
CEO 不是特例：建一個任務、工作程序認領、執行器執行。**一家公司如果它的執行長跑在自己的私有機制上，那個執行長就無法被觀察、追蹤、編預算、暫停**——跟其他人一樣的待遇才是對的。

所以規劃是一條單節點的 workflow，由 cycle 啟動：
```
進入 PLANNING  → 開 company.cycle_plan_v1   → CEO 規劃
PLANNING 結束  → 那個任務終結時
進入 REVIEWING → 開 company.cycle_review_v1 → CEO 覆盤
```
**cycle 不會為了 CEO 無限等下去**：PLANNING 跟其他階段一樣有時限，CEO 慢了、卡了、不存在，階段照樣逾時推進。**一間因為執行長在想事情而停擺的公司，比一間今天沒有計畫的公司更糟。**

**沒有 CEO 的公司照樣跑完一天**：沒有人擔任那個職務時不開任務，階段立刻結束——這正是「還沒雇執行長」的公司該有的樣子（有測試）。

CEO 的執行歸在公司自己的專案「Company operations」底下。不是為了繞過不可為空的欄位：**執行長的執行真的會花錢，而錢必須落在一個報得出來的地方**，跟它旗下事業花的錢分開。

### 它只有一個工具
`submit_command`。它不能直接寫專案、搬錢、開工——它**提出要求**，由 T-604 的管線決定。於是：
- **被拒絕是回傳值，不是例外。** 代理被告知「不行」與理由，可以在同一次執行裡改做別的。一個在這裡拋例外的工具，會因為一個再普通不過的答案而結束整次執行。
- **冪等鍵就是那次工具呼叫的鍵。** 崩潰後重試的執行會重送同一個命令，拿回第一次的結果，而不是開出第二個專案。

### 它不能宣稱沒做過的事
CEO 寫下的計畫，會跟它**那次執行實際送出的命令**比對。一個寫「我配了 20 元給 AI Media」卻沒真的要求過的代理會被退回。

**最便宜的謊就是描述一個沒做過的決定，也是最貴的那種——公司會看起來有一筆它從未配置的預算。** 為了能比對，`commands_log` 新增 `run_id` / `task_id`（migration 0028），順帶把稽核鏈補完：cycle → 計畫 → 命令 → 工作。被拒絕的命令不算數（有測試：預算被駁回的 CEO 不能寫成已配置）。

### 驗證
- `pytest tests/company/test_ceo.py`：18 個（兩個工作一個工具、**提示裡明寫它不做什麼**、不能宣稱沒送出的事、送出過的就能寫、被拒的不算、不能點名已結束的專案、不能決定別家公司的專案、目標數量上限、PLANNING 會開工作並等它、工作歸在公司自己的專案、**沒有 CEO 照樣跑完一天**、暫停中的 CEO 不會被叫、進入兩次只問一次、REVIEWING 問另一個問題、工具是唯一的行動方式、拒絕是答案不是錯誤、不認得的命令、同一次工具呼叫只問一次）。
- 後端 1052 個測試通過；web 254、事件契約 8 通過；六項 check 全過。

### 兩個把我卡住的坑
1. **`company/agents/` 套件遮蔽了 `company/agents.py`。** 規格說 CEO 放在 `company/agents/ceo`，但那個路徑已經有一個模組（雇用代理）。把它搬成 `agents/roster.py` 並從 `__init__` 原樣再匯出，兩邊都成立。
2. **測試整個卡死，查到最後是資料庫的諮詢鎖。** 事件 outbox 會對每家公司取一把鎖來序列化事件序號；我的測試在 `db_session`（會被回滾的外層交易）裡建公司、再用 `committed` 的真連線對同一家公司發事件——**它在等自己**。`db_session.commit()` 只是釋放 savepoint，不是真的提交。那四個測試改成整個世界都建在 `committed` 裡。這個教訓值得記住：**這個專案的兩種 session fixture 不能混用在同一家公司上**。

---

## 兩個不穩定的測試（2026-09-20）

有兩個**只改 markdown** 的提交 CI 是紅的。它們不可能弄壞任何東西——所以紅的是測試本身，而我先前只看了程式提交的 CI，沒回頭看這兩個。兩個都修了，而且兩個都是我自己前幾天寫下的。

**一、AC-9 的三筆事件沒有排序**（`tests/e2e/test_newsroom_sim.py`）
`[e.payload["final"] for e in failed] == [False, False, True]` —— 查詢沒有 `ORDER BY seq`，所以「最後一筆才是最終失敗」在資料庫想給什麼順序時就變成擲骰子。CI 上出現過 `[False, True, False]`。加上 `.order_by(EventRecord.seq)`。

**這跟階段 5 修過的 `test_services.py` 是同一個錯**，我在寫階段 5 收尾的 AC-9 測試時又犯了一次。

順帶一提：我試過寫腳本把測試裡**所有**沒排序的事件查詢一次補上，結果弄壞 12 個測試（有些查的是純量欄位、有些結構不同）。整批還原，只手改真正會出錯的那一個。**「聰明的一次修好」在這裡比「笨的修一個」貴。**

**二、點頭像會落空**（`e2e/office.spec.ts`）
讀到座標與真正點下去之間，avatar 還在走向椅子——座標就過期了。改成**重讀座標、最多試三次**；計時仍然從成功的那一次按下算起，所以 300 毫秒那條驗收條件沒有被放寬。這也是階段 5 在新聞室測試修過的同一個根因（那次的解法是改用鍵盤快捷鍵，但這個測試的重點就是「點」，不能換成按鍵）。

---

## T-605b · 總編輯代理

這是 v2 修訂真正的目的地：**公司決定新聞室能花多少，新聞室決定花在什麼上**。兩邊都不做對方的工作。

### 它委派，它不寫
總編輯只有一個工具 `commission_story`：拿一個候選題材，把整條線發動起來——研究、分析、雙語草稿、審稿、人核准、發布。寫出來的東西是寫手與編輯的事，它下次看到是在明天的數字裡。

**上限不在新聞室這裡。** `commission_story` 走 `start_story` → `start_workflow` → 權限引擎，用的是**公司的** `max_workflows_per_cycle`。所以新聞室的規則指向 `company.policy.max_workflows` 這個函式本身（有測試斷言它就是同一個物件）——**新聞室不能給自己一個比較大的一天**。被拒絕時回來的是一個它能據以行動的答案。

### 它不能宣稱沒委派的事
跟 CEO 同一個原則：寫在計畫裡的題材，必須是新聞室**真的在做**的。不然公司會以為有三篇文章在路上，而沒有人在寫。

### 一個排序問題，與它的正解
一開始我把總編輯的規劃掛在 PLANNING 進入時，接在 CEO 後面。但**兩個規劃工作會在同一個交易裡建立，於是同時可被認領**——總編輯可能在 CEO 配預算**之前**就選完題，那就架空了「在公司剛設的預算內決定」。

正解不是加鎖，是換時機：**總編輯改在 EXECUTING 進入時規劃**。PLANNING 只有在 CEO 回答後才結束，所以進入 EXECUTING 是預算**第一個可知的時刻**。而且這也正是新聞室真正的運作方式——**工作日的第一件事就是desk決定今天是什麼**。EXECUTING 的完成檢查是「這一輪開的 workflow 都結束了」，總編輯那一輪也是其中之一，所以它會等。

### 順帶修掉的三件事
1. **`submit_command` 沒有被宣告給權限引擎**，所以 CEO 一叫工具就被中止（「不是已知的動作」）。加上宣告與 `ceo` 的允許規則。**允許那個工具不等於允許它要求的事**——每個命令都會再被自己的動作決定一次。
2. **模擬用位置參數建 `FakeToolUse`**（它是 pydantic 模型），兩個模擬檔都錯。
3. **模擬的 CEO 解析不到快照**：第一則訊息裡先有任務的 `Input:` JSON，我的正則從第一個 `{` 抓到最後一個 `}`，抓到兩段黏在一起的東西。改成從「The company right now:」這個標記之後才開始解析。

### 驗證
- `pytest tests/newsroom/test_editor_in_chief.py`：17 個（只委派不寫、看得到候選與可花的錢、空桌會說出來、不能列出沒委派的題材、委派過的就能寫、已發布的不能再做、別家的題材不是它的、承諾的數字要等於選的數量、不能超過一天能扛的量、委派會啟動整條線、已在製作中的不會重複委派、不存在的題材、規劃會排在公司之後、沒有頭的desk不擋路也不規劃、會等它回答、暫停中的頭不會被問、**上限是公司的那一個**）。
- `pytest tests/e2e/test_newsroom_sim.py`：新增 **AC-11 的模擬版**——沒有人按任何東西：cycle 自己開、CEO 設目標與預算、總編輯從桌上挑題材委派、整條線跑起來。這是這個專案第一次**公司自己決定了自己的一天**。
- 後端 1117 個測試通過；web 254、事件契約 8 通過；六項 check 全過。

### 還沒接上的
`ensure_cycle_schedule` 仍然沒有人呼叫——公司會在被要求時跑完一輪，但還不會自己每天開始。那是 T-610 冷啟動的部分。

---

## T-606 · 治理：不問任何人就執行的規則

治理是公司在「量測完」與「覆盤前」之間**確定性**做的事。它讀已經存在的數字，套用事先寫下的規則，**不呼叫模型、也不能呼叫**——這些規則存在的全部意義，就是**在 CEO 慢、錯、不在、或想爭辯的時候仍然成立**（有一條測試直接斷言整個過程沒有產生任何 `model_calls`）。

三條規則，照這個順序跑：

1. **Kill criteria。** 專案或事業違反自己寫下的判準就被暫停。判準是**核准時就固定**的——這才公平：要打敗的數字是在還不知道打不打得到之前選的。
2. **一直失敗的代理被暫停。** 連續三次最終失敗就不再派工。二次可能是同一個壞輸入出現兩次；三次就該停。
3. **快到上限的花費被公告。** cost guard 在 100% 拒絕呼叫；這條在 80% 說出來，**讓逼近在撞牆之前就看得見**。

**暫停不是關閉。** 治理能讓工作停下來，但**只有人能結束一個專案或事業，而 CEO 只能提案**。這個不對稱是刻意的：一條因為一週數字難看而觸發的規則，應該能止血，**不應該能關掉一門生意**（有測試確認它從不送出 `KillProject`）。

它做的每件事都走命令管線，所以**規則決定的暫停，跟人決定的暫停記在同一個地方**，理由欄寫著是哪條規則。

幾個邊界都有測試：沒人量過的指標不是違規（**沉默不是證據**）、`evaluate_after_cycles` 內不評判、`consecutive_cycles` 要每一輪都違反、看不懂的判準只記警告不暫停任何東西、事業被暫停時它的專案跟著停（**被暫停的生意不該還在花錢**）、重跑不會暫停兩次。

### 順手修掉一個真的錯
`PauseProject` 原本用角色判斷事件的 `trigger`，於是**治理暫停專案時被標成 `human`**——一個自動的決定看起來像某個人做的。改成由 actor 決定：系統發起的暫停就是治理規則，因為這間公司裡沒有別的東西會在沒有人也沒有 CEO 的情況下暫停專案。

### 驗證
- `pytest tests/company/test_governance.py`：19 個。
- 後端 1136 個測試通過；web 254、事件契約 8 通過；六項 check 全過。

### 另外修好的一個 e2e 競爭
T-605b 那一輪的 CI e2e 紅了：新聞室的瀏覽器測試點開寫手，斷言面板顯示「撰稿：⟨題目⟩」——但**寫手已經寫完了**。它在跟生產線賽跑，而我的改動讓時序變了。正解是**拿掉那個競爭**，不是讓系統變慢：改成斷言面板提到這個題目（不論是當前任務或下一步的交接），後面跟著的草稿連結斷言不變，測試真正要證明的「從辦公室走到草稿」完全保留。

---

## 提交紀錄

| 提交 | 日期 | 內容 | 持續整合 |
|---|---|---|---|
| `28ab67e` | 2026-09-20 | T-605b 總編輯代理、`commission_story`、AC-11 的模擬版 | ❌ 執行編號 `35499880456`（python、web 通過；e2e 的新聞室測試與生產線賽跑）→ 已修，見上 |
| `94be330` | 2026-09-20 | 修兩個不穩定的測試（AC-9 事件排序、辦公室點頭像重試） | ✅ 執行編號 `35498653718`（python 2 分 49 秒、e2e 4 分 55 秒、web 1 分 5 秒） |
| `d8d9e27` / `73813b3` | 2026-09-20 | 只改開發紀錄的兩個提交 | ❌ 兩個**不穩定的測試**，不是那兩個提交造成的（AC-9 的事件沒排序、辦公室點頭像落空）→ 已修，見下 |
| `aa2b639` | 2026-09-20 | T-605a CEO 代理（規劃 / 覆盤 workflow、`submit_command` 工具、說到做到的驗證器）；`commands_log` 記錄發出它的執行 | ✅ 執行編號 `35497974168`（e2e 6 分 48 秒、python 2 分 42 秒、web 1 分 11 秒） |
| `1932d29` | 2026-09-20 | T-609 代理記憶（寫入時雙重上限、進 context、維護工作） | ✅ 執行編號 `35493594226` |
| `b794a0b` | 2026-09-20 | T-604 命令管線、九個動詞、`commands_log`；補-2 重啟 workflow、補-3 加預算放行被擋的任務 | ✅ 執行編號 `35494906311`（e2e 5 分 2 秒、python 2 分 45 秒、web 1 分 47 秒） |
| `2c66378` | 2026-09-20 | T-603 第二批：CompanySnapshot、token 上限與裁剪紀錄、`/snapshot` API | ✅ 執行編號 `35493172070`（web 1 分 16 秒、e2e 5 分 53 秒、python 3 分 13 秒） |
| `c6c8dd4` | 2026-09-20 | T-603 第一批：KPI 與領域掛鉤、`kpi_snapshots`、修掉 `ARTICLE_PUBLISHED` 耦合 | ✅ 執行編號 `35492557724`（web 59 秒、python 2 分 50 秒、e2e 7 分 11 秒） |
| `278f802` | 2026-09-20 | T-600 第二批：新聞室的組織（AI Media 事業、部門與團隊、產品）、`/api/companies/{id}/org` | ✅ 執行編號 `35491545739`（e2e 6 分 26 秒、python 2 分 49 秒、web 1 分 16 秒） |
| `eb29c3a` | 2026-09-20 | 修 web typecheck（測試 fixture 缺欄位）；`make lint-web` 補上 typecheck | ✅ 執行編號 `35491126802` |
| `1fa692b` | 2026-09-20 | T-600 第一批：組織四張表、服務、`AGENT_ASSIGNED`、即時契約 | ❌ 執行編號 `35490732378`（python、e2e 通過；web typecheck 失敗）→ 見上一列 |
| `8bc0ef7` | 2026-09-20 | T-602 帳本（一輪一專案一筆結算）、補-1 `model_calls` 連到 workflow 與 cycle | ✅ 執行編號 `35489615243`（e2e 4 分 55 秒、python 2 分 33 秒、web 1 分 3 秒） |
| `e6d004c` | 2026-09-20 | T-601 cycle 狀態機、stage runner、時限；cycle 預算視窗 | ✅ 執行編號 `35489042649`（python 2 分 33 秒、e2e 7 分 31 秒、web 1 分 13 秒） |
