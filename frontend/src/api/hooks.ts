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

/** Servers for fleet-wide views; follows the Real / Simulated / All choice. */
export const useAllServers = (query: Record<string, string | string[] | undefined> = {}) => {
  const source = useSourceParam();
  return useQuery({
    queryKey: ["servers", "all", source, query],
    queryFn: () => get<Page<Server>>("/servers", { page_size: 500, sort: "name", source, ...query }),
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
