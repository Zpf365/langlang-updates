import { describe, expect, it } from "vitest";
import { matchesOrderSearch, parseOrderSearch } from "./orderSearch";

describe("order search", () => {
  const order = {
    order_id: "ORDER-1001",
    buyer_name: "测试买家",
    phone: "13800000000",
    item_summary: "测试商品",
    store_name: "测试店铺",
    waybill_id: "WAYBILL-9",
    purchase_order_no: "PURCHASE-8",
    pickup_code: "A-001",
    purchase_orders: [
      {
        external_order_id: "PURCHASE-ALSO-8",
        mail_no: "MAIL-7",
      },
    ],
  };

  it("splits whitespace and comma separated order numbers", () => {
    expect(parseOrderSearch("ORDER-1, ORDER-2；ORDER-3")).toEqual([
      "order-1",
      "order-2",
      "order-3",
    ]);
  });

  it("matches when any complete order token matches any supported identifier", () => {
    expect(matchesOrderSearch(order, "NOT-FOUND ORDER-1001")).toBe(true);
    expect(matchesOrderSearch(order, "NOT-FOUND PURCHASE-8")).toBe(true);
    expect(matchesOrderSearch(order, "NOT-FOUND MAIL-7")).toBe(true);
    expect(matchesOrderSearch(order, "NOT-FOUND A-001")).toBe(true);
  });

  it("rejects when no token matches", () => {
    expect(matchesOrderSearch(order, "NOT-FOUND UNKNOWN-2")).toBe(false);
  });
});
