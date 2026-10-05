import React, { useState, useEffect, useRef, useCallback } from "react";
import {
  ShoppingBag,
  Store,
  Package,
  User,
  CheckCircle2
} from "lucide-react";
import { DeviceFrame } from "./components/DeviceFrame";
import { Header } from "./components/Header";
import { CatalogTab } from "./components/CatalogTab";
import { CartTab } from "./components/CartTab";
import { OrdersTab } from "./components/OrdersTab";
import { ProfileTab } from "./components/ProfileTab";
import { LoginScreen } from "./components/LoginScreen";
import { OfferSheet } from "./components/OfferSheet";
import { useEventStream } from "./hooks/useEventStream";
import {
  CatalogItem,
  CartItem,
  PrincipalProfile,
  ShippingCity,
  OrderDocument,
  CustomerSession,
  LoyaltyOffer,
  SnapshotEvent,
  TERMINAL_STATUSES
} from "./types/retail";

export const App: React.FC = () => {
  const [activeTab, setActiveTab] = useState<"catalog" | "cart" | "orders" | "profile">("catalog");
  const [activePrincipalId, setActivePrincipalId] = useState<string>("demo1");

  const [catalog, setCatalog] = useState<CatalogItem[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [warehouses, setWarehouses] = useState<string[]>([]);
  const [cities, setCities] = useState<ShippingCity[]>([]);
  const [complaintReasons, setComplaintReasons] = useState<string[]>([]);
  const [profiles, setProfiles] = useState<Record<string, PrincipalProfile>>({});
  const [cart, setCart] = useState<CartItem[]>([]);
  const [orders, setOrders] = useState<OrderDocument[]>([]);
  const [firestoreConnected, setFirestoreConnected] = useState<boolean>(false);
  const [isLoadingOrders, setIsLoadingOrders] = useState<boolean>(false);

  // Login session and the agent's verdict on it.
  const [session, setSession] = useState<CustomerSession | null>(null);
  const [offer, setOffer] = useState<LoyaltyOffer | null>(null);
  const [offerSheetOpen, setOfferSheetOpen] = useState<boolean>(false);
  const [loginError, setLoginError] = useState<string | null>(null);

  // Notification Toast
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  // Browser-clock stopwatch for the login. Held in refs because these are
  // measurements, not state: nothing renders from them, and turning them into
  // state would re-render the app on every tick of the thing being measured.
  const loginClickAt = useRef<number | null>(null);
  const outcomeReported = useRef<boolean>(false);

  const showToast = (msg: string) => {
    setToastMessage(msg);
    setTimeout(() => setToastMessage(null), 3500);
  };

  // Fetch initial metadata and catalog
  useEffect(() => {
    const fetchData = async () => {
      try {
        // Health
        const healthRes = await fetch("/api/health");
        if (healthRes.ok) {
          const healthData = await healthRes.json();
          setFirestoreConnected(healthData.firestoreConnection === "connected");
        }

        // Principals
        const princRes = await fetch("/api/principals");
        if (princRes.ok) {
          const princData = await princRes.json();
          setProfiles(princData.profiles || {});
        }

        // Catalog
        const catRes = await fetch("/api/catalog");
        if (catRes.ok) {
          const catData = await catRes.json();
          setCatalog(catData.items || []);
          setCategories(catData.categories || []);
          setWarehouses(catData.warehouses || []);
          setCities(catData.cities || []);
          setComplaintReasons(catData.complaintReasons || []);
        }
      } catch (err) {
        console.error("Failed to load initial data:", err);
      }
    };

    fetchData();
  }, []);

  // The orders feed is one customer's, filtered and ordered by Firestore. It
  // used to be fetched before login as an unfiltered page of the whole seeded
  // collection and narrowed in the browser, which is why an order placed
  // seconds earlier was usually not in it.
  const loadOrders = useCallback(async () => {
    if (!session) return;
    setIsLoadingOrders(true);
    try {
      const res = await fetch(
        `/api/orders?principalId=${encodeURIComponent(activePrincipalId)}&limit=40`
      );
      if (res.ok) {
        const data = await res.json();
        setOrders(data.orders || []);
      }
    } catch (err) {
      console.error("Failed to load orders:", err);
    } finally {
      setIsLoadingOrders(false);
    }
  }, [session, activePrincipalId]);

  useEffect(() => {
    void loadOrders();
  }, [loadOrders]);

  // ------------------------------------------------------------------
  // Login and the agent's verdict
  // ------------------------------------------------------------------

  const agentStatus = session?.agentProcessingStatus ?? null;
  const agentSettled =
    agentStatus !== null && TERMINAL_STATUSES.includes(agentStatus);

  const handleSignIn = async (principalId: string) => {
    setLoginError(null);
    outcomeReported.current = false;
    loginClickAt.current = performance.now();

    try {
      const res = await fetch("/api/session/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ principalId })
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "Login failed" }));
        throw new Error(err.detail || "Login failed");
      }

      const data = await res.json();
      const ackMs = performance.now() - (loginClickAt.current ?? 0);

      setActivePrincipalId(principalId);
      setSession(data.session as CustomerSession);
      setOffer(null);
      setActiveTab("catalog");

      // One clock, start to finish: the click and the response were both
      // timed here. Reported rather than displayed, because the console is
      // where the presenter reads it from.
      void reportTelemetry(data.sessionId, { clientAckMs: Math.round(ackMs) });

      if (data.reused) {
        showToast("Reusing the session already in flight");
      }
    } catch (e) {
      setLoginError(e instanceof Error ? e.message : "Login failed");
    }
  };

  const handleSignOut = () => {
    setSession(null);
    setOffer(null);
    setOfferSheetOpen(false);
    setLoginError(null);
    outcomeReported.current = false;
    setActiveTab("catalog");
  };

  const reportTelemetry = async (
    sessionId: string,
    measurements: Record<string, number | string>
  ) => {
    try {
      await fetch(`/api/telemetry/${sessionId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(measurements)
      });
    } catch {
      // Telemetry is decoration on the console. Losing it must never affect
      // the customer-facing flow.
    }
  };

  // Live view of this session. The handlers are recreated on every render,
  // which is why useEventStream holds them in a ref rather than in its
  // dependency list: rebuilding the connection on each event would drop the
  // changes committed while it reconnected.
  const handleSessionEvent = useCallback((payload: SnapshotEvent<CustomerSession>) => {
    if (!payload?.data) return;
    setSession((prev) => ({ ...(prev ?? {}), ...payload.data }));
  }, []);

  const handleOfferEvent = useCallback((payload: SnapshotEvent<LoyaltyOffer>) => {
    if (!payload?.data) return;
    const incoming = payload.data;
    setOffer(incoming);
    // Only an offer that has not been redeemed yet interrupts the customer.
    // A REDEEMED update arriving because they just claimed it must not
    // reopen the sheet they closed.
    if (incoming.status === "ACTIVE") {
      setOfferSheetOpen(true);
    }
  }, []);

  useEventStream(
    session ? `/api/stream/session/${session.sessionId}` : null,
    { session: handleSessionEvent, offer: handleOfferEvent }
  );

  // Report the end-to-end browser timing once, when the agent settles.
  useEffect(() => {
    if (!session || !agentSettled || outcomeReported.current) return;
    if (loginClickAt.current === null) return;

    outcomeReported.current = true;
    const visibleMs = Math.round(performance.now() - loginClickAt.current);
    void reportTelemetry(session.sessionId, {
      offerVisibleMs: visibleMs,
      outcome: session.offerId ? "OFFER" : session.skipReason || "NO_OFFER"
    });
  }, [session, agentSettled]);

  // Cart operations
  const handleAddToCart = (item: CatalogItem, qty: number = 1) => {
    setCart((prev) => {
      const existing = prev.find((i) => i.sku === item.sku);
      if (existing) {
        return prev.map((i) =>
          i.sku === item.sku ? { ...i, quantity: i.quantity + qty } : i
        );
      }
      return [
        ...prev,
        {
          sku: item.sku,
          name: item.name,
          category: item.category,
          unitPrice: item.unitPrice,
          quantity: qty,
          allocatedWarehouse: item.category === "Sensors" ? "WH-ROTTERDAM-1" : "WH-FRANKFURT-1"
        }
      ];
    });
    showToast(`Added ${item.name} to cart`);
  };

  const handleUpdateQuantity = (sku: string, qty: number) => {
    setCart((prev) =>
      prev.map((i) => (i.sku === sku ? { ...i, quantity: qty } : i))
    );
  };

  const handleRemoveItem = (sku: string) => {
    setCart((prev) => prev.filter((i) => i.sku !== sku));
  };

  const handleClearCart = () => {
    setCart([]);
  };

  // Cart count by SKU
  const cartCountBySku = cart.reduce((acc, item) => {
    acc[item.sku] = (acc[item.sku] || 0) + item.quantity;
    return acc;
  }, {} as Record<string, number>);

  const totalCartCount = cart.reduce((acc, item) => acc + item.quantity, 0);

  // Submit Order to Backend & Firestore
  const handleSubmitOrder = async (orderPayload: any): Promise<OrderDocument | null> => {
    const res = await fetch("/api/orders/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(orderPayload)
    });

    if (!res.ok) {
      const errData = await res.json().catch(() => ({ detail: "Network error" }));
      throw new Error(errData.detail || "Failed to submit order");
    }

    const data = await res.json();
    return data.order as OrderDocument;
  };

  const handleOrderSuccess = (newOrder: OrderDocument) => {
    setCart([]);
    setOrders((prev) => [newOrder, ...prev]);
    setActiveTab("orders");

    // An offer is spendable until it is attached to an order, and this order
    // just attached it. Reflecting that here stops the cart from continuing to
    // advertise a discount the next order will not get.
    if (newOrder.loyaltyOffer?.offerApplied) {
      setOffer((prev) =>
        prev && prev.offerId === newOrder.loyaltyOffer?.offerId
          ? { ...prev, status: "REDEEMED", orderId: newOrder.orderId }
          : prev
      );
      setOfferSheetOpen(false);
      showToast(
        `Order ${newOrder.orderId} placed with ${newOrder.loyaltyOffer.discountPercent}% agent offer`
      );
      return;
    }

    showToast(`Order ${newOrder.orderId} committed to Firestore!`);
  };

  const handleClaimOffer = async (offerId: string) => {
    const res = await fetch(`/api/offers/${offerId}/claim`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({})
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: "Could not claim offer" }));
      throw new Error(err.detail || "Could not claim offer");
    }

    const data = await res.json();
    setOffer(data.offer as LoyaltyOffer);
    showToast("Offer redeemed");
  };

  // The offer the cart should be priced against: the one the agent issued for
  // this session, until an order has spent it. Only the id is ever sent; the
  // backend reads the discount off the document itself.
  const spendableOffer =
    offer && !offer.orderId && offer.status !== "EXPIRED" ? offer : null;

  if (!session) {
    return (
      <DeviceFrame>
        <div className="w-full h-full flex flex-col bg-slateDark-900 overflow-hidden relative font-sans">
          <LoginScreen
            profiles={profiles}
            firestoreConnected={firestoreConnected}
            onSignIn={handleSignIn}
            error={loginError}
          />
        </div>
      </DeviceFrame>
    );
  }

  return (
    <DeviceFrame>
      <div className="w-full h-full flex flex-col bg-slateDark-900 overflow-hidden relative font-sans">
        {/* Top Header: who is signed in, and what the agent decided */}
        <Header
          activePrincipalId={activePrincipalId}
          profiles={profiles}
          session={session}
          offer={offer}
          onSignOut={handleSignOut}
          onShowOffer={offer ? () => setOfferSheetOpen(true) : undefined}
        />

        {/* Tab Content Body */}
        <div className="flex-1 flex flex-col overflow-hidden relative">
          {activeTab === "catalog" && (
            <CatalogTab
              catalog={catalog}
              categories={categories}
              warehouses={warehouses}
              onAddToCart={handleAddToCart}
              cartCountBySku={cartCountBySku}
            />
          )}

          {activeTab === "cart" && (
            <CartTab
              cart={cart}
              activePrincipalId={activePrincipalId}
              principal={profiles[activePrincipalId]}
              offer={spendableOffer}
              cities={cities}
              complaintReasons={complaintReasons}
              onUpdateQuantity={handleUpdateQuantity}
              onRemoveItem={handleRemoveItem}
              onClearCart={handleClearCart}
              onSubmitOrder={handleSubmitOrder}
              onOrderSuccess={handleOrderSuccess}
            />
          )}

          {activeTab === "orders" && (
            <OrdersTab
              orders={orders}
              activePrincipalId={activePrincipalId}
              customerId={session.customerId}
              onRefresh={loadOrders}
              isLoading={isLoadingOrders}
            />
          )}

          {activeTab === "profile" && (
            <ProfileTab
              activePrincipalId={activePrincipalId}
              principal={profiles[activePrincipalId]}
              session={session}
              offer={offer}
              onSignOut={handleSignOut}
            />
          )}
        </div>

        {/* Toast Notification */}
        {toastMessage && (
          <div className="absolute bottom-20 left-4 right-4 bg-slate-800/95 backdrop-blur border border-brand-500/50 text-white px-3.5 py-2.5 rounded-2xl shadow-2xl flex items-center gap-2 z-50 animate-bounce text-xs font-mono">
            <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
            <span className="truncate">{toastMessage}</span>
          </div>
        )}

        {/* Bottom Mobile Navigation Bar */}
        <nav className="h-16 bg-slateDark-900/95 backdrop-blur-lg border-t border-slate-800/90 px-4 flex items-center justify-around z-40">
          {/* Store / Catalog */}
          <button
            onClick={() => setActiveTab("catalog")}
            className={`flex flex-col items-center gap-1 transition-all ${
              activeTab === "catalog"
                ? "text-brand-400 font-bold scale-105"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <Store className="w-5 h-5" />
            <span className="text-[10px] tracking-tight">Catalog</span>
          </button>

          {/* Cart */}
          <button
            onClick={() => setActiveTab("cart")}
            className={`flex flex-col items-center gap-1 relative transition-all ${
              activeTab === "cart"
                ? "text-brand-400 font-bold scale-105"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <div className="relative">
              <ShoppingBag className="w-5 h-5" />
              {totalCartCount > 0 && (
                <span className="absolute -top-1.5 -right-2.5 w-4 h-4 bg-brand-600 text-white rounded-full text-[10px] font-bold flex items-center justify-center font-mono shadow-sm shadow-brand-600/50">
                  {totalCartCount}
                </span>
              )}
            </div>
            <span className="text-[10px] tracking-tight">Cart</span>
          </button>

          {/* Orders */}
          <button
            onClick={() => setActiveTab("orders")}
            className={`flex flex-col items-center gap-1 relative transition-all ${
              activeTab === "orders"
                ? "text-brand-400 font-bold scale-105"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <Package className="w-5 h-5" />
            <span className="text-[10px] tracking-tight">Orders</span>
          </button>

          {/* Profile */}
          <button
            onClick={() => setActiveTab("profile")}
            className={`flex flex-col items-center gap-1 transition-all ${
              activeTab === "profile"
                ? "text-brand-400 font-bold scale-105"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <User className="w-5 h-5" />
            <span className="text-[10px] tracking-tight">Profile</span>
          </button>
        </nav>

        {/* Retention offer, slid up over the running app */}
        {offer && offerSheetOpen && (
          <OfferSheet
            offer={offer}
            onClose={() => setOfferSheetOpen(false)}
            onClaim={handleClaimOffer}
          />
        )}
      </div>
    </DeviceFrame>
  );
};
