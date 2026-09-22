# 開發紀錄 07 — 階段 7 營收（Revenue）

- 期間：2026-09-22 起（進行中）
- 目標：至少一條真實營收通路，ROI 進入 CEO 決策。
- 驗收條件（`platform/13_DEVELOPMENT_ROADMAP.md` §2）：一筆真實 revenue 由 webhook 寫入，並出現在 Dashboard 與 CycleReview；Marketing 超額被攔。
- 設計依據：`platform/06_BUSINESS_REVENUE.md`（實體、Ledger、Payment 只由整合寫入）、`platform/14_TASK_BREAKDOWN.md` §2（T-701 ～ T-708）、`platform/11_EVENT_CATALOG.md`。
- 相關決策：D-018（`customers` 不存個資）、**D-022**（只用自有網站、T-701 的範圍；其中「Stripe 訂閱」已被 D-024 取代）、**D-023**（新台幣為主幣別）、**D-024**（統一金流單次收款、一年使用權、讀者 Email 登入）。
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
| 1a | D-023 | 新台幣為主幣別（計量維持美元，結算時換算） | ✅ |
| 1b | D-024 | T-701 改成一年使用權（migration 0037） | ✅ |
| 1c | D-024 | 讀者 Email 登入 | 待做 |
| 2 | T-702 | 統一金流單次收款：建立訂單 → 付款頁 → 通知網址驗證簽章 → 記付款、開通一年 | 待做 |
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

## 方向調整（2026-09-22）：Stripe 不能用

T-701 推上去之後，使用者告知**台灣的銀行帳戶不支援 Stripe**。接下來的對話做了三個決定：

1. **金流改用統一金流（PAYUNi）**。綠界的審核太麻煩；統一金流的個人會員可以申請（信用卡額度每月 20 萬、撥款 T+7）。先用個人身分收款，營運金額超過一定程度再轉成公司。
2. **不做定期扣款，改成一次付款買一年使用權**。我原本查到統一金流的定期扣款要另外申請幕後授權和信用卡 Token，還在規劃「由我們的系統每期發動扣款」。使用者指出這樣太繞了：只要收一次款，給一年的使用權就好。這樣連額外權限都不用申請，信用卡、ATM、超商代碼都能收。
3. **讀者用 Email 登入**。「給對方一年使用權」的前提是網站認得這個人，但公開站目前不用登入（T-515），系統也不存個資（D-018）。有兩種做法：Email 登入連結，或完全不存個資的兌換碼。使用者選了 Email 登入。

另外，統一金流只收新台幣，所以要先處理**幣別**。

**T-701 已經做好的部分沒有白費**：金流商在程式裡只是一個字串，三張表、付款冪等、只能新增不能修改、不存個資這些都照樣適用。要改的是「訂閱」的語意（見下一批）。

---

## D-023 · 新台幣為主幣別

### 問題

帳本、預算、報表、CEO 看到的數字、審批門檻全部是美元（寫死的）。統一金流收進來的是新台幣。兩種幣別放進同一張表直接加總，餘額就會是錯的。

### 設計：兩種錢分開，帳本只有一種幣別

盤點之後，系統裡的「錢」其實有兩種：

| | 是什麼 | 幣別 |
|---|---|---|
| **計量** | 每一次模型呼叫、搜尋花了多少；每次執行、每個任務的上限；預留 | **維持美元**。供應商就是用美元收費，這是事實，換算只會多一層誤差 |
| **公司的錢** | 帳本、預算、收入、報表、Dashboard、CEO 的快照、審批門檻 | **新台幣** |

兩者交會的地方各自只有一個轉換點，全部經過 `infra/money.py`：

- **帳本**：`Ledger.record` 是唯一的入口。非主幣別的金額在這裡換算，並在同一列記下 `source_amount`、`source_currency`、`fx_rate`。每天結算模型費用時傳入 `currency="USD"`，由這裡換成新台幣。
- **報表**：把計量的美元換成新台幣再加總（Reporting、Dashboard KPI）。
- **成本守門員**：預算是新台幣，花費是美元。**移動的是預算**：預算只是一個數字，花費卻是很多筆。

放在 infra 層，是因為 runtime 的守門員和 company 層都要用，而 runtime 不能 import company。

### 匯率

先用設定檔裡的固定值 `FX_RATES={"USD":"32"}`，手動更新。每一列都記著自己當時用的匯率，所以之後改匯率或接台銀的每日匯率，都不會改寫過去的帳。遇到沒有匯率的幣別時直接拒絕（`FxError`），不會用猜的。

### 改名

指標名稱不再帶幣別：`cost_usd` → `cost`，`revenue_usd` → `revenue`，`profit_usd` → `profit`，`model_cost_usd` → `model_cost`，單位就是主幣別。`views_per_usd` → `views_per_cost_unit`。policy 的 `governance.*_limit_usd` 拿掉 `_usd`。名稱如果寫著 usd，值卻是新台幣，那個名稱就是在說謊。

計量的欄位（`model_calls.cost_usd`、`tasks.budget_usd`、`per_run_usd`）**名稱不變**，因為它們確實是美元。

### 預設值照 32 換算

CEO 撥款門檻 $5 → NT$160、探索上限 $2 → NT$64、擴大事業 $50 → NT$1600、模擬預設撥款 NT$160。行為跟改之前完全一樣，只是單位換了。這些值使用者之後可以調。

### 既有資料（migration 0036）

用 32 一次換算：交易（保留原始美元和匯率；因為 append-only，只在這一個語句期間暫停 trigger，做完馬上恢復）、預算、KPI 快照、停損條件、目標、policy。降級可以完整還原。

**不換算的**：
- 計量表：本來就是美元。
- 付款與價格：記的是金流商實際收的幣別。
- 凍結的提案：核准指向的是當時那份文件。
- cycle 的計畫與覆盤：那天寫下的內容。

驗證：另外開一個暫時的資料庫，塞入一組美元樣本（交易、預算、policy、停損條件、KPI 快照、目標），跑升級 → 檢查 → 降級 → 檢查 → 再升級。1.5 美元 → 48 新台幣（`source_amount` 1.5、`fx_rate` 32）；預算 10 → 320；門檻 50 → 1600；`cost_usd > 2` → `cost > 64`；`views_per_usd < 20` → `views_per_cost_unit < 0.625`。降級後全部回到原值。暫時的資料庫已經刪除。

### 畫面

Dashboard 本來就用 `Intl.NumberFormat` 並帶入 API 回傳的幣別，所以不用改。在 zh-TW 語系下，新台幣顯示為「$87.50」，美元顯示為「US$87.50」。Cycle 列表原本寫死「US$」，改為依幣別格式化。代理面板和 trace 裡每次執行的成本本來就是計量，維持「US$」。給代理看的文字（命令摘要、每日摘要）用 `format_money` 輸出「NT$160」。CEO 的提示多加一句：所有金額都是快照裡 `capital.currency` 的幣別。

### 測試怎麼改（重點：沒有東西壞掉，改的是測試的單位）

完整測試第一次跑出 29 個失敗，每一個都是「測試假設帳本和報表是美元」，沒有一個是換算寫錯。逐一處理的方式：

| 測試 | 改法 |
|---|---|
| `test_ledger` | 用固定的 `Fx(TWD, USD=32)`。結算的列是新台幣，並檢查 `source_amount`、`fx_rate`。P-7 改成「計量的美元 == 帳本各列 `source_amount` 的總和」，另外驗證新台幣總額 == Σ(原始金額 × 該列匯率)。 |
| `test_reporting` | 第一個測試用 32，驗證換算（$0.50 → NT$16）。其餘測試關心的是「哪些呼叫算進哪個範圍」，用 1:1 匯率保留原本的算術，並在檔頭說明原因。 |
| `test_cost_guard` | 預算改寫成新台幣（3.20 = $0.10），行為與原本相同。 |
| policy、預算閘門驗收、Dashboard KPI、治理、摘要、新聞室比率、cycles API | 數字改成新台幣，並註明原本的美元值。 |

新增的測試：
- 主幣別的錢照原樣入帳。
- 之後改匯率，不會改寫已經入帳的列。
- 沒有匯率的幣別會被拒絕。
- **守門員比較的是金額、不是數字**：NT$1.50 這個數字比一次 $0.05 的呼叫大，但實際上是比較少的錢，必須擋下；NT$2.00 = $0.0625 則放行。
- Cycle 列表以新台幣顯示。

### 驗證

- 完整 Python 測試：1400 個通過。
- web 312 個（Cycle 列表的測試多了一條檢查）、event-schema 8 個通過。
- `make lint-py`、`make lint-web`、`gen-schema-check`、`gen-api-check`、`alembic check` 都通過。

---

## D-024 · T-701 改成一年使用權

### 改了什麼

「訂閱」在 T-701 裡是 Stripe 那一邊的物件：它有自己的 id、自己的狀態（試用、付費中、逾期、取消），也由它負責按期扣款。改成單次付款之後，金流商那邊沒有任何「訂閱」可以對應，這張表就失去了意義。所以換成**使用權**（`memberships`）：

| | 之前（D-022） | 現在（D-024） |
|---|---|---|
| 是什麼 | 金流商的訂閱物件在我們這邊的副本 | 我們自己的紀錄：誰可以看哪個產品、看到什麼時候 |
| 狀態 | TRIALING / ACTIVE / PAST_DUE / CANCELED | ACTIVE / EXPIRED |
| 誰改變它 | 金流商的通知 | 付款（延長）與時間（到期） |
| 一筆付款買到什麼 | 沒有記錄 | 付款上記 `grants_from` → `grants_until` |
| 價格 | 有金流商的 id | 我們自己的，下單時帶出去 |

一個客戶對一個產品只有**一列**使用權：續購時延長這一列，過期後回來時重新打開這一列。每一段時間是由哪一筆付款買的，記在付款上。

### 規則

- **提早續購不會浪費**：還沒到期就付款，新的一年從原本的到期日往後接。例如 6 月付款、9 月才到期，新的一年從 9 月開始算。
- **過期後才買，從付款那天起算**，不會往回補。
- **能不能看，看的是日期**（`expires_at > now`），在讀者請求的當下判斷。EXPIRED 是每天由 cycle 寫一次的帳務紀錄，所以就算那天的結算還沒跑，時間一到也看不了（`test_access_ends_on_time_even_before_the_books_catch_up`）。
- **到期處理掛在「進入 MEASURING」時，排在帳本與報表之前**，所以報表算付費人數時，今天到期的人已經流失了。
- **到期不一定是流失**：如果客戶還有別的產品在有效期內，就不算流失。
- **流失的時間點是到期那一刻**，不是系統發現的那一刻。
- **回頭客**：重開同一個客戶（`customers.reactivate`，發出 CUSTOMER_RETURNED 並記錄離開幾天），不會新增一個客戶，所以 CUSTOMER_ACQUIRED 仍然只有一次。**已知代價**：`churned_at` 會被清掉，所以查詢空窗期內某個時間點的 `paying(at=...)`，會把他算成付費中。空窗期本身仍然記在事件和每筆付款的期間裡，哪天報表需要再從那裡算。
- 一年的算法是「明年的同一天」。2/29 買的，到期日是隔年的 2/28；月費在 1/31 購買，到期日是 2/28。
- 入帳類別從 `subscription` 改成 `membership`。

### migration 0037

- 刪掉 `subscriptions`。
- 付款表：拿掉 `subscription_id`，加上 `price_id`、`membership_id`、`grants_from`、`grants_until`，以及一條約束：「買了使用權，就要說清楚是哪一段」。
- 價格表：拿掉金流商的 id。

`subscriptions` 只存在了一天，除了測試從來沒被寫入過。**即使如此，migration 發現表裡有資料時仍會拒絕刪除**，降級時也一樣，不會默默丟掉任何一列。

**我在這裡犯的錯**：
1. 第一版先刪 `subscriptions` 表、後拆掉付款表指向它的外鍵，升級失敗了。
2. 我用一串指令同時做升級和降級，第一步失敗後後面還是繼續跑，於是開發資料庫被退回到 0035。再升回來時，0036 的換算重新跑了一次；當時開發資料庫裡沒有美元資料，所以沒有影響。
3. 之後改用 shell 變數存 alembic 指令，但 zsh 不會拆開變數裡的空白，那串指令其實一個都沒執行，還一度讓人以為成功了。

最後改成一步一步執行，並檢查每一步的輸出：升級 → 降級 → 升級 → `alembic check`，全部正常。

### 驗證

- `tests/db/test_business_models.py`：13 個。
  - 三張表的欄位都沒有個資。
  - 付款不能修改、不能刪除，同一筆扣款不能變成兩筆。
  - 買了使用權就要有完整的期間（三種錯法各一個案例）。
  - 使用權的到期日必須晚於開始日；一個客戶對一個產品只有一列。
  - 價格的三個約束。
- `tests/company/test_memberships.py`：18 個。
  - 日曆（含閏年、月底）。
  - 價格：預設新台幣、退休的產品不能訂價、退休的價格不能購買。
  - 付一次得到一年，並成為客戶；收入記到該客戶與產品。
  - 同一個通知送兩次：一筆付款、一列帳本、一年。
  - 帳本記的是實際收到的金額。
  - 提早續購接在原本的到期日後面。
  - 到期即流失；到期當下就不能看；還有別的產品在有效期內就不算流失。
  - 回頭客重開同一列；每個客戶的價值。
  - 帳本已有同一個鍵、卻沒有付款指向它時，拒絕而不猜測。
- 完整 Python 測試：1405 個通過。`make lint-py`、`make lint-web`、`gen-schema`、`gen-api-check`、`alembic check` 都通過。

---

## 提交紀錄

| 提交 | 日期 | 內容 | 持續整合 |
|---|---|---|---|
| `c870c87` | 2026-09-22 | T-701：價格、訂閱、付款三張表（migration 0035）、`company/subscriptions.py`；D-022 | ✅ 執行編號 `35728147255`（e2e 7 分 26 秒、python 4 分 3 秒、web 1 分 21 秒） |
| `d9646ee` | 2026-09-22 | D-023：新台幣為主幣別（計量維持美元、結算時換算）、migration 0036；記下 D-024 | ✅ 執行編號 `35732693591`（e2e 6 分 56 秒、python 3 分 35 秒、web 52 秒） |
| `82542a5` | 2026-09-22 | D-024：一次付款買一年使用權，`memberships` 取代 `subscriptions`（migration 0037） | ✅ 執行編號 `35734051682`（e2e 5 分 3 秒、python 3 分 50 秒、web 1 分 21 秒） |
