// How long the browser takes to handle what arrives on the socket (AC-S7: p95 < 100 ms).
//
// One message in, one sample out: parsing it, reducing it into the store and notifying the
// subscribers is the whole of "handling" — everything after that is React's and the renderer's,
// which the FPS floor covers instead. The last SAMPLE_CAP samples are kept in a ring, so the
// recorder costs one number per message and never grows.
//
// The numbers are read by the soak test through window.__autoraRealtime; nothing in the app
// reads them, and nothing branches on them.
export const SAMPLE_CAP = 500;

export interface HandlingStats {
  /** Messages handled since the page loaded (not since the last trim). */
  count: number;
  /** Events inside them: a backlog arrives as one message carrying many. */
  events: number;
  p50: number;
  p95: number;
  max: number;
}

export function percentile(values: readonly number[], p: number): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.floor((p / 100) * sorted.length))];
}

export class Latency {
  private samples: number[] = [];
  private at = 0;
  count = 0;
  events = 0;

  record(ms: number, events = 1): void {
    this.count += 1;
    this.events += events;
    if (this.samples.length < SAMPLE_CAP) this.samples.push(ms);
    else {
      this.samples[this.at] = ms;
      this.at = (this.at + 1) % SAMPLE_CAP;
    }
  }

  stats(): HandlingStats {
    return {
      count: this.count,
      events: this.events,
      p50: round(percentile(this.samples, 50)),
      p95: round(percentile(this.samples, 95)),
      max: round(Math.max(0, ...this.samples)),
    };
  }

  reset(): void {
    this.samples = [];
    this.at = 0;
    this.count = 0;
    this.events = 0;
  }
}

const round = (ms: number) => Math.round(ms * 100) / 100;

/** The one the store records into. */
export const handling = new Latency();

declare global {
  interface Window {
    __autoraRealtime?: { stats(): HandlingStats; reset(): void };
  }
}

if (typeof window !== "undefined") {
  window.__autoraRealtime = { stats: () => handling.stats(), reset: () => handling.reset() };
}
