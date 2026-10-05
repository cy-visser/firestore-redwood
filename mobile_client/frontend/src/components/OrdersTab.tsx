import React, { useState } from "react";
import {
  Package,
  Clock,
  CheckCircle2,
  Truck,
  Star,
  ChevronDown,
  ChevronUp,
  Gift,
  RefreshCw
} from "lucide-react";
import { OrderDocument } from "../types/retail";

interface OrdersTabProps {
  /**
   * Orders for the signed-in customer, newest first. The backend already
   * scoped and sorted this, so there is nothing to filter here.
   */
  orders: OrderDocument[];
  activePrincipalId: string;
  /** The customer the feed was fetched for, shown so the scope is visible. */
  customerId: string;
  onRefresh: () => void;
  isLoading: boolean;
}

export const OrdersTab: React.FC<OrdersTabProps> = ({
  orders,
  activePrincipalId,
  customerId,
  onRefresh,
  isLoading,
}) => {
  const [expandedOrderId, setExpandedOrderId] = useState<string | null>(null);

  const getStatusBadge = (status: string) => {
    switch (status) {
      case "DELIVERED":
        return {
          label: "Delivered",
          icon: <CheckCircle2 className="w-3 h-3" />,
          color: "text-emerald-400 bg-emerald-500/10 border-emerald-500/30"
        };
      case "SHIPPED":
        return {
          label: "Shipped",
          icon: <Truck className="w-3 h-3" />,
          color: "text-cyan-400 bg-cyan-500/10 border-cyan-500/30"
        };
      case "PROCESSING":
      default:
        return {
          label: "Processing",
          icon: <Clock className="w-3 h-3" />,
          color: "text-amber-400 bg-amber-500/10 border-amber-500/30"
        };
    }
  };

  return (
    <div className="flex-1 flex flex-col overflow-y-auto px-4 py-3 space-y-3 pb-28">
      {/* Header and Refresh */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-bold text-white flex items-center gap-1.5">
            <Package className="w-4 h-4 text-brand-400" />
            <span>Firestore Order Feed</span>
          </h2>
          <p className="text-[10px] text-slate-400 font-mono">
            Live stream from Firestore Native collection <span className="text-brand-400 font-bold">retail</span>
            {" · "}
            <span className="text-slate-300">{customerId}</span>
          </p>
        </div>

        <button
          onClick={onRefresh}
          disabled={isLoading}
          className="p-1.5 rounded-lg bg-slate-800 text-slate-300 hover:text-white border border-slate-700 hover:border-slate-600 transition-all disabled:opacity-50"
          title="Refresh Firestore Orders"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? "animate-spin text-brand-400" : ""}`} />
        </button>
      </div>

      {/* Orders List */}
      {orders.length === 0 ? (
        <div className="bg-slateDark-850 border border-slate-800 rounded-2xl p-6 text-center space-y-2">
          <Package className="w-8 h-8 text-slate-600 mx-auto" />
          <p className="text-xs text-slate-300 font-semibold">
            No orders yet for {activePrincipalId}
          </p>
          <p className="text-[11px] text-slate-500 font-mono">
            Place an order from the cart to see it stream here in real-time.
          </p>
        </div>
      ) : (
        <div className="space-y-2.5">
          {orders.map((order) => {
            const isExpanded = expandedOrderId === order.orderId;
            const status = getStatusBadge(order.orderStatus);
            const formattedDate = new Date(order.createdAt).toLocaleTimeString([], {
              hour: "2-digit",
              minute: "2-digit",
              second: "2-digit"
            });

            return (
              <div
                key={order.orderId}
                className="bg-slateDark-850 border border-slate-800 rounded-2xl p-3.5 shadow-sm space-y-2 transition-all hover:border-slate-700"
              >
                {/* Order Top Bar */}
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5">
                    <span className="text-[10px] font-mono text-brand-400 bg-brand-500/10 px-2 py-0.5 rounded border border-brand-500/20 font-bold">
                      {order.orderId.split("-").slice(0, 3).join("-")}...
                    </span>
                    <span
                      className={`inline-flex items-center gap-1 text-[10px] font-mono px-2 py-0.5 rounded-full border ${status.color}`}
                    >
                      {status.icon}
                      <span>{status.label}</span>
                    </span>
                  </div>

                  <span className="text-[10px] font-mono text-slate-500">{formattedDate}</span>
                </div>

                {/* Customer and Amount */}
                <div className="flex items-baseline justify-between pt-1">
                  <div>
                    <h4 className="text-xs font-semibold text-white">
                      {order.customerName}
                    </h4>
                    <p className="text-[10px] text-slate-400 font-mono">
                      {order.shippingAddress?.city}, {order.shippingAddress?.countryCode} •{" "}
                      {order.lineItems?.length || 0} items
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="text-sm font-bold text-white font-mono">
                      €{order.financials?.grandTotal?.toFixed(2)}
                    </p>
                    <p className="text-[9px] text-emerald-400 font-mono">
                      Margin: {((order.financials?.profitMargin || 0) * 100).toFixed(1)}%
                    </p>
                  </div>
                </div>

                {/* Offer provenance. This is the whole point of the demo: the
                    discount on this order came from a named agent offer, and
                    the order document says so. */}
                {order.loyaltyOffer?.offerApplied && (
                  <div className="flex items-center gap-1.5 text-[10px] font-mono text-emerald-300 bg-emerald-500/10 border border-emerald-500/25 rounded-lg px-2 py-1">
                    <Gift className="w-3 h-3 shrink-0" />
                    <span className="truncate">
                      {order.loyaltyOffer.discountPercent}% agent offer
                      {order.loyaltyOffer.promoCode
                        ? ` · ${order.loyaltyOffer.promoCode}`
                        : ""}
                    </span>
                  </div>
                )}

                {/* Feedback pill */}
                <div className="flex items-center justify-between bg-slate-900/80 rounded-xl px-2.5 py-1.5 text-[11px] font-mono border border-slate-800/80">
                  <div className="flex items-center gap-1">
                    {[1, 2, 3, 4, 5].map((s) => (
                      <Star
                        key={s}
                        className={`w-3 h-3 ${
                          s <= (order.customerFeedback?.rating || 5)
                            ? "text-amber-400 fill-amber-400"
                            : "text-slate-700"
                        }`}
                      />
                    ))}
                    <span className="text-slate-400 ml-1 text-[10px]">
                      (Sent: {order.customerFeedback?.sentimentScore?.toFixed(2)})
                    </span>
                  </div>

                  {order.supportMetrics?.hasActiveComplaint && (
                    <span className="text-[9px] text-rose-400 font-bold bg-rose-500/10 px-1.5 py-0.2 rounded border border-rose-500/20">
                      COMPLAINT LOGGED
                    </span>
                  )}
                </div>

                {/* Expansion Details */}
                {isExpanded && (
                  <div className="pt-2 border-t border-slate-800 space-y-2 text-xs font-mono">
                    <div className="space-y-1 bg-slate-900/60 p-2.5 rounded-xl border border-slate-800">
                      <p className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">
                        Line Items Breakdown
                      </p>
                      {order.lineItems?.map((li) => (
                        <div key={li.sku} className="flex justify-between text-slate-300 text-[11px]">
                          <span>
                            {li.quantity}x {li.name}
                          </span>
                          <span className="font-bold">€{li.totalPrice.toFixed(2)}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Toggle Expand */}
                <button
                  onClick={() => setExpandedOrderId(isExpanded ? null : order.orderId)}
                  className="w-full text-center text-[10px] font-mono text-slate-500 hover:text-slate-300 pt-1 flex items-center justify-center gap-1"
                >
                  <span>{isExpanded ? "Hide Details" : "View Line Items & Telemetry"}</span>
                  {isExpanded ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
