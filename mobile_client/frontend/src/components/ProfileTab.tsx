import React from "react";
import {
  ShieldCheck,
  Activity,
  Lock
} from "lucide-react";
import { PrincipalProfile } from "../types/retail";

interface ProfileTabProps {
  activePrincipalId: string;
  principal: PrincipalProfile;
  onSwitchPrincipal: (id: string) => void;
}

export const ProfileTab: React.FC<ProfileTabProps> = ({
  activePrincipalId,
  principal,
  onSwitchPrincipal,
}) => {
  const isDemo1 = activePrincipalId === "demo1";

  return (
    <div className="flex-1 flex flex-col overflow-y-auto px-4 py-3 space-y-3 pb-28">
      {/* Account Hero Card */}
      <div
        className={`rounded-3xl p-4 text-white relative overflow-hidden shadow-xl border ${
          isDemo1
            ? "bg-gradient-to-br from-brand-700 via-amber-700 to-slate-900 border-amber-500/40 shadow-brand-700/20"
            : "bg-gradient-to-br from-sky-700 via-indigo-800 to-slate-900 border-sky-500/40 shadow-sky-700/20"
        }`}
      >
        <div className="flex items-center justify-between mb-3">
          <span className="text-[10px] font-mono px-2.5 py-0.5 rounded-full bg-black/40 border border-white/20 uppercase tracking-wider font-bold">
            {principal?.loyaltyTier} MEMBER
          </span>
          <span className="text-[10px] font-mono text-white/80 flex items-center gap-1">
            <Lock className="w-3 h-3" />
            <span>IAM Service Account</span>
          </span>
        </div>

        <h3 className="text-lg font-extrabold tracking-tight mb-0.5">
          {principal?.displayName}
        </h3>
        <p className="text-xs font-mono text-white/80 truncate mb-4">
          {principal?.iamPrincipal}
        </p>

        <div className="grid grid-cols-3 gap-2 pt-3 border-t border-white/15 text-center font-mono">
          <div>
            <span className="text-[10px] text-white/70 block">Tier Discount</span>
            <span className="text-sm font-bold text-white">
              {Math.round((principal?.discountRate || 0) * 100)}% OFF
            </span>
          </div>
          <div>
            <span className="text-[10px] text-white/70 block">Account Age</span>
            <span className="text-sm font-bold text-white">
              {principal?.accountAgeDays} days
            </span>
          </div>
          <div>
            <span className="text-[10px] text-white/70 block">Orders (12m)</span>
            <span className="text-sm font-bold text-white">
              {principal?.historicalMetrics?.ordersCountLast12m}
            </span>
          </div>
        </div>
      </div>

      {/* Switch Demo Principal Quick Action */}
      <div className="bg-slateDark-850 border border-slate-800 rounded-2xl p-3 flex items-center justify-between">
        <div>
          <p className="text-xs font-bold text-white">Switch IAM Principal</p>
          <p className="text-[10px] text-slate-400 font-mono">
            Currently acting as {activePrincipalId}
          </p>
        </div>
        <button
          onClick={() => onSwitchPrincipal(isDemo1 ? "demo2" : "demo1")}
          className="px-3 py-1.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-brand-400 border border-slate-700 text-xs font-mono font-semibold transition-all"
        >
          Switch to {isDemo1 ? "demo2" : "demo1"}
        </button>
      </div>

      {/* Cloud Security & IAM Role Info */}
      <div className="bg-slateDark-850 border border-slate-800 rounded-2xl p-3.5 space-y-2 text-xs font-mono">
        <h4 className="font-bold text-slate-200 flex items-center gap-1.5">
          <ShieldCheck className="w-4 h-4 text-emerald-400" />
          <span>Google Cloud IAM Credentials</span>
        </h4>
        <div className="bg-slate-900/80 p-2.5 rounded-xl border border-slate-800 space-y-1 text-[11px] text-slate-400">
          <div className="flex justify-between">
            <span>Project:</span>
            <span className="text-slate-200">elevate-cyvisser</span>
          </div>
          <div className="flex justify-between">
            <span>Firestore Database:</span>
            <span className="text-slate-200">redwood</span>
          </div>
          <div className="flex justify-between">
            <span>Granted IAM Role:</span>
            <span className="text-emerald-400 font-bold">roles/datastore.user</span>
          </div>
          <div className="flex justify-between">
            <span>Provisioned By:</span>
            <span className="text-cyan-400">Terraform (terraform/iam.tf)</span>
          </div>
        </div>
      </div>

      {/* 4-Pillars Historical Metrics */}
      <div className="bg-slateDark-850 border border-slate-800 rounded-2xl p-3.5 space-y-2.5">
        <h4 className="text-xs font-bold text-slate-200 flex items-center gap-1.5">
          <Activity className="w-3.5 h-3.5 text-cyan-400" />
          <span>BigQuery ML Churn Health Metrics</span>
        </h4>

        <div className="space-y-2 font-mono text-xs">
          <div>
            <div className="flex justify-between text-slate-400 text-[11px] mb-1">
              <span>App Engagement Score</span>
              <span className="text-white font-bold">
                {Math.round((principal?.engagementMetrics?.appEngagementScore || 0) * 100)}%
              </span>
            </div>
            <div className="w-full h-2 bg-slate-900 rounded-full overflow-hidden border border-slate-800">
              <div
                className={`h-full rounded-full ${
                  (principal?.engagementMetrics?.appEngagementScore || 0) > 0.7
                    ? "bg-emerald-500"
                    : "bg-amber-500"
                }`}
                style={{
                  width: `${(principal?.engagementMetrics?.appEngagementScore || 0) * 100}%`
                }}
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-2 pt-1">
            <div className="bg-slate-900 p-2 rounded-xl border border-slate-800">
              <span className="text-[10px] text-slate-500 block">Total Spend (90d)</span>
              <span className="text-xs font-bold text-white">
                €{principal?.historicalMetrics?.totalSpend90d?.toFixed(2)}
              </span>
            </div>
            <div className="bg-slate-900 p-2 rounded-xl border border-slate-800">
              <span className="text-[10px] text-slate-500 block">Lifetime Spend</span>
              <span className="text-xs font-bold text-white">
                €{principal?.historicalMetrics?.lifetimeSpend?.toFixed(2)}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
