import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { makeEnvelope } from "../test/fixtures";
import { EventFeed } from "./EventFeed";

describe("EventFeed", () => {
  it("renders its waiting state without events", () => {
    render(<EventFeed events={[]} selectedStreamId={null} onSelect={vi.fn()} />);
    expect(screen.getByText("Waiting for the first mapped event…")).toBeInTheDocument();
    expect(screen.getByText("0")).toBeInTheDocument();
  });

  it("renders measurements and selects a stream revision", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    const item = makeEnvelope({ streamId: "4242-1", magnitude: null, place: null });
    render(<EventFeed events={[item]} selectedStreamId="4242-1" onSelect={onSelect} />);

    const row = screen.getByRole("button", { name: /Unknown region/ });
    expect(row).toHaveClass("selected");
    expect(screen.getByText("?")).toBeInTheDocument();
    await user.click(row);
    expect(onSelect).toHaveBeenCalledWith("4242-1");
  });

  it("renders weather type, source, place, and severity instead of a fake magnitude", () => {
    const item = makeEnvelope({
      source: "nws",
      alertType: "Tornado Warning",
      severity: "Extreme",
      place: "Test County",
    });
    render(<EventFeed events={[item]} selectedStreamId={null} onSelect={vi.fn()} />);

    expect(screen.getByRole("button", { name: /Tornado Warning/ })).toBeInTheDocument();
    expect(screen.getByText("EXT")).toHaveClass("weather", "severity-extreme");
    expect(screen.getByText("nws")).toBeInTheDocument();
    expect(screen.getByText(/Test County/)).toBeInTheDocument();
  });
});
