import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { DeploymentCard, deploymentText, type Instances } from "@/features/platform/deployment";

const now = new Date().toISOString();
const DATA: Instances = {
  heartbeat_seconds: 15,
  summary: { api: 2, workers: 2, stale: 1, leader: "worker-1:7", versions: ["0.14.0", "0.15.0"], mixed_versions: true },
  instances: [
    { id: "api-1:1", role: "api", hostname: "api-1", version: "0.15.0", git_commit: "abcdef0123", runs_worker: false, is_leader: false, started_at: now, last_seen_at: now, status: "active", this_instance: true },
    { id: "worker-1:7", role: "worker", hostname: "worker-1", version: "0.15.0", git_commit: null, runs_worker: true, is_leader: true, started_at: now, last_seen_at: now, status: "active", this_instance: false },
    { id: "worker-0:9", role: "worker", hostname: "worker-0", version: "0.14.0", git_commit: null, runs_worker: true, is_leader: false, started_at: now, last_seen_at: now, status: "stale", this_instance: false },
  ],
};

function show(body: unknown) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } })));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><DeploymentCard /></QueryClientProvider>);
}

describe("deployment card", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("lists every replica with the leader, stale instances and mixed versions", async () => {
    show(DATA);
    expect(await screen.findByText("worker-1:7")).toBeInTheDocument();
    expect(screen.getByText(/Scheduler leader/)).toBeInTheDocument();
    expect(screen.getByText(/No heartbeat/)).toBeInTheDocument();
    expect(screen.getByText(/Mixed versions: 0.14.0, 0.15.0/)).toBeInTheDocument();
    expect(screen.getByText("this page")).toBeInTheDocument();
    expect(screen.getByText("abcdef0")).toBeInTheDocument();
  });

  it("warns when no worker runs anywhere", async () => {
    show({ ...DATA, summary: { ...DATA.summary, workers: 0, mixed_versions: false }, instances: [DATA.instances[0]] });
    expect(await screen.findByRole("status")).toHaveTextContent(/No worker is running/);
  });

  it("summarises the deployment in one line", () => {
    expect(deploymentText(DATA.summary)).toBe("2 API · 2 workers · 1 stale");
    expect(deploymentText({ ...DATA.summary, workers: 1, stale: 0 })).toBe("2 API · 1 worker");
    expect(deploymentText(null)).toBe("unknown");
  });
});
