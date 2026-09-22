# 開發紀錄 06 — 階段 6 自主營運迴圈（Autonomous Loop）

- 期間：2026-09-20 ～ 2026-09-22（完成；雛形收尾三件見〈雛形收尾〉）
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
| T-600 | **組織模型（v2）**：`business_units`、`departments`、`roles`、`products` 四張表；代理歸屬部門與職務；事業歸屬；種子資料與 API；辦公室改成可進入的部門 | `ARCHITECTURE_V2.md` | ✅ |
| T-601 | Cycle FSM + stage runner + deadlines（`cycles` 表、五階段、逾時強制推進） | T-212、T-104 | ✅ |
| T-602 | Ledger：`model_calls` → expense 結算、餘額與已花額度 | T-209、T-103 | ✅ |
| T-603 | Reporting + CompanySnapshot（token 上限）、`kpi_snapshots` | T-602、T-516 | ✅ |
| T-604 | 命令管線（`submit_command`：instantiate_workflow、pause/kill、allocate_budget、update_strategy）、`commands_log` | T-205 | ✅ |
| T-609 | `agent_memory`（最近 N 次執行的摘要進 context，TTL 30 天、每個代理 ≤ 50 列） | T-211 | ✅ |
| T-605a | CEO 代理（公司層：資本、優先順序、組合；**增量**：覆盤時決定機會） | T-211、T-603、T-604 | ✅ |
| T-605b | 總編輯代理（新聞室：選題、品質、發佈順序） | T-605a | ✅ |
| T-606 | 治理：kill criteria 自動暫停、kill 提案 → 人審、代理連續失敗 N 次自動暫停 | T-603、T-604 | ✅ |
| T-607 | Fallback plan + 每日摘要文件（`documents`） | T-601、T-605 | ✅ |
| T-608 | Cycle 頁面 + Today's Goal 改成來自 `cycles.plan` | T-309、T-601 | ✅ |
| T-610 | 7 個 cycle 的 soak（加速時鐘） | 全部 | ✅ |
| T-611 | **機會與提案（v2.1）**：三張表 + `projects.opportunity_id`；兩個狀態機；八個命令 | T-605a、`ARCHITECTURE_V2_1.md` | ✅ |
| T-612 | 客戶（`customers` + `transactions.customer_id`） | — | ✅（提前實作，D-018 取代 D-017 的延後） |

階段 5 帶過來的缺口，排進這一期：

| 編號 | 內容 | 併入 |
|---|---|---|
| 補-1 | `model_calls` 連到 workflow（每篇文章的總成本，AC-S8）——同時也是 T-602 按專案結算的前提 | T-602 ✅ |
| 補-2 | 人可從收件匣重新啟動失敗的 workflow（AC-9 的最後一句） | T-604 ✅ |
| 補-3 | 加預算後自動放回 `BLOCKED_BUDGET` 的任務（AC-13、P-5） | T-604 ✅ |
| 補-4 | CEO 代理讓辦公室有第 6 個 avatar（AC-1） | T-605a ✅ |
| 補-5 | AC-S4 的崩潰恢復改測寫手；AC-S6 的靜態掃描；AC-S7 的 WS p95 | T-610 ✅ |

### 雛形收尾：還沒做的三件事（2026-09-22 盤點）

階段 6 的任務都做完了，這三件是「雛形可以說自己完成了」還差的，寫在這裡免得只留在對話裡：

| 編號 | 內容 | 為什麼還沒做 |
|---|---|---|
| 尾-1 | **重新量 AC-S7 的 soak**：現有數字（FPS、heap、WS p95）是 T-600 第三～五批之前量的，部門分區、事業色帶、房間隔離都改過場景 | ✅ 2026-09-22 重量通過，見〈尾-1〉一節 |
| 尾-2 | **真的把 `domains/newsroom` 刪掉一次**，跑 runtime / company / realtime 的測試（AC-S5 的最後一句） | ✅ 2026-09-22 做過：那句話不成立，原因全是正確行為 → D-021、AC-S5 改寫，見〈尾-2〉一節 |
| 尾-3 | **AC-13 的端到端測試**：預算調到極低 → model call 被拒 → run ABORTED → `BUDGET_EXHAUSTED` 進 timeline → 加預算後任務自己回到佇列 | ✅ 2026-09-22 通過，並發現驗收條件本身寫錯了兩處，見〈尾-3〉一節 |

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

## T-607 · CEO 沒回答的那些天

### 先補一個從 T-601 就存在的空洞
`cycles.plan` 與 `cycles.review` 這兩個欄位**從建好到現在都沒有人寫入**。CEO 的產出留在任務的 output 裡，沒有回到 cycle 上——於是「那天決定了什麼」跟著那次執行一起沉在底下，而快照也答不出「上一輪有沒有覆盤」。現在每個階段結束時把它記上去：**當天的決定要比做出它的那次執行活得久**。

### CEO 失敗時
一次失敗或逾時的規劃**不是沉默**：fallback 執行、`CYCLE_PLAN_FALLBACK` 帶著原因發出、公司**繼續做它昨天在做的事**。

fallback 刻意是**最無聊的那個決定**——沿用上一輪的預算配置、不設新目標——因為**沒有人規劃的一天是該守住陣地的一天，不是該即興發揮的一天**。公司想要別的，就寫進 `company.fallback_plan` 政策。

原因會被說清楚，三種情況分開：沒有 CEO 可問、CEO 的執行失敗（第幾次嘗試）、階段結束時任務還卡在某個狀態。

### 覆盤不見時
cycle **仍然走到 DONE**，但 `cycles.review` 會記下 `missing` 與原因，而**下一份快照會明說覆盤不見了**——CEO 被告知它不見了，而不是被留著把沉默當成一切順利。

### 每日摘要（`documents`，migration 0029）
cycle 走到 DONE 時，公司把那天寫下來：規劃了什麼、做了什麼、花了多少、規則做了什麼、覆盤說了什麼。一個 cycle 一份，重跑是**改寫**不是複製。

**用算術寫的，不是用模型寫的。** 裡面每一項本來就是一個數字或一個已記錄的決定，所以這份摘要不花錢、不可能對「發生過什麼」說錯，而且**在 CEO 失敗的那些天照樣存在**——一份由模型撰寫的、關於模型無法規劃的那一天的摘要，會是這間公司裡最不可信的一句話。有測試斷言寫摘要產生零筆 `model_calls`。

### 驗證
- `pytest tests/company/test_fallback.py`：13 個（計畫被記到 cycle 上、失敗時沿用上一輪、公司可自訂 fallback、第一輪沒有東西可沿用、沒有 CEO 時記「沒有人被問」、覆盤不見會被記成不見且進快照、有覆盤時摘要帶著它、摘要說出那天做了什麼、沒人規劃的那天會直說、規則做了什麼會寫進去、只寫一份且就地改寫、cycle 結束會留下摘要、**寫摘要不花任何錢**）。
- 後端 1149 個測試通過；web 254、事件契約 8 通過；六項 check 全過。

### 寫測試時撞到的
三個測試卡住，因為我只把 CEO 的**任務**標成完成，沒動它的 **workflow**。EXECUTING 等的是 workflow 不是 task，所以 cycle 就卡在那裡——**跟正式環境會發生的事一模一樣**。測試輔助函式改成兩個都結束，並把這件事寫在註解裡。

---

## T-608 · 把「今天」端到畫面上

這一輪之前，畫面上看得到代理在動、看得到錢，但**看不到這間公司今天打算做什麼**。「今日目標」來自一張長期目標表（`goals`），它跟 cycle 沒有關係——公司昨天規劃了什麼、今天做到哪裡，在 UI 上根本不存在。T-608 把 cycle 端出來。

### 兩個端點，兩個問題

`GET /api/companies/{id}/cycles` 回答「最近幾天過得如何」，一天一列，看得出趨勢；`GET /api/cycles/{id}` 回答「那一天到底發生什麼事」。

細節頁**不生成任何敘述**，它只是把已經存在的東西擺在一起：CEO 記下的計畫、Reporting 量到的 KPI、跑過的 workflow、結束時寫下的摘要、依序的事件。沒有計畫的那一天就直說沒有——fallback 已經把原因記下來了（T-607），一個安靜顯示空計畫的頁面會剛好把最值得看的部分藏起來。

### AC-12：字來自計畫，數字來自量到的

> 「發布 5 篇雙語文章」來自 `cycles.plan`；「3 / 5」來自真實的已發布數。

後端的 `_goals()` 就是這件事：`target` 取自計畫、`current` 取自那一輪的 KPI 快照，**永遠不取自計畫自己對進度的說法**。一個目標寫 `published_articles`、量到的是 `newsroom.published_articles`，也會對上（比對最後一段）。

有一個測試專門守這件事：錯過目標的那一天，計畫仍然說 5、計數仍然說 1——頁面不會替那天圓謊。

### 即時串流也知道今天是第幾輪

`RealtimeSnapshot` 多了 `cycle`（id、seq、stage、deadline），Python reducer 從 `CycleStarted` / `CycleStageChanged` 跟著走，TypeScript reducer 也跟著走——**兩邊必須算出同一個結果**，所以契約 fixture 重新產生了，而且這次的隨機歷史裡真的有 cycle（6 輪、20 次階段轉換）。

比對 deadline 時比的是「那個時刻」而不是「那串字」：快照和事件的 JSON 由兩個不同的地方寫出，只有時刻必須一致。

### 重新產生 fixture 撞出來的事

契約歷史是隨機的，但有三個測試檔把「這次抽到什麼」寫死了——第一個交接剛好給 analyst、第一個 `AGENT_RUN_FAILED` 剛好是最後一次失敗。歷史一換，它們就紅了。**這是測試的問題，不是新歷史的問題**：現在它們從事件自己的 payload 讀出該走到哪個角色，而不是背下來。3D 辦公室的 mapping 測試也放行了 `human`——那是審批桌，一個沒有人坐的位子。

還有一件事順手修好：seed 6 的歷史變得太稀（120 步裡只有 31 步做了事，因為抽到的動作當下都不合法）。與其調權重調到綠，不如讓**不合法的動作只吃掉一次抽籤、不吃掉整步**（`ATTEMPTS = 6`）。每個 seed 的歷史都變密了，312 個事件、27 種類型。

### 驗證
- `pytest tests/api/test_cycles_api.py`：11 個（兩個端點的形狀、找不到的公司與 cycle、**AC-12 的目標對照**、沒有計畫的那天、覆盤不見、摘要、時間軸依序、成本）。
- `vitest src/features/cycles`：8 個（進度的算法、今天是哪一輪、壞掉的成本字串、列表、沒人規劃的那天、沒跑過的公司、細節頁）。
- `vitest src/realtime`：cycle 事件的 reducer 三個測試，加上跨語言契約現在會比對 `cycle`。
- 後端 1160 個測試、web 268 個測試通過；六項 check 全過。

---

## T-610 · 七天，沒有人插手

這一輪要回答的是階段 6 唯一重要的問題：**放著不管，它會自己跑嗎？**

### 先補上最後一個沒有人呼叫的函式

`ensure_cycle_schedule` 從 T-601 就寫好了，但沒有人呼叫它——公司有 cycle runner、有排程處理器，就是沒有排程那一列。等於每一次「自主運轉」都得有人先手動開第一輪。

現在 `create_company` 自己建立它。**每日排程是「成為一間公司」的一部分**，不是某個人記得去打開的開關。一間沒有代理的公司就是每天規劃「什麼都不做」——對一間沒有人的公司來說，那是剛剛好的工作量。

### 治理留下痕跡（`cycles.governance`，migration 0030）

AC-14 要求「auto-pause 規則至少被評估一次並**記錄**」。原本治理只在**有東西被擋下來**時留下事件，所以「規則跑了、什麼都沒發現」和「根本沒有人跑規則」事後看起來一模一樣。

現在每一輪都把結果寫進 `cycles.governance`：看了幾個專案、幾個事業、幾個代理、幾個預算，暫停了誰，警告了什麼。這是本來就算出來的數字，寫下來不花成本，而且在 cycle 詳細頁看得到（接上 T-608）。

### 驗收測試：`make autonomy`

`backend/tests/acceptance/test_autonomous.py`，四個測試，整組約一分鐘：

1. **新公司出生就有一天**（AC-11 前半）：建立公司 → 排程存在、下一次觸發在未來、再問一次不會變成第二列。
2. **排程到點，第一輪自己出現**（AC-11 後半）：時鐘轉到排程前五分鐘 → 沒有 cycle；再轉十分鐘 → 有一輪、一個 `CYCLE_STARTED`。整個測試沒有呼叫過 `cycles.start`。
3. **七天，沒有人插手**（AC-14）：示範新聞室，七輪全部 DONE、七輪都有計畫與覆盤、七輪都被規則看過、七份 KPI 快照。而且**第一輪開始之後，事件裡沒有任何 `actor.kind == "human"`**——人把公司建好就走了。
4. **沒有領域的公司照樣過日子**（`ARCHITECTURE_V2_1` §9）：一間只有 CEO 與一個專案、完全不知道新聞為何物的公司，一樣規劃、量測、治理、覆盤。CEO 甚至自己開了第二個專案。

**只有時鐘是假的。** 排程器、worker、任務管理、代理、工具、帳本、規則，都是 worker 進程真的在跑的那些；時鐘一動，維護迴圈就到期、排程就觸發、階段時限就過期，一天只要幾毫秒。

七輪跑出來長這樣，每一輪都是 CEO 自己規劃、自己覆盤：

```
cycle 1..7: DONE plan=ceo review=ok governance={'agents': 7, 'budgets': 1, 'projects': 2, 'business_units': 1}
```

### 補-5：階段 5 留下的三個洞

- **AC-S4 改測寫手**：崩潰恢復原本測的是 echo 的研究員。新聞室模擬多了一個 `demo.pause` 旋鈕（照 echo 的樣子做），可以讓某一次嘗試在**工具已經寫完、任務還沒結束**的那個空檔卡住——那正是崩潰最傷的時刻。`test_killed_worker_loses_no_draft`：稿子寫好 → `kill -9` → 第二個 worker 回收租約、以第 2 次嘗試完成 → **一篇文章、一個版本、一個 `TASK_SUCCEEDED`**。
- **AC-S6 靜態掃描**：`src/no-fake-data.test.ts` 掃自己的原始碼。store、reducer、features 裡不准有計時器；socket 客戶端可以有計時器，但**名字要列在測試裡**（`ackTimer`、`reconnectTimer`、`watchdog`）——多一個就紅，逼人說清楚那是哪一種。另外不准出現寫死的名字、頭像或代理陣列。
- **AC-S7 量 WS 延遲**：`src/realtime/latency.ts` 記錄「一則 socket 訊息變成狀態」花了多久，保留最後 500 筆，透過 `window.__autoraRealtime` 給測試讀。長時間 soak 與 CI 的即時測試都會斷言 **p95 < 100 毫秒**。

### 驗證
- `make autonomy`：4 個測試通過（約 1 分鐘）。
- `pytest tests/e2e/test_recovery.py`：2 個（echo 研究員、新聞室寫手）。
- `vitest src/no-fake-data.test.ts`：5 個；`vitest src/realtime/latency.test.ts`：4 個。
- 後端 1165 個測試、web 277 個測試通過；六項 check 全過。

---

## T-611 · 這間公司要做哪一門生意

到 T-610 為止，公司會**經營**一門生意：規劃、執行、量測、覆盤，七天不用人管。它不會**發現**新的生意。T-611 補上 `ARCHITECTURE_V2_1` 的那一層。

### 三張表，一個可空欄位（migration 0031）

| 表 | 回答的問題 |
|---|---|
| `opportunities` | 「這**可能**是門生意」——在花任何一塊錢之前就存在 |
| `opportunity_signals` | 「我們觀察到什麼」——外殼是公司的，意思是領域的 |
| `business_proposals` | 「那我們到底要怎麼做」——一個機會可以有好幾份 |

加上 `projects.opportunity_id` 一個可空欄位，**整個商業迴圈的執行模型就完成了**。探索與驗證就是 Project，所以預算、成本歸屬、停損全部沿用既有機制——不需要新的預算種類、不需要新的治理規則、不需要新的排程器。

### 兩條不肯讓步的規則

**被否決的機會留著。** 「我們看過 AI 客服，因為獲客成本不划算而否決」是公司的決策記憶；刪掉它，六個月後會再查一次、再花一次錢。所以 `open_opportunities()` 看不到它，`by_key()` 永遠找得到它。

**送出的提案凍結。** 要改就是新版本，舊的變成 `SUPERSEDED`。理由很實際：核准必須指向**當時被核准的那份東西**，否則「我們核准了 2000 美元去做什麼」這個問題沒有可稽核的答案。資料庫也擋著——一個 APPROVED 的機會沒有指向它變成的那門生意，寫不進去。

寫測試時修掉一個我自己寫錯的語意：一開始「送出提案」會把同一個機會的**草稿**也一起 SUPERSEDED。那是錯的——兩份草稿是還在寫的替代方案，送出其中一份不該丟掉另一份。現在只有**已送出**的會被取代，因為「同時只能有一份等著被決定」。

### 八個命令，一條線

全部走既有的命令管線。那條線是 §6 的：**不可逆、或動到真錢的那一刻，由人決定。**

| CEO 自己來 | 人決定 |
|---|---|
| 打分、推進到 EVALUATING / VALIDATING、否決 | **推進到 APPROVED**（承諾開一門生意） |
| 探索預算（≤ 門檻，預設 2 美元） | 超過門檻的探索預算 |
| 加減既有事業的資本（≤ 50 美元） | **建立事業單位**（真資本 + 長期組織） |
| 暫停事業（止血） | **收掉事業**（不可逆） |

`CreateBusinessUnit` 只收一個 **proposal_id**：名字、產品、kill criteria、錢，全部從那份凍結的文件讀出來。一個交易裡完成事業、產品、提案核准、機會核准、資本撥入——要嘛全發生，要嘛都沒發生。**核准指向的是一份文件，不是某人當場打的一串參數。**

### 順手補上 T-610 欠的靜態檢查

`ARCHITECTURE_V2_1` §9 要求兩個會跑的檢查：拿掉領域仍能跑一輪（T-610 做了），**加上 Core 不得出現領域詞彙**（T-610 漏了，現在補上）。

`test_core_vocabulary.py` 讀的是**程式碼，不是散文**：用 AST 把 docstring 與註解排除，只看識別字與真正被使用的字串。`company/` 與 `realtime/` 必須乾淨——它們現在是了，代價是修掉一處真的違規：模擬 CEO 的計畫裡寫死了 `newsroom.published_articles`，現在改成從快照裡讀公司**實際在量的**指標，沒有領域時就用核心指標。

`runtime/`、`db/`、`infra/` 還有七處，一條一條列在 `KNOWN_LEAKS` 裡（`ApprovalKind.ARTICLE`、`Company.type` 的 `newsroom`、`story_match_threshold`、fetcher 的 User-Agent）。這是個**只能變少的棘輪**：新增一處會紅，修好一處也會紅——那正是該刪掉那一行的時候。

### CEO 還看不到全部

快照多了 `opportunities` 一段（機會、它的提案、正在探索它的專案與花費），所以 CEO **看得到**；但 CEO 的 `CycleReview` schema 還沒有「機會決策」那一段，也就是說**目前只有人會用這八個命令**。那是 T-605a 的增量。

→ 已在下面的〈T-605a 增量〉補上。

### 驗證
- `pytest tests/company/test_opportunities.py`：18 個（機會先於投入存在、訊號累積、打分不是決定、只能向前、否決留著、APPROVED 必須有事業、資料庫也擋、過期、排序、一機會多提案、送出凍結與取代、決定過的不能再決定、草稿不能跳過送出、探索就是 Project、公司隔離、快照三個、cycle 自己過期且不花模型錢）。
- `pytest tests/company/test_business_verbs.py`：14 個（CEO 自己打分推進、APPROVED 要人審、否決留理由、探索預算只給探索專案、大額探索要人審、開事業一律人審、人核准後事業照提案開出來、未送出的提案開不了、同名事業不開第二次、加資本有上限、暫停是 CEO 的而收掉不是、三種結果都留下紀錄、跨公司擋住）。
- `pytest tests/company/test_core_vocabulary.py`：4 個。
- 後端 1273 個測試、web 277 個測試通過；六項 check 全過。

---

## T-600 第三批 · 辦公室變成那張組織圖

前兩批把組織建進資料庫，但 3D 辦公室還是 v1 的樣子：**座位由角色決定**（`SLOTS` 寫死 researcher 坐哪、writer 坐哪），2D 備援把五個區硬塞成四列。這一批把 `ARCHITECTURE_V2` §14.7 的核心做掉：**部門決定房間，角色不再決定**。

### 串流上多帶兩個欄位

`AgentView` 與 `AGENT_CREATED` / `AGENT_ASSIGNED` 多了 `office_zone_key` 與 `business_unit_key`。團隊**繼承上層部門**的兩者（Research 是 Newsroom 的團隊，坐在新聞室那塊地板，屬於 AI Media），所以查詢要 join 母部門。

這裡撞到一個真的效能回歸：第一版用 `coalesce(自己的事業, 母部門的事業)` 當 join 條件，快照從 24 毫秒掉到 **205 毫秒**——那種 join 沒有索引幫得上。改成只取需要的欄位、事業另外一次小查詢（一間公司的事業是個位數），回到 24 毫秒。`test_snapshot_is_fast` 就是為了這種事存在的。

### 座位：部門先，角色只是偏好

`assignSeats` 現在先看 `office_zone_key` 決定房間，在房間裡**優先坐為它的角色打造的那張桌子**——所以新聞室看起來和原本設計的一模一樣，但規則底下已經是組織的。連帶：

- 第三個研究員不再無家可歸（原本 researcher 只有兩張桌），它會坐研究區的其他空桌；
- 沒有組織圖的公司照 v1 的規則走（角色自己的區），這是誠實的答案；
- 房間坐滿才溢位到彈性座位，再滿才列為「未排座位」。

### 可以走進去的部門

- **部門列**（`features/office/Departments.tsx`）：一個部門一顆按鈕，帶人數；按下去相機飛到那個房間（`zoneBox`），再按一次或按「整層」就退出來。
- **網址**：`?department=<key>`，可以分享一個「直接進到編輯部」的連結。
- **數字鍵跟著房間**：以前 1–6 是「第 n 個角色」，現在是「這個房間裡由左到右第 n 個人」；在整層時就是全公司。寫死一張角色表，公司一有自己的部門就會指錯人。
- **2D 備援**：一列一個部門（不再是寫死的四列），沒有編制的人自己一列——不是丟掉。

### 名字來自伺服器

§14.7 另外要求：**顯示名稱要來自伺服器，不是前端的字典**。部門列與 2D 備援的名字都來自 `GET /api/companies/{id}/org`（新增 `orgQuery`）；前端只替自己畫的那幾塊地板留了名字，給還沒有組織圖的公司用。

### e2e 抓到一個真的 bug

`?department=newsroom_writing` 開進去是不會生效的：兩個 effect 互踩——「把狀態寫回網址」在名冊串流到達之前就先把參數刪掉了。改成用一個 ref 記住「這個 key 是從網址套用過的」，於是「使用者離開房間」與「連結要求進入房間」不再互相誤判。**本機跑 e2e 才發現的**，單元測試看不到這種時序。

新聞室的 e2e 也順勢改了：它原本按「3」表示「第三個角色（寫手）」，現在改成**先進編輯部、再按 1**——正好是這一批新功能的用法。

### 驗證
- `vitest src/office3d/scene/layout.test.ts`：13 個（多了部門決定房間、沒畫過的房間退到彈性座位、房間坐滿的溢位順序）。
- `vitest src/features/office/departments.test.tsx`：5 個（部門與人數、沒有組織圖時用地板、進入與離開、進入不改變選取、沒人編制就不顯示）。
- `vitest src/office3d/interaction`：8 個（數字鍵在房間內與整層的兩種意思）。
- `playwright office.spec.ts`：8 個全過，含新增的「進部門 → 網址 → 房間裡的人」。
- `playwright newsroom.spec.ts`、`realtime.spec.ts` 通過。
- 後端 1273 個測試、web 287 個測試通過；六項 check 全過。

### 還沒做
事業著色（同一事業的部門同色）、跨部門交接的信差路徑（目前仍是同一層樓的走道，§14.7 說要嘛走到門口消失、要嘛改成不走路的提示）。這兩件事需要動場景幾何，留在 3D 的下一輪。

→ 已在下面的〈第四批〉補上。

---

## T-605a 增量 · CEO 決定要做哪一門生意

T-611 把機會、提案與八個命令都建好了，也把機會放進 CEO 的快照，但 CEO 的覆盤 schema 沒有「機會」那一段——**看得到，卻說不出決定**，所以那八個命令實際上只有人會用。這一輪把最後那段接起來。

### 覆盤多一段

`CycleReview` 多了 `opportunities`，每一項是一個決定：

| 決定 | 意思 | 送出的命令 |
|---|---|---|
| `watch` | 這一輪沒有新東西，維持原狀（可以順便打分） | 無 |
| `evaluate` | 值得好好看一看 | `AdvanceOpportunity → EVALUATING` |
| `validate` | 值得用一個小額、有停損的專案去驗證 | `AdvanceOpportunity → VALIDATING` |
| `reject` | 這不是一門生意，並且說出為什麼 | `RejectOpportunity` |
| `invest` | 照那份提案開一門生意 | `CreateBusinessUnit`（**一律人審**） |

`watch` 是真正的決定，而且會是最常見的一個：一個這輪沒有任何新證據的機會，本來就應該原地不動。

### 兩個新驗證器

- **`opportunities_are_real_and_open`**：決定的對象必須是這間公司的，而且還開著。對一個上週已經否決的機會再下決定，等於悄悄把一個已經關上的問題重新打開。
- **`said_what_it_decided`**：覆盤寫的每一個決定（`watch` 以外）都必須對應到**這次執行真的送出過**的命令，而且步驟要對——送出 `EVALUATING` 卻寫成 `validate` 會被擋下來。這跟 `said_what_it_did` 是同一個性質、同一個理由：最便宜的謊是描述一個沒做過的決定，也是事後最貴的那個。順手把兩個驗證器共用的「這次執行送出了什麼」抽成 `_submitted()`。

### 模擬的 CEO 也會了（而且刻意不會做兩件事）

模擬腳本現在會**替最值得看的那個機會打分**（分數來自訊號數，不是憑空生出來的自信），並把還在 `DISCOVERED` 的那個推進到 `EVALUATING`。

它**永遠不否決、也永遠不投資**——一段腳本不該是「關掉一個問題」或「投下公司資本」的那個東西。真實模型當然可以，權限與人審都已經在原位。

### 一個會跑的證明

`test_the_company_decides_about_an_opportunity_by_itself`（加速時鐘，約 4 秒）：人記下一個機會、加一筆訊號，然後走開。一輪 cycle 之後——

- 機會變成 `EVALUATING`、有了分數；
- `commands_log` 裡剛好兩筆：`ScoreOpportunity`、`AdvanceOpportunity`，**actor 是代理、role 是 ceo、結果都是 done**；
- cycle 的 `review.opportunities` 寫著 `evaluate`，指著同一個機會。

沒有人按任何東西。

### 驗證
- `pytest tests/company/test_ceo.py`：23 個（新增 5 個：覆盤可以決定機會、不能宣稱沒送出的決定、`watch` 不需要命令、送錯步驟會被抓、已決定或別家公司的機會被擋、摘要說出看了幾個）。
- `pytest tests/acceptance/test_autonomous.py`：5 個。
- 後端 1279 個測試、web 287 個測試通過；六項 check 全過。

### 還沒接上的
`invest`（`CreateBusinessUnit`）在提示、驗證器與權限上都到位了，但模擬不會走到那一步，所以**「從提案開出一門生意」目前只有人測過**（`test_business_verbs.py` 裡有）。要讓它在模擬裡自己走完，需要一個會寫提案的代理——那是探索專案的工作，還沒有。

→ 已在下面的〈會寫提案的代理〉補上。

---

## T-612 · 誰付錢給這間公司

D-017 把這件事延後了，理由是「現在沒有收入，做了只是空表」。那個理由今天仍然成立——**這間公司還是一塊錢都沒賺到**。改做的理由是另一件事：它是 `ARCHITECTURE_V2_1` §7 那七種切法裡唯一缺的一塊，而且只是一張表加一個可空欄位；等第一塊錢進來那天才補，補的是一次 migration 加一次回填。這個取捨記在 D-018。

### 一張刻意很小的表（migration 0032）

```
customers: id, company_id, business_unit_id, external_ref, kind, acquired_at, churned_at
transactions: customer_id   -- 新增，可空
```

**它不存任何個資，而且這是設計本身**：沒有姓名、沒有 email、沒有地址、沒有卡號，只有 `external_ref`——金流商那邊的 id。能識別一個人的東西留在金流商那裡，那裡本來就有保護、刪除請求也是寄到那裡。公司需要知道的是「這個客戶賺我們多少、什麼時候走的」，這兩件事都不需要名字。

有一個測試直接斷言欄位集合，並且檢查沒有任何欄位名長得像 `name` / `email` / `card` / `ssn`——**多一個可疑欄位就紅**。這種邊界靠註解守不住。

**刻意不做**（與 D-017 一致）：發票、稅務、應收應付、對帳、多幣別換算。等到收入多到需要它們的時候，它們會是自己的東西，不是把這張表撐大。

### 兩個操作，幾個查詢

- `acquire()` **依金流商的 id 冪等**——webhook 送兩次不該變成兩個客戶，而兩次送達唯一一致的東西就是那個 id。
- `churn()` **不刪列**：他們付過的錢仍然發生過。事件裡帶 `days`（待了幾天），那是判斷一門生意好不好的數字，順手記下最便宜。
- `revenue_by_customer()`：§7 最後那個沒答案的問題。**沒有客戶的收入不計入**——問題是「哪些客戶賺我們多少」，把匿名收入悄悄加進去是在回答另一個問題。
- `paying(at=...)`：某個時刻有幾個人在付錢，可依事業切。

### KPI 多一個數字

`kpi_snapshots.metrics` 的核心指標多了 `customers`（公司與事業兩個 scope；專案沒有自己的客戶）。這仍然符合「核心只存用錢、時間、組織度量的東西」：**幾個人在付錢，換哪個產業意思都一樣**。

### 驗證
- `pytest tests/company/test_customers.py`：9 個（欄位裡沒有個資、同一個金流 id 是同一個客戶、兩家公司可以有同一個 id、空 ref 不是客戶、churn 保留資料與天數、資料庫擋住「走得比來得早」、依客戶分收入與時間窗、某時刻在付錢的人數、KPI 依事業計數）。
- 後端 1288 個測試、web 287 個測試通過；六項 check 全過。

### 現在可以答、但還沒有資料的
七種切法全部答得出來了——每個事業、每個產品、每個專案、每個代理、毛利、現金流、**每個客戶**。全部回傳零，因為還沒有人付過錢。那正是「邊界先留好」的意思。

---

## 會寫提案的代理 · 商業迴圈的最後一個洞

到上一輪為止：機會可以被發現、打分、推進，提案可以被核准變成事業——但**沒有任何東西會寫那份提案**。整條迴圈只有在「人自己打一份」的時候才接得起來。這一輪補上。

### 先補兩個命令

代理要改變公司只有一條路：命令管線。而管線裡**沒有「寫提案」這個動詞**，所以在寫代理之前得先有 `DraftProposal` 與 `SubmitProposal`。兩個都是 **CEO 與 strategist 可以自己做**——它們產出的是文件，不是承諾；承諾是 `CreateBusinessUnit`，那一個一律人審（§6）。

### `strategist`：公司層的代理，不屬於任何領域

「我們要賣什麼、賣給誰、賣多少錢、什麼情況下不值得做」——**換哪個產業都是同一個問題**，所以它跟 CEO 一樣住在 `company/agents/`，刪掉所有領域它照樣工作。

四個驗證器守住它可能說的四種謊：
- 機會必須是這間公司的、而且還開著；
- 它說的那份提案必須存在、屬於那個機會、**而且是這次執行寫的**（`authored_by_run_id`）；
- 說「已送出」就必須真的送出；
- 提案必須有 kill criteria，而且**至少寫出一個風險**——沒有風險的提案不是想過，是希望過。

### 探索就是專案（§4 的兌現）

`company/exploration.py` 在 EXECUTING 進入時啟動工作，一輪**只開一條**：拿分數最高、正在 EVALUATING、還沒有活著的提案的那個機會，替它開一個 `Explore: <title>` 專案（`opportunity_id` 指回去），再跑 `company.business_proposal_v1`。

於是三件事自動成立，而這正是「探索是 Project」的理由：預算由 CEO 用 `AllocateExplorationBudget` 給、成本自動歸到那個機會、**停損是專案本來就有的 kill criteria**（預設超過 2 美元自動暫停）。沒有新機制。

一輪一條也不是後加的節流閥，是 §4 的規則：慢迴圈一天只走一步。

### 會跑的證明

`test_an_opportunity_becomes_a_proposal_a_person_can_decide`（約 7 秒）：

- 第一天：CEO 覆盤時把人記下的機會打分、推進到 EVALUATING；
- 第二天：公司自己開探索專案，strategist 寫出提案並送出；
- 斷言 `PROPOSAL_DRAFTED` / `PROPOSAL_SUBMITTED` **沒有任何一筆的 actor 是人**；
- 然後人核准 `CreateBusinessUnit`——事業開出來、機會變成 APPROVED、指向那門生意。

**兩個人的動作之間，全部是公司自己做的。**

### 三個真的 bug（都是跑起來才發現的）

1. **strategist 沒有 `submit_command` 的權限**：權限矩陣只給了 ceo。它的第一次執行是 `PolicyDenied`。順手把 `strategist` 加成權限矩陣的第九欄，所有列預設 D。
2. **`Context` 沒有 `run_id`**：處理器要把 `authored_by_run_id` 寫進提案，但管線沒有把「是哪次執行問的」交給處理器——工具回傳 `AttributeError`，命令一筆都沒進 log。加上 `Context.run_id`（人審通過後執行的那條路徑**刻意留 None**：那時是人在執行，不該把寫入掛到代理的執行上）。
3. **模擬讀錯了工具輸出的形狀**：`submit_command` 把命令的結果**攤平**在輸出裡，不是包在 `result` 底下，所以模擬拿不到 `proposal_id`，提案寫了卻沒送出。

### 第四次了：測試查詢沒篩公司

`test_a_decided_proposal_cannot_be_decided_again` 查 `PROPOSAL_DECIDED` 事件時沒有加公司條件。單獨跑過、全套跑就紅——因為新的驗收測試會 commit 同名事件。**這是我第四次犯同一個錯**（前三次在 `test_organization.py`、`test_commands.py`、AC-9 的事件查詢）。這次把這一輪寫的四個測試檔全部掃過，補上四處。

### 驗證
- `pytest tests/company/test_strategist.py`：11 個（它只有一個工具且不能開事業、草稿與送出、沒有 kill criteria 會被拒、已決定的機會不再寫提案、不能報告別的執行寫的提案、不能說送出了還是草稿的東西、別家公司的機會、一輪一條且挑分數最高的、已有提案的不再開、沒有 strategist 就不開、DISCOVERED 還不到寫提案的時候）。
- `pytest tests/acceptance/test_autonomous.py`：6 個。
- 後端 1359 個測試、web 287 個測試通過；六項 check 全過。

---

## T-600 第四批 · 事業著色與跨部門交接

§14.7 剩下的兩件事。

### 一眼看得出這間公司有幾門生意

每個有人的房間，地板靠門那一側多一條**事業色帶**（`BusinessBands.tsx`）。顏色不隨主題變——它標的是事業，跟角色色一樣是身分不是裝飾——而且**按 key 排序指派，不是 hash**：兩個相鄰的顏色必須分得出來，用 hash 等於交給運氣。公司再開第二門生意，第一門的顏色不會跟著換。

**共用職能沒有色帶**：Executive 與 Finance 不是一門生意，地板不該假裝它們是。

同一個顏色在三個地方一致：3D 地板、2D 備援的每一列、部門列的小圓點。

### 跨部門交接：走到門口，不走進別人桌前

原本信差一律走到「那個角色的第一個人」的桌邊。有了部門之後這是錯的兩次：

1. 下一個任務是**誰有空誰認領**，走到某一張特定的桌子等於畫出一個可能沒發生的交接；
2. 部門一旦變成真正的房間，那條路徑根本不存在。

現在的規則很短：**接手的人就在同一個房間 → 走到他桌邊（照舊）；在別的房間 → 走到那個房間的門口，交完回來。** `doorOf(zone)` 給出每個房間從走道進入的那個點（CEO 室是玻璃隔間上的那道門）。

沒有人擔任的角色仍然走到floor plan替它留的桌子——那不是交接對象的問題，是這層樓本來就畫了那張桌子。

### 驗證
- `vitest src/office3d/scene/layout.test.ts`：15 個（新增：每個房間的門都在走道裡、不壓到家具、走到門口的路徑不出牆）。
- `vitest src/office3d/scene/bands.test.ts`：6 個（顏色由 key 排序決定、超過色盤就重複、一事業多房同色、共用職能沒有色帶、畫不出來的房間不生色帶、色帶在房間內）。
- `vitest src/office3d/visual/director.test.ts`：18 個（新增：同房走桌、跨房走門、沒人擔任的角色、兩次同一道門只走一次）。
- `vitest src/office3d/fallback`：12 個（2D 的事業色）。
- `playwright office.spec.ts + newsroom.spec.ts`：9 個全過（本機）。
- web 293 個測試通過；`lint-web`（含 typecheck）通過。

### 這一批沒做的
房間**沒有變成真正的隔間**——它們仍然是同一層樓上的區域，只是現在有門、有色帶、可以進入。真正的「走進去看到另一批人」需要重畫場景幾何，那是一個比這大得多的改動，而且目前這層樓對六到十個代理是夠的。

→ 「進去只看到這個部門的人」已在下面的〈第五批〉補上。

---

## 修掉 `ApprovalKind.ARTICLE` · 執行層不該知道什麼是文章

T-611 補上的詞彙掃描列了七處違規，這是其中最實在的一處：**`approvals.kind` 是一個固定清單的 enum，而清單裡有 `article`**。import-linter 攔不到它，因為那是一個字串——正是 `ARCHITECTURE_V2_1` §9 說的那種耦合。

### 做法：它是一個詞，不是一張清單

照這個儲存庫本來就有的先例——`transactions.category`——處理：

- `ApprovalKind` enum 保留**執行層自己**要問的五種（工具呼叫、命令、專案、終止、策略），拿掉 `article`；
- `approvals.kind` 的資料庫約束從「在這張清單裡」改成「**是一個小寫 token**」（migration 0033）；
- 事件契約的 `ApprovalKind` 從 `Literal[...]` 變成 `str`；
- 新聞室宣告自己的詞：`ARTICLE_APPROVAL = "article"`，就在它用的地方。

**既有資料一列都不用動**：`article` 還是 `article`，只是它現在是新聞室的詞，不是執行層的。

### 連測試也不該知道

原本三個 runtime / realtime 的測試用 `ApprovalKind.ARTICLE`。把它們改成 import 新聞室的常數會是同一個錯誤搬個地方，所以它們各自宣告了一個**自己編的** kind（`"shipment"`）——那反而證明得更乾淨：執行層儲存任何 token，而且根本沒聽過新聞室的詞。

### 棘輪少了兩格

`test_core_vocabulary.py` 的 `KNOWN_LEAKS` 從七條減到五條（`runtime/events/catalog.py` 與 `db/models/runtime.py` 的三條走了）。這個測試會在**變多時紅、也會在變少而沒更新清單時紅**，所以這次的改動必須同時刪掉那三行——這正是它的用途。

### 剩下的五條
`Company.type` 的 `newsroom`（產業分類，比較像資料）、`infra/settings.py` 的 `story_match_threshold`、fetcher 的 User-Agent。另外前端的審批頁還有一張 `KIND_LABEL` 字典帶著 `article: "文章"`——那是一個翻譯，而且不認得的 kind 會直接顯示 token，不會壞；但它確實還是前端知道了一個領域的詞。

### 驗證
- 後端 1359 個測試、web 293 個測試通過；六項 check 全過（含重新產生事件契約與 OpenAPI）。

---

## 修掉 `story_match_threshold` 的耦合 · 核心不該有新聞室的旋鈕

棘輪上的第二條：`infra/settings.py` 裡有 `story_match_threshold`——「兩則新聞像到什麼程度算同一件事」。**對一間賣軟體的公司來說，這個欄位毫無意義**，核心不該帶著它。

### 核心解析環境，領域定義意思

跟 KPI 掛鉤、跟 `approvals.kind` 同一個做法：核心提供**機制**，領域提供**意思**。

`infra.settings` 把「.env 在哪裡」變成公開的 `env_file()`——核心知道設定檔在哪，這是它的事；`domains/newsroom/settings.py` 用同一個檔、自己的前綴 `NEWSROOM_`、自己的驗證，宣告 `story_match_threshold`。第二個領域照做，而且永遠不必碰到 `infra/settings.py`。

環境變數因此從 `STORY_MATCH_THRESHOLD` 改成 **`NEWSROOM_STORY_MATCH_THRESHOLD`**（它沒有出現在 `.env.example`，只是一個預設值，影響面很小；新名字也比較誠實——它說得出這是誰的旋鈕）。`RUNBOOK` 已同步。

### 順手把測試也修對

`test_threshold_setting` 本來是「核心的 Settings 有這個值」。現在它測的是**新聞室自己的設定類別**：預設是 0.65、可以被覆寫、超出範圍會被自己的驗證擋下來。門檻的理由（0.65 是量出來的，不是挑出來的）跟著搬到領域裡，`test_embed_live.py` 換模型時仍然會重新檢查它。

### 棘輪 5 → 4

剩下：`Company.type` 的 `newsroom`（產業分類，比較像資料，該不該動要先有個決定）、fetcher 的 User-Agent。前端審批頁那張 `KIND_LABEL` 字典不在這個掃描範圍內。

### 驗證
- 後端 1359 個測試通過；六項 check 全過。

---

## 修掉 fetcher 的 User-Agent · 抓網頁的不是新聞室，是這套軟體

棘輪上的第三條：`infra/http/__init__.py` 裡寫死 `AutoraNewsroom/0.1`。fetcher 是共用服務——一間賣軟體的公司照樣會抓網頁——它不該用某個領域的名字自我介紹。

### 預設是產品的名字，要說更多由呼叫的人說

- 核心的預設變成 `Autora/{__version__} (+repo)`；
- `HttpFetcher(user_agent=...)`：**誰在抓，是呼叫者的事**。新聞室在自己的設定模組裡宣告 `AutoraNewsroom/0.1`，組裝根把它傳下去——輪詢 RSS 的時候，對方的維運人員真的會看這個字串，說出是哪一門生意在問是有禮貌的。

順手改掉一件行為：User-Agent 本來只在 fetcher **自己建立 client** 時才設定，外面注入 client（測試與未來的接線）就沒有了。現在改成**每個請求都帶**，所以注入 client 也一樣會自我介紹。

### 這個洞本來沒有測試

——它洩漏了那麼久，正是因為沒有人檢查過送出去的是什麼。現在有了：預設帶 `Autora/`、而且裡面不含 newsroom；呼叫者給了就用呼叫者的。

### 棘輪 4 → 2

只剩 `Company.type` 的 `NEWSROOM` 與 `newsroom` 兩條——那是一張「公司可以是哪種產業」的分類表，比較像資料而不是詞彙，**該不該動需要先有一個決定**（D-016 / D-017 都沒有裁定過）。我不打算自己把它刪掉。

### 驗證
- `pytest tests/newsroom/test_sources.py`：32 個（新增 1 個：fetcher 說得出是誰在抓）。
- 後端 1360 個測試、web 293 個測試通過；六項 check 全過。

---

## 決定 `Company.type`（D-019）· 拿掉，不是改寫

棘輪上最後兩條都是同一個欄位：`companies.type`，一張「公司可以是哪種產業」的封閉清單（newsroom / saas / research / ecommerce / software_studio）。

先查它到底被誰用：

- **沒有任何程式分支讀它**——後端前端都沒有 `if company.type == ...`；
- **前端從不顯示它**；
- **資料庫裡每一列都是 `newsroom`**，包括產業中立驗收測試建立的、根本沒有新聞室的那幾間公司——**這個欄位在我們自己的測試裡就已經在說謊**。

### 真正的問題不是詞彙，是 v2 讓它變錯了

v2 之後公司是一個**事業組合**：同時經營 AI Media 與 AI Education 的公司，`type` 要填什麼？「做哪個產業」屬於 `business_units`，不屬於公司。`newsroom` 出現在核心只是症狀。

所以三個選項裡——保留並豁免、改成自由字串（像 `approvals.kind`）、整欄拿掉——選**拿掉**。改成自由字串會留下一個 v2 之後形狀就不對的欄位。

### 兩個保留

- **事件 `COMPANY_CREATED.type` 留著，但改成可空、而且不再寫入。** 已經記下的事件帶著舊值；把欄位從 schema 刪掉會讓讀的人分不出「沒有」和「錯的」。
- **資料不保留**：全部是同一個值，而且沒有一個為真。

日後真的需要機器可讀的產業標籤時，它加在 `business_units` 上，而且意思由那門生意定義（§9）。

### 棘輪歸零

`test_core_vocabulary` 的 `KNOWN_LEAKS` 空了，於是整個棘輪機制也拿掉了：`runtime`、`db`、`infra` 從「有清單的例外」升級成跟 `company`、`realtime` 一樣的**嚴格檢查**。**領域以下的每一層，現在都不再出現任何領域詞彙。**

（前端審批頁那張 `KIND_LABEL` 字典裡還有 `article: "文章"`，不在這個掃描範圍內；它是翻譯，而且不認得的 kind 會顯示原始 token。）

### 驗證
- 後端 1361 個測試、web 293 個測試通過；六項 check 全過（含重新產生 OpenAPI 與前端型別）。

---

## T-600 第五批 · 進到部門裡，就只看到這個部門的人

`ARCHITECTURE_V2` §14.7 那句話是：**公司樓層（各部門是可進入的區域）→ 進入部門 → 看到該部門的代理**。第三批做了前半（可進入、相機進到那個房間），這一批做後半。

進入一個部門之後，3D 只畫**這個部門的人**。其餘的公司還在——桌子、地板、色帶都在，走進去的是同一層樓——但別的部門的頭像不畫了。理由很直接：**進房間就是為了看這間房間的工作**，背景裡還站著八個人的話，那正是操作者想避開的東西。

### 沒有人會無聲消失

部門列會說：「其他部門 N 人不在畫面上」。一個介面可以少畫東西，但不可以讓人**以為那些人不存在**。

### e2e 抓到我漏掉的一半

改完 `Agents` 之後單元測試全綠，e2e 卻說進了房間還是 7 個頭像——因為**頭像不是 `Agents` 畫的**，是 `StatusIndicators` 的 `HeadTags` 自己從名冊拿的。兩邊現在共用同一個 `membersInRoom()`。

這種漏掉一半的錯誤，單元測試看不到（兩個元件各自都「對」），**只有把整個場景跑起來才會現形**。

### 驗證
- `vitest src/office3d/agents/rooms.test.ts`：4 個（整層看得到所有人、房間裡只有自己的人、沒有組織圖的公司用地板區塊進入、沒有人的房間是空的不是壞掉）。
- `vitest src/features/office/departments.test.tsx`：7 個（新增：說出其他部門有幾個人、只算別的房間）。
- `playwright office.spec.ts`：10 個，新增的那個在**真的 3D 場景**裡數頭像：整層 7 個 → 進研究部 2 個 → 退出來又是 7 個。
- web 297 個測試通過；`office`、`newsroom`、`realtime` 三個 e2e 檔 12 個全過（本機）。

### 仍然沒做的
**玻璃隔間**。房間現在靠「只有這裡有人」讀出來，不是靠牆。要加隔間就得同時改同房間內的走路路徑（現在信差是繞到走道再繞回來的，會穿過玻璃），而我**看不到畫面**、沒辦法確認視覺結果——所以我不在這裡動場景幾何。這件事需要有人看著螢幕做。

---

## 真實模型的 soak · 七天，四小時，一個設計缺口

到目前為止所有「自主」的證據都是模擬模型給的。這一次跑真的：`MODEL_PROVIDER=nvidia`、`z-ai/glm-5.3-flash`（D-006 的免費端點），公司自己的兩個代理（CEO 與 strategist），加速時鐘、真實模型。

`backend/tests/acceptance/test_autonomous_real.py`，標記 `integration`，預設不跑：

```bash
DATABASE_URL=<dev url, 資料庫改成 autora_soak> \
    .venv/bin/pytest backend -m integration -k autonomous_real -s
```

新聞室的生產線**刻意不在這間公司裡**：真跑一篇要幾小時（T-519 給 45 分鐘跑一篇），七天份證明不了這個測試要證明的東西。這裡測的是**公司自己的**代理：規劃、探索、覆盤。

### 結果（7 輪，4 小時 7 分）

```
cycle 1: DONE plan=ceo      review=ok
cycle 2: DONE plan=ceo      review=ok
cycle 3: DONE plan=ceo      review=missing
cycle 4: DONE plan=ceo      review=missing
cycle 5: DONE plan=fallback review=missing
cycle 6: DONE plan=fallback review=missing
cycle 7: DONE plan=fallback review=missing

41 次模型呼叫：30 成功、11 失敗（全部是 909 秒後 504）
成功的平均 159 秒，最久 808 秒
plan 21 成功 / 3 失敗　review 4 / 6　propose 5 / 2
```

**迴圈撐住了**：七輪全部 DONE，沒有一輪卡住，中途沒有任何人介入。每一輪都有計畫（CEO 的或 fallback 的）、都被規則看過。T-607 的 fallback 與「覆盤不見要說出原因」在真實環境裡真的被用到了——**而且是主要的那條路徑**。

**公司自己的代理在真模型下會動**：四輪由 CEO 規劃、兩輪有覆盤，strategist 寫出並送出了一份真的提案——《AI English Speaking Coach — prepaid 1-week sprint for adults》。那是模型自己想的，通過了 schema 與四個驗證器。

管線也照樣拒絕它：`AllocateBudget` 被拒 5 次、`CreateProject` 4 次要人審 4 次被拒、`DraftProposal` 被拒 2 次。**模型亂要東西的時候，公司說不。**

### 這次 soak 最重要的產出：治理把 CEO 停掉了

第 5 輪之後全部是 fallback，原因不是模型變笨：

```
AGENT_PAUSED  reason="3 runs in a row failed"  actor=governance
agents: ceo=paused, strategist=active
執行失敗分類：ProviderError 11、MaxStepsExceeded 1
```

**供應商連續逾時三次，公司就把自己的 CEO 停職了。** T-606 的規則是「一個做不好自己工作的代理，停掉比一直重試便宜」——但它現在把**「代理壞了」**和**「端點掛了」**當成同一件事。這兩者要做的決定完全不同：前者該停，後者該等。

這是真實模型才會暴露的缺口，模擬模型永遠不會 504。

**我沒有直接改它**，因為這是一個有判斷成分的語意決定（也有人會說：不管為什麼，一個跑不動的 CEO 就是跑不動）。建議的修法是治理只數**代理自己的失敗**（驗證器、工具、輸出），`ProviderError` 這類基礎設施失敗不計入——需要一筆 D-0xx。

→ 已決定並實作：**D-020**，見下。

### 另外兩件事
- **這個免費端點跑不動一整天的迴圈**：成功呼叫平均 159 秒，四分之一的呼叫掛 15 分鐘後 504。D-006 當初在單次呼叫上的結論，在公司規模上又出現一次。
- **CEO 沒有替機會打分**：它直接把機會推到 EVALUATING（`AdvanceOpportunity done 1`），`score` 仍是 null。模擬的 CEO 會先打分——真模型選了別的做法，而這是允許的。

### CI 抓到我的 e2e 寫錯

那一批（第五批）的 e2e 在本機過、在 CI 紅：我用「整層的頭像數 − 2」去比對「其他部門 N 人」，但**這是兩個不同的數**——頭像數的是**有座位的人**，而「其他部門」數的是**有部門的人**。組織圖還沒安排的代理會被畫出來、卻不屬於任何部門，兩邊就對不上。本機與 CI 的種子資料剛好差一個人，所以只有 CI 紅。

改成拿部門列自己的數字相加（用 `aria-pressed` 認出正在進入的那一間），不再自己算。**同一個畫面上的兩個數字要對得起來**，才是這個測試該守的東西。

### 驗證
- 測試本身斷言的是**迴圈的承諾**（七輪都 DONE、都有計畫、都被治理看過、第一輪之後沒有人介入），對模型的表現只寬鬆斷言「至少有一輪 CEO 真的規劃過、至少有一輪有覆盤」。**一個每輪都失敗的模型是一個結果，不是一個壞掉的測試。**

---

## D-020 · 供應商掛掉不是代理的錯

真實模型 soak 抓到的那個缺口，現在補上了。

`NOT_THE_AGENT_S_FAULT = ("ProviderError", "LeaseExpired", "BudgetExhausted", "Aborted:")`——這四類失敗**不說明代理能不能做它的工作**：模型供應商逾時、worker 被殺掉租約過期、公司的預算用完、任務管理器中止了這次執行。

關鍵設計：**跳過，不是當成成功**。治理往回看的時候把這些略過，底下那一串它自己的失敗仍然數得到——所以「連續三次自己搞砸」照樣被暫停，中間夾幾次 504 都一樣。前綴比對是因為中止會帶原因（`Aborted:timeout`）。

四個測試守住四個角度：三次 504 不暫停、其他三種基礎設施失敗也不暫停、**故障夾在中間不會把它自己的三次藏起來**、故障底下的一次成功仍然打斷連續。

**沒有改的**：人還是可以手動暫停任何代理；供應商故障照樣記在 `agent_runs.error` 與 `model_calls` 裡——它只是不再被算成代理的過錯。

---

## AC-9 的最後一句 · 從收件匣重新啟動失敗的流程

`RestartWorkflow` 這個命令在 T-604（補-2）就做好了、也有測試——但**畫面上沒有任何地方會呼叫它**。一個人打開收件匣，看不到失敗的流程，也按不了任何按鈕。AC-9 的最後一句一直沒有兌現。

### 收件匣多一段

`GET /api/companies/{id}/workflows/failed`：失敗的執行、哪幾個步驟失敗、**有沒有人已經重新啟動過**。

最後那一欄本來我是用「同樣範本、同樣參數、時間更晚的執行」去猜的——那是錯的：兩個長得一樣的執行不能證明其中一個是另一個的重啟。改成查 `commands_log` 裡那筆 `RestartWorkflow`。**紀錄本來就存在，不需要用相似度去猜。**

`POST /api/companies/{id}/workflows/{run_id}/restart`：**走命令管線**，不是後門。所以它會被決定、被記錄（誰按的）、而且**受同一個每輪流程上限約束**——不管按的是人還是 CEO，暴衝就是暴衝。

### 畫面說出公司的回答

按鈕不假設會成功：`已重新啟動`／`等待有人核准`／`公司拒絕了：<原因>`／網路錯誤，四種都直接顯示。一個「重新啟動」按了沒反應、也不說為什麼的介面，比沒有按鈕更糟。

舊的執行**留著**：失敗了什麼、為什麼失敗，正是留著它的理由。新的執行從頭跑，但公司已經知道的東西（題材、證據、草稿）都還在。

### 驗證
- `pytest tests/api/test_workflows.py`：11 個（新增 4 個：列出可重啟的、人透過管線重啟且舊執行保留歷史、還在跑的不能重啟、別家公司的被拒）。
- `vitest src/features/approvals`：15 個（新增 6 個：公司說了什麼的四種措辭、顯示失敗步驟並重啟、拒絕要顯示出來、請求根本沒送出也要顯示、已經重啟過、沒有失敗就整段不出現）。
- 後端 1369、web 303 個測試通過；六項 check 全過。

### 沒有用瀏覽器測到的那一段
頁面 → API 這條線是用型別、元件測試與 API 測試接起來的，**沒有 Playwright 測試真的在瀏覽器裡重啟一個失敗的流程**：e2e 的環境沒有辦法透過 API 製造一個失敗的執行（失敗旋鈕 `demo.editor_fails` 只在 Python 的模擬測試裡）。要做到就得在正式端點上開一個測試用的參數——那個代價比這段沒測到更糟，所以我沒有做，而是寫在這裡。

---

## CI 又抓到同一個房間 e2e：我拿一分鐘前的數字當承諾

`2f175cd` 的 CI 紅了，紅的不是這次改的東西，而是房間的 e2e（`office.spec.ts`「進到部門裡只畫這個部門的人」）：走出房間後期待 5 個頭像，畫面上有 7 個。

**原因不是畫面錯，是我的斷言假設錯了。** 測試一開始抓一次頭像數當作「整層有幾個人」，再拿它跟一分鐘後的數字比。這假設名冊在這段期間不會動——但名冊是活的（快照到齊、代理陸續入座），CI 上先看到 5 個、之後才是 7 個。這是我第二次在同一個測試上把兩個不同時刻的數字當成同一件事。

**改法是改承諾，不是改數字。** 辦公室答應的是「走進房間只畫這個房間的人，走出來每個人都回來」——回來的是**那些人**，不是**那個數字**：
- `settledTags()`：等頭像數不再變動，才把整層的人記下來（記的是 id，不是個數）；
- 最後一步改成「當時在場的每個 id 都回到畫面上」。名冊中途多了人也不算失敗，因為那本來就不是承諾的一部分。

一個數字快照會把「公司是活的」誤判成「畫面壞了」。用集合比，測的才是那句承諾。

## 尾-1 · 重新量 AC-S7：換掉量測對象，也換掉讀數字的方式

AC-S7 的數字是 2026-09-19 量的，量的是 echo 那家三張桌的公司。T-600 之後辦公室換過骨架——部門分區、事業色帶、房間隔離——**舊數字仍然是真的，但它描述的是一個已經不存在的場景**。

### 改了量測本身
- **改看有組織圖的公司**（示範新聞室，7 個代理、6 個房間）。量三張桌的公司等於量沒人會看的畫面。
- **每分鐘進出一個房間**，兩小時共 60 次。進房間會把其他人的頭頂標籤整批卸載，出來再整批掛回去——這正是會漏的形狀，所以讓它真的發生，而不是假設它免費。
- 加了斷言：房間裡畫的人數要少於整層（`tagsInside < tagsOutside`），否則「隔離」這件事根本沒被測到。

### 結果（2026-09-22，120 分鐘）：通過
25 輪、走路 50 趟、進出房間 60 次（六個房間都去過）；**回收後 heap 15.6 → 18.3 MB（+2.7，上限 50）**；每 10 分鐘的回收後 heap 是 17.0 → 18.1 並且**越後面越平**（17.0、17.4、17.7、17.9、18.0、18.0、18.1），是快取填滿的形狀，不是漏的形狀；**FPS 最低 58、第 10 百分位與中位數 60**；**WS p95 0.7 毫秒**（1051 則訊息，上限 100）；無頁面錯誤。完整取樣存在 `logs/perf/2026-09-22-office-soak-120min.json`。

### 差點被我寫成「監聽數漏了」
摘要裡監聽數第一筆 2349、最後一筆 3516，看起來兩小時漲了五成。**但那是我讀數字的方式錯了**：把整份取樣切四等分來看，平均是 3207 → 3165 → 3067 → 3144（持平，甚至略降），整段在 2349～4310 之間上下——因為頭頂標籤與面板一直在掛載卸載，相鄰兩筆就能差三分之一。**第一筆對最後一筆，講的是兩個端點剛好落在哪裡，不是趨勢。**

這跟同一天我在房間 e2e 犯的是同一個錯（拿兩個時刻的數字當同一件事），這是第三次了。所以這次不是改數字，是改摘要：節點與監聽數改成回報**四等分平均加上最小/最大**，讓「有沒有往上漂」跟「本來就多吵」分開來看。一個會漏的東西，在四等分平均上藏不住。

---

## 尾-3 · AC-13 端到端：寫測試的時候才發現這條驗收條件在模擬下不可能成立

每一段都有自己的測試——守門員會拒絕、`AllocateBudget` 會放回佇列——但**從來沒有人把整條鏈跑過一次**，而 AC-13 要的正是整條：

> 上限小到付不起下一次呼叫 → 呼叫在發出前被拒 → run ABORTED、task BLOCKED_BUDGET、代理 WAITING → 錢透過命令管線進來 → 工作自己回到佇列 → 做完。

### 先撞到的事：免費的模型不可能把預算用完

`router_from_settings` 在 `MODEL_PROVIDER=fake` 時把價格寫死成免費。守門員的判斷是「已花 + 這次預估 > 上限」——兩邊都是 0，**任何上限都不會被超過**。但驗收文件寫著 AC-13「在 fake 與真模型下都必須通過」。

**這條驗收條件在模擬下是無法成立的**，而且兩個階段沒人發現，因為沒有人把整條跑過。

更難看的是：這件事**早就被寫下來過**。D-005（2026-09-18）在講 NVIDIA 免費端點時就註明「價格為 0 時專案預算無法擋下呼叫」——同一個事實，只是當時談的是真實的免費端點，沒有人把它跟 AC-13 的「在 fake 下也要通過」連起來。知道一件事，跟讓它出現在該出現的地方，是兩回事。

改法很小：模擬的模型**預設仍然免費，但 `MODEL_PRICES` 有寫就照它算**。這樣預算閘門能在不花真錢的前提下被真的測到。

**我有驗證這個改動是承重的**，不是裝飾：把 `router.py` 的改動暫時收起來再跑一次，測試在第 104 行掛掉——一次拒絕都沒有發生，跟預測的一樣。一個「拿掉之後測試照樣綠」的改動，等於沒有被測到。

### 再來：驗收條件有兩句跟系統不符，而系統是對的

| AC-13 原文 | 系統實際 | 誰該改 |
|---|---|---|
| 「agent FAILED{BudgetExceeded}」 | 代理是 **WAITING{budget}**、run 是 ABORTED | 改文件。錢用完不是代理**工作**失敗——跟 D-020（供應商掛掉不是代理的錯）同一個判斷 |
| 「加預算後恢復」（像是有人去重排） | `AllocateBudget` 這個命令**自己**把被擋住的任務放回佇列 | 改文件。補-3 做的就是這個，文件還停在它之前 |

兩處都改文件去符合系統，不是反過來。

### 測到了什麼
`backend/tests/acceptance/test_budget_gate.py` 一個測試走完整條，其中四個斷言是這次才真的被檢查的：
- **被拒的呼叫沒有抵達供應商**（`model_calls` 是空的）——「在發出前擋下」跟「發出去才算錢」是兩件事；
- **只拒絕一次**，不是每次重試都記一筆；
- **沒有任何 TASK_FAILED**——等待不是失敗；
- 加預算後 `released_tasks` 正好是那個被擋住的任務 id，**然後沒有人再碰它，工作自己做完**。

---

## 尾-2 · 真的刪掉新聞室一次：那句驗收條件是假的，而每個原因都是對的

AC-S5 最後一句寫著「刪除 `domains/newsroom` 後 runtime/company/realtime 測試全綠」。`lint-imports` 保證的是「沒有 import」，**不等於「拿掉之後還會動」**——這件事只能做一次實驗來回答，而兩個階段以來沒有人做過。

基準線：那三個目錄 998 個測試通過。刪掉之後：

| 撞到什麼 | 為什麼 | 是不是分層破了 |
|---|---|---|
| `alembic upgrade head` 失敗 → 測試資料庫建不起來（runtime 563、realtime 28 個 error） | 遷移的 `env.py` 呼叫 `app.load_models()`，它要 import 每一張資料表，包含新聞室的 | **不是**。遷移本來就得看到所有資料表 |
| `tests/company` 整個載不進來 | conftest 串到 `tests/api/conftest.py` → API app → 新聞室 router | **不是**。router 在 app 層，那一層本來就可以知道領域 |
| `test_codegen` 4 個失敗 | 產生給前端的事件型別是**所有領域事件的聯集**，少一個領域當然對不上 | **不是**。產物本來就該跟著註冊的事件變 |

**runtime 有 154 個測試照樣通過**（不需要資料庫的那些），而且**沒有任何一個失敗是因為底層 import 了領域**。守著那條線的 `lint-imports` 是好的。

### 順手查出程式跟自己的文件不一致
`domains/__init__.py` 寫著「Each subpackage exposes `register(runtime)`」。查下去：新聞室**有**（服務、審批回呼、任務掛鉤、編輯台的階段掛鉤），echo **沒有**，而且組裝根另外還有約 19 處直接 import——因為 models metadata、事件目錄、policy、templates、tools、KPI、排程、設定這些，**在 Runtime 物件存在之前就要接好**，`register(runtime)` 接不到。

所以那句 docstring 不是完全錯，是**只描述了兩種接線裡的一種**，讀的人會以為領域是可插拔的。改成把兩種都說清楚。

### 決定：D-021，不做動態領域註冊
把 AC-S5 改寫成它真正想說、而且真的被守著的那件事，並記下為什麼現在不做註冊表：只有一個領域時，註冊表買到的只是讓一句文件成真，代價是註冊順序要另立規則、接錯從 import 期延到執行期、「現在到底接了什麼」不能再用 grep 回答。

**重要的是這個選項沒有關掉**：改不改得成，取決於領域知識有沒有散到組裝根以外——而這正是 `lint-imports` 每次 CI 在守的。等事業迴圈真的開出第二個領域，「刪掉一個領域公司還能開」才是有人要用的性質，那時再做，也才測得有意義。

### 這一節最值得記的一句
一條沒有人執行過的驗收條件，跟沒有這條驗收條件，價值是一樣的——而且更糟：它讓人以為某件事被保證了。這三件（尾-1 的舊數字、尾-3 的免費模型、尾-2 的這句話）全都是這種東西，三件裡有兩件根本不成立。

---

## 提交紀錄

| 提交 | 日期 | 內容 | 持續整合 |
|---|---|---|---|
| `cd5fe2a` | 2026-09-22 | 尾-2：真的刪掉新聞室一次，AC-S5 最後一句不成立（原因全是正確行為）；D-021 不做動態領域註冊 | ✅ 執行編號 `35714020459`（e2e 5 分 24 秒、python 4 分 9 秒、web 1 分 21 秒） |
| `fecdec3` | 2026-09-22 | 尾-3：AC-13 端到端；模擬模型可定價（免費的模型不可能用完預算）；修正 AC-13 兩處文字 | ✅ 執行編號 `35710462845`（e2e 5 分 23 秒、python 3 分、web 1 分 23 秒） |
| `3525e5a` | 2026-09-22 | 尾-1：AC-S7 在 T-600 之後的場景重量通過；摘要改看四等分平均（差點記下不存在的漏洞） | ✅ 執行編號 `35709131329`（e2e 7 分 43 秒、python 3 分 6 秒、web 1 分 8 秒） |
| `6757146` | 2026-09-22 | 房間 e2e 改比「誰回來了」而不是「回來幾個」（名冊會邊跑邊長） | ✅ 執行編號 `35683815525`（e2e 7 分 50 秒、python 4 分 14 秒、web 1 分 23 秒） |
| `2f175cd` | 2026-09-22 | AC-9 最後一句：從收件匣重新啟動失敗的流程（走命令管線、會說出公司的回答） | ❌ 執行編號 `35683017824`（python、web 通過；紅的是房間 e2e，跟這次改的東西無關）→ 已修，見上一列 |
| `01d479b` | 2026-09-21 | D-020：治理不把供應商故障算成代理的失敗（跳過，不算成功） | ✅ 執行編號 `35621711541`（e2e 6 分 8 秒、python 2 分 46 秒、web 1 分 21 秒） |
| `8309652` | 2026-09-21 | 修掉房間 e2e 比錯兩個數字（頭像數 vs 有部門的人數） | ✅ 執行編號 `35620251073`（e2e 5 分 6 秒、python 4 分 51 秒、web 54 秒） |
| `5970f48` | 2026-09-21 | 真實模型的七輪 soak（`integration`）、RUNBOOK；發現治理把 CEO 停職 | ❌ 執行編號 `35618973131`（python、web 通過；房間 e2e 的斷言寫錯）→ 已修，見上一列 |
| `557eaa1` | 2026-09-21 | T-600 第五批：進到部門裡只畫這個部門的人；部門列說出其他部門有幾個人 | ✅ 執行編號 `35589824361`（e2e 7 分 5 秒、python 4 分 5 秒、web 1 分 18 秒） |
| `32d5220` | 2026-09-21 | D-019：移除 `companies.type`（migration 0034）；詞彙棘輪歸零、升級為嚴格檢查 | ✅ 執行編號 `35586782392`（e2e 6 分 52 秒、python 3 分 58 秒、web 1 分 19 秒） |
| `c735e78` | 2026-09-21 | fetcher 的 User-Agent 改成產品名、由呼叫者指定；每個請求都帶；棘輪 4 → 2 | ✅ 執行編號 `35544907410`（e2e 4 分 27 秒、python 3 分 49 秒、web 1 分 18 秒） |
| `8c80145` | 2026-09-21 | `story_match_threshold` 搬進新聞室自己的設定（`NEWSROOM_` 前綴）；棘輪 5 → 4 | ✅ 執行編號 `35543346720`（e2e 5 分 47 秒、python 3 分 40 秒、web 1 分 19 秒） |
| `e6b6de3` | 2026-09-20 | 修掉 `ApprovalKind.ARTICLE` 的詞彙耦合（kind 變成 token、migration 0033）；棘輪 7 → 5 | ✅ 執行編號 `35520761825`（e2e 6 分 8 秒、python 4 分 5 秒、web 1 分 16 秒） |
| `840668a` | 2026-09-20 | T-600 第四批：事業色帶（3D／2D／部門列一致）、跨部門交接走到門口 | ✅ 執行編號 `35513162230`（e2e 6 分 17 秒、python 3 分 55 秒、web 1 分） |
| `dc3232a` | 2026-09-20 | 會寫提案的代理（strategist、`DraftProposal`/`SubmitProposal`、探索專案）；三個真 bug | ✅ 執行編號 `35512740969`（e2e 5 分 57 秒、python 4 分 1 秒、web 1 分 14 秒） |
| `a920382` | 2026-09-20 | T-612 客戶（migration 0032、不存個資、依客戶分收入、KPI 計數）；D-018 | ✅ 執行編號 `35510208625`（e2e 7 分 2 秒、python 3 分 22 秒、web 1 分 16 秒） |
| `76512b7` | 2026-09-20 | T-605a 增量：CEO 覆盤決定機會、兩個驗證器、模擬會打分與推進 | ✅ 執行編號 `35509048656`（e2e 7 分 16 秒、python 3 分 42 秒、web 1 分 17 秒） |
| `5dce8ba` | 2026-09-20 | T-600 第三批：部門決定房間、可進入的部門與網址、數字鍵跟著房間、2D 一列一部門 | ✅ 執行編號 `35506470460`（e2e 7 分 20 秒、python 2 分 42 秒、web 1 分 20 秒） |
| `d9062a6` | 2026-09-20 | T-611 第二批：八個命令、權限線、快照帶機會；補上 §9 的核心詞彙掃描 | ✅ 執行編號 `35505187231`（e2e 6 分 11 秒、python 3 分 51 秒、web 1 分 1 秒） |
| `3f703cb` | 2026-09-20 | T-611 第一批：機會、訊號、提案三張表與兩個狀態機（migration 0031） | — （與第二批同一次 CI） |
| `ad2f350` | 2026-09-20 | T-610 冷啟動排程、`cycles.governance`、自主運轉驗收；補-5 三個洞 | ✅ 執行編號 `35503697490`（e2e 7 分 16 秒、python 3 分 43 秒、web 1 分 14 秒） |
| `f286ccc` | 2026-09-20 | T-608 cycle 兩個端點與兩個頁面、今日目標改用 `cycles.plan`、即時快照帶上 cycle | ✅ 執行編號 `35502445901`（e2e 5 分 48 秒、python 2 分 48 秒、web 1 分 14 秒） |
| `6fa0ad4` | 2026-09-20 | T-607 fallback plan、覆盤不見的記錄、每日摘要文件（migration 0029） | ✅ 執行編號 `35501253495`（e2e 7 分 6 秒、python 2 分 54 秒、web 1 分 5 秒） |
| `6c02a56` | 2026-09-20 | T-606 治理（kill criteria 自動暫停、連續失敗、預算警示）；修 e2e 競爭 | ✅ 執行編號 `35500687901`（e2e 7 分 5 秒、python 2 分 54 秒、web 1 分 12 秒） |
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
