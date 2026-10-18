import { useNavigate } from "react-router-dom";
import { Tooltip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { ENGINES, HEALTH_LABEL } from "@/lib/engines";
import { HEALTH_VAR } from "./status";

export interface FleetTile { id: string; name: string; engine: string; health: string; environment: string; reasons: string[]; is_demo: boolean }

/**
 * Every visible server as one tile, grouped by environment and coloured by health.
 * Tile size shrinks with fleet size so thousands of servers still fit on one screen.
 */
export function FleetStrip({ tiles }: { tiles: FleetTile[] }) {
  const navigate = useNavigate();
  const groups = new Map<string, FleetTile[]>();
  for (const t of tiles) groups.set(t.environment, [...(groups.get(t.environment) ?? []), t]);
  const mixed = tiles.some((t) => t.is_demo) && tiles.some((t) => !t.is_demo);
  const size = tiles.length > 600 ? 10 : tiles.length > 200 ? 14 : tiles.length > 60 ? 20 : 28;
  return (
    <div className="space-y-3">
      {[...groups.entries()].map(([env, list], gi) => {
        const bad = list.filter((t) => t.health !== "healthy").length;
        return (
          <div key={env} className="grid grid-cols-[112px_1fr] items-start gap-3">
            <div className="pt-0.5">
              <div className="text-[12.5px] font-medium text-text-2">{env}</div>
              <div className="text-2xs text-muted tabular">{list.length} servers{bad ? `, ${bad} need attention` : ""}</div>
              {mixed && list.some((t) => t.is_demo) && <div className="text-2xs text-sim tabular">{list.filter((t) => t.is_demo).length} simulated</div>}
            </div>
            <ul className="flex flex-wrap gap-[3px]" aria-label={`${env} servers`}>
              {list.map((t, i) => (
                <li key={t.id}>
                  <Tooltip content={
                    <span>
                      <span className="font-medium">{t.name}</span>
                      <span className="block text-[#b9c6d6]">{ENGINES[t.engine]?.label ?? t.engine}, {HEALTH_LABEL[t.health] ?? t.health}{t.is_demo ? ", simulated" : ""}</span>
                      {t.reasons.length > 0 && <span className="mt-1 block">{t.reasons.join("; ")}</span>}
                    </span>
                  }>
                    <button
                      onClick={() => navigate(`/servers/${t.id}`)}
                      aria-label={`${t.name}: ${HEALTH_LABEL[t.health] ?? t.health}${t.is_demo ? " (simulated)" : ""}`}
                      className={cn("block rounded-[3px] transition-transform hover:scale-125 focus-visible:scale-125", t.is_demo && "sim-hatch")}
                      style={{
                        width: size, height: size, backgroundColor: HEALTH_VAR[t.health] ?? HEALTH_VAR.unknown,
                        opacity: t.health === "healthy" ? 0.78 : 1,
                        animation: `dbm-strip-in 360ms ${Math.min((gi * 12 + i) * 14, 700)}ms both cubic-bezier(.2,.8,.2,1)`,
                      }}
                    />
                  </Tooltip>
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </div>
  );
}
