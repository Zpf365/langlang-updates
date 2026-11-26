import { describe, expect, it } from "vitest";
import {
  fulfillmentStageLabel,
  orderStatusLabel,
  shippingCountdown,
} from "./orderStatus";

describe("order status labels", () => {
  it("uses the numeric platform status over stale status text", () => {
    expect(orderStatusLabel({ status: 30, status_text: "待发货" })).toBe("待收货");
    expect(orderStatusLabel({ status: 21, status_text: "待发货" })).toBe("部分发货");
  });

  it("does not call a shipped order pending when workbench stage is missing", () => {
    expect(fulfillmentStageLabel({ status: 30, fulfillment_stage: "purchased" })).toBe("待收货");
    expect(fulfillmentStageLabel({ status: 30, fulfillment_stage: "shipped" })).toBe("已发货");
    expect(fulfillmentStageLabel({ status: 20, fulfillment_stage: "purchased" })).toBe("已采购待发货");
  });

  it("shows absolute shipment deadline countdown only for status 20", () => {
    const now = 1_800_000_000;
    expect(shippingCountdown({ status: 30, shipping_deadline_at: now + 60 }, now)).toBe("");
    expect(shippingCountdown({ status: 21, shipping_deadline_at: now + 60 }, now)).toBe("");
    expect(shippingCountdown({ status: 20 }, now)).toBe("");
    expect(shippingCountdown({ status: 20, shipping_deadline_at: now - 1 }, now)).toBe("已超时");
    expect(shippingCountdown({ status: 20, shipping_deadline_at: now + 3600 }, now)).toBe("剩余 1小时");
    expect(shippingCountdown({ status: 20, shipping_deadline_at: now + 1 }, now)).toBe("剩余 1分");
  });
});
