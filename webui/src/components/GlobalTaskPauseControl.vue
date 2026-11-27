<script setup>
import {
  computed,
  onBeforeUnmount,
  onMounted,
  ref,
} from "vue";

import { call } from "../api";
import {
  applyTaskControlReadFailure,
  applyTaskControlSnapshot,
  globalTaskPauseState as state,
  setTaskControlActionError,
} from "../globalTaskPauseState";
import ModalDialog from "./ModalDialog.vue";

const props = defineProps({
  request: { type: Function, default: null },
  pollIntervalMs: { type: Number, default: 5000 },
});

const confirmOpen = ref(false);
let pollTimer = null;
let requestEpoch = 0;
let statusSequence = 0;
let disposed = false;

const busy = computed(() => Boolean(state.action));
const buttonState = computed(() => {
  if (state.action === "pausing") return "pausing";
  if (state.action === "resuming") return "resuming";
  if (!state.loaded || state.paused === null) return "unknown";
  return state.paused ? "paused" : "active";
});
const buttonLabel = computed(() => {
  if (state.action === "pausing") return "正在暂停…";
  if (state.action === "resuming") return "正在恢复…";
  if (!state.loaded) return "读取任务状态…";
  if (state.paused === true) return "继续操作";
  if (state.paused === false) return "暂停操作";
  return "状态未知";
});
const runningLabel = computed(() => {
  const running = state.running === null ? "—" : String(state.running);
  if (state.paused === true) return `已暂停 · 运行任务 ${running}`;
  if (state.paused === false) return `运行任务 ${running}`;
  return "任务状态读取失败";
});
const actionDisabled = computed(() => (
  busy.value
  || !state.loaded
  || state.paused === null
));
const retryVisible = computed(() => (
  state.loaded
  && state.paused === null
  && !busy.value
));

function request(action, payload = {}) {
  return props.request
    ? props.request(action, payload)
    : call(action, payload);
}

async function refreshStatus() {
  if (busy.value) return;
  const epoch = requestEpoch;
  const sequence = ++statusSequence;
  state.refreshing = true;
  try {
    const data = await request("tasks.status", {});
    if (
      disposed
      || epoch !== requestEpoch
      || sequence !== statusSequence
    ) return;
    applyTaskControlSnapshot(data);
  } catch (cause) {
    if (
      disposed
      || epoch !== requestEpoch
      || sequence !== statusSequence
    ) return;
    applyTaskControlReadFailure(cause);
  }
}

async function performAction(action, expectedPaused) {
  if (busy.value) return;
  requestEpoch += 1;
  statusSequence += 1;
  state.refreshing = false;
  state.error = "";
  state.action = action === "pause" ? "pausing" : "resuming";
  try {
    const data = await request(
      action === "pause" ? "tasks.pause-all" : "tasks.resume-all",
      {},
    );
    applyTaskControlSnapshot(data);
    if (state.paused !== expectedPaused) {
      setTaskControlActionError(
        action === "pause" ? "暂停未生效，请重试" : "恢复未生效，请重试",
      );
    }
  } catch (cause) {
    setTaskControlActionError(cause);
  } finally {
    state.action = "";
    confirmOpen.value = false;
  }
}

function handlePrimaryAction() {
  if (actionDisabled.value) return;
  if (state.paused === true) {
    performAction("resume", false);
    return;
  }
  confirmOpen.value = true;
}

function confirmPause() {
  if (busy.value) return;
  performAction("pause", true);
}

function invalidateInFlightStatus() {
  requestEpoch += 1;
  statusSequence += 1;
  state.refreshing = false;
}

onMounted(() => {
  disposed = false;
  window.addEventListener(
    "langlang-account-handoff",
    invalidateInFlightStatus,
  );
  refreshStatus();
  if (props.pollIntervalMs > 0) {
    pollTimer = window.setInterval(refreshStatus, props.pollIntervalMs);
  }
});

onBeforeUnmount(() => {
  disposed = true;
  invalidateInFlightStatus();
  window.removeEventListener(
    "langlang-account-handoff",
    invalidateInFlightStatus,
  );
  if (pollTimer !== null) window.clearInterval(pollTimer);
  pollTimer = null;
});

defineExpose({ refreshStatus });
</script>

<template>
  <section
    class="global-task-pause-control"
    :class="`state-${buttonState}`"
    aria-live="polite"
  >
    <button
      class="global-task-pause-action"
      type="button"
      data-testid="global-task-pause-action"
      :data-state="buttonState"
      :disabled="actionDisabled"
      :aria-busy="busy || state.refreshing"
      @click="handlePrimaryAction"
    >
      <strong>{{ buttonLabel }}</strong>
      <small>{{ runningLabel }}</small>
    </button>
    <button
      v-if="retryVisible"
      class="global-task-pause-retry"
      type="button"
      data-testid="global-task-pause-retry"
      :disabled="state.refreshing"
      @click="refreshStatus"
    >{{ state.refreshing ? "正在重试…" : "重试读取状态" }}</button>
    <p v-if="state.error" class="global-task-pause-error">{{ state.error }}</p>
  </section>

  <ModalDialog
    :open="confirmOpen"
    title="暂停全部受控操作"
    subtitle="暂停不会强制中断正在提交的平台写请求。"
    width="560px"
    mask-class="global-task-pause-mask"
    :mask-style="{
      backgroundColor: 'rgba(10, 22, 34, 0.74)',
      opacity: 1,
      backdropFilter: 'blur(2px)',
    }"
    panel-class="global-task-pause-dialog"
    :panel-style="{ backgroundColor: '#fff', opacity: 1 }"
    :z-index="2400"
    :busy="busy"
    @close="confirmOpen = false"
  >
    <div class="global-task-pause-copy">
      <p>当前请求会收尾；之后不会再启动新的受控操作。</p>
      <p>不会强行中断在途平台写；账号切换或退出登录仍可使用。</p>
      <p>恢复后只继续可恢复任务，已完成或待核实项目不会重提。</p>
    </div>
    <template #footer>
      <button class="btn" type="button" :disabled="busy" @click="confirmOpen = false">取消</button>
      <button
        class="btn primary"
        type="button"
        data-testid="global-task-pause-confirm"
        :disabled="busy"
        @click="confirmPause"
      >{{ busy ? "正在暂停…" : "确认暂停" }}</button>
    </template>
  </ModalDialog>
</template>

<style scoped>
.global-task-pause-control {
  margin: 10px 11px 3px;
  display: grid;
  gap: 5px;
}

.global-task-pause-action {
  width: 100%;
  min-height: 52px;
  padding: 9px 10px;
  display: grid;
  align-content: center;
  gap: 3px;
  border: 1px solid rgba(255, 255, 255, .14);
  border-radius: 7px;
  color: #e8f3f8;
  background: rgba(255, 255, 255, .07);
  text-align: left;
  cursor: pointer;
}

.global-task-pause-action:hover:not(:disabled) {
  background: rgba(255, 255, 255, .12);
}

.global-task-pause-action:disabled {
  cursor: default;
  opacity: .68;
}

.global-task-pause-action strong,
.global-task-pause-action small {
  display: block;
}

.global-task-pause-action strong {
  font-size: 11px;
}

.global-task-pause-action small {
  color: #a9bdca;
  font-size: 9px;
}

.state-paused .global-task-pause-action {
  border-color: rgba(230, 181, 89, .42);
  color: #ffe4a7;
  background: rgba(184, 126, 33, .1);
}

.state-unknown .global-task-pause-action {
  border-color: rgba(226, 117, 128, .36);
  color: #ffc7ce;
  background: rgba(150, 51, 63, .1);
}

.global-task-pause-retry {
  min-height: 28px;
  border: 1px solid rgba(255, 255, 255, .13);
  border-radius: 5px;
  color: #d8e6ee;
  background: rgba(255, 255, 255, .05);
  font-size: 9px;
  cursor: pointer;
}

.global-task-pause-error {
  margin: 0;
  color: #ffc3ca;
  font-size: 9px;
  line-height: 1.45;
}

.global-task-pause-copy {
  display: grid;
  gap: 8px;
}

.global-task-pause-copy p {
  margin: 0;
  color: #4c5e6c;
  font-size: 12px;
  line-height: 1.7;
}

:global(.global-task-pause-mask) {
  background: rgba(10, 22, 34, 0.74) !important;
  backdrop-filter: blur(2px);
}

:global(.global-task-pause-mask .modal-card) {
  position: relative;
  z-index: 1;
  background: #fff !important;
  opacity: 1 !important;
  isolation: isolate;
}

:global(.global-task-pause-mask.modal-enter-active),
:global(.global-task-pause-mask.modal-leave-active),
:global(.global-task-pause-mask.modal-enter-from),
:global(.global-task-pause-mask.modal-leave-to) {
  opacity: 1 !important;
  transition: none !important;
}
</style>
