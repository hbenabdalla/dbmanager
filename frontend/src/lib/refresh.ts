/** Auto-refresh preference for charts (per viewer, remembered in this browser). */
import { useSyncExternalStore } from "react";

export const REFRESH_OPTIONS: { label: string; ms: number }[] = [
  { label: "Off", ms: 0 },
  { label: "15s", ms: 15_000 },
  { label: "30s", ms: 30_000 },
  { label: "1m", ms: 60_000 },
  { label: "5m", ms: 300_000 },
];
const KEY = "dbm.autoRefreshMs";
const DEFAULT_MS = 60_000;

function read(): number {
  try {
    const v = Number(window.localStorage.getItem(KEY));
    return REFRESH_OPTIONS.some((o) => o.ms === v) && window.localStorage.getItem(KEY) !== null ? v : DEFAULT_MS;
  } catch {
    return DEFAULT_MS;
  }
}

let current = typeof window === "undefined" ? DEFAULT_MS : read();
const listeners = new Set<() => void>();

export function setRefreshMs(ms: number) {
  current = ms;
  try {
    window.localStorage.setItem(KEY, String(ms));
  } catch {
    /* storage unavailable: keep the in-memory value */
  }
  listeners.forEach((l) => l());
}

export function useRefreshMs(): number {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => current,
    () => current,
  );
}

/**
 * Polling interval for a chart query. Long ranges never poll faster than every 5 minutes:
 * a new sample barely changes a 7-90 day chart, and those queries are the expensive ones.
 */
export function refetchFor(ms: number, range: string): number | false {
  if (!ms) return false;
  const long = ["7d", "30d", "90d", "custom"].includes(range);
  return long ? Math.max(ms, 300_000) : ms;
}
