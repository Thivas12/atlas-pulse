import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { makeSourceFreshnessItem, makeSourceFreshnessResponse } from "../test/fixtures";
import { SourceFreshnessPanel } from "./SourceFreshnessPanel";

describe("SourceFreshnessPanel", () => {
  it("keeps a failed poll separate from still-current upstream data", () => {
    const response = makeSourceFreshnessResponse([
      makeSourceFreshnessItem("gdelt", {
        poll_status: "degraded",
        last_outcome: "failed",
        last_stage: "fetch",
        last_failure_code: "transport_exhausted",
        consecutive_failures: 2,
        transport_attempts: 3,
        passed: false,
      }),
      makeSourceFreshnessItem("usgs"),
    ]);

    render(<SourceFreshnessPanel response={response} loading={false} error={null} />);

    expect(screen.getByText("1 / 2 sources pass · 2026-09-14 12:00:00Z")).toBeInTheDocument();
    const gdelt = screen.getByRole("article", { name: "GDELT freshness" });
    expect(within(gdelt).getByText("Degraded")).toBeInTheDocument();
    expect(within(gdelt).getByText("Current")).toBeInTheDocument();
    expect(within(gdelt).getByText("Failure: transport exhausted at fetch")).toBeInTheDocument();
    expect(within(gdelt).getByText(/2 consecutive failures/)).toBeInTheDocument();
    expect(screen.getByText(/not independent monitoring/)).toBeInTheDocument();
  });

  it("shows a first-poll state without inventing upstream freshness", () => {
    const response = makeSourceFreshnessResponse([
      makeSourceFreshnessItem("nws", {
        poll_status: "starting",
        source_data_status: "not_reported",
        last_outcome: null,
        last_stage: null,
        last_attempt_at: null,
        last_success_at: null,
        last_source_generated_at: null,
        last_success_age_seconds: null,
        source_age_seconds: null,
        timestamp_basis: null,
        transport_attempts: 0,
        passed: false,
      }),
    ]);

    render(<SourceFreshnessPanel response={response} loading={false} error={null} />);

    const nws = screen.getByRole("article", { name: "NOAA / NWS freshness" });
    expect(within(nws).getByText("Starting")).toBeInTheDocument();
    expect(within(nws).getByText("Not reported")).toBeInTheDocument();
    expect(within(nws).getByText("No poll attempt recorded")).toBeInTheDocument();
    expect(
      within(nws).getByText("Waiting for first successful source timestamp"),
    ).toBeInTheDocument();
  });

  it("states that event visibility cannot replace unavailable freshness evidence", () => {
    const { rerender } = render(
      <SourceFreshnessPanel response={undefined} loading={true} error={null} />,
    );
    expect(screen.getByText("Checking source poll evidence…")).toBeInTheDocument();

    rerender(
      <SourceFreshnessPanel
        response={undefined}
        loading={false}
        error={new Error("invalid payload")}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Event visibility below is not a substitute",
    );
    expect(screen.getByText("Unknown")).toHaveClass("unknown");
  });

  it("does not style cached passing evidence as current after a refetch failure", () => {
    render(
      <SourceFreshnessPanel
        response={makeSourceFreshnessResponse()}
        loading={false}
        error={new Error("refetch failed")}
      />,
    );

    expect(screen.getByText("Unknown")).toHaveClass("unknown");
    expect(screen.getByRole("alert")).toHaveTextContent("freshness evidence is unavailable");
  });
});
