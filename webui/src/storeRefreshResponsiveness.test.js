import { readFile } from "node:fs/promises";

import { afterEach, describe, expect, it, vi } from "vitest";

import * as storeSync from "./storeListSync";


describe("store refresh responsiveness", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

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

  it("stops polling after a cancelled refresh and reports terminal timeouts", async () => {
    const cancelled = await storeSync.pollStoreRefreshJob({
      jobId: "job-cancelled",
      getStatus: async () => ({ status: "cancelled", message: "刷新已取消" }),
      sleep: async () => {},
    });
    expect(cancelled.status).toBe("cancelled");

    await expect(storeSync.pollStoreRefreshJob({
      jobId: "job-timeout",
      getStatus: async () => ({ status: "timeout", message: "刷新等待超时" }),
      sleep: async () => {},
    })).rejects.toThrow("刷新等待超时");
  });

  it("stops requesting refresh status after the page leaves", async () => {
    const controller = new AbortController();
    const getStatus = vi.fn(async () => ({ status: "running" }));
    await expect(storeSync.pollStoreRefreshJob({
      jobId: "job-leaving",
      getStatus,
      signal: controller.signal,
      onStatus: () => controller.abort(),
      sleep: async () => {},
    })).rejects.toMatchObject({ name: "AbortError" });
    expect(getStatus).toHaveBeenCalledTimes(1);
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
      loginActionDisabled: true,
      openBackendActionDisabled: true,
      selectionActionDisabled: true,
    });
    expect(busyAction.selectionActionDisabled).toBe(true);
  });
  it("skips applying identical store list snapshots", () => {
    expect(storeSync.shouldApplyStoreListSnapshot).toBeTypeOf("function");
    if (!storeSync.shouldApplyStoreListSnapshot) return;
    const snapshot = {
      stores: [{ id: 1, name: "店铺一", metrics: { today: 10 } }],
      groups: ["默认分组"],
    };
    const same = {
      stores: [{ id: 1, name: "店铺一", metrics: { today: 10 } }],
      groups: ["默认分组"],
    };
    const changed = {
      stores: [{ id: 1, name: "店铺一", metrics: { today: 11 } }],
      groups: ["默认分组"],
    };
    expect(storeSync.shouldApplyStoreListSnapshot(snapshot, same)).toBe(false);
    expect(storeSync.shouldApplyStoreListSnapshot(snapshot, changed)).toBe(true);
  });

  it("keeps one follow-up timer and clears it when the page leaves", async () => {
    expect(storeSync.scheduleSingleFollowup).toBeTypeOf("function");
    if (!storeSync.scheduleSingleFollowup) return;
    vi.useFakeTimers();
    const run = vi.fn(async () => {});
    const followup = storeSync.scheduleSingleFollowup({
      timeoutMs: 100,
      run,
    });
    followup.schedule();
    await vi.advanceTimersByTimeAsync(50);
    followup.schedule();
    await vi.advanceTimersByTimeAsync(60);
    expect(run).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(40);
    expect(run).toHaveBeenCalledTimes(1);

    followup.schedule();
    followup.stop();
    await vi.advanceTimersByTimeAsync(200);
    expect(run).toHaveBeenCalledTimes(1);
  });

  it("wires lifecycle cleanup into the stores page", async () => {
    const source = await readFile(
      new URL("./pages/StoresPage.vue", import.meta.url),
      "utf8",
    );
    expect(source).toContain("shouldApplyStoreListSnapshot");
    expect(source).toContain("scheduleSingleFollowup");
    expect(source).toContain("hydrationFollowup?.stop()");
    expect(source).toContain("storeRefreshPollController?.abort()");
    expect(source).toContain("onDeactivated");
    expect(source).toContain("storePageActivity.deactivate()");
    expect(source).toContain("storePageActivity.activate()");
    expect(source).toContain("autoRefresh.pause()");
    expect(source).toContain("autoRefresh.resume()");
    expect(source).toContain("createPageRequestScope");
    expect(source).toContain("startDateRolloverTimer(token)");
    expect(source).toContain("reloadAfterDateRollover(token)");
    expect(source).toContain("cancelStoreRefreshJob");
    expect(source).toContain('"stores.refresh.cancel"');
    expect(source).toContain("storeRefreshActivityText(refreshProgress)");
  });

  it("describes bounded refresh activity without exposing worker details", () => {
    expect(storeSync.storeRefreshActivityText).toBeTypeOf("function");
    if (!storeSync.storeRefreshActivityText) return;

    expect(storeSync.storeRefreshActivityText({
      status: "running",
      active: 6,
      max_workers: 12,
      queued: 94,
    })).toBe("正在刷新 6 家 · 等待 94 家");
    expect(storeSync.storeRefreshActivityText({
      status: "cancelled",
      completed: 6,
      total: 100,
      cancelled: 94,
    })).toBe("刷新已取消：完成 6/100，未开始 94");
  });

  it("requests backend cancellation before discarding a page poll", async () => {
    expect(storeSync.cancelStoreRefreshJob).toBeTypeOf("function");
    if (!storeSync.cancelStoreRefreshJob) return;
    const cancel = vi.fn(async () => ({
      status: "running",
      cancel_requested: true,
    }));

    await expect(storeSync.cancelStoreRefreshJob({
      jobId: "job-page-leave",
      cancel,
    })).resolves.toMatchObject({ cancel_requested: true });
    expect(cancel).toHaveBeenCalledWith("job-page-leave", "page_leave");
  });

  it("pauses KeepAlive effects on deactivation and rebuilds on return", async () => {
    expect(storeSync.createPageActivityLifecycle).toBeTypeOf("function");
    if (!storeSync.createPageActivityLifecycle) return;
    const pause = vi.fn();
    const resume = vi.fn(async () => {});
    const lifecycle = storeSync.createPageActivityLifecycle({
      pause,
      resume,
    });

    lifecycle.deactivate();
    lifecycle.deactivate();
    expect(pause).toHaveBeenCalledTimes(1);
    expect(lifecycle.isActive()).toBe(false);

    await lifecycle.activate();
    await lifecycle.activate();
    expect(resume).toHaveBeenCalledTimes(1);
    expect(lifecycle.isActive()).toBe(true);
  });

  it("keeps the latest activation when re-entering before resume completes", async () => {
    expect(storeSync.createPageActivityLifecycle).toBeTypeOf("function");
    if (!storeSync.createPageActivityLifecycle) return;
    let finishFirstResume;
    const resumeContexts = [];
    const resume = vi.fn((context) => {
      resumeContexts.push(context);
      if (resumeContexts.length === 1) {
        return new Promise((resolve) => {
          finishFirstResume = resolve;
        });
      }
      return Promise.resolve();
    });
    const pause = vi.fn();
    const lifecycle = storeSync.createPageActivityLifecycle({
      pause,
      resume,
    });

    lifecycle.deactivate();
    const firstActivation = lifecycle.activate();
    await Promise.resolve();
    expect(resume).toHaveBeenCalledTimes(1);
    expect(resumeContexts[0].isCurrent()).toBe(true);

    lifecycle.deactivate();
    const latestActivation = lifecycle.activate();
    const repeatedActivation = lifecycle.activate();
    expect(lifecycle.isActive()).toBe(true);
    expect(repeatedActivation).toBe(latestActivation);
    expect(resumeContexts[0].isCurrent()).toBe(false);
    expect(resume).toHaveBeenCalledTimes(2);
    expect(resumeContexts[1].token).not.toBe(resumeContexts[0].token);

    finishFirstResume();
    await Promise.all([firstActivation, latestActivation]);
    expect(resume).toHaveBeenCalledTimes(2);
    expect(resumeContexts[1].isCurrent()).toBe(true);
    expect(lifecycle.isActive()).toBe(true);
  });

  it("blocks stale load, silent sync, timer and finally writes after re-entry", async () => {
    const deferred = () => {
      let resolve;
      let reject;
      const promise = new Promise((resolvePromise, rejectPromise) => {
        resolve = resolvePromise;
        reject = rejectPromise;
      });
      return { promise, reject, resolve };
    };
    const oldLoad = deferred();
    const oldSync = deferred();
    const latestLoad = deferred();
    const state = {
      stores: [],
      groups: [],
      message: "",
      loading: false,
      loadedDateKey: "",
    };
    let listRequests = 0;
    let syncRequests = 0;
    let timerCallback = null;
    let lifecycle;
    let timer;
    const timerFires = [];
    const requestScope = storeSync.createPageRequestScope({
      capture: () => lifecycle.capture(),
      isCurrent: (token) => lifecycle.isCurrent(token),
    });
    const resume = ({ token }) => {
      listRequests += 1;
      return requestScope.run({
        token,
        start: () => {
          state.loading = true;
        },
        request: () => latestLoad.promise,
        success: (data) => {
          state.stores = data.stores;
          state.groups = data.groups;
          state.loadedDateKey = data.dateKey;
          timer.start(token);
        },
        finish: () => {
          state.loading = false;
        },
      });
    };
    lifecycle = storeSync.createPageActivityLifecycle({
      pause: () => timer.stop(),
      resume,
    });
    timer = storeSync.createPageTimer({
      capture: () => lifecycle.capture(),
      isCurrent: (token) => lifecycle.isCurrent(token),
      onFire: (token) => timerFires.push(token),
      setTimer: (callback) => {
        timerCallback = callback;
        return callback;
      },
      clearTimer: () => {},
    });

    const oldToken = lifecycle.capture();
    const oldLoadResult = requestScope.run({
      token: oldToken,
      start: () => {
        state.loading = true;
      },
      request: () => {
        listRequests += 1;
        return oldLoad.promise;
      },
      success: (data) => {
        state.stores = data.stores;
      },
      finish: () => {
        state.loading = false;
      },
    });
    const oldSyncResult = requestScope.run({
      token: oldToken,
      request: () => {
        syncRequests += 1;
        return oldSync.promise;
      },
      success: () => {
        state.message = "stale sync success";
      },
      error: () => {
        state.message = "stale sync failure";
      },
    });
    timer.start(oldToken);
    const oldTimerCallback = timerCallback;

    lifecycle.deactivate();
    const latestActivation = lifecycle.activate();
    expect(listRequests).toBe(2);
    expect(syncRequests).toBe(1);

    oldLoad.resolve({
      stores: [{ id: 1, name: "旧店铺" }],
      groups: ["旧分组"],
      dateKey: "2026-11-26",
    });
    await oldLoadResult;
    oldSync.reject(new Error("旧静默同步失败"));
    await oldSyncResult;

    expect(state.stores).toEqual([]);
    expect(state.groups).toEqual([]);
    expect(state.message).toBe("");
    expect(state.loadedDateKey).toBe("");
    expect(state.loading).toBe(true);
    expect(timerFires).toEqual([]);

    latestLoad.resolve({
      stores: [{ id: 2, name: "新店铺" }],
      groups: ["新分组"],
      dateKey: "2026-11-27",
    });
    await latestActivation;
    expect(listRequests).toBe(2);
    expect(state.stores).toEqual([{ id: 2, name: "新店铺" }]);
    expect(state.groups).toEqual(["新分组"]);
    expect(state.loadedDateKey).toBe("2026-11-27");
    expect(state.loading).toBe(false);

    expect(timer.isRunning()).toBe(true);
    oldTimerCallback();
    expect(timer.isRunning()).toBe(true);
    expect(timerFires).toEqual([]);
    timerCallback();
    expect(timerFires).toEqual([lifecycle.capture()]);
  });
});
