"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { getToken } from "@/api/auth";
import { connectQueryInvalidation } from "@/api/invalidation";
import { API_URL, toWebSocketUrl } from "@/config";
import { attachToDocument, RealtimeClient } from "@/realtime/client";
import { realtimeStore } from "@/stores/realtime";

/**
 * Keep the realtime store in sync with one company while the calling page is mounted, and let
 * its events refresh the server-state queries. Switching company resets the store first.
 */
export function useCompanyStream(companyId: string | null): void {
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!companyId) return;
    if (realtimeStore.getState().company?.companyId !== companyId) realtimeStore.getState().reset();
    const client = new RealtimeClient({
      apiUrl: API_URL,
      wsUrl: toWebSocketUrl(API_URL),
      companyId,
      getToken,
    });
    const stopInvalidation = connectQueryInvalidation(queryClient, realtimeStore);
    const detach = attachToDocument(client);
    client.start();
    return () => {
      detach();
      stopInvalidation();
      client.stop();
    };
  }, [companyId, queryClient]);
}
