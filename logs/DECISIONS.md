# Decision Log

每筆決策：日期、決定、影響的文件 / tasks、預設值。新決策往下加，不改舊紀錄（被推翻時加一筆新的並註明取代）。

| # | 日期 | 決定 | 影響 | 具體預設 |
|---|---|---|---|---|
| D-001 | 2026-09-16 | **發布審批先由人核准** | `platform/07` Permission Matrix；`3d-office/06` workflow `approve` 節點；T-205 policy defaults；T-514 template | `company_policies.newsroom.auto_approve_if_fact_check_passed = false`。改為自動 = HUMAN action（UpdatePolicy）。Approval 過期 `expires_after = 24h`，過期 → task 保留 WAITING（不取消），下個 cycle snapshot 列出 |
| D-002 | 2026-09-16 | **主語言 zh-TW，en 為第二語言** | `platform/05` §4；T-508 validators；T-509 Writer prompt；公開站預設路由 | `newsroom.primary_lang = "zh-TW"`, `newsroom.langs = ["zh-TW","en"]`, `newsroom.require_all_langs = true`（兩語皆通過才發布；未另行決定前維持）。Writer 先寫 zh-TW 再產 en；claim_ids 兩語相同 |
| D-003 | 2026-09-16 | **web_search 使用 Tavily** | `platform/05` §5 tools；`platform/09`（非模型，屬 tool cost）；T-500 新增 adapter task；T-506 Researcher | tool `web_search` 的 provider = `tavily`；每次呼叫記 `tool_usage`/expense category `tool_cost`；API key 只在 env；fixture 模式下不打外部 API。Tavily 回傳的 `content` 只作候選，**Evidence 仍必須由 `fetch_url` 抓原始頁快照**——search 結果不是 evidence |

## 尚未決定的 P0

| # | 問題 | 暫用預設（未決定前照此實作） |
|---|---|---|
| Q-embed | Embedding 供應商 | `embed` alias 抽象化（T-503）；MVP 先以任一可用的 OpenAI-compatible embedding endpoint 實作 adapter，換供應商只改設定 |
| Q-budget | 每日預算數量級 | `company.daily_cap_usd = 10`, `max_workflows_per_cycle = 5`, per-run 上限見 `platform/04` |
| Q-channel | 發布通路是否僅自有網站 | 僅自有網站；Marketing MVP 只產文案草稿 |
