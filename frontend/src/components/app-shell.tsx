import { useQuery } from "@tanstack/react-query";
import {
  BellRing, BookOpen, ChevronDown, ChevronsLeft, CircleHelp, Github, Info, ChevronsRight, Database, FolderTree, KeyRound, Layers, LogOut, Menu as MenuIcon, Moon, Plus,
  Search, Server as ServerIcon, Sun, UserPlus, UserRound, Wand2, X,
} from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { get } from "@/api/client";
import { Menu, MenuContent, MenuItem, MenuLabel, MenuSeparator, MenuTrigger } from "@/components/ui/menu";
import { useAuth } from "@/lib/auth";
import { FleetSourceProvider } from "@/lib/fleet-source";
import { allowed, NAV_GROUPS, type NavGroup, type NavSection } from "@/lib/nav";
import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";
import { useAlertSummary } from "@/features/alerts/alerts";
import { ChangePasswordDialog } from "@/features/auth/change-password";
import { useAbout } from "@/features/platform/about";
import { CommandPalette } from "./command-palette";
import { SimulationIndicator } from "./fleet-source";

/** Top-bar bell: number of active alerts the user may see (hidden without alerts.view). */
function AlertBell({ can }: { can: (p: string) => boolean }) {
  const summary = useAlertSummary(can("alerts.view"));
  const navigate = useNavigate();
  if (!can("alerts.view") || !summary.data?.visible) return null;
  const n = summary.data.active;
  const crit = (summary.data.by_severity.critical ?? 0) + (summary.data.by_severity.high ?? 0) > 0;
  return (
    <button onClick={() => navigate("/alerts")} className="relative rounded-md p-2 text-muted hover:bg-surface-3 hover:text-text"
      aria-label={n ? `${n} active alert${n === 1 ? "" : "s"}` : "No active alerts"} title={n ? `${n} active alerts` : "No active alerts"}>
      <BellRing className="size-4" />
      {n > 0 && (
        <span data-testid="alert-count" className={cn("absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] font-semibold text-white", crit ? "bg-crit" : "bg-warn")}>
          {n > 99 ? "99+" : n}
        </span>
      )}
    </button>
  );
}

/** Help menu: documentation (only when DBM_DOCUMENTATION_URL is configured), source code, About. */
function HelpMenu() {
  const about = useAbout();
  const navigate = useNavigate();
  const links = about.data?.links;
  const open = (url: string) => window.open(url, "_blank", "noopener,noreferrer");
  return (
    <Menu>
      <MenuTrigger asChild>
        <button className="rounded-md p-2 text-muted hover:bg-surface-3 hover:text-text" aria-label="Help"><CircleHelp className="size-4" /></button>
      </MenuTrigger>
      <MenuContent>
        {links?.documentation && <MenuItem onSelect={() => open(links.documentation!)}><BookOpen /> Documentation</MenuItem>}
        {links?.github && <MenuItem onSelect={() => open(links.github!)}><Github /> Source code on GitHub</MenuItem>}
        {(links?.documentation || links?.github) && <MenuSeparator />}
        <MenuItem onSelect={() => navigate("/platform/about")}><Info /> About DB Manager{about.data ? ` ${about.data.version.application}` : ""}</MenuItem>
      </MenuContent>
    </Menu>
  );
}

const COLLAPSED_KEY = "dbm.nav.collapsed-groups";
const RAIL_KEY = "dbm.nav.rail";

function readJson<T>(key: string, fallback: T): T {
  try {
    const v = localStorage.getItem(key);
    return v ? (JSON.parse(v) as T) : fallback;
  } catch {
    return fallback;
  }
}
function writeJson(key: string, v: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(v));
  } catch {
    /* private mode: preference not kept */
  }
}

function Brand({ compact }: { compact?: boolean }) {
  if (compact) {
    return (
      <div className="flex justify-center py-4">
        <svg viewBox="0 0 32 32" className="size-7" aria-label="DB Manager">
          <rect width="32" height="32" rx="7" fill="#15243a" />
          <g fill="none" strokeWidth="2.2">
            <ellipse cx="16" cy="9.5" rx="8" ry="3.2" stroke="#6c97ff" />
            <path d="M8 9.5v13c0 1.8 3.6 3.2 8 3.2s8-1.4 8-3.2v-13" stroke="#6c97ff" />
            <path d="M8 16c0 1.8 3.6 3.2 8 3.2s8-1.4 8-3.2" stroke="#37c48f" />
          </g>
        </svg>
      </div>
    );
  }
  return (
    <div className="flex items-center gap-2.5 px-4 py-4">
      <svg viewBox="0 0 32 32" className="size-7" aria-hidden>
        <rect width="32" height="32" rx="7" fill="#15243a" />
        <g fill="none" strokeWidth="2.2">
          <ellipse cx="16" cy="9.5" rx="8" ry="3.2" stroke="#6c97ff" />
          <path d="M8 9.5v13c0 1.8 3.6 3.2 8 3.2s8-1.4 8-3.2v-13" stroke="#6c97ff" />
          <path d="M8 16c0 1.8 3.6 3.2 8 3.2s8-1.4 8-3.2" stroke="#37c48f" />
        </g>
      </svg>
      <div className="leading-tight">
        <div className="text-[14px] font-semibold text-nav-strong">DB Manager</div>
        <div className="text-2xs text-nav-muted">Fleet operations</div>
      </div>
    </div>
  );
}

function SectionLink({ s, can, canPlatform, compact }: { s: NavSection; can: (p: string) => boolean; canPlatform: (p: string) => boolean; compact?: boolean }) {
  const loc = useLocation();
  const check = (perm: string | string[] | undefined, platform?: boolean) => allowed(perm, platform ? canPlatform : can);
  const items = (s.items ?? []).filter((i) => check(i.perm, i.platform));
  const activeChild = items.some((i) => (i.to === "/" ? loc.pathname === "/" : loc.pathname === i.to || loc.pathname.startsWith(i.to + "/")));
  const [open, setOpen] = useState(activeChild);
  useEffect(() => {
    if (activeChild) setOpen(true);
  }, [activeChild]);
  const Icon = s.icon;
  const base = "flex w-full items-center gap-2.5 rounded-md px-2.5 py-[7px] text-[13px] transition-colors duration-150";
  if (compact) {
    const to = s.to ?? items[0]?.to;
    if (!to) return null;
    const active = s.to ? (s.to === "/" ? loc.pathname === "/" : loc.pathname.startsWith(s.to)) : activeChild;
    return (
      <NavLink to={to} title={s.label} aria-label={s.label}
        className={cn("flex justify-center rounded-md py-2 transition-colors duration-150", active ? "bg-nav-surface text-nav-strong" : "text-nav-text hover:bg-nav-surface/60 hover:text-nav-strong")}>
        <Icon className="size-[18px]" />
      </NavLink>
    );
  }
  if (!s.items) {
    return (
      <NavLink to={s.to!} end={s.to === "/"} className={({ isActive }) => cn(base, isActive ? "bg-nav-surface text-nav-strong" : "text-nav-text hover:bg-nav-surface/60 hover:text-nav-strong")}>
        <Icon className="size-4 opacity-80" /> {s.label}
      </NavLink>
    );
  }
  if (!items.length) return null;
  return (
    <div>
      <button onClick={() => setOpen((o) => !o)} aria-expanded={open}
        className={cn(base, activeChild ? "text-nav-strong" : "text-nav-text hover:bg-nav-surface/60 hover:text-nav-strong")}>
        <Icon className="size-4 opacity-80" /> {s.label}
        <ChevronDown className={cn("ml-auto size-3.5 opacity-60 transition-transform", !open && "-rotate-90")} />
      </button>
      {open && (
        <div className="anim-expand mb-1 ml-[18px] mt-0.5 border-l border-white/10 pl-2.5">
          {items.map((i) => (
            <NavLink key={i.to} to={i.to} end
              className={({ isActive }) => cn("block rounded-md px-2.5 py-[5px] text-[12.5px] transition-colors",
                isActive ? "bg-nav-surface font-medium text-nav-strong" : "text-nav-text/85 hover:text-nav-strong")}>
              {i.label}
            </NavLink>
          ))}
        </div>
      )}
    </div>
  );
}

function Group({ g, can, canPlatform, collapsed, onToggle, compact }: {
  g: NavGroup; can: (p: string) => boolean; canPlatform: (p: string) => boolean; collapsed: boolean; onToggle: () => void; compact?: boolean;
}) {
  const loc = useLocation();
  const visible = g.sections.filter((s) => allowed(s.perm, s.platform ? canPlatform : can));
  if (!visible.length) return null;
  const containsActive = visible.some((s) => {
    const paths = s.to ? [s.to] : (s.items ?? []).map((i) => i.to);
    return paths.some((p) => (p === "/" ? loc.pathname === "/" : loc.pathname === p || loc.pathname.startsWith(p + "/")));
  });
  const open = !collapsed || containsActive;
  if (compact) {
    return (
      <div className="space-y-0.5 border-t border-white/5 pt-2 first:border-0 first:pt-0">
        {visible.map((s) => <SectionLink key={s.label} s={s} can={can} canPlatform={canPlatform} compact />)}
      </div>
    );
  }
  return (
    <div className="pt-2 first:pt-0">
      <button onClick={onToggle} aria-expanded={open} disabled={containsActive}
        className="flex w-full items-center px-2.5 pb-1 pt-1 text-2xs font-semibold uppercase tracking-[0.08em] text-nav-muted hover:text-nav-text disabled:cursor-default disabled:hover:text-nav-muted">
        {g.label}
        {!containsActive && <ChevronDown className={cn("ml-auto size-3 transition-transform duration-150", !open && "-rotate-90")} />}
      </button>
      {open && <div className="space-y-0.5">{visible.map((s) => <SectionLink key={s.label} s={s} can={can} canPlatform={canPlatform} />)}</div>}
    </div>
  );
}

/** Global "+ Add" menu: only entries the caller may use. */
function AddMenu({ can, canPlatform }: { can: (p: string) => boolean; canPlatform: (p: string) => boolean }) {
  const navigate = useNavigate();
  const entries = [
    { label: "Database server", icon: ServerIcon, to: "/fleet/servers?register=1", ok: can("server.register") },
    { label: "Bulk onboarding", icon: Layers, to: "/fleet/onboarding/bulk", ok: can("server.register") },
    { label: "Environment", icon: FolderTree, to: "/fleet/environments?create=1", ok: can("inventory.manage") },
    { label: "Database user", icon: Database, to: "/admin/users", ok: can("user.manage") },
    { label: "Platform user", icon: UserPlus, to: "/platform/users?create=1", ok: can("users.manage") },
    { label: "Identity provider", icon: KeyRound, to: "/platform/identity/wizard", ok: canPlatform("idp.manage") },
  ].filter((e) => e.ok);
  if (!entries.length) return null;
  return (
    <Menu>
      <MenuTrigger asChild>
        <button className="inline-flex h-8 items-center gap-1.5 rounded-md bg-accent px-2.5 text-[13px] font-medium text-accent-fg transition-colors duration-150 hover:bg-accent-hover" aria-label="Add">
          <Plus className="size-4" /> <span className="hidden sm:inline">Add</span>
        </button>
      </MenuTrigger>
      <MenuContent>
        <MenuLabel>Add to DB Manager</MenuLabel>
        {entries.map((e) => <MenuItem key={e.label} onSelect={() => navigate(e.to)}><e.icon /> {e.label}</MenuItem>)}
      </MenuContent>
    </Menu>
  );
}

export function AppShell() {
  const { me, can, canPlatform, logout } = useAuth();
  const { theme, toggle } = useTheme();
  const [palette, setPalette] = useState(false);
  const [mobileNav, setMobileNav] = useState(false);
  const [pwOpen, setPwOpen] = useState(false);
  const [collapsedGroups, setCollapsedGroups] = useState<string[]>(() => readJson<string[]>(COLLAPSED_KEY, []));
  const [rail, setRail] = useState<boolean>(() => readJson<boolean>(RAIL_KEY, false));
  const loc = useLocation();
  const navigateTo = useNavigate();
  const toggleGroup = (id: string) => setCollapsedGroups((c) => {
    const next = c.includes(id) ? c.filter((x) => x !== id) : [...c, id];
    writeJson(COLLAPSED_KEY, next);
    return next;
  });
  const toggleRail = () => setRail((r) => {
    writeJson(RAIL_KEY, !r);
    return !r;
  });
  const settings = useQuery({ queryKey: ["settings-public"], queryFn: () => get<{ editable: Record<string, unknown>; readonly: Record<string, unknown> }>("/settings"), staleTime: 300_000 });
  const banner = settings.data?.editable["ui.banner_text"] as string | undefined;
  const setup = useQuery({ queryKey: ["platform-setup-state"], queryFn: () => get<{ completed: boolean; needs_setup: boolean; current_step?: string | null }>("/platform/setup"), staleTime: 60_000, retry: false });
  const showSetup = setup.data?.needs_setup && !loc.pathname.startsWith("/setup");

  useEffect(() => setMobileNav(false), [loc.pathname]);
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette((p) => !p);
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);
  useEffect(() => {
    if (me?.must_change_password) setPwOpen(true);
  }, [me?.must_change_password]);

  const navFor = (compact: boolean) => (
    <nav aria-label="Main" className={cn("scroll-thin flex-1 overflow-y-auto pb-4", compact ? "space-y-2 px-1.5" : "space-y-1 px-2")}>
      {NAV_GROUPS.map((g) => (
        <Group key={g.id} g={g} can={can} canPlatform={canPlatform} compact={compact}
          collapsed={collapsedGroups.includes(g.id)} onToggle={() => toggleGroup(g.id)} />
      ))}
    </nav>
  );
  const railWidth = rail ? "lg:w-[64px]" : "lg:w-[232px]";

  return (
    <FleetSourceProvider>
    <div className="flex min-h-screen">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-surface focus:px-3 focus:py-2">Skip to content</a>
      <aside className={cn("fixed inset-y-0 left-0 z-30 hidden flex-col bg-nav transition-[width] duration-200 lg:flex", railWidth)}>
        <Brand compact={rail} />
        {navFor(rail)}
        <button onClick={toggleRail} aria-label={rail ? "Expand navigation" : "Collapse navigation"} title={rail ? "Expand navigation" : "Collapse navigation"}
          className="m-2 flex items-center justify-center gap-2 rounded-md py-1.5 text-[12px] text-nav-muted hover:bg-nav-surface hover:text-nav-strong">
          {rail ? <ChevronsRight className="size-4" /> : <><ChevronsLeft className="size-4" /> Collapse</>}
        </button>
      </aside>
      {mobileNav && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/50" onClick={() => setMobileNav(false)} />
          <aside className="relative flex h-full w-[260px] flex-col bg-nav">
            <div className="flex items-center justify-between pr-2"><Brand />
              <button aria-label="Close navigation" className="rounded p-1.5 text-nav-text hover:bg-nav-surface" onClick={() => setMobileNav(false)}><X className="size-4" /></button>
            </div>
            {navFor(false)}
          </aside>
        </div>
      )}
      <div className={cn("flex min-w-0 flex-1 flex-col transition-[padding] duration-200", rail ? "lg:pl-[64px]" : "lg:pl-[232px]")}>
        <header className="sticky top-0 z-20 flex h-12 items-center gap-2 border-b border-border bg-surface/90 px-3 backdrop-blur sm:px-5">
          <button className="rounded p-1.5 text-muted hover:bg-surface-3 lg:hidden" aria-label="Open navigation" onClick={() => setMobileNav(true)}>
            <MenuIcon className="size-4" />
          </button>
          <button onClick={() => setPalette(true)}
            className="flex h-8 w-full max-w-sm items-center gap-2 rounded-md border border-border bg-surface-2 px-2.5 text-[13px] text-muted hover:border-border-strong">
            <Search className="size-4" />
            <span className="truncate">Search servers, pages and actions</span>
            <kbd className="ml-auto hidden rounded border border-border bg-surface px-1.5 font-mono text-2xs sm:inline">Ctrl K</kbd>
          </button>
          <div className="ml-auto flex items-center gap-1">
            <AddMenu can={can} canPlatform={canPlatform} />
            <SimulationIndicator />
            <AlertBell can={can} />
            <HelpMenu />
            <button onClick={toggle} className="rounded-md p-2 text-muted hover:bg-surface-3 hover:text-text" aria-label={theme === "dark" ? "Use light theme" : "Use dark theme"}>
              {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
            </button>
            <Menu>
              <MenuTrigger asChild>
                <button className="flex items-center gap-2 rounded-md px-2 py-1 text-[13px] hover:bg-surface-3">
                  <span className="flex size-6 items-center justify-center rounded-full bg-accent-soft text-[11px] font-semibold uppercase text-accent">{me?.username.slice(0, 2)}</span>
                  <span className="hidden max-w-[160px] truncate sm:inline">{me?.display_name ?? me?.username}</span>
                  <ChevronDown className="size-3.5 text-muted" />
                </button>
              </MenuTrigger>
              <MenuContent>
                <MenuLabel>
                  Signed in as <span className="font-medium text-text">{me?.username}</span>
                  <span className="block">via {me?.auth_source === "local" ? "local account" : me?.auth_source.toUpperCase()}</span>
                </MenuLabel>
                <MenuSeparator />
                <MenuLabel>Access</MenuLabel>
                <div className="max-h-40 overflow-y-auto px-2 pb-1">
                  {me?.grants.map((g, i) => (
                    <div key={i} className="py-0.5 text-[12px]"><span className="text-text">{g.role}</span> <span className="text-muted">on {g.scope_label ?? g.scope_type}</span></div>
                  ))}
                  {!me?.grants.length && <div className="text-[12px] text-muted">No roles assigned yet</div>}
                </div>
                <MenuSeparator />
                <MenuItem onSelect={() => navigateTo("/account/tokens")}><KeyRound /> API tokens</MenuItem>
                {me?.auth_source === "local" && <MenuItem onSelect={() => setPwOpen(true)}><UserRound /> Change password</MenuItem>}
                <MenuItem onSelect={() => logout()}><LogOut /> Sign out</MenuItem>
              </MenuContent>
            </Menu>
          </div>
        </header>
        {banner && <div className="border-b border-warn/30 bg-warn-soft px-5 py-1.5 text-[12.5px] text-warn">{banner}</div>}
        {showSetup && (
          <div className="anim-fade flex flex-wrap items-center gap-2 border-b border-accent/25 bg-accent-soft px-5 py-1.5 text-[12.5px] text-text-2">
            <Wand2 className="size-4 text-accent" />
            <span>DB Manager is not fully set up yet{setup.data?.current_step ? ` (stopped at “${setup.data.current_step}”)` : ""}.</span>
            <NavLink to="/setup" className="font-medium text-accent hover:underline">{setup.data?.current_step ? "Resume setup" : "Start setup"}</NavLink>
          </div>
        )}
        <main id="main" className="mx-auto w-full max-w-[1600px] flex-1 px-3 py-5 sm:px-6">
          <Outlet />
        </main>
      </div>
      <CommandPalette open={palette} onOpenChange={setPalette} />
      <ChangePasswordDialog open={pwOpen} onOpenChange={setPwOpen} forced={me?.must_change_password} />
    </div>
    </FleetSourceProvider>
  );
}
