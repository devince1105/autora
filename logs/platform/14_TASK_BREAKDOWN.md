# 14 — Task Breakdown (Platform)

> **Phase 1–6 的 coding tasks 以 `3d-office/10_TASK_BREAKDOWN.md`（T-101 ~ T-610）為準**，它已涵蓋第一輪的 TASK-001 ~ TASK-054 並與 3D / Realtime 整合。
> 本文件提供：(1) 第一輪 task ID 對應表（避免兩套編號混淆）、(2) Phase 7–8 的 tasks。

---

## 1. 對應表（第一輪 → 執行用）

| 第一輪 | 內容 | 執行用 |
|---|---|---|
| TASK-001 | Settings & DB bootstrap | T-101, T-102 |
| TASK-002 | Generic FSM | T-104 |
| TASK-003 | Task/AgentRun/AgentStep schema | T-201 |
| TASK-004 | TaskManager | T-202 |
| TASK-005 | EventBus outbox | T-105, T-106 |
| TASK-006 | DAG instantiation | T-203 |
| TASK-007 | Scheduler | T-212 |
| TASK-008 | ToolRegistry | T-204 |
| TASK-009 | PolicyEngine | T-205 |
| TASK-010 | Approvals | T-206 |
| TASK-011 | AgentRunner | T-211 |
| TASK-012 | ModelGateway + Router | T-207 |
| TASK-013 | AnthropicProvider | T-208 |
| TASK-014 | Evaluator | 併入 T-211 |
| TASK-015 | CostGuard | T-209 |
| TASK-016 | Trace + BlobStore | T-210 |
| TASK-017/018 | EchoAgent + Worker + P1 integration | T-213, T-215 |
| TASK-020–028 | Company Engine（schema、ledger、commands、reporting、CEO、cycle、governance） | T-103, T-601–T-607 |
| TASK-030–045 | Newsroom | T-501–T-520 |
| TASK-050–054 | Autonomous Loop + UI | T-608–T-610, T-309–T-313 |

---

## 2. Phase 7 — Revenue

**T-701 · Business schema** — products, customers, leads, opportunities, subscriptions, orders, payments, campaigns, campaign_spend / Deps: T-103 / Validate: `pytest tests/db/test_business_models.py`
**T-702 · Payment webhook (provider adapter, 冪等, → Ledger revenue)** / Deps: T-701, T-602 / AC: 重複 webhook 不重複入帳 / Validate: `pytest tests/business/test_webhook.py`
**T-703 · Campaign cap + spend_ad_budget policy (LIMITED → HUMAN)** / Deps: T-205, T-701 / Validate: `pytest tests/business/test_campaign_policy.py`
**T-704 · Distribution channel adapter #1**（依 Open Question 決定通路；介面 `DistributionChannel.publish(article, copy) -> external_ref`） / Deps: T-513 / Validate: `pytest -m integration tests/business/test_channel.py`
**T-705 · Finance Agent (read-only + BudgetProposal → approval)** / Deps: T-211, T-603 / AC: 無任何寫 transactions 的路徑 / Validate: `pytest tests/business/agents/test_finance.py`
**T-706 · Business Agent (OpportunityProposal → PROJECT_PROPOSED → HUMAN)** / Deps: T-211, T-604 / Validate: `pytest tests/business/agents/test_business.py`
**T-707 · Revenue KPIs in snapshot & dashboard** / Deps: T-603, T-309 / Validate: `pytest tests/company/test_reporting_revenue.py && pnpm -F web test dashboard-revenue`
**T-708 · Phase 7 acceptance** / Validate: `pytest tests/acceptance/test_revenue.py`

## 3. Phase 8 — Multi-company

**T-801 · workflow_templates & model_policies 入 DB（載入器 + 快取）** / Deps: T-203, T-207
**T-802 · Company quotas + per-company cost caps** / Deps: T-209
**T-803 · Auth: users/memberships/roles; WS 與 REST 授權** / Deps: T-303
**T-804 · Redis: rate limiter + pub/sub（取代每 process LISTEN）** / Deps: T-303
**T-805 · Second domain package（research_company）** / Deps: T-514 as pattern / AC: 零修改 runtime/company/realtime
**T-806 · 3D Office: seatsForRole(n) 與多公司切換** / Deps: T-402
**T-807 · Phase 8 acceptance（兩公司隔離）** / Validate: `pytest tests/acceptance/test_multicompany.py`

---

## 4. Task 撰寫規則（給 coding agents）

- 單一責任；明確 input/output；列出 files；不得修改其他 domain。
- 每個 task 附 validation command；PR 必須附命令輸出。
- 修改 event payload 或 projection 的 task 必須同時更新 `packages/event-schema` 產生物與契約測試。
- 新增 tool 必須宣告 side_effect 與 produced 類型；新增 command 必須列入 Permission Matrix。
