import { useEffect, useRef, useState } from "react";

/**
 * Subscribe to one of the backend's SSE streams.
 *
 * Both apps use this: the mobile client watches a single session, the console
 * watches both collections. The reconnect is the reason it is shared. An SSE
 * connection is dropped routinely -- a laptop sleeping, a proxy timing out, a
 * backend restart between demo runs -- and the browser's own retry gives up
 * silently after a few attempts, which looks exactly like a pipeline that has
 * stopped emitting.
 */

type Handler = (payload: any) => void;

export type StreamStatus = "connecting" | "open" | "reconnecting" | "closed";

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 15000;

export function useEventStream(
  url: string | null,
  handlers: Record<string, Handler>,
): StreamStatus {
  const [status, setStatus] = useState<StreamStatus>(url ? "connecting" : "closed");

  // Held in a ref so a re-render caused by an event does not tear down and
  // rebuild the connection, which would drop every change committed in
  // between and loop for as long as events keep arriving.
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  useEffect(() => {
    if (!url) {
      setStatus("closed");
      return;
    }

    let source: EventSource | null = null;
    let retryTimer: number | undefined;
    let attempt = 0;
    let cancelled = false;

    const connect = () => {
      if (cancelled) return;
      source = new EventSource(url);

      source.onopen = () => {
        attempt = 0;
        setStatus("open");
      };

      Object.keys(handlersRef.current).forEach((event) => {
        source!.addEventListener(event, (raw) => {
          const message = raw as MessageEvent;
          let payload: any = message.data;
          try {
            payload = JSON.parse(message.data);
          } catch {
            // A frame that is not JSON is still worth delivering as text.
          }
          handlersRef.current[event]?.(payload);
        });
      });

      source.onerror = () => {
        // EventSource retries on its own but stops after a handful of
        // failures and never tells anyone. Take the reconnect over.
        source?.close();
        if (cancelled) return;
        setStatus("reconnecting");
        attempt += 1;
        const delay = Math.min(
          RECONNECT_BASE_MS * 2 ** (attempt - 1),
          RECONNECT_MAX_MS,
        );
        retryTimer = window.setTimeout(connect, delay);
      };
    };

    setStatus("connecting");
    connect();

    return () => {
      cancelled = true;
      window.clearTimeout(retryTimer);
      source?.close();
      setStatus("closed");
    };
  }, [url]);

  return status;
}

/**
 * Read a streaming SSE response produced by a POST.
 *
 * EventSource cannot issue a POST, and the churn recalculation has to be a
 * POST because it runs something. Parsing the frames by hand is the price of
 * that, and it is only a few lines because the backend emits one event per
 * frame with no multi-line payloads.
 */
export async function readEventStream(
  response: Response,
  onEvent: (event: string, data: any) => void,
): Promise<void> {
  const reader = response.body?.getReader();
  if (!reader) return;

  const decoder = new TextDecoder();
  let buffer = "";

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let split = buffer.indexOf("\n\n");
    while (split !== -1) {
      const frame = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      split = buffer.indexOf("\n\n");

      let event = "message";
      let data = "";
      frame.split("\n").forEach((line) => {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
        // A line starting with ":" is a keepalive comment; ignore it.
      });
      if (!data) continue;

      try {
        onEvent(event, JSON.parse(data));
      } catch {
        onEvent(event, data);
      }
    }
  }
}
