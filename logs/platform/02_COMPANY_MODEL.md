# 02 — Company Model

---

## 1. Company 是資料，不是 process

| 欄位群 | 存放 | 說明 |
|---|---|---|
| identity | `companies` | id, slug, name, type(`newsroom`/`saas`/…), status, created_at |
| mission / strategy | `companies.mission`, `companies.strategy_doc`(jsonb, 版本化；P6 拆 `company_strategies`) | 策略是版本化文件；CEO 提案修改、人核准 |
| goals | `company_goals` | 層級式 annual → quarter → cycle；metric、target、current、deadline、status |
| policies | `company_policies` | 結構化 jsonb：預算上限、需審批動作、kill criteria 預設、發布規則、語言規則。**不是自然語言** |
| capital / budget | `budgets` + `transactions` | capital = transactions 加總；budget = 分配給 project 的信封 |
| agents | `agents` + `agent_activity` | 定義 + 現在在幹嘛 |
| projects / products | `projects`, `products`(P5) | Project 是預算與 ROI 單位 |
| customers / revenue / expenses | `customers`(P5), `transactions` | revenue / expense 是 transactions 的視圖 |
| assets | `articles`, `documents`, blobs | 產出物 |
| knowledge | `documents`(+embedding), decision ledger(`cycles.review`) | 結構化優先 |
| KPIs | `kpi_snapshots` | Reporting service 每 cycle 計算，非 LLM 產生 |
| current state | `cycles`（現在哪個 cycle、哪個 stage）+ 活動中的 `tasks` | 「公司現在在做什麼」 |

**持續存在的方式**：沒有任何公司狀態在記憶體。Worker 重啟後，Scheduler 讀 `schedules`、TaskManager 撿 READY tasks、過期租約回收。公司「醒來」不需要初始化程序。

---

## 2. Company Operating Loop（Cycle）

MVP：每日一次，五個 stage，骨架 scheduled、內部 event/DAG-driven。

| Stage | 觸發者 | 負責 | State 變化 | Events | Permission | Human Approval | 失敗 Recovery |
|---|---|---|---|---|---|---|---|
| **PLANNING** | Scheduler（每日 06:00）建立 `cycles` row | Reporting → `CompanySnapshot` → **CEO Agent** → `CyclePlan` → PolicyEngine → TaskManager 實例化 workflows | cycle=PLANNING；cycle goals；tasks 建立 | CYCLE_STARTED, GOAL_CREATED, WORKFLOW_RUN_CREATED, TASK_CREATED | CEO: `create_cycle_goal`、`instantiate_workflow`（限已核准 project、cycle 預算、≤max_workflows）；`create_project` → HUMAN | 新 Project、超門檻預算、策略修改 | CEO run 失敗 → 重試 1 次 → fallback plan（policy 定義）+ CYCLE_PLAN_FALLBACK |
| **EXECUTING** | PLANNING 完成 | TaskManager 派工；domain agents；Publisher service | tasks / stories / articles 沿 FSM | TASK_*, AGENT_*, TOOL_*, 業務事件 | 各 role 依 Permission Matrix | `publish_article`（MVP 預設 human，policy 可關） | task 層 retry；final FAILED 只取消其 workflow 下游；stage deadline（20:00）→ 強制 MEASURING |
| **MEASURING** | EXECUTING 結束或 deadline | Analytics collector、Ledger 結算 model_calls → expense、Reporting → `kpi_snapshots` | cycle=MEASURING | KPI_SNAPSHOT_CREATED, EXPENSE_RECORDED | 無 LLM | 無 | 純 SQL；失敗重跑；3 次失敗 alert |
| **REVIEWING** | MEASURING 完成 | Governance 先跑 kill criteria（確定性）→ **CEO Agent** → `CycleReview`（每 project continue / modify / pause / kill_proposal + 明日方向） | project 可能 PAUSED；kill 提案進 approvals | CYCLE_REVIEWED, PROJECT_PAUSED, PROJECT_KILL_PROPOSED | CEO: `pause_project` ALLOW；`kill_project` HUMAN；`update_strategy` HUMAN | Kill、策略、超門檻預算重分配 | CEO 失敗 → 記錄；cycle 仍 DONE；下個 snapshot 含「上次 review 缺失」 |
| **DONE** | REVIEWING 完成 | — | cycle=DONE；下一次已排程 | CYCLE_COMPLETED | — | — | — |

Source 輪詢（每 30 分）與 analytics 收集（每小時）獨立於 Cycle，只產生資料，不觸發 LLM。

---

## 3. CEO 如何取得資訊：CompanySnapshot

不是查表，是 Reporting service 每 cycle 產出的 JSON（token 上限）：

```
CompanySnapshot
  period: {cycle_id, date}
  capital: {balance, daily_cap, daily_spent}
  goals: [{id, level, metric, target, current, trend_7d}]
  projects: [{id, name, state, budget_remaining, kpis_last_cycle, kpis_7d, kill_criteria_status, open_issues[]}]
  last_cycle: {published, failed_tasks[], approvals_pending, top_articles_by_views[], revisions_requested}
  candidates: {top_source_items[≤N]}          # newsroom 提供的 hook
  strategy_summary: string
  human_notes: string                          # 你在 UI 留的指示
```

CEO 的輸出空間受 schema 限制：

```
CyclePlan   { goals:[{metric,target}], workflows:[{template, params, project_id, priority}], rationale }
CycleReview { projects:[{id, decision: continue|modify|pause|kill_proposal, params?, rationale}],
              next_cycle_hints:{topics[], target_articles}, strategy_change_proposal? }
```

「Revenue↓ Cost↑ Traffic↑ Conversion↓」的判斷：Reporting 算出趨勢 → CEO 解讀並提案 → auto-pause 由 kill criteria 確定性觸發，不依賴 CEO。

---

## 4. 停止沒有商業價值的 Project

Project 在 APPROVED 時必須帶結構化 kill criteria：

```json
{ "evaluate_after_cycles": 7,
  "auto_pause_if":     {"metric": "cost_per_published_article", "op": ">", "value": 3.0},
  "kill_proposal_if":  {"metric": "views_per_cost_unit", "op": "<", "value": 0.625, "consecutive_cycles": 3} }
```

- `auto_pause` 由 Governance 在 MEASURING/REVIEWING 之間確定性觸發。
- `kill` 由 CEO 提案（可引用 criteria），**一律 Human Approval**（MVP）。
- 人拒絕 → `override_reason` 記錄，進下次 snapshot。

---

## 5. 避免無限循環與浪費（十道閘門）

1. Cycle 是唯一的 LLM 觸發根；沒有「agent 完成 → 自己建新 agent task」的路徑。
2. `max_steps / max_tokens / per_run_usd` 在 AgentRun 硬上限。
3. `max_attempts`（task）× `repair_limit`（run 內）有上限（預設 3×2）。
4. 預算三層：company daily cap → project cap → task cap；耗盡 → `BLOCKED_BUDGET` + BUDGET_EXHAUSTED。
5. `max_workflows_per_cycle`；CEO 提案超過即截斷。
6. Stage deadline 強制推進。
7. Idempotency key 在每個 tool call 與 command。
8. Circuit breaker：同一 tool / provider 連續失敗 N 次 → 熔斷。
9. Article revise ≤ 2 次，超過 → story DROPPED。
10. Prompt caching + 上下文 token 上限。

---

## 6. FSM 定義

```
Task:      PENDING → READY → RUNNING → SUCCEEDED
                      ▲        ├→ WAITING_APPROVAL → RUNNING | CANCELLED
                      │        ├→ BLOCKED_BUDGET → READY
                      │        └→ FAILED ─(attempt<max)→ READY ; else FAILED(final) → 下游 CANCELLED
AgentRun:  CREATED → RUNNING → EVALUATING → COMPLETED
                        ├→ REPAIRING → RUNNING
                        ├→ WAITING_APPROVAL → RUNNING
                        └→ FAILED | ABORTED
Agent Activity: 見 3d-office/02
Project:   PROPOSED → APPROVED → ACTIVE ⇄ PAUSED → KILLED ; ACTIVE → COMPLETED ; PROPOSED → REJECTED
Article:   DRAFT → IN_REVIEW → APPROVED → PUBLISHED → ARCHIVED ; IN_REVIEW → DRAFT(revise, ≤2) | REJECTED
Story:     DISCOVERED → SELECTED → IN_PRODUCTION → PUBLISHED | DROPPED ; DISCOVERED → IGNORED
Cycle:     PLANNING → EXECUTING → MEASURING → REVIEWING → DONE（每 stage 有 deadline）
Approval:  PENDING → APPROVED | REJECTED | EXPIRED
```
