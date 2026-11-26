import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { waitForStartupRefreshStage } from "./autoRefresh";

const page = readFileSync(
  new URL("./pages/OrdersParityPage.vue", import.meta.url),
  "utf8",
);

function sourceBlock(source, startMarker, endMarker) {
  const start = source.indexOf(startMarker);
  const end = source.indexOf(endMarker, start + startMarker.length);
  expect(start).toBeGreaterThanOrEqual(0);
  expect(end).toBeGreaterThan(start);
  return source.slice(start, end);
}

describe("order startup refresh", () => {
  it("loads the store filter before the startup order refresh", () => {
    const mount = sourceBlock(
      page,
      "onMounted(async () => {",
      "watch(activeTab, async",
    );
    const loadStoresAt = mount.indexOf("await loadStores();");
    const startupRefreshAt = mount.indexOf("await refreshOrdersAfterStartup();");

    expect(mount).toContain('message: "正在加载店铺筛选列表…"');
    expect(loadStoresAt).toBeGreaterThanOrEqual(0);
    expect(startupRefreshAt).toBeGreaterThan(loadStoresAt);
    expect(mount).not.toContain("await syncOrders({ automatic: true });");
  });

  it("keeps store loading independent from order-data failures", () => {
    const load = sourceBlock(
      page,
      "async function load({ storesLoaded = false } = {}) {",
      "function startupOrderProgressText",
    );
    const loadStoresAt = load.indexOf("await loadStores();");
    const settledAt = load.indexOf("await Promise.allSettled([");

    expect(loadStoresAt).toBeGreaterThanOrEqual(0);
    expect(settledAt).toBeGreaterThan(loadStoresAt);
    expect(load).not.toContain("await Promise.all([");
  });

  it("refreshes every store from the quick-refresh control", () => {
    const quickRefresh = sourceBlock(
      page,
      "async function quickRefreshOrders() {",
      "async function refreshOrder(order)",
    );

    expect(quickRefresh).toContain("全部店铺");
    expect(quickRefresh).not.toContain("store_id: filters.store_id");
  });

  it("waits for only the startup order stage instead of downstream products", async () => {
    const statuses = [
      {
        status: "running",
        thread_alive: true,
        stages: {
          stores: { status: "running" },
        },
      },
      {
        status: "running",
        thread_alive: true,
        stages: {
          stores: { status: "completed" },
          orders: { status: "running", result: { current: 0, total: 2 } },
        },
      },
      {
        status: "running",
        thread_alive: true,
        stages: {
          stores: { status: "completed" },
          orders: {
            status: "completed",
            result: { current: 2, total: 2, succeeded: 2 },
          },
          products: { status: "running" },
        },
      },
      {
        status: "completed",
        thread_alive: false,
        stages: {
          products: { status: "completed" },
        },
      },
    ];
    let readCount = 0;
    const progress = [];

    const result = await waitForStartupRefreshStage({
      name: "orders",
      readStatus: async () => statuses[Math.min(readCount++, statuses.length - 1)],
      onStatus: (status) => progress.push(status.stages.orders?.status || ""),
      sleep: async () => {},
    });

    expect(result.available).toBe(true);
    expect(result.stage.status).toBe("completed");
    expect(result.stage.result.succeeded).toBe(2);
    expect(readCount).toBe(3);
    expect(progress).toEqual(["", "running", "completed"]);
  });

  it("does not wait when startup order refresh is disabled", async () => {
    let readCount = 0;
    let sleepCalls = 0;

    const result = await waitForStartupRefreshStage({
      name: "orders",
      readStatus: async () => {
        readCount += 1;
        return {
          status: "running",
          thread_alive: true,
          enabled: { stores: true, orders: false },
        };
      },
      isApplicable: (status) => status.enabled?.orders !== false,
      sleep: async () => {
        sleepCalls += 1;
      },
    });

    expect(result.available).toBe(false);
    expect(result.stage).toBeNull();
    expect(readCount).toBe(1);
    expect(sleepCalls).toBe(0);
  });
});
