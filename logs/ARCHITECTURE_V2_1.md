# Architecture Revision v2.1 — 從「經營事業」到「發現事業」

- 日期：2026-09-20
- 狀態：**架構修訂，尚未實作**。v2（`ARCHITECTURE_V2.md`）的組織模型**原封不動保留**，本文件只在它之上補一層。
- 修訂原因：v2 讓公司會「經營既有的事業」，但一間自主公司還要會**發現、驗證、投入、開創**新事業，並依結果決定擴大、暫停或收掉。v2 缺的是從「市場觀察」到「開一門生意」之間的那段。
- 前提：**AI News 是 Business #001，而且快落地了**。AI Media / Newsroom / Daily English World 的 v2 設計完全不動；v2.1 不碰它。

---

## 0. 一句話

> v2 回答「這間公司由誰、用什麼組織、做哪些事」。
> v2.1 回答「這間公司**怎麼決定要做哪門生意**，以及賺到的錢怎麼回到這個決定裡」。

而且它**不新增任何執行機制**：機會與提案有自己的狀態機，但**沒有自己的排程器**。它們只在既有的每日 cycle 的階段裡前進，一個 cycle 最多推一步。探索與驗證本身是 **Project**，因為 Project 本來就是「有預算、有存續判準的一段工作」。

---

## 1. Opportunity 需要獨立 Entity 嗎？——需要

**需要，而且不能用 `business_units` 的某個狀態代替。**

理由：
1. **它必須在任何投入之前就存在。** 如果用「PROPOSED 狀態的 business unit」表示機會，那張表會裝滿公司從沒做過的東西，而「我們有哪些事業」這個查詢從此永遠要記得加狀態過濾。`business_units.state` 的意思是「這門生意跑不跑」，不該再兼差表示「這門生意存不存在」。
2. **被否決的機會仍然有價值。** 「我們看過 AI 客服，2026-09 因為獲客成本判斷不划算而否決」是要留住的知識，否則六個月後會再查一次、再花一次錢。機會表是公司的**決策記憶**。
3. **它會長期累積證據。** 一個機會在被決定之前可能被觀察好幾輪，訊號一點一點加上去。

```
opportunities
  id, company_id
  key, title
  thesis              -- 為什麼這可能是門生意（一段話）
  market              -- 目標市場的描述
  state               -- DISCOVERED → EVALUATING → VALIDATING → APPROVED / REJECTED / EXPIRED
  score               -- 最近一次評估的分數（可空；比較用，不是決策本身）
  discovered_by_run_id
  decided_at, decided_by, decision_reason
  expires_at          -- 機會會過期：市場會變，舊結論不該永久有效
  business_unit_id    -- APPROVED 之後指向它變成的那門生意
```

**Opportunity 不產生代理工作。** 它是被工作產生的、也被工作推進的：真正的執行永遠走 Project → Workflow → Task。

### Market Observation 不另開一張表

這是一個明確的取捨。「觀察市場」是**工作，不是資料**：它的執行紀錄已經是 workflow run 與 trace，它的產物是「一個新機會」或「既有機會上的一筆訊號」。所以：

```
opportunity_signals
  id, opportunity_id, company_id
  source              -- 哪裡來的（搜尋、既有事業的數據、人給的）
  summary
  metric, value       -- 可量化時才有
  observed_at
  evidence_ref        -- 指向 blob / document 的快照，和新聞室的證據同一種做法
```

**被否決的另一個選項**：開一張 `market_observations` 表存所有觀察。否決理由是——沒有掛到任何機會上的觀察，沒有人會去讀；而「我們掃過市場但沒發現東西」這件事，探索專案的 workflow run 與事件已經記下了。不要為了對稱而建一張只寫不讀的表。

---

## 2. Business Proposal 需要獨立 Entity 嗎？——需要，而且比 Opportunity 更需要

**需要。** 三個理由，第三個是關鍵：

1. **一個機會可以有多個提案。** 「AI English Learning」可以是訂閱制 App、可以是 B2B 授權、可以是內容付費。CEO 比較的是**提案**，不是機會。
2. **提案是決策的對象，必須可版本化、可凍結。** 送交決策後就不可改；要改就是新版本。理由跟這個系統其他地方一樣：**核准必須指向當時被核准的那份東西**，否則「我們核准了 2000 美元去做什麼」這個問題沒有可稽核的答案。
3. **提案是唯一能讓「開一門生意」這個動作可重播的東西。** 建立事業單位的命令只帶 `proposal_id`；事業的初始形狀（產品、部門、角色、預算、kill criteria）全部從提案讀出來。沒有提案，開事業就變成一串散落的參數。

```
business_proposals
  id, company_id, opportunity_id
  version             -- 同一個機會的第 n 版提案
  state               -- DRAFT → SUBMITTED → APPROVED / REJECTED / SUPERSEDED
  business_model              target_market            target_customer
  proposed_product            expected_revenue_model   expected_margin
  estimated_startup_cost      estimated_monthly_cost
  required_agents             required_capabilities    -- 需要哪些職務與能力
  risks                       validation_plan          kill_criteria
  authored_by_run_id
  approval_id                 -- 人核准的那一筆
  decided_at, decision_reason
  validation_project_id       -- 驗證那段工作（見 §4）
```

**提案是 Decision Artifact**：不是 workflow、不是 agent、不是 product。它是一份被決定的文件。

---

## 3. 哪些資料屬於 Company Core，哪些屬於 Business Domain？

**判準只有一句：換一個產業，這一列的意思還一樣嗎？**

| | Company Core | Business Domain |
|---|---|---|
| 判準 | 用**錢、時間、組織**度量的東西，換產業意思不變 | 意思由產業定義的東西 |
| 實體 | Company、Opportunity、Proposal、BusinessUnit、Department、Role、Agent（定義）、Product、Customer、Project、Budget、Transaction、Cycle、KpiSnapshot、Task/WorkflowRun/AgentRun 的**骨架** | 新聞室的 Source / Story / Claim / Evidence / Article / Distribution；SaaS 的 Ticket / Feature / Release |
| 流程 | Cycle 的五階段、命令管線、權限、帳本、治理 | workflow 範本、代理提示與輸出 schema、工具、驗證器、模擬劇本 |
| 事件 | `CYCLE_*`、`PROJECT_*`、`BUDGET_*`、`EXPENSE_*`、`OPPORTUNITY_*`、`PROPOSAL_*`、`BUSINESS_UNIT_*` | `ARTICLE_*`、`STORY_*`、`CLAIM_*` |
| KPI | **數字的容器**（`kpi_snapshots.metrics` jsonb） | 數字的**定義**（什麼叫「發布一篇」、什麼叫「讀完」） |

**測試這條線的方法**：一列資料拿給一家 SaaS 公司看，它需不需要重新定義？
「客戶 X 為產品 Y 付了 500 元」——不需要，Core。
「一篇文章有 3 個主張」——需要，Domain。

**機會的證據屬於哪邊？** `opportunity_signals` 的**外殼**在 Core（來源、摘要、時間、指標、證據指標），**內容**由領域產生。Core 不解讀 summary，只存與顯示。

---

## 4. Business Lifecycle 怎麼和現在的 Cycle / Project / Task 對接？

**這是 v2.1 最重要的設計決定：不新增迴圈。**

商業迴圈是一個**比 cycle 慢很多的迴圈**，但它**沒有自己的排程器、沒有自己的 worker**。它靠既有的每日 cycle 前進，每個 cycle 最多推進一步。

```
商業迴圈的一步            發生在哪                      誰做              花誰的錢
─────────────────────────────────────────────────────────────────────────────────
市場觀察 / 機會發現        EXECUTING（探索專案的 workflow） 領域代理          探索專案的預算
機會評估（打分、比較）      REVIEWING                      CEO              CEO 的每輪預算
產生商業提案              EXECUTING（探索專案的 workflow） 分析型代理        探索專案的預算
驗證                     EXECUTING（驗證專案）           領域代理          驗證專案的預算（上限小、有 kill criteria）
投資決策                  REVIEWING → 人審批              CEO 提案、人決定   —
建立事業單位              命令（核准後執行）               系統              資本 transfer
建立產品 / 專案            PLANNING（新事業的第一輪）        事業負責人        新事業的預算
獲客 / 產生收入            EXECUTING                     事業的代理        事業的預算
量測                     MEASURING                      帳本 + Reporting  —（無 LLM）
CEO 覆盤 → 擴大/續/停/收    REVIEWING                      CEO（收掉要人審）  —
再投入                    下一輪 PLANNING                 CEO              —
```

**探索與驗證是 Project。** 這是讓整件事不需要新機制的關鍵：Project 本來就是「有預算、有 ROI、有存續判準的一段工作」，而探索與驗證正是這種東西。於是：

- 「分配探索預算」**就是**給探索專案一個預算——不需要新的預算種類；
- 「驗證失敗就停損」**就是**驗證專案的 kill criteria——不需要新的治理規則；
- 探索與驗證的成本**自動**進帳本、自動歸屬，因為它們是專案。

所以 `projects` 只加**一個可空欄位**：

```
projects
  opportunity_id   -- 新增，可空：這段工作是為了探索 / 驗證哪個機會
```

**防暴衝仍然成立**：機會與提案的狀態機沒有排程器，只有 cycle 的階段掛鉤會推它們，而一個 cycle 一天一次。即使 CEO 想一口氣開十門生意，`max_workflows_per_cycle` 與「開事業要人審」兩道閘門都還在原位。

---

## 5. CEO 在哪個階段介入？

v2 已經把 CEO 從「今天找五篇新聞」拉回公司層。v2.1 再往上一層：**CEO 也負責決定這間公司要做哪些生意**。

| Cycle 階段 | CEO 做什麼 | CEO **不**做什麼 |
|---|---|---|
| PLANNING | 分配資本與預算到事業與專案（含**探索預算**）、定專案優先順序 | 不決定今天寫哪五篇新聞（總編輯的事） |
| REVIEWING | 讀各事業的損益與 KPI；評估機會、**比較提案**；決定要不要進入驗證；提出投資決策；提出擴大 / 暫停 / 收掉 | 不做研究、不寫程式、不寫稿 |

CEO 的動作序列固定是：**Observe → Decide → Allocate → Delegate → Monitor → Review**。它產出的永遠是**決定與資源**，不是產物。

新增的 CEO 命令（全部走既有命令管線）：

| 命令 | 權限 | 說明 |
|---|---|---|
| `AllocateExplorationBudget` | CEO LIMITED（≤ 門檻）→ 超過人審 | 給探索專案錢 |
| `ScoreOpportunity` | CEO ALLOW | 打分與排序，純記錄 |
| `AdvanceOpportunity` | CEO ALLOW（到 EVALUATING / VALIDATING）｜**人審**（到 APPROVED） | 推進機會的狀態 |
| `RejectOpportunity` | CEO ALLOW | 否決並記下理由（保留為決策記憶） |
| `CreateBusinessUnit` | **人審，一律** | 從提案開一門生意，並配初始資本 |
| `ScaleBusinessUnit` | CEO LIMITED → 超過人審 | 加減資本 |
| `PauseBusinessUnit` | CEO ALLOW | 暫停 |
| `WindDownBusinessUnit` | **人審，一律** | 收掉 |

---

## 6. Human Approval 在哪個階段介入？

**判準：不可逆、或動到真錢的那一刻。**

| 階段 | 人要不要核准 | 為什麼 |
|---|---|---|
| 市場觀察、機會發現 | 否 | 便宜、可逆，錯了只是浪費一點探索預算 |
| 機會評估、打分 | 否 | 只是記錄 |
| 產生提案 | 否 | 提案是文件，不是承諾 |
| 進入驗證 | 否（但受探索預算上限） | 驗證本來就設計成小額、時間盒、有停損 |
| **投資決策 / 建立事業單位** | **是** | 這是公司真正投下資本、而且開出一個會持續花錢的組織 |
| 超過門檻的資本配置 | 是 | 既有規則，沿用 |
| **收掉事業** | **是** | 不可逆 |
| 發布內容 | 是（D-001） | 不變 |

**人核准的是「提案」這份文件**，不是一串參數——這是 §2 第 3 個理由的實際用途。

---

## 7. Revenue / Profit 怎麼回饋給 CEO？

```
Business Unit → Product → Customer → Revenue
                                   ↘
Project / Agent / LLM  →  Expense  →  Profit = Revenue − Expense（每事業、每期）
```

**Profit 不是一張表。** 它是 Reporting 在 MEASURING 算出來的數字，寫進 `kpi_snapshots.metrics`，再進 CompanySnapshot 的事業組合那一段，CEO 在 REVIEWING 讀到。錢的真相只有 `transactions` 一個來源（T-602 已經確立），Profit 是它的視圖。

要支援使用者列的那七種切法，需要的欄位 v2 已經加完，只差一個：

| 查詢 | 靠什麼 | 狀態 |
|---|---|---|
| Revenue by Business Unit | `transactions.business_unit_id` | v2 已加 |
| Revenue by Product | `transactions.product_id` | v2 已加 |
| Cost by Business Unit | 同上 | v2 已加 |
| Cost by Project | `transactions.project_id` | 本來就有 |
| Agent / LLM Cost | `model_calls`（已連到 workflow 與 cycle） | T-602 已完成 |
| Gross Profit | Reporting 計算 | T-603 |
| Cash Flow | `Ledger.balance(at=...)` 的時間序列 | T-602 已有基礎 |
| **Revenue by Customer** | **`customers` 表 + `transactions.customer_id`** | **v2.1 新增，延後實作** |

**現在不做完整會計系統**，但邊界先留好：

```
customers
  id, company_id, business_unit_id
  external_ref        -- 金流商那邊的 id；我們不存卡號、不存個資細節
  kind                -- subscriber / sponsor / client
  acquired_at, churned_at
transactions
  customer_id         -- 新增，可空
```

刻意不做的：發票、稅務、應收應付、對帳、多幣別換算。理由是現在沒有收入，做了只是空表；而上面這兩個欄位足以讓「每個事業、每個產品、每個客戶賺多少」在第一塊錢進來的當天就答得出來。

---

## 8. 怎麼支援第二門生意？

**該新增的（全部在 Core 之外）**

1. 一個 domain 套件 `domains/<name>/`：models、tools、agents（提示 + 輸出 schema + 驗證器）、workflow 範本、policy、events、simulation——與 `domains/newsroom` 同一組註冊介面。
2. 資料列：`business_units` 一列、`departments` 與 `roles` 數列、`agents` 數列、`products` 一列以上、`projects` 若干。
3. `app.py` 的組裝根多幾行註冊（組裝根本來就是唯一可以認得所有層的地方）。

**不該改的**：Agent Runtime、FSM、DAG、事件系統、任務系統、Model Gateway、Memory、權限引擎、Company Core。

**第二門生意會逼出來的三件事**（現在就知道，不必等）：
1. **同一個 role key 在兩個部門**：SaaS 也會有 `writer`。v2 的 `roles` 是 `(company, key)` 唯一，所以兩個部門不能各有一個 `writer`。**裁定：維持 key 全公司唯一**，第二個叫 `docs_writer`——因為那個 key 就是執行層的行為鍵，兩個部門的 writer 本來就該有不同的提示與工具，用不同的 key 是誠實的。
2. **交接跨部門**：信差動畫目前只有一間房的路徑（v2 §14.7 已列）。
3. **CompanySnapshot 會變大**：兩門生意就是兩份 KPI。token 上限的裁切順序要明確——先砍非活躍事業的細節，再砍其他。

---

## 9. 現在刪掉 `domains/newsroom`，Company Core 還能動嗎？

**答案：還不行。我找到一個真的耦合。**

### 耦合 #1（真的，要修）：Core 認得新聞室的事件名稱

`autora/company/reporting_min.py`：

```python
PUBLISHED_EVENT = "ARTICLE_PUBLISHED"
...
published_today: int | None   # "Articles published today"
```

Core 層的 KPI 直接數新聞室的事件，而且欄位名就叫 `published_today`。當初的註解說「從事件數，所以公司層不需要從新聞室拿任何東西」——**那解決的是 import 的方向，不是詞彙的耦合**。import-linter 攔不到它，因為它是一個字串。

**修法（併入 T-603）**：Reporting 不認得任何領域詞彙。領域**註冊一個 KPI 掛鉤**：

```
domain.kpis(session, company_id, window) -> {"published_articles": 3, "views": 1200}
```

Core 只負責把回傳的數字放進 `kpi_snapshots.metrics`（本來就是 jsonb）與 snapshot。**Core 存數字，領域定義數字的意思。**

### 耦合 #2（domain 之間）：echo 借用新聞室的角色

`domains/echo/workflow.py` 的註解直說：用 researcher / analyst / writer 這幾個角色，「so the office shows the familiar desks」——一個領域借另一個領域的角色，只為了在 3D 辦公室有桌子。刪掉 newsroom，echo 的代理就沒有座位。v2 把座位改成部門驅動之後，echo 應該有自己的部門與角色。

### 耦合 #3（前端）：辦公室寫死了新聞室的角色

`office3d/scene/layout.ts` 的 `SLOTS`、`palette.ts` 的 `ROLE_COLOR`、兩份 `ROLE_LABEL`、`mapping.ts` 裡的 `role === "editor"` 分支。v2 §14.7 已經列入改動範圍；v2.1 再加一條：**角色的顯示名稱要來自伺服器（`roles.title`），不是前端的字典**——否則每加一個角色就要改前端。

### 修完之後的答案

修掉這三處，加上 v2 的組織模型，答案就是 **Yes**：刪掉 `domains/newsroom`，Core 會剩下一間沒有事業、沒有產品、沒有領域代理的公司——它照樣開 cycle、照樣結算（金額為零）、照樣接受人建立新的事業。**這正是「還沒有第一門生意」的公司該有的樣子**，也正是 v2.1 描述的起點。

建議把這件事變成**會跑的測試**（併入 T-610）：一個 `tests/acceptance/test_industry_agnostic.py`，把領域全部拿掉後跑完一輪 cycle；加上一個靜態檢查——`autora/company/**` 不得出現任何領域詞彙（article、story、claim、newsroom…）。**AC-S5 現在檢查 import 方向；這條檢查詞彙。**

---

## 10. 十個問題的答案（整理）

| # | 問題 | 答案 |
|---|---|---|
| 1 | Opportunity 要獨立嗎 | **要**。它必須先於投入存在；被否決的機會是決策記憶；證據會長期累積 |
| 2 | Business Proposal 要獨立嗎 | **要**，而且更必要。一個機會多個提案；提案凍結後才可核准；開事業的命令只帶 proposal_id |
| 3 | 哪些是 Company Core | 用錢 / 時間 / 組織度量、換產業意思不變的：機會、提案、事業、組織、專案、金流、cycle、KPI 的**容器** |
| 4 | 哪些是 Business Domain | 意思由產業定義的：內容實體、工具、提示、workflow 範本、KPI 的**定義** |
| 5 | 怎麼對接 cycle / project / task | **不新增迴圈**。商業迴圈每個 cycle 最多推一步；探索與驗證**就是 Project**，所以預算、成本歸屬、停損全部沿用 |
| 6 | CEO 在哪介入 | PLANNING 配資源（含探索預算）；REVIEWING 評估機會、比較提案、提投資決策與擴大/暫停/收掉 |
| 7 | 人在哪介入 | 建立事業單位、收掉事業、超過門檻的資本配置（以及既有的發布）。探索、評估、驗證都不需要 |
| 8 | Revenue / Profit 怎麼回饋 | `transactions` 是唯一真相 → Reporting 在 MEASURING 算出每事業損益 → `kpi_snapshots` → CompanySnapshot → CEO 在 REVIEWING 讀到 |
| 9 | 怎麼支援第二門生意 | 加一個 domain 套件 + 幾列資料 + 組裝根幾行；執行層與 Core 不動 |
| 10 | 怎麼避免 Core 被污染 | 三條規則（Core 不得出現領域詞彙、不得內含 workflow 範本、領域不得寫金流表）＋ 兩個會跑的檢查（拿掉領域仍能跑一輪；靜態掃描詞彙）。**目前有一處違規：`reporting_min` 的 `ARTICLE_PUBLISHED`** |

---

## 11. 關係圖

### 11.1 商業生命週期

```
        ┌──────────────────────────── 再投入 ─────────────────────────────┐
        │                                                                │
        ▼                                                                │
   ┌─────────┐                                                           │
   │ Company │ 資本、策略、風險                                            │
   └────┬────┘                                                           │
        │ 探索預算（= 一個 Project）                                        │
        ▼                                                                │
   ┌──────────────┐   訊號     ┌────────────────────┐                     │
   │ Opportunity  │◄───────────┤ opportunity_signals│                     │
   │ DISCOVERED   │            └────────────────────┘                     │
   │ → EVALUATING │                                                       │
   └──────┬───────┘                                                       │
          │ CEO 評估、打分                                                 │
          ▼                                                               │
   ┌──────────────────┐   一個機會可以有多份                                │
   │ BusinessProposal │   v1 / v2 / v3，CEO 比較的是這個                    │
   │ DRAFT→SUBMITTED  │                                                   │
   └──────┬───────────┘                                                   │
          │ 驗證（= 一個小額、有停損的 Project）                              │
          ▼                                                               │
   ┌──────────────────┐                                                   │
   │ 投資決策          │  ★ 人核准（核准的對象是那份提案）                     │
   └──────┬───────────┘                                                   │
          │ CreateBusinessUnit(proposal_id, capital)                      │
          ▼                                                               │
   ┌──────────────┐                                                       │
   │ BusinessUnit │ ← 資本以 transfer 交易撥入                              │
   └──────┬───────┘                                                       │
          ├─────────────┬──────────────┐                                  │
          ▼             ▼              ▼                                  │
    ┌─────────┐   ┌───────────┐  ┌──────────┐                             │
    │ Product │   │Department │  │ Project  │                             │
    └────┬────┘   │ / Role /  │  └────┬─────┘                             │
         │        │  Agent    │       │                                   │
         ▼        └───────────┘       ▼                                   │
    ┌──────────┐                ┌──────────┐                              │
    │ Customer │                │ Workflow │                              │
    └────┬─────┘                │  → Task  │                              │
         │                      └────┬─────┘                              │
         ▼ Revenue                   ▼ Expense                            │
    ┌──────────────────────────────────────┐                              │
    │      transactions（唯一的金流真相）      │                              │
    └────────────────┬─────────────────────┘                              │
                     ▼ MEASURING：帳本結算 + Reporting                       │
              ┌──────────────┐                                            │
              │ kpi_snapshots│ Profit = Revenue − Expense（每事業）          │
              └──────┬───────┘                                            │
                     ▼ REVIEWING                                          │
              ┌──────────────┐                                            │
              │  CEO Review  │ Scale / Continue / Pause / Kill ★收掉要人審   │
              └──────┬───────┘                                            │
                     └────────────────────────────────────────────────────┘
```

### 11.2 三層的關係

```
┌───────────────────────────────────────────────────────────────────────┐
│  Company Core                    換產業意思不變的東西                    │
│                                                                       │
│  Opportunity → Proposal → BusinessUnit → Product → Customer           │
│  Organization（Department / Role）  Project   Budget   Transaction     │
│  Cycle（PLANNING→…→DONE）  命令管線  權限引擎  帳本  治理  KpiSnapshot     │
│                                                                       │
│  規則：不得出現領域詞彙、不得內含 workflow 範本、不解讀 KPI 的意義            │
└───────────────────────────────┬───────────────────────────────────────┘
                                │ 註冊（單向，只在組裝根 app.py 接起來）
                                │ ↑ 領域可以讀 Core；Core 不可 import 領域
┌───────────────────────────────┴───────────────────────────────────────┐
│  Business Domains              意思由產業定義的東西                      │
│                                                                       │
│  domains/newsroom   Source Story Claim Evidence Article Distribution  │
│                     工具、代理提示、workflow 範本、KPI 的定義              │
│  domains/saas（未來）Ticket Feature Release …                          │
│                                                                       │
│  註冊介面：events / models / policy / templates / behaviors / tools /   │
│            schedules / simulation / KPI 掛鉤                           │
└───────────────────────────────┬───────────────────────────────────────┘
                                │ 執行
┌───────────────────────────────┴───────────────────────────────────────┐
│  Agent Runtime                 誰都不必知道的執行層                      │
│                                                                       │
│  TaskManager  DAG  FSM  AgentRunner  Model Gateway  Tools  Memory     │
│  事件 outbox  Realtime 投影  Cost Guard  Scheduler  Worker             │
│                                                                       │
│  它只認三種字串：role、task name、tool name。                             │
│  它不知道什麼是文章，也不知道什麼是事業。                                   │
└───────────────────────────────────────────────────────────────────────┘
```

---

## 12. 對任務清單的影響

v2.1 **不改** v2 已經定的組織模型，也不改已完成的 T-600 / T-601 / T-602。

| 任務 | v2.1 的影響 |
|---|---|
| T-603 Reporting / Snapshot | **加一條**：KPI 掛鉤（修掉耦合 #1）；snapshot 加事業組合與機會摘要 |
| T-604 命令管線 | **加**：§5 的八個機會 / 事業命令 |
| T-605a CEO | **加**：評估機會、比較提案；輸出多一段 `opportunities` |
| T-606 治理 | **加**：事業層級的 kill criteria（機制與專案相同） |
| T-610 soak | **加**：拿掉領域仍能跑一輪 + Core 詞彙靜態掃描 |
| **新增 T-611** | **機會與提案**：兩張表 + `opportunity_signals` + `projects.opportunity_id`；狀態機；命令。排在 T-605a 之後 |
| **新增 T-612（延後）** | `customers` + `transactions.customer_id`；等真的有收入 |

建議順序：T-600（做到一半）→ T-603 → T-604 → T-609 → T-605a → **T-611** → T-605b → T-606 → T-607 → T-608 → T-610。
