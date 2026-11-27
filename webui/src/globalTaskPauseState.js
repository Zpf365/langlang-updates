import { reactive } from "vue";

function countOrNull(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

export const globalTaskPauseState = reactive({
  loaded: false,
  refreshing: false,
  action: "",
  paused: null,
  running: null,
  pausedCount: 0,
  count: 0,
  revision: 0,
  error: "",
});

export function applyTaskControlSnapshot(payload = {}) {
  const paused = typeof payload.paused === "boolean" ? payload.paused : null;
  const error = String(payload.error || "");
  globalTaskPauseState.loaded = true;
  globalTaskPauseState.refreshing = false;
  globalTaskPauseState.paused = error || paused === null ? null : paused;
  globalTaskPauseState.running = countOrNull(payload.running);
  globalTaskPauseState.pausedCount = countOrNull(payload.paused_count) || 0;
  globalTaskPauseState.count = countOrNull(payload.count) || 0;
  globalTaskPauseState.revision = countOrNull(payload.revision) || 0;
  globalTaskPauseState.error = error || (
    paused === null ? "任务状态读取失败：后端未返回有效暂停状态" : ""
  );
}

export function applyTaskControlReadFailure(cause) {
  globalTaskPauseState.loaded = true;
  globalTaskPauseState.refreshing = false;
  globalTaskPauseState.paused = null;
  globalTaskPauseState.running = null;
  globalTaskPauseState.pausedCount = 0;
  globalTaskPauseState.count = 0;
  globalTaskPauseState.error = cause instanceof Error
    ? cause.message
    : String(cause || "任务状态读取失败");
}

export function setTaskControlActionError(cause) {
  globalTaskPauseState.error = cause instanceof Error
    ? cause.message
    : String(cause || "操作失败");
}

export function resetGlobalTaskPauseState() {
  Object.assign(globalTaskPauseState, {
    loaded: false,
    refreshing: false,
    action: "",
    paused: null,
    running: null,
    pausedCount: 0,
    count: 0,
    revision: 0,
    error: "",
  });
}
