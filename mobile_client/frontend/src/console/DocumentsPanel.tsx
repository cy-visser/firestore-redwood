import React, { useEffect, useRef, useState } from "react";
import { Braces } from "lucide-react";
import { CustomerSession, LoyaltyOffer, SnapshotEvent } from "../types/retail";

/**
 * How long a changed key stays highlighted. Long enough to catch the eye from
 * the back of a room, short enough that a burst of updates does not leave the
 * whole document lit.
 */
const FLASH_MS = 1400;

/** Keys worth pointing at during the demo: the model output and the AI path. */
const ACCENT_KEYS = new Set([
  "churnProbability",
  "churnRiskTier",
  // The tier the agent acted on, and why it differs from the model's. On an
  // escalated offer escalationReason is the judge's own sentence, which is the
  // line worth reading out.
  "eligibilityTier",
  "escalated",
  "escalationReason",
  // The skip path's own narration. On a declined escalation skipDetail is the
  // judge's sentence and the only key in the document that says why nothing
  // was issued; escalationGate says which branch produced it.
  "skipReason",
  "skipDetail",
  "escalationGate",
  "agentProcessingStatus",
  "status",
  "discountTotal",
  "grandTotal",
  "offerApplied",
]);

interface DocumentsPanelProps {
  session: SnapshotEvent<CustomerSession> | null;
  offer: SnapshotEvent<LoyaltyOffer> | null;
  order: SnapshotEvent<Record<string, unknown>> | null;
}

interface JsonDocumentProps {
  title: string;
  collection: string;
  snapshot: SnapshotEvent<Record<string, unknown>> | null;
  emptyHint: string;
}

function renderValue(value: unknown): string {
  if (value === null) return "null";
  if (value === undefined) return "undefined";
  if (typeof value === "string") return JSON.stringify(value);
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value) ?? "undefined";
}

const JsonDocument: React.FC<JsonDocumentProps> = ({
  title,
  collection,
  snapshot,
  emptyHint,
}) => {
  const [flashed, setFlashed] = useState<Record<string, boolean>>({});

  // Rendered form of every key as of the previous snapshot. Comparing the
  // rendered strings rather than the values means a nested object only counts
  // as changed when its contents actually differ, not merely because the SSE
  // frame produced a fresh object identity.
  const previousRef = useRef<Record<string, string> | null>(null);

  const data = snapshot?.data ?? null;

  useEffect(() => {
    if (!data) {
      previousRef.current = null;
      return;
    }

    const next: Record<string, string> = {};
    const changed: Record<string, boolean> = {};
    const previous = previousRef.current;

    Object.entries(data).forEach(([key, value]) => {
      const rendered = renderValue(value);
      next[key] = rendered;
      // The first snapshot of a document is not a change. Without this guard
      // every field flashes at once the moment a login lands, which hides the
      // one field that actually moved on the next update.
      if (previous !== null && previous[key] !== rendered) changed[key] = true;
    });

    previousRef.current = next;
    if (Object.keys(changed).length === 0) return;

    setFlashed(changed);
    const timer = window.setTimeout(() => setFlashed({}), FLASH_MS);
    return () => window.clearTimeout(timer);
  }, [data]);

  const entries = data ? Object.entries(data) : [];

  return (
    <div className="flex-1 min-w-0 flex flex-col rounded-xl border border-slate-800 bg-slateDark-950 overflow-hidden">
      <div className="px-3 py-2 bg-slateDark-850 border-b border-slate-800">
        <div className="flex items-baseline justify-between gap-2">
          <p className="text-[11px] font-bold text-slate-100">{title}</p>
          <p className="text-[9px] font-mono text-slate-500 truncate">
            {collection}
          </p>
        </div>
        <p className="text-[9px] font-mono text-slate-500 truncate">
          {snapshot
            ? `${snapshot.id} · created ${snapshot.createTime ?? "—"} · updated ${snapshot.updateTime ?? "—"}`
            : emptyHint}
        </p>
      </div>

      <div className="flex-1 overflow-auto p-3 font-mono text-[11px] leading-relaxed">
        {entries.length === 0 ? (
          <p className="text-slate-600">{"{}"}</p>
        ) : (
          <>
            <p className="text-slate-500">{"{"}</p>
            {entries.map(([key, value]) => (
              <div
                key={key}
                className={`rounded px-1.5 -mx-1.5 transition-colors duration-700 ${
                  flashed[key] ? "bg-brand-500/25" : "bg-transparent"
                }`}
              >
                <span
                  className={
                    ACCENT_KEYS.has(key) ? "text-brand-300" : "text-sky-300"
                  }
                >
                  &nbsp;&nbsp;&quot;{key}&quot;
                </span>
                <span className="text-slate-500">: </span>
                <span
                  className={
                    ACCENT_KEYS.has(key)
                      ? "text-amber-200 font-semibold"
                      : "text-slate-200"
                  }
                >
                  {renderValue(value)}
                </span>
                <span className="text-slate-600">,</span>
              </div>
            ))}
            <p className="text-slate-500">{"}"}</p>
          </>
        )}
      </div>
    </div>
  );
};

export const DocumentsPanel: React.FC<DocumentsPanelProps> = ({
  session,
  offer,
  order,
}) => {
  // The panel renders whatever keys the document carries, so it is deliberately
  // untyped at this point: the demo's whole argument is that these are the real
  // Firestore documents and not a curated view of them.
  const sessionDoc = session
    ? { ...session, data: session.data as unknown as Record<string, unknown> }
    : null;
  const offerDoc = offer
    ? { ...offer, data: offer.data as unknown as Record<string, unknown> }
    : null;
  const orderDoc = order
    ? { ...order, data: order.data as unknown as Record<string, unknown> }
    : null;

  return (
    <section className="bg-slateDark-900 border border-slate-800 rounded-2xl p-4 flex flex-col gap-3 min-h-0">
      <header className="flex items-center justify-between">
        <h2 className="text-sm font-bold text-white flex items-center gap-2">
          <Braces className="w-4 h-4 text-brand-400" />
          Documents
        </h2>
        <span className="text-[10px] font-mono uppercase tracking-wider text-slate-500">
          live · changed keys flash
        </span>
      </header>

      <div className="flex gap-3 min-h-0 h-72">
        <JsonDocument
          title="Customer Session"
          collection="customer_sessions"
          snapshot={sessionDoc}
          emptyHint="no session yet — sign in on the phone"
        />
        <JsonDocument
          title="Order"
          collection="retail"
          snapshot={orderDoc}
          emptyHint="no order yet — place one on the phone"
        />
        <JsonDocument
          title="Loyalty Offer"
          collection="loyalty_offers"
          snapshot={offerDoc}
          emptyHint="no offer for this session"
        />
      </div>
    </section>
  );
};
