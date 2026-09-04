import React, { useState } from "react";
import {
  Search,
  Plus,
  Check,
  Cpu,
  Radio,
  Zap,
  Server,
  Activity,
  Sliders,
  Warehouse
} from "lucide-react";
import { CatalogItem } from "../types/retail";

interface CatalogTabProps {
  catalog: CatalogItem[];
  categories: string[];
  warehouses: string[];
  onAddToCart: (item: CatalogItem, qty?: number) => void;
  cartCountBySku: Record<string, number>;
}

export const CatalogTab: React.FC<CatalogTabProps> = ({
  catalog,
  categories,
  onAddToCart,
  cartCountBySku,
}) => {
  const [selectedCategory, setSelectedCategory] = useState<string>("All");
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [activeItemModal, setActiveItemModal] = useState<CatalogItem | null>(null);
  const [recentlyAddedSku, setRecentlyAddedSku] = useState<string | null>(null);

  const filteredItems = catalog.filter((item) => {
    const matchesCategory =
      selectedCategory === "All" || item.category === selectedCategory;
    const matchesSearch =
      item.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      item.sku.toLowerCase().includes(searchQuery.toLowerCase()) ||
      item.category.toLowerCase().includes(searchQuery.toLowerCase());
    return matchesCategory && matchesSearch;
  });

  const getCategoryIcon = (category: string) => {
    switch (category) {
      case "Sensors":
        return <Activity className="w-3.5 h-3.5" />;
      case "Networking":
        return <Radio className="w-3.5 h-3.5" />;
      case "Edge Computing":
        return <Server className="w-3.5 h-3.5" />;
      case "Automation":
        return <Cpu className="w-3.5 h-3.5" />;
      case "Power":
      case "Cooling":
        return <Zap className="w-3.5 h-3.5" />;
      default:
        return <Sliders className="w-3.5 h-3.5" />;
    }
  };

  const getCategoryBadgeColor = (category: string) => {
    switch (category) {
      case "Sensors":
        return "text-emerald-400 bg-emerald-500/15 border-emerald-500/30";
      case "Networking":
        return "text-cyan-400 bg-cyan-500/15 border-cyan-500/30";
      case "Edge Computing":
        return "text-indigo-400 bg-indigo-500/15 border-indigo-500/30";
      case "Automation":
        return "text-amber-400 bg-amber-500/15 border-amber-500/30";
      case "Power":
        return "text-orange-400 bg-orange-500/15 border-orange-500/30";
      default:
        return "text-slate-400 bg-slate-500/15 border-slate-500/30";
    }
  };

  const handleAdd = (item: CatalogItem, e?: React.MouseEvent) => {
    if (e) e.stopPropagation();
    onAddToCart(item, 1);
    setRecentlyAddedSku(item.sku);
    setTimeout(() => setRecentlyAddedSku(null), 1200);
  };

  return (
    <div className="flex-1 flex flex-col overflow-y-auto px-4 py-3 space-y-3 pb-24">
      {/* Search Bar */}
      <div className="relative">
        <Search className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="Search by SKU, sensor, gateway, PLC..."
          className="w-full pl-9 pr-3 py-2 bg-slateDark-850 border border-slate-700/60 rounded-xl text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-brand-500/50 focus:border-brand-500/80 transition-all font-mono"
        />
        {searchQuery && (
          <button
            onClick={() => setSearchQuery("")}
            className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-slate-400 hover:text-slate-200"
          >
            Clear
          </button>
        )}
      </div>

      {/* Category Pills Slider */}
      <div className="flex items-center gap-1.5 overflow-x-auto no-scrollbar py-0.5 -mx-4 px-4">
        {["All", ...categories].map((cat) => (
          <button
            key={cat}
            onClick={() => setSelectedCategory(cat)}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs whitespace-nowrap transition-all font-medium ${
              selectedCategory === cat
                ? "bg-brand-600 text-white shadow-md shadow-brand-600/30 border border-brand-500/60"
                : "bg-slateDark-850 text-slate-400 hover:text-slate-200 border border-slate-800"
            }`}
          >
            {cat !== "All" && getCategoryIcon(cat)}
            <span>{cat}</span>
          </button>
        ))}
      </div>

      {/* Catalog List */}
      <div className="space-y-2.5">
        <div className="flex items-center justify-between text-xs text-slate-400 px-1 font-mono">
          <span>
            {filteredItems.length} {filteredItems.length === 1 ? "Product" : "Products"} Available
          </span>
          <span className="text-[10px] text-slate-500">EU Certified • 24/7 Logistics</span>
        </div>

        {filteredItems.map((item) => {
          const countInCart = cartCountBySku[item.sku] || 0;
          const isRecentlyAdded = recentlyAddedSku === item.sku;

          return (
            <div
              key={item.sku}
              onClick={() => setActiveItemModal(item)}
              className="group bg-gradient-to-b from-slateDark-850 to-slateDark-900 border border-slate-800/90 hover:border-slate-700/80 rounded-2xl p-3.5 transition-all shadow-sm active:scale-[0.99] cursor-pointer"
            >
              <div className="flex items-start justify-between gap-2 mb-1.5">
                <span
                  className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-mono border font-semibold ${getCategoryBadgeColor(
                    item.category
                  )}`}
                >
                  {getCategoryIcon(item.category)}
                  <span>{item.category}</span>
                </span>
                <span className="text-[10px] font-mono text-slate-400 bg-slate-800/80 px-1.5 py-0.5 rounded border border-slate-700/50">
                  {item.sku}
                </span>
              </div>

              <h3 className="text-sm font-semibold text-slate-100 group-hover:text-brand-300 transition-colors leading-tight mb-1">
                {item.name}
              </h3>

              <div className="flex items-center gap-3 text-[11px] text-slate-400 font-mono mb-3">
                <span className="flex items-center gap-1 text-emerald-400/90">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
                  In Stock
                </span>
                <span>•</span>
                <span className="flex items-center gap-1 text-slate-400">
                  <Warehouse className="w-3 h-3 text-slate-500" />
                  Rotterdam / Frankfurt Hub
                </span>
              </div>

              <div className="flex items-center justify-between pt-2 border-t border-slate-800/60">
                <div>
                  <span className="text-base font-bold text-white font-mono">
                    €{item.unitPrice.toLocaleString("en-US", { minimumFractionDigits: 2 })}
                  </span>
                  <span className="text-[10px] text-slate-400 ml-1">/ unit excl. VAT</span>
                </div>

                <div className="flex items-center gap-2">
                  {countInCart > 0 && (
                    <span className="text-[11px] font-mono px-2 py-1 rounded-lg bg-brand-500/20 text-brand-400 border border-brand-500/40">
                      {countInCart} in cart
                    </span>
                  )}

                  <button
                    onClick={(e) => handleAdd(item, e)}
                    className={`flex items-center gap-1 px-3 py-1.5 rounded-xl text-xs font-semibold transition-all ${
                      isRecentlyAdded
                        ? "bg-emerald-600 text-white shadow-md shadow-emerald-600/30"
                        : "bg-brand-600 hover:bg-brand-500 text-white shadow-md shadow-brand-600/20"
                    }`}
                  >
                    {isRecentlyAdded ? (
                      <>
                        <Check className="w-3.5 h-3.5" />
                        <span>Added!</span>
                      </>
                    ) : (
                      <>
                        <Plus className="w-3.5 h-3.5" />
                        <span>Add</span>
                      </>
                    )}
                  </button>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Product Detail Modal */}
      {activeItemModal && (
        <div
          className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-end sm:items-center justify-center p-0 sm:p-4"
          onClick={() => setActiveItemModal(null)}
        >
          <div
            className="w-full max-w-sm bg-slateDark-900 border-t sm:border border-slate-700 rounded-t-3xl sm:rounded-3xl p-5 space-y-4 max-h-[85vh] overflow-y-auto"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <span
                className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-mono font-semibold border ${getCategoryBadgeColor(
                  activeItemModal.category
                )}`}
              >
                {getCategoryIcon(activeItemModal.category)}
                <span>{activeItemModal.category}</span>
              </span>
              <span className="text-xs font-mono text-slate-400 bg-slate-800 px-2 py-0.5 rounded border border-slate-700">
                {activeItemModal.sku}
              </span>
            </div>

            <div>
              <h2 className="text-lg font-bold text-white mb-1">{activeItemModal.name}</h2>
              <p className="text-xs text-slate-400 font-mono">
                Industrial-grade hardware with BigQuery CDC telemetry integration.
              </p>
            </div>

            <div className="bg-slateDark-850 border border-slate-800 rounded-xl p-3 space-y-2 text-xs font-mono">
              <div className="flex justify-between text-slate-300">
                <span className="text-slate-500">Unit Catalog Price:</span>
                <span className="font-bold text-white">
                  €{activeItemModal.unitPrice.toFixed(2)}
                </span>
              </div>
              <div className="flex justify-between text-slate-300">
                <span className="text-slate-500">Estimated Production Cost:</span>
                <span>€{activeItemModal.cost.toFixed(2)}</span>
              </div>
              <div className="flex justify-between text-slate-300">
                <span className="text-slate-500">Target Profit Margin:</span>
                <span className="text-emerald-400">
                  {(
                    ((activeItemModal.unitPrice - activeItemModal.cost) /
                      activeItemModal.unitPrice) *
                    100
                  ).toFixed(1)}
                  %
                </span>
              </div>
            </div>

            <div className="flex items-center gap-2 pt-2">
              <button
                onClick={() => setActiveItemModal(null)}
                className="flex-1 py-2.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-semibold"
              >
                Close
              </button>
              <button
                onClick={() => {
                  handleAdd(activeItemModal);
                  setActiveItemModal(null);
                }}
                className="flex-1 py-2.5 rounded-xl bg-brand-600 hover:bg-brand-500 text-white text-xs font-semibold shadow-lg shadow-brand-600/30 flex items-center justify-center gap-1.5"
              >
                <Plus className="w-4 h-4" />
                <span>Add to Cart</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
