import { expect, type Page, test } from "@playwright/test";
import securityHeaders from "../security-headers.json" with { type: "json" };
import {
  makeEnvelope,
  makeSourceFreshnessResponse,
  makeSourcePollHistoryResponse,
} from "../src/test/fixtures";

const smokeSignal = makeEnvelope({
  eventId: "us-smoke-browser",
  place: "Smoke Test Ridge",
  streamId: "2100000000000-0",
});

const emptyMapStyle = {
  version: 8,
  glyphs: "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf",
  sources: {},
  layers: [],
};

interface ContractRouteState {
  handledApiUrls: string[];
  unexpectedApiPaths: string[];
}

async function installContractRoutes(page: Page): Promise<ContractRouteState> {
  const state: ContractRouteState = { handledApiUrls: [], unexpectedApiPaths: [] };

  await page.route("https://tiles.openfreemap.org/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/styles/liberty") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        headers: { "access-control-allow-origin": "*" },
        body: JSON.stringify(emptyMapStyle),
      });
      return;
    }
    await route.abort();
  });

  await page.route("https://fonts.googleapis.com/**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/css",
      headers: { "access-control-allow-origin": "*" },
      body: "",
    });
  });

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    state.handledApiUrls.push(url.toString());

    let response: unknown;
    if (url.pathname === "/api/v1/signals") {
      const source = url.searchParams.get("source");
      const items = source === null || source === "usgs" ? [smokeSignal] : [];
      response = {
        count: items.length,
        items,
        next_cursor: items.at(-1)?.stream_id ?? null,
        has_more: false,
        order: "newest_revision_first",
      };
    } else if (url.pathname === "/api/v1/source-freshness") {
      response = makeSourceFreshnessResponse();
    } else if (url.pathname === "/api/v1/source-polls") {
      response = makeSourcePollHistoryResponse();
    } else if (url.pathname === "/api/v1/incidents") {
      response = {
        count: 0,
        total_incidents: 0,
        items: [],
        incidents_truncated: false,
        candidate_edges_truncated: false,
        rule_version: "spatiotemporal-correlation-v1",
        caveat: "Synthetic browser-smoke fixture; no incident claim is made.",
        relationship_rule_version: "claim-relationships-v1",
        relationship_caveat: "Synthetic browser-smoke fixture; no relationship claim is made.",
        parameters: {
          radius_km: 75,
          time_window_minutes: 180,
          lookback_hours: 24,
          candidate_edge_limit: 1_000,
          incident_limit: 100,
          active_only: true,
          bbox: null,
        },
      };
    } else {
      state.unexpectedApiPaths.push(url.pathname);
      await route.fulfill({
        status: 501,
        contentType: "application/json",
        body: JSON.stringify({ detail: `Unhandled browser-smoke route: ${url.pathname}` }),
      });
      return;
    }

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(response),
    });
  });

  return state;
}

test("production dashboard boots against strict public contracts", async ({ page }) => {
  const pageErrors: string[] = [];
  const policyViolations: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("console", (message) => {
    const text = message.text();
    if (
      message.type() === "error" &&
      (text.includes("Content Security Policy") || text.includes("violates the following"))
    ) {
      policyViolations.push(text);
    }
  });
  const routes = await installContractRoutes(page);

  const documentResponse = await page.goto("/");
  if (!documentResponse) throw new Error("dashboard navigation returned no document response");
  const documentHeaders = documentResponse.headers();
  for (const [name, expected] of Object.entries(securityHeaders)) {
    expect(documentHeaders[name.toLowerCase()]).toBe(expected);
  }

  await expect(page.getByRole("link", { name: "AtlasPulse home" })).toBeVisible();
  await expect(page.getByText("SOURCES CURRENT", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Poll heartbeat and upstream age" }),
  ).toBeVisible();
  await expect(page.getByText(/3 \/ 3 sources pass/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Recent poll transitions" })).toBeVisible();
  await expect(page.getByText("3 retained", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Live disruption map")).toBeVisible();

  const signal = page.getByRole("button", { name: /Smoke Test Ridge/ });
  await expect(signal).toBeVisible();
  await signal.click();

  const detail = page.getByLabel("Selected signal details");
  await expect(detail).toBeVisible();
  await expect(detail.getByRole("heading", { name: /Smoke Test Ridge/ })).toBeVisible();
  await expect(detail.getByText("us-smoke-browser", { exact: true })).toBeVisible();
  await expect(detail.getByText("2100000000000-0", { exact: true })).toBeVisible();

  const filteredRequest = page.waitForRequest((request) => {
    const url = new URL(request.url());
    return url.pathname === "/api/v1/signals" && url.searchParams.get("source") === "usgs";
  });
  await page.getByRole("button", { name: "Earthquakes", exact: true }).click();
  await filteredRequest;
  await expect(signal).toBeVisible();

  await expect
    .poll(() =>
      routes.handledApiUrls.some((requestUrl) => {
        const url = new URL(requestUrl);
        return url.pathname === "/api/v1/signals" && url.searchParams.has("bbox");
      }),
    )
    .toBe(true);
  expect(routes.unexpectedApiPaths).toEqual([]);
  expect(policyViolations).toEqual([]);
  expect(pageErrors).toEqual([]);
});
