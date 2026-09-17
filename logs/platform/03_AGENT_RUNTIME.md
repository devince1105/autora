# 03 — Agent Runtime

---

## 1. 元件責任

| 元件 | 責任 | MVP 實作 |
|---|---|---|
| **AgentRunner** | 執行 Observe→Think→Plan→Act→Observe→Evaluate→Repair→Finish；對 ModelGateway、ToolRegistry、Evaluator、ActivityService 的唯一呼叫者 | Python class（asyncio） |
| **TaskManager** | Task FSM、DAG 依賴解析、attempt/retry、lease、派工；計算 `unlocks` | Postgres `FOR UPDATE SKIP LOCKED` |
| **FSM** | 通用小型狀態機：states、transitions、guards；寫 `state_transitions` | 純 Python |
| **DAG** | Template → tasks + depends_on；依賴滿足 → READY；final FAILED → 下游 CANCELLED | TaskManager 內函式 |
| **Scheduler** | 時間觸發；`schedules` 表 lease 去重 | APScheduler in worker |
| **EventBus** | 同 TX 寫 outbox（含 `seq`）；NOTIFY；dispatcher 呼叫 handlers（冪等） | Postgres outbox + LISTEN/NOTIFY |
| **ActivityService** | 更新 `agent_activity` 並發 AGENT_* 事件（同 TX） | 見 3d-office/02 |
| **ToolRegistry** | 註冊、schema、side_effect 分級、執行包裝（timeout、TOOL_* 事件、`produced[]`、cost） | decorator |
| **ModelGateway / Router** | 統一模型介面、alias 解析、structured output、cache、cost 預留/結算 | 見 09 |
| **Memory** | 為 run 組裝上下文（有 token 上限）；寫回 run 摘要 | 見 08 |
| **Retry** | run 內 repair（評估失敗）；task 層 attempt（崩潰/超時/最終評估失敗）；指數退避 | TaskManager |
| **Evaluator** | schema + domain validators + 可選 LLM rubric（policy 開啟時） | 純函式 + 註冊表 |
| **PolicyEngine** | 每個 tool call / command：ALLOW / DENY / LIMITED / HUMAN | 見 07 |
| **Approvals** | 建立 approval、暫停 task/run、決議後恢復或取消 | `approvals` + API + UI |
| **StateManager** | 所有寫入走 repository；agent context 禁止直接 SQL | SQLAlchemy repos |
| **Trace / Audit** | `agent_steps`、`model_calls`、`state_transitions`、`commands_log`、`events`、`policy_decisions` | Postgres + BlobStore |
| **CostTracking** | model_calls → expense（按 cycle 彙總） | Ledger |
| **FailureRecovery** | 租約回收、poison task 隔離、stage 跳過、啟動時 reconciliation | TaskManager + Scheduler |

---

## 2. Agent 標準介面

```
AgentDefinition (agents)
  id, company_id, role, display_name, description
  capabilities: [str]
  permissions: PermissionSet          # role 預設 + company 覆寫（只能收緊）
  tools: [tool_name]                  # ⊆ permissions 允許
  model_policy: {capability -> alias} # 不含 model name
  budget: {per_run_usd, per_day_usd, max_steps, max_tokens}
  goals: [goal_id]
  memory_config: {load_recent_runs, load_documents, ...}
  status: active | paused | retired

AgentRun (agent_runs)
  id, agent_id, task_id, project_id, company_id, cycle_id, attempt
  state, input, output(符合 task.output_schema), evaluation{passed, score, issues[]}
  cost_usd, tokens_in, tokens_out, steps_count, handoff, started_at, finished_at, error
```

---

## 3. 執行迴圈（AgentRunner，非 LLM 自由發揮）

```
OBSERVE   Memory.assemble(run): task input、相關 documents、近期 runs 摘要、snapshot 子集（token 上限）
THINK     activity=THINKING{plan}；一次 model call → 結構化 plan {steps[], tools_needed[], est_budget}
          PolicyEngine 預檢 tools_needed 與 est_budget；不通過 → FAILED(policy)
ACT loop  (≤ max_steps) 每步一次 model call，可帶 tool calls
          每個 tool call → PolicyEngine.check → ALLOW / DENY / NEEDS_APPROVAL
            NEEDS_APPROVAL → run & task WAITING_APPROVAL；activity=WAITING{approval}；worker 釋放
            ALLOW → activity=WORKING{tool}；TOOL_CALLED → 執行 → TOOL_COMPLETED{produced} / TOOL_FAILED
          每步寫 agent_steps；CostGuard reserve/settle
OBSERVE   tool 結果回填
EVALUATE  activity=REVIEWING{evaluate}；schema → domain validators → (optional rubric)
REPAIR    失敗 → activity=REVIEWING{repair}；issues 回饋模型；≤ repair_limit
FINISH    COMPLETED：output、settle、handoff(由 DAG 計算)、activity=COMPLETED{display_until, handoff}
          或 FAILED / ABORTED：activity=FAILED{error_class}
```

---

## 4. 何時用什麼（明確裁決）

| 機制 | 用在 | 不用在 | 理由 |
|---|---|---|---|
| FSM | 生命週期（≤8 狀態） | 跨實體協調 | guard、審計、可視化 |
| DAG | Workflow Template | Cycle 階段（那是 FSM） | 靜態可驗證的資料依賴 |
| Event | 跨模組通知、審計、realtime | 派工、狀態真相 | 解耦；newsroom 不需知道 business |
| Scheduler | 只有「時間」是觸發條件的事 | 任務推進 | — |

---

## 5. 失敗恢復

| 情況 | 處理 |
|---|---|
| worker 崩潰 | lease 過期 → task 回 READY → 新 attempt（MVP 重跑整個 run；P6 可從最後 step 續跑） |
| tool 失敗 | TOOL_FAILED{will_retry}；tool 級 retry（冪等 tool 才可）；否則回饋模型 |
| provider 失敗 | circuit breaker；task 延後（backoff）而非狂重試 |
| 評估最終失敗 | run FAILED → task attempt+1 或 final FAILED → 下游 CANCELLED → 事件可見 |
| 預算耗盡 | ABORTED → BLOCKED_BUDGET；加預算後 Scheduler 回收為 READY |
| 審批過期 | APPROVAL EXPIRED → task CANCELLED（policy 可設為保留） |
| stage 超時 | 強制推進，未完成 tasks CANCELLED 並記錄 |
| 啟動 reconciliation | RUNNING 且無 lease 的 task → READY；activity 與最新事件不一致 → 修正並記 bug |

---

## 6. Trace / Audit

五張表就是審計軌跡：`events`（含 seq、TOOL_*、AGENT_*）、`agent_steps`（prompt hash、tool calls、results 摘要、cost、blob）、`model_calls`、`state_transitions`、`commands_log`（+ `policy_decisions`）。
`trace(run_id) = events(run_id) ⋈ agent_steps(run_id)`；不存在前端合成的 trace 行。
