/** Shared helpers for the React Flow based views (topology, replication, execution plans). */
import { useEffect, useState } from "react";
import type { Page, Server } from "@/api/types";
import { get } from "@/api/client";

/** true when the user asked the OS for reduced motion; follows changes live. */
export function useReducedMotion(): boolean {
  const query = "(prefers-reduced-motion: reduce)";
  const [reduced, setReduced] = useState(() => typeof window !== "undefined" && !!window.matchMedia?.(query).matches);
  useEffect(() => {
    const mq = window.matchMedia?.(query);
    if (!mq) return;
    const on = () => setReduced(mq.matches);
    mq.addEventListener?.("change", on);
    return () => mq.removeEventListener?.("change", on);
  }, []);
  return reduced;
}

/** Every server visible to the caller (pages through /servers so 1,000+ fleets are complete). */
export async function fetchAllServers(query: Record<string, string | undefined> = {}, signal?: AbortSignal): Promise<Server[]> {
  const out: Server[] = [];
  const pageSize = 500;
  for (let page = 1; page <= 40; page++) {
    const res = await get<Page<Server>>("/servers", { page, page_size: pageSize, sort: "name", ...query }, signal);
    out.push(...res.items);
    if (out.length >= res.total || res.items.length < pageSize) break;
  }
  return out;
}

/** Status colour tokens (health only). */
export const STATUS_VAR: Record<string, string> = {
  healthy: "var(--ok)", ok: "var(--ok)", warning: "var(--warn)", warn: "var(--warn)", critical: "var(--crit)", crit: "var(--crit)",
  unavailable: "var(--down)", down: "var(--down)", unknown: "var(--border-strong)",
};

/** Keyframes for animated edges; the media query turns them off for reduced motion. */
export function FlowStyles() {
  return (
    <style>{`
@keyframes dbm-flow-dash { to { stroke-dashoffset: -20; } }
.dbm-flow-animated { stroke-dasharray: 6 4; animation: dbm-flow-dash 900ms linear infinite; }
.dbm-flow-node { transition: opacity 200ms ease, box-shadow 200ms ease, transform 200ms ease; }
.react-flow__node:focus-visible .dbm-flow-node, .dbm-flow-node:focus-visible { box-shadow: 0 0 0 3px var(--ring); outline: none; }
.react-flow { --xy-background-color: var(--bg); --xy-node-border-radius: 8px; --xy-edge-stroke: var(--border-strong);
  --xy-minimap-background-color: var(--surface); --xy-controls-button-background-color: var(--surface);
  --xy-controls-button-background-color-hover: var(--surface-2); --xy-controls-button-color: var(--text-2);
  --xy-controls-button-border-color: var(--border); --xy-attribution-background-color: transparent; }
.react-flow__controls { box-shadow: none; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
.react-flow__minimap { border: 1px solid var(--border); border-radius: 6px; }
@media (prefers-reduced-motion: reduce) { .dbm-flow-animated { animation: none; } .dbm-flow-node { transition: none; } }
`}</style>
  );
}

/**
 * React Flow gives a node ``pointer-events: none`` when it is neither selectable, draggable
 * nor has a click handler. Our nodes are static but contain buttons, so every flow passes this
 * no-op handler to keep them interactive.
 */
export const keepPointerEvents = () => undefined;
