import { afterEach, describe, expect, it, vi } from "vitest";
import { createOrderCountdownClock } from "./orderCountdownClock";
import { shippingCountdown } from "./orderStatus";

describe("order countdown clock", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("uses one fake timer for many orders and applies server skew", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-25T00:00:00Z"));
    let monotonic = 100;
    const values = [];
    const clock = createOrderCountdownClock({
      monotonicNow: () => monotonic,
      wallNow: () => Date.now() / 1000,
    });
    clock.subscribe((value) => values.push(value));
    clock.setServerTime(1_800_000_000);
    clock.start();
    const orders = [
      { status: 20, shipping_deadline_at: 1_800_001_380 },
      { status: 20, shipping_deadline_at: 1_800_086_400 },
    ];
    expect(vi.getTimerCount()).toBe(1);
    expect(shippingCountdown(orders[0], clock.nowSeconds())).toBe("剩余 23分");
    monotonic += 60;
    await vi.advanceTimersByTimeAsync(1000);
    expect(shippingCountdown(orders[0], clock.nowSeconds())).toBe("剩余 22分");
    expect(shippingCountdown(orders[1], clock.nowSeconds())).toBe("剩余 23小时59分");
    expect(vi.getTimerCount()).toBe(1);
    clock.stop();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("keeps the absolute server anchor while hidden and restores without reset", () => {
    let monotonic = 10;
    const values = [];
    const clock = createOrderCountdownClock({
      monotonicNow: () => monotonic,
    });
    clock.subscribe((value) => values.push(value));
    clock.setServerTime(2_000_000_000);
    clock.start();
    monotonic += 3_600;
    clock.stop();
    clock.start();
    expect(values.at(-1)).toBe(2_000_003_600);
    expect(clock.start()).toBe(false);
  });

  it("does not turn missing deadlines into fake countdowns", () => {
    expect(shippingCountdown({ status: 20 }, 1_800_000_000)).toBe("");
  });
});
