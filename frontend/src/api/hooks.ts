import { useQuery } from "@tanstack/react-query";
import { useSourceParam } from "@/lib/fleet-source";
import { get } from "./client";
import type { Application, Cluster, EngineInfo, Environment, Page, Server, ServerDetail, Site } from "./types";

export const useEnvironments = () => useQuery({ queryKey: ["environments"], queryFn: () => get<Environment[]>("/environments"), staleTime: 60_000 });
export const useSites = () => useQuery({ queryKey: ["sites"], queryFn: () => get<Site[]>("/sites"), staleTime: 60_000 });
export const useClusters = () => useQuery({ queryKey: ["clusters"], queryFn: () => get<Cluster[]>("/clusters"), staleTime: 60_000 });
export const useApplications = () => useQuery({ queryKey: ["applications"], queryFn: () => get<Application[]>("/applications"), staleTime: 60_000 });
export const useEngines = () => useQuery({ queryKey: ["engines"], queryFn: () => get<EngineInfo[]>("/engines"), staleTime: 300_000 });

export const useServer = (id: string | undefined) =>
  useQuery({ queryKey: ["server", id], queryFn: () => get<ServerDetail>(`/servers/${id}`), enabled: !!id, refetchInterval: 30_000 });

const SERVER_PAGE = 500;
const MAX_SERVER_PAGES = 200; // safety stop (100 000 servers)

/** Every page of GET /servers: requests page 1, 2, … until all `total` items are in. */
export async function fetchAllServers(query: Record<string, string | string[] | undefined>, signal?: AbortSignal): Promise<Page<Server>> {
  const items: Server[] = [];
  let total = 0;
  for (let page = 1; page <= MAX_SERVER_PAGES; page++) {
    const res = await get<Page<Server>>("/servers", { ...query, page, page_size: SERVER_PAGE }, signal);
    items.push(...res.items);
    total = res.total;
    if (items.length >= total || res.items.length === 0) break;
  }
  return { items, total, page: 1, page_size: items.length };
}

/** Servers for fleet-wide views (all pages); follows the Real / Simulated / All choice. */
export const useAllServers = (query: Record<string, string | string[] | undefined> = {}) => {
  const source = useSourceParam();
  return useQuery({
    queryKey: ["servers", "all", source, query],
    queryFn: ({ signal }) => fetchAllServers({ sort: "name", source, ...query }, signal),
    staleTime: 15_000,
  });
};

/** Capability check from discovery data. Unknown capabilities are treated as usable. */
export function capUsable(server: ServerDetail | undefined, cap: string): boolean {
  const c = server?.capabilities?.[cap];
  if (!c) return true;
  return c.level === "FULL" || c.level === "PARTIAL";
}

export function serverCan(server: ServerDetail | undefined, perm: string): boolean {
  return !!server?.permissions.includes(perm);
}
