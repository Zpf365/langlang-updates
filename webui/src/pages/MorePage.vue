<script setup>
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { call } from "../api";
import Icon from "../components/Icon.vue";
import {
  hasEntitlement,
  hasOperationsPermission,
} from "../session";

const route = useRoute();
const router = useRouter();
const logStatus = ref(null);
const logBusy = ref("");
const logFeedback = ref("");
const updateBusy = ref(false);
const updateFeedback = ref("");
const allModules = [
  ["operations", "运营中心", "radar", "店铺健康、商品机会、运营打法与持久化行动任务", "/operations", "operations"],
  ["store-groups", "店铺分组", "folder", "在店铺管理中维护分组", "/shops", "module.store_groups"],
  ["activity", "活动管理", "calendar", "平台活动读取、报名、商品预检与 SKU 提交", "/activity"],
  ["automation", "自动化", "workflow", "旧版真实同步任务、托管执行和执行记录", "/automation", "module.automation"],
  ["finance", "资金账户", "wallet", "官方余额、结算与资金流水", "/finance", "module.finance"],
  ["storefront", "店铺主页", "store", "官方主页商品排序、隐藏和置顶", "/storefront", "module.storefront"],
  ["after-sales", "售后管理", "return", "在订单管理内筛选并处理真实售后订单", "/orders?after_sales=1", "module.after_sales"],
  ["team", "团队管理", "users", "团队成员、角色和运营中心功能权限", "/more/team", "module.team"],
  ["delivery-ledger", "未完成", "clipboard", "真实交付状态、未验证边界和最近审计", "/more/delivery-ledger", "module.delivery_ledger"],
];

const modules = computed(() =>
  allModules.filter(([, , , , , permission]) => {
    if (permission === "operations") {
      return hasOperationsPermission("operations_view");
    }
    if (permission?.startsWith("module.")) {
      return hasEntitlement(permission);
    }
    return true;
  }),
);
const canViewRuntimeLogs = computed(() => hasEntitlement("module.runtime_logs"));
const canUseSoftwareUpdate = computed(() => hasEntitlement("feature.software_update"));

async function loadLogStatus() {
  try {
    logStatus.value = await call("logs.status", {});
  } catch (cause) {
    logFeedback.value = cause instanceof Error ? cause.message : String(cause);
  }
}

async function exportLogs(days) {
  if (logBusy.value) return;
  logBusy.value = `export-${days}`;
  logFeedback.value = "";
  try {
    const result = await call("logs.export", { days });
    logFeedback.value = `已导出 ${result.days} 天日志：${result.path}`;
  } catch (cause) {
    logFeedback.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    logBusy.value = "";
  }
}

async function openLogDirectory() {
  if (logBusy.value) return;
  logBusy.value = "open";
  logFeedback.value = "";
  try {
    const result = await call("logs.open-dir", {});
    logFeedback.value = `已打开：${result.path}`;
  } catch (cause) {
    logFeedback.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    logBusy.value = "";
  }
}

async function checkUpdatesManually() {
  if (updateBusy.value) return;
  updateBusy.value = true;
  updateFeedback.value = "";
  try {
    const info = await call(
      "update.check",
      { manual: true },
      { origin: "manual" },
    );
    window.dispatchEvent(new CustomEvent("langlang-update-info", { detail: info }));
    updateFeedback.value = info.available
      ? `发现新版本 v${info.latest_version}，可从左上角更新提示查看。`
      : "当前已经是最新版本。";
  } catch (cause) {
    updateFeedback.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    updateBusy.value = false;
  }
}

onMounted(() => {
  if (canViewRuntimeLogs.value) loadLogStatus();
});
</script>

<template>
  <section class="page">
    <header class="page-header">
      <div>
        <h1>更多</h1>
        <p>澜琅独有的管理能力统一收纳在二级入口</p>
      </div>
    </header>
    <div class="more-card-grid">
      <button
        v-for="[key, label, icon, detail, path] in modules"
        :key="key"
        class="more-card card"
        type="button"
        :disabled="!path"
        :title="path ? label : `${label}尚未实施，请在未完成账本查看边界`"
        @click="router.push(path || `/more/${key}`)"
      >
        <span class="more-icon"><Icon :name="icon" :size="24" /></span>
        <span>
          <strong>{{ label }}</strong>
          <small>{{ detail }}<b v-if="!path" class="unimplemented-tag">未实施</b></small>
        </span>
      </button>
    </div>
    <section v-if="canViewRuntimeLogs" class="card runtime-tools">
      <header>
        <div>
          <h2>运行日志</h2>
          <p>用于排查环境、账号、页面操作和真实接口异常；导出包不包含数据库或密钥。</p>
        </div>
        <button v-if="canUseSoftwareUpdate" class="btn" type="button" :disabled="updateBusy" @click="checkUpdatesManually">
          {{ updateBusy ? "检查中…" : "检查更新" }}
        </button>
      </header>
      <p v-if="updateFeedback" class="runtime-feedback">{{ updateFeedback }}</p>
      <div v-if="logStatus" class="runtime-log-meta">
        <span><b>实际路径</b>{{ logStatus.log_path }}</span>
        <span><b>运行 ID</b>{{ logStatus.run_id }}</span>
        <span><b>最近错误</b>{{ logStatus.recent_errors }}</span>
      </div>
      <div v-else class="runtime-log-meta">
        <span>正在读取日志状态…</span>
      </div>
      <div class="runtime-actions">
        <button class="btn" type="button" :disabled="Boolean(logBusy)" @click="exportLogs(1)">
          {{ logBusy === "export-1" ? "导出中…" : "导出最近 1 天" }}
        </button>
        <button class="btn" type="button" :disabled="Boolean(logBusy)" @click="exportLogs(7)">
          {{ logBusy === "export-7" ? "导出中…" : "导出最近 7 天" }}
        </button>
        <button class="btn" type="button" :disabled="Boolean(logBusy)" @click="openLogDirectory">
          {{ logBusy === "open" ? "正在打开…" : "打开日志目录" }}
        </button>
      </div>
      <p v-if="logFeedback" class="runtime-feedback">{{ logFeedback }}</p>
    </section>
    <div v-if="route.params.section" class="card more-content">
      <h2>{{ modules.find((item) => item[0] === route.params.section)?.[1] }}</h2>
      <p>该模块尚未实施；请返回“未完成”账本查看真实状态和阻塞原因。</p>
    </div>
  </section>
</template>

<style scoped>
.more-card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(290px, 1fr));
  gap: 14px;
}

.runtime-tools {
  margin-top: 18px;
  padding: 20px;
}

.runtime-tools header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.runtime-tools h2 {
  margin: 0 0 6px;
}

.runtime-tools p {
  margin: 0;
  color: var(--muted);
  font-size: 11px;
  line-height: 1.7;
}

.runtime-log-meta {
  margin-top: 14px;
  padding: 12px;
  display: grid;
  gap: 7px;
  border-radius: 7px;
  color: #526474;
  background: #f5f8fa;
  font-size: 10px;
  word-break: break-all;
}

.runtime-log-meta b {
  min-width: 70px;
  display: inline-block;
  color: #1f3548;
}

.runtime-actions {
  margin-top: 13px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.runtime-feedback {
  margin-top: 11px !important;
  padding: 9px 10px;
  border-radius: 6px;
  color: #315d46 !important;
  background: #edf8f2;
  word-break: break-all;
}

.more-card {
  min-height: 98px;
  padding: 17px;
  display: flex;
  align-items: center;
  gap: 15px;
  border: 1px solid var(--line);
  text-align: left;
}

.more-card:hover {
  border-color: #a6cdb9;
  background: #fbfffd;
}

.more-icon {
  width: 45px;
  height: 45px;
  display: grid;
  place-items: center;
  border-radius: 12px;
  color: #0ea966;
  background: #e8f8f0;
}

.more-card strong,
.more-card small {
  display: block;
}

.unimplemented-tag {
  margin-left: 7px;
  padding: 1px 5px;
  border-radius: 4px;
  color: #b04452;
  background: #fdecef;
  font-size: 9px;
}

.more-card strong {
  color: #263543;
  font-size: 15px;
}

.more-card small {
  margin-top: 7px;
  color: #83909c;
  font-size: 12px;
}

.more-content {
  margin-top: 18px;
  min-height: 300px;
  padding: 22px;
}

.more-content h2 {
  margin-top: 0;
}

.more-content p {
  color: var(--muted);
}
</style>
