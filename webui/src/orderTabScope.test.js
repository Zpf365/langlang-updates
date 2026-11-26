import { readFileSync } from "node:fs";
import { computed, reactive, ref } from "vue";
import { describe, expect, it } from "vitest";
import {
  countOrdersForTab,
  filterOrdersForTabScope,
  matchesOrderTab,
} from "./orderTabScope";

const todayNoon = Math.floor(new Date(2026, 10, 23, 12, 0, 0).getTime() / 1000);
const yesterdayNoon = todayNoon - 86400;

function order(overrides) {
  return {
    order_id: "ORDER",
    store_id: 1,
    status: 20,
    create_time: todayNoon,
    item_product_id: "",
    is_marked: 0,
    after_sale_count: 0,
    status_text: "",
    purchase_orders: [],
    ...overrides,
  };
}

function filters(overrides = {}) {
  return {
    store_id: "",
    search: "",
    start_date: "",
    end_date: "",
    mark: "全部标识",
    ...overrides,
  };
}

describe("order tab counts", () => {
  it("scopes every tab to the selected store", () => {
    const rows = [
      order({ order_id: "A-WAIT", store_id: 1, status: 20 }),
      order({ order_id: "B-PARTIAL", store_id: 2, status: 17 }),
    ];

    const storeA = filterOrdersForTabScope(rows, filters({ store_id: "1" }), "");
    expect(countOrdersForTab(storeA, "")).toBe(1);
    expect(countOrdersForTab(storeA, "20")).toBe(1);

    const allStores = filterOrdersForTabScope(rows, filters(), "");
    expect(countOrdersForTab(allStores, "")).toBe(2);
    expect(countOrdersForTab(allStores, "20")).toBe(2);
  });

  it("counts both status 17 and status 20 as waiting for shipment", () => {
    const rows = [
      order({ order_id: "PARTIAL", status: 17 }),
      order({ order_id: "WAIT", status: 20 }),
      order({ order_id: "DONE", status: 100 }),
    ];
    const scope = filterOrdersForTabScope(rows, filters(), "");

    expect(countOrdersForTab(scope, "20")).toBe(2);
    expect(matchesOrderTab(rows[0], "20")).toBe(true);
    expect(matchesOrderTab(rows[1], "20")).toBe(true);
    expect(matchesOrderTab(rows[2], "20")).toBe(false);
  });

  it("applies date, search, mark, and hidden-product filters without using the active tab", () => {
    const rows = [
      order({ order_id: "TODAY-KEEP", item_product_id: "P1" }),
      order({ order_id: "TODAY-HIDDEN", item_product_id: "P2", status: 17 }),
      order({ order_id: "OLD-KEEP", item_product_id: "P3", create_time: yesterdayNoon }),
      order({ order_id: "MARKED", status: 100, is_marked: 1 }),
    ];

    const dated = filterOrdersForTabScope(
      rows,
      filters({ start_date: "2026-11-23", end_date: "2026-11-23" }),
      "",
    );
    expect(countOrdersForTab(dated, "")).toBe(3);
    expect(countOrdersForTab(dated, "20")).toBe(2);

    const searched = filterOrdersForTabScope(dated, filters({ search: "TODAY-KEEP" }), "");
    expect(countOrdersForTab(searched, "")).toBe(1);
    expect(countOrdersForTab(searched, "20")).toBe(1);

    const hidden = filterOrdersForTabScope(dated, filters(), "P2");
    expect(countOrdersForTab(hidden, "")).toBe(2);
    expect(countOrdersForTab(hidden, "20")).toBe(1);

    const marked = filterOrdersForTabScope(rows, filters({ mark: "已标记" }), "");
    expect(countOrdersForTab(marked, "")).toBe(1);
    expect(countOrdersForTab(marked, "marked")).toBe(1);
    expect(countOrdersForTab(marked, "20")).toBe(0);
  });

  it("updates immediately when a store switch or refresh changes the same computed scope", () => {
    const rowsA = [order({ order_id: "A-ONE", store_id: 1, status: 20 })];
    const rowsB = [order({ order_id: "B-ONE", store_id: 2, status: 20 })];
    const currentOrders = ref(rowsA);
    const currentFilters = reactive(filters({ store_id: "1" }));
    const scope = computed(() => (
      filterOrdersForTabScope(currentOrders.value, currentFilters, "")
    ));

    expect(countOrdersForTab(scope.value, "20")).toBe(1);
    currentOrders.value = [...rowsA, ...rowsB];
    expect(countOrdersForTab(scope.value, "20")).toBe(1);
    currentFilters.store_id = "";
    expect(countOrdersForTab(scope.value, "")).toBe(2);
    expect(countOrdersForTab(scope.value, "20")).toBe(2);
    currentOrders.value = [
      ...currentOrders.value,
      order({ order_id: "A-TWO", store_id: 1, status: 17 }),
    ];
    currentFilters.store_id = "1";
    expect(countOrdersForTab(scope.value, "20")).toBe(2);
  });

  it("wires the page and all tab badges to the non-tab scope", () => {
    const page = readFileSync(
      new URL("./pages/OrdersParityPage.vue", import.meta.url),
      "utf8",
    );

    expect(page).toContain("const tabScopeOrders = computed");
    expect(page).toContain("filterOrdersForTabScope(orders.value, filters, hiddenProductIds.value)");
    expect(page).toContain("countOrdersForTab(tabScopeOrders.value, key)");
    expect(page).toContain("{{ tabCount(tab.key) }}");
    expect(page).not.toContain("if (!key) return orders.value.length");
  });

  it("adds only one wait-purchase tab and keeps normal automation states", () => {
    const page = readFileSync(
      new URL("./pages/OrdersParityPage.vue", import.meta.url),
      "utf8",
    );
    expect(page).toContain('{ key: "wait_purchase", label: "待采购"');
    expect(page).not.toContain('label: "待关联"');
    expect(page).not.toContain('label: "待拍单"');
    expect(page).not.toContain('label: "采购中"');
    expect(matchesOrderTab(order({
      procurement_state: "unlinked",
      needs_procurement: true,
    }), "wait_purchase")).toBe(true);
    expect(matchesOrderTab(order({
      procurement_state: "wait_supplier",
      needs_procurement: false,
      status_text: "待发货",
    }), "wait_purchase")).toBe(false);
  });
});
