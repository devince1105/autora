# 10 — Database Schema (v1)

PostgreSQL 16 + pgvector。主鍵 UUIDv7。所有表含 `company_id`（MVP 不做 RLS）。`created_at/updated_at` 省略不列。
與 `3d-office/07` 的 delta 已合併於此。

---

## 1. MVP 必要

### Company
| 表 | 關鍵欄位 |
|---|---|
| companies | slug, name, type, mission, strategy_doc jsonb, status |
| company_goals | parent_goal_id?, level(annual/quarter/cycle), metric, target, current, deadline, status |
| company_policies | key, value jsonb, updated_by |
| cycles | seq, stage, plan jsonb, review jsonb, started_at, stage_deadline, ended_at |
| projects | name, state, kill_criteria jsonb, created_by_run?, approved_by, override_reason? |
| budgets | project_id?, period, amount, hard_cap |
| transactions | project_id?, kind, category, amount, currency, ref_type, ref_id, occurred_at, source, idempotency_key UNIQUE |
| kpi_snapshots | project_id?, cycle_id, metrics jsonb |
| commands_log | command, actor jsonb, payload, decision, result, event_ids[] |

### Runtime
| 表 | 關鍵欄位 |
|---|---|
| agents | role, display_name, avatar_key, capabilities[], permissions jsonb, tools[], model_policy jsonb, budget jsonb, status |
| agent_activity | agent_id PK, state, detail jsonb, run_id?, task_id?, since, last_event_seq |
| workflow_runs | project_id, cycle_id?, template_name, params jsonb, state |
| tasks | workflow_run_id?, project_id, cycle_id?, name, display_name, required_role, state, depends_on uuid[], input, output, output_schema_ref, attempt, max_attempts, budget_usd, progress jsonb?, priority, lease_owner?, lease_until? |
| agent_runs | task_id, agent_id, attempt, state, input, output, evaluation jsonb, cost_usd, tokens_in, tokens_out, steps_count, handoff jsonb?, error, started_at, finished_at |
| agent_steps | run_id, seq, kind, prompt_hash, tool_calls jsonb, summary, blob_key?, cost_usd |
| model_calls | run_id?, task_id?, project_id, provider, model_id, alias, tokens_in, tokens_out, cache_read, cache_write, cost_usd, latency_ms, status |
| approvals | kind, ref_type, ref_id, payload, summary, state, expires_at, decided_by, decided_at, reason |
| policy_decisions | actor_agent_id?, action, args_hash, decision, rule_id, run_id? |
| events | seq bigserial UNIQUE, event_type, schema_version, aggregate_type, aggregate_id, agent_id?, task_id?, run_id?, workflow_run_id?, cycle_id?, correlation_id, causation_id, actor jsonb, payload jsonb, occurred_at, processed_at? |
| state_transitions | entity_type, entity_id, from_state, to_state, reason, actor, at |
| schedules | name, cron, next_run_at, lease_until?, last_run_at, enabled |
| agent_memory | agent_id, kind, content jsonb, expires_at |

### Newsroom
| 表 | 關鍵欄位 |
|---|---|
| sources | kind, url, trust_level, poll_interval, language, status |
| source_items | source_id, external_id, url, title, published_at, content_hash UNIQUE(source_id, content_hash) |
| evidence | source_id?, url, retrieved_at, blob_key, extracted_text, text_hash |
| evidence_chunks | evidence_id, seq, text, embedding vector |
| stories | project_id, title, summary, angle, state, score, source_item_ids uuid[] |
| claims | story_id, text, claim_type, status |
| claim_evidence | claim_id, evidence_id, quote, quote_offset, support_type |
| articles | story_id, slug UNIQUE, title, state, primary_lang, published_langs[], current_version_id, published_at |
| article_versions | article_id, version, lang, draft_group_id, translation_of_version_id?, body jsonb, author_run_id, change_summary; UNIQUE(article_id, version, lang) |
| fact_check_reports | article_version_id, results jsonb, passed, run_id |
| distributions | article_id, channel, external_ref?, status, copy jsonb, published_at |
| analytics_events | article_id, lang, event_type, session_hash, ts |
| analytics_daily | article_id, lang, date, views, uniques, read_complete, referrers jsonb |
| documents | kind, title, body, embedding, ref_type, ref_id |

---

## 2. Phase 2 / 5 / 6

| Phase | 表 |
|---|---|
| 平台 P2 | company_strategies（版本化）、model_policies、workflow_templates（入表）、tool_usage、realtime_cursors(可選) |
| 平台 P5 Revenue | products, customers, leads, opportunities, subscriptions, orders, payments, campaigns, campaign_spend |
| 平台 P6 Multi-company | users, memberships, roles, company_quotas |

---

## 3. Entity Relationship（核心）

```
companies 1─* company_goals / cycles / projects / agents / company_policies
cycles 1─* workflow_runs 1─* tasks 1─* agent_runs 1─* agent_steps
agents 1─1 agent_activity ; agents 1─* agent_runs
projects 1─* budgets / transactions / kpi_snapshots
agent_runs 1─* model_calls              (cost 鏈：model_call → run → task → project → company)
tasks *─* tasks (depends_on)
approvals *─1 (tasks | commands | projects)
events → 任意 aggregate；索引 (company_id, seq), (run_id, seq), (task_id, seq), (correlation_id, seq)

sources 1─* source_items *─* stories
stories 1─* claims 1─* claim_evidence *─1 evidence 1─* evidence_chunks
stories 1─1 articles 1─* article_versions 1─* fact_check_reports
articles 1─* distributions ; articles 1─* analytics_events → analytics_daily
```

---

## 4. 設計決策

- `transactions` 單表 + kind；revenue/expense 是視圖。
- `tasks` 就是 queue：`WHERE state='READY' AND (lease_until IS NULL OR lease_until<now()) ORDER BY priority, created_at FOR UPDATE SKIP LOCKED LIMIT 1`。
- `events` 是 outbox、audit log、realtime 來源；永不刪除；`seq` 全域 bigserial（多公司仍可用）。
- 不做 event partition（MVP）、不做 materialized view、不做 per-company sequence。
