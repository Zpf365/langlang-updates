import { describe, expect, it } from "vitest";

import {
  depositDisplay,
  exposureDisplay,
  plainMoneyText,
  salesDisplay,
} from "./storeTableDisplay";

function sales(values = {}) {
  return {
    value: 5300,
    orderValue: 1,
    refundAmount: 0,
    refundOrderCount: 0,
    showRefund: false,
    title: "",
    ...values,
  };
}

describe("store table display adapter", () => {
  it("renders one enlarged sales line when refund is absent or explicitly zero", () => {
    const display = salesDisplay(sales());

    expect(display.salesText).toBe("53/1");
    expect(display.refundText).toBe("0/0");
    expect(display.showRefund).toBe(false);
    expect(display.singleLine).toBe(true);
  });

  it("keeps a zero sales line when only refund exists", () => {
    const display = salesDisplay(sales({
      value: 0,
      orderValue: 0,
      refundAmount: 1200,
      refundOrderCount: 1,
      showRefund: true,
    }));

    expect(display.salesText).toBe("0/0");
    expect(display.refundText).toBe("12/1");
    expect(display.singleLine).toBe(false);
  });

  it("distinguishes unknown pairs from real zero", () => {
    const unknown = salesDisplay(sales({
      value: null,
      orderValue: null,
      refundAmount: null,
      refundOrderCount: null,
      showRefund: true,
    }));
    const realZero = salesDisplay(sales({
      value: 0,
      orderValue: 0,
      refundAmount: 0,
      refundOrderCount: 0,
    }));

    expect(unknown.salesText).toBe("—/—");
    expect(unknown.refundText).toBe("—/—");
    expect(unknown.salesUnknown).toBe(true);
    expect(unknown.refundUnknown).toBe(true);
    expect(realZero.salesText).toBe("0/0");
    expect(realZero.salesUnknown).toBe(false);
  });

  it("formats long amounts literally without a currency symbol", () => {
    const display = salesDisplay(sales({ value: 1234567890123 }));

    expect(display.salesText).toBe("1,234,567.89万+/1");
    expect(display.salesText).not.toContain("¥");
    expect(display.salesText).not.toMatch(/销|退|单/);
  });

  it("pairs exposure people with product click counts and preserves zero", () => {
    const unknown = exposureDisplay(
      {},
      "today",
      { stale: false },
    );
    const realZero = exposureDisplay(
      {
        product_exposure_people: { today: 0 },
        exposure_click_counts: { today: 0 },
      },
      "today",
      { stale: false },
    );
    const mixed = exposureDisplay(
      {
        product_exposure_people: { today: 16754 },
        product_exposure_counts: { today: 21084 },
        exposure_click_counts: { today: 500 },
      },
      "today",
      { stale: false },
    );
    const missingClicks = exposureDisplay(
      {
        product_exposure_people: { today: 17 },
        product_exposure_counts: { today: 21 },
      },
      "today",
      { stale: false },
    );
    const long = exposureDisplay(
      {
        product_exposure_people: { today: 12345678 },
        exposure_click_counts: { today: 98765432 },
      },
      "today",
      { stale: false },
    );

    expect(unknown.text).toBe("—/—");
    expect(unknown.unknown).toBe(true);
    expect(realZero.text).toBe("0/0");
    expect(realZero.unknown).toBe(false);
    expect(mixed.text).toBe("1.67万+/500");
    expect(missingClicks.text).toBe("17/—");
    expect(long.text).toBe("1,234.56万+/9,876.54万+");
  });

  it("keeps stale exposure unknown instead of showing cached values", () => {
    const display = exposureDisplay(
      {
        product_exposure_people: { seven_days: 16754 },
        product_exposure_counts: { seven_days: 21084 },
        exposure_click_counts: { seven_days: 500 },
      },
      "seven_days",
      { stale: true, title: "数据不是今天" },
    );

    expect(display.text).toBe("—/—");
    expect(display.unknown).toBe(true);
    expect(display.title).toContain("数据不是今天");
  });

  it("formats table and detail money without a currency symbol", () => {
    expect(plainMoneyText(1234567890123)).toBe("1,234,567.89万+");
    expect(plainMoneyText(null)).toBe("—");
    expect(depositDisplay({
      metrics: { deposit: 50000 },
      deposit_meta: { source: "官方保证金接口" },
    })).toMatchObject({
      text: "500",
      title: "官方保证金接口",
    });
    expect(depositDisplay({
      metrics: { deposit: 50000 },
      deposit_meta: {},
    }).text).toBe("—");
  });

  it.each([
    [5300, "53"],
    [5350, "53.5"],
    [999999, "9,999.99"],
    [1000000, "1万"],
    [1234500, "1.23万+"],
    [1234599, "1.23万+"],
    [100000000, "100万"],
    [105500000, "105.5万"],
  ])("formats %s cents without excess zeros or rounded-up wan values", (value, expected) => {
    expect(plainMoneyText(value)).toBe(expected);
  });

  it("applies the same wan truncation to orders and exposure", () => {
    const salesWithLargeCount = salesDisplay(sales({ orderValue: 12345 }));
    const exposureWithLargeCount = exposureDisplay(
      {
        product_exposure_people: { today: 10000 },
        exposure_click_counts: { today: 12345 },
      },
      "today",
      { stale: false },
    );

    expect(salesWithLargeCount.salesText).toBe("53/1.23万+");
    expect(exposureWithLargeCount.text).toBe("1万/1.23万+");
  });
});
