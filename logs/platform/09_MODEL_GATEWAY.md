# 09 — Model Gateway

原則：Agent 程式碼裡沒有 model name。第一階段只用一個 frontier model（Claude，id 來自設定檔），架構支援未來多供應商與本地模型。

---

## 1. 呼叫鏈

```
Agent ──▶ ModelGateway.complete(ModelRequest)
            │  ModelRequest {capability, messages, tools?, output_schema?, budget_ctx{run,task,project,company}, cache_hint}
            ├─▶ ModelRouter.resolve(role, capability, company_policy) → alias("frontier"|"fast"|"cheap"|"embed")
            │                                                        → ModelBinding{provider, model_id, price_table}
            ├─▶ CostGuard.reserve(budget_ctx, est_tokens)     # 超額 → BudgetExceeded → run ABORTED
            ├─▶ CircuitBreaker.check(provider)
            ├─▶ Provider.complete(...)                        # AnthropicProvider (MVP) | OpenAI | Google | OpenAICompatibleLocal | Fake
            ├─▶ StructuredOutput.validate(output_schema)      # 失敗 → issues → AgentRunner repair
            ├─▶ CostGuard.settle(actual) → model_calls row
            └─▶ ModelResponse {content, tool_calls, usage, cost_usd, model_id, cache_hit, latency_ms}
```

## 2. Provider 介面

```
class ModelProvider(Protocol):
    name: str
    def complete(req) -> ModelResponse
    def embed(texts, model_id) -> list[vector]
    def count_tokens(messages, model_id) -> int
    price_table: dict[model_id, {in, out, cache_read, cache_write}]   # USD / 1M tokens
```

新增供應商 = 一個 adapter + price 表；Agent 零改動。**Embedding 是獨立 binding**（alias `embed`），可指向不同供應商（Open Question）。

## 3. Model Policy（設定檔；Phase 6 入 `model_policies` 表）

```
aliases:
  frontier: {provider: anthropic, model_id: ${FRONTIER_MODEL_ID}}
  fast:     {provider: anthropic, model_id: ${FAST_MODEL_ID}}       # MVP 可 = frontier
  embed:    {provider: ${EMBED_PROVIDER}, model_id: ${EMBED_MODEL_ID}}
routing:                     # (role, capability) → alias
  ceo.reasoning: frontier
  research.research_extraction: frontier
  analyst.reasoning: frontier
  writer.drafting: frontier
  editor.editing: frontier
  editor.verification: frontier
  marketing.drafting: frontier   # P2 → fast
  "*.embedding": embed
fallback:
  frontier: [fast]             # provider 熔斷時
```

capability 詞彙：`reasoning`、`research_extraction`、`drafting`、`editing`、`verification`、`embedding`。

## 4. 成本

- `model_calls`：run_id、task_id、project_id、company_id、provider、model_id、alias、tokens_in/out、cache_read/write、cost_usd、latency_ms、status。
- 預算三層在 `reserve` 檢查；`settle` 後差額回補。
- Prompt caching：system prompt + snapshot 標記為 cache 前綴；`cache_hit` 記錄。

## 5. FakeModelProvider（simulation）

- 依 `(role, task_name, attempt)` 回傳 fixture 結構化輸出；可設延遲、可設「第 N 次失敗」分支。
- 產生真實 `model_calls`（cost=0）與真實事件；Agent 程式碼不變。
- 切換：`MODEL_PROVIDER=fake`。

## 6. 測試

- Router 解析與 fallback。
- Structured output 驗證失敗 → issues 結構。
- CostGuard reserve/settle 一致。
- `grep` 程式碼無 model id 字串。
- Anthropic integration smoke（usage、cache 標記）。
