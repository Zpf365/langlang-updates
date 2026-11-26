import {
  computed,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "vue";
import { withActionOrigin } from "./api";

export const AUTO_REFRESH_INTERVAL_MS = 600000;
export const STARTUP_REFRESH_STAGE_TIMEOUT_MS = 600000;

const STARTUP_ACTIVE_STATUSES = new Set(["starting", "running"]);
const STARTUP_STAGE_TERMINAL_STATUSES = new Set([
  "completed",
  "partial",
  "failed",
  "timeout",
  "cancelled",
]);

export function getStartupRefreshStage(status, name) {
  const stage = status?.stages?.[name];
  return stage && typeof stage === "object" ? stage : null;
}

export async function waitForStartupRefreshStage({
  name,
  readStatus,
  onStatus = () => {},
  isApplicable = () => true,
  timeoutMs = STARTUP_REFRESH_STAGE_TIMEOUT_MS,
  pollIntervalMs = 250,
  sleep = (delay) => new Promise((resolve) => setTimeout(resolve, delay)),
  now = () => Date.now(),
}) {
  const stageName = String(name || "").trim();
  if (!stageName) throw new Error("启动刷新阶段名称不能为空");
  if (typeof readStatus !== "function") {
    throw new Error("读取启动刷新状态的方法不能为空");
  }

  const startedAt = now();
  while (true) {
    const status = await readStatus();
    if (!status || typeof status !== "object") {
      return {
        available: false,
        stage: null,
        status: null,
        timedOut: false,
      };
    }
    onStatus(status);
    if (!isApplicable(status)) {
      return {
        available: false,
        stage: getStartupRefreshStage(status, stageName),
        status,
        timedOut: false,
      };
    }
    const stage = getStartupRefreshStage(status, stageName);
    if (
      stage
      && STARTUP_STAGE_TERMINAL_STATUSES.has(String(stage.status || ""))
    ) {
      return {
        available: true,
        stage,
        status,
        timedOut: false,
      };
    }

    const active = Boolean(status.thread_alive)
      || STARTUP_ACTIVE_STATUSES.has(String(status.status || ""));
    if (!active) {
      return {
        available: Boolean(stage),
        stage,
        status,
        timedOut: false,
      };
    }

    const elapsed = Math.max(0, now() - startedAt);
    if (elapsed >= timeoutMs) {
      return {
        available: Boolean(stage),
        stage,
        status,
        timedOut: true,
      };
    }
    await sleep(Math.min(pollIntervalMs, Math.max(0, timeoutMs - elapsed)));
  }
}

export function createAutoRefreshScheduler({
  refresh,
  intervalMs = AUTO_REFRESH_INTERVAL_MS,
  enabled = true,
  isBusy = () => false,
  onState = () => {},
  setTimer = setTimeout,
  clearTimer = clearTimeout,
  now = () => Date.now(),
}) {
  let active = Boolean(enabled);
  let stopped = false;
  let running = false;
  let manual = false;
  let timer = null;
  let nextRunAt = null;

  function publish(status, details = {}) {
    onState({
      status,
      running,
      manual,
      nextRunAt,
      ...details,
    });
  }

  function cancelTimer() {
    if (timer !== null) {
      clearTimer(timer);
      timer = null;
    }
  }

  function schedule(delay = intervalMs) {
    cancelTimer();
    if (stopped || !active) {
      nextRunAt = null;
      publish("disabled");
      return;
    }
    nextRunAt = now() + Math.max(0, delay);
    publish("scheduled");
    timer = setTimer(run, Math.max(0, delay));
  }

  async function run() {
    timer = null;
    if (stopped || !active) return;
    if (running || manual || isBusy()) {
      publish("skipped", { message: "上一次操作尚未结束，本轮自动刷新已顺延" });
      schedule();
      return;
    }
    running = true;
    nextRunAt = null;
    publish("running");
    try {
      const result = await withActionOrigin(
        "auto_refresh",
        refresh,
      );
      publish("success", { result });
    } catch (error) {
      publish("failed", {
        error,
        message: error instanceof Error ? error.message : String(error),
      });
    } finally {
      running = false;
      if (!stopped && active && !manual) schedule();
    }
  }

  function start() {
    stopped = false;
    schedule();
    return controller;
  }

  function stop() {
    stopped = true;
    cancelTimer();
    nextRunAt = null;
    publish("stopped");
  }

  function beginManual() {
    if (running) return false;
    manual = true;
    cancelTimer();
    nextRunAt = null;
    publish("manual");
    return true;
  }

  function endManual() {
    manual = false;
    if (!stopped) schedule();
  }

  function setEnabled(value) {
    active = Boolean(value);
    if (!active) {
      cancelTimer();
      nextRunAt = null;
      publish("disabled");
    } else if (!stopped && !manual && !running) {
      schedule();
    }
  }

  function pause() {
    cancelTimer();
  }

  function resume() {
    if (stopped || !active || manual || running) return;
    if (nextRunAt === null) {
      schedule();
      return;
    }
    cancelTimer();
    timer = setTimer(
      run,
      Math.max(0, nextRunAt - now()),
    );
  }

  const controller = {
    start,
    stop,
    beginManual,
    endManual,
    setEnabled,
    pause,
    resume,
    getNextRunAt: () => nextRunAt,
    isRunning: () => running,
    isManual: () => manual,
  };
  return controller;
}

export function readAutoRefreshEnabled(storage, key) {
  try {
    const value = storage?.getItem(key);
    if (value === "0") return false;
    if (value === "1") return true;
  } catch {
    // Storage can be unavailable in private or test environments.
  }
  return true;
}

export function writeAutoRefreshEnabled(storage, key, enabled) {
  try {
    storage?.setItem(key, enabled ? "1" : "0");
  } catch {
    // Keep the in-memory setting when local storage is unavailable.
  }
}

export function useAutoRefresh({
  storageKey,
  refresh,
  isBusy = () => false,
  intervalMs = AUTO_REFRESH_INTERVAL_MS,
  storage = globalThis.localStorage,
}) {
  const enabled = ref(readAutoRefreshEnabled(storage, storageKey));
  const running = ref(false);
  const manualRunning = ref(false);
  const status = ref("idle");
  const message = ref("");
  const nextRunAt = ref(null);
  const now = ref(Date.now());
  let scheduler = null;
  let clockTimer = null;
  let visibilityHandler = null;
  let accountHandoffHandler = null;

  const secondsRemaining = computed(() => {
    if (!enabled.value || !nextRunAt.value) return null;
    return Math.max(0, Math.ceil((nextRunAt.value - now.value) / 1000));
  });
  const countdownText = computed(() => {
    if (!enabled.value) return "已关闭";
    if (running.value) return "自动刷新中";
    if (secondsRemaining.value === null) return "等待调度";
    const minutes = Math.floor(secondsRemaining.value / 60);
    const seconds = secondsRemaining.value % 60;
    return `${minutes}:${String(seconds).padStart(2, "0")} 后刷新`;
  });
  const statusText = computed(() => {
    if (status.value === "failed") return message.value || "自动刷新失败";
    if (status.value === "success") return "自动刷新完成";
    if (status.value === "skipped") return message.value || "上一轮未结束，已顺延";
    if (running.value) return "自动刷新中";
    return enabled.value ? "每 10 分钟" : "已关闭";
  });

  function handleState(value) {
    running.value = Boolean(value.running);
    manualRunning.value = Boolean(value.manual);
    status.value = String(value.status || "");
    nextRunAt.value = value.nextRunAt ?? null;
    if (value.message) message.value = String(value.message);
    else if (value.status === "success") message.value = "";
  }

  function startScheduler() {
    scheduler = createAutoRefreshScheduler({
      refresh,
      intervalMs,
      enabled: enabled.value,
      isBusy,
      onState: handleState,
    });
    scheduler.start();
  }

  function tickClock() {
    now.value = Date.now();
  }

  onMounted(() => {
    startScheduler();
    clockTimer = window.setInterval(tickClock, 1000);
    visibilityHandler = () => {
      if (document.hidden) scheduler?.pause();
      else scheduler?.resume();
    };
    accountHandoffHandler = () => {
      scheduler?.stop();
      scheduler = null;
      status.value = "paused";
      message.value = "账户切换，自动刷新已暂停";
      nextRunAt.value = null;
      running.value = false;
      if (clockTimer !== null) {
        window.clearInterval(clockTimer);
        clockTimer = null;
      }
    };
    document.addEventListener("visibilitychange", visibilityHandler);
    window.addEventListener("langlang-account-handoff", accountHandoffHandler);
  });

  watch(enabled, (value) => {
    writeAutoRefreshEnabled(storage, storageKey, value);
    scheduler?.setEnabled(value);
  });

  onBeforeUnmount(() => {
    scheduler?.stop();
    scheduler = null;
    if (clockTimer !== null) {
      window.clearInterval(clockTimer);
      clockTimer = null;
    }
    if (visibilityHandler) {
      document.removeEventListener("visibilitychange", visibilityHandler);
      visibilityHandler = null;
    }
    if (accountHandoffHandler) {
      window.removeEventListener("langlang-account-handoff", accountHandoffHandler);
      accountHandoffHandler = null;
    }
  });

  return {
    enabled,
    running,
    manualRunning,
    status,
    message,
    nextRunAt,
    secondsRemaining,
    countdownText,
    statusText,
    beginManual: () => scheduler?.beginManual() ?? true,
    endManual: () => scheduler?.endManual(),
    toggle: () => {
      enabled.value = !enabled.value;
    },
  };
}
