# Architecture Revision v2 — 公司、組織與事業

- 日期：2026-09-20
- 狀態：**架構修訂，尚未實作**。本文件是 v2 的規範來源；`platform/` 與 `3d-office/` 的文件依本文件修改。
- 修訂原因：v1 把 `CEO / Researcher / Analyst / Writer / Editor / Marketing / Finance` 直接當成公司的組織結構。那是**一條新聞內容生產線**，不是一間公司。一間公司會有多個事業、多個職能部門、多個產品，而內容生產線只是其中一個部門在做的事。
- 修訂範圍：**只改「公司/組織/事業」這一層的模型與它們跟執行層的關係**。執行層（Agent Runtime、FSM、DAG、事件系統、任務系統、Model Gateway、權限引擎、Memory、WebSocket、Trace、3D 視覺化）**不重做**。

---

## 0. 一句話總結這次修訂

> v1 問的是「這條生產線上有哪些角色？」
> v2 問的是「這間公司經營哪些事業、用哪些職能、由誰執行？」

而讓這件事便宜的原因只有一個：**執行層從頭到尾只認 `role` 這個字串**。行為查找（`(role, task_name)` → AgentBehavior）、任務認領（`task.required_role == agent.role`）、權限規則主體，三者都只吃字串。所以 v2 在字串**之上**補一層組織，字串本身不動——執行層因此不需要改。

---

## 1. Organization Model

**Organization 不是一張表。** 它是「部門樹 + 角色目錄 + 代理任職」三者合起來呈現的形狀。為它單獨開一張表會多一層沒有自己資料的間接層。

```
Organization（組織圖）= departments 的樹
                      + roles（每個部門有哪些職務）
                      + agents（誰在做那個職務）
```

「組織圖」這個查詢因此是一個 join，不是一張表；改組織就是改部門樹與角色歸屬，不需要版本化另一份文件。

**Team 也不是一張表。** Team 與 Department 的差別只有規模。用**同一張 `departments` 表加上 `parent_department_id`** 表達任意深度：`Newsroom` 底下的 `Research`、`Writing`、`Editing`、`Audience` 就是 parent 指向 Newsroom 的部門。深度自己會說話，不需要 `kind` 欄位。

這回答了「不要為了形式強制建立所有層級」：**層級由資料決定，不由 schema 強制**。一間只有 3 個代理的公司可以只有一個部門；長大之後在同一張表裡長出子部門。

---

## 2. Company Model

Company 是**經濟主體**：擁有資本、策略、事業組合與風險。它不是一條生產線，也不直接擁有「寫手」。

| 關注 | 存放 | v1 → v2 |
|---|---|---|
| identity（slug, name, type, status） | `companies` | 不變 |
| mission / strategy | `companies.mission`, `strategy_doc` | 不變（P7 拆 `company_strategies`） |
| goals | `company_goals`（annual → quarter → cycle） | 不變，但 goal 可歸屬到 business unit |
| policies | `company_policies` | 不變 |
| capital | `transactions` 加總 | 不變 |
| budget | `budgets` | **新增 business unit 這一層** |
| **business portfolio** | **`business_units`（新）** | **v1 沒有這個概念** |
| **organization** | **`departments` + `roles`（新）+ `agents`** | **v1 只有扁平的 `agents.role`** |
| **products** | **`products`（新）** | v1 文件提過（P5）但從未建立 |
| customers / revenue | `customers`（延後）、`transactions` | 收入要能歸到 product |
| current state | `cycles` + 進行中的 `tasks` | 不變 |

**公司做的事**（CEO 的職責範圍）：資本配置、事業組合的進退、專案優先順序、雇用與人力配置、風險。**公司不做的事**：決定今天要寫哪五篇新聞。

---

## 3. Business Unit Model

Business Unit = **一門生意**。它有自己的市場、產品、客戶、收入與損益，也有自己的存續判準。

```
business_units
  id, company_id
  key            -- "ai_media"（穩定識別碼）
  name           -- "AI Media"
  mission
  state          -- PROPOSED → ACTIVE ⇄ PAUSED → WOUND_DOWN
  kill_criteria  -- 這門生意的存續判準（結構化，與 project 同格式）
```

例：`AI Media`、`AI Education`、`AI SaaS`。

**Business Unit 不是部門。** 判斷法：
- 問「它賣什麼給誰、賺多少？」有答案 → Business Unit。
- 問「它替公司做哪一類工作？」有答案 → Department。

Finance 服務所有事業，不賣東西給外人 → Department。
AI Media 有產品、有讀者、有收入 → Business Unit。

---

## 4. Department Model

Department = **一種職能**，也是代理的「家」。

```
departments
  id, company_id
  business_unit_id      -- NULL = 公司層級的共用職能
  parent_department_id  -- NULL = 頂層；有值即「團隊」
  key                   -- "newsroom", "engineering", "newsroom_research"
  name, purpose
  office_zone_key       -- 3D 辦公室的區域（見 §12）
```

**關鍵設計：`business_unit_id` 可以是 NULL。** 這一個可空欄位同時容納兩種部門，不需要兩張表：

| 部門 | business_unit_id | 意思 |
|---|---|---|
| Executive、Finance、Engineering、Operations | NULL | 公司層級的共用職能，服務所有事業 |
| Newsroom（及其 Research / Writing / Editing / Audience） | AI Media | 這門生意自己的職能 |

這正好同時滿足使用者給的兩張圖：組織樹裡 Newsroom 在 AI Media 底下；3D 辦公室裡 Newsroom 與 Engineering、Finance 並列成為可以走進去的房間。兩者不矛盾——**房間是部門，部門知道自己屬於哪門生意**。

---

## 5. Role Model

Role = **職務**。它是組織與執行層之間的**契約**。

```
roles
  id, company_id
  key                  -- "editor_in_chief"（執行層唯一認得的東西）
  title                -- "Editor-in-Chief"
  department_id        -- 這個職務屬於哪個部門
  reports_to_role_id   -- 匯報對象（組織圖的線）
  is_lead              -- 是否為該部門的負責人
  responsibilities     -- 文字，給人看也給提示用
  default_capabilities / default_tools / default_permissions
  default_model_policy / default_budget
```

**最重要的決定：`roles.key` 就是今天 `agents.role` 的那個字串。**

- 執行層（behaviors、`required_role`、policy rules）**繼續只認字串**，一行都不改。
- `agents` 新增 `role_id` 外鍵，只是讓那個字串**解析得到組織位置**。
- 因此組織改了，執行層不會壞；而執行層要跑得起來的前提仍然是「這個 role key 有註冊對應的 behavior」——這個前提本來就存在。

**Role ≠ Department。** 部門是盒子，職務是盒子裡的頭銜。Newsroom 這個部門裡有 `editor_in_chief`、`researcher`、`writer`、`editor` 四個職務。

**Role ≠ Agent。** 職務是型別，代理是實例：兩個代理可以同時擔任 `writer`。

**為什麼 role key 不能變成有命名空間的字串（例如 `editorial.writer`）** — 這不是偏好，是硬限制：
`runtime/models/router.py` 的路由鍵格式是 `"<role>.<capability>"`，解析時檢查 `key.count(".") != 1`，多一個點就是設定錯誤。而這張路由表就是 `platform/04_AGENT_SPEC.md` 每個代理章節裡的 `alias` 那一列。**改角色的命名空間 = 同時改設定檔格式**。所以 v2 的 role key 一律維持單層小寫識別碼，部門關係放在 `roles.department_id`，不放在字串裡。

---

## 6. Agent Model

Agent = **實際做事的 AI Worker**。它是執行者，不是組織本身。

```
agents（既有表，新增兩個外鍵）
  id, company_id
  role                 -- 字串，= roles.key（保留，執行層靠它）
  role_id              -- 新增：解析到職務
  department_id        -- 新增：它的「家」
  display_name, description, avatar_key
  capabilities, tools, permissions, model_policy, budget
  status               -- active / paused / retired
agent_activity（既有）
  state, task_id, detail, last_event_seq   -- 「它現在在做什麼」
agent_memory（T-609，尚未建立）
  最近 N 次執行的摘要
```

使用者要求 Agent 必須有的九項，對應如下：role ✅ `role`/`role_id`、department ✅ `department_id`、capabilities ✅、permissions ✅、**goals**（見下）、memory（T-609）、tools ✅、model ✅ `model_policy`、current task ✅ `agent_activity.task_id`、state ✅ `agent_activity.state`。

**Agent 的 goals 不放在 agent 上。** 目標屬於公司與事業（`company_goals`）與專案（`projects`）；代理的「目標」是它現在被指派的任務。把目標複製到代理身上會產生第二份真相，而且會誘使代理自己給自己派工作——那正是防暴衝閘門 1 禁止的事。代理要知道目標時，從 context 組裝時讀公司/專案的目標（`platform/08` 已經這樣規定）。

**一個代理只有一個家。** 跨事業的協作透過 **Project** 發生，不透過雙重編制。這讓「誰付錢」永遠有唯一答案。

---

## 7. Project Model

Project = **現在正在做的一件事**，而且是**預算、ROI 與存續判準的單位**。

```
projects（既有表，新增一個外鍵）
  id, company_id
  business_unit_id   -- 新增，可空：NULL = 公司層級專案（如「導入成本歸屬」）
  name, description
  state              -- PROPOSED → APPROVED → ACTIVE ⇄ PAUSED → KILLED / COMPLETED
  kill_criteria      -- 核准後必須有（資料庫 CHECK 已強制）
  created_by_run_id, approved_by, override_reason
```

例：`Launch bilingual news MVP`、`Build newsletter`、`Improve SEO`、`Develop AI English learning feature`。

**Project ≠ Department。** 專案橫跨部門（一個新聞 MVP 同時用到 Newsroom 與 Engineering）；部門不持有預算。
**Project ≠ Product。** 專案是**暫時的工作**，產品是**持續的提供**。「Build newsletter」是專案，「Daily English World」是產品。專案可以指向它要推進的產品。

---

## 8. Task Model

Task = **一個代理在一次執行裡能完成的具體工作**。完全不變。

```
tasks（既有）
  workflow_run_id, cycle_id, project_id
  name, display_name
  required_role      -- 字串，對應 roles.key
  state, depends_on, input, output, attempt, budget_usd, lease_*
```

Task 由 workflow 範本（DAG）產生，範本在程式裡宣告，**LLM 不產生 DAG**。這一點 v1 的設計正確，完全保留。

**Task ≠ Project。** 專案是跨多個 cycle、多條 workflow 的容器；任務是單一代理、單次執行的工作單位。中間那層是 `workflow_runs`。

---

## 9. Product Model

Product = **公司提供給市場的東西**（新增）。

```
products
  id, company_id
  business_unit_id   -- 產品一定屬於某門生意
  key                -- "daily_english_world"
  name, description
  state              -- DRAFT → LIVE → RETIRED
  public_url
```

例：`Daily English World`（AI Media 的產品）。

產品是**收入與客戶的掛載點**：訂閱、贊助、廣告都歸到產品，再歸到事業，才有事業損益。內容產出物（`articles`）歸屬到產品，「這個產品這個月賺多少、花多少」才答得出來。

`customers` 與 `subscriptions` 暫不建立——現在沒有收入，建了也是空表。模型先定義，等真的有付費才實作（P7）。

---

## 10. 完整 Entity Relationship

```
                                   ┌──────────────┐
                                   │   Company    │  資本、策略、目標、政策
                                   └──────┬───────┘
              ┌───────────────────────────┼───────────────────────────┐
              │                           │                           │
      ┌───────▼────────┐        ┌─────────▼────────┐        ┌─────────▼────────┐
      │ Business Unit  │ 1    * │   Department     │        │  company_goals   │
      │  (一門生意)     ├────────┤   (一種職能)      │        │  company_policies│
      │  AI Media      │        │  business_unit_id│        │  budgets         │
      │  AI Education  │        │   可為 NULL      │        │  transactions    │
      └───┬────────┬───┘        └────┬────────┬────┘        └──────────────────┘
          │        │                 │        │ parent_department_id（= Team）
          │        │                 │        └──────────┐
   ┌──────▼─────┐  │            ┌────▼─────┐        ┌────▼─────┐
   │  Product   │  │            │   Role   │        │Department│
   │ Daily      │  │            │ (職務)    │        │ (子部門)  │
   │ English    │  │            │  key ────┼────┐   └──────────┘
   │ World      │  │            └────┬─────┘    │
   └──────┬─────┘  │                 │ 1      * │ roles.key == agents.role
          │        │            ┌────▼─────┐    │   == tasks.required_role
          │        │            │  Agent   │◄───┘   ← 執行層唯一認得的連結
          │        │            │ (執行者)  │
          │        │            └────┬─────┘
          │        │                 │ 執行
          │   ┌────▼──────┐          │
          └───┤  Project  │          │
     推進      │ 預算/ROI/ │          │
              │ kill準則  │          │
              └────┬──────┘          │
                   │ 1             * │
              ┌────▼─────────┐       │
              │ WorkflowRun  │       │
              │  (DAG 實例)   │       │
              └────┬─────────┘       │
                   │ 1             * │
              ┌────▼─────┐           │
              │   Task   │───────────┘  required_role 決定誰能認領
              └────┬─────┘
                   │ 1..*
              ┌────▼─────┐
              │ AgentRun │ → agent_steps → model_calls
              └──────────┘

      Cycle（每日）──→ 產生 WorkflowRun ──→ 產生 Task
        ↑ 唯一會啟動 LLM 工作的根
```

**基數與可空性一覽**

| 關係 | 基數 | 可空 | 意義 |
|---|---|---|---|
| Company → Business Unit | 1:N | — | 一間公司多門生意 |
| Company → Department | 1:N | — | 部門一定屬於公司 |
| Business Unit → Department | 1:N | **可空** | NULL = 公司層級共用職能 |
| Department → Department | 1:N | 可空 | 子部門 = Team |
| Department → Role | 1:N | — | 職務一定屬於某部門 |
| Role → Agent | 1:N | — | 同一職務可以有多個代理 |
| Agent → Department | N:1 | — | 一個代理一個家 |
| Business Unit → Product | 1:N | — | 產品一定屬於某門生意 |
| Business Unit → Project | 1:N | **可空** | NULL = 公司層級專案 |
| Project → WorkflowRun → Task | 1:N:N | — | 不變 |
| Cycle → WorkflowRun / Task | 1:N | 可空 | 稽核鏈（T-601 已加外鍵） |

---

## 11. 邊界問題的正面回答

> 「Agent、Role、Department、Business Unit、Project、Task 的邊界到底在哪裡？」

**每個實體只回答一個問題。** 如果一個實體要回答兩個，就是邊界畫錯了。

| 實體 | 它回答的唯一問題 | 它**不**回答 |
|---|---|---|
| Business Unit | 我們在做哪門生意？（市場、產品、損益） | 誰來做 |
| Department | 公司用哪一類職能做事？代理住在哪裡？ | 賣什麼、賺多少 |
| Role | 這個代理的職務是什麼？（組織與執行層的契約） | 它是誰、它現在在做什麼 |
| Agent | 誰實際在做？現在狀態如何？ | 該做什麼（那是任務給的） |
| Project | 現在要達成什麼、花誰的錢、什麼時候該停？ | 由哪個部門做（專案橫跨部門） |
| Task | 下一個具體工作是什麼、哪種職務能做？ | 為什麼要做（那是專案的事） |
| Product | 我們提供給市場什麼？ | 怎麼做出來（那是專案與任務的事） |

**四條容易搞混的界線，明確裁定：**

1. **Department vs Business Unit**：部門是「怎麼做」，事業是「做什麼生意」。Finance 是部門（服務所有事業），AI Media 是事業（有產品與讀者）。Newsroom 是**AI Media 的部門**——它是職能，但只服務一門生意。
2. **Role vs Department**：Newsroom 是部門，`editor_in_chief` 是它的職務。一個部門多個職務；一個職務只屬於一個部門。
3. **Project vs Department**：預算掛在專案，不掛在部門。理由：部門的人力成本會隨專案流動，讓部門持有預算會讓 ROI 無法歸因。「這篇文章花多少」要能回答（AC-S8），靠的是 workflow → project，不是部門。
4. **Project vs Product**：專案會結束，產品會持續。專案的 kill criteria 問「這件事還值得做嗎」；產品的存續由事業的 kill criteria 決定。

---

## 12. CEO 與 Editor-in-Chief 的分工（這次修訂的核心）

v1 的 T-605 設計是：CEO 讀 CompanySnapshot → 輸出 `CyclePlan{workflows: [...]}` → 直接實例化 `story_to_article_v2` × N。**這正是使用者指出的錯誤**：那是總編輯的工作，不是執行長的工作。

v2 把規劃拆成兩層：

| | CEO（Executive 部門） | Editor-in-Chief（AI Media / Newsroom 部門） |
|---|---|---|
| 看什麼 | CompanySnapshot：事業組合、資本、各 BU 的 KPI 與損益、待審批、風險 | EditorialSnapshot：題材候選、昨日成效、稿件狀態、本輪配到的預算 |
| 決定什麼 | 資本與預算配置到**事業與專案**、專案優先順序、暫停/提案關閉、雇用 | 今天做哪些題目、優先順序、品質標準、發佈順序 |
| 輸出 | `CyclePlan{goals, allocations[], project_priorities[], rationale}` | `EditorialPlan{stories[{seed, priority}], target_articles}` |
| 動作 | `allocate_budget`、`pause_project`、`kill_project`(人審)、`update_strategy`(人審)、`create_project`(人審)、`hire_agent`(人審) | `instantiate_workflow`（在配到的預算與 `max_workflows` 之內） |
| 頻率 | 每個 cycle 的 PLANNING 與 REVIEWING | 每個 cycle 的 PLANNING，在 CEO 之後 |

**執行順序（同一個 cycle，不需要改 FSM）**

```
PLANNING
  ├─ 1. CEO 規劃（公司層）：預算配到事業與專案、定優先順序
  └─ 2. 每個 ACTIVE 事業的負責人規劃：在自己拿到的額度內啟動 workflow
EXECUTING → MEASURING → REVIEWING（CEO 覆盤）→ DONE
```

**這不需要改 T-601。** `CycleRunner.when_entering(PLANNING, hook)` 本來就是一個**清單**，按註冊順序執行；CEO 是第一個掛鉤，事業負責人是第二個。`cycles.plan` 這個 jsonb 存 `{company: CyclePlan, units: {ai_media: EditorialPlan}}`，欄位不用改。

**成長路徑**（現在不做）：等第二門生意出現、而且需要不同的節奏時，再給 `cycles` 加 `business_unit_id`，讓事業有自己的 cycle。現在只有一門生意，加了只是空轉。

---

## 13. 對現有 Role 的實際影響：不需要改名

這是這次修訂最便宜的部分。現有角色與 v2 的對應：

| 現有 role 字串 | v2 部門 | 改名？ |
|---|---|---|
| `ceo` | Executive（公司層） | 不改，但**職責縮小**（不再直接派新聞工作） |
| `researcher` | AI Media / Newsroom / Research | 不改 |
| `analyst` | AI Media / Newsroom / Research | 不改 |
| `writer` | AI Media / Newsroom / Writing | 不改 |
| `editor` | AI Media / Newsroom / Editing | 不改（它本來做的就是文稿編輯 = Copy Editor） |
| `marketing` | AI Media / Newsroom / Audience | 不改 |
| **`editor_in_chief`** | AI Media / Newsroom（`is_lead`） | **新增** |

**零改名，一個新角色。** 使用者圖中的 `Fact Checker`、`English Writer`、`SEO / Social / Newsletter` 是**以後才拆**的細分職務；現在 `analyst` 兼事實查核、`writer` 兼雙語、`marketing` 兼三個通路。等某個職務真的忙不過來或需要不同提示時再拆，拆的時候是「新增一個 role + 一個 behavior」，不是重構。

---

## 14. 各層衝擊

### 14.1 保留不動的 v1 設計

以下全部**不改**，因為它們的抽象層次本來就在組織之下：

- **Agent Runtime**：AgentRunner 的 OBSERVE → THINK → ACT → EVALUATE 迴圈、repair、租約心跳。
- **FSM**：Task / AgentRun / WorkflowRun / Approval / Cycle 五個狀態機與 `state_transitions` 稽核。
- **DAG / Workflow**：範本在程式裡宣告、服務步驟、宣告式迴圈（D-013）。LLM 不產生 DAG。
- **事件系統**：append-only `events`、outbox、schema 版本化、zod codegen、投影契約。
- **任務系統**：`FOR UPDATE SKIP LOCKED` 認領、租約、重試、預算阻擋。
- **Model Gateway**：capability → alias → binding、成本記錄、cost guard 的四層預算。
- **權限引擎**：三層來源（工具副作用 → 角色預設 → 公司政策只能收緊）、ALLOW / DENY / NEEDS_APPROVAL / LIMITED。
- **Memory**：working memory（`agent_steps`）+ role memory（`agent_memory`）與 context 組裝順序。
- **WebSocket / Trace / 3D 視覺化的技術底座**：backlog、SNAPSHOT_REQUIRED、reducer 雙實作、R3F 場景與 2D 備援。
- **Cycle 的 stage runner 與掛鉤機制**（T-601，剛完成）：`when_entering` / `finishes_when` 正好容納兩層規劃。
- **Ledger**（T-602，剛完成）：一輪一專案一筆的結算方式不變，只是分組時多一個事業維度。

### 14.2 需要修改的

| 對象 | 修改 |
|---|---|
| `agents` | 新增 `role_id`、`department_id` 兩個外鍵（`role` 字串保留） |
| `projects` | 新增 `business_unit_id`（可空） |
| `budgets` | 新增 `business_unit_id`（可空），預算層級變成 公司 → 事業 → 專案 → 任務/執行 |
| `transactions` | 新增 `business_unit_id`、`product_id`（可空），讓事業損益與產品收入可查 |
| `company_goals` | 新增 `business_unit_id`（可空），目標可歸屬到事業 |
| `cost_guard` | 預算檢查多一層事業 |
| `AGENT_CREATED` payload | **必須**帶部門（原因見 §17.1） |
| 新事件 `AGENT_ASSIGNED` | **新增**：代理調部門時發出，否則畫面永遠不會換房間（§17.1） |
| `AgentView`（realtime 投影） | 新增部門/事業欄位，backend 與 TS reducer 必須同步改 |
| CEO 的命令集 | 從「直接開 workflow」改成「配置預算與優先順序」 |
| CompanySnapshot | 改成以事業組合為主軸（見 §14.5） |
| 3D 辦公室 | 場景從「一個房間 N 個座位」改成「公司樓層 → 進入部門」 |
| 新聞室 domain | 從「等於公司」改成「AI Media 事業的 Newsroom 部門」 |

### 14.3 新增的 Entity

1. `business_units` — 事業
2. `departments` — 部門（含以 `parent_department_id` 表達的團隊）
3. `roles` — 職務目錄
4. `products` — 產品
5. （延後到有收入時）`customers`、`subscriptions`

### 14.4 關係改變的

| 關係 | v1 | v2 |
|---|---|---|
| Agent 屬於 | Company | Company → Department（→ Business Unit 或公司層級） |
| Role 是 | `agents.role` 字串 | 字串**仍是執行層契約**，另有 `roles` 目錄給組織用 |
| Project 屬於 | Company | Company，可再歸屬 Business Unit |
| 收入/支出歸屬 | Company（+ Project） | Company → Business Unit → Product / Project |
| 誰決定今天做什麼 | CEO | CEO 決定資源，Business Unit 負責人決定內容 |
| Newsroom 是 | 公司本身 | AI Media 事業的一個部門 |

### 14.5 對 Database 的影響

- **4 張新表 + 6 個新欄位**，都是新增，**沒有破壞性變更**：現有欄位不改名、不刪除。
- 新外鍵一律**可空**，既有資料不需要回填就能繼續跑；種子資料負責建立完整的組織。
- 估計 2 個 migration：一個建組織（business_units / departments / roles + agents 的兩個外鍵），一個建事業歸屬（products + projects / budgets / transactions / company_goals 的欄位）。
- **不動 `events`**（append-only）。事件要帶部門/事業時，是**新增 payload 欄位**——依事件契約規則，加欄位不需要升版本，舊前端也不會壞。
- **`tasks.required_role` 不加外鍵。** `3d-office/07_DATABASE_MODEL.md` §5 第 4 條寫「`required_role` 必須存在於該公司的 `agents.role`，或是 `human` / `system`」，目前資料庫沒有強制。有了 `roles` 表看似可以補外鍵，但 `human` 與 `system` 兩個值不是代理職務、不該出現在職務目錄裡。**維持不加外鍵、改由範本驗證時檢查**（`dag.py` 已經在驗證字串格式，加一層「這個 role 有註冊 behavior」即可），比為了外鍵而在職務表裡塞兩個假職務乾淨。

### 14.6 對 Runtime 的影響

**核心主張：執行層不需要改。** 理由在 §0 與 §5：它只認 role 字串。

唯一真正的新增是**權限與預算多了一個維度**（可選、可後補）：
- 權限規則現在可以寫「Newsroom 的 `editor_in_chief` 可以 `instantiate_workflow`，但只在 AI Media 的預算內」——這只是 `facts` 多帶一個 `business_unit_id`，`FactsHook` 機制已經存在。
- Cost guard 多一層事業預算，形狀與現有的專案層完全相同。

### 14.7 對 3D Office 的影響（改動最大）

v1：一個房間，固定幾個座位，座位對應角色。
v2：**整間公司的 Digital Twin**。

- **場景分層**：公司樓層（各部門是可進入的區域）→ 進入部門 → 看到該部門的代理。
- **區域 = 部門**：`departments.office_zone_key` 決定它在樓層的哪一塊；座位在部門內分配，不再全域寫死角色。
- **事業著色**：屬於同一事業的部門在樓層上相鄰或同色，讓「這間公司有幾門生意」一眼看得出來。
- **即時資料必須帶部門**：realtime 快照的每個 agent 要帶 `department_key` / `business_unit_key`，前端才能把人放進正確的房間。這是**新增欄位**，投影契約相容。
- **不變的鐵律**：畫面上每個「某某正在做什麼」都必須來自 `agent_activity` + 任務 + 事件流。**前端不得自行編造**（AC-S6 已經這樣要求，v2 只是把它套用到更多房間）。
- 2D 備援跟著改成「部門清單 → 部門內的代理清單」（目前 `fallback/board.ts` 的 `ROW_OF` 把五個區塞成四列，研究與編輯還被合併，那張對照表會整個換掉）。

**實際要動的前端檔案**（盤點結果，依相依順序）：`scene/layout.ts`（`SLOTS`、`ZONES`、`ROOM`、`LABELS`、`assignSeats`）→ `palette.ts`（`FloorKind` 封閉 union、五套主題的地板色、`zoneTrim`）→ `agents/roster.ts`（`Member` 與 `rosterKey`，不加部門就不會重繪）→ `stores/ui.ts`（新增「目前在哪個部門」）→ `camera/framing.ts`（`ROOM_BOX` 與 `clampTarget` 改成按部門；`fitZoom` 本來就吃任意 Box3，這部分只是接線）→ `visual/CueRunner.tsx` 與 `walkPath`（**跨部門的交接目前沒有路徑可走**，走道與中軸都在同一間房裡；要嘛信差走到門口消失，要嘛跨部門交接改成不走路的提示）→ `visual/director.ts`、`interaction/picking.ts`（數字鍵 1–6 目前直接索引全域角色表）→ `fallback/board.ts` → `features/office/OfficePage.tsx`（網址要能指定部門）→ 代理面板（`CardModel` 加部門）。連帶要改的測試：`layout.test.ts`、`board2d.test.tsx`、`director.test.ts`、`picking.test.tsx`、`courier.test.tsx`，以及 e2e 的 `office.spec.ts`、`office-soak.spec.ts`。

### 14.8 對 Newsroom Domain 的影響

- **地位改變**：從「這間公司」變成「AI Media 事業的 Newsroom 部門」。`domains/newsroom` 的程式碼邊界不變（仍然透過註冊介面接進來，`runtime` 不 import 它）。
- **新增 `editor_in_chief` 角色與它的規劃行為**：題材選擇、優先順序、當日產量目標——也就是原本錯放在 CEO 身上的那部分。
- **workflow 範本不變**：`story_to_article_v2` 照舊，只是發動者從 CEO 改成 Editor-in-Chief。
- **產出物歸屬產品**：`articles` 增加 `product_id`，「Daily English World 這個月賺多少花多少」才答得出來。
- **種子資料改寫**：建立 公司 → AI Media 事業 → Executive / Newsroom（及子部門）→ 職務 → 代理，而不是直接建 6 個代理。
- **「一個角色一個代理」的假設要拆掉**，但範圍比想像小：**執行層本來就容許同一角色多個代理**（認領用 `FOR UPDATE SKIP LOCKED`，加上「每個代理同時只有一個進行中的執行」的唯一索引）。真正假設一對一的只有三處：`staff_newsroom()` 的 `DISPLAY_NAMES`（以角色為鍵的名字表，結構上就生不出第二個寫手）、3D 的座位數（研究 2、編輯 2、行銷 3、CEO 1，第三個研究員會變成沒有座位而在 3D 中消失）、以及信差動畫挑「該角色的第一個人」而不是真正接手的那一個。

---

## 15. 對階段 6 任務清單的影響

| 任務 | 影響 |
|---|---|
| T-601 Cycle | **完成，不需改**。掛鉤機制正好容納兩層規劃 |
| T-602 Ledger | **完成**，之後按事業分組時多一個維度 |
| T-603 Reporting / CompanySnapshot | **改**：以事業組合為主軸；另需 `EditorialSnapshot` |
| T-604 命令管線 | **改**：CEO 的命令集改為資源配置類 |
| T-605 CEO 代理 | **拆成兩個**：CEO（公司）與 Editor-in-Chief（事業） |
| T-606 治理 | 不變，但 kill criteria 多一層（事業也有存續判準） |
| T-607 Fallback / 每日摘要 | 不變 |
| T-608 Cycle 頁面 | **改**：加事業與部門的視角 |
| T-609 agent_memory | 不變 |
| T-610 7 輪 soak | 不變 |
| **新增 T-600** | **組織模型**：4 張新表、欄位、種子資料、API——必須排在 T-603 之前 |

建議新順序：**T-600（新）→ T-603 → T-604 → T-609 → T-605a（CEO）→ T-605b（Editor-in-Chief）→ T-606 → T-607 → T-608 → T-610**。

---

## 16. 盤點發現的三個硬限制（v2 必須遵守）

開工前把「哪些程式碼綁死在扁平 role 上」整個盤過一次，結果有三件事會直接決定做法。

### 17.1 代理的身分只能從 `AGENT_CREATED` 重建——所以調部門需要一個新事件

即時投影有一條契約（`realtime/reducer.py` 與 `frontend/web/src/realtime/reducer.ts` 必須產生相同結果，`tests/realtime/test_contract.py` 每次 CI 驗證）。而**重建一個代理的唯一來源是 `AGENT_CREATED` 的 payload**——reducer 就是從 `payload.role / display_name / avatar_key` 造出代理，沒有別的路。目前事件目錄裡**沒有 `AGENT_UPDATED` 也沒有 `AGENT_MOVED`**。

因此：
1. `AGENT_CREATED` **必須**帶上部門（否則快照有部門、reducer 沒有，契約測試當場失敗）；
2. 如果代理可以調部門，**必須新增一個 `AGENT_ASSIGNED` 事件**，否則畫面上的 avatar 永遠不會換房間——它只會在下次整份快照重載時跳過去。

這是整個 v2 裡唯一一個「不加就會壞」的必需品，不是選配。

### 17.2 Role key 不能有命名空間（router 的單點限制）

見 §5。`router.py` 用 `key.count(".") != 1` 解析 `role.capability`，所以 `editorial.writer` 會直接變成設定錯誤。**這條限制反過來證明 v2 的做法是對的**：組織關係放在 `roles.department_id`，不放進字串。

### 17.3 3D 辦公室的「部門」目前是前端的一個常數

`office3d/scene/layout.ts` 的 `SLOTS: Record<role, {zone, lane, desks}>` 是**整個產品裡唯一的部門模型**，而它是一個前端常數：`researcher/analyst → research` 區、`writer/editor → editorial` 區、`marketing → growth` 區、`ceo → ceo` 室。地板顏色的 `ZoneId` 還是一個**封閉的 union 型別**，被 `palette.ts` 的 `FloorKind` 與五套主題各自的 `floors` 對照表寫死。

所以「部門由資料決定」這件事，在前端是一個**型別層級**的改動，不只是換個資料來源：`ZoneId` 要從封閉 union 變成開放字串（含 fallback），五套主題的地板色要能按部門生成。這是 v2 裡工作量最大的一塊，必須誠實估進去。

---

## 17. 這次修訂推翻了哪些既有決定

v2 不是只有新增，它**明確推翻兩個已經寫進紀錄的決定**。不寫清楚的話，未來會有人照舊文件做事。

| 既有紀錄 | 原本怎麼決定 | v2 改成 |
|---|---|---|
| `logs/devlog/04_PHASE_4.md` 的未決問題回答 | 「空間**依角色（部門）分區、同一層樓**，不是切換頁面」 | **改成可進入的部門**：公司樓層 → 進入部門。原本的答案是在「只有一條新聞線」的前提下做的，前提已經不成立 |
| D-011（辦公室視覺） | 區域固定為研究／編輯／行銷／彈性座位／接待／茶水間六色 | 區域改為**由部門資料產生**，顏色隨之；D-011 的配色原則保留，但對象不再是寫死的六區 |
| `3d-office/04_3D_OFFICE_ARCHITECTURE.md` §2 | 「MVP：一間辦公室、6 個 Agent」、「佈局按 role 鍵入」 | 一間公司多個部門空間；佈局按**部門**鍵入，部門內再按 role 排座位 |

另外有一個**命名衝突**必須先解決：`platform/10_DATABASE_SCHEMA.md` 已經規劃了一張 `roles` 表，但那是 **P6 多公司的人類使用者 RBAC**（`users, memberships, roles, company_quotas`），跟 v2 的「代理職務目錄」完全是兩回事。裁定：**`roles` 給代理職務**（本文件的定義），未來的人類權限表改名為 `member_roles`。不裁定的話 schema 文件會自相矛盾。

還有一個既有的小矛盾，v2 正好修掉：`company/policy.py` 裡已經有 `"finance"` 這個角色的權限規則，但**沒有對應的 behavior、也沒有任何代理**——因為 v1 沒有地方安放「財務」這個職能。v2 有了 Finance 部門，它就有家了。

---

## 18. 要改哪些文件

| 文件 | 章節 | 改什麼 |
|---|---|---|
| `platform/02_COMPANY_MODEL.md` | §1 實體表、§2 EXECUTING 的權限欄 | 加入組織與事業；CEO 職責縮小 |
| `platform/04_AGENT_SPEC.md` | 開頭的名冊句、§1–§6 各代理、§7 | 代理按部門分組；每個代理加「部門／事業」列；新增 Editor-in-Chief |
| `platform/07_PERMISSION_MODEL.md` | §3 權限矩陣 | 矩陣目前是「一個角色一欄」；加上部門維度。順帶修掉 §2 提到的 `runtime/policy/defaults.py`——**那個檔案不存在**，規則實際在 `company/policy.py` 與各 domain 的 `policy.py` |
| `platform/10_DATABASE_SCHEMA.md` | §1 Company 區塊、`agents` 列、§3 ER | 新增四張表；`agents` 加兩個外鍵；ER 的「`companies 1─* agents`」改成經過部門 |
| `platform/11_EVENT_CATALOG.md` | 代理事件 | `AGENT_CREATED` 加部門；新增 `AGENT_ASSIGNED` |
| `platform/12_API_SPEC.md` | `GET /api/roles`、`AgentOut` | 改成 `GET /api/org`（部門樹 → 職務）；雇用時指定部門 |
| `3d-office/02_AGENT_STATE_MODEL.md` | §視覺狀態、`walkTo` | 視覺狀態仍是 `f(activity, role)`；跨部門交接要新的處理（見 §14.7） |
| `3d-office/03_EVENT_MODEL.md` | 角色命名空間那段 | 宣告 role 仍是單層字串，另有部門欄位 |
| `3d-office/04_3D_OFFICE_ARCHITECTURE.md` | §1 目錄樹、§2 場景、§5 互動、§7 效能、§9 測試 | 改動最大：可進入的部門、導覽、多空間的三角形預算、測試改成「每個部門的每個職務有座位」 |
| `3d-office/07_DATABASE_MODEL.md` | §2 `agents`、§3 分階段表、§5 一致性規則 | §3 的「階段 4 3D Office：無新增（純前端）」不再成立 |
| `logs/DECISIONS.md` | 新增 D-016 | 記錄本次修訂，並註明推翻 D-011 的哪一部分 |
| `logs/RUNBOOK.md` | `/office` 的說明 | 導覽方式改了 |
| `3d-office/10_TASK_BREAKDOWN.md` | 階段 6 | 新增 T-600；T-605 拆成兩個 |

**注意**：`3d-office/04_3D_OFFICE_ARCHITECTURE.md` §1 的目錄樹**已經與程式碼不符**（路徑寫成 `apps/web/`，實際是 `frontend/web/`；列出的 `Room.tsx`／`Zone.tsx`／`Courier.tsx` 都不存在）。改這份文件時順手校正。

---

## 19. 最終關係圖

```
Company  ── 資本 / 策略 / 目標 / 政策 / 風險
   │
   ├── Organization ················ 不是表，是 departments + roles + agents 的形狀
   │
   ├── Business Unit（一門生意）
   │      │   AI Media │ AI Education │ AI SaaS
   │      │
   │      ├── Product（提供給市場）
   │      │      └── Daily English World ── customers / revenue（延後）
   │      │
   │      └── Department（這門生意的職能）
   │             └── Newsroom
   │                    ├── Research ── Role: researcher, analyst
   │                    ├── Writing  ── Role: writer
   │                    ├── Editing  ── Role: editor
   │                    ├── Audience ── Role: marketing
   │                    └── Role: editor_in_chief（is_lead）
   │
   ├── Department（公司層級共用職能，business_unit_id = NULL）
   │      ├── Executive ── Role: ceo
   │      ├── Finance ───── Role: cfo
   │      ├── Engineering ─ Role: engineering_lead, developer, qa
   │      ├── Product
   │      ├── Marketing
   │      ├── Sales
   │      └── Operations
   │
   ├── Project（預算 / ROI / kill criteria；可歸屬事業，可跨部門）
   │      └── WorkflowRun（DAG 實例）
   │             └── Task（required_role 決定哪種職務能認領）
   │                    └── AgentRun → agent_steps → model_calls
   │
   └── Cycle（每日；唯一會啟動 LLM 工作的根）
          PLANNING ─ CEO 配資源 → 事業負責人開工作
          EXECUTING → MEASURING（結算 + KPI）→ REVIEWING（CEO 覆盤）→ DONE

Agent ── 住在一個 Department，擔任一個 Role，執行 Task
        role 字串是組織與執行層之間唯一的連結
```
