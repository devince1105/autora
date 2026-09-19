# 開發紀錄 05 — 階段 5 Newsroom（AI 雙語新聞室）

- 期間：2026-09-19 起（進行中）
- 目標：6 個代理的 `story_to_article_v2` 真實跑通（真模型與模擬兩種模式），雙語文章發布到公開站，從 3D 辦公室可以進入每個產物。
- 驗收條件（`logs/3d-office/09_DEVELOPMENT_ROADMAP.md` 階段 5）：`logs/3d-office/11_MVP_ACCEPTANCE.md` 的 AC-1 ～ AC-10；每條在 `MODEL_PROVIDER=fake` 下都要通過，真模型跑 AC-2、AC-5 的 smoke。
- 設計依據：`platform/05_NEWSROOM_DOMAIN.md`（領域模型、流程、fact-check 三層、雙語規則、工具）、`3d-office/06_NEWSROOM_INTEGRATION.md`（6 個代理、資料流、從 3D 進入內容、模擬模式）、`platform/10_DATABASE_SCHEMA.md`（Phase 5 表）。
- 相關決策：D-001（發布先由人核准）、D-002（主語言 zh-TW，en 第二語言，兩語都通過才發布）、D-003（web_search 用 Tavily，結果只是候選，Evidence 必須由 `fetch_url` 快照）、D-006（模型 `z-ai/glm-5.3-flash`）。
- 使用模型：Claude Opus 5（未另行指定前）。

## 開發計畫

任務來自 `3d-office/10_TASK_BREAKDOWN.md`。Newsroom 不進 Core：一律透過 `domains/newsroom` 註冊（工具、代理行為、流程範本、事件、activity links），由 `autora/app.py` 組裝；`runtime` 不可 import `domains`（import-linter 已強制）。

| 任務 | 內容 | 依賴 | 狀態 |
|---|---|---|---|
| T-500 | `web_search` 工具：Tavily adapter + fixture 模式（`SearchProvider` 介面、金鑰只在環境變數、逾時、速率限制、每次呼叫記成本） | T-204、T-209 | ✅ |
| T-501 | Sources 與 poller（models、RSS / URL 輪詢、去重） | T-212 | ⏳ |
| T-502 | `fetch_url` 工具 + Evidence + 快照（BlobStore） | T-204、T-210 | ⏳ |
| T-503 | `evidence_chunks` + embedding（pgvector） | T-502、T-207 | ⏳ |
| T-504 | Stories + 去重 | T-501、T-503 | ⏳ |
| T-505 | Claims / ClaimEvidence + 工具（`create_claim`、`link_evidence`，引文定位） | T-504 | ⏳ |
| T-506 | Researcher 代理（提示、`ResearchNote` schema、validators） | T-211、T-500、T-502 | ⏳ |
| T-507 | Analyst 代理（`AnalysisNote`、claims） | T-505 | ⏳ |
| T-508 | Articles / ArticleVersions（lang、draft_group）+ `write_draft` 工具；雙語 claim 集合一致 | T-504 | ⏳ |
| T-509 | Writer 代理（雙語草稿） | T-507、T-508 | ⏳ |
| T-510 | 確定性 fact-check validators + `run_fact_check` 工具 | T-505、T-508 | ⏳ |
| T-511 | Editor 代理（`EditorReview`、`request_revision` / `accept_draft`） | T-509、T-510 | ⏳ |
| T-512 | Publisher（`PublishArticle` command、冪等、Distribution site） | T-508、T-205 | ⏳ |
| T-513 | Marketing 代理（`DistributionPlan`、`create_distribution`：只寫 DB） | T-512 | ⏳ |
| T-514 | `story_to_article_v2` 範本 + `register()` + `activity_links` | T-203、T-506 ～ T-513 | ⏳ |
| T-515 | 公開站（zh-TW / en）+ beacon API | T-512 | ⏳ |
| T-516 | Analytics collector（每小時 → `analytics_daily`，事件） | T-515、T-212 | ⏳ |
| T-517 | Newsroom 管理頁（stories、articles、versions、fact-check、distribution、timeline） | T-308、T-311 | ⏳ |
| T-518 | 模擬資料（FakeModelProvider 劇本 + fixture HTML + revise 分支） | T-207、T-514 | ⏳ |
| T-519 | 真模型 smoke | T-208、T-514 | ⏳ |
| T-520 | 階段 5 E2E（3D → Writer → 草稿頁） | T-411、T-517、T-518 | ⏳ |

建議順序（依賴與「先有資料層、再接代理」）：
T-500 → T-501 → T-502 → T-503 → T-504 → T-505 → T-508 → T-510 → T-512（資料層與服務，都可用確定性測試驗證）→ T-506 → T-507 → T-509 → T-511 → T-513（代理，依流程順序）→ T-514 → T-518（整條流程在模擬模式跑通）→ T-515 → T-516 → T-517 → T-520 → T-519（真模型最後跑，花費最少）。

### 進入條件與帶過來的事項

- **進入條件**：「階段 4 驗收 + 版權確認的 3D 資產」。
  - ✅ 階段 4 於 2026-09-19 驗收通過（`04_PHASE_4.md` 的驗收表：2 小時長時間測試、面板 4～7 毫秒）。
  - ✅ 3D 資產只有 Kenney Mini Characters（CC0），每個檔案的來源與授權記在 `office3d/assets/LICENSES.md`，`check-assets` 在 CI 檢查（T-404、D-008）；辦公室場景全部是程式產生的幾何（D-010）。
- 帶過來的：
  - `TOOLS_PROFILE`、`TAVILY_API_KEY` 設定欄位在 T-209 就已存在（`TOOLS_PROFILE=live` 沒有金鑰會拒絕啟動）；**`.env` 目前沒有 `TAVILY_API_KEY`**，所以 Tavily 的真實呼叫測試（integration）要等使用者填入金鑰才能跑，其餘都用 fixture。
  - 模型：NVIDIA `z-ai/glm-5.3-flash`（D-006），免費端點有速率限制；真模型測試放在最後（T-519）。
  - **Embedding 供應者尚未決定**（`DECISIONS.md` Q-embed，暫用預設：以任一 OpenAI 相容的 embedding 端點實作 adapter，換供應者只改設定）。T-503 前要確認；NVIDIA Build 也有 OpenAI 相容的 embedding 模型，可沿用現有金鑰。
  - 資料庫映像已是 `pgvector/pgvector:pg16`，不需要換。
  - `domains/newsroom/policy.py`（T-205 的權限規則）已存在；`autora/app.py` 已預留 newsroom 的事件、範本、工具、行為與模擬的註冊位置。
  - `activity.detail.links` 的消費端（代理面板的連結）在階段 3 已預留，T-514 開始有資料。

---

## T-500 · `web_search`（Tavily adapter + fixture 模式）

### 做了什麼
- `autora/infra/search/`：`SearchProvider` 介面（`search(query, k, recency_days) -> SearchResponse{results, provider, cost_usd}`）與兩種錯誤——`SearchUnavailable`（逾時、429、5xx，值得重試）、`SearchRejected`（400 / 401 / 422 / 432 / 433：金鑰錯、額度用完、參數錯，重試沒用）。放在 infra 層，因為它只是對外部服務的 adapter，不知道新聞室。
  - `TavilySearchProvider`：照 Tavily 目前的 API 文件（2026-09-19 查證）以 `Authorization: Bearer` 送金鑰，金鑰不放在 body、不出現在任何錯誤訊息；每次呼叫有逾時；單一程序內的滑動視窗速率限制（滿了就等空位，要等太久就以可重試的錯誤結束）；`include_usage` 取得實際用掉的 credits，沒有就依深度（basic 1、advanced 2）計算，乘上 `TAVILY_COST_PER_CREDIT` 得到成本；摘要截到 1,000 字（搜尋結果是摘要，不是全文）；`recency_days` 轉成 Tavily 的 `time_range`。
  - `FixtureSearchProvider`：離線語料，依查詢詞重疊排序（英文依單字、中文依單字元），**沒有符合就回空結果，不會捏造網頁**。
- 新聞室的離線語料 `domains/newsroom/fixtures/search.json`：虛構的「流明市（Lumen City）微電網」題材，中英混合、含一則數字不一致的分析（之後給 fact-check 的矛盾情境用）與一則無關的新聞；網址都在不存在的 `*.fixtures.autora.test`，不會和真實新聞混淆。
- `domains/newsroom/tools/search.py`：`web_search` 工具（read、可重試、20 秒逾時、參數有上限：k 1～10、recency 1～365 天）。**不寫資料、`produced` 永遠是空的**；輸出附上「這些是候選不是證據，要用請 `fetch_url`」，每次呼叫都提醒模型（D-003）。成本記在 `TOOL_COMPLETED.cost_usd`。
- 組裝：`autora/app.py` 的 `build_search_provider` 依 `TOOLS_PROFILE` 選 Tavily 或離線語料；`build_tools` 註冊新聞室工具（工作程序的設定會傳進去）。新設定 `TAVILY_SEARCH_DEPTH`、`TAVILY_COST_PER_CREDIT`、`TAVILY_TIMEOUT_SECONDS`、`TAVILY_REQUESTS_PER_MINUTE`（`.env.example` 有說明）。
- 兩個通用的小改動：
  - **工具登錄表**：工具丟出的例外若自帶 `retryable`，以它為準（原本一律用工具的預設值：金鑰錯誤也會被重試）。
  - **設定**：`.env` 裡留空的金鑰（`TAVILY_API_KEY=`，照 `.env.example` 複製來的）現在等於沒設。原本空字串會被當成有金鑰——這是寫 T-500 的整合測試時發現的：測試沒有跳過，而是執行並失敗（用空金鑰向 Tavily 送出一次請求；沒有有效金鑰，不會計費）。三個金鑰欄位（Anthropic、NVIDIA、Tavily）都套用。

### 驗證
- `tests/newsroom/test_search.py`（22 個）：Tavily 的請求內容與標頭（金鑰只在標頭）、結果解析（兩種日期格式、無法解析的日期丟掉、摘要截斷）、recency 對應、成本（有 / 沒有 usage、basic / advanced）、各種錯誤碼的分類與訊息不含金鑰、逾時；速率限制（等空位、等太久放棄）；離線語料的排序、中文查詢、不捏造、recency；工具經過登錄表：`TOOL_COMPLETED` 帶成本、`produced` 為空、失敗時 `will_retry` 依錯誤種類、參數上限；依設定選 provider。Tavily 都用 mock transport，不連網。
- `tests/newsroom/test_tavily.py`（integration，需要 `TAVILY_API_KEY`）：一次真實搜尋、成本記錄、沒有產生 Evidence。**目前 `.env` 沒有金鑰，已確認會跳過**；要跑請填入金鑰後執行 `.venv/bin/pytest backend -m integration`。
- `tests/infra/test_settings.py`、`tests/runtime/test_tools.py` 各加一個測試（空白金鑰、例外自帶 retryable）。
- 後端 678 個測試通過；`ruff check`、`ruff format`、`lint-imports`（2 個合約）通過。

---

## 提交紀錄

| 提交 | 日期 | 內容 | 持續整合 |
|---|---|---|---|
| `8dc5d52` | 2026-09-19 | 階段 5 開發計畫；T-500 `web_search`（Tavily + 離線語料） | ✅ 執行編號 `35424065962`（e2e 4 分 4 秒、python 1 分 45 秒、web 51 秒） |
