# 11 — Event Catalog

Envelope（含 `seq`、關聯鍵、actor、schema_version）以 `3d-office/03_EVENT_MODEL.md` 為準。本文件是完整目錄與持久化分類。

原則：**所有 domain event 持久化**（`events`，永不刪除）；**ephemeral** 只有高頻進度與心跳；**replay** 指重建投影，不是重建 domain state（不是 event sourcing）。

| 群組 | Event | 持久化 | Replay 用途 | 產生者 |
|---|---|---|---|---|
| Company | COMPANY_CREATED, GOAL_CREATED, GOAL_UPDATED, POLICY_UPDATED, STRATEGY_UPDATED | 是 | 審計 | Command |
| Cycle | CYCLE_STARTED, CYCLE_STAGE_CHANGED, CYCLE_STAGE_TIMEOUT, CYCLE_PLAN_FALLBACK, CYCLE_REVIEWED, CYCLE_COMPLETED | 是 | cycle 時間線 | Cycle runner |
| Project | PROJECT_PROPOSED, APPROVED, REJECTED, PAUSED, RESUMED, KILL_PROPOSED, KILLED, COMPLETED | 是 | 審計 | Command / Governance |
| Finance | BUDGET_ALLOCATED, BUDGET_EXHAUSTED, EXPENSE_RECORDED, REVENUE_RECORDED | 是 | 財務投影 | Ledger |
| Revenue（T-701、D-022） | CUSTOMER_ACQUIRED, CUSTOMER_CHURNED, SUBSCRIPTION_STARTED, SUBSCRIPTION_STATE_CHANGED, PAYMENT_RECEIVED | 是 | 營收投影、客戶價值 | `company/customers`、`company/subscriptions`（由金流 webhook 呼叫） |
| Workflow | WORKFLOW_RUN_CREATED, WORKFLOW_RUN_COMPLETED, WORKFLOW_RUN_FAILED, WORKFLOW_RUN_EXTENDED（T-514） | 是 | — | TaskManager |
| Task | TASK_CREATED, TASK_READY, TASK_STARTED, TASK_WAITING, TASK_SUCCEEDED(unlocks[]), TASK_FAILED, TASK_CANCELLED, TASK_BLOCKED | 是 | 任務統計、handoff | TaskManager |
| Agent | AGENT_CREATED, AGENT_PAUSED, AGENT_RESUMED, AGENT_IDLE, AGENT_RUN_STARTED, AGENT_THINKING, AGENT_WORKING, AGENT_WAITING, AGENT_REVIEWING, AGENT_RUN_COMPLETED, AGENT_RUN_FAILED, AGENT_RUN_ABORTED | 是 | agent_activity 投影、trace | AgentRunner / ActivityService |
| Agent (ephemeral) | AGENT_STEP_PROGRESS, AGENT_HEARTBEAT | **否** | — | AgentRunner / Worker |
| Tool | TOOL_CALLED, TOOL_COMPLETED(produced[]), TOOL_FAILED, TOOL_DENIED | 是 | trace、產物計數 | ToolRegistry / PolicyEngine |
| Governance | APPROVAL_REQUESTED, APPROVAL_APPROVED, APPROVAL_REJECTED, APPROVAL_EXPIRED, POLICY_DENIED | 是 | 審計 | Approvals / PolicyEngine |
| Model | （無 event；`model_calls` 表） | — | — | Gateway |
| Scheduler | SCHEDULE_FIRED | 是（可歸檔） | — | Scheduler |
| Reporting | KPI_SNAPSHOT_CREATED | 是 | — | Reporting |
| Newsroom | SOURCE_POLLED, SOURCE_ITEM_DISCOVERED(可歸檔), SOURCE_PAUSED, EVIDENCE_CAPTURED, STORY_DISCOVERED, STORY_SELECTED, STORY_DROPPED, CLAIM_CREATED, CLAIM_VERIFIED, CLAIM_REJECTED, ARTICLE_CREATED, ARTICLE_REVISION_REQUESTED, ARTICLE_REVIEWED, ARTICLE_APPROVED, ARTICLE_REJECTED, ARTICLE_PUBLISHED, DISTRIBUTION_CREATED, ANALYTICS_DAILY_UPDATED | 是 | 內容 pipeline 投影、溯源 | Newsroom tools / services |
| Analytics raw | （beacon 進 `analytics_events` 表，不是 event） | — | analytics_daily 可重算 | Beacon API |
| Business (P5) | LEAD_CREATED, CAMPAIGN_CREATED, CAMPAIGN_SPEND_RECORDED（延後，見 D-022；OPPORTUNITY_* 已由 T-611 實作於 company） | 是 | — | — |

## Handler 規則
- 每個 handler 冪等（以 event_id 去重）。
- Handler 失敗不阻塞其他 handler；失敗記錄 `event_handler_failures`（或 log）並可重試。
- Domain handler 只訂閱自己關心的事件；跨 domain 只透過 event，不 import。

## 版本策略
- 加欄位不升版；改語意升 `schema_version`；新 type 前端預設忽略、Trace 以 raw 顯示。
