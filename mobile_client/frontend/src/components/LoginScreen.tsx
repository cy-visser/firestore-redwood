import React, { useState } from "react";
import { ShieldCheck, UserCheck, Sparkles, Loader2, Database } from "lucide-react";
import { PrincipalProfile } from "../types/retail";

interface LoginScreenProps {
  profiles: Record<string, PrincipalProfile>;
  firestoreConnected: boolean;
  onSignIn: (principalId: string) => Promise<void>;
  error: string | null;
}

/**
 * The demo's front door: two identity cards and a sign in button.
 *
 * Signing in is non-blocking by design. The POST writes a session and returns;
 * the app opens on the catalog immediately and the agent's verdict arrives
 * later over SSE. Holding this screen until the agent finished would put a
 * twenty-second spinner in front of the audience and would make the demo look
 * like a synchronous request to a slow API, which is the opposite of what the
 * architecture actually does.
 */
export const LoginScreen: React.FC<LoginScreenProps> = ({
  profiles,
  firestoreConnected,
  onSignIn,
  error,
}) => {
  const [selected, setSelected] = useState<string>("demo1");
  const [submitting, setSubmitting] = useState<boolean>(false);

  const handleSignIn = async () => {
    // The button is disabled for the duration of the request. The backend
    // reuses an in-flight session anyway, so a double tap cannot produce two
    // offers, but there is no reason to make it work for its living.
    if (submitting) return;
    setSubmitting(true);
    try {
      await onSignIn(selected);
    } finally {
      setSubmitting(false);
    }
  };

  const cards: Array<{
    id: string;
    accent: string;
    ring: string;
    icon: React.ReactNode;
    tagline: string;
  }> = [
    {
      id: "demo1",
      accent: "from-brand-600 to-amber-600",
      ring: "border-amber-500/60 shadow-brand-600/20",
      icon: <UserCheck className="w-4 h-4" />,
      tagline: "Enterprise VIP • low churn risk",
    },
    {
      id: "demo2",
      accent: "from-sky-600 to-indigo-600",
      ring: "border-sky-500/60 shadow-sky-600/20",
      icon: <ShieldCheck className="w-4 h-4" />,
      tagline: "Standard Loyalty • high churn risk",
    },
  ];

  return (
    <div className="flex-1 flex flex-col overflow-y-auto px-5 pt-12 pb-8 bg-slateDark-900">
      <div className="flex items-center gap-2 mb-1">
        <div className="w-9 h-9 rounded-xl bg-gradient-to-tr from-brand-600 to-amber-500 flex items-center justify-center shadow-lg shadow-brand-600/30">
          <Sparkles className="w-5 h-5 text-white" />
        </div>
        <div>
          <h1 className="text-lg font-extrabold tracking-tight text-white">
            Redwood Retail
          </h1>
          <p className="text-[10px] text-slate-400 font-mono flex items-center gap-1">
            <Database className="w-2.5 h-2.5" />
            <span>
              {firestoreConnected ? "redwood.retail • connected" : "connecting…"}
            </span>
          </p>
        </div>
      </div>

      <p className="text-xs text-slate-400 mt-5 mb-3 leading-relaxed">
        Choose an identity to sign in as.
      </p>

      <div className="space-y-3">
        {cards.map((card) => {
          const profile = profiles[card.id];
          const isSelected = selected === card.id;
          return (
            <button
              key={card.id}
              onClick={() => setSelected(card.id)}
              className={`w-full text-left rounded-2xl p-4 border transition-all ${
                isSelected
                  ? `bg-gradient-to-br ${card.accent} text-white shadow-xl ${card.ring}`
                  : "bg-slateDark-850 border-slate-700/60 text-slate-300 hover:border-slate-600"
              }`}
            >
              <div className="flex items-center justify-between mb-1.5">
                <span className="flex items-center gap-1.5 text-xs font-bold tracking-tight">
                  {card.icon}
                  {card.id}
                </span>
                <span
                  className={`text-[9px] font-mono px-1.5 py-0.5 rounded uppercase tracking-wider ${
                    isSelected
                      ? "bg-black/30 text-white/90"
                      : "bg-slate-800 text-slate-400"
                  }`}
                >
                  {profile?.loyaltyTier || "…"}
                </span>
              </div>
              <p className="text-sm font-semibold truncate">
                {profile?.displayName || "Loading profile…"}
              </p>
              <p
                className={`text-[10px] font-mono truncate mt-0.5 ${
                  isSelected ? "text-white/75" : "text-slate-500"
                }`}
              >
                {profile?.iamPrincipal || card.tagline}
              </p>
            </button>
          );
        })}
      </div>

      {error && (
        <p className="mt-4 text-[11px] font-mono text-rose-300 bg-rose-500/10 border border-rose-500/30 rounded-xl px-3 py-2">
          {error}
        </p>
      )}

      <button
        onClick={handleSignIn}
        disabled={submitting}
        className="mt-6 w-full rounded-2xl bg-white text-slate-900 font-bold text-sm py-3.5 shadow-lg transition-all disabled:opacity-60 disabled:cursor-not-allowed hover:bg-slate-100 flex items-center justify-center gap-2"
      >
        {submitting && <Loader2 className="w-4 h-4 animate-spin" />}
        {submitting ? "Writing session…" : "Sign in"}
      </button>
    </div>
  );
};
