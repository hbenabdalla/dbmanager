import { useState } from "react";
import { cn } from "@/lib/utils";
import { Input } from "./ui/input";

export type RangeValue = { range: string; start?: string; end?: string };
const OPTIONS = ["15m", "30m", "1h", "6h", "12h", "24h", "7d", "30d", "90d"];

export function TimeRangePicker({ value, onChange }: { value: RangeValue; onChange: (v: RangeValue) => void }) {
  const [custom, setCustom] = useState(value.range === "custom");
  const toLocal = (iso?: string) => (iso ? iso.slice(0, 16) : "");
  return (
    <div className="flex flex-wrap items-center gap-2">
      <div role="radiogroup" aria-label="Time range" className="inline-flex rounded-md border border-border bg-surface p-0.5">
        {OPTIONS.map((o) => (
          <button key={o} role="radio" aria-checked={value.range === o && !custom}
            onClick={() => { setCustom(false); onChange({ range: o }); }}
            className={cn("rounded-[4px] px-2.5 py-1 text-[12.5px] font-medium tabular", value.range === o && !custom ? "bg-accent-soft text-accent" : "text-muted hover:text-text")}>
            {o}
          </button>
        ))}
        <button role="radio" aria-checked={custom} onClick={() => setCustom(true)}
          className={cn("rounded-[4px] px-2.5 py-1 text-[12.5px] font-medium", custom ? "bg-accent-soft text-accent" : "text-muted hover:text-text")}>
          Custom
        </button>
      </div>
      {custom && (
        <div className="flex items-center gap-1.5">
          <Input type="datetime-local" aria-label="Start" className="h-8 w-[190px]" defaultValue={toLocal(value.start)}
            onChange={(e) => e.target.value && onChange({ range: "custom", start: new Date(e.target.value).toISOString(), end: value.end ?? new Date().toISOString() })} />
          <span className="text-muted">to</span>
          <Input type="datetime-local" aria-label="End" className="h-8 w-[190px]" defaultValue={toLocal(value.end)}
            onChange={(e) => e.target.value && onChange({ range: "custom", start: value.start ?? new Date(Date.now() - 86400000).toISOString(), end: new Date(e.target.value).toISOString() })} />
        </div>
      )}
    </div>
  );
}
