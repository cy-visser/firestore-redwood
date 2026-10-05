import React, { useCallback, useEffect, useState } from "react";
import {
  LoaderCircle,
  Lock,
  RefreshCw,
  TrendingUp,
  Trash2,
  TriangleAlert,
} from "lucide-react";
import { ChurnScore, ConsoleLogEntry } from "../types/retail";
import { readEventStream } from "../hooks/useEventStream";
import {
  ResetCompleteModal,
  ResetSummary,
} from "./ResetCompleteModal";

interface ChurnPanelProps {
  /** Push a line into the shared Event Log. */
  onLog: (kind: ConsoleLogEntry["kind"], message: string) => void;
}

interface ChurnResponse {
  scores: ChurnScore[];
  demoControlsEnabled: boolean;
  churnFunctionConfigured: boolean;
}

const DISABLED_REASON =
  "Demo controls are disabled on the backend. They are on by default; " +
  "restart without ENABLE_DEMO_CONTROLS=0 to enable this button.";

// Distinct from DISABLED_REASON on purpose. A switched-off control and a
// control with nowhere to call are different faults with different fixes, and
// one message covering both sends the presenter to the wrong one.
const UNCONFIGURED_REASON =
  "The churn function's URL is not set on the backend (CHURN_FUNCTION_URL). " +
  "Deploy with ./deploy.sh, which wires it to the redwood-churn service.";

function formatProbability(value: number | null): string {
  if (value === null || Number.isNaN(value)) return "—";
  return value.toFixed(4);
}

function tierColour(tier: string | null): string {
  switch ((tier ?? "").toUpperCase()) {
    case "HIGH":
    case "HIGH_RISK":
    case "CRITICAL":
      return "text-rose-300 border-rose-500/40 bg-rose-500/10";
    case "MEDIUM":
    case "MEDIUM_RISK":
      return "text-amber-300 border-amber-500/40 bg-amber-500/10";
    default:
      return "text-emerald-300 border-emerald-500/40 bg-emerald-500/10";
  }
}

/** Pull `detail` out of a FastAPI error body without assuming it is there. */
async function errorDetail(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (
      body !== null &&
      typeof body === "object" &&
      typeof (body as { detail?: unknown }).detail === "string"
    ) {
      return (body as { detail: string }).detail;
    }
  } catch {
    // A non-JSON error body is not worth a second failure path.
  }
  return `HTTP ${response.status}`;
}

export const ChurnPanel: React.FC<ChurnPanelProps> = ({ onLog }) => {
  const [scores, setScores] = useState<ChurnScore[]>([]);
  const [controlsEnabled, setControlsEnabled] = useState(false);
  const [churnConfigured, setChurnConfigured] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [resetSummary, setResetSummary] = useState<ResetSummary | null>(null);

  const loadScores = useCallback(async () => {
    setLoading(true);
    try {
      const response = await fetch("/api/console/churn");
      if (!response.ok) throw new Error(await errorDetail(response));
      const body = (await response.json()) as ChurnResponse;
      setScores(body.scores ?? []);
      setControlsEnabled(Boolean(body.demoControlsEnabled));
      // Absent on an older backend, which should not read as misconfigured.
      setChurnConfigured(body.churnFunctionConfigured !== false);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadScores();
  }, [loadScores]);

  // The pipeline runs for roughly 75 seconds, and reset takes ~30 seconds.
  // A button that sits silent for that long is indistinguishable from a broken
  // one, so the elapsed time ticks even between log lines.
  useEffect(() => {
    if (!running && !resetting) return;
    const started = Date.now();
    setElapsed(0);
    const timer = window.setInterval(
      () => setElapsed(Math.floor((Date.now() - started) / 1000)),
      1000,
    );
    return () => window.clearInterval(timer);
  }, [running, resetting]);

  const recalculate = async () => {
    if (!controlsEnabled || !churnConfigured || running) return;
    setRunning(true);
    onLog("control", "POST /api/console/recalculate-churn");
    try {
      const response = await fetch("/api/console/recalculate-churn", {
        method: "POST",
      });
      if (!response.ok) {
        const detail = await errorDetail(response);
        onLog("control", `Recalculate refused: ${detail}`);
        setError(detail);
        // A 403 means the backend flag changed under us; reflect that in the UI
        // rather than leaving an enabled button that cannot work.
        if (response.status === 403) setControlsEnabled(false);
        return;
      }

      // EventSource cannot POST, so the churn function's log -- relayed by the
      // backend as SSE frames -- is read straight off the response body with
      // the shared frame parser.
      await readEventStream(response, (event, data) => {
        const payload = data as { line?: string; message?: string } | string;
        if (event === "log") {
          const line =
            typeof payload === "string" ? payload : (payload.line ?? "");
          onLog("log", line);
        } else if (event === "error") {
          const message =
            typeof payload === "string" ? payload : (payload.message ?? "");
          onLog("control", `[ERROR] ${message}`);
        } else if (event === "done") {
          onLog("control", "Churn pipeline finished.");
        }
      });

      await loadScores();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      onLog("control", `Recalculate failed: ${message}`);
      setError(message);
    } finally {
      setRunning(false);
    }
  };

  const resetDemo = async () => {
    if (!controlsEnabled || resetting) return;
    setResetting(true);
    onLog("control", "POST /api/console/reset");
    const startedAt = Date.now();
    let capturedSummary: ResetSummary | null = null;
    try {
      const response = await fetch("/api/console/reset", { method: "POST" });
      if (!response.ok) {
        const detail = await errorDetail(response);
        onLog("control", `Reset refused: ${detail}`);
        setError(detail);
        if (response.status === 403) setControlsEnabled(false);
        return;
      }

      await readEventStream(response, (event, data) => {
        if (event === "log") {
          const line =
            typeof data === "string" ? data : ((data as { line?: string })?.line ?? "");
          onLog("log", line);
        } else if (event === "summary") {
          capturedSummary = data as ResetSummary;
        } else if (event === "error") {
          const message =
            typeof data === "string" ? data : ((data as { message?: string })?.message ?? "");
          onLog("control", `[ERROR] ${message}`);
        } else if (event === "done") {
          onLog("control", "Reset finished.");
        }
      });

      const totalElapsed = (Date.now() - startedAt) / 1000;
      if (capturedSummary) {
        (capturedSummary as ResetSummary).elapsedSeconds = totalElapsed;
        setResetSummary(capturedSummary);
      } else {
        setResetSummary({
          succeeded: true,
          elapsedSeconds: totalElapsed,
        });
      }

      await loadScores();
      setError(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      onLog("control", `Reset failed: ${message}`);
      setError(message);
      setResetSummary({
        succeeded: false,
        error: message,
        elapsedSeconds: (Date.now() - startedAt) / 1000,
      });
    } finally {
      setResetting(false);
    }
  };

  const buttonBase =
    "flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded-xl text-xs font-semibold font-mono border transition-all";
  const disabledClasses =
    "cursor-not-allowed opacity-40 border-slate-800 bg-slateDark-850 text-slate-500";

  return (
    <section className="bg-slateDark-900 border border-slate-800 rounded-2xl p-4 flex flex-col gap-3">
      <header className="flex items-center justify-between">
        <h2 className="text-sm font-bold text-white flex items-center gap-2">
          <TrendingUp className="w-4 h-4 text-brand-400" />
          Churn Risk
        </h2>
        <button
          onClick={() => void loadScores()}
          className="text-[10px] font-mono text-slate-400 hover:text-brand-400 flex items-center gap-1 transition-colors"
        >
          <RefreshCw className={`w-3 h-3 ${loading ? "animate-spin" : ""}`} />
          refresh
        </button>
      </header>

      <div className="space-y-2">
        {scores.length === 0 && !loading && (
          <p className="text-[11px] font-mono text-slate-500">
            No churn rows returned for the demo customers.
          </p>
        )}
        {scores.map((score) => (
          <div
            key={score.customerId}
            className="rounded-xl border border-slate-800 bg-slateDark-950 px-3 py-2"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-[11px] font-mono font-semibold text-slate-200">
                {score.customerId}
              </span>
              <span
                className={`text-[9px] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded border ${tierColour(score.churnRiskTier)}`}
              >
                {score.churnRiskTier ?? "unscored"}
              </span>
            </div>
            <div className="mt-1 flex items-end justify-between gap-2">
              <span className="text-xl font-mono font-bold text-brand-300 leading-none">
                {formatProbability(score.churnProbability)}
              </span>
              <span className="text-[9px] font-mono text-slate-500 text-right truncate">
                {score.customerSegment ?? "—"}
                <br />
                {score.calculatedAt ?? "never calculated"}
              </span>
            </div>
          </div>
        ))}
      </div>

      {!controlsEnabled && (
        <p className="flex items-start gap-2 text-[10px] font-mono text-amber-400/90 bg-amber-500/5 border border-amber-500/20 rounded-lg px-3 py-2">
          <Lock className="w-3.5 h-3.5 shrink-0 mt-px" />
          <span>{DISABLED_REASON}</span>
        </p>
      )}

      {/* Only shown when the controls are otherwise available: two stacked
          warnings about the same dead button is noise, and the switched-off
          one is the more fundamental of the two. */}
      {controlsEnabled && !churnConfigured && (
        <p className="flex items-start gap-2 text-[10px] font-mono text-amber-400/90 bg-amber-500/5 border border-amber-500/20 rounded-lg px-3 py-2">
          <TriangleAlert className="w-3.5 h-3.5 shrink-0 mt-px" />
          <span>{UNCONFIGURED_REASON}</span>
        </p>
      )}

      <div className="flex gap-2">
        <button
          onClick={() => void recalculate()}
          disabled={!controlsEnabled || !churnConfigured || running}
          title={
            !controlsEnabled
              ? DISABLED_REASON
              : !churnConfigured
                ? UNCONFIGURED_REASON
                : "Rerun the churn pipeline in the redwood-churn function"
          }
          aria-label={
            controlsEnabled && churnConfigured
              ? "Recalculate churn"
              : "Recalculate churn (disabled)"
          }
          className={
            !controlsEnabled || !churnConfigured || running
              ? `${buttonBase} ${disabledClasses}`
              : `${buttonBase} border-brand-500/50 bg-brand-500/15 text-brand-200 hover:bg-brand-500/25`
          }
        >
          {running ? (
            <>
              <LoaderCircle className="w-3.5 h-3.5 animate-spin" />
              running {elapsed}s
            </>
          ) : (
            <>
              <RefreshCw className="w-3.5 h-3.5" />
              Recalculate Churn
            </>
          )}
        </button>

        <button
          onClick={() => void resetDemo()}
          disabled={!controlsEnabled || resetting}
          title={
            controlsEnabled
              ? "Delete demo sessions and offers so the demo can run again"
              : DISABLED_REASON
          }
          aria-label={controlsEnabled ? "Reset demo" : "Reset demo (disabled)"}
          className={
            !controlsEnabled || resetting
              ? `${buttonBase} ${disabledClasses}`
              : `${buttonBase} border-slate-700 bg-slateDark-850 text-slate-200 hover:bg-slate-800`
          }
        >
          {resetting ? (
            <>
              <LoaderCircle className="w-3.5 h-3.5 animate-spin" />
              resetting {elapsed}s
            </>
          ) : (
            <>
              <Trash2 className="w-3.5 h-3.5" />
              Reset Demo
            </>
          )}
        </button>
      </div>

      {error && (
        <p className="flex items-start gap-2 text-[10px] font-mono text-rose-300 bg-rose-500/5 border border-rose-500/20 rounded-lg px-3 py-2">
          <TriangleAlert className="w-3.5 h-3.5 shrink-0 mt-px" />
          <span>{error}</span>
        </p>
      )}

      {resetSummary && (
        <ResetCompleteModal
          summary={resetSummary}
          onClose={() => setResetSummary(null)}
        />
      )}
    </section>
  );
};
