import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ReplayControls } from "./ReplayControls";

function renderControls(overrides: Partial<React.ComponentProps<typeof ReplayControls>> = {}) {
  const properties: React.ComponentProps<typeof ReplayControls> = {
    current: 2,
    total: 8,
    playing: false,
    speed: 1,
    hasMore: true,
    loadingMore: false,
    onCurrentChange: vi.fn(),
    onPlayingChange: vi.fn(),
    onSpeedChange: vi.fn(),
    onLoadMore: vi.fn(),
    ...overrides,
  };
  render(<ReplayControls {...properties} />);
  return properties;
}

describe("ReplayControls", () => {
  it("drives playback, speed, position, and pagination", async () => {
    const user = userEvent.setup();
    const properties = renderControls();

    await user.click(screen.getByRole("button", { name: "Play replay" }));
    await user.click(screen.getByRole("button", { name: "2×" }));
    fireEvent.change(screen.getByRole("slider", { name: "Replay position" }), {
      target: { value: "5" },
    });
    await user.click(screen.getByRole("button", { name: "Load 500 more" }));

    expect(properties.onPlayingChange).toHaveBeenCalledWith(true);
    expect(properties.onSpeedChange).toHaveBeenCalledWith(2);
    expect(properties.onCurrentChange).toHaveBeenCalledWith(5);
    expect(properties.onLoadMore).toHaveBeenCalledOnce();
  });

  it("disables playback for an empty replay", () => {
    renderControls({ total: 0, current: 0, hasMore: false });
    expect(screen.getByRole("button", { name: "Play replay" })).toBeDisabled();
    expect(screen.getByRole("slider", { name: "Replay position" })).toBeDisabled();
    expect(screen.getByText("0 / 0")).toBeInTheDocument();
  });

  it("shows the paused action and a guarded loading state", () => {
    renderControls({ playing: true, loadingMore: true });
    expect(screen.getByRole("button", { name: "Pause replay" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Loading…" })).toBeDisabled();
  });
});
