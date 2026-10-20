import { Check, FlaskConical } from "lucide-react";
import { Link } from "react-router-dom";
import { Menu, MenuContent, MenuItem, MenuLabel, MenuSeparator, MenuTrigger } from "@/components/ui/menu";
import { useAuth } from "@/lib/auth";
import { type FleetSource, useFleetSource } from "@/lib/fleet-source";
import { cn } from "@/lib/utils";

const LABEL: Record<Exclude<FleetSource, "auto">, string> = { real: "Real servers", simulated: "Simulated servers", all: "All servers" };

/**
 * Header indicator, shown only while simulated servers exist. It states that simulation data is
 * present (not that the platform runs in a demo mode) and lets the user choose what fleet-wide
 * views include.
 */
export function SimulationIndicator() {
  const { counts, source, effective, setSource } = useFleetSource();
  const { canPlatform } = useAuth();
  if (!counts?.simulated) return null;
  const options: { value: FleetSource; label: string; n?: number }[] = [
    { value: "auto", label: "Automatic", n: undefined },
    { value: "real", label: LABEL.real, n: counts.real },
    { value: "simulated", label: LABEL.simulated, n: counts.simulated },
    { value: "all", label: LABEL.all, n: counts.total },
  ];
  return (
    <Menu>
      <MenuTrigger asChild>
        <button
          className="flex h-8 shrink-0 items-center gap-2 whitespace-nowrap rounded-md border border-sim/35 bg-sim-soft px-2.5 text-[12.5px] text-sim hover:border-sim/60"
          aria-label={`Simulation data active: ${counts.real} real and ${counts.simulated} simulated servers. Showing ${LABEL[effective].toLowerCase()}.`}
        >
          <FlaskConical className="size-3.5" />
          <span className="hidden font-medium xl:inline">Simulation data active</span>
          <span className="hidden text-text-2 xl:inline">
            <span className="tabular">{counts.real}</span> real, <span className="tabular">{counts.simulated}</span> simulated
          </span>
          <span className="hidden whitespace-nowrap rounded bg-surface px-1.5 text-[11.5px] font-medium text-text-2 sm:inline">{LABEL[effective]}</span>
        </button>
      </MenuTrigger>
      <MenuContent>
        <MenuLabel>Fleet-wide views show</MenuLabel>
        {options.map((o) => (
          <MenuItem key={o.value} onSelect={() => setSource(o.value)}>
            <Check className={cn(source === o.value ? "opacity-100" : "opacity-0")} />
            <span className="flex-1">{o.label}</span>
            {o.n !== undefined && <span className="tabular text-[12px] text-muted">{o.n}</span>}
            {o.value === "auto" && <span className="text-[12px] text-muted">{counts.real ? "real" : "all"}</span>}
          </MenuItem>
        ))}
        {canPlatform("simulation.view") && (
          <>
            <MenuSeparator />
            <MenuItem asChild><Link to="/platform/simulation"><FlaskConical /> Manage simulation data</Link></MenuItem>
          </>
        )}
      </MenuContent>
    </Menu>
  );
}

/** Inline Real / Simulated / All switch for fleet-wide pages (only when both kinds exist). */
export function SourceFilter({ className }: { className?: string }) {
  const { counts, effective, setSource } = useFleetSource();
  if (!counts?.simulated) return null;
  const items: { value: Exclude<FleetSource, "auto">; label: string; n: number }[] = [
    { value: "real", label: "Real", n: counts.real },
    { value: "simulated", label: "Simulated", n: counts.simulated },
    { value: "all", label: "All", n: counts.total },
  ];
  return (
    <div role="radiogroup" aria-label="Show servers" className={cn("inline-flex h-8 items-center rounded-md border border-border bg-surface p-0.5", className)}>
      {items.map((i) => (
        <button
          key={i.value}
          role="radio"
          aria-checked={effective === i.value}
          onClick={() => setSource(i.value)}
          className={cn(
            "flex h-full items-center gap-1.5 rounded-[5px] px-2.5 text-[12.5px] font-medium transition-colors",
            effective === i.value ? (i.value === "simulated" ? "bg-sim-soft text-sim" : "bg-surface-3 text-text") : "text-muted hover:text-text",
          )}
        >
          {i.value === "simulated" && <FlaskConical className="size-3.5" />}
          {i.label}
          <span className="tabular text-[11.5px] opacity-75">{i.n}</span>
        </button>
      ))}
    </div>
  );
}
