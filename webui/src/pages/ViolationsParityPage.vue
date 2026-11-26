<script setup>
import { computed, onMounted, ref } from "vue";
import { call } from "../api";
import ModalDialog from "../components/ModalDialog.vue";

const stores = ref([]);
const violations = ref([]);
const selectedStore = ref("");
const search = ref("");
const activeStatus = ref("all");
const note = ref("");
const loading = ref(false);
const platformSyncLoading = ref(false);
const syncState = ref({
  tone: "",
  title: "等待同步",
  detail: "启动后会自动刷新当前登录的真实店铺。",
});
const currentPage = ref(1);
const previewImage = ref(null);
const pageSize = 20;
const statuses = [
  ["all", "全部"],
  ["pending", "待处理"],
  ["appealing", "申诉中"],
  ["rejected", "申诉未通过"],
  ["approved", "申诉通过"],
  ["done", "已处理"],
];

function isLoggedInRealStore(store) {
  const appId = String(store?.app_id || "").trim().toLowerCase();
  if (appId === "demo-store") return false;
  return /已登录/.test(`${store?.web_status || ""} ${store?.status || ""}`);
}

const loggedInStores = computed(() => stores.value.filter(isLoggedInRealStore));
const selectedStoreRow = computed(() => (
  loggedInStores.value.find(
    (store) => String(store.id) === String(selectedStore.value),
  ) || null
));

function statusMatches(status, key) {
  const text = String(status || "");
  if (key === "all") return true;
  if (key === "pending") return /待处理|待申诉|及时未处理|^10$/.test(text);
  if (key === "appealing") return /申诉中/.test(text);
  if (key === "rejected") return /申诉未通过|驳回/.test(text);
  if (key === "approved") return /申诉通过|申诉成功/.test(text);
  if (key === "done") return /已处理|超时未申诉|已撤销/.test(text);
  return false;
}

function statusLabel(status) {
  const text = String(status || "");
  if (text === "10") return "待申诉";
  return text || "-";
}

function formatViolationTime(value) {
  const numeric = Number(value || 0);
  const date = numeric > 10_000_000_000 ? new Date(numeric) : new Date(numeric * 1000);
  const valid = Number.isFinite(date.getTime()) && numeric > 0;
  return valid ? date.toLocaleString("zh-CN", { hour12: false }) : "-";
}

const filtered = computed(() => {
  const term = search.value.trim().toLowerCase();
  return violations.value.filter((row) => {
    if (
      !selectedStore.value
      || String(row.store_id) !== String(selectedStore.value)
    ) return false;
    if (!statusMatches(row.status, activeStatus.value)) return false;
    if (!term) return true;
    return [row.record_id, row.reason, row.title, row.product_id, row.store_name]
      .some((value) => String(value || "").toLowerCase().includes(term));
  });
});

const totalPages = computed(() => Math.max(1, Math.ceil(filtered.value.length / pageSize)));
const pagedViolations = computed(() => {
  const start = (currentPage.value - 1) * pageSize;
  return filtered.value.slice(start, start + pageSize);
});

function statusCount(key) {
  return violations.value.filter((row) => {
    if (
      !selectedStore.value
      || String(row.store_id) !== String(selectedStore.value)
    ) return false;
    return statusMatches(row.status, key);
  }).length;
}

function setStatus(key) {
  activeStatus.value = key;
  currentPage.value = 1;
}

function openImagePreview(row) {
  if (row?.head_img) previewImage.value = row;
}

function cloudSyncState(result) {
  const cloud = result?.cloud_counterfeit;
  if (!cloud || typeof cloud !== "object" || Object.keys(cloud).length === 0) {
    return {
      tone: "danger",
      title: "云端未部署/同步失败",
      detail: "响应中没有组织云端售假库状态；本地违规记录仍按真实店铺同步结果展示。",
    };
  }
  if (cloud.skipped) {
    return {
      tone: "danger",
      title: "云端未部署/同步失败",
      detail: cloud.error
        || "组织云端售假违规库未配置或当前登录缺少云端权限。",
    };
  }
  if (cloud.error) {
    return {
      tone: "danger",
      title: "云端同步失败",
      detail: String(cloud.error),
    };
  }
  const saved = cloud.saved;
  const hasSavedCount = (
    (typeof saved === "number" && Number.isFinite(saved))
    || (typeof saved === "string" && saved.trim() !== "" && Number.isFinite(Number(saved)))
  );
  if (hasSavedCount) {
    return {
      tone: "success",
      title: "云端售假库已同步",
      detail: `本次幂等写入 ${Number(saved)} 条售假标题索引。`,
    };
  }
  return {
    tone: "warning",
    title: "云端同步状态待确认",
    detail: "云端响应不完整，不能据此显示同步成功。",
  };
}

async function refreshViolations(options = {}) {
  if (loading.value) return;
  if (!selectedStore.value || !selectedStoreRow.value) {
    violations.value = [];
    syncState.value = {
      tone: "danger",
      title: "待同步：没有已登录的真实店铺",
      detail: "请先在店铺管理中登录真实店铺；不会用演示店铺或样例违规填充。",
    };
    note.value = "没有已登录的真实店铺，违规记录无法同步";
    return;
  }
  loading.value = true;
  note.value = "";
  syncState.value = {
    tone: "running",
    title: options?.automatic ? "正在启动自动同步" : "正在同步违规记录",
    detail: `正在读取 ${selectedStoreRow.value.name} 的官方违规记录。`,
  };
  try {
    const result = await call("violations.refresh", {
      store_id: Number(selectedStore.value),
    });
    violations.value = result.violations || [];
    const complete = Number(result.succeeded || 0) > 0
      && !(result.errors || []).length;
    const cloud = cloudSyncState(result);
    let title = "违规记录同步失败/待同步";
    let detail = `成功 ${Number(result.succeeded || 0)}/${Number(result.refreshed || 0)} 家；${(result.errors || []).slice(0, 3).join("；") || cloud.detail}`;
    if (complete) {
      title = cloud.tone === "danger"
        ? cloud.title
        : `官方违规记录已同步 · ${selectedStoreRow.value.name}`;
      detail = cloud.detail;
    }
    syncState.value = {
      tone: complete && cloud.tone !== "danger" ? cloud.tone : "danger",
      title,
      detail,
    };
    note.value = complete
      ? (cloud.tone === "danger" ? cloud.title : `已同步 ${selectedStoreRow.value.name} 的官方违规记录`)
      : `${syncState.value.title}：${syncState.value.detail}`;
    currentPage.value = 1;
  } catch (cause) {
    const message = cause instanceof Error ? cause.message : String(cause);
    violations.value = [];
    syncState.value = {
      tone: "danger",
      title: "违规记录同步失败/待同步",
      detail: `云端未部署/同步失败：${message}`,
    };
    note.value = syncState.value.detail;
  } finally {
    loading.value = false;
  }
}

async function syncPlatformBlacklist() {
  if (platformSyncLoading.value) return;
  platformSyncLoading.value = true;
  note.value = "";
  try {
    const result = await call("violations.sync-platform-blacklist", {
      store_ids: selectedStore.value ? [selectedStore.value] : [],
    });
    note.value = result.errors?.length
      ? `平台违规黑名单同步完成：成功 ${result.succeeded}/${result.refreshed} 家；${result.errors.slice(0, 2).join("；")}`
      : `平台违规黑名单同步完成：${result.platform_record_count} 条平台记录，上货违规标题库 ${result.active_rule_count} 条规则`;
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    platformSyncLoading.value = false;
  }
}

async function refreshSelectedStore() {
  await refreshViolations({ automatic: true });
}

onMounted(async () => {
  try {
    const storeData = await call("stores.list", {});
    stores.value = storeData.stores || [];
    const realStore = loggedInStores.value[0];
    selectedStore.value = realStore ? realStore.id : "";
    if (!realStore) {
      violations.value = [];
      syncState.value = {
        tone: "danger",
        title: "待同步：没有已登录的真实店铺",
        detail: "请先登录真实店铺；违规中心不会显示测试店铺或样例数据。",
      };
      note.value = "没有已登录的真实店铺，违规记录无法同步";
      return;
    }
    await refreshViolations({ automatic: true });
  } catch (cause) {
    const message = cause instanceof Error ? cause.message : String(cause);
    violations.value = [];
    syncState.value = {
      tone: "danger",
      title: "违规记录同步失败/待同步",
      detail: message,
    };
    note.value = message;
  }
});
</script>

<template>
  <section class="violation-page">
    <header class="violation-head">
      <div>
        <h1>违规中心</h1>
        <p>汇总各店铺后台违规记录，仅保留最近一次成功刷新结果。</p>
      </div>
      <div class="violation-head-actions">
        <button class="btn" type="button" :disabled="loading || platformSyncLoading" @click="refreshViolations">
          {{ loading ? "正在刷新…" : "刷新当前店铺违规" }}
        </button>
        <button class="btn green" type="button" :disabled="loading || platformSyncLoading" @click="syncPlatformBlacklist">
          {{ platformSyncLoading ? "正在同步黑名单…" : "同步平台违规黑名单" }}
        </button>
      </div>
    </header>

    <div class="violation-filter">
      <select v-model="selectedStore" class="select store-select" @change="refreshSelectedStore">
        <option value="">{{ loggedInStores.length ? "请选择已登录店铺" : "没有已登录的真实店铺" }}</option>
        <option v-for="store in loggedInStores" :key="store.id" :value="store.id">{{ store.name }}</option>
      </select>
      <input v-model="search" class="input search-input" placeholder="搜索违规编号、原因、商品" @input="currentPage = 1" />
      <span>{{ filtered.length }} 条</span>
    </div>

    <div class="sync-state" :class="syncState.tone" role="status">
      <strong>{{ syncState.title }}</strong>
      <span>{{ syncState.detail }}</span>
    </div>

    <div class="status-tabs">
      <button
        v-for="[key, label] in statuses"
        :key="key"
        type="button"
        :class="{ active: activeStatus === key }"
        @click="setStatus(key)"
      >{{ label }} {{ statusCount(key) }}</button>
    </div>

    <div class="violation-card">
      <table class="data-table violation-table">
        <thead>
          <tr>
            <th>店铺 / 违规编号</th>
            <th>违规类型 / 时间</th>
            <th>违规对象</th>
            <th>处罚方式 / 信用变更</th>
            <th>违规说明</th>
            <th>状态 / 操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in pagedViolations" :key="`${row.store_id}:${row.record_id}`">
            <td>
              <strong>{{ row.store_name }}</strong>
              <small>{{ row.record_id || "-" }}</small>
            </td>
            <td>
              <span>{{ row.reason || row.source || "-" }}</span>
              <small>{{ formatViolationTime(row.time) }}</small>
            </td>
            <td>
              <div class="violation-object">
                <button
                  v-if="row.head_img"
                  class="violation-thumb"
                  type="button"
                  title="点击放大商品主图"
                  @click="openImagePreview(row)"
                >
                  <img :src="row.head_img" :alt="row.title || '违规商品主图'" />
                </button>
                <div v-else class="violation-thumb empty">无主图</div>
                <div class="violation-object-copy">
                  <span>{{ row.title || "-" }}</span>
                  <small>{{ row.product_id || "-" }}</small>
                </div>
              </div>
            </td>
            <td>{{ row.source || "-" }}</td>
            <td class="violation-description">{{ row.fix || row.reason || "-" }}</td>
            <td>{{ statusLabel(row.status) }}</td>
          </tr>
          <tr v-if="!loading && filtered.length === 0">
            <td colspan="6" class="violation-empty">
              {{ syncState.tone === "danger"
                ? syncState.title
                : violations.length
                  ? "当前筛选条件下没有违规记录"
                  : `${selectedStoreRow?.name || "当前店铺"} 暂无官方违规记录` }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <footer class="violation-pagination">
      <button class="btn" :disabled="currentPage <= 1" type="button" @click="currentPage -= 1">上一页</button>
      <span>{{ currentPage }} / {{ totalPages }}</span>
      <button class="btn" :disabled="currentPage >= totalPages" type="button" @click="currentPage += 1">下一页</button>
    </footer>

    <ModalDialog
      :open="Boolean(previewImage)"
      title="违规商品主图"
      :subtitle="previewImage?.title || previewImage?.product_id || ''"
      width="760px"
      @close="previewImage = null"
    >
      <div v-if="previewImage" class="violation-image-preview">
        <img :src="previewImage.head_img" :alt="previewImage.title || '违规商品主图'" />
      </div>
      <template #footer>
        <button class="btn" type="button" @click="previewImage = null">关闭</button>
      </template>
    </ModalDialog>

    <div v-if="note" class="toast">{{ note }}</div>
  </section>
</template>

<style scoped>
.violation-page {
  min-height: 100%;
  padding: 26px 22px 28px;
  background: #f2f4f7;
}

.violation-head {
  min-height: 84px;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
}

.violation-head h1 {
  margin: 0;
  color: #202b35;
  font-size: 24px;
  font-weight: 600;
}

.violation-head p {
  margin: 18px 0 0;
  color: #8795a2;
  font-size: 12px;
}

.violation-head-actions {
  display: flex;
  gap: 9px;
}

.violation-filter,
.sync-state,
.status-tabs,
.violation-card,
.violation-pagination {
  margin-left: 12px;
  margin-right: 12px;
}

.violation-filter {
  min-height: 52px;
  padding: 0 18px;
  display: flex;
  align-items: center;
  gap: 12px;
  border: 1px solid #dbe3ea;
  background: #fff;
}

.store-select {
  width: 180px;
}

.search-input {
  width: 380px;
}

.violation-filter span {
  color: #758392;
}

.sync-state {
  min-height: 48px;
  padding: 10px 18px;
  display: grid;
  gap: 4px;
  border: 1px solid #dbe3ea;
  border-top: 0;
  color: #60717f;
  background: #fff;
}

.sync-state strong,
.sync-state span {
  display: block;
}

.sync-state span {
  font-size: 11px;
}

.sync-state.success {
  border-color: #b7dec9;
  color: #276b4e;
  background: #f3fbf7;
}

.sync-state.warning {
  border-color: #ead6b2;
  color: #805923;
  background: #fffaf1;
}

.sync-state.danger {
  border-color: #efc4c8;
  color: #a33f49;
  background: #fff7f8;
}

.sync-state.running {
  border-color: #bdd5f2;
  color: #315f91;
  background: #f5f9ff;
}

.status-tabs {
  min-height: 64px;
  padding: 13px 18px;
  display: flex;
  align-items: center;
  gap: 10px;
  border: 1px solid #dbe3ea;
  border-top: 0;
  background: #fff;
}

.status-tabs button {
  height: 36px;
  padding: 0 16px;
  border: 1px solid #eadfd8;
  border-radius: 4px;
  color: #6a554d;
  background: #fff;
}

.status-tabs button.active {
  border-color: #9ddfbe;
  background: #f2fcf7;
}

.violation-card {
  min-height: 390px;
  border: 1px solid #dbe3ea;
  background: #fff;
}

.violation-table {
  width: 100%;
}

.violation-table th {
  height: 48px;
  color: #30404f;
  background: #eef6fc;
  text-align: center;
}

.violation-table th:nth-child(1) { width: 16%; }
.violation-table th:nth-child(2) { width: 14%; }
.violation-table th:nth-child(3) { width: 18%; }
.violation-table th:nth-child(4) { width: 17%; }
.violation-table th:nth-child(5) { width: 22%; }
.violation-table th:nth-child(6) { width: 13%; }

.violation-table td {
  padding: 10px;
  vertical-align: top;
  line-height: 1.5;
}

.violation-table td strong,
.violation-table td span,
.violation-table td small {
  display: block;
}

.violation-object {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.violation-object-copy {
  min-width: 0;
  flex: 1;
}

.violation-thumb {
  width: 56px;
  height: 56px;
  flex: 0 0 56px;
  padding: 0;
  overflow: hidden;
  border: 1px solid #d9e1e7;
  border-radius: 5px;
  background: #f6f8fa;
  cursor: zoom-in;
}

.violation-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.violation-thumb.empty {
  display: grid;
  place-items: center;
  color: #9aa5ae;
  font-size: 10px;
  cursor: default;
}

.violation-image-preview {
  min-height: 320px;
  display: grid;
  place-items: center;
  background: #f5f7f9;
}

.violation-image-preview img {
  max-width: 100%;
  max-height: 68vh;
  object-fit: contain;
}

.violation-table td small {
  margin-top: 4px;
  color: #87939e;
  font-size: 10px;
}

.violation-description {
  white-space: pre-wrap;
  color: #5f6d79;
}

.violation-empty {
  height: 338px !important;
  color: #8c98a3;
  text-align: center;
}

.violation-pagination {
  min-height: 66px;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  color: #677583;
}

.green {
  border-color: #1cc977;
  color: #fff;
  background: #20c97b;
}
</style>
