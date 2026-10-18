import { useQuery } from "@tanstack/react-query";
import { Command } from "cmdk";
import { BellRing, BookOpen, Info, KeyRound, Layers, type LucideIcon, Moon, Network, Search, Server as ServerIcon, ShieldCheck, Wand2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { get } from "@/api/client";
import type { Page, Server } from "@/api/types";
import { Dialog } from "@/components/ui/dialog";
import { useAbout } from "@/features/platform/about";
import { useAuth } from "@/lib/auth";
import { allowed, NAV } from "@/lib/nav";
import { useTheme } from "@/lib/theme";
import { EngineMark, HealthDot } from "./status";

export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const navigate = useNavigate();
  const { can, canPlatform } = useAuth();
  const { toggle } = useTheme();
  const [q, setQ] = useState("");
  useEffect(() => {
    if (!open) setQ("");
  }, [open]);
  const servers = useQuery({
    queryKey: ["palette-servers", q],
    queryFn: () => get<Page<Server>>("/servers", { q, page_size: 8, sort: "health" }),
    enabled: open && can("server.view"),
    staleTime: 15_000,
  });
  const about = useAbout();
  const docs = about.data?.links.documentation;
  const go = (to: string) => {
    onOpenChange(false);
    navigate(to);
  };
  const check = (perm: string | string[] | undefined, platform?: boolean) => allowed(perm, platform ? canPlatform : can);
  const pages = NAV.flatMap((s) =>
    !check(s.perm, s.platform) ? [] : s.items ? s.items.filter((i) => check(i.perm, i.platform)).map((i) => ({ label: `${s.label}: ${i.label}`, to: i.to })) : [{ label: s.label, to: s.to! }],
  );
  const actions: { label: string; icon: LucideIcon; run: () => void; keywords: string }[] = [
    { label: "Add a database server", icon: ServerIcon, run: () => go("/fleet/servers?register=1"), keywords: "register onboard new server database", ok: can("server.register") },
    { label: "Bulk onboard servers (CSV / JSON)", icon: Layers, run: () => go("/fleet/onboarding/bulk"), keywords: "bulk import csv json many", ok: can("server.register") },
    { label: "Open fleet topology", icon: Network, run: () => go("/topology"), keywords: "map graph topology", ok: can("server.view") },
    { label: "Review security posture", icon: ShieldCheck, run: () => go("/security"), keywords: "security findings posture", ok: can("security.view") },
    { label: "Configure an identity provider", icon: KeyRound, run: () => go("/platform/identity/wizard"), keywords: "ldap oidc sso keycloak entra okta", ok: canPlatform("idp.manage") },
    { label: "Run the platform setup wizard", icon: Wand2, run: () => go("/setup"), keywords: "setup onboarding first run checklist", ok: canPlatform("settings.manage") },
    { label: "Show active alerts", icon: BellRing, run: () => go("/alerts"), keywords: "alerts alarms firing incidents notifications", ok: can("alerts.view") },
    { label: "Open the documentation", icon: BookOpen, run: () => { onOpenChange(false); window.open(docs!, "_blank", "noopener,noreferrer"); }, keywords: "help docs documentation manual guide", ok: !!docs },
    { label: "About DB Manager", icon: Info, run: () => go("/platform/about"), keywords: "about version build license help", ok: true },
    { label: "Toggle light / dark theme", icon: Moon, run: () => { toggle(); onOpenChange(false); }, keywords: "theme dark light mode", ok: true },
  ].filter((a) => a.ok);
  const ql = q.toLowerCase();
  const shownActions = actions.filter((a) => !q || a.label.toLowerCase().includes(ql) || a.keywords.includes(ql));
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Go to" width="max-w-xl">
      <Command shouldFilter={false} label="Command palette" className="-mx-1">
        <div className="mb-2 flex items-center gap-2 rounded-md border border-border px-2.5">
          <Search className="size-4 text-muted" />
          <Command.Input autoFocus value={q} onValueChange={setQ} placeholder="Search servers, pages and actions…" className="h-9 w-full bg-transparent text-[13.5px] outline-none placeholder:text-muted" />
        </div>
        <Command.List className="scroll-thin max-h-[52vh] overflow-y-auto">
          <Command.Empty className="px-2 py-6 text-center text-[13px] text-muted">No matches</Command.Empty>
          {(servers.data?.items.length ?? 0) > 0 && (
            <Command.Group heading="Servers" className="mb-2 [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1 [&_[cmdk-group-heading]]:text-2xs [&_[cmdk-group-heading]]:text-muted">
              {servers.data!.items.map((s) => (
                <Command.Item key={s.id} value={`server-${s.id}`} onSelect={() => go(`/servers/${s.id}`)}
                  className="flex cursor-pointer items-center gap-2.5 rounded-md px-2 py-1.5 text-[13px] data-[selected=true]:bg-surface-3">
                  <HealthDot health={s.health_status} />
                  <EngineMark engine={s.engine} />
                  <span className="font-medium">{s.name}</span>
                  <span className="ml-auto text-2xs text-muted">{s.environment?.name}</span>
                </Command.Item>
              ))}
            </Command.Group>
          )}
          {shownActions.length > 0 && (
            <Command.Group heading="Actions" className="mb-2 [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1 [&_[cmdk-group-heading]]:text-2xs [&_[cmdk-group-heading]]:text-muted">
              {shownActions.map((a) => (
                <Command.Item key={a.label} value={`action-${a.label}`} onSelect={a.run}
                  className="flex cursor-pointer items-center gap-2.5 rounded-md px-2 py-1.5 text-[13px] data-[selected=true]:bg-surface-3">
                  <a.icon className="size-4 text-muted" /> {a.label}
                </Command.Item>
              ))}
            </Command.Group>
          )}
          <Command.Group heading="Pages" className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1 [&_[cmdk-group-heading]]:text-2xs [&_[cmdk-group-heading]]:text-muted">
            {pages.filter((p) => !q || p.label.toLowerCase().includes(q.toLowerCase())).map((p) => (
              <Command.Item key={p.to} value={p.to} onSelect={() => go(p.to)}
                className="cursor-pointer rounded-md px-2 py-1.5 text-[13px] data-[selected=true]:bg-surface-3">
                {p.label}
              </Command.Item>
            ))}
          </Command.Group>
        </Command.List>
      </Command>
    </Dialog>
  );
}
