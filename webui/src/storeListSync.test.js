import { afterEach, describe, expect, it, vi } from "vitest";
import {
  STORE_LIST_SYNC_INTERVAL_MS,
  getStoreInteractionState,
  startRepeatingSync,
} from "./storeListSync";

afterEach(() => {
  vi.useRealTimers();
});

describe("store list live sync", () => {
  it("keeps login available while a backend window is opening", () => {
    const state = getStoreInteractionState({
      hasSelection: true,
      loginBusy: false,
      openBackendBusy: true,
    });

    expect(state.loginActionDisabled).toBe(false);
    expect(state.openBackendActionDisabled).toBe(true);
    expect(state.filtersEnabled).toBe(true);
    expect(state.groupsEnabled).toBe(true);
    expect(state.selectionEnabled).toBe(true);
  });

  it("blocks duplicate login and backend opens independently", () => {
    const loginBusy = getStoreInteractionState({
      hasSelection: true,
      loginBusy: true,
      openBackendBusy: false,
    });
    const backendBusy = getStoreInteractionState({
      hasSelection: true,
      loginBusy: false,
      openBackendBusy: true,
    });

    expect(loginBusy.loginActionDisabled).toBe(true);
    expect(loginBusy.openBackendActionDisabled).toBe(false);
    expect(backendBusy.loginActionDisabled).toBe(false);
    expect(backendBusy.openBackendActionDisabled).toBe(true);
  });

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
