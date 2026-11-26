import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  AUTO_REFRESH_INTERVAL_MS,
  createAutoRefreshScheduler,
  readAutoRefreshEnabled,
  writeAutoRefreshEnabled,
} from "./autoRefresh";
import { readFileSync } from "node:fs";

const source = readFileSync(
  new URL("./autoRefresh.js", import.meta.url),
  "utf8",
);

describe("shared auto refresh scheduler", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("uses a ten minute cycle and schedules the next run after completion", async () => {
    const refresh = vi.fn().mockResolvedValue("ok");
    const states = [];
    const scheduler = createAutoRefreshScheduler({
      refresh,
      onState: (state) => states.push(state.status),
    });

    scheduler.start();
    expect(AUTO_REFRESH_INTERVAL_MS).toBe(600000);
    expect(vi.getTimerCount()).toBe(1);
    await vi.advanceTimersByTimeAsync(599999);
    expect(refresh).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(states).toContain("success");
    expect(vi.getTimerCount()).toBe(1);

    await vi.advanceTimersByTimeAsync(600000);
    expect(refresh).toHaveBeenCalledTimes(2);
  });

  it("clears every timer when the page unmounts", async () => {
    const refresh = vi.fn().mockResolvedValue(undefined);
    const scheduler = createAutoRefreshScheduler({ refresh });

    scheduler.start();
    expect(vi.getTimerCount()).toBe(1);
    scheduler.stop();
    expect(vi.getTimerCount()).toBe(0);
    await vi.advanceTimersByTimeAsync(1200000);
    expect(refresh).not.toHaveBeenCalled();
  });

  it("skips an overlapping cycle and keeps the next schedule", async () => {
    let busy = true;
    const refresh = vi.fn().mockResolvedValue(undefined);
    const scheduler = createAutoRefreshScheduler({
      refresh,
      isBusy: () => busy,
    });

    scheduler.start();
    await vi.advanceTimersByTimeAsync(600000);
    expect(refresh).not.toHaveBeenCalled();
    busy = false;
    await vi.advanceTimersByTimeAsync(600000);
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("defers a manual operation and does not refresh immediately afterwards", async () => {
    const refresh = vi.fn().mockResolvedValue(undefined);
    const scheduler = createAutoRefreshScheduler({ refresh });

    scheduler.start();
    expect(scheduler.beginManual()).toBe(true);
    await vi.advanceTimersByTimeAsync(600000);
    expect(refresh).not.toHaveBeenCalled();
    scheduler.endManual();
    await vi.advanceTimersByTimeAsync(599999);
    expect(refresh).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("continues after failures", async () => {
    const refresh = vi.fn()
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValueOnce(undefined);
    const scheduler = createAutoRefreshScheduler({ refresh });

    scheduler.start();
    await vi.advanceTimersByTimeAsync(600000);
    expect(refresh).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(600000);
    expect(refresh).toHaveBeenCalledTimes(2);
  });

  it("can be disabled and resumed without duplicate timers", async () => {
    const refresh = vi.fn().mockResolvedValue(undefined);
    const scheduler = createAutoRefreshScheduler({ refresh });

    scheduler.start();
    scheduler.setEnabled(false);
    expect(vi.getTimerCount()).toBe(0);
    await vi.advanceTimersByTimeAsync(600000);
    expect(refresh).not.toHaveBeenCalled();
    scheduler.setEnabled(true);
    expect(vi.getTimerCount()).toBe(1);
    await vi.advanceTimersByTimeAsync(600000);
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("keeps the absolute deadline across pause and resume", async () => {
    const refresh = vi.fn().mockResolvedValue(undefined);
    const scheduler = createAutoRefreshScheduler({ refresh });

    scheduler.start();
    await vi.advanceTimersByTimeAsync(120000);
    scheduler.pause();
    await vi.advanceTimersByTimeAsync(120000);
    scheduler.resume();
    await vi.advanceTimersByTimeAsync(359999);
    expect(refresh).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("runs once immediately when resumed after the deadline", async () => {
    const refresh = vi.fn().mockResolvedValue(undefined);
    const scheduler = createAutoRefreshScheduler({ refresh });

    scheduler.start();
    await vi.advanceTimersByTimeAsync(120000);
    scheduler.pause();
    await vi.advanceTimersByTimeAsync(480000);
    scheduler.resume();
    await vi.advanceTimersByTimeAsync(0);
    expect(refresh).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(0);
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("does not create duplicate timers after repeated page switches", async () => {
    const refresh = vi.fn().mockResolvedValue(undefined);
    const scheduler = createAutoRefreshScheduler({ refresh });

    scheduler.start();
    for (let index = 0; index < 8; index += 1) {
      scheduler.pause();
      scheduler.resume();
    }
    expect(vi.getTimerCount()).toBe(1);
    await vi.advanceTimersByTimeAsync(600000);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(1);
  });

  it("persists the enabled switch locally with enabled as the default", () => {
    const values = new Map();
    const storage = {
      getItem: (key) => values.get(key) ?? null,
      setItem: (key, value) => values.set(key, value),
    };

    expect(readAutoRefreshEnabled(storage, "scope")).toBe(true);
    writeAutoRefreshEnabled(storage, "scope", false);
    expect(readAutoRefreshEnabled(storage, "scope")).toBe(false);
    writeAutoRefreshEnabled(storage, "scope", true);
    expect(readAutoRefreshEnabled(storage, "scope")).toBe(true);
  });

  it("does not reset the timer when the KeepAlive page is deactivated", () => {
    expect(source).not.toContain("onDeactivated");
    expect(source).not.toContain("onActivated");
    expect(source).toContain("nextRunAt - now()");
    expect(source).toContain("scheduler?.pause();");
    expect(source).toContain("scheduler?.resume();");
    expect(source).toContain("onBeforeUnmount(() => {");
    expect(source).toContain("scheduler?.stop();");
  });
});
