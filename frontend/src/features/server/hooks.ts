import { useQuery } from "@tanstack/react-query";
import { get } from "@/api/client";
import type { SeriesResponse } from "@/api/types";
import type { RangeValue } from "@/components/time-range";
import { refetchFor, useRefreshMs } from "@/lib/refresh";

export function rangeQuery(range: RangeValue) {
  return { range: range.range === "custom" ? undefined : range.range, start: range.start, end: range.end };
}

/** One request for all metrics of a server and range (the cache key is shared by every chart). */
export function useMetrics(
  serverId: string,
  metrics: string[],
  range: RangeValue,
  opts: { labeled?: boolean; enabled?: boolean; agg?: string } = {},
) {
  const refreshMs = useRefreshMs();
  return useQuery({
    queryKey: ["metrics", serverId, metrics, range, opts.labeled ?? false, opts.agg ?? "avg"],
    queryFn: ({ signal }) =>
      get<SeriesResponse>(
        `/servers/${serverId}/metrics`,
        {
          metric: metrics,
          ...rangeQuery(range),
          labeled: opts.labeled ? "true" : undefined,
          agg: opts.agg && opts.agg !== "avg" ? opts.agg : undefined,
        },
        signal,
      ),
    refetchInterval: refetchFor(refreshMs, range.range),
    refetchIntervalInBackground: false,
    staleTime: 10_000,
    retry: (n, e) => n < 1 && !(e && typeof e === "object" && "code" in e && (e as { code: string }).code === "timeseries_unavailable"),
    enabled: opts.enabled ?? true,
    placeholderData: (p) => p,
  });
}

export function pointsFor(data: SeriesResponse | undefined, metric: string) {
  return data?.series.find((s) => s.metric === metric && !s.label)?.points ?? [];
}
