"use client";

// Budgets and capital on the admin dashboard (D-054): what the company has, what each envelope
// allows, and the two things an operator does about it. A budget is the AllocateBudget command
// (the CEO's and the CFO's too); capital is one capital_in row on the ledger.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { addCapital, financeQuery, queryKeys, setBudget, type Finance } from "@/api/queries";
import { formatMoney } from "@/features/dashboard/model";

const PERIODS: Record<string, string> = { cycle: "每期", day: "每日", month: "每月" };
const SCOPES: Record<string, string> = { company: "公司", project: "專案", business_unit: "事業單位" };

export function FinancePanel({ companyId }: { companyId: string }) {
  const finance = useQuery(financeQuery(companyId));
  return (
    <section className="mx-auto max-w-6xl px-4 pb-8" aria-labelledby="finance-heading">
      <h2 id="finance-heading" className="mb-3 text-lg font-semibold">
        預算與資金
      </h2>
      {finance.data ? (
        <FinanceView companyId={companyId} finance={finance.data} />
      ) : (
        <p className="text-sm text-muted">{finance.isError ? "讀不到資金資料。" : "載入中…"}</p>
      )}
    </section>
  );
}

export function FinanceView({ companyId, finance }: { companyId: string; finance: Finance }) {
  const money = (amount: string | number) => formatMoney(Number(amount), finance.currency);
  const zero = finance.budgets.filter((b) => b.hard_cap && Number(b.amount) === 0);
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <div className="rounded-xl border border-line bg-surface px-5 py-4">
        <p className="text-sm font-medium text-muted">帳上資金</p>
        <p className="mt-2 text-3xl font-semibold tabular-nums" data-testid="finance-balance">
          {money(finance.balance)}
        </p>
        <p className="mt-1 text-xs text-muted">
          今日已入帳支出 {money(finance.spent_today)}
          {finance.cycle_seq != null ? ` · 第 ${finance.cycle_seq} 期（${finance.cycle_stage}）` : ""}
        </p>
        <p className="mt-2 text-xs text-muted">CEO 依帳上資金規劃預算；帳上為 0 時，它只會分配 0。</p>
        <CapitalForm companyId={companyId} />
      </div>
      <div className="rounded-xl border border-line bg-surface px-5 py-4">
        <p className="text-sm font-medium text-muted">預算</p>
        {zero.length ? (
          <p className="mt-2 rounded border border-danger-line bg-danger-soft px-2 py-1 text-xs text-danger" role="alert">
            {zero.map((b) => b.name).join("、")} 的預算是 0（硬上限）：這些範圍內的工作都會被擋下。
          </p>
        ) : null}
        {finance.budgets.length ? (
          <ul className="mt-2 divide-y divide-line text-sm" data-testid="finance-budgets">
            {finance.budgets.map((b) => (
              <li key={b.id} className="flex justify-between gap-2 py-1.5">
                <span>
                  <span className="text-xs text-muted">{SCOPES[b.scope]}</span> {b.name}
                </span>
                <span className="tabular-nums">
                  {money(b.amount)} <span className="text-xs text-muted">{PERIODS[b.period] ?? b.period}</span>
                  {b.hard_cap ? null : <span className="ml-1 text-xs text-muted">（軟上限）</span>}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-2 text-sm text-muted">還沒有任何預算：不設就不限制。</p>
        )}
        <BudgetForm companyId={companyId} finance={finance} />
      </div>
    </div>
  );
}

function useRefresh(companyId: string) {
  const client = useQueryClient();
  return () => client.invalidateQueries({ queryKey: queryKeys.finance(companyId) });
}

function CapitalForm({ companyId }: { companyId: string }) {
  const refresh = useRefresh(companyId);
  const [amount, setAmount] = useState("");
  const [memo, setMemo] = useState("");
  const [requestId, setRequestId] = useState(() => crypto.randomUUID());
  const add = useMutation({
    mutationFn: () => addCapital(companyId, amount, memo.trim() || null, requestId),
    onSuccess: () => {
      setAmount("");
      setMemo("");
      setRequestId(crypto.randomUUID());
    },
    onSettled: refresh,
  });
  return (
    <form
      className="mt-4 flex flex-wrap items-end gap-2 text-sm"
      onSubmit={(e) => {
        e.preventDefault();
        add.mutate();
      }}
    >
      <label className="grid gap-1">
        <span className="text-xs text-muted">注資金額</span>
        <input
          required
          type="number"
          min="1"
          step="1"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          className="w-32 rounded border border-line bg-canvas px-2 py-1"
        />
      </label>
      <label className="grid flex-1 gap-1">
        <span className="text-xs text-muted">備註</span>
        <input
          value={memo}
          maxLength={200}
          onChange={(e) => setMemo(e.target.value)}
          placeholder="例：九月營運資金"
          className="rounded border border-line bg-canvas px-2 py-1"
        />
      </label>
      <button type="submit" disabled={add.isPending} className="rounded border border-line px-3 py-1 disabled:opacity-50">
        記入注資
      </button>
      {add.isError ? <p className="w-full text-xs text-danger">注資沒有記入：{String(add.error)}</p> : null}
    </form>
  );
}

function BudgetForm({ companyId, finance }: { companyId: string; finance: Finance }) {
  const refresh = useRefresh(companyId);
  const [scope, setScope] = useState(finance.projects[0]?.id ?? "");
  const [period, setPeriod] = useState<"cycle" | "day" | "month">("cycle");
  const [amount, setAmount] = useState("");
  const save = useMutation({
    mutationFn: () => setBudget(companyId, { amount, period, project_id: scope || null }),
    onSuccess: () => setAmount(""),
    onSettled: refresh,
  });
  const result = save.data;
  return (
    <form
      className="mt-4 flex flex-wrap items-end gap-2 text-sm"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <label className="grid gap-1">
        <span className="text-xs text-muted">範圍</span>
        <select value={scope} onChange={(e) => setScope(e.target.value)} className="rounded border border-line bg-canvas px-2 py-1">
          {finance.projects.map((p) => (
            <option key={p.id} value={p.id}>
              專案：{p.name}
            </option>
          ))}
          <option value="">整間公司</option>
        </select>
      </label>
      <label className="grid gap-1">
        <span className="text-xs text-muted">期間</span>
        <select
          value={period}
          onChange={(e) => setPeriod(e.target.value as "cycle" | "day" | "month")}
          className="rounded border border-line bg-canvas px-2 py-1"
        >
          {Object.entries(PERIODS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </label>
      <label className="grid gap-1">
        <span className="text-xs text-muted">金額（{finance.currency}）</span>
        <input
          required
          type="number"
          min="0"
          step="1"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          className="w-28 rounded border border-line bg-canvas px-2 py-1"
        />
      </label>
      <button type="submit" disabled={save.isPending} className="rounded border border-line px-3 py-1 disabled:opacity-50">
        設定預算
      </button>
      {result ? (
        <p className="w-full text-xs text-muted" role="status">
          {result.decision === "allow"
            ? `已設定${result.released_tasks ? `，${result.released_tasks} 個被預算擋下的任務已重新排隊` : ""}。`
            : `未設定：${result.reason ?? result.decision}`}
        </p>
      ) : null}
      {save.isError ? <p className="w-full text-xs text-danger">預算沒有設定：{String(save.error)}</p> : null}
    </form>
  );
}
