# 07 — Permission / Governance Model

---

## 1. 裁決結果

`ALLOW` / `DENY` / `LIMITED(quota)` / `HUMAN`（NEEDS_APPROVAL）。
每個裁決寫 `policy_decisions`（actor、action、args_hash、decision、rule_id、run_id）。

## 2. 三個來源，只能收緊

1. **Tool side_effect 預設**：`read → ALLOW`、`write → 依 role`、`irreversible → HUMAN（任何 role 不可覆寫）`。
2. **Role defaults**（程式碼 `runtime/policy/defaults.py`）。
3. **company_policies**：可收緊（ALLOW→LIMITED/HUMAN/DENY）、可調 quota；**不可放寬**。放寬 policy 本身是 HUMAN action。

## 3. Permission Matrix（MVP roles + P5）

| Action | Researcher | Analyst | Writer | Editor | Marketing | CEO | Finance(P5) | Human |
|---|---|---|---|---|---|---|---|---|
| web_search / fetch_url | ALLOW | DENY | DENY | DENY | ALLOW | DENY | DENY | — |
| compare_13f（D-037） | ALLOW | DENY | DENY | DENY | DENY | DENY | DENY | — |
| read_evidence / search_evidence | ALLOW | ALLOW | ALLOW | ALLOW | ALLOW | ALLOW(摘要) | DENY | — |
| create_claim / link_evidence | DENY | ALLOW | DENY | DENY | DENY | DENY | DENY | — |
| list_claims（T-505 新增：讀主張與引文） | ALLOW | ALLOW | ALLOW | ALLOW | ALLOW | ALLOW | DENY | — |
| write_draft | DENY | DENY | ALLOW | DENY | DENY | DENY | DENY | — |
| read_draft | DENY | DENY | ALLOW | ALLOW | ALLOW | ALLOW | DENY | — |
| run_fact_check / request_revision / accept_draft | DENY | DENY | DENY | ALLOW | DENY | DENY | DENY | — |
| approve_article | DENY | DENY | DENY | DENY | DENY | DENY | DENY | ALLOW（policy 可設 system auto） |
| publish_article | DENY | DENY | DENY | DENY | DENY | DENY | DENY | ALLOW（system 於 APPROVED 後） |
| create_distribution | DENY | DENY | DENY | DENY | LIMITED(僅 PUBLISHED；MVP 僅 DB) | DENY | DENY | ALLOW |
| spend_ad_budget (P5) | DENY | DENY | DENY | DENY | LIMITED(campaign cap) → HUMAN | DENY | DENY | ALLOW |
| create_cycle_goal | DENY | DENY | DENY | DENY | DENY | ALLOW | DENY | ALLOW |
| instantiate_workflow | DENY | DENY | DENY | DENY | DENY | LIMITED(cycle 預算, ≤max_workflows) | DENY | ALLOW |
| create_project | DENY | DENY | DENY | DENY | DENY | HUMAN | DENY | ALLOW |
| allocate_budget | DENY | DENY | DENY | DENY | DENY | LIMITED(≤門檻) → HUMAN | HUMAN(提案) | ALLOW |
| pause_project | DENY | DENY | DENY | DENY | DENY | ALLOW | DENY | ALLOW |
| kill_project | DENY | DENY | DENY | DENY | DENY | HUMAN | DENY | ALLOW |
| update_strategy | DENY | DENY | DENY | DENY | DENY | HUMAN | DENY | ALLOW |
| record_transaction (manual) | DENY | DENY | DENY | DENY | DENY | DENY | DENY | ALLOW |
| payment / transfer / sign contract | DENY | DENY | DENY | DENY | DENY | HUMAN | HUMAN | ALLOW |
| delete / irreversible | DENY | DENY | DENY | DENY | DENY | DENY | DENY | ALLOW |
| pause_agent / resume_agent | DENY | DENY | DENY | DENY | DENY | DENY | DENY | ALLOW（governance 亦可 pause） |

## 4. Human Approval 流程

```
PolicyEngine → HUMAN
  → approvals row {kind, ref_type, ref_id, payload, summary, expires_at}
  → task/run WAITING_APPROVAL；agent_activity WAITING{approval}
  → APPROVAL_REQUESTED（Inbox、3D Approval Desk 燈）
  → 人 decide（REST Command）→ APPROVED：run 帶 approved_ctx 恢復 / REJECTED：task CANCELLED / EXPIRED：policy 決定
```

## 5. 治理規則（非 LLM）

- kill criteria auto-pause（02 §4）。
- Agent 連續 N 次 FAILED → `pause_agent`（governance）+ 事件。
- 每日成本超過 cap 的 80% → 警示；100% → BLOCKED_BUDGET。
- Policy 變更、agent 定義變更一律記錄 actor 與 diff。
