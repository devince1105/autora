// Hand-written entry point. Everything else in src/ is generated.
import { EventEnvelope } from "./generated";

export * from "./generated";

export type ParsedEvent =
  | { ok: true; event: EventEnvelope }
  | { ok: false; eventType: string | undefined; error: string };

/**
 * Validate one event from the wire. Never throws: the realtime reducer logs and drops
 * events it cannot parse (unknown type, newer schema version) instead of crashing.
 */
export function parseEvent(input: unknown): ParsedEvent {
  const result = EventEnvelope.safeParse(input);
  if (result.success) {
    return { ok: true, event: result.data };
  }
  const eventType =
    typeof input === "object" && input !== null && "event_type" in input
      ? String((input as { event_type: unknown }).event_type)
      : undefined;
  return { ok: false, eventType, error: result.error.message };
}
