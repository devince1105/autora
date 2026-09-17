# 08 — Memory Architecture

原則：**不要為了 RAG 而 RAG。** 結構化資料用 SQL；向量只用在有明確 ROI 的三處。

| 類型 | 內容 | 存放 | 為什麼 |
|---|---|---|---|
| **1. Company Memory** | goals、strategy 版本、decision ledger（每 cycle 的 plan/review）、policies、KPI 歷史 | PostgreSQL 結構化表 | 精確查詢、趨勢、審計；向量會失真 |
| **2. Agent Memory** | (a) 工作記憶：run 內 step 上下文 (b) 角色記憶：最近 N 次 run 的結構化摘要（做了什麼、被退回原因） | (a) run 期間記憶體，落盤到 `agent_steps` (b) `agent_memory`(jsonb, TTL + 筆數上限) | 有上限的近期經驗比無限成長的向量記憶有效可控 |
| **3. Episodic Memory** | 完整 trace：agent_runs、agent_steps、model_calls、tool 結果、events | PostgreSQL（索引與摘要）+ Object Storage（完整 prompt/response、HTML 快照） | 審計與除錯需要完整，不需進 DB 索引 |
| **4. Semantic Knowledge** | Evidence chunks、Article bodies、Story summaries | PostgreSQL + pgvector（`evidence_chunks.embedding`, `documents.embedding`） | 三個用途：fact-check 檢索、story 去重、「我們寫過嗎」 |
| **5. Operational State** | tasks、cycles、approvals、schedules、leases、agent_activity | PostgreSQL | transaction、SKIP LOCKED、LISTEN/NOTIFY |

## Memory.assemble(run)

```
context = [
  system(role prompt, version),
  company_snapshot_subset(role),        # CEO 全量；其他角色只給 goals + policies 摘要
  task.input,
  recent_runs_summary(agent, N=3),      # agent_memory
  domain_context(task)                  # newsroom: story、claims、evidence 摘要（token 上限）
]
總 token 上限由 policy 設定；超過時先砍 recent_runs，再砍 domain_context 的低分項。
```

## Redis 的定位

MVP 不用。Phase 6（多 worker）只做：rate limiter、跨 process pub/sub（取代每 process LISTEN）、ModelGateway 短期 cache。不做 session store、不做 queue 真相。

## Object Storage

`BlobStore(put/get/delete/presign)`；MVP `LocalFS`，compose 可選 MinIO；正式 S3/R2。內容：Evidence HTML、完整 model I/O、圖檔。

## 保留策略

- `events`、`agent_runs`、`model_calls`：永不刪除（可按月歸檔 events 的高頻類型）。
- `agent_steps` blob：90 天後可壓縮歸檔。
- `agent_memory`：TTL 30 天、每 agent ≤ 50 筆。
- `analytics_events`：彙總後 30 天清除。
