import { screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ColumnDef } from "@tanstack/react-table";
import { ConfirmDialog } from "@/components/confirm";
import { HealthBadge, RiskBadge, SeverityBadge } from "@/components/status";
import { DataTable } from "@/components/ui/data-table";
import { bytes, duration, ms } from "@/lib/format";
import { allowed } from "@/lib/nav";
import { renderWithProviders } from "./utils";

describe("status badges", () => {
  it("labels health and severity in words, not only colour", () => {
    renderWithProviders(<><HealthBadge health="unavailable" /><SeverityBadge severity="critical" /><RiskBadge risk="DESTRUCTIVE" /></>);
    expect(screen.getByText("Unavailable")).toBeInTheDocument();
    expect(screen.getByText("Critical")).toBeInTheDocument();
    expect(screen.getByText("Destructive")).toBeInTheDocument();
  });
});

describe("ConfirmDialog", () => {
  it("keeps the action disabled until the expected text is typed", async () => {
    const onConfirm = vi.fn();
    renderWithProviders(<ConfirmDialog open onOpenChange={() => {}} title="Drop it?" expected="pg-prod-01" danger onConfirm={onConfirm} confirmLabel="Drop" />);
    const btn = screen.getByRole("button", { name: "Drop" });
    expect(btn).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Confirmation text"), "pg-prod-0");
    expect(btn).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Confirmation text"), "1");
    expect(btn).toBeEnabled();
    fireEvent.click(btn);
    expect(onConfirm).toHaveBeenCalledWith("pg-prod-01", "");
  });

  it("requires a reason when asked", async () => {
    renderWithProviders(<ConfirmDialog open onOpenChange={() => {}} title="x" askReason reasonRequired onConfirm={() => {}} confirmLabel="Go" />);
    expect(screen.getByRole("button", { name: "Go" })).toBeDisabled();
    await userEvent.type(screen.getByRole("textbox"), "CHG-1");
    expect(screen.getByRole("button", { name: "Go" })).toBeEnabled();
  });
});

describe("DataTable", () => {
  const cols: ColumnDef<{ n: string; v: number }, unknown>[] = [{ header: "Name", accessorKey: "n" }, { header: "Value", accessorKey: "v" }];
  it("sorts when a header is clicked", async () => {
    renderWithProviders(<DataTable columns={cols} data={[{ n: "b", v: 2 }, { n: "a", v: 1 }, { n: "c", v: 3 }]} />);
    await userEvent.click(screen.getByRole("button", { name: /Name/ }));
    const cells = screen.getAllByRole("cell").filter((_, i) => i % 2 === 0).map((c) => c.textContent);
    expect(cells).toEqual(["a", "b", "c"]);
  });
  it("shows an empty state", () => {
    renderWithProviders(<DataTable columns={cols} data={[]} />);
    expect(screen.getByText("Nothing to show")).toBeInTheDocument();
  });
});

describe("formatting and navigation helpers", () => {
  it("formats units", () => {
    expect(bytes(1536)).toBe("1.50 KB");
    expect(ms(1500)).toBe("1.5 s");
    expect(duration(3700)).toBe("1h 1m");
  });
  it("hides navigation without permission", () => {
    const can = (p: string) => p === "server.view";
    expect(allowed("server.view", can)).toBe(true);
    expect(allowed(["audit.view", "users.manage"], can)).toBe(false);
    expect(allowed(undefined, can)).toBe(true);
  });
});
