export const STORE_LIST_SYNC_INTERVAL_MS = 3000;
export const STORE_REFRESH_POLL_MS = 750;
const STORE_REFRESH_TERMINAL_STATUSES = new Set([
  "completed",
  "partial",
  "failed",
  "timeout",
  "cancelled",
]);

export async function pollStoreRefreshJob({
  jobId,
  getStatus,
  onStatus = () => {},
  signal,
  sleep = (delay) => new Promise((resolve) => setTimeout(resolve, delay)),
  pollMs = STORE_REFRESH_POLL_MS,
}) {
  while (true) {
    if (signal?.aborted) {
      throw new DOMException("店铺页面已离开，停止读取刷新状态", "AbortError");
    }
    const status = await getStatus(jobId);
    onStatus(status);
    const statusName = String(status?.status || "");
    if (
      STORE_REFRESH_TERMINAL_STATUSES.has(statusName)
      && !["failed", "timeout"].includes(statusName)
    ) {
      return status;
    }
    if (["failed", "timeout"].includes(statusName)) {
      throw new Error(
        status.message
        || (statusName === "timeout" ? "店铺刷新等待超时" : "店铺刷新失败"),
      );
    }
    if (signal?.aborted) {
      throw new DOMException("店铺页面已离开，停止读取刷新状态", "AbortError");
    }
    await sleep(pollMs);
  }
}

export function storeRefreshActivityText(status = {}) {
  const state = String(status.status || "");
  if (state === "cancelled" || status.cancel_requested) {
    const completed = Number(status.completed || 0);
    const total = Number(status.total || 0);
    const cancelled = Number(status.cancelled || 0);
    if (state === "cancelled") {
      return `刷新已取消：完成 ${completed}/${total}，未开始 ${cancelled}`;
    }
    return `正在取消刷新：${status.active || 0} 家处理中，`
      + `${status.queued || 0} 家等待停止`;
  }
  if (state === "running" || state === "starting") {
    return `正在刷新 ${Number(status.active || 0)} 家 · `
      + `等待 ${Number(status.queued || 0)} 家`;
  }
  return String(status.message || "");
}

export async function cancelStoreRefreshJob({
  jobId,
  cancel,
  reason = "page_leave",
}) {
  if (!jobId) return null;
  return cancel(jobId, reason);
}

export function shouldApplyStoreListSnapshot(previous, next) {
  return JSON.stringify({
    stores: previous?.stores || [],
    groups: previous?.groups || [],
  }) !== JSON.stringify({
    stores: next?.stores || [],
    groups: next?.groups || [],
  });
}

export function scheduleSingleFollowup({
  timeoutMs,
  run,
  setTimer = setTimeout,
  clearTimer = clearTimeout,
}) {
  let timer = null;
  let stopped = false;

  function clear() {
    if (timer !== null) {
      clearTimer(timer);
      timer = null;
    }
  }

  function schedule() {
    clear();
    if (stopped) return;
    const scheduledTimer = setTimer(() => {
      if (stopped || timer !== scheduledTimer) return;
      timer = null;
      void run();
    }, Math.max(0, Number(timeoutMs) || 0));
    timer = scheduledTimer;
  }

  function stop() {
    stopped = true;
    clear();
  }

  return { schedule, stop };
}

export function createPageActivityLifecycle({
  pause = () => {},
  resume = async () => {},
} = {}) {
  let active = true;
  let activationPromise = null;
  let lifecycleEpoch = 0;

  function capture() {
    return lifecycleEpoch;
  }

  function isCurrent(token) {
    return active && Number(token) === lifecycleEpoch;
  }

  function deactivate() {
    const wasActive = active;
    active = false;
    lifecycleEpoch += 1;
    activationPromise = null;
    if (wasActive) pause();
    return wasActive;
  }

  function activate() {
    if (active) return activationPromise ?? Promise.resolve(false);
    active = true;
    const token = ++lifecycleEpoch;
    const isCurrent = () => (
      active
      && token === lifecycleEpoch
    );
    const activation = (async () => {
      await resume({ token, isCurrent });
      return isCurrent();
    })();
    let trackedActivation;
    trackedActivation = activation.finally(() => {
      if (activationPromise === trackedActivation) {
        activationPromise = null;
      }
    });
    activationPromise = trackedActivation;
    return trackedActivation;
  }

  return {
    activate,
    capture,
    deactivate,
    isActive: () => active,
    isCurrent,
  };
}

export function createPageRequestScope({ capture, isCurrent }) {
  async function run({
    token = capture(),
    start = () => {},
    request,
    success = () => {},
    error = () => {},
    finish = () => {},
  }) {
    if (!isCurrent(token)) {
      return { current: false, stale: true };
    }
    start();
    let value;
    let failure;
    let failed = false;
    try {
      value = await request();
    } catch (cause) {
      failure = cause;
      failed = true;
    }
    if (!isCurrent(token)) {
      return {
        current: false,
        stale: true,
        ...(failed ? { error: failure } : { value }),
      };
    }
    try {
      if (failed) error(failure);
      else success(value);
    } finally {
      if (isCurrent(token)) finish();
    }
    return {
      current: true,
      stale: false,
      ...(failed ? { error: failure } : { value }),
    };
  }

  return { capture, isCurrent, run };
}

export function createPageTimer({
  capture,
  isCurrent,
  onFire,
  intervalMs = 60000,
  setTimer = setInterval,
  clearTimer = clearInterval,
}) {
  let timer = null;

  function stop() {
    if (timer !== null) {
      clearTimer(timer);
      timer = null;
    }
  }

  function start(token = capture()) {
    stop();
    if (!isCurrent(token)) return false;
    const timerId = setTimer(() => {
      if (!isCurrent(token)) {
        clearTimer(timerId);
        if (timer === timerId) timer = null;
        return;
      }
      void onFire(token);
    }, intervalMs);
    timer = timerId;
    return true;
  }

  return {
    isRunning: () => timer !== null,
    start,
    stop,
  };
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
  loginBusy = storeActionBusy,
  openBackendBusy = storeActionBusy,
} = {}) {
  const loginUnavailable = !hasSelection || Boolean(loginBusy);
  const openBackendUnavailable = !hasSelection || Boolean(openBackendBusy);
  return {
    filtersEnabled: true,
    groupsEnabled: true,
    selectionEnabled: true,
    loginActionDisabled: loginUnavailable,
    openBackendActionDisabled: openBackendUnavailable,
    selectionActionDisabled: loginUnavailable || openBackendUnavailable,
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
