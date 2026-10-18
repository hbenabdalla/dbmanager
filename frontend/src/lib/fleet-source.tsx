import { useQuery } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { get } from "@/api/client";

/**
 * Which servers fleet-wide views show: real ones, simulated ones or both.
 *
 * "auto" (the default) means real servers when any exist, otherwise everything — so a
 * simulated demo fleet never blends into an operational view unnoticed, and a fresh
 * installation with only demo data still shows something. The choice is remembered per browser.
 */
export type FleetSource = "auto" | "real" | "simulated" | "all";
export interface FleetSources { real: number; simulated: number; total: number; simulation_active: boolean }

interface Ctx {
  source: FleetSource;
  /** What "auto" currently resolves to (never "auto"). */
  effective: Exclude<FleetSource, "auto">;
  setSource: (s: FleetSource) => void;
  counts: FleetSources | undefined;
}

const KEY = "dbm.fleet-source";
const FleetSourceCtx = createContext<Ctx | null>(null);

function readStored(): FleetSource {
  try {
    const v = window.localStorage.getItem(KEY);
    if (v === "real" || v === "simulated" || v === "all" || v === "auto") return v;
  } catch {
    /* storage unavailable */
  }
  return "auto";
}

export function resolveSource(source: FleetSource, counts: FleetSources | undefined): Exclude<FleetSource, "auto"> {
  if (source !== "auto") return source;
  return counts && counts.real === 0 ? "all" : "real";
}

export function FleetSourceProvider({ children }: { children: ReactNode }) {
  const [source, setState] = useState<FleetSource>(readStored);
  const q = useQuery({ queryKey: ["fleet-sources"], queryFn: () => get<FleetSources>("/fleet/sources"), refetchInterval: 60_000 });
  const setSource = useCallback((s: FleetSource) => {
    setState(s);
    try {
      window.localStorage.setItem(KEY, s);
    } catch {
      /* storage unavailable */
    }
  }, []);
  const value = useMemo<Ctx>(() => ({ source, setSource, counts: q.data, effective: resolveSource(source, q.data) }), [source, setSource, q.data]);
  return <FleetSourceCtx.Provider value={value}>{children}</FleetSourceCtx.Provider>;
}

/** Outside the provider (isolated component tests) everything is shown. */
export function useFleetSource(): Ctx {
  return useContext(FleetSourceCtx) ?? { source: "all", effective: "all", setSource: () => undefined, counts: undefined };
}

/** The value to send as the ``source`` query parameter of fleet-wide endpoints. */
export function useSourceParam(): string {
  const { source } = useFleetSource();
  return source;
}
