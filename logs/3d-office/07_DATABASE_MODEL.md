# 07 — Database Model (delta over platform schema)

> 平台層 schema（companies、goals、policies、cycles、projects、budgets、transactions、kpi_snapshots、agents、
> workflow_runs、tasks、agent_runs、agent_steps、model_calls、approvals、policy_decisions、events、
> state_transitions、schedules、agent_memory、sources、source_items、evidence、evidence_chunks、stories、claims、
> claim_evidence、articles、article_versions、fact_check_reports、distributions、analytics_events、analytics_daily、documents）
> 以 `logs/platform/10_DATABASE_SCHEMA.md` 為準。本文件只列**為了 3D Office / Realtime / 雙語 Newsroom 新增或修改**的部分，
> 以及本 roadmap 各 Phase 需要的表。

---

## 1. 新增表

### `agent_activity`（MVP，Phase 2）
每個 agent 一列；Runtime 在同一 transaction 內更新。是 realtime snapshot 的來源。

| 欄位 | 型別 | 說明 |
|---|---|---|
| agent_id | uuid PK, FK agents | |
| company_id | uuid, idx | |
| state | text | IDLE/THINKING/WORKING/WAITING/REVIEWING/COMPLETED/FAILED/PAUSED |
| detail | jsonb | 見 02 §4 |
| run_id | uuid null | 當前 run |
| task_id | uuid null | |
| since | timestamptz | 進入本狀態時間 |
| last_event_seq | bigint | 造成本列的事件 seq（一致性檢查） |
| updated_at | timestamptz | |

索引：`(company_id)`。

### `realtime_cursors`（Phase 3，可選）
API process 重啟後的 cursor 持久化：`process_id, company_id, last_seq`。MVP 可用「啟動時 cursor = head_seq」代替；列為可選。

---

## 2. 修改既有表

### `events`
| 變更 | 說明 |
|---|---|
| `seq bigserial NOT NULL UNIQUE` | 排序/去重/replay 依據 |
| `schema_version int NOT NULL DEFAULT 1` | |
| `agent_id uuid null`, `task_id uuid null`, `run_id uuid null`, `workflow_run_id uuid null`, `cycle_id uuid null` | 從 payload 提升為欄位以建索引 |
| `actor jsonb` | `{kind, id}` |
| 索引 | `(company_id, seq)`, `(run_id, seq)`, `(task_id, seq)`, `(correlation_id, seq)`, `(event_type, occurred_at)` |
| 新 event_type | TOOL_CALLED/COMPLETED/FAILED/DENIED、AGENT_THINKING/WORKING/WAITING/REVIEWING、TASK_READY/WAITING/BLOCKED、DISTRIBUTION_CREATED、ANALYTICS_DAILY_UPDATED |

### `tasks`
| 變更 | 說明 |
|---|---|
| `required_role text` | 取代/補充 `required_capability`，供佈局與 handoff 使用（MVP 一個 role 對應一組 capabilities） |
| `progress jsonb null` | `{label, current, target}`，由 tool/agent 更新；沒有就是 null（不顯示百分比） |
| `display_name text` | 面向使用者的任務名（「Find today's AI stories」） |

### `agent_runs`
| 變更 | 說明 |
|---|---|
| `handoff jsonb null` | 完成時由 DAG 計算的下游 `{to_role, task_id}` |

### `article_versions`
| 變更 | 說明 |
|---|---|
| `lang text NOT NULL` | `zh-TW` / `en` |
| `draft_group_id uuid` | 同一次 Writer 產出的雙語配對 |
| `translation_of_version_id uuid null` | |
| UNIQUE `(article_id, version, lang)` | |

### `articles`
| 變更 | 說明 |
|---|---|
| `primary_lang text` | |
| `published_langs text[]` | |

### `agents`
| 變更 | 說明 |
|---|---|
| `display_name`, `avatar_key text` | 前端顯示；`avatar_key` 只是資產鍵（如 `"default"`），不含佈局 |

---

## 3. 各 Phase 需要的表（本 roadmap 的 Phase 編號）

| Phase | 表 |
|---|---|
| **1 Company + Agent Core** | companies, company_goals, company_policies, agents, agent_activity, projects, budgets, transactions, state_transitions |
| **2 Runtime + Task + Event** | workflow_runs, tasks, agent_runs, agent_steps, model_calls, events(seq), approvals, policy_decisions, schedules |
| **3 WebSocket + Dashboard** | kpi_snapshots, cycles（供 dashboard），(realtime_cursors 可選) |
| **4 3D Office** | 無新增（純前端） |
| **5 Newsroom** | sources, source_items, evidence, evidence_chunks(pgvector), stories, claims, claim_evidence, articles, article_versions(lang), fact_check_reports, distributions, analytics_events, analytics_daily |
| **6 Autonomous Loop** | agent_memory, documents；cycles 全功能 |

---

## 4. Realtime 相關查詢（必須快）

```sql
-- snapshot: agents
SELECT a.id, a.role, a.display_name, act.state, act.detail, act.since, act.run_id, act.task_id
FROM agents a JOIN agent_activity act ON act.agent_id = a.id
WHERE a.company_id = :cid AND a.status <> 'retired';

-- snapshot: active tasks
SELECT id, display_name, required_role, state, depends_on, workflow_run_id, progress, updated_at
FROM tasks WHERE company_id = :cid
  AND (state IN ('READY','RUNNING','WAITING_APPROVAL','BLOCKED_BUDGET','PENDING')
       OR (state IN ('SUCCEEDED','FAILED','CANCELLED') AND updated_at > now() - interval '10 minutes'));

-- backlog
SELECT * FROM events WHERE company_id = :cid AND seq > :since ORDER BY seq LIMIT 200;

-- trace
SELECT e.*, s.tool_calls, s.cost_usd, s.blob_key
FROM events e LEFT JOIN agent_steps s ON s.run_id = e.run_id AND s.seq = (e.payload->>'step_seq')::int
WHERE e.run_id = :rid ORDER BY e.seq;
```

---

## 5. 一致性規則（DB 層可檢查）

1. `agent_activity.last_event_seq` 必須等於該 agent 最新 `AGENT_*` 事件的 seq（測試中以查詢驗證）。
2. `events.seq` 與 `agent_activity` 更新在同一 transaction（程式碼 review + 測試：模擬中途失敗，兩者都不存在）。
3. `article_versions` 同一 `draft_group_id` 的所有 version 的 blocks 引用的 `claim_ids` 集合必須相同（validator）。
4. `tasks.required_role` 必須存在於 `agents.role`（該公司）或為 `human` / `system`。

---

## 6. 不做

- 不做 event partition（MVP 事件量小）；Phase 6 若 >10M rows 再按月分區。
- 不做 materialized view for KPI；`kpi_snapshots` 已是快照。
- 不做 per-company sequence。
