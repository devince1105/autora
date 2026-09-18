"use client";

import { useEffect, useState } from "react";

import { realtimeStore, serverNow } from "@/stores/realtime";

/** Server-corrected current time, refreshed every `intervalMs` (for clock-based rules such as
 * COMPLETED turning IDLE after display_until, and "data may be stale for N s"). */
export function useNow(intervalMs = 1000): Date {
  const [now, setNow] = useState(() => serverNow(realtimeStore.getState()));
  useEffect(() => {
    const timer = setInterval(() => setNow(serverNow(realtimeStore.getState())), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return now;
}
