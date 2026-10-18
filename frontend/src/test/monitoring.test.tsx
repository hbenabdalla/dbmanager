import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ApiError } from "@/api/client";
import { ErrorState, StorageUnavailable, isStorageUnavailable } from "@/components/ui/states";
import { formatStep } from "@/lib/format";
import { refetchFor, setRefreshMs } from "@/lib/refresh";

describe("monitoring storage outage", () => {
  it("shows a dedicated state instead of a generic error", () => {
    const err = new ApiError(503, "timeseries_unavailable", "Monitoring storage is unavailable");
    expect(isStorageUnavailable(err)).toBe(true);
    render(<ErrorState error={err} />);
    expect(screen.getByText("Monitoring Storage Unavailable")).toBeInTheDocument();
    expect(screen.getByText(/Inventory, administration and security keep working/)).toBeInTheDocument();
  });
  it("has a compact chart placeholder", () => {
    render(<StorageUnavailable compact />);
    expect(screen.getByRole("status")).toHaveTextContent("Monitoring Storage Unavailable");
  });
});

describe("auto-refresh", () => {
  it("is off when disabled and never polls long ranges faster than 5 minutes", () => {
    expect(refetchFor(0, "1h")).toBe(false);
    expect(refetchFor(15_000, "15m")).toBe(15_000);
    expect(refetchFor(15_000, "30d")).toBe(300_000);
    expect(refetchFor(60_000, "custom")).toBe(300_000);
  });
  it("persists the choice", () => {
    setRefreshMs(30_000);
    expect(window.localStorage.getItem("dbm.autoRefreshMs")).toBe("30000");
  });
});

describe("resolution label", () => {
  it("formats steps", () => {
    expect(formatStep(30)).toBe("30 second");
    expect(formatStep(300)).toBe("5 minute");
    expect(formatStep(21600)).toBe("6 hour");
    expect(formatStep(86400)).toBe("1 day");
  });
});
