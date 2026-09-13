import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { makeIncident } from "../test/fixtures";
import { IncidentDetail } from "./IncidentDetail";

describe("IncidentDetail", () => {
  it("shows measured edges, claim provenance, and both safety boundaries", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<IncidentDetail incident={makeIncident()} onClose={onClose} />);

    const detail = screen.getByRole("complementary", { name: "Selected incident candidate" });
    expect(within(detail).getByText("2 nodes / 1 edge")).toBeInTheDocument();
    expect(within(detail).getByText("7.75 km")).toBeInTheDocument();
    expect(within(detail).getByText("7.75 km · 5.0 min")).toBeInTheDocument();
    expect(within(detail).getByText("Claim relationships")).toBeInTheDocument();
    expect(within(detail).getByText("0 corroborating")).toBeInTheDocument();
    expect(within(detail).getByText("0 conflicting")).toBeInTheDocument();
    expect(within(detail).getByText("1 unresolved")).toBeInTheDocument();
    expect(within(detail).getByText("No decisive claim pair")).toBeInTheDocument();
    expect(within(detail).getByText("hazard domain · fire_related")).toBeInTheDocument();
    expect(within(detail).getByText(/firms:fire-test/)).toHaveTextContent("event_type");
    expect(within(detail).getByText(/not proof of truth/)).toBeInTheDocument();
    expect(within(detail).getByText(/do not establish causation/)).toBeInTheDocument();
    expect(within(detail).getAllByRole("link", { name: /Open source evidence/ })).toHaveLength(2);
    await user.click(within(detail).getByRole("button", { name: "Close details" }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("does not render unsafe evidence URLs", () => {
    const incident = makeIncident();
    incident.nodes[0].event.payload.source_url = "javascript:alert(1)";
    incident.nodes[1].event.payload.source_url = "https://user:secret@example.test/report";
    render(<IncidentDetail incident={incident} onClose={vi.fn()} />);
    expect(screen.queryByRole("link", { name: /Open source evidence/ })).toBeNull();
  });
});
