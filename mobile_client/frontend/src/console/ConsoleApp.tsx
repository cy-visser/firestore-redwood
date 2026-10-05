import React, { useCallback, useMemo, useRef, useState } from "react";
import { Activity, Boxes, Sparkles } from "lucide-react";
import {
  ClientTelemetry,
  ConsoleLogEntry,
  CustomerSession,
  LoyaltyOffer,
  OrderTrace,
  PipelineTrace,
  SnapshotEvent,
  WriteLatency,
} from "../types/retail";
import { StreamStatus, useEventStream } from "../hooks/useEventStream";
import { PipelinePanel } from "./PipelinePanel";
import { DocumentsPanel } from "./DocumentsPanel";
import { ChurnPanel } from "./ChurnPanel";
import { EventLogPanel } from "./EventLogPanel";

const STREAM_URL = "/api/stream/console";

/**
 * The log is a live tail, not an audit trail. A recalculation alone emits
 * several hundred lines, and an unbounded array behind a React list is a slow
 * leak that only shows up halfway through a demo.
 */
const MAX_LOG_ENTRIES = 300;

/** Payload shape of the `control` event published by console_service. */
interface ControlEvent {
  action: string;
  counts?: Record<string, number>;
  /** Present on `reset`. Says whether the agent was warmed before the demo. */
  warmUp?: {
    attempted: boolean;
    succeeded: boolean;
    status?: string | null;
    elapsedSeconds?: number | null;
  };
  at?: string;
}

const STATUS_STYLES: Record<StreamStatus, string> = {
  open: "bg-emerald-400 shadow-sm shadow-emerald-400/50 animate-pulse",
  connecting: "bg-amber-400 animate-pulse",
  reconnecting: "bg-amber-400 animate-pulse",
  closed: "bg-rose-500",
};

const STATUS_LABELS: Record<StreamStatus, string> = {
  open: "SSE live",
  connecting: "Connecting…",
  reconnecting: "Reconnecting…",
  closed: "Disconnected",
};

function latestByCreateTime<T>(
  records: Record<string, SnapshotEvent<T>>,
): SnapshotEvent<T> | null {
  let best: SnapshotEvent<T> | null = null;
  let bestAt = -Infinity;
  Object.values(records).forEach((record) => {
    const at = record.createTime ? Date.parse(record.createTime) : NaN;
    const score = Number.isNaN(at) ? -Infinity : at;
    if (best === null || score >= bestAt) {
      best = record;
      bestAt = score;
    }
  });
  return best;
}

export const ConsoleApp: React.FC = () => {
  // Collections watched as queries redeliver the whole result set.
  // Keeping them by document id lets redelivery overwrite in place.
  const [sessions, setSessions] = useState<
    Record<string, SnapshotEvent<CustomerSession>>
  >({});
  const [offers, setOffers] = useState<
    Record<string, SnapshotEvent<LoyaltyOffer>>
  >({});
  const [orders, setOrders] = useState<
    Record<string, SnapshotEvent<Record<string, unknown>>>
  >({});
  // Traces are keyed by document id for the same reason sessions and offers
  // are: the watch is a query, and a query redelivers its whole result set.
  const [traces, setTraces] = useState<
    Record<string, PipelineTrace | OrderTrace>
  >({});
  const [loginWrite, setLoginWrite] = useState<WriteLatency | null>(null);
  const [orderWrite, setOrderWrite] = useState<WriteLatency | null>(null);
  const [telemetry, setTelemetry] = useState<ClientTelemetry | null>(null);
  const [logEntries, setLogEntries] = useState<ConsoleLogEntry[]>([]);

  const nextLogId = useRef(0);

  // Snapshot fingerprints already logged, so a query redelivering unchanged
  // documents does not produce duplicate log lines.
  const seenRef = useRef<Record<string, string>>({});

  const appendLog = useCallback(
    (kind: ConsoleLogEntry["kind"], message: string) => {
      nextLogId.current += 1;
      const entry: ConsoleLogEntry = {
        id: nextLogId.current,
        at: new Date().toISOString(),
        kind,
        message,
      };
      setLogEntries((previous) => {
        const next = [...previous, entry];
        return next.length > MAX_LOG_ENTRIES
          ? next.slice(next.length - MAX_LOG_ENTRIES)
          : next;
      });
    },
    [],
  );

  const isNewRevision = (key: string, stamp: string | null): boolean => {
    const fingerprint = stamp ?? "";
    if (seenRef.current[key] === fingerprint) return false;
    seenRef.current[key] = fingerprint;
    return true;
  };

  const status = useEventStream(STREAM_URL, {
    session: (payload: SnapshotEvent<CustomerSession>) => {
      setSessions((previous) => ({ ...previous, [payload.id]: payload }));

      if (isNewRevision(`session:${payload.id}`, payload.updateTime)) {
        appendLog(
          "session",
          `${payload.id} → ${payload.data.agentProcessingStatus}` +
            (payload.data.skipReason ? ` (${payload.data.skipReason})` : "") +
            // The judge's own sentence, where there is one. Worth the width:
            // it is the difference between a log that records that nothing
            // happened and one that records why.
            (payload.data.skipDetail ? ` — ${payload.data.skipDetail}` : ""),
        );
      }
    },

    offer: (payload: SnapshotEvent<LoyaltyOffer>) => {
      setOffers((previous) => ({ ...previous, [payload.id]: payload }));
      if (isNewRevision(`offer:${payload.id}`, payload.updateTime)) {
        appendLog(
          "offer",
          `${payload.id} → ${payload.data.status} · ${payload.data.discountPercent}% · ${payload.data.churnRiskTier}`,
        );
      }
    },

    order: (raw: any) => {
      const orderId = raw.id || raw.orderId || "unknown";
      const doc: SnapshotEvent<Record<string, unknown>> = {
        id: orderId,
        data: raw.data || raw,
        createTime: raw.createTime || raw.createdAt || null,
        updateTime: raw.updateTime || raw.updatedAt || null,
      };
      setOrders((previous) => ({ ...previous, [orderId]: doc }));
      const total =
        typeof doc.data.grandTotal === "number"
          ? `€${(doc.data.grandTotal as number).toFixed(2)}`
          : "—";
      const offerObj = doc.data.loyaltyOffer as
        | { discountPercent?: number; promoCode?: string }
        | undefined;
      const offerInfo = offerObj?.discountPercent
        ? `${offerObj.discountPercent}% agent offer (${offerObj.promoCode ?? ""})`
        : "list price";
      appendLog("order", `${orderId} → ${total} · ${offerInfo}`);
    },

    write: (payload: WriteLatency) => {
      if (payload.kind === "login") {
        setLoginWrite(payload);
        appendLog("write", `login document written in ${payload.durationMs} ms`);
      } else if (payload.kind === "order") {
        setOrderWrite(payload);
        appendLog("write", `order document written in ${payload.durationMs} ms`);
      }
    },

    trace: (payload: any) => {
      // A query watch redelivers its whole result set, newest first, so the
      // last frame of a snapshot carries the *oldest* document. Setting a
      // single slot per frame meant the panel showed whichever trace happened
      // to be emitted last rather than the one for the session on screen.
      const data = payload.data || payload;
      const id = payload.id || data.sessionId || data.orderId;
      if (!id) return;
      setTraces((previous) => ({ ...previous, [id]: data }));
    },

    telemetry: (payload: ClientTelemetry) => {
      setTelemetry(payload);
      appendLog(
        "telemetry",
        `${payload.sessionId} browser clock: ack=${payload.clientAckMs ?? "—"}ms visible=${payload.offerVisibleMs ?? "—"}ms${
          payload.outcome ? ` outcome=${payload.outcome}` : ""
        }`,
      );
    },

    control: (payload: ControlEvent) => {
      // The reset deletes the documents these panels are showing, and a
      // Firestore query watch has no way to say so: a removed document simply
      // drops out of the result set, and sse.py deliberately does not forward
      // the non-existent snapshot a document watch would produce. Without
      // this the last run's session, offer and order stay on screen for the
      // 35 seconds the reset takes, and any panel whose document is not
      // recreated keeps them indefinitely.
      if (payload.action === "reset-started" || payload.action === "reset") {
        setSessions({});
        setOffers({});
        setOrders({});
        setTraces({});
        setLoginWrite(null);
        setOrderWrite(null);
        setTelemetry(null);
        seenRef.current = {};
      }

      const counts = payload.counts
        ? ` ${JSON.stringify(payload.counts)}`
        : "";
      const warmUp = payload.warmUp?.attempted
        ? ` warmUp=${payload.warmUp.succeeded ? "ok" : "cold"}`
        : "";
      appendLog("control", `${payload.action}${counts}${warmUp}`);
    },
  });

  const session = useMemo(() => latestByCreateTime(sessions), [sessions]);

  // Prefer the offer belonging to the session on screen; fall back to the most
  // recent offer so the panel is not blank when the console starts mid-demo.
  const offer = useMemo(() => {
    const sessionId = session?.data.sessionId ?? session?.id ?? null;
    if (sessionId) {
      const matching = Object.values(offers).filter(
        (candidate) => candidate.data.sessionId === sessionId,
      );
      if (matching.length > 0) {
        return latestByCreateTime(
          Object.fromEntries(matching.map((item) => [item.id, item])),
        );
      }
    }
    return latestByCreateTime(offers);
  }, [offers, session]);

  const order = useMemo(() => latestByCreateTime(orders), [orders]);

  // Correlated rather than "most recent". Both traces are keyed by the id of
  // the thing they describe, so the panel always shows the spans belonging to
  // the session and the order it is already displaying.
  const sessionTrace = useMemo(() => {
    const id = session?.data.sessionId ?? session?.id ?? null;
    return id ? ((traces[id] as PipelineTrace) ?? null) : null;
  }, [traces, session]);

  const orderTrace = useMemo(() => {
    const id = order?.id ?? null;
    if (!id) return null;
    // cdc_service keys its traces "order_{orderId}" so they cannot collide
    // with a session id in the same collection.
    return (traces[`order_${id}`] as OrderTrace) ?? null;
  }, [traces, order]);

  return (
    <div className="h-screen flex flex-col bg-slateDark-950 text-slate-100 overflow-hidden">
      <header className="shrink-0 px-6 py-3 bg-slateDark-900/90 backdrop-blur-md border-b border-slate-800/80 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-tr from-brand-600 to-amber-500 flex items-center justify-center shadow-md shadow-brand-600/30">
            <Sparkles className="w-4 h-4 text-white" />
          </div>
          <div>
            <h1 className="text-base font-bold tracking-tight text-white flex items-center gap-2">
              Redwood Console
              <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-brand-500/20 text-brand-400 border border-brand-500/30">
                operator
              </span>
            </h1>
            <p className="text-[10px] text-slate-400 font-mono flex items-center gap-1.5">
              <Boxes className="w-2.5 h-2.5" />
              customer_sessions · loyalty_offers · customer_churn_risk
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-slate-800/60 border border-slate-700/60 text-[10px] font-mono">
          <Activity className="w-3 h-3 text-slate-400" />
          <span className={`w-2 h-2 rounded-full ${STATUS_STYLES[status]}`} />
          <span className="text-slate-300">{STATUS_LABELS[status]}</span>
          <span className="text-slate-600">{STREAM_URL}</span>
        </div>
      </header>

      <main className="flex-1 min-h-0 grid grid-cols-3 gap-4 p-4 overflow-hidden">
        <div className="col-span-2 flex flex-col gap-4 min-h-0 overflow-y-auto pr-1">
          <PipelinePanel
            trace={sessionTrace}
            orderTrace={orderTrace}
            loginWrite={loginWrite}
            orderWrite={orderWrite}
            telemetry={telemetry}
            session={session}
            offer={offer}
          />
          <DocumentsPanel session={session} offer={offer} order={order} />
        </div>

        <div className="col-span-1 flex flex-col gap-4 min-h-0">
          <ChurnPanel onLog={appendLog} />
          <EventLogPanel entries={logEntries} capacity={MAX_LOG_ENTRIES} />
        </div>
      </main>
    </div>
  );
};
