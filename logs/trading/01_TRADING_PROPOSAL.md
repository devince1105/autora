# 01 — AI 交易代理：架構提案與 Phase 0（OKX 模擬盤）

> 狀態：**提案，尚未實作**。對應決策 D-037（`logs/DECISIONS.md`）。
> 範圍：Autora 的第二個真實業務 `domains/trading`——代理讀 BTC-USDT 行情、決定 BUY / SELL / HOLD，由程式檢查風險、送單、記帳、檢討績效。

---

## 0. 終點與路徑

使用者的理想是：**給它一筆錢，讓它自己想辦法賺錢。**

這份提案把它當成終點，但不從終點出發。自主程度分成四級，每升一級都要**上一級的證據**加上**一筆人做的決策**，代理自己不能升級自己：

| 級 | 名稱 | 錢 | 誰按下單 | 升級條件（必要，不充分） |
|---|---|---|---|---|
| 0 | 模擬盤（本文 Phase 0） | OKX demo，假錢 | 程式自動送 | —— |
| 1 | 小額實盤、每筆人核准（D-037） | 真錢 US$100 | 人按核准，程式送 | §9 Phase 0 驗收全過 |
| 2 | 小額實盤、限額內自動 | 真錢 US$100 | 程式自動送（上限內） | Phase 1 ≥ 4 週、≥ 30 筆真實成交、對帳零差異；**另立一筆決策修改 policy 第 3 條的適用範圍** |
| 3 | 「自己想辦法」 | 人決定是否加碼 | 同上 | 代理可**提案**新策略、新幣種、新本金；每一項都是等人審的指令（照 D-031 財務代理的模式），不會自己生效 |

**誠實的預期**：大多數 LLM 直接判斷漲跌的策略，扣掉手續費後贏不了「買了放著」。所以績效從第一天就要跟基準線比（§7），而不是只看賺賠。這個實驗最先證明的是**閉環可靠**，其次才是**有沒有優勢**。

---

## 1. 系統架構

### 1.1 在 Autora 裡的位置

```
backend/autora/
  infra/exchanges/okx.py        ← OKX REST 客戶端（簽章、demo/live、只有讀與下單，沒有提領的程式碼）
  domains/trading/              ← 新 domain，只依賴 runtime、company（import-linter 規則不變）
    models.py                   ← 資料表（§4）
    market.py                   ← 行情快照與技術指標（純程式，不叫 LLM）
    risk.py                     ← Risk Engine（純函式，§5.2）
    orders.py                   ← Order Engine：意圖 → OKX 訂單 → 成交回寫
    books.py                    ← 交易帳：持倉、平均成本、已實現損益、對帳
    performance.py              ← 每日損益、基準線、每週檢討
    agents/trader.py            ← 交易代理：行為、脈絡、結構化輸出、驗證器
    workflow.py                 ← 範本 trading.decide_v1、排程
    policy.py / events.py / simulation.py
```

`autora/app.py` 照現有方式註冊（events、models、policy、templates、tools、behaviors、fake model）。

### 1.2 一次決策的流程（workflow `trading.decide_v1`）

```
排程（預設每 1 小時）
  │
  ▼
[snapshot]  system 節點：抓 ticker、K 線、餘額 → 算指標 → 寫 trading_market_snapshots
  │
  ▼
[decide]    agent 節點：交易代理讀快照、持倉、近期決策與檢討 → 輸出 TradeDecision（結構化）
  │         HOLD → 記錄後結束
  ▼
[risk]      system 節點：Risk Engine 檢查 → 通過 / 拒絕（拒絕也記錄，附原因）
  │
  ▼
[approve]   human 節點（Phase 1 才有；Phase 0 不存在這個節點）
  │
  ▼
[execute]   system 節點：執行前**重跑一次** Risk Engine（價格與持倉可能變了）→ 送 OKX
  │
  ▼
[reconcile] system 節點：查訂單與成交 → 寫 trading_fills → 更新帳
```

另外兩個排程，不經 LLM：

- **對帳**（每 5 分鐘）：還沒終結的訂單去 OKX 查狀態；交易帳餘額與 OKX 餘額比對，不一致就暫停交易並發事件。
- **每日結算**（每天 00:05 Asia/Taipei）：寫 `trading_daily_pnl`、算基準線；每週一再叫代理寫一次檢討（§7）。

### 1.3 最重要的一條邊界

> **交易代理手上沒有下單工具。**

代理只有唯讀工具，它的「決定」是一份結構化輸出。把決定變成訂單的是 system 節點，中間一定經過 Risk Engine。這比「給它下單工具、再靠 policy 擋」更強：就算 prompt injection 或模型亂來，它也沒有東西可以按。

---

## 2. 技術選型

| 項目 | 選擇 | 理由 |
|---|---|---|
| 語言、框架 | 沿用 Python 3.12、SQLAlchemy async、Alembic、FastAPI | 同一個 repo，不另起堆疊 |
| OKX 客戶端 | **自己用 `httpx` 寫一個薄客戶端**，不用 ccxt 或官方 SDK | 只需要約 8 個端點；自己寫才能保證**程式裡根本沒有提領、轉帳、槓桿的方法**；httpx 已是依賴 |
| 技術指標 | 純 Python（`decimal`）實作 EMA、RSI、ATR、區間高低點 | 量很小（幾百根 K 線），不值得為此加 pandas / numpy |
| 金額 | `Decimal` + DB `Numeric`，永遠不用 float | 跟 `infra/money.py` 的原則一致 |
| 排程 | 現有 `runtime/scheduler.py`（`schedules` 表 + croniter） | 已經處理多 worker 不重複觸發 |
| 模型 | 現有 Model Gateway，交易代理用 `frontier` alias | 每次呼叫已記 `model_calls`，成本自動歸到交易專案 |
| 人工核准 | 現有 `runtime/approvals`（human task 節點 + `on_decided` hook） | Inbox 與 3D Approval Desk 直接能用 |

---

## 3. OKX API 整合

> 下列端點與參數依 OKX v5 API 撰寫；**TR-01 實作時要逐一與官方文件核對**，差異記在 devlog。

### 3.1 帳號與金鑰

1. **開一個 OKX 子帳戶專門給 Autora**，只轉入 US$100 USDT。主帳戶的錢，代理在物理上碰不到——這是比任何程式檢查都硬的上限。
2. 在子帳戶建立 API key：權限只勾 **Read + Trade**，不勾 Withdraw；**綁定 IP 白名單**（worker 的出口 IP）。
3. 模擬盤要另外建立 **demo trading 專用的 API key**（實盤 key 不能用在模擬盤，反之亦然）。
4. 金鑰只放在 **trading worker 容器**的環境變數：`OKX_API_KEY`、`OKX_API_SECRET`、`OKX_API_PASSPHRASE`、`OKX_ENV=demo|live`。API 與 web 容器沒有這些變數。
5. `.env.example` 只列變數名稱，不放值；推送前照慣例做秘密掃描。

### 3.2 簽章

每個私有請求帶四個 header：`OK-ACCESS-KEY`、`OK-ACCESS-SIGN`、`OK-ACCESS-TIMESTAMP`（ISO 8601，毫秒、UTC）、`OK-ACCESS-PASSPHRASE`。
`SIGN = Base64(HMAC_SHA256(secret, timestamp + METHOD + requestPath(+query) + body))`。
模擬盤另加 `x-simulated-trading: 1`。**客戶端建構時就綁定 demo 或 live，執行期不能切換。**

### 3.3 會用到的端點（全部）

| 用途 | 端點 | 公開 / 私有 |
|---|---|---|
| 商品規格（最小下單量、數量精度、價格精度） | `GET /api/v5/public/instruments?instType=SPOT&instId=BTC-USDT` | 公開 |
| 即時價 | `GET /api/v5/market/ticker?instId=BTC-USDT` | 公開 |
| K 線 | `GET /api/v5/market/candles?instId=BTC-USDT&bar=1H&limit=200` | 公開 |
| 餘額 | `GET /api/v5/account/balance?ccy=BTC,USDT` | 私有・Read |
| 下單 | `POST /api/v5/trade/order` | 私有・Trade |
| 查單 | `GET /api/v5/trade/order?instId=…&clOrdId=…` | 私有・Read |
| 成交明細 | `GET /api/v5/trade/fills?instId=…&ordId=…` | 私有・Read |
| 撤單（限價單才需要） | `POST /api/v5/trade/cancel-order` | 私有・Trade |

**程式裡不存在**：提領、資金劃轉、槓桿設定、合約、借貸的任何方法。測試會檢查客戶端類別的公開方法清單，多一個就失敗。

### 3.4 下單參數

- `instId=BTC-USDT`、`tdMode=cash`（現貨、無槓桿）、`ordType=market`（Phase 0–1 只用市價單）。
- 買：`side=buy`、`tgtCcy=quote_ccy`、`sz=<USDT 金額>`（例如 `10`）。
- 賣：`side=sell`、`sz=<BTC 數量>`，依 `lotSz` 向下取整。
- `clOrdId` = 我們的訂單 ID（英數、≤ 32 字元）。**同一個 `clOrdId` 送兩次，OKX 會拒絕第二次**；重試因此是安全的：送單逾時就用 `clOrdId` 去查，而不是再送一次。
- 手續費以 `fills` 回傳的 `fee`、`feeCcy` 為準（現貨買單的手續費通常從拿到的 BTC 扣）。

### 3.5 失敗處理

| 情況 | 做法 |
|---|---|
| 網路逾時、5xx | 用 `clOrdId` 查單；查不到才標記 FAILED，**不自動重送** |
| 4xx（參數錯誤、餘額不足） | FAILED，記錄 OKX 的 `sCode`/`sMsg`，不重試 |
| 限流（429） | 退避後重試**查詢類**請求；下單不重試 |
| 連續 3 筆訂單 FAILED | governance 暫停交易代理（沿用 `pause_agent`），要人恢復 |

---

## 4. 資料模型

全部以 `trading_` 開頭、帶 `company_id`；金額 `Numeric(28, 12)`；時間 `timestamptz`。

### `trading_market_snapshots` — 代理做決定時看到的東西

| 欄位 | 說明 |
|---|---|
| `id`, `company_id`, `inst_id`, `taken_at` | |
| `last`, `bid`, `ask` | ticker |
| `candles` (jsonb) | 最近 N 根 K 線原始值 |
| `indicators` (jsonb) | EMA20/50、RSI14、ATR14、24h 高低、成交量變化 |
| `balance` (jsonb) | 當下 USDT、BTC 餘額 |

### `trading_decisions` — 每一次決定（包括 HOLD）

| 欄位 | 說明 |
|---|---|
| `id`, `company_id`, `run_id`, `snapshot_id` | 追回當次 agent run 與它看到的行情 |
| `action` | `BUY` / `SELL` / `HOLD` |
| `quote_amount` | BUY：要花多少 USDT |
| `base_amount` | SELL：要賣多少 BTC |
| `confidence` | 0–1，之後拿來檢查校準 |
| `reason` | 決策理由（必須引用快照裡的數字，驗證器檢查） |
| `strategy` | 策略標籤，Phase 0 固定 `llm_discretionary_v1` |
| `stop_loss`, `take_profit` | 選填；Phase 0–1 **只記錄、只提醒**，不自動掛單（§5.4） |
| `model_id`, `created_at` | |

### `trading_orders` — 從決定到交易所

| 欄位 | 說明 |
|---|---|
| `id` | 同時作為 `clOrdId` |
| `decision_id`, `company_id`, `env` (`demo`/`live`) | |
| `side`, `ord_type`, `requested_quote`, `requested_base` | |
| `risk_verdict` (jsonb) | 每一條檢查的結果與當下數值（通過也存） |
| `approval_id` | Phase 1 才有 |
| `state` | 見下方狀態機 |
| `okx_ord_id`, `submitted_at`, `closed_at` | |
| `avg_px`, `filled_base`, `filled_quote` | 成交彙總 |
| `error` (jsonb) | OKX 錯誤碼與訊息 |

狀態機：

```
PROPOSED ─risk 拒絕→ REJECTED
   │
   ├─(Phase 1) AWAITING_APPROVAL ─人拒絕→ REJECTED
   │                            ─逾時→ EXPIRED
   ▼
APPROVED ─執行前 risk 重檢不過→ REJECTED
   │
   ▼
SUBMITTED → FILLED | PARTIALLY_FILLED → FILLED | CANCELLED | FAILED
```

### `trading_fills` — 成交明細（交易所為準）

`id`、`order_id`、`trade_id`（OKX 的成交 ID，**unique**，對帳重跑不會重複寫入）、`px`、`sz`、`fee`、`fee_ccy`、`filled_at`、`realized_pnl`（賣出成交才有，平均成本法計算）。

### `trading_daily_pnl` — 每日結算

`date`（Asia/Taipei）、`equity_open`、`equity_close`、`realized_pnl`、`unrealized_pnl`、`fees_usdt`、`model_cost_usdt`、`net_pnl`、`baseline_hold_btc`、`baseline_cash`、`trades`、`holds`、`risk_rejections`。

### 需求欄位對照

| 需求的欄位 | 存在哪裡 |
|---|---|
| symbol | `trading_orders` 經 `decision → snapshot.inst_id` |
| action | `trading_decisions.action` |
| price | `trading_orders.avg_px`、`trading_fills.px` |
| quantity | `trading_fills.sz`、`trading_orders.filled_base` |
| position size | 由 `books.py` 從成交算出；每次決定時的持倉存在 `snapshot.balance` |
| timestamp | 各表的 `created_at`、`filled_at` |
| strategy | `trading_decisions.strategy` |
| AI decision / decision reason | `trading_decisions.action`、`reason`、`confidence` |
| stop loss / take profit | `trading_decisions.stop_loss`、`take_profit` |
| order ID | `trading_orders.id`（clOrdId）、`okx_ord_id` |
| execution result | `trading_orders.state`、`error` |
| fee | `trading_fills.fee`、`fee_ccy` |
| realized PnL | `trading_fills.realized_pnl`，彙總在 `trading_daily_pnl` |

**帳分開記**：交易帳以 USDT 計價，只存在上面這些表，不寫進公司的新台幣 `transactions`（D-023）。公司帳本仍然只記模型費用；交易損益出現在報表時換算成新台幣並註明匯率。

---

## 5. 責任邊界

### 5.1 交易代理（LLM）——只負責判斷

| 能做 | 不能做 |
|---|---|
| 讀行情快照、持倉、自己過去的決定與結果、上週檢討 | 下單、撤單（**手上沒有這些工具**） |
| 輸出一份 `TradeDecision`（BUY/SELL/HOLD、金額、理由、信心、停損停利） | 改風險上限、改排程、改策略、改自己的 prompt |
| HOLD（不交易是正常結果，不是失敗） | 讀網路、讀新聞（Phase 0 不給；之後可以借新聞室已審的證據，另案決定） |
| 每週寫一份檢討；**提案**改策略（等人審，不自動生效） | 升級自己的自主等級 |

工具（全部 `side_effect=read`）：`get_market_snapshot`、`get_position`、`list_recent_decisions`、`get_performance`。

驗證器（輸出不合格就要求重寫，照現有 validator 模式）：

- `action` 為 BUY 時 `quote_amount` 必填且 > 0；SELL 時 `base_amount` 必填；HOLD 時兩者皆空。
- `reason` 至少引用一個快照裡實際存在的數值（例如 RSI、價格），且數值與快照一致。
- `stop_loss` < 現價 < `take_profit`（有填的話）。

驗證器**不檢查風險上限**——那是 Risk Engine 的事。代理提一個 $50 的買單不是格式錯誤，是會被風險拒絕的決定，而且拒絕要被記錄下來，檢討時看得到。

### 5.2 Risk Engine——決定性、不可由代理修改

`risk.py` 是一個純函式：`check(decision, account_state, market, limits, now) -> RiskVerdict`。同樣的輸入永遠得到同樣的輸出，每一條都有單元測試。

**上限的來源**：

- `HARD_LIMITS`：程式碼裡的 frozen dataclass 常數。要改只能改程式碼、經過 review、另記一筆決策。
- 公司政策 `trading.limits`：**只能收緊**（每一項取 `min(hard, policy)`），照 policy engine 第 4 條的精神。
- 代理沒有任何工具能讀寫這兩者以外的東西，也沒有能改公司政策的指令權限。

| 檢查 | Phase 0/1 上限 | 不過時 |
|---|---|---|
| 總開關 `TRADING_ENABLED`、公司政策 `trading.paused`、代理未被暫停 | 必須為開 | 拒絕 |
| 商品 | 只有 `BTC-USDT`、`SPOT`、`tdMode=cash` | 拒絕 |
| 分配資金 | 交易帳總權益 ≤ US$100 起始 + 已實現獲利（**不會動用子帳戶裡超出分配的錢**） | 拒絕 |
| 單筆金額 | ≤ US$10（買賣皆然）；≥ OKX 最小下單量 | 拒絕 |
| 買後持倉 | BTC 市值 ≤ US$30 | 拒絕 |
| 可用餘額 | 買：USDT ≥ 金額 × 1.002（手續費緩衝）；賣：BTC ≥ 數量 | 拒絕 |
| 每日虧損 | 當日（Asia/Taipei）權益變化 ≤ −US$3 → **禁止買進**直到隔日；賣出仍允許（降低風險） | 拒絕買 |
| 交易頻率 | 每日 ≤ 6 筆；兩筆之間 ≥ 30 分鐘 | 拒絕 |
| 行情新鮮度 | 快照 ≤ 2 分鐘；執行時現價與決策時價差 ≤ 0.5% | 拒絕（下一輪重來） |
| 價差 | (ask − bid) / mid ≤ 0.1% | 拒絕 |

`RiskVerdict` 存每一條的名稱、結果、當下數值與上限，整份寫進 `trading_orders.risk_verdict`。

### 5.3 Order Engine——只負責正確地執行

- 只接受 `RiskVerdict.passed = true` 的訂單；執行前**再跑一次** `check`（Phase 1 從提案到核准可能過了很久）。
- 送單、以 `clOrdId` 冪等、查單、寫成交、推進狀態機。
- 不做判斷：不調整金額、不改方向、不自己補單。

### 5.4 停損與停利

Phase 0–1 **只記錄、只提醒**：對帳排程發現價格穿過代理寫的停損或停利時，發事件並在下一次決策的脈絡裡告訴代理，由它決定要不要 SELL（仍經 Risk Engine，Phase 1 仍要人核准）。

理由：自動停損就是「不經人核准的真錢下單」，這要等第 2 級的那筆決策。**已知限制**：Phase 1 最壞情況是 US$30 持倉在人沒回應時一路跌；上限因此就是那 US$30。

### 5.5 人

- 建立 OKX 子帳戶與 API key、設 IP 白名單、決定轉入多少錢。
- Phase 1：核准或拒絕每一筆訂單（Inbox／3D Approval Desk）。
- 任何時候：一鍵暫停（`trading.paused` 或 `pause_agent`）。
- 決定升級（§0）；審核代理的策略提案。

---

## 6. 與現有規則的關係

| 規則 | 交易 domain 怎麼處理 |
|---|---|
| policy 第 3 條：不可逆動作一定要人 | Phase 0：模擬盤訂單不是真錢，送單的 system 節點不經代理的工具；live 用的送單路徑在 Phase 1 **一定**經過 human 節點。第 2 級要另立決策 |
| D-001 核准逾時「任務保留等待、不取消」 | **交易例外**：訂單核准 10 分鐘逾時就 EXPIRED 並取消任務——價格已經變了，舊的決定不該留著被按。用 `on_decided` hook 與逾時處理實作 |
| D-035 新聞室不給建議 | 交易 domain **不接公開網站**；新聞室不引用交易代理的看法；交易頁只在後台（需登入） |
| D-023 新台幣為主幣別 | 交易帳是 USDT，不進 `transactions`；報表換算時註明匯率 |
| 模型費用 | 照現有 cost guard 記到交易專案；績效一律算「扣掉模型費之後」 |

---

## 7. 績效分析

每日結算（程式）：

- 淨損益 = 已實現 + 未實現 − 手續費 − 模型費（換成 USDT）
- **基準線**：(a) 全部拿現金不動 = 0；(b) 第一天把 US$30 買成 BTC 放著；(c) 每天定額買 US$1（DCA）到 US$30 為止
- 勝率、平均賺／賠、最大回撤、HOLD 比例、被風險拒絕的次數與原因
- **信心校準**：信心 0.8 的決定，事後有沒有比信心 0.5 的準

每週檢討（代理）：讀上週的數字與每筆決定，寫一份檢討（存為代理的記憶，下一次決策會讀到）。它可以附上**策略修改提案**，提案是一道等人審的指令，不會自己生效（照 D-031 的模式）。

---

## 8. Phase 0 開發步驟（OKX 模擬盤）

任務編號用 `TR-xx`（`T-8xx` 已被 platform/14 的 Phase 8 使用）。

| # | 任務 | 依賴 | 驗收 |
|---|---|---|---|
| TR-01 | `infra/exchanges/okx.py`：簽章、demo/live 綁定、§3.3 的 8 個端點、錯誤分類 | — | 簽章以官方文件範例驗證；fake transport 測試；**公開方法清單測試**（沒有提領、劃轉） |
| TR-02 | 資料表與 migration（§4） | — | migration 上下可逆；unique 約束（`trade_id`、`clOrdId`）有測試 |
| TR-03 | `market.py`：快照與指標 | TR-01 | 指標對照手算的固定資料 |
| TR-04 | `risk.py`：Risk Engine 與 `HARD_LIMITS` | TR-02 | **每一條檢查至少一個通過、一個拒絕的測試**；公司政策放寬被忽略的測試 |
| TR-05 | `orders.py`：送單、冪等、查單、狀態機 | TR-01, TR-02, TR-04 | 逾時後以 `clOrdId` 查回、不重送；重複成交不重複寫 |
| TR-06 | `books.py`：持倉、平均成本、已實現損益、對帳 | TR-05 | 固定成交序列算出的損益對照手算；對帳不一致會暫停 |
| TR-07 | 交易代理：行為、脈絡、結構化輸出、驗證器、fake model 模擬 | TR-03 | `MODEL_PROVIDER=fake` 下跑完 BUY、SELL、HOLD 三種路徑 |
| TR-08 | workflow `trading.decide_v1`、三個排程、總開關、governance 暫停 | TR-04~07 | 整條流程在 fake model + fake OKX 下跑通；總開關關閉時什麼都不送 |
| TR-09 | `performance.py`：每日結算、基準線、每週檢討 | TR-06, TR-07 | 固定資料的結算對照手算 |
| TR-10 | 後台頁（需登入）：決定、訂單、損益、風險拒絕列表；事件接上 3D 辦公室 | TR-08 | e2e 看得到一筆完整紀錄 |
| TR-11 | **Phase 0 驗收**：接上 OKX demo 實跑 14 天 | 全部 | 見 §9 |

## 9. Phase 0 驗收（進入 Phase 1 的條件）

- [ ] OKX demo 連續跑 14 天，≥ 20 次非 HOLD 的決定真的送到交易所
- [ ] 每一筆訂單都查得到：它的快照、決定理由、風險檢查、成交、手續費、損益
- [ ] 交易帳與 OKX demo 餘額每日對帳**零差異**
- [ ] 每一條風險檢查在實跑或測試中都被觸發過至少一次
- [ ] 總開關、`trading.paused`、連續失敗暫停都實際測過
- [ ] 一份 14 天的績效報告（含三條基準線與模型費）

**績效好壞不是升級條件**。模擬盤的成交條件跟實盤不同，這裡驗的是閉環可靠。

## 10. Phase 1 追加（真錢、每筆人核准）

| # | 任務 |
|---|---|
| TR-12 | human 節點 `approve_order`：核准卡片顯示決定、理由、風險檢查、現價；10 分鐘逾時即 EXPIRED |
| TR-13 | `RUNBOOK.md` 加一節：OKX 子帳戶、Read+Trade 金鑰、IP 白名單、轉入 US$100、切換 `OKX_ENV=live` |
| TR-14 | 對帳警示：不一致時暫停並通知人（email，沿用 `infra/email.py`） |
| TR-15 | Phase 1 驗收：≥ 4 週、≥ 30 筆真實成交、對帳零差異、人核准／拒絕比例與理由的統計 |

## 11. 還沒決定的事（實作前或實作中要問）

| # | 問題 | 暫用預設 |
|---|---|---|
| Q-tr-cadence | 多久決策一次 | 每 1 小時；模型費用或限流吃不消就改 4 小時 |
| Q-tr-model | 交易代理用哪個模型 | 目前的 `frontier` alias（D-006：`z-ai/glm-5.3-flash`，免費但有限流、供應者會記錄輸入輸出——行情資料不敏感，可接受） |
| Q-tr-news | 代理要不要讀新聞室已審的證據 | Phase 0 不讀；之後另立決策 |
| Q-tr-notify | Phase 1 核准通知管道 | email；手機推播之後再說 |
| Q-tr-tz | 「每日」的切點 | Asia/Taipei 00:00 |
