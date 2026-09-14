import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { makeSourcePollHistoryResponse, makeSourcePollTransition } from "../test/fixtures";
import { SourcePollHistory } from "./SourcePollHistory";

describe("SourcePollHistory", () => {
  it("shows a bounded failure-and-later-success sequence without raw source detail", () => {
    const response = makeSourcePollHistoryResponse(
      [
        makeSourcePollTransition({ streamId: "2002-0", source: "gdelt" }),
        makeSourcePollTransition({
          streamId: "2001-0",
          source: "gdelt",
          transition: "failed",
        }),
        makeSourcePollTransition({
          streamId: "2000-0",
          source: "gdelt",
          transition: "started",
        }),
      ],
      { has_more: true },
    );

    render(<SourcePollHistory response={response} loading={false} error={null} />);

    const rows = screen.getAllByRole("listitem");
    expect(rows).toHaveLength(3);
    expect(within(rows[0]).getByText("Succeeded")).toBeInTheDocument();
    expect(within(rows[0]).getByText(/2 fetched · 1 published/)).toBeInTheDocument();
    expect(within(rows[1]).getByText("Failed")).toBeInTheDocument();
    expect(within(rows[1]).getByText(/transport exhausted at fetch/)).toBeInTheDocument();
    expect(within(rows[2]).getByText("Started")).toBeInTheDocument();
    expect(screen.getByText("Older retained entries available")).toBeInTheDocument();
    expect(screen.queryByText(/secret|https?:\/\//i)).not.toBeInTheDocument();
  });

  it("distinguishes an empty retained ledger from an unavailable one", () => {
    const empty = makeSourcePollHistoryResponse([]);
    const { rerender } = render(
      <SourcePollHistory response={empty} loading={false} error={null} />,
    );
    expect(screen.getByText("No poll transitions are retained yet.")).toBeInTheDocument();
    expect(screen.getByText(empty.caveat)).toBeInTheDocument();

    rerender(
      <SourcePollHistory
        response={undefined}
        loading={false}
        error={new Error("invalid payload")}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Current freshness above remains independently evaluated",
    );
    expect(screen.getByText("Unavailable")).toHaveClass("unknown");
  });

  it("shows a loading state without inventing retained transitions", () => {
    render(<SourcePollHistory response={undefined} loading={true} error={null} />);
    expect(screen.getByText("Loading retained poll transitions…")).toBeInTheDocument();
  });
});
