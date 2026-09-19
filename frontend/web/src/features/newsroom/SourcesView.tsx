"use client";

// The newsroom's sources (T-517): where stories come from, and a form to add one.
import { useState, type FormEvent } from "react";

import type { NewSource } from "@/api/queries";

import { formatTime, type SourceView } from "./model";
import { Badge, Empty } from "./parts";

const KINDS: Record<string, string> = { rss: "RSS / Atom", url_list: "網址清單", search_query: "搜尋" };

export function SourcesView({ sources }: { sources: readonly SourceView[] | undefined }) {
  if (!sources) return <Empty>載入中…</Empty>;
  if (sources.length === 0) return <Empty>還沒有來源。</Empty>;
  return (
    <ul className="divide-y divide-line rounded border border-line bg-surface text-sm">
      {sources.map((source) => (
        <li key={source.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
          <Badge text={source.status === "active" ? "啟用" : "暫停"} tone={source.status === "active" ? "ok" : "warn"} />
          <span className="font-medium">{source.name}</span>
          <span className="text-muted">{KINDS[source.kind] ?? source.kind}</span>
          <span className="truncate text-muted">{source.url ?? String(source.config.query ?? "")}</span>
          <span className="grow" />
          <span className="text-xs text-muted">
            信任度 {Number(source.trust_level).toFixed(1)}・{source.items} 則項目・
            {source.last_polled_at ? `上次讀取 ${formatTime(source.last_polled_at)}` : "尚未讀取"}
          </span>
        </li>
      ))}
    </ul>
  );
}

export function AddSourceForm({ onAdd }: { onAdd: (source: NewSource) => Promise<unknown> }) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState<NewSource["kind"]>("rss");
  const [target, setTarget] = useState("");
  const [trust, setTrust] = useState("0.5");
  const [language, setLanguage] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const lines = target.split(/\s+/).filter(Boolean);
    try {
      await onAdd({
        name,
        kind,
        url: kind === "rss" ? target.trim() : null,
        config: kind === "url_list" ? { urls: lines } : kind === "search_query" ? { query: target.trim() } : {},
        trust_level: trust,
        language: language || null,
        poll_interval_seconds: 3600,
      });
      setName("");
      setTarget("");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="grid gap-3 rounded border border-line bg-surface p-4 text-sm sm:grid-cols-2">
      <label className="grid gap-1">
        名稱
        <input required value={name} onChange={(e) => setName(e.target.value)} className="rounded border border-line bg-canvas px-2 py-1" />
      </label>
      <label className="grid gap-1">
        種類
        <select value={kind} onChange={(e) => setKind(e.target.value as NewSource["kind"])} className="rounded border border-line bg-canvas px-2 py-1">
          {Object.entries(KINDS).map(([value, text]) => (
            <option key={value} value={value}>
              {text}
            </option>
          ))}
        </select>
      </label>
      <label className="grid gap-1 sm:col-span-2">
        {kind === "rss" ? "Feed 網址" : kind === "url_list" ? "網址（空白或換行分隔）" : "搜尋字詞"}
        <textarea required rows={kind === "url_list" ? 3 : 1} value={target} onChange={(e) => setTarget(e.target.value)} className="rounded border border-line bg-canvas px-2 py-1" />
      </label>
      <label className="grid gap-1">
        信任度（0–1）
        <input type="number" min="0" max="1" step="0.1" value={trust} onChange={(e) => setTrust(e.target.value)} className="rounded border border-line bg-canvas px-2 py-1" />
      </label>
      <label className="grid gap-1">
        語言（選填，例：zh-TW）
        <input value={language} onChange={(e) => setLanguage(e.target.value)} className="rounded border border-line bg-canvas px-2 py-1" />
      </label>
      <div className="flex items-center gap-3 sm:col-span-2">
        <button type="submit" disabled={busy} className="rounded bg-accent px-3 py-1 text-accent-ink disabled:opacity-50">
          {busy ? "新增中…" : "新增來源"}
        </button>
        {error ? <span className="text-danger">{error}</span> : null}
      </div>
    </form>
  );
}
