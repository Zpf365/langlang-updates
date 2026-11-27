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

    expect(display.salesText).toBe("53.00/1");
    expect(display.refundText).toBe("0.00/0");
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

    expect(display.salesText).toBe("0.00/0");
    expect(display.refundText).toBe("12.00/1");
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
    expect(realZero.salesText).toBe("0.00/0");
    expect(realZero.salesUnknown).toBe(false);
  });

  it("formats long amounts literally without a currency symbol", () => {
    const display = salesDisplay(sales({ value: 1234567890123 }));

    expect(display.salesText).toBe("12,345,678,901.23/1");
    expect(display.salesText).not.toContain("¥");
    expect(display.salesText).not.toMatch(/销|退|单/);
  });

  it("formats exposure people per reveal count and preserves zero", () => {
    const unknown = exposureDisplay(
      {},
      "today",
      { stale: false },
    );
    const realZero = exposureDisplay(
      {
        product_exposure_people: { today: 0 },
        product_exposure_counts: { today: 0 },
      },
      "today",
      { stale: false },
    );
    const long = exposureDisplay(
      {
        product_exposure_people: { today: 12345678 },
        product_exposure_counts: { today: 98765432 },
      },
      "today",
      { stale: false },
    );

    expect(unknown.text).toBe("—/—");
    expect(unknown.unknown).toBe(true);
    expect(realZero.text).toBe("0/0");
    expect(realZero.unknown).toBe(false);
    expect(long.text).toBe("12,345,678/98,765,432");
  });

  it("keeps stale exposure unknown instead of showing cached values", () => {
    const display = exposureDisplay(
      {
        product_exposure_people: { seven_days: 16754 },
        product_exposure_counts: { seven_days: 21084 },
      },
      "seven_days",
      { stale: true, title: "数据不是今天" },
    );

    expect(display.text).toBe("—/—");
    expect(display.unknown).toBe(true);
    expect(display.title).toContain("数据不是今天");
  });

  it("formats table and detail money without a currency symbol", () => {
    expect(plainMoneyText(1234567890123)).toBe("12,345,678,901.23");
    expect(plainMoneyText(null)).toBe("—");
    expect(depositDisplay({
      metrics: { deposit: 50000 },
      deposit_meta: { source: "官方保证金接口" },
    })).toMatchObject({
      text: "500.00",
      title: "官方保证金接口",
    });
    expect(depositDisplay({
      metrics: { deposit: 50000 },
      deposit_meta: {},
    }).text).toBe("—");
  });
});
