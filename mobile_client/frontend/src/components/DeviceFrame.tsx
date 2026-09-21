import React, { useState, useEffect } from "react";
import { Smartphone, Monitor, Wifi, BatteryCharging, Signal } from "lucide-react";

interface DeviceFrameProps {
  children: React.ReactNode;
}

export const DeviceFrame: React.FC<DeviceFrameProps> = ({ children }) => {
  const [isFrameMode, setIsFrameMode] = useState<boolean>(true);
  const [currentTime, setCurrentTime] = useState<string>("09:41");

  useEffect(() => {
    const updateClock = () => {
      const d = new Date();
      const h = String(d.getHours()).padStart(2, "0");
      const m = String(d.getMinutes()).padStart(2, "0");
      setCurrentTime(`${h}:${m}`);
    };
    updateClock();
    const timer = setInterval(updateClock, 30000);
    return () => clearInterval(timer);
  }, []);

  return (
    <div className="min-h-screen bg-slateDark-950 flex flex-col items-center justify-start text-slate-100 selection:bg-brand-500">
      {/* Top Developer & Mode Control Bar */}
      <header className="w-full bg-slateDark-900/80 backdrop-blur border-b border-slate-800 px-4 py-2 flex items-center justify-between z-50 text-xs font-mono">
        <div className="flex items-center gap-2">
          <span className="w-2.5 h-2.5 rounded-full bg-emerald-500 animate-pulse" />
          <span className="font-semibold text-slate-200">Redwood Retail Mobile</span>
          <span className="text-slate-500 hidden sm:inline">| Firestore Enterprise Native</span>
        </div>

        <div className="flex items-center gap-3">
          <div className="flex items-center bg-slate-800/80 rounded-lg p-0.5 border border-slate-700/50">
            <button
              onClick={() => setIsFrameMode(true)}
              className={`flex items-center gap-1 px-2.5 py-1 rounded-md transition-all ${
                isFrameMode
                  ? "bg-brand-600 text-white shadow-sm font-medium"
                  : "text-slate-400 hover:text-slate-200"
              }`}
              title="Preview in Mobile Phone Frame"
            >
              <Smartphone className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">Device Frame</span>
            </button>
            <button
              onClick={() => setIsFrameMode(false)}
              className={`flex items-center gap-1 px-2.5 py-1 rounded-md transition-all ${
                !isFrameMode
                  ? "bg-brand-600 text-white shadow-sm font-medium"
                  : "text-slate-400 hover:text-slate-200"
              }`}
              title="Full Screen / Direct Mobile View"
            >
              <Monitor className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">Full / PWA</span>
            </button>
          </div>
        </div>
      </header>

      {/* Main Content Area */}
      <main className="flex-1 w-full flex items-center justify-center p-0 sm:p-4 md:p-6 overflow-hidden">
        {isFrameMode ? (
          <div className="relative w-[393px] h-[852px] max-w-[100vw] max-h-[100vh] sm:max-h-[852px] bg-black rounded-none sm:rounded-[50px] p-0 sm:p-3 shadow-2xl border-0 sm:border-[10px] sm:border-slate-800 ring-1 ring-white/10 flex flex-col overflow-hidden">
            {/* Dynamic Island / Bezel Top on Mobile Frame */}
            <div className="hidden sm:flex absolute top-0 left-0 right-0 h-10 items-center justify-between px-7 pt-2 text-[11px] font-semibold text-slate-300 z-50 pointer-events-none">
              <span>{currentTime}</span>
              {/* Dynamic Island Pill */}
              <div className="w-24 h-6 bg-black rounded-full border border-slate-800/80 flex items-center justify-center gap-1 shadow-inner">
                <span className="w-2 h-2 rounded-full bg-slate-900 border border-slate-700" />
                <span className="w-2.5 h-2.5 rounded-full bg-slate-900 border border-brand-500/40" />
              </div>
              <div className="flex items-center gap-1.5">
                <Signal className="w-3 h-3 text-slate-300" />
                <Wifi className="w-3 h-3 text-slate-300" />
                <BatteryCharging className="w-3.5 h-3.5 text-emerald-400" />
              </div>
            </div>

            {/* Internal Mobile Viewport */}
            <div className="flex-1 w-full h-full bg-slateDark-900 rounded-none sm:rounded-[38px] flex flex-col overflow-hidden relative">
              {children}
            </div>

            {/* Bottom Home Indicator on Mobile Frame */}
            <div className="hidden sm:block absolute bottom-1 left-1/2 -translate-x-1/2 w-32 h-1 bg-slate-600 rounded-full pointer-events-none" />
          </div>
        ) : (
          /* Full Viewport / Native PWA Mode */
          <div className="w-full max-w-md h-[100vh] bg-slateDark-900 flex flex-col overflow-hidden relative shadow-xl border-x border-slate-800">
            {children}
          </div>
        )}
      </main>
    </div>
  );
};
