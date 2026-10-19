import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { BrandLogo, BrandMark } from "@/components/brand";
import { DatabaseEngineIcon } from "@/components/engine-icon";
import { AuthProvider } from "@/lib/auth";
import { ENGINES, engineMeta, normalizeEngine } from "@/lib/engines";
import { mockFetch, renderWithProviders } from "./utils";

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("DatabaseEngineIcon", () => {
  it("names every engine for assistive technology and keeps Db2 LUW and z/OS distinct", () => {
    const { container } = renderWithProviders(<div>{Object.keys(ENGINES).map((e) => <DatabaseEngineIcon key={e} engine={e} />)}</div>);
    for (const label of ["PostgreSQL", "MySQL", "MariaDB", "SQL Server", "Oracle", "Db2 LUW", "Db2 for z/OS"]) {
      expect(screen.getByRole("img", { name: label })).toBeInTheDocument();
    }
    const marks = [...container.querySelectorAll("[data-engine-mark]")].map((m) => m.getAttribute("data-engine-mark"));
    expect(marks).toEqual(["postgresql", "mysql", "mariadb", "mssql", "oracle", "db2", "db2zos"]);
    // the z/OS mark has its own band and monogram; no image or external resource is used
    expect(container.querySelector('[data-engine-mark="db2zos"] text')?.textContent).toBe("Dz");
    expect(container.querySelector('[data-engine-mark="db2"] text')?.textContent).toBe("D2");
    expect(container.querySelectorAll("img")).toHaveLength(0);
    expect(container.innerHTML).not.toMatch(/https?:\/\//);
  });

  it("accepts common spellings and falls back for unknown engines", () => {
    expect(normalizeEngine("sqlserver")).toBe("mssql");
    expect(normalizeEngine("db2-zos")).toBe("db2zos");
    expect(normalizeEngine("Postgres")).toBe("postgresql");
    expect(engineMeta("").label).toBe("Unknown engine");
    const { container } = renderWithProviders(<>
      <DatabaseEngineIcon engine="sqlserver" withLabel />
      <DatabaseEngineIcon engine="db2-zos" withLabel version="13.1" />
      <DatabaseEngineIcon engine="cockroachdb" />
      <DatabaseEngineIcon engine={null} />
    </>);
    expect(screen.getByText("SQL Server")).toBeInTheDocument();
    expect(screen.getByText("Db2 for z/OS 13.1")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "cockroachdb" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Unknown engine" })).toBeInTheDocument();
    // unknown engines get the neutral database glyph, not a monogram
    expect(container.querySelectorAll('[data-engine-mark="unknown"] ellipse').length).toBeGreaterThan(0);
  });

  it("hides a decorative icon when the name is already shown", () => {
    const { container } = renderWithProviders(<label><DatabaseEngineIcon engine="oracle" decorative />Oracle Database</label>);
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(container.querySelector('[data-engine="oracle"]')).toHaveAttribute("aria-hidden", "true");
  });

  it("shows an operator-supplied licensed logo unaltered and falls back when it fails to load", async () => {
    mockFetch((url) => {
      if (url.pathname === "/api/v1/auth/me") return { body: { id: "u1", username: "dba", permissions: [], platform_permissions: [], grants: [] } };
      if (url.pathname === "/api/v1/branding/engine-logos") return { body: { engines: ["oracle"] } };
      return undefined;
    });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { container } = render(<QueryClientProvider client={qc}><MemoryRouter><AuthProvider>
      <DatabaseEngineIcon engine="oracle" /><DatabaseEngineIcon engine="mysql" />
    </AuthProvider></MemoryRouter></QueryClientProvider>);
    await waitFor(() => expect(container.querySelector("img")).not.toBeNull());
    const img = container.querySelector("img")!;
    expect(img.getAttribute("src")).toBe("/api/v1/branding/engine-logos/oracle");
    expect(img.className).toContain("object-contain"); // scaled, never stretched
    expect(screen.getByRole("img", { name: "Oracle" })).toBeInTheDocument();
    expect(container.querySelector('[data-engine-mark="mysql"]')).not.toBeNull(); // no licensed MySQL logo
    fireEvent.error(img);
    await waitFor(() => expect(container.querySelector('[data-engine-mark="oracle"]')).not.toBeNull());
  });
});

describe("DBManager logo", () => {
  it("renders the provided logo files with the product name as text alternative", () => {
    const { container } = renderWithProviders(<><BrandLogo width={160} /><BrandMark size={24} plate label="DBManager" /><BrandLogo decorative /></>);
    expect(screen.getAllByRole("img", { name: "DBManager" })).toHaveLength(2); // logo img + labelled mark
    const imgs = [...container.querySelectorAll("img")];
    expect(imgs.map((i) => i.getAttribute("src"))).toEqual(expect.arrayContaining([expect.stringMatching(/dbmanager-logo-small\.png$/), expect.stringMatching(/dbmanager-mark\.png$/)]));
    expect(imgs.filter((i) => i.getAttribute("alt") === "DBManager")).toHaveLength(1);
    expect(imgs.find((i) => i.getAttribute("src")?.includes("dbmanager-logo-small"))?.getAttribute("width")).toBe("160");
  });
});
