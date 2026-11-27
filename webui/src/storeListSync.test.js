import { afterEach, describe, expect, it, vi } from "vitest";
import {
  STORE_LIST_SYNC_INTERVAL_MS,
  startRepeatingSync,
} from "./storeListSync";

afterEach(() => {
  vi.useRealTimers();
});

describe("store list live sync", () => {
  it("defaults to a low-frequency refresh during long background work", () => {
    expect(STORE_LIST_SYNC_INTERVAL_MS).toBe(3000);
  });

  it("refreshes immediately and repeats until stopped", async () => {
    vi.useFakeTimers();
    const synchronize = vi.fn(async () => {});

    const stop = startRepeatingSync({
      synchronize,
      intervalMs: 1200,
    });
    await vi.advanceTimersByTimeAsync(0);
    expect(synchronize).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(1200);
    expect(synchronize).toHaveBeenCalledTimes(2);

    stop();
    await vi.advanceTimersByTimeAsync(3600);
    expect(synchronize).toHaveBeenCalledTimes(2);
  });

  it("does not overlap a slow stores.list request", async () => {
    vi.useFakeTimers();
    let finish;
    const synchronize = vi.fn(() => new Promise((resolve) => {
      finish = resolve;
    }));

    const stop = startRepeatingSync({
      synchronize,
      intervalMs: 1200,
    });
    await vi.advanceTimersByTimeAsync(0);
    expect(synchronize).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(2400);
    expect(synchronize).toHaveBeenCalledTimes(1);

    finish();
    await Promise.resolve();
    await vi.advanceTimersByTimeAsync(1200);
    expect(synchronize).toHaveBeenCalledTimes(2);
    stop();
  });
});
