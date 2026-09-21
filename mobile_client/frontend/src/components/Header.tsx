import React from "react";
import { ShieldCheck, UserCheck, Sparkles, Database } from "lucide-react";
import { PrincipalProfile } from "../types/retail";

interface HeaderProps {
  activePrincipalId: string;
  onSelectPrincipal: (id: string) => void;
  profiles: Record<string, PrincipalProfile>;
  firestoreConnected: boolean;
}

export const Header: React.FC<HeaderProps> = ({
  activePrincipalId,
  onSelectPrincipal,
  profiles,
  firestoreConnected,
}) => {
  const currentProfile = profiles[activePrincipalId];

  return (
    <div className="pt-8 sm:pt-10 px-4 pb-3 bg-slateDark-900/90 backdrop-blur-md border-b border-slate-800/80 sticky top-0 z-40">
      {/* Top Brand Line */}
      <div className="flex items-center justify-between mb-2.5">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-lg bg-gradient-to-tr from-brand-600 to-amber-500 flex items-center justify-center shadow-md shadow-brand-600/30">
            <Sparkles className="w-4 h-4 text-white" />
          </div>
          <div>
            <h1 className="text-sm font-bold tracking-tight text-white flex items-center gap-1.5">
              Redwood Retail
              <span className="text-[10px] font-mono px-1.5 py-0.2 rounded bg-brand-500/20 text-brand-400 border border-brand-500/30">
                v2.0
              </span>
            </h1>
            <p className="text-[10px] text-slate-400 font-mono flex items-center gap-1">
              <Database className="w-2.5 h-2.5" />
              <span>redwood.retail</span>
            </p>
          </div>
        </div>

        {/* Database Live Connectivity Indicator */}
        <div className="flex items-center gap-1.5 px-2 py-1 rounded-full bg-slate-800/60 border border-slate-700/60 text-[10px] font-mono">
          <span
            className={`w-2 h-2 rounded-full ${
              firestoreConnected
                ? "bg-emerald-400 animate-pulse shadow-sm shadow-emerald-400/50"
                : "bg-amber-400"
            }`}
          />
          <span className="text-slate-300">
            {firestoreConnected ? "Firestore Native" : "Connecting..."}
          </span>
        </div>
      </div>

      {/* IAM Principal Switcher Pill */}
      <div className="flex items-center justify-between bg-slateDark-850 p-1 rounded-xl border border-slate-700/60 shadow-inner">
        <div className="flex items-center gap-1">
          <button
            onClick={() => onSelectPrincipal("demo1")}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all ${
              activePrincipalId === "demo1"
                ? "bg-gradient-to-r from-brand-600 to-amber-600 text-white shadow-md shadow-brand-600/30"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <UserCheck className="w-3.5 h-3.5" />
            <span>demo1</span>
            <span className="text-[9px] px-1 py-0.2 rounded bg-black/30 text-amber-200 font-mono">
              VIP 25%
            </span>
          </button>

          <button
            onClick={() => onSelectPrincipal("demo2")}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all ${
              activePrincipalId === "demo2"
                ? "bg-gradient-to-r from-sky-600 to-indigo-600 text-white shadow-md shadow-sky-600/30"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <ShieldCheck className="w-3.5 h-3.5" />
            <span>demo2</span>
            <span className="text-[9px] px-1 py-0.2 rounded bg-black/30 text-sky-200 font-mono">
              STD 10%
            </span>
          </button>
        </div>

        {/* Identity Email Snippet */}
        <div className="text-right pr-1.5 hidden min-[360px]:block">
          <p className="text-[9px] font-mono text-slate-400 truncate max-w-[120px]">
            {currentProfile?.iamPrincipal?.split("@")[0] || activePrincipalId}
          </p>
          <p className="text-[8px] text-slate-500 font-mono uppercase tracking-wider">
            IAM Service Account
          </p>
        </div>
      </div>
    </div>
  );
};
