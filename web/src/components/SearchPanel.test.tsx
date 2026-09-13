import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { makeEnvelope } from "../test/fixtures";
import type { SearchResponse } from "../types";
import { SearchPanel } from "./SearchPanel";

function response(): SearchResponse {
  const envelope = makeEnvelope({ source: "nws", eventId: "alert-1", place: "Test County" });
  return {
    count: 1,
    candidates_considered: 4,
    items: [
      {
        ...envelope,
        document_text: "Title: Severe Thunderstorm Warning",
        distance_km: 12.25,
        ranking: {
          lexical_rank: 1,
          lexical_score: 0.8,
          dense_rank: 2,
          dense_similarity: 0.91,
          rrf_score: 0.98,
          exact_phrase_match: true,
          token_coverage: 1,
          rerank_score: 0.99,
        },
        citation: {
          status: "traceable",
          url: "https://api.weather.gov/alerts/alert-1",
          source_field: "source_url",
          reasons: ["public_http_url"],
        },
      },
    ],
    embedding_model: "BAAI/bge-small-en-v1.5",
    ranking_mode: "hybrid",
    ranking_rule: "rrf60-transparent-rerank-v1",
    caveat: "Ranked source events, not a generated answer.",
    parameters: {
      query: "violent storm",
      limit: 20,
      candidate_limit: 100,
      source: null,
      occurred_after: null,
      occurred_before: null,
      active_only: true,
      bbox: null,
      near: null,
      radius_km: null,
      ranking_mode: "hybrid",
    },
  };
}

const requiredProps = {
  query: "",
  draft: "",
  useViewport: true,
  viewportAvailable: false,
  loading: false,
  error: null,
  selectedStreamId: null,
  onDraftChange: vi.fn(),
  onUseViewportChange: vi.fn(),
  onSubmit: vi.fn(),
  onSelect: vi.fn(),
};

describe("SearchPanel", () => {
  it("explains the no-generation boundary and disables incomplete searches", () => {
    render(<SearchPanel {...requiredProps} />);
    expect(screen.getByText(/never a generated answer/)).toBeInTheDocument();
    expect(screen.getByText(/RRF \+ transparent rerank/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Search" })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: /current map viewport/ })).toBeDisabled();
  });

  it("submits a valid draft and toggles the available viewport", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    const onViewport = vi.fn();
    const onDraft = vi.fn();
    render(
      <SearchPanel
        {...requiredProps}
        draft="violent storm"
        viewportAvailable
        onSubmit={onSubmit}
        onUseViewportChange={onViewport}
        onDraftChange={onDraft}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Search" }));
    await user.click(screen.getByRole("checkbox", { name: /current map viewport/ }));
    await user.type(screen.getByRole("searchbox"), " now");

    expect(onSubmit).toHaveBeenCalledOnce();
    expect(onViewport).toHaveBeenCalledWith(false);
    expect(onDraft).toHaveBeenCalled();
  });

  it("renders auditable channel ranks, evidence, selection, and caveat", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(
      <SearchPanel
        {...requiredProps}
        query="violent storm"
        draft="violent storm"
        response={response()}
        selectedStreamId="1000-0"
        onSelect={onSelect}
      />,
    );

    expect(screen.getByText("4 candidates")).toBeInTheDocument();
    expect(screen.getByText(/FTS #1 · VECTOR #2 · FINAL 0.990 · 12.3 km/)).toBeInTheDocument();
    expect(screen.getByText("traceable")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open source evidence/ })).toHaveAttribute(
      "href",
      "https://api.weather.gov/alerts/alert-1",
    );
    expect(screen.getByText(/not a generated answer/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Severe Thunderstorm Warning/ }));
    expect(onSelect).toHaveBeenCalledWith("1000-0");
  });

  it("shows loading, errors, and an empty bounded result", () => {
    const { rerender } = render(
      <SearchPanel {...requiredProps} query="storm" draft="storm" loading />,
    );
    expect(screen.getByRole("button", { name: "Searching…" })).toBeDisabled();

    rerender(
      <SearchPanel
        {...requiredProps}
        query="storm"
        draft="storm"
        error={new Error("retrieval unavailable")}
      />,
    );
    expect(screen.getByText("retrieval unavailable")).toBeInTheDocument();

    rerender(
      <SearchPanel
        {...requiredProps}
        query="storm"
        draft="storm"
        response={{ ...response(), count: 0, candidates_considered: 0, items: [] }}
      />,
    );
    expect(screen.getByText(/No current evidence matched/)).toBeInTheDocument();

    rerender(
      <SearchPanel
        {...requiredProps}
        query="storm"
        draft="storm"
        response={{
          ...response(),
          ranking_mode: "dense",
          parameters: { ...response().parameters, ranking_mode: "dense" },
        }}
      />,
    );
    expect(screen.getByText(/DENSE evaluation mode/)).toBeInTheDocument();
  });
});
