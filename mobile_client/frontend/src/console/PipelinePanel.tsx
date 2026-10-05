import React from "react";
import {
  BrainCircuit,
  ChevronRight,
  Database,
  Gauge,
  Gift,
  Radio,
  Server,
  Smartphone,
  Table2,
} from "lucide-react";
import {
  ClientTelemetry,
  CustomerSession,
  LoyaltyOffer,
  OrderTrace,
  PipelineTrace,
  SnapshotEvent,
  TERMINAL_STATUSES,
  WriteLatency,
} from "../types/retail";

type NodeState = "idle" | "active" | "done" | "skipped" | "error";

/**
 * Which process's clock produced a node's number.
 *
 * Every measurement in this panel is an elapsed time taken on one clock by the
 * component that owns the step. Nothing here is the difference between two
 * timestamps from different machines, with the single exception of the two
 * marked `approx`, where a CloudEvent's `ce-time` is compared against the
 * receiving service's arrival time. Those are rendered with a leading `≈` so
 * the distinction survives being read from the back of a room.
 */
type Clock = "app" | "eventarc" | "run" | "agent" | "cdc";

const CLOCKS: Record<Clock, { label: string; color: string }> = {
  app: { label: "App backend", color: "text-sky-400" },
  eventarc: { label: "Eventarc ≈ Cloud Run", color: "text-amber-400" },
  run: { label: "Cloud Run", color: "text-indigo-400" },
  agent: { label: "Agent Engine", color: "text-emerald-400" },
  cdc: { label: "CDC service", color: "text-fuchsia-400" },
};

interface PipelineNode {
  key: string;
  label: string;
  detail: string;
  latency: number | null | undefined;
  clock: Clock;
  approx?: boolean;
  Icon: React.ComponentType<{ className?: string }>;
  state: NodeState;
}

interface PipelinePanelProps {
  trace: PipelineTrace | null;
  orderTrace: OrderTrace | null;
  loginWrite: WriteLatency | null;
  orderWrite: WriteLatency | null;
  telemetry: ClientTelemetry | null;
  session: SnapshotEvent<CustomerSession> | null;
  offer: SnapshotEvent<LoyaltyOffer> | null;
}

const NODE_STYLES: Record<NodeState, string> = {
  idle: "border-slate-800 bg-slateDark-900 text-slate-600",
  active:
    "border-brand-500/70 bg-brand-500/10 text-brand-300 shadow-lg shadow-brand-600/20 animate-pulse",
  done: "border-emerald-500/50 bg-emerald-500/10 text-emerald-300",
  skipped: "border-slate-700 bg-slateDark-850 text-slate-400",
  error: "border-rose-500/60 bg-rose-500/10 text-rose-300",
};

function formatMs(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (value < 1000) return `${Math.round(value)} ms`;
  return `${(value / 1000).toFixed(2)} s`;
}

/** Wall clock only, to the second. The date is never the interesting part. */
function shortTime(value: string | null | undefined): string | null {
  if (!value) return null;
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) return null;
  return new Date(parsed).toISOString().slice(11, 23);
}

/** The escalation branch, as a short lower-case label for the agent node. */
function gateLabel(gate: string | null | undefined): string | null {
  if (!gate) return null;
  return `gate ${gate.toLowerCase().replace(/_/g, "-")}`;
}

export const PipelinePanel: React.FC<PipelinePanelProps> = ({
  trace,
  orderTrace,
  loginWrite,
  orderWrite,
  telemetry,
  session,
  offer,
}) => {
  const status = session?.data.agentProcessingStatus ?? null;
  const isTerminal = status !== null && TERMINAL_STATUSES.includes(status);
  // The bridge writes DISPATCHED before it calls the agent, so the delivery
  // hop is provably done well before the agent reaches a terminal state.
  const delivered = trace?.bridgeStatus != null || trace?.agentClaimMs != null || isTerminal;
  const claimed = trace?.agentClaimMs != null || isTerminal;
  const skipped = status === "SKIPPED";
  const errored = status === "ERROR" || trace?.bridgeStatus === "AGENT_ERROR";

  // Built as a list because the agent's inner spans are conditional: the churn
  // lookup is always there once it has run, the judgement almost never is.
  // Where the judge did not run, the branch that stopped it takes its place,
  // so the node never goes quiet about a decision it made. Falling all the way
  // back to the worker id keeps the line from going blank while the agent is
  // still working.
  const agentDetail =
    [
      trace?.churnLookupMs != null ? `churn ${formatMs(trace.churnLookupMs)}` : null,
      trace?.llmEscalationMs != null
        ? `judge ${(trace.escalationVerdict ?? "").toLowerCase()} ${formatMs(trace.llmEscalationMs)}`
        : gateLabel(trace?.escalationGate),
    ]
      .filter(Boolean)
      .join(" · ") ||
    session?.data.agentWorkerId ||
    (status ?? "idle").toLowerCase();

  const nodes: PipelineNode[] = [
    {
      key: "client",
      label: "Mobile Client",
      detail: telemetry?.sessionId ? "login · telemetry" : "login write",
      latency: loginWrite?.durationMs,
      clock: "app",
      Icon: Smartphone,
      state: session ? "done" : telemetry ? "active" : "idle",
    },
    {
      key: "firestore",
      label: "Firestore",
      detail: orderWrite ? "order write" : session ? "customer_sessions" : "awaiting login",
      latency: orderWrite?.durationMs,
      clock: "app",
      Icon: Database,
      state: session ? "done" : "idle",
    },
    {
      key: "eventarc",
      label: "Eventarc",
      detail: shortTime(trace?.eventTime) ?? (session ? "in flight" : "idle"),
      latency: trace?.eventarcDeliveryMs,
      clock: "eventarc",
      approx: true,
      Icon: Radio,
      state: delivered ? "done" : session ? "active" : "idle",
    },
    {
      key: "bridge",
      label: "Cloud Run",
      detail: shortTime(trace?.bridgeReceivedAt) ?? (session ? "in flight" : "idle"),
      latency: trace?.bridgeOverheadMs,
      clock: "run",
      Icon: Server,
      state: delivered ? "done" : session ? "active" : "idle",
    },
    {
      key: "agent",
      label: "Agent Engine",
      // The round trip is the bridge's measurement; the spans inside it are
      // the agent's, which is why only one of them is the headline number.
      // The judge span is absent on almost every run -- it is only measured
      // when the model's tier is a candidate and there is a complaint the
      // model cannot have seen -- so it is appended rather than substituted.
      detail: agentDetail,
      latency: trace?.agentCallMs,
      clock: "run",
      Icon: BrainCircuit,
      state: errored ? "error" : isTerminal ? "done" : claimed ? "active" : "idle",
    },
    {
      key: "offer",
      label: "Offer",
      detail: offer
        ? [
            `${offer.data.discountPercent}%`,
            trace?.llmOutcome === "FAILED" ? "rules" : "Gemini",
            // Worth calling out: without the judgement there would be no
            // offer here at all, because the model's own tier did not qualify.
            offer.data.escalated ? "escalated" : null,
          ]
            .filter(Boolean)
            .join(" · ")
        : skipped
          ? `skipped: ${session?.data.skipReason ?? "no offer"}`
          : "pending",
      latency: trace?.llmReasoningMs,
      clock: "agent",
      Icon: Gift,
      state: offer ? "done" : skipped ? "skipped" : "idle",
    },

    {
      key: "bigquery",
      label: "BigQuery",
      detail: orderTrace?.bqTables?.length
        ? orderTrace.bqTables.join(" · ")
        : orderWrite
          ? "replicating"
          : "no order yet",
      latency: orderTrace?.bqTotalMs,
      clock: "cdc",
      approx: true,
      Icon: Table2,
      state: orderTrace ? "done" : orderWrite ? "active" : "idle",
    },
  ];

  // Only the clocks actually represented, so the legend shrinks with the row
  // rather than always claiming five clocks are in play.
  const legend = Array.from(new Set(nodes.map((node) => node.clock)));

  return (
    <section className="bg-slateDark-900 border border-slate-800 rounded-2xl p-4 flex flex-col gap-3">
      <header className="flex items-center justify-between">
        <h2 className="text-sm font-bold text-white flex items-center gap-2">
          <Gauge className="w-4 h-4 text-brand-400" />
          Retention Pipeline
        </h2>
        <span className="text-[10px] font-mono uppercase tracking-wider text-slate-500">
          {session?.id ?? "no session"}
        </span>
      </header>

      {/* Seven nodes, each lit by the state its documents are actually in and
          carrying the latency of the step it owns. */}
      <div className="flex items-stretch gap-1">
        {nodes.map((node, index) => (
          <React.Fragment key={node.key}>
            <div
              className={`flex-1 min-w-0 rounded-xl border px-2 py-2.5 transition-all duration-500 ${NODE_STYLES[node.state]}`}
            >
              <node.Icon className="w-4 h-4 mb-1" />
              <p className="text-[10px] font-semibold leading-tight text-slate-100 truncate">
                {node.label}
              </p>
              <p
                className={`text-[13px] font-mono font-bold leading-tight truncate ${
                  node.latency == null ? "text-slate-600" : "text-white"
                }`}
              >
                {node.approx && node.latency != null ? "≈ " : ""}
                {formatMs(node.latency)}
              </p>
              <p
                className={`text-[9px] font-mono truncate ${CLOCKS[node.clock].color}`}
                title={node.detail}
              >
                {node.detail}
              </p>
            </div>
            {index < nodes.length - 1 && (
              <ChevronRight className="w-3 h-3 self-center shrink-0 text-slate-700" />
            )}
          </React.Fragment>
        ))}
      </div>

      {/* Every number above names its clock here. Two measurements from
          different clocks are never subtracted from one another, and the two
          marked ≈ are the only ones that cross a clock boundary at all. */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-1 text-[9px] font-mono uppercase tracking-wider">
        <span className="text-slate-600">Clocks</span>
        {legend.map((clock) => (
          <span key={clock} className={CLOCKS[clock].color}>
            {CLOCKS[clock].label}
          </span>
        ))}
        {trace?.bridgeError && (
          <span className="text-rose-400 normal-case tracking-normal truncate">
            {trace.bridgeError}
          </span>
        )}
      </div>
    </section>
  );
};
