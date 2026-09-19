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
| T-501 | Sources 與 poller（models、RSS / URL 輪詢、去重） | T-212 | ✅ |
| T-502 | `fetch_url` 工具 + Evidence + 快照（BlobStore） | T-204、T-210 | ✅ |
| T-503 | `evidence_chunks` + embedding（pgvector） | T-502、T-207 | ✅ |
| T-504 | Stories + 去重 | T-501、T-503 | ✅ |
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
  - **Embedding 供應者尚未決定**（`DECISIONS.md` Q-embed，暫用預設：以任一 OpenAI 相容的 embedding 端點實作 adapter，換供應者只改設定）。T-503 前要確認；NVIDIA Build 也有 OpenAI 相容的 embedding 模型，可沿用現有金鑰。→ T-503 決定（D-012）。
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
- `tests/newsroom/test_tavily.py`（integration，需要 `TAVILY_API_KEY`）：一次真實搜尋、成本記錄、沒有產生 Evidence。當時 `.env` 沒有金鑰，確認會跳過；**使用者在 T-502 期間填入金鑰後實際執行通過**（basic 搜尋 1 次，成本 0.008 美元記在 `TOOL_COMPLETED`）。
- `tests/infra/test_settings.py`、`tests/runtime/test_tools.py` 各加一個測試（空白金鑰、例外自帶 retryable）。
- 後端 678 個測試通過；`ruff check`、`ruff format`、`lint-imports`（2 個合約）通過。

---

## T-501 · 新聞來源與輪詢

### 做了什麼
- **資料表**（migration `0012`，`domains/newsroom/models.py`）：
  - `sources`：種類 `rss`（RSS / Atom 網址）、`url_list`（`config.urls`，要追蹤的網頁）、`search_query`（`config.query`，用 T-500 的搜尋供應者）；`trust_level` 0～1（T-510 查核用：信任度低的來源不能單獨支持一個主張）、語言、輪詢間隔（至少 60 秒）、狀態 `active` / `paused`、`next_poll_at`、連續失敗次數與最後錯誤。
  - `source_items`：每個來源依 `external_id` 與 `content_hash`（標準網址 + 標題）各有唯一限制，同一則再被列出不會變成新項目。
- **標準網址**（`canonical_url`）：scheme 與主機轉小寫、去掉預設埠、去掉 `#` 片段、去掉追蹤參數（`utm_*`、`fbclid`、`gclid` 等）、去掉結尾斜線。同一頁不同寫法算同一頁。**跨來源**的重複（兩個來源連到同一篇）留給 T-504 的故事分群。
- **讀 feed**（`domains/newsroom/feeds.py`）：只用標準函式庫，支援 RSS 2.0、RSS 1.0（RDF）、Atom。feed 是不可信的輸入：**有 DTD 或 entity 宣告的文件一律拒絕**（XML 炸彈），HTML 摘要去標籤、解碼、截到 1,000 字，沒有連結的項目略過。
- **抓網頁的共用元件**（`infra/http`，T-502 的 `fetch_url` 也會用）：
  - `HttpFetcher`：只允許 http / https，有逾時、大小上限（預設 5 MB）、轉址次數上限，**拒絕私有位址**（連線前先解析網域，loopback、內網、link-local、保留位址、IPv4 對應的 IPv6 都拒絕；每次轉址重新檢查）。不做這個檢查的話，模型或 feed 裡的網址可以讓伺服器去讀 `localhost:8000` 的 API 或雲端的 metadata 端點。已知限制：檢查與實際連線之間仍有 DNS 重綁的空隙，要完全封住需要固定解析結果連線，列為之後的強化。
  - `FixtureFetcher`：依 `fixtures/routes.json` 把網址對應到本地檔案，其他網址回 404，檔案不能跑出 fixture 目錄。
- **輪詢**（`domains/newsroom/sources.py` 的 `SourcePoller`）：
  - 每家公司一個排程 `newsroom.poll_sources`（每 5 分鐘），觸發時輪詢該公司「到期、啟用中」的來源（每次最多 20 個、同時 4 個）。新增第一個來源時自動建立排程（`add_source`）。
  - 分兩段：先並行抓取（不寫資料），再寫入新項目、發事件、把 `next_poll_at` 往後移。**等網路時不發事件**，所以不會占住公司的事件鎖。
  - 每次最多收 50 則、由新到舊，第一次讀大型 feed 時不會洗版時間軸。
  - 事件：每則新項目一個 `SOURCE_ITEM_DISCOVERED`、每次輪詢一個 `SOURCE_POLLED`（新增幾則、共列出幾則、成本、錯誤）；失敗會記錄並在下個間隔重試，**連續 5 次失敗自動暫停**並發 `SOURCE_PAUSED`；成功一次就歸零。`search_query` 來源的搜尋成本記在 `SOURCE_POLLED.cost_usd`。
  - 模式與工具相同：`TOOLS_PROFILE=fixture` 讀 `fixtures/feeds/` 裡的虛構 feed（RSS 與 Atom 各一，含追蹤參數、重複、沒有連結的項目等情況），`live` 才連網。
- 組裝：`autora/app.py` 新增 `build_page_fetcher`、`build_scheduler`（工作程序的排程器現在會註冊新聞室的處理器）；設定 `FETCH_TIMEOUT_SECONDS`、`FETCH_MAX_BYTES`。
- 前端：事件型別重新產生（`make gen-schema`）；時間軸與軌跡頁加上三個新事件的中文說明（「讀取來源：新增 2 則・共 5 則」、「來源讀取失敗」、「來源暫停」）。
- **還沒有的**：新增 / 管理來源的 API 與頁面（計畫中的 `/api/sources` 與管理頁屬於 T-517）；在那之前只能從程式呼叫 `add_source`。

### 驗證
- `tests/newsroom/test_sources.py`（31 個）：
  - feed：RSS、Atom、RDF 的解析；DTD / entity、壞 XML、不是 feed 的文件都拒絕。
  - 標準網址與 `content_hash`。
  - 新增來源的檢查（錯誤的 scheme、空清單、`javascript:` 網址、空查詢、k 超出範圍、未知種類）與排程只建一個。
  - 輪詢：RSS 第一次 3 則新項目（追蹤參數已去掉、由新到舊）、第二次 0 則；網址清單裡 `/a` 與 `/a/` 算一則；搜尋來源（fixture 不計費、付費的記成本）；上限只留最新的；失敗記錄、連續 3 次（測試設定）暫停、成功後歸零；`poll_due` 只處理到期且啟用中的來源。
  - **經過排程器的端到端**：工作程序的排程器觸發 `newsroom.poll_sources`，項目寫入資料庫（這個測試會 commit，結束時停用自己的排程，避免影響其他排程測試）。
  - 抓網頁：轉址、`localhost`、`127.0.0.1`、`10.x`、`169.254.169.254`、`[::1]`、`[::ffff:127.0.0.1]`、`file://`、`ftp://` 都在連線前拒絕；轉址到內網拒絕；大小上限、404、503、429、轉址迴圈；fixture 的 404 與路徑跳脫。
- 寫測試時抓到自己的錯：網址清單的項目 ID 與標題原本直接用原始網址，`/a` 與 `/a/` 被當成兩則。改成先轉標準網址。
- 後端 709 個測試通過；`ruff`、`lint-imports`、`make db-check`（模型與 migration 一致）通過；前端 event-schema 8 個、web 208 個測試與 typecheck、lint 通過。

---

## T-502 · `fetch_url` 與 Evidence

### 做了什麼
- **資料表 `evidence`**（migration `0013`）：網址（標準形式）、最後落點、標題、內容類型、語言、擷取時間與日期、BlobStore 的快照位置、抽出的本文與其雜湊、是否截斷，以及來源（若某個來源列過這個網址，信任度就跟著它）、任務、執行。**同一家公司、同一網址、同一天、同樣內容只有一筆**（唯一限制），再抓一次就重用。
- **抽本文**（`domains/newsroom/extract.py`）：用標準函式庫的 `html.parser`，不另裝擷取套件、也不用模型——同一頁永遠得到同樣的文字，之後引文才能精確定位（T-505）。去掉 script、style、導覽、頁首頁尾、側欄、表單；頁面有 `<article>` / `<main>` 就只取那段；區塊元素變成段落（段落間空一行），HTML 原始碼裡的換行只是排版、當成空格；編碼依序看回應標頭、`<meta charset>`、預設 UTF-8（未知編碼名稱退回 UTF-8）；純文字直接分段。
  - 沒有用設計文件提到的 trafilatura：新增套件要下載，而標準函式庫版本已足以處理新聞頁；之後若遇到抽不好的真實網站再評估。
- **`fetch_url` 工具**（write、可重試、30 秒）：抓網頁（共用 T-501 的抓取元件：只抓公開網址）→ 原始內容存進 BlobStore（`evidence/<公司>/<年>/<月>/<日>/<id>.snapshot`，私有、不公開）→ 抽本文（上限 20 萬字，超過截斷並標記）→ 寫入 `evidence`、`TOOL_COMPLETED`，新的快照另發 `EVIDENCE_CAPTURED`，都在同一個交易。回傳 evidence_id、前 1,500 字摘錄，並提醒模型「引文必須與證據文字完全一致」。
  - 重用時也回報 `produced: evidence`：代理確實用了這份證據，AC-5 面板上的「Sources」計數才正確。
  - 不能成為證據的直接失敗、不重試：非文字內容（PDF、圖片）、沒有可讀文字的頁面（多半要執行 JavaScript）、私有位址、4xx。
  - `render=true`（執行 JavaScript 後再讀）目前沒有 Playwright 可用：照常讀取並在結果中說明，不假裝有執行。
- **`read_evidence` 工具**（read）：分段讀證據全文（offset、limit 最多 8,000 字、`next_offset`），只能讀自己公司的證據。
- `FixtureFetcher` 像真的伺服器一樣忽略網址的查詢參數與 `#` 片段（沒有路由特別指定時）；離線頁面 `fixtures/pages/`：搜尋結果與 feed 背後的 4 個虛構新聞頁（外面包著導覽、script、側欄、頁尾），另有一個需要 JavaScript 的頁面和一個 PDF。
- 組裝：`build_tools` 會帶入 BlobStore（工作程序用設定的目錄）；時間軸加上「擷取證據」的說明。

### 過程中的問題
- 測試用了隨便產生的執行 ID，`evidence.run_id` 有外鍵，寫入失敗。改用測試共用的 `running_agent_run` 建立真的代理、任務與執行——也順便驗證了事件上帶著正確的 run、task、agent。
- 第一版測試用 `add_source` 建來源，它會建立真的輪詢排程，而其他排程測試會觸發資料庫裡所有到期的排程；改成直接建立來源資料列。
- 已知限制：快照先寫進 BlobStore 再寫資料庫；資料庫交易若失敗，會留下一個沒有被引用的快照檔（不影響正確性，之後可加清理）。

### 驗證
- `tests/newsroom/test_evidence.py`（12 個）：抽本文（去掉周邊雜訊、段落切分、中文頁、og:title / h1 當標題、沒有 article 時用 body、Big5 與未知編碼、純文字）；`fetch_url` 建立證據與快照（帶追蹤參數的網址存成標準網址、快照內容與原頁相同、連到列過它的來源、事件帶 run / task / agent）；同一天同內容重用、隔天重新擷取；PDF、需要 JavaScript 的頁面、404 都失敗且不重試；截斷；`read_evidence` 分段與只能讀自己公司的。
- **真實連網（integration）**：`test_fetch_live.py` 抓 `https://example.com`（免費）通過——真實的 DNS 公開位址檢查、串流讀取、抽取、快照；`test_tavily.py` 的真實搜尋也通過（見 T-500）。
- 後端 721 個測試通過；`ruff`、`lint-imports`、`make db-check` 通過；event-schema 8 個、web 208 個測試與 typecheck、lint 通過。

---

## T-503 · 證據分段與向量搜尋

### 決定 embedding 模型（D-012）
- 使用者同意沿用 NVIDIA 的建議。先列出帳號可用的 embedding 模型（免費的模型清單查詢），再實際試兩個：`nvidia/llama-3.2-nv-embedqa-1b-v1` 對這個帳號不可用（404）；`nvidia/nemotron-3-embed-1b` 可用，而且**跨中英檢索有效**：中文問句「停電時微電網可以供電多久？」對英文相關段落 0.51、中文相關段落 0.58、無關的咖啡價格 −0.01。
- 它只提供 2048 維（試了 `dimensions` 1024 / 768，伺服器回「只能 2048」）。pgvector 的 `vector` 型別索引上限是 2000 維，所以改用 **`halfvec(2048)`**（16 位元浮點，索引上限 4000 維、儲存減半）；資料庫的 pgvector 是 0.8.6，支援。
- 沒有安裝 Python 的 `pgvector` 套件：自己寫一個 SQLAlchemy 型別（`db/vector.py`），向量以文字形式傳送、在 SQL 裡轉型，asyncpg 不需要額外的編碼器。

### 做了什麼
- **`embed` binding**（`runtime/models/embeddings.py`，屬於執行層，不知道新聞室）：`Embedder` 把文字分批（每批 16）送給供應者、檢查每個向量的維度與儲存一致，**每次呼叫都記一筆 `model_calls`**（alias `embed`、capability `embedding`，歸屬到該次代理執行、任務與角色），成本照 `MODEL_PRICES` 的 `input` 價格（沒列就是 0）。供應者：`OpenAICompatibleEmbeddings`（NVIDIA 的 `/embeddings`，區分 query 與 passage）、`HashingEmbeddings`（模擬與測試用：確定性的特徵雜湊，英文依單字、中文依兩字組，共用字詞的文字才會相近，不連網）。
- 設定：`EMBED_PROVIDER`（`fake` / `nvidia`）、`EMBED_MODEL_ID`（非 fake 時必填，啟動時檢查；NVIDIA 需要金鑰）。**已在使用者的 `.env` 加上 `EMBED_PROVIDER=nvidia`、`EMBED_MODEL_ID=nvidia/nemotron-3-embed-1b`**（非機密設定）；`.env.example` 有說明。
- **分段**（`domains/newsroom/chunks.py`）：依段落合併到約 900 字，超過 1,400 字的段落在句尾（。！？.!?）切開，沒有句尾就硬切；**每段記錄它在證據原文中的起訖位置**，命中結果可以放回原文脈絡，引用時一律引證據原文、不是段落。
- **資料表 `evidence_chunks`**（migration `0014`）：段落文字、起訖位置、`halfvec(2048)` 向量（可為空）、算出向量的模型；HNSW 索引（cosine）。
- **`fetch_url` 擷取新證據時一併分段並算向量**，而且在寫入任何資料之前就算好，所以等 embedding 服務時不會占住公司的事件鎖。**embedding 失敗不影響擷取**：證據照存，段落沒有向量（失敗也記在 `model_calls`）。一頁最多先算 64 段，更長的部分只能用關鍵字找到。重用既有證據時不重算。
- **`search_evidence` 工具**（read）：把問句轉成向量，在自己公司的段落中找最近的（只比對同一個模型算的向量；過濾條件下用 pgvector 0.8 的 iterative scan，確保過濾後仍有 k 筆），可限定在某幾份證據內；向量不可用或沒有結果時退回**關鍵字搜尋**（英文 3 字以上的詞、中文兩字組），結果標示用的是哪種方法。回傳段落、所屬證據、起訖位置、分數，並提醒「引文必須與證據原文一致」。

### 過程中的問題
- `halfvec` 型別第一版：寫入時參數型別被改成 Text，自己的轉換函式沒被呼叫（asyncpg 收到 list 而報錯）；讀取時也因為轉成 Text 而拿到字串。改成用一個小的 TypeDecorator 負責寫入轉換、讀取時轉型後再宣告回向量型別。
- echo 的工作程序測試原本只把聊天模型固定成 fake，其他設定照開發者的 `.env`；加了 `EMBED_PROVIDER=nvidia` 之後，測試裡建立的工作程序會帶著真實的 embedding 設定（雖然 echo 用不到）。改成測試一律固定 fake embedding 與 fixture 工具，測試不依賴 `.env`、不連網。

### 驗證
- `tests/newsroom/test_chunks.py`（11 個）：分段（合併段落、每段可在原文定位、全文都被涵蓋、長段落依句尾切、無句尾硬切、空白文字）；雜湊向量（確定性、單位長度、共用字詞較近、中文兩字組）；NVIDIA 相容供應者（請求內容、依 index 排序、token 用量、429 / 5xx 可重試、4xx 不可）；`Embedder`（分批、每次呼叫一筆 `model_calls` 與成本、維度不符報錯、失敗也記錄）；設定檢查；`halfvec` 存取（0.25、−0.5、1.0 在半精度下完全相同）；`fetch_url` 產生段落與向量、段落都能在證據原文定位、重用時不重算；embedding 失敗仍擷取、搜尋退回關鍵字；`search_evidence` 以向量找到正確段落（含中文查詢）、限定證據、別家公司找不到。
- **真實 embedding（integration）**：`test_embed_live.py` 通過——2048 維、中文問句對相關英文段落的相似度比無關段落高出 0.2 以上、兩次呼叫各記一筆 `model_calls`。
- 後端 732 個測試通過；`ruff`、`lint-imports`、`make db-check` 通過；事件型別沒有變動。

---

## T-504 · 故事（Story）與去重

### 先量門檻
- 分群要一個「多像才算同一件事」的門檻，這取決於 embedding 模型，所以先用真實模型量 fixture 標題（免費呼叫）：同一事件的兩則英文標題 **0.73**；同一事件的中英版本 **0.57**；同一城市相關但不同的報導 **0.41～0.54**；無關 **0.12～0.21**。
- 中英同事件（0.57）與「相關但不同」（最高 0.54）太接近，硬要跨語言合併會誤併相關報導。**門檻定 0.65**：同語言的同一事件會合併，跨語言的暫時是兩個故事（研究員會把兩者都收成證據）。門檻是設定 `STORY_MATCH_THRESHOLD`，理由寫在 `stories.py`；`test_embed_live.py` 用真實模型檢查這個門檻仍然成立（換模型時會提醒）。

### 做了什麼
- **資料表**（migration `0015`）：
  - `stories`：標題、摘要、角度、狀態、分數（0～1）、項目數、不同來源數、首次出現、最新項目時間、**向量（成員項目的中心，`halfvec(2048)`、HNSW）**與模型、手動建立時的 `seed`（網址 / 查詢，研究員的起點）、選定後的專案。
  - `story_items`：項目屬於哪個故事（每個項目最多一個）、加入時的相似度（開頭那則為空）。
- **狀態機**（照 platform/02 §6）：DISCOVERED → SELECTED → IN_PRODUCTION → PUBLISHED；三個進行中的狀態都可以 → DROPPED；DISCOVERED → IGNORED。每次轉換都寫 `state_transitions` 稽核。
- **分群**（`domains/newsroom/stories.py` 的 `StoryDesk`，**不用語言模型**）：新排程 `newsroom.cluster_stories`（每 5 分鐘、比輪詢晚 2 分鐘，和輪詢排程一起在新增第一個來源時建立），處理還沒分群、7 天內的項目：
  1. 標題 + 摘要算向量（先算好才寫資料，不在等網路時占住事件鎖）；
  2. **同一個網址已經在某個故事裡 → 併入**（兩個 feed 連到同一篇）；
  3. 否則找 30 天內最相近的故事，相似度 ≥ 門檻 → 併入，並更新中心向量；
  4. 否則開新故事（DISCOVERED，發 `STORY_DISCOVERED`）。
  - **比對不分故事狀態**：已經在寫、寫過、被忽略、被放棄的故事也會吸收新項目，同一件事不會再開一次題（「我們寫過嗎」），被忽略的題目也不會換個來源又冒出來。
- **分數**（沒有模型）：0.5 × 佐證（不同來源數，3 個以上滿分）+ 0.3 × 新鮮度（最新項目，72 小時內線性降到 0）+ 0.2 × 來源平均信任度。每次有項目加入就重算。
- **生命週期操作**：`create`（手動建立，例如之後的「開始研究」，會算向量讓後來的項目能併入）、`select`（檢查專案屬於同一家公司、發 `STORY_SELECTED`）、`ignore`（只有稽核、不發事件）、`drop`（發 `STORY_DROPPED` 與原因）。IN_PRODUCTION / PUBLISHED 由之後的流程（T-514、T-512）推進。
- 事件：`STORY_DISCOVERED`、`STORY_SELECTED`、`STORY_DROPPED`（`{story_id, title, score}`，後兩個另有專案 / 原因）；時間軸顯示「發現題材：…・分數 57」「選定題材」「放棄題材：…・原因」。

### 過程中的問題
- `build_scheduler` 的修改第一次沒有生效：格式化工具先把那段程式碼換了行，我用文字取代的修改沒對上，只改到了說明文字。端到端的排程測試抓到了（「沒有 `newsroom.cluster_stories` 的處理器」），改用精確編輯補上。
- 同一個排程測試裡，分群的時段（T−8 分）比輪詢（T−5 分）早，第一次觸發時還沒有項目；把測試的時鐘往前推到下一個分群時段（T+2 分），和正式運作「:00 輪詢、:02 分群」一致。

### 驗證
- `tests/newsroom/test_stories.py`（9 個，向量用「依主題給定」的腳本，每個併入 / 新開的判斷都是確定的）：兩個 feed 的 5 則項目分成 3 個故事（微電網 3 則、2 個來源；相似度 0.6 < 0.65 的居民報導另開；咖啡另開），事件與相似度正確、再跑一次沒有待處理；同一網址從另一個來源進來會併入（相似度 1.0）；被忽略的故事吸收新項目、不開新題；超過 30 天的故事不比對；分數（1 個來源、剛發布、信任 0.5 → 0.567；一週前 → 0.267）；embedding 失敗什麼都不寫（排程之後重試）；選定 / 忽略 / 放棄、不合法的轉換、跨公司的專案被拒、稽核紀錄；**經過工作程序排程器：輪詢後分群**。
- 真實模型：`test_embed_live.py` 新增門檻檢查通過（同事件 ≥ 0.65、相關與無關 < 0.65）。
- 後端 741 個測試通過；`ruff`、`lint-imports`、`make db-check` 通過；event-schema 8 個、web 209 個測試與 typecheck、lint 通過。

---

## 提交紀錄

| 提交 | 日期 | 內容 | 持續整合 |
|---|---|---|---|
| `361d02f` | 2026-09-19 | T-504 故事、分群與去重 | ✅ 執行編號 `35428926753`（e2e 4 分 45 秒、python 1 分 41 秒、web 53 秒） |
| `7456a94` | 2026-09-19 | T-503 證據分段、`embed` binding、`search_evidence`；D-012 | ✅ 執行編號 `35427610141`（e2e 4 分 14 秒、python 1 分 39 秒、web 59 秒） |
| `e785fd7` | 2026-09-19 | T-502 `fetch_url` 與 Evidence、`read_evidence` | ✅ 執行編號 `35426803364`（e2e 5 分 13 秒、python 1 分 55 秒、web 1 分） |
| `074fae2` | 2026-09-19 | T-501 新聞來源與輪詢 | ✅ 執行編號 `35425897013`（e2e 5 分 2 秒、python 1 分 46 秒、web 1 分 8 秒） |
| `8dc5d52` | 2026-09-19 | 階段 5 開發計畫；T-500 `web_search`（Tavily + 離線語料） | ✅ 執行編號 `35424065962`（e2e 4 分 4 秒、python 1 分 45 秒、web 51 秒） |
