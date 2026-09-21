import React, { useState, useEffect } from "react";
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
import { JSONInspectorModal } from "./components/JSONInspectorModal";
import {
  CatalogItem,
  CartItem,
  PrincipalProfile,
  ShippingCity,
  OrderDocument
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

  // Inspector Modal State
  const [inspectorOrder, setInspectorOrder] = useState<OrderDocument | null>(null);
  const [inspectorTitle, setInspectorTitle] = useState<string>("Firestore Order JSON Document");

  // Notification Toast
  const [toastMessage, setToastMessage] = useState<string | null>(null);

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

        // Load initial orders
        loadOrders();
      } catch (err) {
        console.error("Failed to load initial data:", err);
      }
    };

    fetchData();
  }, []);

  const loadOrders = async () => {
    setIsLoadingOrders(true);
    try {
      const res = await fetch("/api/orders?limit=40");
      if (res.ok) {
        const data = await res.json();
        setOrders(data.orders || []);
      }
    } catch (err) {
      console.error("Failed to load orders:", err);
    } finally {
      setIsLoadingOrders(false);
    }
  };

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
    showToast(`Order ${newOrder.orderId} committed to Firestore!`);
  };

  // Inspect JSON Preview
  const handleInspectCartJSON = async () => {
    if (cart.length === 0) return;
    try {
      const cityObj = cities.find((c) => c.city === profiles[activePrincipalId]?.defaultAddress?.city) || cities[0];
      const previewPayload = {
        principalId: activePrincipalId,
        items: cart,
        shippingAddress: cityObj,
        paymentMethod: "INVOICE_NET30",
        serviceLevel: "NEXT_DAY_AIR",
        feedbackRating: activePrincipalId === "demo1" ? 5 : 3
      };

      const res = await fetch("/api/orders/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(previewPayload)
      });

      if (res.ok) {
        const data = await res.json();
        setInspectorTitle("Cart Preview JSON Document");
        setInspectorOrder(data.order);
      }
    } catch (e) {
      console.error("Failed to generate preview:", e);
    }
  };

  const handleInspectOrderJSON = (order: OrderDocument) => {
    setInspectorTitle(`Firestore Document: ${order.orderId}`);
    setInspectorOrder(order);
  };

  return (
    <DeviceFrame>
      <div className="w-full h-full flex flex-col bg-slateDark-900 overflow-hidden relative font-sans">
        {/* Top Header with IAM Principal Switcher */}
        <Header
          activePrincipalId={activePrincipalId}
          onSelectPrincipal={(id) => {
            setActivePrincipalId(id);
            showToast(`Switched active user to IAM Principal: ${id}`);
          }}
          profiles={profiles}
          firestoreConnected={firestoreConnected}
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
              cities={cities}
              complaintReasons={complaintReasons}
              onUpdateQuantity={handleUpdateQuantity}
              onRemoveItem={handleRemoveItem}
              onClearCart={handleClearCart}
              onInspectJSON={handleInspectCartJSON}
              onSubmitOrder={handleSubmitOrder}
              onOrderSuccess={handleOrderSuccess}
            />
          )}

          {activeTab === "orders" && (
            <OrdersTab
              orders={orders}
              activePrincipalId={activePrincipalId}
              onRefresh={loadOrders}
              isLoading={isLoadingOrders}
              onInspectOrderJSON={handleInspectOrderJSON}
            />
          )}

          {activeTab === "profile" && (
            <ProfileTab
              activePrincipalId={activePrincipalId}
              principal={profiles[activePrincipalId]}
              onSwitchPrincipal={(id) => {
                setActivePrincipalId(id);
                showToast(`Switched active user to: ${id}`);
              }}
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

        {/* Live JSON Inspector Modal */}
        {inspectorOrder && (
          <JSONInspectorModal
            order={inspectorOrder}
            title={inspectorTitle}
            onClose={() => setInspectorOrder(null)}
          />
        )}
      </div>
    </DeviceFrame>
  );
};
