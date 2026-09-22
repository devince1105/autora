# 開發紀錄 07 — 階段 7 營收（Revenue）

- 期間：2026-09-22 起（進行中）
- 目標：至少一條真實營收通路，ROI 進入 CEO 決策。
- 驗收條件（`platform/13_DEVELOPMENT_ROADMAP.md` §2）：一筆真實 revenue 由 webhook 寫入，並出現在 Dashboard 與 CycleReview；Marketing 超額被攔。
- 設計依據：`platform/06_BUSINESS_REVENUE.md`（實體、Ledger、Payment 只由整合寫入）、`platform/14_TASK_BREAKDOWN.md` §2（T-701 ～ T-708）、`platform/11_EVENT_CATALOG.md`。
- 相關決策：D-018（`customers` 不存個資）、**D-022**（訂閱 + Stripe、只用自有網站、T-701 的範圍）。
- 使用模型：Claude Opus 5。

## 進入條件（2026-09-22）

`13` §5 的 Gate：前一階段驗收全過 + 本階段的 P0 問題有答案。

- 階段 6：T-601 ～ T-612 完成，雛形收尾三件（尾-1 ～ 尾-3）今天做完，CI 全綠（`cd5fe2a`）。
- 本階段卡住的兩題由使用者回答：
  - **營收模型**：付費訂閱，金流商 Stripe。
  - **發布通路**：先只用自有網站。之後想串 Instagram、Threads 等社群，每週固定時間產出。記為 T-704 的方向，開做時另立決策。

兩題記在 D-022。

## 開發計畫

| 順序 | 任務 | 內容 | 狀態 |
|---|---|---|---|
| 1 | T-701 | 訂閱要用的三張表：價格、訂閱、付款 | ✅ |
| 2 | T-702 | Stripe webhook：簽章驗證、事件對應、冪等 → 呼叫 T-701 的函式 | 待做 |
| 3 | T-707 | 營收 KPI 進 CompanySnapshot 與 Dashboard | 待做 |
| 4 | T-705 | Finance 代理（只讀 + 預算提案） | 待做 |
| 5 | T-706 | Business 代理 | 待做（要先看 T-611 的 strategist 已經涵蓋多少） |
| 6 | T-708 | 階段 7 驗收 | 待做 |
| — | T-703 | 廣告花費上限 | 延後（D-022：沒有付費廣告） |
| — | T-704 | 外部通路 adapter | 延後（D-022：方向是 IG / Threads 每週產出） |

T-702 排在 T-707 前面，理由是驗收要「真實的 revenue 由 webhook 寫入」。沒有 webhook，KPI 只能拿手寫的資料來算。

---

## T-701 · 價格、訂閱、付款

### 開工前的盤點：九張表裡已經有三張

任務清單寫的是 products、customers、leads、opportunities、subscriptions、orders、payments、campaigns、campaign_spend 九張表。這份清單是營收模型還沒決定時寫的，所以把每一種可能都列了進去。對照現況：

| 表 | 現況 | 這次 |
|---|---|---|
| `products` | T-600 已做（組織 v2） | 不動 |
| `customers` | T-612 已做（D-018） | 不動，由訂閱呼叫 |
| `opportunities` | T-611 已做，而且意思不一樣：那裡是「可能成為一門生意的機會」，不是銷售漏斗裡的商機 | 不動 |
| `subscriptions`、`payments` | 沒有 | **新增** |
| （清單外）`prices` | 沒有 | **新增**。理由見下 |
| `leads` | 沒有 | **不做**：訂閱站的 lead 就是一個 email，而這家公司不存個資（D-018） |
| `orders` | 沒有 | **不做**：沒有一次性商品 |
| `campaigns`、`campaign_spend` | 沒有 | **不做**：只用自有網站，沒有付費廣告。T-703 一起延後 |

**為什麼另外加 `prices`，而不是在 `products` 加一個價格欄位**：價格會變，產品不會。月費從 5 塊調到 6 塊時，舊訂閱仍然是用 5 塊賣出去的，它的付款也是 5 塊。所以價格是一張表：調價就是把舊的一列退休、新增一列，每個訂閱都指向它當初成交的那個價格。這也是 Stripe 自己的模型（Product 跟 Price 分開），T-702 對應起來是一對一。

### 放在哪一層：`company/`，不是 `domains/business/`

路線圖原本寫 `autora/domains/business/**`。改放 `company/subscriptions.py`，理由跟 D-018 把 `customers` 放在 company 一樣：**每種產業收錢的方式都一樣**。新聞媒體、教育、SaaS 的訂閱，都是「一個價格、一個週期、金流商說錢到了」。`test_core_vocabulary` 照樣全綠，這一層沒有出現任何領域詞彙。

**Stripe 在核心裡只是一個字串**：`provider = 'stripe'`，資料庫檢查它的格式（`^[a-z][a-z0-9_]*$`），但沒有任何程式寫 `if provider == "stripe"`。Stripe 的 SDK、webhook 簽章、事件名稱對應，全部是 T-702 adapter 的事。

### 三條規則

1. **每個寫入都以金流商的 id 做冪等。** 金流商一定會重送同一個事件，第二次送達不能造成任何改變：不會多一個訂閱、多一筆付款，也不會多一列帳本。訂閱靠 `(company_id, provider, external_ref)` 唯一；付款靠 `(provider, external_ref)` 唯一，對應的帳本列的冪等鍵是 `payment:{provider}:{external_ref}`。
2. **訂閱是承諾，付款才是錢。** 只有 `record_payment` 會寫帳本。訂閱變成 ACTIVE 不會入帳，因為那只代表金流商準備去扣款，扣成功了才有收入。帳本記的是**實際扣到的金額**，不是牌價（proration、折扣都會讓兩者不同）。
3. **不存個資**，延續 D-018。三張表都只有金流商的 id 和金額。測試會掃欄位名稱，出現 name、email、card 之類的字就會紅。

### 狀態機

```
TRIALING → ACTIVE | PAST_DUE | CANCELED
ACTIVE   → PAST_DUE | CANCELED
PAST_DUE → ACTIVE | CANCELED
CANCELED   （終點）
```

- **PAST_DUE 可以回到 ACTIVE**：卡片扣款失敗後金流商會重試，重試成功就回來了。PAST_DUE 仍然算「付費中」，因為多數重試會成功。
- **CANCELED 是終點**：同一個人回來訂閱，是一筆新的訂閱。舊的那筆仍然記得他什麼時候離開。
- **同一個狀態再送一次不算轉移**，只更新 `current_period_end`。金流商的 `subscription.updated` 事件大多是這種。
- **取消客戶的最後一個訂閱，就等於客戶流失**：呼叫 `customers.churn`。如果他還有另一個訂閱沒取消，就不算流失。

### 付款只能新增

`payments` 掛上 migration 0002 的那個 `autora_forbid_mutation()` trigger，跟 `transactions` 一樣。退款會是另一列，不會去改原來那一列。

### 一個刻意拒絕、不去猜的情況

如果帳本裡已經有 `payment:stripe:in_xxx` 這個鍵，卻沒有任何一筆付款指向它，代表有人手動寫過帳本。這時兩條路都不對：安靜地回傳會把這筆付款弄丟，另外再寫一列又會被唯一鍵擋下。所以直接丟出錯誤，說明原因（`test_a_ledger_row_with_no_payment_behind_it_is_refused_not_guessed`）。

### 附帶的小改動

`Ledger.record` 多了 `product_id` 參數。`transactions.product_id` 這個欄位在 T-600 就有了，但一直沒有寫入的路徑，所以「每個產品賺多少」到今天之前一直答不出來。

### 自己寫錯、修掉的

- 第一版測試用了 `Ledger().revenue()`，但這個方法不存在。帳本只有 `balance()`，改用它（這些測試的公司只有收入，所以 balance 就等於收入）。
- `pytest.raises` 包在 `begin_nested()` 裡面，順序寫反了：例外被 `pytest.raises` 接住之後，savepoint 以為一切正常而嘗試 RELEASE，結果在已經中止的交易上失敗。改成 `pytest.raises` 在外、savepoint 在內。
- 稽核列用 `created_at` 排序，但 `state_transitions` 沒有這個欄位，時間欄位叫 `at`。而且 `at` 是 `now()`，同一個交易裡兩列的值會一樣，所以改用 id 排序（uuid7 本身就是依寫入時間排序）。
- 刪掉一個自己寫的測試：它檢查「enum 的每個值都在 CHECK 約束裡」，但約束本來就是從 enum 產生的，這個測試永遠不會失敗。
- `test_company_catalog_complete` 紅了一次，這是預期中的：它是事件目錄的絆線，新增事件時必須同步更新清單。已經更新清單，也更新了 `11_EVENT_CATALOG.md`（新增 Revenue 群組；PAYMENT_RECEIVED 不再「移至 Business 群組」）。

### 驗證

- `pytest tests/db/test_business_models.py tests/company/test_subscriptions.py`：26 個測試通過。
- `alembic upgrade head` → `downgrade -1` → `upgrade head` 來回一次；`alembic check` 回報 models 與 migration 沒有差異。
- `make lint-py`（ruff、`lint-imports` 2 kept）、`make lint-web`（typecheck）、`make gen-schema`（前端事件型別已重新產生）。
- 完整 Python 測試：1395 個加上新增的 26 個全部通過；web 312 個、event-schema 8 個通過。

### 留給 T-702 的

- **回頭客**：`customers.acquire` 遇到已經流失的客戶，會直接回傳那一列，`churned_at` 還在。同一個人流失之後又回來訂閱，`paying()` 不會算到他。要不要「復活」一個客戶（還是把回頭客當成新的一列），要等看到 Stripe 實際送來的資料再決定。
- **事件順序**：Stripe 不保證 webhook 的送達順序。T-701 的 `move` 對不合法的轉移（例如 CANCELED → ACTIVE）會丟 `IllegalTransition`。T-702 要決定遇到這種情況時，是去跟 Stripe 查目前的狀態，還是直接略過。

---

## 提交紀錄

| 提交 | 日期 | 內容 | 持續整合 |
|---|---|---|---|
| `c870c87` | 2026-09-22 | T-701：價格、訂閱、付款三張表（migration 0035）、`company/subscriptions.py`；D-022 | ✅ 執行編號 `35728147255`（e2e 7 分 26 秒、python 4 分 3 秒、web 1 分 21 秒） |
