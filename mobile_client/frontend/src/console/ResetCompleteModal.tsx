import React, { useEffect } from "react";
import { CheckCircle2, XCircle, ArrowRight } from "lucide-react";

export interface ResetSummary {
  succeeded: boolean;
  error?: string;
  deleted?: {
    sessions: number;
    offers: number;
    mobileOrders: number;
    legacyOrders?: number;
    traces?: number;
  };
  scores?: Array<{
    customerId: string;
    churnRiskTier: string | null;
    churnProbability: number | null;
  }>;
  warmUp?: {
    attempted: boolean;
    succeeded: boolean;
    status?: string | null;
    elapsedSeconds?: number | null;
  };
  elapsedSeconds?: number;
}

interface ResetCompleteModalProps {
  summary: ResetSummary;
  onClose: () => void;
}

export const ResetCompleteModal: React.FC<ResetCompleteModalProps> = ({
  summary,
  onClose,
}) => {
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  const deleted = summary.deleted;
  const deletedParts: string[] = [];
  if (deleted) {
    if (deleted.sessions != null)
      deletedParts.push(`${deleted.sessions} session${deleted.sessions === 1 ? "" : "s"}`);
    if (deleted.offers != null)
      deletedParts.push(`${deleted.offers} offer${deleted.offers === 1 ? "" : "s"}`);
    if (deleted.mobileOrders != null)
      deletedParts.push(`${deleted.mobileOrders} order${deleted.mobileOrders === 1 ? "" : "s"}`);
    if (deleted.traces != null && deleted.traces > 0)
      deletedParts.push(`${deleted.traces} trace${deleted.traces === 1 ? "" : "s"}`);
  }

  const scoresText =
    summary.scores && summary.scores.length > 0
      ? summary.scores
          .map(
            (s) =>
              `${s.customerId} ${s.churnRiskTier ?? "—"} ${
                s.churnProbability != null ? s.churnProbability.toFixed(4) : "—"
              }`,
          )
          .join(" · ")
      : "—";

  const warmUpText = summary.warmUp?.attempted
    ? summary.warmUp.succeeded
      ? `warm (${summary.warmUp.status ?? "DONE"} in ${summary.warmUp.elapsedSeconds?.toFixed(1) ?? "—"}s)`
      : `still cold after ${summary.warmUp.elapsedSeconds?.toFixed(1) ?? "—"}s (${summary.warmUp.status ?? "timeout"})`
    : "skipped";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md bg-slateDark-900 border border-slate-800 rounded-2xl p-6 shadow-2xl space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3">
          {summary.succeeded ? (
            <div className="w-10 h-10 rounded-xl bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center text-emerald-400">
              <CheckCircle2 className="w-6 h-6" />
            </div>
          ) : (
            <div className="w-10 h-10 rounded-xl bg-rose-500/10 border border-rose-500/30 flex items-center justify-center text-rose-400">
              <XCircle className="w-6 h-6" />
            </div>
          )}
          <div>
            <h2 className="text-base font-bold text-white">
              {summary.succeeded ? "Reset complete" : "Reset failed"}
            </h2>
            <p className="text-xs text-slate-400 font-mono">
              {summary.succeeded
                ? "The demo starting point has been restored."
                : "The reset encountered an error."}
            </p>
          </div>
        </div>

        {summary.error ? (
          <div className="p-3 bg-rose-500/10 border border-rose-500/30 rounded-xl text-xs font-mono text-rose-300 break-words">
            {summary.error}
          </div>
        ) : (
          <div className="bg-slateDark-950 border border-slate-800/80 rounded-xl p-3.5 space-y-2 font-mono text-xs">
            <div className="flex justify-between items-baseline text-slate-300">
              <span className="text-slate-500 uppercase text-[10px] tracking-wider">
                Deleted
              </span>
              <span className="text-right text-slate-200">
                {deletedParts.join(" · ") || "None"}
              </span>
            </div>
            <div className="flex justify-between items-baseline text-slate-300">
              <span className="text-slate-500 uppercase text-[10px] tracking-wider">
                Churn
              </span>
              <span className="text-right text-slate-200">{scoresText}</span>
            </div>
            <div className="flex justify-between items-baseline text-slate-300">
              <span className="text-slate-500 uppercase text-[10px] tracking-wider">
                Agent
              </span>
              <span className="text-right text-slate-200">{warmUpText}</span>
            </div>
            {summary.elapsedSeconds != null && (
              <div className="flex justify-between items-baseline text-slate-300">
                <span className="text-slate-500 uppercase text-[10px] tracking-wider">
                  Elapsed
                </span>
                <span className="text-right text-brand-300 font-semibold">
                  {summary.elapsedSeconds.toFixed(1)}s
                </span>
              </div>
            )}
          </div>
        )}

        <div className="pt-2 flex justify-end">
          <button
            onClick={onClose}
            className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-gradient-to-r from-brand-600 via-amber-600 to-brand-600 hover:from-brand-500 hover:to-amber-500 text-white font-semibold text-xs shadow-lg shadow-brand-600/30 transition-all active:scale-[0.98]"
          >
            <span>Start the demo</span>
            <ArrowRight className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
    </div>
  );
};
