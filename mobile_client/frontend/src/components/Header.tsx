import React from "react";
import {
  UserCheck,
  ShieldCheck,
  Sparkles,
  Database,
  Gift,
  HeartPulse,
  AlertTriangle,
  LogOut
} from "lucide-react";
import { CustomerSession, LoyaltyOffer, PrincipalProfile } from "../types/retail";

interface HeaderProps {
  activePrincipalId: string;
  profiles: Record<string, PrincipalProfile>;
  session: CustomerSession;
  /** The agent's offer, if this customer has one that is still unspent. */
  offer: LoyaltyOffer | null;
  onSignOut: () => void;
  onShowOffer?: () => void;
}

interface AgentPill {
  label: string;
  icon: React.ReactNode;
  className: string;
}

/**
 * Describe what the agent decided, from the session document alone.
 *
 * Returns null while the agent is still working, and the header renders
 * nothing at all in that case. A spinner here would say "the app is waiting",
 * and the whole argument of this architecture is that it is not: the login
 * returned immediately, the catalog is open and usable, and the outcome
 * arrives over SSE whenever the agent is finished. Announcing an intermediate
 * state invites the audience to watch it, which is the one thing the design is
 * trying to make unnecessary.
 *
 * The app never asks the backend "is it done yet". This is the rendering of
 * whatever the last snapshot said, so the pill is exactly as current as
 * Firestore is.
 */
function agentPill(
  session: CustomerSession,
  offer: LoyaltyOffer | null,
): AgentPill | null {
  switch (session.agentProcessingStatus) {
    case "PROCESSED":
      return offer || session.offerId
        ? {
            // Name the discount here: it is the number that will show up on
            // the order, so seeing it in the header first makes the causal
            // chain obvious during the demo.
            label: offer
              ? `Offer ready · ${offer.discountPercent}% off`
              : "Offer ready",
            icon: <Gift className="w-3 h-3" />,
            className: "bg-brand-500/15 text-brand-300 border-brand-500/40",
          }
        : {
            label: "Loyalty status: healthy",
            icon: <HeartPulse className="w-3 h-3" />,
            className: "bg-emerald-500/10 text-emerald-300 border-emerald-500/30",
          };
    case "SKIPPED":
      return {
        label: "Loyalty status: healthy",
        icon: <HeartPulse className="w-3 h-3" />,
        className: "bg-emerald-500/10 text-emerald-300 border-emerald-500/30",
      };
    case "ERROR":
      return {
        label: "Agent error",
        icon: <AlertTriangle className="w-3 h-3" />,
        className: "bg-rose-500/10 text-rose-300 border-rose-500/30",
      };
    // PENDING, PROCESSING and anything unrecognised: say nothing.
    default:
      return null;
  }
}

export const Header: React.FC<HeaderProps> = ({
  activePrincipalId,
  profiles,
  session,
  offer,
  onSignOut,
  onShowOffer,
}) => {
  const currentProfile = profiles[activePrincipalId];
  const isDemo1 = activePrincipalId === "demo1";
  const pill = agentPill(session, offer);
  const pillClickable = Boolean(onShowOffer) && (Boolean(offer) || Boolean(session.offerId));

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
            </h1>
            <p className="text-[10px] text-slate-400 font-mono flex items-center gap-1">
              <Database className="w-2.5 h-2.5" />
              <span>redwood.retail</span>
            </p>
          </div>
        </div>
      </div>

      {/* Signed-in identity and the agent's progress on this login */}
      <div className="flex items-center justify-between bg-slateDark-850 p-1 pl-2 rounded-xl border border-slate-700/60 shadow-inner gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span
            className={`flex items-center gap-1.5 px-2 py-1 rounded-lg text-xs font-semibold shrink-0 ${
              isDemo1
                ? "bg-gradient-to-r from-brand-600 to-amber-600 text-white shadow-md shadow-brand-600/30"
                : "bg-gradient-to-r from-sky-600 to-indigo-600 text-white shadow-md shadow-sky-600/30"
            }`}
          >
            {isDemo1 ? (
              <UserCheck className="w-3.5 h-3.5" />
            ) : (
              <ShieldCheck className="w-3.5 h-3.5" />
            )}
            <span>{activePrincipalId}</span>
          </span>

          <div className="min-w-0 hidden min-[360px]:block">
            <p className="text-[10px] font-semibold text-slate-200 truncate">
              {currentProfile?.displayName || session.customerName || session.customerId}
            </p>
            <p className="text-[9px] text-slate-500 font-mono truncate">
              {session.customerId}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-1.5 shrink-0">
          {pill && (
            <button
              type="button"
              onClick={pillClickable ? onShowOffer : undefined}
              disabled={!pillClickable}
              title={pillClickable ? "Show the offer" : undefined}
              className={`flex items-center gap-1 px-2 py-1 rounded-lg border text-[10px] font-mono ${pill.className} ${
                pillClickable ? "cursor-pointer hover:brightness-125" : "cursor-default"
              }`}
            >
              {pill.icon}
              <span className="hidden min-[340px]:inline">{pill.label}</span>
            </button>
          )}

          <button
            type="button"
            onClick={onSignOut}
            title="Sign out"
            className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 transition-all"
          >
            <LogOut className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
    </div>
  );
};
