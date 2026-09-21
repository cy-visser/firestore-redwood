import React, { useState } from "react";
import { X, Copy, Check, ShieldCheck, Database } from "lucide-react";
import { OrderDocument } from "../types/retail";

interface JSONInspectorModalProps {
  order: OrderDocument | null;
  onClose: () => void;
  title?: string;
}

export const JSONInspectorModal: React.FC<JSONInspectorModalProps> = ({
  order,
  onClose,
  title = "Firestore Order JSON Document",
}) => {
  const [copied, setCopied] = useState<boolean>(false);
  const [activeTab, setActiveTab] = useState<"json" | "pillars">("json");

  if (!order) return null;

  const jsonString = JSON.stringify(order, null, 2);

  const handleCopy = () => {
    navigator.clipboard.writeText(jsonString);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-3 sm:p-4">
      <div className="w-full max-w-lg bg-slateDark-900 border border-slate-700 rounded-3xl p-4 sm:p-5 flex flex-col max-h-[90vh] shadow-2xl overflow-hidden">
        {/* Modal Header */}
        <div className="flex items-center justify-between pb-3 border-b border-slate-800">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-xl bg-brand-500/20 border border-brand-500/40 flex items-center justify-center text-brand-400">
              <Database className="w-4 h-4" />
            </div>
            <div>
              <h3 className="text-sm font-bold text-white leading-tight">{title}</h3>
              <p className="text-[10px] text-emerald-400 font-mono flex items-center gap-1">
                <ShieldCheck className="w-3 h-3" />
                <span>100% generate_retail_dataset.py Compliant</span>
              </p>
            </div>
          </div>

          <button
            onClick={onClose}
            className="w-8 h-8 rounded-full bg-slate-800 hover:bg-slate-700 flex items-center justify-center text-slate-400 hover:text-white transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Parity & BQML Badges */}
        <div className="flex items-center gap-1.5 py-2 overflow-x-auto no-scrollbar text-[10px] font-mono">
          <span className="px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 whitespace-nowrap">
            ✓ Native Firestore
          </span>
          <span className="px-2 py-0.5 rounded-full bg-cyan-500/10 text-cyan-400 border border-cyan-500/20 whitespace-nowrap">
            ✓ Dataflow CDC Stream
          </span>
          <span className="px-2 py-0.5 rounded-full bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 whitespace-nowrap">
            ✓ BigQuery ML Features
          </span>
        </div>

        {/* View Switcher */}
        <div className="flex items-center bg-slateDark-850 p-1 rounded-xl border border-slate-800 my-1 font-mono text-xs">
          <button
            onClick={() => setActiveTab("json")}
            className={`flex-1 py-1 rounded-lg text-center transition-all ${
              activeTab === "json"
                ? "bg-brand-600 text-white font-semibold"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            Raw JSON Document
          </button>
          <button
            onClick={() => setActiveTab("pillars")}
            className={`flex-1 py-1 rounded-lg text-center transition-all ${
              activeTab === "pillars"
                ? "bg-brand-600 text-white font-semibold"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            4-Pillars BQML Inspector
          </button>
        </div>

        {/* Content Body */}
        <div className="flex-1 overflow-y-auto my-2 rounded-2xl bg-black/60 border border-slate-800 p-3 font-mono text-xs">
          {activeTab === "json" ? (
            <pre className="text-slate-300 whitespace-pre-wrap select-text leading-relaxed text-[11px]">
              {jsonString}
            </pre>
          ) : (
            <div className="space-y-3">
              {/* Pillar 1 */}
              <div className="bg-slateDark-850 border border-slate-800 rounded-xl p-2.5 space-y-1">
                <span className="text-[10px] text-amber-400 font-bold uppercase tracking-wider">
                  Pillar 1: Demographics & Account State
                </span>
                <div className="grid grid-cols-2 gap-1 text-[11px] text-slate-300">
                  <div>Tier: {order.accountState?.loyaltyTier}</div>
                  <div>Member: {order.accountState?.isLoyaltyMember ? "Yes (1)" : "No (0)"}</div>
                  <div>Age: {order.accountState?.accountAgeDays} days</div>
                  <div>Segment: {order.accountState?.customerSegment}</div>
                </div>
              </div>

              {/* Pillar 2 */}
              <div className="bg-slateDark-850 border border-slate-800 rounded-xl p-2.5 space-y-1">
                <span className="text-[10px] text-emerald-400 font-bold uppercase tracking-wider">
                  Pillar 2: Transactional Metrics
                </span>
                <div className="grid grid-cols-2 gap-1 text-[11px] text-slate-300">
                  <div>90d Spend: €{order.transactionalMetrics?.totalSpend90d?.toFixed(2)}</div>
                  <div>Lifetime: €{order.transactionalMetrics?.lifetimeSpend?.toFixed(2)}</div>
                  <div>AOV: €{order.transactionalMetrics?.avgOrderValue?.toFixed(2)}</div>
                  <div>Monthly Freq: {order.transactionalMetrics?.purchaseFrequencyMonthly}</div>
                </div>
              </div>

              {/* Pillar 3 */}
              <div className="bg-slateDark-850 border border-slate-800 rounded-xl p-2.5 space-y-1">
                <span className="text-[10px] text-cyan-400 font-bold uppercase tracking-wider">
                  Pillar 3: App Engagement & Activity
                </span>
                <div className="grid grid-cols-2 gap-1 text-[11px] text-slate-300">
                  <div>Logins/mo: {order.engagement?.loginFrequencyMonthly}</div>
                  <div>Session: {order.engagement?.avgSessionDurationMinutes} min</div>
                  <div>Engagement: {order.engagement?.appEngagementScore}</div>
                  <div>30d Sessions: {order.engagement?.appSessionsLast30d}</div>
                </div>
              </div>

              {/* Pillar 4 */}
              <div className="bg-slateDark-850 border border-slate-800 rounded-xl p-2.5 space-y-1">
                <span className="text-[10px] text-rose-400 font-bold uppercase tracking-wider">
                  Pillar 4: Support & Sentiment Satisfaction
                </span>
                <div className="grid grid-cols-2 gap-1 text-[11px] text-slate-300">
                  <div>Rating: {order.customerFeedback?.rating} / 5</div>
                  <div>Sentiment: {order.customerFeedback?.sentimentScore}</div>
                  <div>Active Complaint: {order.supportMetrics?.hasActiveComplaint ? "YES" : "No"}</div>
                  <div>Reason: {order.supportMetrics?.primaryComplaintReason || "None"}</div>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Modal Footer */}
        <div className="flex items-center justify-between pt-2 border-t border-slate-800">
          <span className="text-[10px] font-mono text-slate-500">
            Order ID: {order.orderId}
          </span>

          <button
            onClick={handleCopy}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-brand-600 hover:bg-brand-500 text-white text-xs font-semibold shadow-md shadow-brand-600/30 transition-all active:scale-95"
          >
            {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
            <span>{copied ? "Copied JSON!" : "Copy JSON"}</span>
          </button>
        </div>
      </div>
    </div>
  );
};
