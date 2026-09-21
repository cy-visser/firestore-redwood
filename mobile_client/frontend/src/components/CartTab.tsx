import React, { useState } from "react";
import {
  Trash2,
  Plus,
  Minus,
  ShoppingBag,
  Truck,
  Star,
  AlertTriangle,
  FileCode,
  ArrowRight
} from "lucide-react";
import {
  CartItem,
  PrincipalProfile,
  ShippingCity,
  OrderDocument
} from "../types/retail";

interface CartTabProps {
  cart: CartItem[];
  activePrincipalId: string;
  principal: PrincipalProfile;
  cities: ShippingCity[];
  complaintReasons: string[];
  onUpdateQuantity: (sku: string, qty: number) => void;
  onRemoveItem: (sku: string) => void;
  onClearCart: () => void;
  onInspectJSON: () => void;
  onSubmitOrder: (orderPayload: any) => Promise<OrderDocument | null>;
  onOrderSuccess: (order: OrderDocument) => void;
}

export const CartTab: React.FC<CartTabProps> = ({
  cart,
  activePrincipalId,
  principal,
  cities,
  complaintReasons,
  onUpdateQuantity,
  onRemoveItem,
  onClearCart,
  onInspectJSON,
  onSubmitOrder,
  onOrderSuccess,
}) => {
  const [selectedCity, setSelectedCity] = useState<string>(
    principal?.defaultAddress?.city || "Amsterdam"
  );
  const [paymentMethod, setPaymentMethod] = useState<string>("INVOICE_NET30");
  const [serviceLevel, setServiceLevel] = useState<string>("NEXT_DAY_AIR");
  const [feedbackRating, setFeedbackRating] = useState<number>(
    activePrincipalId === "demo1" ? 5 : 3
  );
  const [feedbackText, setFeedbackText] = useState<string>("");
  const [complaintReason, setComplaintReason] = useState<string>(
    complaintReasons[0] || "LATE_DELIVERY"
  );
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // Financial calculations
  const subtotal = cart.reduce((acc, item) => acc + item.unitPrice * item.quantity, 0);
  const discountRate = principal?.discountRate ?? 0.0;
  const discountTotal = Math.round(subtotal * discountRate * 100) / 100;
  const taxAmount = Math.round(subtotal * 0.21 * 100) / 100;
  const shippingFee = activePrincipalId === "demo1" && subtotal > 1000 ? 0.0 : 45.0;
  const grandTotal = Math.round((subtotal - discountTotal + taxAmount + shippingFee) * 100) / 100;

  // Sentiment Preview
  const getSentimentLabel = (rating: number) => {
    if (rating >= 4) return { label: "Positive (+0.85)", color: "text-emerald-400 bg-emerald-500/10 border-emerald-500/30" };
    if (rating === 3) return { label: "Neutral (+0.10)", color: "text-amber-400 bg-amber-500/10 border-amber-500/30" };
    return { label: "Negative / Churn Risk (-0.75)", color: "text-rose-400 bg-rose-500/10 border-rose-500/30" };
  };

  const handleOrderSubmit = async () => {
    if (cart.length === 0) return;
    setIsSubmitting(true);
    setSubmitError(null);

    const cityObj = cities.find((c) => c.city === selectedCity) || {
      city: selectedCity,
      countryCode: "NL",
      postalCode: "1016 BS",
      province: "North Holland"
    };

    const payload = {
      principalId: activePrincipalId,
      items: cart.map((i) => ({
        sku: i.sku,
        quantity: i.quantity,
        name: i.name,
        category: i.category,
        unitPrice: i.unitPrice,
        allocatedWarehouse: i.allocatedWarehouse
      })),
      shippingAddress: {
        streetAddress: `Industrial Park Way ${Math.floor(Math.random() * 500) + 10}`,
        city: cityObj.city,
        province: cityObj.province,
        postalCode: cityObj.postalCode,
        countryCode: cityObj.countryCode
      },
      paymentMethod,
      serviceLevel,
      feedbackRating,
      feedbackText: feedbackText.trim() || undefined,
      complaintReason: feedbackRating <= 2 ? complaintReason : undefined
    };

    try {
      const order = await onSubmitOrder(payload);
      if (order) {
        onOrderSuccess(order);
      }
    } catch (err: any) {
      setSubmitError(err?.message || "Failed to commit order to Firestore");
    } finally {
      setIsSubmitting(false);
    }
  };

  if (cart.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center p-6 text-center space-y-4">
        <div className="w-16 h-16 rounded-full bg-slateDark-850 border border-slate-800 flex items-center justify-center text-slate-500">
          <ShoppingBag className="w-8 h-8" />
        </div>
        <div>
          <h3 className="text-base font-bold text-white mb-1">Your Cart is Empty</h3>
          <p className="text-xs text-slate-400 max-w-xs font-mono">
            Browse our industrial catalog and add optical sensors, gateways, or PLCs to place a test order.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col overflow-y-auto px-4 py-3 space-y-3 pb-28">
      {/* Active User Tier Benefit Card */}
      <div className="bg-gradient-to-r from-slateDark-850 via-slateDark-800 to-slateDark-850 border border-slate-700/60 rounded-2xl p-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg bg-brand-500/20 border border-brand-500/40 flex items-center justify-center text-brand-400 font-bold text-xs font-mono">
            {Math.round(discountRate * 100)}%
          </div>
          <div>
            <p className="text-xs font-bold text-slate-200">
              {principal?.displayName} ({activePrincipalId})
            </p>
            <p className="text-[10px] text-amber-400/90 font-mono">
              Tier: {principal?.loyaltyTier} • {Math.round(discountRate * 100)}% Auto Discount
            </p>
          </div>
        </div>
        <button
          onClick={onClearCart}
          className="text-xs text-slate-400 hover:text-rose-400 p-1 font-mono flex items-center gap-1"
        >
          <Trash2 className="w-3.5 h-3.5" />
          <span>Clear</span>
        </button>
      </div>

      {/* Cart Items List */}
      <div className="space-y-2">
        <span className="text-xs font-mono text-slate-400 px-1">
          Order Line Items ({cart.reduce((a, b) => a + b.quantity, 0)} units)
        </span>

        {cart.map((item) => (
          <div
            key={item.sku}
            className="bg-slateDark-850 border border-slate-800 rounded-xl p-3 flex items-center justify-between gap-3 shadow-sm"
          >
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-0.5">
                <span className="text-[10px] font-mono text-brand-400 bg-brand-500/10 px-1.5 py-0.2 rounded border border-brand-500/20">
                  {item.sku}
                </span>
                <span className="text-[10px] font-mono text-slate-500 truncate">
                  {item.category}
                </span>
              </div>
              <h4 className="text-xs font-semibold text-white truncate">{item.name}</h4>
              <p className="text-xs font-mono font-bold text-slate-300 mt-1">
                €{(item.unitPrice * item.quantity).toFixed(2)}{" "}
                <span className="text-[10px] font-normal text-slate-500">
                  (€{item.unitPrice.toFixed(2)} ea)
                </span>
              </p>
            </div>

            {/* Stepper */}
            <div className="flex items-center gap-1.5 bg-slate-900 border border-slate-700/80 rounded-lg p-1">
              <button
                onClick={() =>
                  item.quantity > 1
                    ? onUpdateQuantity(item.sku, item.quantity - 1)
                    : onRemoveItem(item.sku)
                }
                className="w-6 h-6 rounded flex items-center justify-center text-slate-400 hover:text-white hover:bg-slate-800 transition-all"
              >
                <Minus className="w-3 h-3" />
              </button>
              <span className="w-6 text-center text-xs font-mono font-bold text-white">
                {item.quantity}
              </span>
              <button
                onClick={() => onUpdateQuantity(item.sku, item.quantity + 1)}
                className="w-6 h-6 rounded flex items-center justify-center text-slate-400 hover:text-white hover:bg-slate-800 transition-all"
              >
                <Plus className="w-3 h-3" />
              </button>
            </div>
          </div>
        ))}
      </div>

      {/* Logistics & Delivery Options */}
      <div className="bg-slateDark-850 border border-slate-800/90 rounded-2xl p-3.5 space-y-3">
        <h4 className="text-xs font-bold text-slate-200 flex items-center gap-1.5">
          <Truck className="w-3.5 h-3.5 text-brand-400" />
          <span>Logistics & Delivery Destination</span>
        </h4>

        <div className="grid grid-cols-2 gap-2 text-xs font-mono">
          <div>
            <label className="block text-[10px] text-slate-400 mb-1">European City:</label>
            <select
              value={selectedCity}
              onChange={(e) => setSelectedCity(e.target.value)}
              className="w-full bg-slate-900 border border-slate-700 rounded-lg px-2 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-brand-500"
            >
              {cities.map((c) => (
                <option key={c.city} value={c.city}>
                  {c.city} ({c.countryCode})
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-[10px] text-slate-400 mb-1">Service Level:</label>
            <select
              value={serviceLevel}
              onChange={(e) => setServiceLevel(e.target.value)}
              className="w-full bg-slate-900 border border-slate-700 rounded-lg px-2 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-brand-500"
            >
              <option value="NEXT_DAY_AIR">Next-Day Air</option>
              <option value="EXPRESS_PARCEL">Express Parcel</option>
              <option value="STANDARD_FREIGHT">Standard Freight</option>
            </select>
          </div>
        </div>

        <div>
          <label className="block text-[10px] text-slate-400 mb-1 font-mono">Payment Method:</label>
          <div className="grid grid-cols-2 gap-1.5 font-mono text-[11px]">
            {["INVOICE_NET30", "CREDIT_CARD", "IDEAL", "SEPA_DIRECT_DEBIT"].map((method) => (
              <button
                key={method}
                onClick={() => setPaymentMethod(method)}
                className={`py-1.5 px-2 rounded-lg border text-left truncate transition-all ${
                  paymentMethod === method
                    ? "bg-brand-600/20 text-brand-300 border-brand-500/60 font-semibold"
                    : "bg-slate-900 text-slate-400 border-slate-800 hover:border-slate-700"
                }`}
              >
                {method.replace(/_/g, " ")}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Customer Satisfaction & Sentiment Feedback Section */}
      <div className="bg-slateDark-850 border border-slate-800/90 rounded-2xl p-3.5 space-y-3">
        <div className="flex items-center justify-between">
          <h4 className="text-xs font-bold text-slate-200 flex items-center gap-1.5">
            <Star className="w-3.5 h-3.5 text-amber-400 fill-amber-400" />
            <span>Customer Rating & Sentiment</span>
          </h4>
          <span
            className={`text-[10px] font-mono px-2 py-0.5 rounded-full border ${
              getSentimentLabel(feedbackRating).color
            }`}
          >
            {getSentimentLabel(feedbackRating).label}
          </span>
        </div>

        {/* 5-Star Selector */}
        <div className="flex items-center justify-center gap-3 py-1 bg-slate-900/80 rounded-xl border border-slate-800">
          {[1, 2, 3, 4, 5].map((star) => (
            <button
              key={star}
              onClick={() => setFeedbackRating(star)}
              className="p-1 transition-transform hover:scale-110 active:scale-95"
            >
              <Star
                className={`w-6 h-6 transition-colors ${
                  star <= feedbackRating
                    ? "text-amber-400 fill-amber-400 drop-shadow-[0_0_8px_rgba(251,191,36,0.5)]"
                    : "text-slate-700 hover:text-slate-500"
                }`}
              />
            </button>
          ))}
        </div>

        {/* Low Rating Alert & Complaint Reason */}
        {feedbackRating <= 2 && (
          <div className="bg-rose-950/40 border border-rose-800/50 rounded-xl p-3 space-y-2">
            <div className="flex items-center gap-1.5 text-rose-300 text-xs font-semibold">
              <AlertTriangle className="w-4 h-4 text-rose-400" />
              <span>Negative Sentiment Logged (Active Complaint)</span>
            </div>
            <p className="text-[10px] text-rose-300/80 font-mono">
              Triggers ML churn prediction alert in BigQuery CDC feature view.
            </p>
            <div>
              <label className="block text-[10px] text-slate-300 mb-1 font-mono">
                Primary Complaint Reason:
              </label>
              <select
                value={complaintReason}
                onChange={(e) => setComplaintReason(e.target.value)}
                className="w-full bg-slate-900 border border-rose-800/60 rounded-lg px-2 py-1.5 text-xs text-rose-200 focus:outline-none focus:border-rose-500 font-mono"
              >
                {complaintReasons.map((reason) => (
                  <option key={reason} value={reason}>
                    {reason.replace(/_/g, " ")}
                  </option>
                ))}
              </select>
            </div>
          </div>
        )}

        {/* Feedback Textarea */}
        <div>
          <input
            type="text"
            value={feedbackText}
            onChange={(e) => setFeedbackText(e.target.value)}
            placeholder="Optional order comment or notes..."
            className="w-full bg-slate-900 border border-slate-700/80 rounded-lg px-3 py-2 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-brand-500 font-mono"
          />
        </div>
      </div>

      {/* Transparent Financial Ledger */}
      <div className="bg-slateDark-850 border border-slate-800 rounded-2xl p-3.5 space-y-2 text-xs font-mono">
        <div className="flex justify-between text-slate-400">
          <span>Subtotal</span>
          <span>€{subtotal.toFixed(2)}</span>
        </div>
        {discountTotal > 0 && (
          <div className="flex justify-between text-emerald-400">
            <span>Loyalty Tier Discount ({Math.round(discountRate * 100)}%)</span>
            <span>-€{discountTotal.toFixed(2)}</span>
          </div>
        )}
        <div className="flex justify-between text-slate-400">
          <span>VAT (21% EU Rate)</span>
          <span>€{taxAmount.toFixed(2)}</span>
        </div>
        <div className="flex justify-between text-slate-400">
          <span>Shipping Fee</span>
          <span>{shippingFee === 0 ? "FREE" : `€${shippingFee.toFixed(2)}`}</span>
        </div>
        <div className="pt-2 border-t border-slate-700/80 flex justify-between items-baseline text-white font-bold text-sm">
          <span>Grand Total</span>
          <span className="text-base text-brand-400 font-extrabold">
            €{grandTotal.toFixed(2)}
          </span>
        </div>
      </div>

      {submitError && (
        <div className="bg-rose-950/60 border border-rose-800 rounded-xl p-3 text-xs text-rose-200 font-mono">
          {submitError}
        </div>
      )}

      {/* Actions */}
      <div className="space-y-2 pt-1">
        <button
          onClick={handleOrderSubmit}
          disabled={isSubmitting}
          className="w-full py-3.5 px-4 bg-gradient-to-r from-brand-600 via-amber-600 to-brand-600 hover:from-brand-500 hover:to-amber-500 text-white rounded-xl font-bold text-sm shadow-xl shadow-brand-600/30 flex items-center justify-center gap-2 active:scale-[0.98] transition-all disabled:opacity-50"
        >
          {isSubmitting ? (
            <div className="flex items-center gap-2">
              <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
              <span>Committing to Firestore...</span>
            </div>
          ) : (
            <>
              <span>Place Order as {activePrincipalId}</span>
              <ArrowRight className="w-4 h-4" />
            </>
          )}
        </button>

        <button
          onClick={onInspectJSON}
          className="w-full py-2 px-3 rounded-xl bg-slateDark-850 hover:bg-slate-800 text-slate-400 hover:text-slate-200 text-xs font-mono border border-slate-800 flex items-center justify-center gap-1.5 transition-colors"
        >
          <FileCode className="w-3.5 h-3.5 text-brand-400" />
          <span>Inspect Generated JSON Schema</span>
        </button>
      </div>
    </div>
  );
};
