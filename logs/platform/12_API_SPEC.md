# 12 — API Spec (v1)

FastAPI；OpenAPI 產生 TS client。所有 mutation endpoint 對應一個 Command（走 PolicyEngine，actor=human）。
Admin 端點需 bearer token（MVP 單一 operator）。Worker 不走 HTTP。

---

## Admin

### Company
| Method | Path | 說明 |
|---|---|---|
| GET/POST | `/api/companies` | 列表 / 建立（Command CreateCompany） |
| GET | `/api/companies/{id}` | 含 goals、policies 摘要 |
| GET | `/api/companies/{id}/snapshot` | CompanySnapshot（同 CEO 看到的） |
| GET/PUT | `/api/companies/{id}/policies` | PUT = Command UpdatePolicy（HUMAN） |
| GET | `/api/companies/{id}/kpis?range=` | kpi_snapshots |

### Cycle / Project / Budget
| Method | Path | 說明 |
|---|---|---|
| GET | `/api/companies/{id}/cycles` / `/api/cycles/{id}` | plan、review、timeline(`trace(correlation_id)`) |
| GET/POST | `/api/companies/{id}/projects` | POST = CreateProject |
| POST | `/api/projects/{id}/{pause|resume|kill}` | Commands |
| GET/POST | `/api/projects/{id}/budgets` | POST = AllocateBudget |
| GET/POST | `/api/companies/{id}/transactions` | POST = RecordTransaction（HUMAN） |

### Runtime
| Method | Path | 說明 |
|---|---|---|
| GET | `/api/companies/{id}/agents` | 含 activity |
| POST | `/api/agents/{id}/{pause|resume}` | |
| POST | `/api/companies/{id}/workflows` | InstantiateWorkflow `{template, params, project_id}` |
| GET | `/api/workflow-runs/{id}` | tasks + 狀態 |
| GET | `/api/tasks?state=&role=` / `/api/tasks/{id}` | |
| GET | `/api/runs/{id}` | run + steps 摘要 + output |
| GET | `/api/runs/{id}/trace` | events ⋈ steps |
| GET | `/api/runs/{id}/steps/{seq}/blob` | 完整 I/O（admin） |
| GET | `/api/events?after=&until=&type=&agent_id=&limit=` | 依 seq |
| GET | `/api/approvals?state=` / POST `/api/approvals/{id}/decide` | DecideApproval |

### Realtime
| Method | Path | 說明 |
|---|---|---|
| GET | `/api/companies/{id}/realtime/snapshot` | 投影（last_seq、agents、tasks、kpis、recent_events、cycle） |
| WS | `/ws/companies/{id}?token=&since=` | 見 3d-office/05 |

### Newsroom
| Method | Path | 說明 |
|---|---|---|
| GET/POST | `/api/companies/{id}/sources` | |
| GET | `/api/stories?state=` / `/api/stories/{id}` | sources、evidence、claims、timeline |
| GET | `/api/articles?state=` / `/api/articles/{id}` | versions、fact-check、distribution、analytics、trace |
| GET | `/api/articles/{id}/versions/{vid}` | blocks + claims + evidence quotes |
| POST | `/api/articles/{id}/{approve|reject}` | Commands（HUMAN） |

## Public
| Method | Path | 說明 |
|---|---|---|
| GET | `/api/site/{lang}/articles` / `/api/site/{lang}/articles/{slug}` | 只回 PUBLISHED |
| POST | `/api/analytics/beacon` | `{article_id, lang, event_type, session_hash}`；無 PII |

## 慣例
- 錯誤：RFC 7807 problem+json；Policy DENY → 403 含 rule_id；NEEDS_APPROVAL → 202 含 approval_id。
- 分頁：cursor-based（`after`）。
- 所有時間 UTC ISO 8601。
