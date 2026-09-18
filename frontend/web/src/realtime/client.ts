// RealtimeClient (T-306, 3d-office/05 §3-4): keeps the realtime store in sync with one company.
//
//   1. hydrate: GET /api/companies/{id}/realtime/snapshot  -> store.hydrate
//   2. connect: WS /ws/companies/{id}?token=...&since=<store lastSeq>
//   3. apply:   EVENTS (backlog) and EVENT (live) -> store.applyEvents; EPHEMERAL -> liveProgress
//
// Failure handling:
// - Closed or broken socket: reconnect with exponential backoff (1 s, 2 s, 4 s ... 30 s, +-20 %
//   jitter), resuming from the store's lastSeq; the server replays what was missed. After 5
//   failures in a row the status is "offline" (UI banner); it keeps trying. The screen keeps
//   its last state meanwhile, never blanks.
// - SNAPSHOT_REQUIRED (too far behind, queue overflow, unknown since): re-hydrate, reconnect now.
// - Silence: no message (events or HEARTBEAT) for 2 heartbeat periods -> close and reconnect.
// - Tab back in front after more than 60 s without a message: re-hydrate (cheaper than a long
//   backlog, and the socket may have died unnoticed in the background).
// - ERROR unauthorized / not_found (or close 4401 / 4404): stop; retrying cannot help.
//
// No gap detection: the gateway guarantees an ordered, complete stream per connection and
// ends it with SNAPSHOT_REQUIRED when it cannot (05 §4 as amended in T-303). Duplicates at the
// backlog/live boundary are dropped by the reducer (seq <= lastSeq).
import type { StoreApi } from "zustand/vanilla";

import { realtimeStore, type ConnectionStatus, type RealtimeStoreState } from "@/stores/realtime";

import type { EphemeralMessage } from "./reducer";

export interface SocketLike {
  onopen: ((event: unknown) => void) | null;
  onmessage: ((event: { data: unknown }) => void) | null;
  onclose: ((event: { code: number; reason?: string }) => void) | null;
  onerror: ((event: unknown) => void) | null;
  send(data: string): void;
  close(code?: number, reason?: string): void;
}

export interface RealtimeClientOptions {
  apiUrl: string;
  wsUrl: string;
  companyId: string;
  getToken: () => string | null;
  store?: StoreApi<RealtimeStoreState>;
  createSocket?: (url: string) => SocketLike;
  fetchImpl?: typeof fetch;
  random?: () => number;
  heartbeatTimeoutMs?: number;
  backoffBaseMs?: number;
  backoffMaxMs?: number;
  offlineAfterFailures?: number;
  staleAfterHiddenMs?: number;
  ackEveryEvents?: number;
  ackIntervalMs?: number;
}

export class ClientHalted extends Error {}

const CLOSE_SNAPSHOT_REQUIRED = 4000;
const CLOSE_HEARTBEAT_TIMEOUT = 4002;
const CLOSE_REHYDRATE = 4003;
const HALT: Record<number, ConnectionStatus> = { 4401: "unauthorized", 4404: "not_found" };

export class RealtimeClient {
  private readonly store: StoreApi<RealtimeStoreState>;
  private readonly createSocket: (url: string) => SocketLike;
  private readonly fetchImpl: typeof fetch;
  private readonly random: () => number;
  private readonly heartbeatTimeoutMs: number;
  private readonly backoffBaseMs: number;
  private readonly backoffMaxMs: number;
  private readonly offlineAfterFailures: number;
  private readonly staleAfterHiddenMs: number;
  private readonly ackEveryEvents: number;
  private readonly ackIntervalMs: number;

  private socket: SocketLike | null = null;
  private running = false;
  private halted: ConnectionStatus | null = null;
  private needSnapshot = true;
  private failures = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private watchdog: ReturnType<typeof setTimeout> | null = null;
  private ackTimer: ReturnType<typeof setInterval> | null = null;
  private lastMessageAt = 0;
  private unacked = 0;

  constructor(private readonly options: RealtimeClientOptions) {
    this.store = options.store ?? realtimeStore;
    this.createSocket =
      options.createSocket ?? ((url) => new WebSocket(url) as unknown as SocketLike);
    this.fetchImpl = options.fetchImpl ?? fetch.bind(globalThis);
    this.random = options.random ?? Math.random;
    this.heartbeatTimeoutMs = options.heartbeatTimeoutMs ?? 30_000;
    this.backoffBaseMs = options.backoffBaseMs ?? 1_000;
    this.backoffMaxMs = options.backoffMaxMs ?? 30_000;
    this.offlineAfterFailures = options.offlineAfterFailures ?? 5;
    this.staleAfterHiddenMs = options.staleAfterHiddenMs ?? 60_000;
    this.ackEveryEvents = options.ackEveryEvents ?? 50;
    this.ackIntervalMs = options.ackIntervalMs ?? 5_000;
  }

  /** Start syncing (idempotent). */
  start(): void {
    if (this.running) return;
    this.running = true;
    this.halted = null;
    this.failures = 0;
    this.ackTimer = setInterval(() => this.flushAck(), this.ackIntervalMs);
    void this.open();
  }

  /** Stop syncing: close the socket, cancel every timer. The store keeps its state. */
  stop(): void {
    this.running = false;
    this.clearTimers();
    const socket = this.socket;
    this.socket = null;
    if (socket) {
      socket.onclose = null;
      socket.close(1000, "client stopped");
    }
    this.setStatus("idle");
  }

  /** Call with document visibility changes (see attachToDocument). */
  setVisible(visible: boolean): void {
    if (!visible || !this.running || this.halted) return;
    if (Date.now() - this.lastMessageAt > this.staleAfterHiddenMs) {
      this.needSnapshot = true;
      if (this.socket) this.abandon(this.socket, CLOSE_REHYDRATE, "stale after background");
      else this.scheduleReconnect(0);
    }
  }

  // --- connection ---------------------------------------------------------------------------

  private async open(): Promise<void> {
    if (!this.running || this.halted) return;
    this.setStatus(this.failures === 0 ? "connecting" : this.reconnectingStatus());
    try {
      const company = this.store.getState().company;
      if (this.needSnapshot || !company || company.companyId !== this.options.companyId) {
        await this.hydrate();
        this.needSnapshot = false;
      }
    } catch (error) {
      if (error instanceof ClientHalted) return;
      this.failures += 1;
      this.scheduleReconnect(this.backoff());
      return;
    }
    if (!this.running) return;

    const since = this.store.getState().company?.lastSeq ?? 0;
    const token = this.options.getToken() ?? "";
    const url =
      `${this.options.wsUrl}/ws/companies/${this.options.companyId}` +
      `?token=${encodeURIComponent(token)}&since=${since}`;
    const socket = this.createSocket(url);
    this.socket = socket;
    let opened = false;
    socket.onopen = () => {
      opened = true;
    };
    socket.onmessage = (event) => {
      opened = true;
      this.onMessage(socket, event.data);
    };
    socket.onclose = (event) => this.onClose(socket, event.code);
    // Browsers follow an error with a close event, but Node's WebSocket (undici) does not when
    // the connection is refused: it stays CONNECTING forever. A failure to connect is therefore
    // handled here; onClose ignores a socket that was already handled.
    socket.onerror = () => {
      if (!opened) this.onClose(socket, 1006);
    };
    this.lastMessageAt = Date.now();
    this.armWatchdog(socket);
  }

  private async hydrate(): Promise<void> {
    const response = await this.fetchImpl(
      `${this.options.apiUrl}/api/companies/${this.options.companyId}/realtime/snapshot`,
      { headers: { Authorization: `Bearer ${this.options.getToken() ?? ""}` } },
    );
    if (response.status === 401 || response.status === 404) {
      this.halt(response.status === 401 ? "unauthorized" : "not_found");
      throw new ClientHalted(String(response.status));
    }
    if (!response.ok) throw new Error(`snapshot: HTTP ${response.status}`);
    this.store.getState().hydrate(await response.json());
  }

  private onMessage(socket: SocketLike, data: unknown): void {
    if (socket !== this.socket) return;
    this.lastMessageAt = Date.now();
    this.armWatchdog(socket);
    let message: Record<string, unknown>;
    try {
      message = JSON.parse(String(data));
    } catch {
      return;
    }
    const state = this.store.getState();
    switch (message.type) {
      case "HELLO":
        this.syncClock(message.server_time);
        if (message.mode !== "snapshot_required") {
          this.failures = 0;
          this.setStatus(message.mode === "live" ? "live" : "connecting");
        }
        break;
      case "EVENTS":
        this.count(state.applyEvents((message.items as unknown[]) ?? []));
        break;
      case "BACKLOG_DONE":
        this.setStatus("live");
        break;
      case "EVENT": {
        const envelope = { ...message };
        delete envelope.type; // the rest of the message is the event envelope
        this.count(state.applyEvents([envelope]));
        break;
      }
      case "EPHEMERAL":
        state.applyEphemeral(message as unknown as EphemeralMessage);
        break;
      case "HEARTBEAT":
        this.syncClock(message.server_time);
        break;
      case "SNAPSHOT_REQUIRED":
        this.needSnapshot = true; // the server closes the socket next
        break;
      case "ERROR":
        if (message.code === "unauthorized") this.halt("unauthorized");
        else if (message.code === "not_found") this.halt("not_found");
        break;
    }
  }

  private onClose(socket: SocketLike, code: number): void {
    if (socket !== this.socket) return;
    this.socket = null;
    this.clearWatchdog();
    if (!this.running || this.halted) return;
    if (HALT[code]) {
      this.halt(HALT[code]);
      return;
    }
    if (code === CLOSE_SNAPSHOT_REQUIRED || code === CLOSE_REHYDRATE) {
      this.needSnapshot = true;
      this.scheduleReconnect(0);
      return;
    }
    this.failures += 1;
    this.setStatus(this.reconnectingStatus());
    this.scheduleReconnect(this.backoff());
  }

  // --- helpers ------------------------------------------------------------------------------

  /** 1 s, 2 s, 4 s ... capped, with +-20 % jitter. */
  backoff(failures = this.failures): number {
    const base = Math.min(this.backoffBaseMs * 2 ** Math.max(failures - 1, 0), this.backoffMaxMs);
    return Math.round(base * (0.8 + 0.4 * this.random()));
  }

  private reconnectingStatus(): ConnectionStatus {
    return this.failures >= this.offlineAfterFailures ? "offline" : "reconnecting";
  }

  private scheduleReconnect(delayMs: number): void {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      void this.open();
    }, delayMs);
  }

  private armWatchdog(socket: SocketLike): void {
    this.clearWatchdog();
    this.watchdog = setTimeout(() => {
      if (socket !== this.socket) return;
      this.abandon(socket, CLOSE_HEARTBEAT_TIMEOUT, "no heartbeat");
    }, this.heartbeatTimeoutMs);
  }

  /** Close a socket and handle it now: do not rely on its close event ever arriving (a stuck
   * CONNECTING socket may never send one). The later close event, if any, is ignored. */
  private abandon(socket: SocketLike, code: number, reason: string): void {
    try {
      socket.close(code, reason);
    } catch {
      // closing a socket that never opened may throw in some implementations
    }
    this.onClose(socket, code);
  }

  private clearWatchdog(): void {
    if (this.watchdog) clearTimeout(this.watchdog);
    this.watchdog = null;
  }

  private clearTimers(): void {
    this.clearWatchdog();
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.ackTimer) clearInterval(this.ackTimer);
    this.reconnectTimer = null;
    this.ackTimer = null;
  }

  private halt(status: ConnectionStatus): void {
    this.halted = status;
    this.clearTimers();
    this.setStatus(status);
  }

  private count(applied: number): void {
    this.unacked += applied;
    if (this.unacked >= this.ackEveryEvents) this.flushAck();
  }

  private flushAck(): void {
    if (!this.unacked || !this.socket) return;
    const lastSeq = this.store.getState().company?.lastSeq;
    this.socket.send(JSON.stringify({ type: "ACK", last_seq: lastSeq }));
    this.unacked = 0;
  }

  private syncClock(serverTime: unknown): void {
    if (typeof serverTime !== "string") return;
    const server = Date.parse(serverTime);
    if (!Number.isNaN(server)) {
      this.store.getState().setConnection({ serverOffsetMs: server - Date.now() });
    }
  }

  private setStatus(status: ConnectionStatus): void {
    if (this.store.getState().connection.status !== status) {
      this.store.getState().setConnection({ status });
    }
  }
}

/** Wire a client to the page's visibility. Returns a function that removes the listener. */
export function attachToDocument(client: RealtimeClient, doc: Document = document): () => void {
  const listener = () => client.setVisible(doc.visibilityState === "visible");
  doc.addEventListener("visibilitychange", listener);
  return () => doc.removeEventListener("visibilitychange", listener);
}
