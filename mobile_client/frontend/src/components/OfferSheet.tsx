import React, { useState } from "react";
import {
  Gift,
  X,
  Check,
  Truck,
  Loader2,
  BadgePercent,
} from "lucide-react";
import { LoyaltyOffer } from "../types/retail";

interface OfferSheetProps {
  offer: LoyaltyOffer;
  onClose: () => void;
  onClaim: (offerId: string) => Promise<void>;
}

/**
 * The retention offer, revealed as a sheet sliding up over the app.
 *
 * It arrives whenever the agent finishes, which is seconds after login and
 * long after the app has opened. That is why this is a sheet over the running
 * app rather than a screen in a flow: there is no point in the journey where
 * the client is waiting for it.
 */
export const OfferSheet: React.FC<OfferSheetProps> = ({
  offer,
  onClose,
  onClaim,
}) => {
  const [claiming, setClaiming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const claimed = offer.status === "REDEEMED";

  const handleClaim = async () => {
    if (claiming || claimed) return;
    setClaiming(true);
    setError(null);
    try {
      await onClaim(offer.offerId);
      // The claim succeeded and the toast says so. Leaving the sheet up over
      // the catalog makes the customer dismiss a dialog that has nothing left
      // to tell them, and hides the app they came back to use.
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not claim this offer");
    } finally {
      setClaiming(false);
    }
  };

  return (
    <div className="absolute inset-0 z-50 flex flex-col justify-end">
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />

      <div className="relative bg-slateDark-850 border-t border-brand-500/40 rounded-t-3xl px-5 pt-4 pb-6 shadow-2xl max-h-[85%] overflow-y-auto">
        <div className="w-10 h-1 bg-slate-600 rounded-full mx-auto mb-4" />

        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-slate-500 hover:text-slate-300"
          aria-label="Dismiss offer"
        >
          <X className="w-4 h-4" />
        </button>

        <div className="flex items-center gap-2 mb-3">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-tr from-brand-600 to-amber-500 flex items-center justify-center shadow-lg shadow-brand-600/30">
            <Gift className="w-5 h-5 text-white" />
          </div>
          <div>
            <p className="text-[9px] font-mono uppercase tracking-widest text-brand-400">
              Retention offer
            </p>
            <h2 className="text-base font-extrabold tracking-tight text-white leading-tight">
              {offer.title}
            </h2>
          </div>
        </div>

        <p className="text-xs text-slate-300 leading-relaxed mb-3">
          {offer.description}
        </p>

        {offer.personalizedApology && (
          <p className="text-[11px] text-amber-200/90 bg-amber-500/10 border border-amber-500/25 rounded-xl px-3 py-2 mb-3 leading-relaxed">
            {offer.personalizedApology}
          </p>
        )}

        <div className="grid grid-cols-2 gap-2 mb-3">
          <div className="rounded-2xl bg-slateDark-900 border border-slate-700/60 p-3">
            <p className="text-[9px] font-mono uppercase tracking-wider text-slate-500 mb-0.5">
              Discount
            </p>
            <p className="text-xl font-extrabold text-white font-mono flex items-center gap-1">
              <BadgePercent className="w-4 h-4 text-brand-400" />
              {offer.discountPercent}%
            </p>
          </div>
          <div className="rounded-2xl bg-slateDark-900 border border-slate-700/60 p-3">
            <p className="text-[9px] font-mono uppercase tracking-wider text-slate-500 mb-0.5">
              Promo code
            </p>
            <p className="text-xs font-bold text-brand-300 font-mono break-all">
              {offer.promoCode}
            </p>
          </div>
        </div>

        {(offer.freeExpressShipping || offer.perks?.length > 0) && (
          <div className="flex flex-wrap gap-1.5 mb-3">
            {offer.freeExpressShipping && (
              <span className="text-[10px] font-mono px-2 py-1 rounded-full bg-emerald-500/10 text-emerald-300 border border-emerald-500/30 flex items-center gap-1">
                <Truck className="w-3 h-3" />
                Free express shipping
              </span>
            )}
            {offer.perks?.map((perk) => (
              <span
                key={perk}
                className="text-[10px] font-mono px-2 py-1 rounded-full bg-slate-800 text-slate-300 border border-slate-700"
              >
                {perk}
              </span>
            ))}
          </div>
        )}

        {error && (
          <p className="mb-3 text-[11px] font-mono text-rose-300 bg-rose-500/10 border border-rose-500/30 rounded-xl px-3 py-2">
            {error}
          </p>
        )}

        <button
          onClick={handleClaim}
          disabled={claiming || claimed}
          className={`w-full rounded-2xl font-bold text-sm py-3.5 shadow-lg transition-all flex items-center justify-center gap-2 ${
            claimed
              ? "bg-emerald-500/15 text-emerald-300 border border-emerald-500/40 cursor-default"
              : "bg-gradient-to-r from-brand-600 to-amber-600 text-white hover:brightness-110 disabled:opacity-60"
          }`}
        >
          {claiming && <Loader2 className="w-4 h-4 animate-spin" />}
          {claimed && <Check className="w-4 h-4" />}
          {claimed ? "Claimed" : claiming ? "Claiming…" : "Claim offer"}
        </button>

        {claimed && offer.claimedAt && (
          <p className="mt-2 text-[10px] font-mono text-slate-500 text-center">
            status REDEEMED • claimedAt {offer.claimedAt}
          </p>
        )}
      </div>
    </div>
  );
};
