<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { beginAccountHandoff, call } from "./api";
import Icon from "./components/Icon.vue";
import ModalDialog from "./components/ModalDialog.vue";
import { readAutoRefreshEnabled } from "./autoRefresh";
import {
  ensureBootstrap,
  hasEntitlement,
  resetSessionState,
  sessionState,
} from "./session";
import { normalizeUpdateSummary } from "./updateSummary";

const route = useRoute();
const router = useRouter();
const loading = ref(true);
const error = ref("");
const bootstrap = computed(() => sessionState.bootstrap || {
  brand: { name: "澜琅", version: "1.8.87" },
  primary_nav: [],
  more_nav: [],
  isolated_modules: [],
  account_label: "",
  account: {
    username: "",
    email: "",
    license: {},
    offline: false,
    member_role: "",
  },
});
const moreOpen = ref(false);
const accountMenuOpen = ref(false);
const accountAction = ref("");
const accountBusy = ref(false);
const accountTasks = ref([]);
const accountModalOpen = ref(false);
const accountHandoff = ref(false);
const accountFeedback = ref("");
const bigScreenFeedback = ref("");
const availableUpdate = ref(null);
const updateModalOpen = ref(false);
const updateChecking = ref(false);
const updateError = ref("");
const updateInProgress = ref(false);
const updateJob = ref(null);
let updateCheckTimer = null;
let updatePollTimer = null;
let bigScreenFeedbackTimer = null;
const webuiEventQueue = [];
let webuiEventFlushTimer = null;
const navEmoji = {
  stores: "🏪",
  orders: "📦",
  transit: "🚚",
  logistics: "🚛",
  statistics: "📊",
  big_screen: "🖥",
  products: "📋",
  upload: "⬆",
  supply_market: "▦",
  hosted: "⚙",
  violations: "⚠",
  procurement: "🛒",
  customer_service: "💬",
};
const sidebarUtilityLinks = computed(() => [
  ["采购账号", "shopping-cart", "/procurement-accounts"],
  ["物流监控", "radar", "/logistics"],
  ["AI客服", "message", "/customer-service"],
  ["利润统计", "chart", "/statistics"],
  ...(hasEntitlement("module.finance")
    ? [["资金账户", "wallet", "/finance"]]
    : []),
]);
const canUseSoftwareUpdate = computed(() => (
  hasEntitlement("feature.software_update")
));
const canSwitchAccounts = computed(() => (
  hasEntitlement("account.view_others")
));

const activePath = computed(() => route.path);
const accountInfo = computed(() => bootstrap.value.account || {});
const licenseLabel = computed(() => {
  const license = accountInfo.value.license || {};
  if (accountInfo.value.offline) return license.status_text || "离线宽限中";
  if (license.status_text) return license.status_text;
  if (license.permanent) return "永久授权";
  if (license.active) return license.expires_at ? `有效至 ${String(license.expires_at).slice(0, 10)}` : "授权有效";
  return "授权状态暂无数据";
});
const accountActionTitle = computed(() => (
  accountAction.value === "logout" ? "退出登录" : "切换账号"
));
const updateProgressText = computed(() => {
  const job = updateJob.value || {};
  const current = Number(job.current || 0);
  const total = Number(job.total || 0);
  if (total > 0) {
    return `${Math.min(100, Math.round((current * 100) / total))}% · ${formatBytes(current)} / ${formatBytes(total)}`;
  }
  return current > 0 ? `已处理 ${formatBytes(current)}` : "";
});
const updateProgressPercent = computed(() => {
  const job = updateJob.value || {};
  const current = Number(job.current || 0);
  const total = Number(job.total || 0);
  return total > 0 ? Math.min(100, (current * 100) / total) : 0;
});
const updateSummaryGroups = computed(() => (
  normalizeUpdateSummary(
    availableUpdate.value?.summary,
    availableUpdate.value?.notes || "",
  )
));

function isActive(item) {
  return activePath.value === item.path || activePath.value.startsWith(`${item.path}/`);
}

function navigate(item) {
  moreOpen.value = false;
  accountMenuOpen.value = false;
  router.push(item.path);
}

async function openBigScreen() {
  moreOpen.value = false;
  accountMenuOpen.value = false;
  bigScreenFeedback.value = "";
  if (bigScreenFeedbackTimer !== null) {
    window.clearTimeout(bigScreenFeedbackTimer);
    bigScreenFeedbackTimer = null;
  }
  try {
    const result = await call("dashboard.big-screen.open", {}, { origin: "manual" });
    if (result?.already_running) {
      bigScreenFeedback.value = "数据大屏正在运行";
    }
  } catch (cause) {
    console.error("数据大屏启动失败", cause);
    bigScreenFeedback.value = `数据大屏启动失败：${cause instanceof Error ? cause.message : String(cause)}`;
  }
  if (bigScreenFeedback.value) {
    bigScreenFeedbackTimer = window.setTimeout(() => {
      bigScreenFeedback.value = "";
      bigScreenFeedbackTimer = null;
    }, 5000);
  }
}

function formatBytes(value) {
  const size = Math.max(0, Number(value || 0));
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  if (size < 1024 * 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`;
  return `${(size / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function currentPageLabel() {
  const items = [
    ...(bootstrap.value.primary_nav || []),
    ...(bootstrap.value.more_nav || []),
  ];
  const matched = items.find((item) => (
    route.path === item.path
    || route.path.startsWith(`${item.path}/`)
  ));
  return matched?.label || String(route.path || "/");
}

function controlText(target) {
  return String(
    target.getAttribute("aria-label")
    || target.innerText
    || target.value
    || target.getAttribute("title")
    || target.tagName,
  ).replace(/\s+/g, " ").trim().slice(0, 160);
}

function controlSelector(target) {
  if (target.id) return `#${target.id}`;
  const testId = target.getAttribute("data-testid");
  if (testId) return `[data-testid="${testId.replaceAll('"', '\\"')}"]`;
  const aria = target.getAttribute("aria-label");
  if (aria) return `${target.tagName.toLowerCase()}[aria-label="${aria.replaceAll('"', '\\"')}"]`;
  const classes = [...target.classList].slice(0, 3).join(".");
  return `${target.tagName.toLowerCase()}${classes ? `.${classes}` : ""}`;
}

function scheduleWebuiEventFlush() {
  if (webuiEventFlushTimer !== null) return;
  webuiEventFlushTimer = window.setTimeout(() => {
    webuiEventFlushTimer = null;
    flushWebuiEvents();
  }, 700);
}

function handleBeforeUnload(event) {
  flushWebuiEvents({ keepalive: true });
  if (updateInProgress.value) {
    event.preventDefault();
    event.returnValue = "正在更新，请等待完成";
  }
}

function handleUpdateInfo(event) {
  const info = event?.detail;
  availableUpdate.value = (
    canUseSoftwareUpdate.value && info?.available
      ? info
      : null
  );
}

function startBackgroundRefresh() {
  const enabled = {
    stores: readAutoRefreshEnabled(
      globalThis.localStorage,
      "langlang.auto-refresh.stores",
    ),
    orders: readAutoRefreshEnabled(
      globalThis.localStorage,
      "langlang.auto-refresh.orders",
    ),
    products: readAutoRefreshEnabled(
      globalThis.localStorage,
      "langlang.auto-refresh.products",
    ),
    violations: readAutoRefreshEnabled(
      globalThis.localStorage,
      "langlang.auto-refresh.violations",
    ),
  };
  call(
    "startup.refresh.start",
    { enabled },
    { origin: "startup_refresh" },
  ).catch((cause) => {
    console.error("启动后台刷新失败", cause);
  });
}

async function flushWebuiEvents({ keepalive = false } = {}) {
  if (!webuiEventQueue.length) return;
  const events = webuiEventQueue.splice(0, 50);
  try {
    await call(
      "logs.webui-events",
      { events },
      { origin: "webui_logging", keepalive },
    );
  } catch {
    // UI logging must never interrupt the user's actual action.
  }
}

function trackUserClick(event) {
  const target = event.target instanceof Element
    ? event.target.closest("button, a, input, select, textarea, [role='button']")
    : null;
  if (!target) return;
  const disabled = Boolean(target.disabled)
    || target.getAttribute("aria-disabled") === "true";
  webuiEventQueue.push({
    kind: "click",
    route: route.fullPath,
    page: currentPageLabel(),
    control_text: controlText(target),
    aria_label: String(target.getAttribute("aria-label") || ""),
    selector: controlSelector(target),
    role: String(target.getAttribute("role") || target.tagName.toLowerCase()),
    visible: Boolean(target.offsetParent !== null || target.getClientRects().length),
    disabled,
    occurred_at: new Date().toISOString(),
  });
  scheduleWebuiEventFlush();
}

async function checkForUpdates(manual = false) {
  if (updateChecking.value || updateInProgress.value) return;
  updateChecking.value = true;
  if (manual) updateError.value = "";
  try {
    const info = await call(
      "update.check",
      { manual },
      { origin: manual ? "manual" : "auto_update" },
    );
    availableUpdate.value = info?.available ? info : null;
    if (manual && !info?.available) {
      updateError.value = "当前已经是最新版本。";
    }
  } catch (cause) {
    if (manual) {
      updateError.value = cause instanceof Error ? cause.message : String(cause);
    }
  } finally {
    updateChecking.value = false;
  }
}

function openUpdateDetails() {
  if (!availableUpdate.value || updateInProgress.value) return;
  updateError.value = "";
  updateModalOpen.value = true;
  trackUpdateUiEvent("update.details_opened");
}

function trackUpdateUiEvent(kind, extra = {}) {
  webuiEventQueue.push({
    kind,
    route: route.fullPath,
    page: currentPageLabel(),
    occurred_at: new Date().toISOString(),
    ...extra,
  });
  scheduleWebuiEventFlush();
}

async function startUpdateInstall() {
  const info = availableUpdate.value;
  if (!info || updateInProgress.value) return;
  updateModalOpen.value = false;
  updateError.value = "";
  updateInProgress.value = true;
  updateJob.value = {
    status: "queued",
    message: "正在准备更新…",
    current: 0,
    total: 0,
  };
  trackUpdateUiEvent("update.install_clicked", {
    latest_version: info.latest_version,
  });
  try {
    const started = await call("update.install.start", {
      latest_version: info.latest_version,
    });
    pollUpdateInstall(started.job_id);
  } catch (cause) {
    updateInProgress.value = false;
    updateError.value = cause instanceof Error ? cause.message : String(cause);
    updateModalOpen.value = true;
  }
}

async function pollUpdateInstall(jobId) {
  if (updatePollTimer !== null) window.clearTimeout(updatePollTimer);
  try {
    const job = await call("update.install.status", { job_id: jobId });
    updateJob.value = job;
    if (job.status === "failed") {
      updateInProgress.value = false;
      updateError.value = String(job.error || job.message || "自动更新失败");
      updateModalOpen.value = true;
      trackUpdateUiEvent("update.install_failed", { error: updateError.value });
      return;
    }
    updatePollTimer = window.setTimeout(() => pollUpdateInstall(jobId), 500);
  } catch (cause) {
    updateInProgress.value = false;
    updateError.value = cause instanceof Error ? cause.message : String(cause);
    updateModalOpen.value = true;
  }
}

async function prepareAccountAction(action) {
  accountMenuOpen.value = false;
  accountAction.value = action;
  accountBusy.value = true;
  accountFeedback.value = "";
  accountHandoff.value = false;
  try {
    const status = await call("account.task-status", {});
    accountTasks.value = status.tasks || [];
    accountModalOpen.value = true;
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    accountBusy.value = false;
  }
}

function clearBrowserSessionCache() {
  sessionStorage.clear();
  localStorage.clear();
  if ("caches" in window) {
    caches.keys().then((keys) => Promise.all(keys.map((key) => caches.delete(key)))).catch(() => {});
  }
}

async function confirmAccountAction() {
  if (!accountAction.value || accountBusy.value) return;
  accountBusy.value = true;
  accountFeedback.value = "";
  try {
    const result = await call("account.action", { action: accountAction.value });
    beginAccountHandoff(accountAction.value);
    accountHandoff.value = true;
    resetSessionState();
    clearBrowserSessionCache();
    await router.replace("/shops").catch(() => {});
    const paused = Number(result?.paused_task_count || 0);
    accountFeedback.value = (
      accountAction.value === "logout"
        ? "已退出登录，正在返回登录窗口…"
        : "正在关闭当前账户工作台并返回登录窗口…"
    ) + (paused ? ` 旧账号 ${paused} 个后台任务已暂停并保留。` : "");
  } catch (cause) {
    accountFeedback.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    accountBusy.value = false;
  }
}

function handleAccountHandoff() {
  if (updateCheckTimer !== null) window.clearInterval(updateCheckTimer);
  if (updatePollTimer !== null) window.clearTimeout(updatePollTimer);
  if (bigScreenFeedbackTimer !== null) window.clearTimeout(bigScreenFeedbackTimer);
  if (webuiEventFlushTimer !== null) window.clearTimeout(webuiEventFlushTimer);
  updateCheckTimer = null;
  updatePollTimer = null;
  bigScreenFeedbackTimer = null;
  webuiEventFlushTimer = null;
  webuiEventQueue.length = 0;
}

function showButtonFeedback(event) {
  const target = event.target instanceof Element ? event.target.closest("button") : null;
  if (!target || target.disabled) return;
  target.classList.remove("button-feedback");
  void target.offsetWidth;
  target.classList.add("button-feedback");
  window.setTimeout(() => target.classList.remove("button-feedback"), 220);
}

onMounted(async () => {
  document.addEventListener("pointerdown", showButtonFeedback, true);
  document.addEventListener("click", trackUserClick, true);
  window.addEventListener("langlang-account-handoff", handleAccountHandoff);
  window.addEventListener("beforeunload", handleBeforeUnload);
  window.addEventListener("langlang-update-info", handleUpdateInfo);
  try {
  await ensureBootstrap({ force: true });
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    loading.value = false;
  }
  startBackgroundRefresh();
  if (canUseSoftwareUpdate.value) {
    window.setTimeout(() => checkForUpdates(false), 1200);
    updateCheckTimer = window.setInterval(
      () => checkForUpdates(false),
      30 * 60 * 1000,
    );
  }
});

onBeforeUnmount(() => {
  document.removeEventListener("pointerdown", showButtonFeedback, true);
  document.removeEventListener("click", trackUserClick, true);
  window.removeEventListener("langlang-account-handoff", handleAccountHandoff);
  window.removeEventListener("beforeunload", handleBeforeUnload);
  window.removeEventListener("langlang-update-info", handleUpdateInfo);
  if (updateCheckTimer !== null) window.clearInterval(updateCheckTimer);
  if (updatePollTimer !== null) window.clearTimeout(updatePollTimer);
  if (bigScreenFeedbackTimer !== null) window.clearTimeout(bigScreenFeedbackTimer);
  if (webuiEventFlushTimer !== null) window.clearTimeout(webuiEventFlushTimer);
  flushWebuiEvents({ keepalive: true });
});

watch(
  () => route.fullPath,
  (to, from) => {
    if (to === from) return;
    webuiEventQueue.push({
      kind: "navigation",
      from: from || "",
      to,
      page: currentPageLabel(),
      occurred_at: new Date().toISOString(),
    });
    scheduleWebuiEventFlush();
  },
  { immediate: true },
);
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <img class="brand-logo" src="/langlang-logo.png" alt="澜琅" />
        <div>
          <strong>{{ bootstrap.brand.name }}</strong>
          <small>v{{ bootstrap.brand.version }}</small>
        </div>
      </div>

      <button
        v-if="availableUpdate && canUseSoftwareUpdate"
        class="sidebar-update-badge"
        type="button"
        :aria-label="`发现新版本 ${availableUpdate.latest_version}，当前版本 ${availableUpdate.current_version}`"
        @click="openUpdateDetails"
      >
        <span>发现新版本 v{{ availableUpdate.latest_version }}</span>
        <small>当前 v{{ availableUpdate.current_version }} · 点击查看</small>
      </button>

      <nav class="main-nav" aria-label="主导航">
        <button
          v-for="item in bootstrap.primary_nav"
          :key="item.key"
          class="nav-item"
          :class="{ active: isActive(item) }"
          type="button"
          @click="navigate(item)"
        >
          <span class="nav-emoji">{{ navEmoji[item.key] || "•" }}</span>
          <span>{{ item.label }}</span>
        </button>
      </nav>
      <div v-if="bootstrap.more_nav.length" class="more-wrap">
        <button
          class="nav-item"
          :class="{ active: moreOpen || activePath.startsWith('/more') || activePath.startsWith('/operations') }"
          type="button"
          @click="moreOpen = !moreOpen"
        >
          <span class="nav-emoji">•••</span>
          <span>更多</span>
          <Icon class="nav-chevron" name="chevron" :size="16" />
        </button>
          <div v-if="moreOpen" class="more-menu">
            <button
              v-for="item in bootstrap.more_nav"
              :key="item.key"
              type="button"
              :disabled="item.enabled === false"
              :title="item.enabled === false ? '该模块尚未实施，仅在交付账本中记录' : item.label"
              @click="item.key === 'big_screen' ? openBigScreen() : navigate(item)"
            >
              <Icon :name="item.icon" :size="17" />
              <span>{{ item.label }}<small v-if="item.enabled === false">未实施</small></span>
            </button>
          </div>
      </div>

      <div class="sidebar-bottom">
        <div class="account-wrap">
          <button class="account-summary" type="button" @click="accountMenuOpen = !accountMenuOpen">
            <span class="account-avatar">{{ (accountInfo.username || accountInfo.email || "澜").slice(0, 1) }}</span>
            <span class="account-copy">
              <strong>{{ accountInfo.username || bootstrap.account_label || "当前账户" }}</strong>
              <small>{{ accountInfo.email || "邮箱暂无数据" }}</small>
            </span>
            <Icon name="chevron" :size="14" />
          </button>
          <div v-if="accountMenuOpen" class="account-menu">
            <div class="account-menu-meta">
              <span>{{ accountInfo.member_role || bootstrap.member_role || "账号角色暂无数据" }}</span>
              <small>{{ accountInfo.organization_name || "组织暂无数据" }}</small>
            </div>
            <button v-if="canSwitchAccounts" type="button" :disabled="accountBusy" @click="prepareAccountAction('switch')">
              <Icon name="users" :size="16" /> 切换账号
            </button>
            <button class="danger" type="button" :disabled="accountBusy" @click="prepareAccountAction('logout')">
              <Icon name="close" :size="16" /> 退出登录
            </button>
          </div>
        </div>
        <div v-for="[label, icon, path] in sidebarUtilityLinks" :key="path" class="balance-row">
          <span>{{ label }}</span>
          <Icon :name="icon" :size="14" />
          <button type="button" @click="router.push(path)">查看</button>
        </div>
        <div class="license-row">
          <span>许可证</span>
          <strong>{{ licenseLabel }}</strong>
        </div>
      </div>
    </aside>

    <main class="workspace">
      <div v-if="loading" class="boot-state">正在加载澜琅工作台…</div>
      <div v-else-if="error" class="boot-state error-state">
        <strong>界面加载失败</strong>
        <span>{{ error }}</span>
      </div>
      <RouterView v-else v-slot="{ Component }">
        <KeepAlive>
          <component :is="Component" />
        </KeepAlive>
      </RouterView>
    </main>

    <div
      v-if="bigScreenFeedback"
      class="big-screen-feedback"
      role="status"
      aria-live="polite"
    >
      {{ bigScreenFeedback }}
    </div>

    <ModalDialog
      :open="updateModalOpen"
      title="发现新版本"
      :subtitle="availableUpdate ? `当前 v${availableUpdate.current_version}，最新 v${availableUpdate.latest_version}` : ''"
      width="940px"
      panel-class="update-modal"
      :busy="updateInProgress"
      @close="updateModalOpen = false"
    >
      <div class="update-details">
        <p v-if="updateError" class="update-error">{{ updateError }}</p>
        <div v-if="updateSummaryGroups.length" class="update-summary-grid">
          <section v-for="group in updateSummaryGroups" :key="group.title">
            <h3>{{ group.title }}</h3>
            <ul>
              <li v-for="item in group.items" :key="item">{{ item }}</li>
            </ul>
          </section>
        </div>
        <p v-else>本次更新暂无摘要说明。</p>
        <a
          v-if="availableUpdate?.release_notes_url"
          class="release-notes-link"
          :href="availableUpdate.release_notes_url"
          target="_blank"
          rel="noopener noreferrer"
        >查看完整更新说明</a>
        <small class="update-install-note">确认后工作台会锁定，下载、校验、解压完成后自动安装并重启。</small>
      </div>
      <template #footer>
        <button class="btn" type="button" :disabled="updateInProgress" @click="updateModalOpen = false">稍后</button>
        <button class="btn primary" type="button" :disabled="!availableUpdate" @click="startUpdateInstall">立即更新</button>
      </template>
    </ModalDialog>

    <div
      v-if="updateInProgress"
      class="update-lock-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="正在更新，请等待完成"
    >
      <section class="update-progress-card">
        <span class="update-kicker">自动更新中 · 不可关闭</span>
        <h2>正在更新到 {{ availableUpdate?.latest_version }}</h2>
        <p>{{ updateJob?.message || "正在准备更新…" }}</p>
        <div class="update-progress-track">
          <i :style="{ width: `${updateProgressPercent}%` }"></i>
        </div>
        <strong>{{ updateProgressText }}</strong>
        <small>请保持软件运行，完成后会自动安装并重启。</small>
      </section>
    </div>

    <ModalDialog
      :open="accountModalOpen"
      :title="accountActionTitle"
      :subtitle="accountAction === 'logout' ? '退出会立即执行并移除本机保存的该账号登录信息，后台任务会暂停保留。' : '切换会立即执行；旧账号后台任务会暂停保留，晚到结果不会写入新账号。'"
      width="560px"
      :busy="accountBusy"
      @close="accountHandoff ? null : accountModalOpen = false"
    >
      <div class="account-action-body">
        <div class="account-action-target">
          <strong>{{ accountInfo.username || bootstrap.account_label }}</strong>
          <span>{{ accountInfo.email || "邮箱暂无数据" }} · {{ licenseLabel }}</span>
        </div>
        <div v-if="accountTasks.length" class="account-task-block">
          <strong>检测到 {{ accountTasks.length }} 个活跃后台任务</strong>
          <p>切换会取消页面轮询并暂停旧账号后台任务。任务状态、账号归属和待核实结果都会保留，晚到结果会被丢弃。</p>
          <div v-for="task in accountTasks" :key="`${task.kind}-${task.job_id}`" class="account-task-row">
            <span>{{ task.kind }}</span>
            <small>{{ task.status }} · {{ task.message || "任务处理中" }}</small>
          </div>
        </div>
        <p v-else class="account-safe-note">当前没有活跃后台任务。关闭当前工作台和本地 HTTP 服务后会回到登录窗口。</p>
        <p v-if="accountFeedback" class="account-feedback">{{ accountFeedback }}</p>
      </div>
      <template #footer>
        <button class="btn" type="button" :disabled="accountBusy || accountHandoff" @click="accountModalOpen = false">取消</button>
        <button
          class="btn"
          :class="accountAction === 'logout' ? 'danger' : 'primary'"
          type="button"
          :disabled="accountBusy || accountHandoff"
          @click="confirmAccountAction"
        >{{ accountBusy ? "正在安全暂停并退出…" : accountHandoff ? "正在返回登录…" : `立即安全${accountActionTitle}` }}</button>
      </template>
    </ModalDialog>
  </div>
</template>

<style scoped>
.account-wrap {
  position: relative;
  margin-bottom: 8px;
}

.account-summary {
  width: 100%;
  min-height: 52px;
  padding: 7px 8px;
  display: grid;
  grid-template-columns: 32px minmax(0, 1fr) 16px;
  align-items: center;
  gap: 7px;
  border: 1px solid rgba(255, 255, 255, .12);
  border-radius: 7px;
  color: #dce8f1;
  background: rgba(255, 255, 255, .07);
  text-align: left;
}

.account-summary:hover {
  background: rgba(255, 255, 255, .12);
}

.account-avatar {
  width: 32px;
  height: 32px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  color: #fff;
  background: linear-gradient(145deg, #19bf72, #227bd3);
  font-size: 13px;
  font-weight: 700;
}

.account-copy,
.account-copy strong,
.account-copy small {
  min-width: 0;
  display: block;
}

.account-copy strong,
.account-copy small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.account-copy strong {
  color: #fff;
  font-size: 11px;
}

.account-copy small {
  margin-top: 3px;
  color: #9fb3c3;
  font-size: 9px;
}

.account-menu {
  position: absolute;
  z-index: 30;
  left: 0;
  right: 0;
  bottom: calc(100% + 6px);
  padding: 6px;
  border: 1px solid rgba(255, 255, 255, .13);
  border-radius: 7px;
  background: #102a42;
  box-shadow: 0 12px 28px rgba(2, 13, 23, .28);
}

.account-menu-meta {
  padding: 7px 8px 8px;
  border-bottom: 1px solid rgba(255, 255, 255, .09);
}

.account-menu-meta span,
.account-menu-meta small {
  display: block;
}

.account-menu-meta span {
  color: #dce8f1;
  font-size: 10px;
}

.account-menu-meta small {
  margin-top: 2px;
  color: #93a9ba;
  font-size: 9px;
}

.account-menu > button {
  width: 100%;
  min-height: 32px;
  padding: 0 8px;
  display: flex;
  align-items: center;
  gap: 7px;
  border: 0;
  border-radius: 4px;
  color: #dbe7f0;
  background: transparent;
  font-size: 10px;
  text-align: left;
}

.account-menu > button:hover {
  background: rgba(255, 255, 255, .09);
}

.account-menu > button.danger {
  color: #ff9ea9;
}

.account-action-body {
  display: grid;
  gap: 12px;
}

.big-screen-feedback {
  position: fixed;
  z-index: 90;
  right: 22px;
  bottom: 22px;
  max-width: min(440px, calc(100vw - 44px));
  padding: 9px 12px;
  border: 1px solid #d8e0e7;
  border-radius: 7px;
  color: #314657;
  background: #fff;
  box-shadow: 0 10px 28px rgba(20, 39, 56, .16);
  font-size: 10px;
  line-height: 1.5;
}

.account-action-target,
.account-task-block {
  padding: 12px;
  display: grid;
  gap: 4px;
  border: 1px solid #dfe6eb;
  border-radius: 8px;
  background: #f8fafb;
}

.account-action-target strong,
.account-action-target span {
  display: block;
}

.account-action-target span {
  color: #71808c;
  font-size: 10px;
}

.account-task-block {
  border-color: #efcf9f;
  background: #fff9ed;
}

.account-task-block p,
.account-safe-note {
  margin: 3px 0;
  color: #697986;
  font-size: 11px;
  line-height: 1.7;
}

.account-task-row {
  padding: 7px 8px;
  display: flex;
  align-items: center;
  gap: 9px;
  border-top: 1px solid #f0e3ca;
}

.account-task-row span {
  min-width: 64px;
  color: #8b5f16;
  font-size: 10px;
  font-weight: 700;
}

.account-task-row small {
  color: #7b6c54;
  font-size: 9px;
}

.account-feedback {
  margin: 0;
  padding: 9px 10px;
  border-radius: 6px;
  color: #8a641e;
  background: #fff6df;
  font-size: 10px;
}

.more-menu button small {
  margin-left: 6px;
  color: #b4c1cc;
  font-size: 8px;
}

.sidebar-update-badge {
  width: calc(100% - 22px);
  margin: 12px 11px 8px;
  padding: 10px 11px;
  display: grid;
  gap: 3px;
  border: 1px solid rgba(255, 224, 122, .58);
  border-radius: 8px;
  color: #172b3d;
  background: linear-gradient(135deg, #ffd56a, #ffbd48);
  box-shadow: 0 8px 22px rgba(0, 0, 0, .18);
  text-align: left;
  cursor: pointer;
}

.sidebar-update-badge:hover {
  filter: brightness(1.04);
}

.sidebar-update-badge span {
  font-size: 11px;
  font-weight: 800;
}

.sidebar-update-badge small {
  color: #40505d;
  font-size: 9px;
}

.update-details {
  display: grid;
  gap: 12px;
}

.update-modal {
  height: min(70vh, 640px);
  max-height: 70vh;
}

.update-modal .modal-body {
  overflow-y: auto;
}

.update-summary-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.update-summary-grid section {
  padding: 11px 13px;
  border: 1px solid #e0e7ec;
  border-radius: 8px;
  background: #f8fafb;
}

.update-summary-grid h3 {
  margin: 0 0 7px;
  color: #2f4556;
  font-size: 11px;
}

.update-summary-grid ul {
  margin: 0;
  padding-left: 18px;
  color: #536675;
  font-size: 10px;
  line-height: 1.65;
}

.update-summary-grid li + li {
  margin-top: 4px;
}

.release-notes-link {
  width: fit-content;
  color: #176bb1;
  font-size: 10px;
  text-decoration: none;
}

.release-notes-link:hover {
  text-decoration: underline;
}

.update-details small,
.update-error {
  color: #74828e;
  font-size: 10px;
  line-height: 1.7;
}

.update-install-note {
  padding-top: 2px;
}

@media (max-width: 760px) {
  .update-modal {
    height: min(76vh, 640px);
    max-height: calc(100vh - 24px);
  }

  .update-summary-grid {
    grid-template-columns: 1fr;
  }
}

.update-error {
  margin: 0;
  padding: 9px 10px;
  border-radius: 6px;
  color: #a93745;
  background: #fff0f2;
}

.update-lock-overlay {
  position: fixed;
  z-index: 2000;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(9, 24, 37, .72);
  backdrop-filter: blur(3px);
}

.update-progress-card {
  width: min(560px, calc(100vw - 48px));
  padding: 28px;
  display: grid;
  gap: 12px;
  border: 1px solid #dce6ee;
  border-radius: 12px;
  color: #1a3145;
  background: #fff;
  box-shadow: 0 28px 70px rgba(0, 0, 0, .34);
}

.update-progress-card h2 {
  margin: 0;
  font-size: 24px;
}

.update-progress-card p,
.update-progress-card small {
  margin: 0;
  color: #6b7d8d;
  font-size: 11px;
}

.update-kicker {
  color: #b17212;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: .5px;
}

.update-progress-track {
  height: 10px;
  overflow: hidden;
  border-radius: 999px;
  background: #e7edf2;
}

.update-progress-track i {
  height: 100%;
  display: block;
  border-radius: inherit;
  background: linear-gradient(90deg, #18b977, #2c75d4);
  transition: width .18s linear;
}
</style>
