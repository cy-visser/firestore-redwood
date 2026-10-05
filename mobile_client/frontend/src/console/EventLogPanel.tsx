import React, { useEffect, useRef } from "react";
import { Terminal } from "lucide-react";
import { ConsoleLogEntry } from "../types/retail";

interface EventLogPanelProps {
  entries: ConsoleLogEntry[];
  /** Cap applied upstream, shown so the operator knows the list is bounded. */
  capacity: number;
}

const KIND_STYLES: Record<ConsoleLogEntry["kind"], string> = {
  session: "text-sky-300 border-sky-500/40",
  offer: "text-brand-300 border-brand-500/40",
  telemetry: "text-violet-300 border-violet-500/40",
  control: "text-amber-300 border-amber-500/40",
  stream: "text-emerald-300 border-emerald-500/40",
  log: "text-slate-400 border-slate-700",
  order: "text-emerald-300 border-emerald-500/40",
  write: "text-cyan-300 border-cyan-500/40",
};

function clockTime(iso: string): string {
  const parsed = Date.parse(iso);
  if (Number.isNaN(parsed)) return iso;
  return new Date(parsed).toISOString().slice(11, 23);
}

export const EventLogPanel: React.FC<EventLogPanelProps> = ({
  entries,
  capacity,
}) => {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const pinnedRef = useRef(true);

  // Follow the tail, but stop following the moment the operator scrolls up to
  // read something. Yanking them back to the bottom on the next frame of a
  // 75-second log makes the log unreadable while it is most interesting.
  useEffect(() => {
    const node = scrollRef.current;
    if (!node || !pinnedRef.current) return;
    node.scrollTop = node.scrollHeight;
  }, [entries]);

  const handleScroll = () => {
    const node = scrollRef.current;
    if (!node) return;
    const distanceFromBottom =
      node.scrollHeight - node.scrollTop - node.clientHeight;
    pinnedRef.current = distanceFromBottom < 32;
  };

  return (
    <section className="bg-slateDark-900 border border-slate-800 rounded-2xl p-4 flex flex-col gap-3 min-h-0 flex-1">
      <header className="flex items-center justify-between">
        <h2 className="text-sm font-bold text-white flex items-center gap-2">
          <Terminal className="w-4 h-4 text-brand-400" />
          Event Log
        </h2>
        <span className="text-[10px] font-mono uppercase tracking-wider text-slate-500">
          {entries.length}/{capacity}
        </span>
      </header>

      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="flex-1 min-h-0 overflow-auto rounded-xl border border-slate-800 bg-slateDark-950 p-2.5 font-mono text-[11px] space-y-0.5"
      >
        {entries.length === 0 ? (
          <p className="text-slate-600">
            Waiting for the first snapshot, telemetry report or control action…
          </p>
        ) : (
          entries.map((entry) => (
            <div key={entry.id} className="flex items-start gap-2">
              <span className="text-slate-600 shrink-0">
                {clockTime(entry.at)}
              </span>
              <span
                className={`shrink-0 w-[68px] text-[9px] uppercase tracking-wider border-l pl-1.5 ${KIND_STYLES[entry.kind]}`}
              >
                {entry.kind}
              </span>
              <span className="text-slate-300 break-all whitespace-pre-wrap">
                {entry.message}
              </span>
            </div>
          ))
        )}
      </div>
    </section>
  );
};
