export const STORE_LIST_SYNC_INTERVAL_MS = 3000;
export const STORE_REFRESH_POLL_MS = 750;

export async function pollStoreRefreshJob({
  jobId,
  getStatus,
  onStatus = () => {},
  sleep = (delay) => new Promise((resolve) => setTimeout(resolve, delay)),
  pollMs = STORE_REFRESH_POLL_MS,
}) {
  while (true) {
    const status = await getStatus(jobId);
    onStatus(status);
    if (["completed", "partial"].includes(status?.status)) {
      return status;
    }
    if (status?.status === "failed") {
      throw new Error(status.message || "店铺刷新失败");
    }
    await sleep(pollMs);
  }
}

export async function runStoreDateRolloverReload({
  loadedDateKey,
  currentDateKey,
  loading = false,
  automatic = false,
  load,
  refresh,
  onLoaded = () => {},
}) {
  if (!loadedDateKey || loading || loadedDateKey === currentDateKey) {
    return { reloaded: false, mode: "" };
  }
  if (automatic) {
    await refresh({ automatic: true });
  } else {
    await load();
  }
  onLoaded(currentDateKey);
  return { reloaded: true, mode: automatic ? "refresh" : "load" };
}

export function getStoreInteractionState({
  hasSelection = false,
  storeActionBusy = false,
} = {}) {
  const actionUnavailable = !hasSelection || Boolean(storeActionBusy);
  return {
    filtersEnabled: true,
    groupsEnabled: true,
    selectionEnabled: true,
    selectionActionDisabled: actionUnavailable,
  };
}

export function startRepeatingSync({
  synchronize,
  intervalMs = STORE_LIST_SYNC_INTERVAL_MS,
  setTimer = setTimeout,
  clearTimer = clearTimeout,
  onError = () => {},
}) {
  let stopped = false;
  let timer = null;
  let running = false;
  const accountHandoffHandler = () => stop();

  function stop() {
    stopped = true;
    if (timer !== null) {
      clearTimer(timer);
      timer = null;
    }
    if (typeof window !== "undefined") {
      window.removeEventListener(
        "langlang-account-handoff",
        accountHandoffHandler,
      );
    }
  }

  async function tick() {
    if (stopped) return;
    if (!running) {
      running = true;
      try {
        await synchronize();
      } catch (error) {
        onError(error);
      } finally {
        running = false;
      }
    }
    if (!stopped) {
      timer = setTimer(tick, intervalMs);
    }
  }

  if (typeof window !== "undefined") {
    window.addEventListener(
      "langlang-account-handoff",
      accountHandoffHandler,
    );
  }
  void tick();
  return stop;
}
