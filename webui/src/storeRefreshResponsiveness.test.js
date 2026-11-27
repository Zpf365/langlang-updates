import { describe, expect, it, vi } from "vitest";

import * as storeSync from "./storeListSync";


describe("store refresh responsiveness", () => {
  it("polls the active refresh until it reaches a terminal status", async () => {
    expect(storeSync.pollStoreRefreshJob).toBeTypeOf("function");
    if (!storeSync.pollStoreRefreshJob) return;
    const statuses = [
      { status: "running", current: 1, total: 3 },
      { status: "completed", current: 3, total: 3 },
    ];
    const observed = [];
    const sleeps = [];

    const result = await storeSync.pollStoreRefreshJob({
      jobId: "job-1",
      getStatus: async (jobId) => {
        expect(jobId).toBe("job-1");
        return statuses.shift();
      },
      onStatus: (status) => observed.push(status.status),
      sleep: async (delay) => sleeps.push(delay),
      pollMs: 750,
    });

    expect(result.status).toBe("completed");
    expect(observed).toEqual(["running", "completed"]);
    expect(sleeps).toEqual([750]);
  });

  it("reloads exactly once after the Shanghai business date changes", async () => {
    expect(storeSync.runStoreDateRolloverReload).toBeTypeOf("function");
    if (!storeSync.runStoreDateRolloverReload) return;
    const loaded = [];
    const load = vi.fn(async () => {});
    const refresh = vi.fn(async () => {});

    const result = await storeSync.runStoreDateRolloverReload({
      loadedDateKey: "2026-11-26",
      currentDateKey: "2026-11-27",
      loading: false,
      automatic: false,
      load,
      refresh,
      onLoaded: (dateKey) => loaded.push(dateKey),
    });

    expect(result).toEqual({ reloaded: true, mode: "load" });
    expect(load).toHaveBeenCalledTimes(1);
    expect(refresh).not.toHaveBeenCalled();
    expect(loaded).toEqual(["2026-11-27"]);

    const unchanged = await storeSync.runStoreDateRolloverReload({
      loadedDateKey: "2026-11-27",
      currentDateKey: "2026-11-27",
      load,
      refresh,
      onLoaded: (dateKey) => loaded.push(dateKey),
    });
    expect(unchanged.reloaded).toBe(false);
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("keeps status filters, groups and row selection interactive while loading", () => {
    expect(storeSync.getStoreInteractionState).toBeTypeOf("function");
    if (!storeSync.getStoreInteractionState) return;

    const loading = storeSync.getStoreInteractionState({
      loading: true,
      hasSelection: false,
      storeActionBusy: false,
    });
    const busyAction = storeSync.getStoreInteractionState({
      loading: false,
      hasSelection: true,
      storeActionBusy: true,
    });

    expect(loading).toEqual({
      filtersEnabled: true,
      groupsEnabled: true,
      selectionEnabled: true,
      selectionActionDisabled: true,
    });
    expect(busyAction.selectionActionDisabled).toBe(true);
  });
});
