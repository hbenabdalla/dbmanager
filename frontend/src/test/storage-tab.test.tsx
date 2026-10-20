import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import type { ServerDetail } from "@/api/types";
import { StorageTab } from "@/features/server/tabs/infra";

function stub(body: unknown) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } })));
}

describe("storage tab", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows an Oracle-shaped payload: used bytes, largest tables, redo logs", async () => {
    stub({
      total_bytes: 3 * 2 ** 30,
      databases: [{ name: "ORCLPDB1", size_bytes: 3 * 2 ** 30 }],
      tablespaces: [{ name: "USERS", used_bytes: 2 ** 30, allocated_bytes: 2 * 2 ** 30, used_pct: 50, autoextend: true }],
      top_tables: [{ schema: "SHOP", name: "ORDERS", row_estimate: 1000, table_bytes: 2 ** 20, index_bytes: 2 ** 19, total_bytes: 3 * 2 ** 19 }],
      top_indexes: [],
      temp: { in_use_bytes: null, redo_log_bytes: 2 ** 30 },
      notes: [],
    });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><StorageTab server={{ id: "s1" } as ServerDetail} /></QueryClientProvider>);
    expect(await screen.findByText("SHOP.ORDERS")).toBeInTheDocument();
    expect(screen.getByText(/GB, 50% used/)).toBeInTheDocument();
    expect(screen.getByText(/Online redo logs/)).toBeInTheDocument();
    expect(screen.queryByText(/Temporary files written/)).not.toBeInTheDocument();
    expect(screen.queryByText(/undefined/)).not.toBeInTheDocument();
  });
});
