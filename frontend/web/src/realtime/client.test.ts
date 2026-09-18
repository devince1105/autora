import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createRealtimeStore } from "@/stores/realtime";

import { RealtimeClient, type SocketLike } from "./client";

const fixture = JSON.parse(
  readFileSync(new URL("./__fixtures__/contract.json", import.meta.url), "utf8"),
) as {
  snapshot_before: { company_id: string; last_seq: number };
  events: { seq: number }[];
  snapshot_after: { last_seq: number };
};
const COMPANY = fixture.snapshot_before.company_id;

class FakeSocket implements SocketLike {
  onopen: SocketLike["onopen"] = null;
  onmessage: SocketLike["onmessage"] = null;
  onclose: SocketLike["onclose"] = null;
  onerror: SocketLike["onerror"] = null;
  sent: unknown[] = [];
  closedWith: number | null = null;
  constructor(readonly url: string) {}
  send(data: string) {
    this.sent.push(JSON.parse(data));
  }
  close(code = 1000) {
    this.closedWith = code;
    queueMicrotask(() => this.onclose?.({ code }));
  }
  /** Server side. */
  push(message: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(message) });
  }
  drop(code = 1006) {
    this.onclose?.({ code });
  }
  get since(): number {
    return Number(new URL(this.url).searchParams.get("since"));
  }
}

function setup(overrides: Partial<ConstructorParameters<typeof RealtimeClient>[0]> = {}) {
  const store = createRealtimeStore();
  const sockets: FakeSocket[] = [];
  const snapshots: unknown[] = [fixture.snapshot_before];
  const fetchImpl = vi.fn(async (url: string) => {
    expect(url).toBe(`http://api.test/api/companies/${COMPANY}/realtime/snapshot`);
    const body = snapshots.length > 1 ? snapshots.shift() : snapshots[0];
    return new Response(JSON.stringify(body), { status: 200 });
  });
  const client = new RealtimeClient({
    apiUrl: "http://api.test",
    wsUrl: "ws://api.test",
    companyId: COMPANY,
    getToken: () => "tok en",
    store,
    createSocket: (url) => {
      const socket = new FakeSocket(url);
      sockets.push(socket);
      return socket;
    },
    fetchImpl: fetchImpl as unknown as typeof fetch,
    random: () => 0.5, // no jitter: backoff is exactly 1 s, 2 s, 4 s ...
    ...overrides,
  });
  const last = () => sockets[sockets.length - 1];
  const status = () => store.getState().connection.status;
  return { store, client, sockets, snapshots, fetchImpl, last, status };
}

const hello = (mode = "live", head = 0) => ({
  type: "HELLO",
  server_time: new Date(Date.now() + 5_000).toISOString(),
  head_seq: head,
  mode,
});
const event = (i: number) => ({ type: "EVENT", ...fixture.events[i] });
const tick = (ms = 0) => vi.advanceTimersByTimeAsync(ms);

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

describe("connect", () => {
  it("hydrates, connects with since = snapshot last_seq, then applies live events", async () => {
    const { client, store, last, status, fetchImpl } = setup();
    client.start();
    await tick();
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(status()).toBe("connecting");
    const socket = last();
    expect(socket.url).toBe(
      `ws://api.test/ws/companies/${COMPANY}?token=tok%20en&since=${fixture.snapshot_before.last_seq}`,
    );

    socket.push(hello("live"));
    expect(status()).toBe("live");
    expect(store.getState().connection.serverOffsetMs).toBeGreaterThan(4_000);
    socket.push(event(0));
    socket.push(event(1));
    expect(store.getState().company?.lastSeq).toBe(fixture.events[1].seq);
    client.stop();
    expect(status()).toBe("idle");
  });

  it("replays the backlog, ignores the overlap with live events", async () => {
    const { client, store, last, status } = setup();
    client.start();
    await tick();
    last().push(hello("backlog"));
    expect(status()).toBe("connecting");
    last().push({ type: "EVENTS", items: fixture.events.slice(0, 10) });
    last().push({ type: "BACKLOG_DONE", head_seq: fixture.events[9].seq });
    expect(status()).toBe("live");
    for (const i of [8, 9, 10]) last().push(event(i)); // 8 and 9 were in the backlog
    expect(store.getState().company?.lastSeq).toBe(fixture.events[10].seq);
    expect(store.getState().company?.recentEvents.filter((e) => e.seq === fixture.events[9].seq)).toHaveLength(1);
    client.stop();
  });

  it("applies live progress", async () => {
    const { client, store, last } = setup();
    client.start();
    await tick();
    last().push(hello());
    for (let i = 0; i < fixture.events.length; i++) last().push(event(i));
    const agentId = Object.keys(store.getState().company?.agents ?? {})[0];
    last().push({
      type: "EPHEMERAL", event_type: "AGENT_STEP_PROGRESS", agent_id: agentId, run_id: null,
      occurred_at: new Date().toISOString(), payload: { step_seq: 1, tokens_so_far: 42 },
    });
    expect(store.getState().company?.agents[agentId].liveProgress?.tokensSoFar).toBe(42);
    client.stop();
  });
});

describe("reconnect", () => {
  it("backs off 1 s, 2 s, 4 s, resumes from lastSeq without re-hydrating, then resets", async () => {
    const { client, store, sockets, last, status, fetchImpl } = setup();
    client.start();
    await tick();
    last().push(hello());
    last().push(event(0));
    const resumeFrom = store.getState().company?.lastSeq;

    last().drop();
    expect(status()).toBe("reconnecting");
    await tick(999);
    expect(sockets).toHaveLength(1);
    await tick(1);
    expect(sockets).toHaveLength(2);
    expect(last().since).toBe(resumeFrom);

    last().drop();
    await tick(1_999);
    expect(sockets).toHaveLength(2);
    await tick(1);
    expect(sockets).toHaveLength(3);
    last().drop();
    await tick(4_000);
    expect(sockets).toHaveLength(4);

    last().push(hello()); // success resets the backoff
    expect(status()).toBe("live");
    last().drop();
    await tick(1_000);
    expect(sockets).toHaveLength(5);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    client.stop();
  });

  it("jitter stays within +-20 % and the delay is capped at 30 s", () => {
    const low = setup({ random: () => 0 }).client;
    const high = setup({ random: () => 0.9999 }).client;
    expect(low.backoff(1)).toBe(800);
    expect(high.backoff(1)).toBe(1200);
    expect(low.backoff(3)).toBe(3200);
    expect(high.backoff(20)).toBe(35_999); // 30 s cap, +20 % jitter
    expect(low.backoff(20)).toBe(24_000);
  });

  it("reports offline after 5 failures in a row, and keeps trying", async () => {
    const { client, sockets, last, status } = setup();
    client.start();
    await tick();
    for (let failure = 1; failure <= 5; failure++) {
      last().drop();
      expect(status()).toBe(failure < 5 ? "reconnecting" : "offline");
      await tick(30_000);
    }
    expect(sockets).toHaveLength(6);
    last().push(hello());
    expect(status()).toBe("live");
    client.stop();
  });

  it("SNAPSHOT_REQUIRED re-hydrates and reconnects at once from the new snapshot", async () => {
    const { client, store, snapshots, last, fetchImpl, sockets } = setup();
    snapshots.push(fixture.snapshot_after);
    client.start();
    await tick();
    last().push(hello("snapshot_required"));
    last().push({ type: "SNAPSHOT_REQUIRED", reason: "queue_overflow" });
    last().drop(4000);
    await tick();
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(sockets).toHaveLength(2);
    expect(last().since).toBe(fixture.snapshot_after.last_seq);
    expect(store.getState().company?.lastSeq).toBe(fixture.snapshot_after.last_seq);
    client.stop();
  });

  it("closes a silent socket after the heartbeat timeout and reconnects", async () => {
    const { client, sockets, last } = setup({ heartbeatTimeoutMs: 30_000 });
    client.start();
    await tick();
    last().push(hello());
    await tick(20_000);
    last().push({ type: "HEARTBEAT", server_time: new Date().toISOString(), head_seq: 0 });
    await tick(29_999);
    expect(last().closedWith).toBeNull();
    await tick(1);
    expect(sockets[0].closedWith).toBe(4002);
    await tick(1_000);
    expect(sockets).toHaveLength(2);
    client.stop();
  });

  it("re-hydrates when the tab returns after a long silence, not after a short one", async () => {
    const { client, last, sockets, fetchImpl } = setup({ heartbeatTimeoutMs: 10 * 60_000 });
    client.start();
    await tick();
    last().push(hello());
    await tick(30_000);
    client.setVisible(true);
    await tick();
    expect(sockets).toHaveLength(1);

    await tick(61_000);
    client.setVisible(true);
    await tick();
    expect(sockets[0].closedWith).toBe(4003);
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(sockets).toHaveLength(2);
    client.stop();
  });
});

describe("sockets that never report a close (Node's WebSocket when the server is down)", () => {
  it("an error before the socket opens counts as a failed connection", async () => {
    const { client, sockets, last, status } = setup();
    client.start();
    await tick();
    last().onerror?.({}); // no close event follows
    expect(status()).toBe("reconnecting");
    await tick(1_000);
    expect(sockets).toHaveLength(2);
    last().push(hello());
    last().onerror?.({}); // after opening, an error is followed by a real close: wait for it
    expect(status()).toBe("live");
    client.stop();
  });

  it("the heartbeat timeout frees a socket stuck connecting even if close() does nothing", async () => {
    const { client, sockets } = setup({ heartbeatTimeoutMs: 10_000 });
    client.start();
    await tick();
    sockets[0].close = () => {}; // stuck: never fires onclose
    await tick(10_000);
    await tick(1_000);
    expect(sockets).toHaveLength(2);
    client.stop();
  });
});

describe("stopping for good", () => {
  it("stops on ERROR unauthorized: no reconnect storm", async () => {
    const { client, sockets, last, status } = setup();
    client.start();
    await tick();
    last().push({ type: "ERROR", code: "unauthorized", message: "bad token" });
    last().drop(4401);
    await tick(60_000);
    expect(status()).toBe("unauthorized");
    expect(sockets).toHaveLength(1);
  });

  it("stops when the snapshot says 401 or 404", async () => {
    for (const [code, expected] of [[401, "unauthorized"], [404, "not_found"]] as const) {
      const { client, sockets, status } = setup({
        fetchImpl: (async () => new Response("{}", { status: code })) as unknown as typeof fetch,
      });
      client.start();
      await tick(60_000);
      expect(status()).toBe(expected);
      expect(sockets).toHaveLength(0);
    }
  });

  it("retries a failing snapshot with backoff", async () => {
    let calls = 0;
    const { client, sockets } = setup({
      fetchImpl: (async () => {
        calls += 1;
        if (calls < 3) return new Response("down", { status: 503 });
        return new Response(JSON.stringify(fixture.snapshot_before), { status: 200 });
      }) as unknown as typeof fetch,
    });
    client.start();
    await tick();
    expect(sockets).toHaveLength(0);
    await tick(1_000);
    await tick(2_000);
    expect(calls).toBe(3);
    expect(sockets).toHaveLength(1);
    client.stop();
  });
});

describe("ACK", () => {
  it("acknowledges every 50 events and on the interval", async () => {
    const { client, last, store } = setup({ ackEveryEvents: 50, ackIntervalMs: 5_000 });
    client.start();
    await tick();
    last().push(hello());
    for (let i = 0; i < 50; i++) last().push(event(i));
    expect(last().sent).toEqual([{ type: "ACK", last_seq: store.getState().company?.lastSeq }]);
    last().push(event(50));
    await tick(5_000);
    expect(last().sent).toHaveLength(2);
    await tick(5_000);
    expect(last().sent).toHaveLength(2); // nothing new: no ACK
    client.stop();
  });
});
