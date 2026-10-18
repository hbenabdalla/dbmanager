# UX audit and redesign (Objective B)

Each area was rated **KEEP** (works, leave alone), **IMPROVE** (right idea, missing pieces) or
**REDESIGN** (replaced). Screens were inspected in light and dark mode at 1440×900 and 390×844
with the simulated fleet (`standard` profile) and a real PostgreSQL server.

| Area | Rating | What changed |
|---|---|---|
| Design tokens (colour, type, radius) | KEEP | IBM Plex, cobalt action colour, status colours reserved for health. Added motion tokens (`--dur-fast/--dur/--dur-slow`, easing) and `anim-*` utilities; everything is disabled under `prefers-reduced-motion`. |
| Icons / engine marks | KEEP | Lucide throughout; engine badges unchanged. |
| Sidebar | REDESIGN | Five collapsible groups — Overview, Operations, Optimization, Security, Platform — remembered per browser; a collapsed icon rail; the group holding the current page cannot be collapsed. |
| Global actions | IMPROVE | "+ Add" menu (server, bulk onboarding, environment, users, identity provider) filtered by RBAC; Ctrl/Cmd+K palette gained an Actions group (also RBAC-filtered). |
| Dashboard | IMPROVE | Command-center KPI row with 24 h sparklines and change; health KPIs, engine bars and environment rows are toggles that filter the fleet map and attention list (state in the URL, "Open in server list" carries it over); setup checklist card for administrators. |
| Topology | NEW | React Flow map Environment → Site → Cluster → Server → Database; collapsed groups with counts and health bars, paging beyond 40 members, search with auto-expand, minimap, keyboard navigation; scales to 1,000+ servers. |
| Replication | NEW | Primary → replica diagram with animated direction, lag labels and health colour per link, inferred links shown faded; also inside the server's Replication tab. |
| Server header | IMPROVE | Live metric cards (last hour: value, trend, average, peak; warning/critical borders); cards for metrics the engine does not provide are omitted, never shown empty. |
| Performance tab | IMPROVE | 15m … 90d and custom ranges were already there; added drag-to-zoom with reset, synchronized hover across charts, now/avg/peak per chart. |
| Query statistics | IMPROVE | Top-10 visualization by the selected metric with share of total and max; the table stays. Percentiles are deliberately not shown: engines expose cumulative mean/max per statement, not distributions. |
| Execution plans | REDESIGN | Graph / Tree / Raw tabs: cost-heat node graph with estimated → actual rows and misestimate badges, the existing tree, and the vendor plan (JSON, showplan XML or text). |
| Security posture | REDESIGN | Score gauge, Good/Fair/Poor bands as filters, severity and category tiles that open the filtered findings list, averages by environment and engine. |
| Setup | NEW | Resumable Platform Setup Wizard (welcome → complete), post-setup checklist, banner until setup is finished. |
| Identity providers | NEW | LDAP wizard with staged tests (DNS, TCP, TLS, bind, user search, groups, user bind); OIDC wizard with Keycloak / Entra ID / Okta / generic presets and a staged test. Stored secrets are never displayed. |
| Server registration | REDESIGN | 9-step onboarding wizard with a live, streamed staged connection test and capability results; Monitoring Only is the default and administration is never enabled automatically. |
| Bulk onboarding | NEW | Grid / CSV / JSON input, template, per-row validation with inline fixing, password columns rejected in the browser and on the server, progress and per-row results. |
| Engine support / drivers | IMPROVE | Real-engine validation status per engine and per cell (`FULL*` until proven), "SQL Server Driver MISSING" banner with the remediation. |
| Empty / loading / error states | KEEP | Existing skeletons, empty and error states reused by every new screen; wizards end on success screens. |
