import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const root = new URL("./", import.meta.url);
const router = readFileSync(new URL("./router.js", root), "utf8");
const app = readFileSync(new URL("./App.vue", root), "utf8");
const more = readFileSync(new URL("./pages/MorePage.vue", root), "utf8");
const stores = readFileSync(new URL("./pages/StoresPage.vue", root), "utf8");
const orders = readFileSync(new URL("./pages/OrdersParityPage.vue", root), "utf8");
const orderRow = readFileSync(new URL("./components/OrderTableRow.vue", root), "utf8");
const session = readFileSync(new URL("./session.js", root), "utf8");

describe("commercial entitlement UI gates", () => {
  it("guards every internal route with a capability", () => {
    for (const entitlement of [
      "module.automation",
      "module.finance",
      "module.storefront",
      "module.team",
      "module.delivery_ledger",
      "module.runtime_logs",
      "module.after_sales",
    ]) {
      expect(router).toContain(entitlement);
    }
    expect(router).toContain("meta: { entitlement:");
    expect(router).toContain('queryEntitlements: { after_sales: "module.after_sales" }');
    expect(router).toContain("hasEntitlement(to.meta.entitlement)");
  });

  it("hides internal entries and update controls by default", () => {
    expect(session).toContain("entitlements: {}");
    expect(session).toContain("export function hasEntitlement");
    expect(app).toContain('hasEntitlement("feature.software_update")');
    expect(app).toContain('hasEntitlement("account.view_others")');
    expect(app).toContain('hasEntitlement("module.finance")');
    expect(more).toContain("hasEntitlement(permission)");
    expect(more).toContain('hasEntitlement("module.runtime_logs")');
    expect(stores).toContain('hasEntitlement("module.store_groups")');
    expect(stores).toContain('v-if="canUseStoreGroups"');
    expect(orders).toContain('hasEntitlement("module.after_sales")');
    expect(orders).toContain('tab.key !== "after_sale"');
    expect(orderRow).toContain('v-if="canViewAfterSales"');
  });

  it("keeps required commercial navigation unconditional", () => {
    for (const route of [
      '"/shops"',
      '"/orders"',
      '"/logistics"',
      '"/statistics"',
      '"/managed-products"',
      '"/product-upload"',
      '"/procurement-accounts"',
      '"/violations"',
    ]) {
      expect(router).toContain(`path: ${route}`);
    }
  });
});
