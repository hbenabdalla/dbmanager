import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Server } from "@/api/types";
import { ScopeBaselinePreview, serversInScope } from "@/features/alerts/alerts";

const srv = (id: string, env: string, tags: { key: string; value: string }[] = []) =>
  ({ id, name: id.toUpperCase(), environment: { id: env, name: env, tier: "production" }, tags }) as unknown as Server;
const FLEET = [srv("pg1", "prod", [{ key: "team", value: "payments" }]), srv("pg2", "prod"), srv("my1", "dev", [{ key: "team", value: "web" }])];

describe("z-score baseline preview for wide scopes", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("resolves the servers of each scope type", () => {
    expect(serversInScope(FLEET, "all", null).map((s) => s.id)).toEqual(["pg1", "pg2", "my1"]);
    expect(serversInScope(FLEET, "environment", "prod").map((s) => s.id)).toEqual(["pg1", "pg2"]);
    expect(serversInScope(FLEET, "tag", "team").map((s) => s.id)).toEqual(["pg1", "my1"]);
    expect(serversInScope(FLEET, "tag", "team=web").map((s) => s.id)).toEqual(["my1"]);
    expect(serversInScope(FLEET, "tag", "not a tag!")).toEqual([]);
  });

  it("previews one server at a time and switches on selection", async () => {
    const paths: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      paths.push(new URL(String(input), "http://localhost").pathname);
      return new Response(JSON.stringify({ sufficient_data: true, samples: 500, mean: 10, stddev: 2, current: 11, z_score: 0.5 }), { status: 200, headers: { "Content-Type": "application/json" } });
    }));
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><ScopeBaselinePreview servers={serversInScope(FLEET, "environment", "prod")} metric="cpu.percent" days={14} threshold={3} /></QueryClientProvider>);
    expect(await screen.findByTestId("baseline-preview")).toHaveTextContent("500 samples");
    expect(paths).toContain("/api/v1/servers/pg1/metrics/baseline");
    await userEvent.selectOptions(screen.getByLabelText("Server to preview"), "pg2");
    await waitFor(() => expect(paths).toContain("/api/v1/servers/pg2/metrics/baseline"));
  });

  it("says so when the scope has no server", () => {
    render(<ScopeBaselinePreview servers={[]} metric="cpu.percent" days={14} threshold={3} />);
    expect(screen.getByText(/No server in this scope/)).toBeInTheDocument();
  });
});
