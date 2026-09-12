import { describe, expect, it, vi } from "vitest";
import { fetchLatest, fetchReplay } from "./api";
import { makeEnvelope } from "./test/fixtures";

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("AtlasPulse API client", () => {
  it("validates the latest event response", async () => {
    const body = { count: 1, items: [makeEnvelope()] };
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(body));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchLatest()).resolves.toEqual(body);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/events?limit=500",
      expect.objectContaining({ headers: { Accept: "application/json" } }),
    );
  });

  it("passes an exclusive replay cursor and validates ordering metadata", async () => {
    const body = {
      count: 1,
      items: [makeEnvelope()],
      next_cursor: "1000-0",
      has_more: true,
      order: "oldest_first" as const,
    };
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(body));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchReplay("999-0")).resolves.toEqual(body);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/events/replay?limit=500&after=999-0",
      expect.any(Object),
    );
  });

  it("surfaces HTTP failures", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({}, 503)));
    await expect(fetchLatest()).rejects.toThrow("AtlasPulse API returned HTTP 503");
  });

  it("rejects a response that violates the browser boundary schema", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        jsonResponse({
          count: 1,
          items: [{ ...makeEnvelope(), stream_id: "not-a-stream-id" }],
        }),
      ),
    );
    await expect(fetchLatest()).rejects.toThrow();
  });
});
