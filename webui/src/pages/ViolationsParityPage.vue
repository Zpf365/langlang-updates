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
    if (selectedStore.value && String(row.store_id) !== String(selectedStore.value)) return false;
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
    if (selectedStore.value && String(row.store_id) !== String(selectedStore.value)) return false;
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

async function loadViolations() {
  const data = await call("violations.list", {});
  violations.value = data.violations || [];
  currentPage.value = 1;
}

async function refreshViolations() {
  if (loading.value) return;
  loading.value = true;
  note.value = "";
  try {
    const result = await call("violations.refresh", {
      store_id: selectedStore.value || undefined,
    });
    violations.value = result.violations || [];
    note.value = result.errors?.length
      ? `已刷新 ${result.succeeded}/${result.refreshed} 家；${result.errors.slice(0, 2).join("；")}`
      : `已刷新 ${result.succeeded} 家店铺违规记录`;
    currentPage.value = 1;
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
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

onMounted(async () => {
  try {
    const [storeData] = await Promise.all([call("stores.list", {}), loadViolations()]);
    stores.value = storeData.stores || [];
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
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
          {{ loading ? "正在刷新…" : (selectedStore ? "刷新当前店铺违规" : "刷新全部店铺违规") }}
        </button>
        <button class="btn green" type="button" :disabled="loading || platformSyncLoading" @click="syncPlatformBlacklist">
          {{ platformSyncLoading ? "正在同步黑名单…" : "同步平台违规黑名单" }}
        </button>
      </div>
    </header>

    <div class="violation-filter">
      <select v-model="selectedStore" class="select store-select" @change="currentPage = 1">
        <option value="">全部店铺</option>
        <option v-for="store in stores" :key="store.id" :value="store.id">{{ store.name }}</option>
      </select>
      <input v-model="search" class="input search-input" placeholder="搜索违规编号、原因、商品" @input="currentPage = 1" />
      <span>{{ filtered.length }} 条</span>
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
              {{ violations.length ? "当前筛选条件下没有违规记录" : "点击刷新获取店铺违规记录" }}
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
