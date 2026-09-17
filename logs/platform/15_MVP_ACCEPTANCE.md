# 15 — MVP Acceptance (Platform)

> 使用者故事層與系統層的 AC 以 `3d-office/11_MVP_ACCEPTANCE.md`（AC-1~10、AC-S1~S8、AC-11~14）為準。
> 本文件補充**平台層不變式**：即使沒有 3D / UI，Runtime 與 Company Engine 也必須滿足的條件。

| # | 場景 | 通過條件 |
|---|---|---|
| **P-1** 冷啟動 | `docker compose up` 後建立公司 + sources + project + 預算 | 下一觸發點 Cycle 自動建立，無人介入 |
| **P-2** Evidence 鏈 | 任一 PUBLISHED 文章 | 每個 fact/number/quote block 的 claim 都有 supports evidence；quote 可在 evidence.extracted_text 定位；兩語 claim 集合相同 |
| **P-3** Fact-check 攔截 | 植入無 evidence 的 claim | review 不通過 → revise → 修正後通過；超過 2 次 → story DROPPED |
| **P-4** Human approval | policy=human | publish 前 WAITING_APPROVAL；核准 60 秒內發布；拒絕 → REJECTED、task CANCELLED |
| **P-5** 預算閘門 | project 預算極低 | 下一個 model call 被拒 → ABORTED → BLOCKED_BUDGET → BUDGET_EXHAUSTED；加預算後自動恢復 |
| **P-6** 崩潰恢復 | 執行中 `kill -9` worker | lease 過期 → READY → 新 attempt 完成；無重複 article / evidence / transaction |
| **P-7** 成本歸屬 | 任一 cycle 結束 | Σ model_calls.cost_usd（cycle）== 該 cycle expense transactions 總額；每篇文章可查總成本 |
| **P-8** Analytics 閉環 | 公開站瀏覽 | analytics_events → analytics_daily → 隔日 snapshot `views_per_usd` 有值且 CycleReview 引用 |
| **P-9** 無限循環防護 | 強制 CEO 提案 100 workflows；強制 agent tool-call 循環 | 截斷至 max_workflows；max_steps 停止 |
| **P-10** 審計 | 任一文章 | cycle → plan → workflow → tasks → runs → steps → model_calls → evidence 全鏈可查 |
| **P-11** Domain 隔離 | `lint-imports` | 通過；刪除 `domains/newsroom` 後 runtime/company/realtime 測試全綠 |
| **P-12** 連續運作 | 7 cycles（可加速） | 無人工修復（審批除外）、無 FAILED cycle |
| **P-13** Policy 只能收緊 | 嘗試以 company_policies 放寬 role default | 被拒且記錄 |
| **P-14** Agent 無直寫 | 靜態掃描 + 測試 | agent context 內無 SQL / 無對 transactions、budgets、projects 的寫入 tool |
| **P-15** 模型無硬編碼 | `grep` | 程式碼中無 model id 字串；切換 provider 只改設定 |
| **P-16** Simulation 等價 | `MODEL_PROVIDER=fake` | 全部 P-1~P-12（P-7 成本為 0）通過；事件類型集合與真模型一致 |
