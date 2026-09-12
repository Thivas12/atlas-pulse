import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { makeIncident } from "../test/fixtures";
import { IncidentFeed } from "./IncidentFeed";

describe("IncidentFeed", () => {
  it("renders an honest empty state", () => {
    render(
      <IncidentFeed
        incidents={[]}
        selectedIncidentId={null}
        candidateEdgesTruncated={false}
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByText("No cross-source co-occurrence in this viewport…")).toBeInTheDocument();
  });

  it("renders graph measurements and selects a candidate", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    const incident = makeIncident();
    render(
      <IncidentFeed
        incidents={[incident]}
        selectedIncidentId={incident.incident_id}
        candidateEdgesTruncated={false}
        onSelect={onSelect}
      />,
    );

    const row = screen.getByRole("button", { name: /2-source signal cluster/ });
    expect(row).toHaveClass("selected");
    expect(screen.getByText("2N")).toHaveClass("graph");
    expect(screen.getByText("firms")).toBeInTheDocument();
    expect(screen.getByText("gdelt")).toBeInTheDocument();
    expect(row).toHaveTextContent("1 edge · max 7.8 km");
    await user.click(row);
    expect(onSelect).toHaveBeenCalledWith("incident-test123");
  });

  it("warns when the bounded database edge result is truncated", () => {
    render(
      <IncidentFeed
        incidents={[makeIncident()]}
        selectedIncidentId={null}
        candidateEdgesTruncated={true}
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByText(/Edge limit reached/)).toBeInTheDocument();
  });
});
