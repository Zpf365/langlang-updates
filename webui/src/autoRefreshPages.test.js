import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const stores = readFileSync(
  new URL("./pages/StoresPage.vue", import.meta.url),
  "utf8",
);
const orders = readFileSync(
  new URL("./pages/OrdersParityPage.vue", import.meta.url),
  "utf8",
);

describe("store and order auto refresh integration", () => {
  it("uses the shared composable with persisted independent switches", () => {
    expect(stores).toContain('import { useAutoRefresh } from "../autoRefresh";');
    expect(orders).toMatch(
      /import \{\s*useAutoRefresh[\s\S]*?\} from "\.\.\/autoRefresh";/,
    );
    expect(stores).toContain('storageKey: "langlang.auto-refresh.stores"');
    expect(orders).toContain('storageKey: "langlang.auto-refresh.orders"');
    expect(stores).toContain("autoRefresh.beginManual()");
    expect(orders).toContain("autoRefresh.beginManual()");
    expect(stores).toContain("autoRefresh.endManual()");
    expect(orders).toContain("autoRefresh.endManual()");
    expect(stores).toContain("autoRefresh.countdownText.value");
    expect(orders).toContain("autoRefresh.countdownText.value");
  });

  it("calls the same real RPC as manual refresh with current scope", () => {
    expect(stores).toContain("refreshAll({ automatic: true })");
    expect(stores).toContain('call("stores.refresh.start"');
    expect(stores).toContain("store_ids: storeIds.length ? storeIds : undefined");
    expect(orders).toContain("syncOrders({ automatic: true })");
    expect(orders).toContain('call("orders.sync"');
    expect(orders).toContain("store_id: filters.store_id || undefined");
    expect(orders).toContain("等待本轮结束");
  });

  it("does not replace quick local refresh with automatic remote sync", () => {
    expect(orders).toContain("quickRefreshOrders");
    expect(orders).toContain('call("orders.quick-refresh.start"');
    expect(orders).toContain("快速刷新");
  });
});
