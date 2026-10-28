import { FlaskConical, Plus } from "lucide-react";
import { Link } from "react-router-dom";
import { ENGINES } from "@/lib/engines";
import { DatabaseEngineIcon } from "@/components/engine-icon";
import { BrandLogo } from "@/components/brand";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useAuth } from "@/lib/auth";

const STEPS = [
  ["Choose the engine", "PostgreSQL, MySQL, MariaDB, SQL Server, Oracle, Db2 LUW or Db2 for z/OS; Platform → Engine support shows each engine's level."],
  ["Enter connection details", "Host, port, a least-privileged monitoring account and TLS settings."],
  ["Test the connection", "KBM DBManager checks reachability and permissions before saving."],
  ["Monitoring starts", "Capabilities are discovered and the first collection runs immediately."],
];

/** Shown instead of the dashboard while no servers are registered at all. */
export function FleetOnboarding() {
  const { can, canPlatform } = useAuth();
  const canRegister = can("server.register");
  const canSimulate = canPlatform("simulation.generate");
  return (
    <Card className="overflow-hidden">
      <div className="grid gap-0 lg:grid-cols-[1.1fr_1fr]">
        <div className="px-6 py-8 sm:px-10 sm:py-12">
          <div className="mb-5"><BrandLogo width={200} /></div>
          <h2 className="text-[22px] font-semibold leading-tight">No database servers registered</h2>
          <p className="mt-2 max-w-md text-[14px] leading-relaxed text-text-2">
            Connect your first database server to start monitoring and managing your database fleet.
          </p>
          <div className="mt-6 flex flex-wrap items-center gap-2">
            {canRegister ? (
              <Button asChild variant="primary" size="lg"><Link to="/fleet/servers?register=1"><Plus /> Add database server</Link></Button>
            ) : (
              <p className="text-[13px] text-muted">Ask a Platform Administrator to register servers or to grant you access to them.</p>
            )}
            {canSimulate && (
              <Button asChild variant="secondary" size="lg"><Link to="/platform/simulation?generate=1"><FlaskConical /> Generate demo environment</Link></Button>
            )}
          </div>
          {canSimulate && (
            <p className="mt-3 max-w-md text-[12.5px] text-muted">
              Demo data is optional. It creates a clearly labelled simulated fleet that never connects to a database, and you can remove it at any time.
            </p>
          )}
          <ul className="mt-8 flex flex-wrap gap-x-4 gap-y-2 text-[12.5px] text-text-2" aria-label="Supported engines">
            {Object.keys(ENGINES).map((e) => <li key={e}><DatabaseEngineIcon engine={e} size="sm" withLabel /></li>)}
          </ul>
        </div>
        <ol className="border-t border-border bg-surface-2 px-6 py-8 sm:px-10 lg:border-l lg:border-t-0">
          {STEPS.map(([title, text], i) => (
            <li key={title} className="relative flex gap-4 pb-6 last:pb-0">
              {i < STEPS.length - 1 && <span aria-hidden className="absolute left-[13px] top-7 h-[calc(100%-1.5rem)] w-px bg-border-strong" />}
              <span className="tabular relative flex size-7 shrink-0 items-center justify-center rounded-full border border-border-strong bg-surface text-[12.5px] font-semibold text-text-2">{i + 1}</span>
              <div>
                <div className="text-[13.5px] font-medium">{title}</div>
                <div className="mt-0.5 text-[12.5px] text-muted">{text}</div>
              </div>
            </li>
          ))}
        </ol>
      </div>
    </Card>
  );
}
