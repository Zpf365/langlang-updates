<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { call } from "../api";
import Icon from "../components/Icon.vue";

const router = useRouter();
const data = ref({
  generated_at: "",
  source: "",
  stores: [],
  metrics: {},
  rows: [],
  total: 0,
  page: 1,
  page_count: 1,
});
const loading = ref(false);
const refreshing = ref(false);
const error = ref("");
const storeId = ref("");
const sort = ref("elapsed");
const selectedMetric = ref("abnormal");
const page = ref(1);
const pageSize = ref(50);
const pageSizeInput = ref("50");
const jumpPageInput = ref("1");
let loadSequence = 0;

const metricCards = computed(() => [
  { key: "in_transit", label: "在途（仅统计）", value: data.value.metrics?.in_transit, danger: false },
  { key: "awaiting_pickup", label: "待揽收", value: data.value.metrics?.awaiting_pickup, danger: false },
  { key: "transporting", label: "运输中", value: data.value.metrics?.transporting, danger: false },
  { key: "returned", label: "已退回", value: data.value.metrics?.returned, danger: true },
  { key: "stagnant", label: "停滞", value: data.value.metrics?.stagnant, danger: true },
  { key: "after_sale", label: "售后", value: data.value.metrics?.after_sale, danger: true },
  { key: "abnormal", label: "异常合计", value: data.value.metrics?.abnormal, danger: true },
  { key: "signed_7d", label: "近7天已签收", value: data.value.metrics?.signed_7d, danger: false },
  { key: "today_abnormal", label: "今日新增异常", value: data.value.metrics?.today_abnormal, danger: true },
]);
const countOnlyFilters = new Set([
  "in_transit",
  "awaiting_pickup",
  "transporting",
  "signed_7d",
]);
const listCountText = computed(() => {
  if (loading.value) return "加载中…";
  if (countOnlyFilters.has(selectedMetric.value)) {
    return data.value.empty_message || "仅做数量统计";
  }
  const label = metricCards.value.find((item) => item.key === selectedMetric.value)?.label || "异常";
  return `共 ${data.value.total || 0} 单（${label}）`;
});

async function load() {
  const sequence = ++loadSequence;
  loading.value = true;
  error.value = "";
  try {
    const result = await call("logistics.monitor", {
      store_id: storeId.value || "",
      sort: sort.value,
      metric_filter: selectedMetric.value,
      page: page.value,
      page_size: pageSize.value,
    });
    if (sequence !== loadSequence) return;
    data.value = result || data.value;
    selectedMetric.value = String(result?.active_filter || selectedMetric.value);
    page.value = Number(result?.page || 1);
    jumpPageInput.value = String(page.value);
  } catch (cause) {
    if (sequence !== loadSequence) return;
    error.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    if (sequence === loadSequence) loading.value = false;
  }
}

async function refreshLogistics() {
  if (refreshing.value) return;
  refreshing.value = true;
  error.value = "";
  try {
    await call("orders.sync", {
      store_id: storeId.value || "",
      scope: "logistics",
    });
    await load();
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    refreshing.value = false;
  }
}

function formatElapsed(seconds) {
  const value = Math.max(0, Number(seconds || 0));
  if (value < 3600) return `${Math.floor(value / 60)} 分钟`;
  return `${Math.floor(value / 3600)} 小时`;
}

function formatTime(timestamp) {
  const value = Number(timestamp || 0);
  if (!value) return "—";
  return new Date(value * 1000).toLocaleString("zh-CN", {
    hour12: false,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function changePage(nextPage) {
  const target = Math.max(1, Math.min(data.value.page_count || 1, nextPage));
  if (target === page.value) return;
  page.value = target;
  jumpPageInput.value = String(target);
  load();
}

function applyPageSize() {
  const parsed = Number(pageSizeInput.value);
  if (!Number.isFinite(parsed) || parsed < 1 || parsed > 10000) {
    pageSizeInput.value = String(pageSize.value);
    error.value = "每页条数请输入 1–10000 的整数";
    return;
  }
  pageSize.value = Math.floor(parsed);
  pageSizeInput.value = String(pageSize.value);
  page.value = 1;
  jumpPageInput.value = "1";
  load();
}

function jumpToPage() {
  const parsed = Number(jumpPageInput.value);
  if (!Number.isFinite(parsed)) {
    jumpPageInput.value = String(page.value);
    return;
  }
  changePage(Math.floor(parsed));
}

function openOrder(row) {
  router.push({
    path: "/orders",
    query: {
      store_id: String(row.store_id),
      order_id: String(row.order_id),
      process: "1",
    },
  });
}

function selectMetric(key) {
  if (selectedMetric.value === key) return;
  selectedMetric.value = key;
  page.value = 1;
  load();
}

watch(storeId, () => {
  page.value = 1;
  load();
});

watch(sort, () => {
  page.value = 1;
  load();
});

onMounted(load);
</script>

<template>
  <section class="page logistics-page">
    <header class="page-header logistics-header">
      <div>
        <h1>物流监控</h1>
        <p>只看异常：已退回（售后返回件）、停滞（最后状态超过 48 小时且未签收）、售后。在途、待揽收、运输中只做数量统计，不列出来。</p>
      </div>
      <div class="toolbar">
        <button class="btn primary" type="button" :disabled="refreshing || loading" @click="refreshLogistics">
          <Icon name="refresh" :size="15" />
          {{ refreshing ? "刷新中…" : "刷新物流" }}
        </button>
        <button class="btn muted" type="button" :disabled="loading" @click="load()">重新加载</button>
      </div>
    </header>

    <div class="logistics-source">
      {{ data.source || "正在读取本地已同步订单与售后状态…" }}
      <span v-if="data.generated_at">· {{ data.generated_at }}</span>
    </div>
    <div v-if="error" class="logistics-error">{{ error }}</div>

    <section class="logistics-metrics">
      <button
        v-for="card in metricCards"
        :key="card.key"
        type="button"
        class="logistics-metric"
        :class="{ danger: card.danger, selected: selectedMetric === card.key }"
        :aria-pressed="selectedMetric === card.key"
        :title="`筛选${card.label}`"
        @click="selectMetric(card.key)"
      >
        <span>{{ card.label }}</span>
        <strong>{{ card.value ?? "—" }}</strong>
      </button>
    </section>

    <div class="logistics-filter">
      <label>店铺</label>
      <select v-model="storeId" class="select" :disabled="loading">
        <option value="">全部店铺</option>
        <option v-for="store in data.stores" :key="store.id" :value="String(store.id)">
          {{ store.name }}
        </option>
      </select>
      <label>排序</label>
      <select v-model="sort" class="select" :disabled="loading">
        <option value="elapsed">按已历时最长</option>
        <option value="ship_time">按发货时间</option>
      </select>
      <span class="logistics-count">
        {{ listCountText }}
      </span>
    </div>

    <div class="card logistics-table-card">
      <div class="table-scroll">
        <table class="data-table logistics-table">
          <thead>
            <tr>
              <th>订单号</th>
              <th>店铺</th>
              <th>订单状态</th>
              <th>快递</th>
              <th>运单号</th>
              <th>异常类型</th>
              <th>已历时</th>
              <th>最新状态</th>
              <th>平台异常时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in data.rows" :key="`${row.store_id}:${row.order_id}`">
              <td class="mono">
                <button class="order-jump" type="button" :title="`打开并处理订单 ${row.order_id}`" @click="openOrder(row)">
                  {{ row.order_id }}
                </button>
              </td>
              <td>{{ row.store_name }}</td>
              <td>{{ row.status_text || "—" }}</td>
              <td>{{ row.carrier || "—" }}</td>
              <td class="mono">{{ row.waybill_id || "—" }}</td>
              <td>
                <span
                  class="logistics-badge"
                  :class="row.logistics_status"
                  :title="row.trace_source"
                >{{ row.logistics_status_text }}</span>
              </td>
              <td>{{ formatElapsed(row.elapsed_seconds) }}</td>
              <td :title="row.trace_source">{{ row.latest_trace || "—" }}</td>
              <td>{{ formatTime(row.abnormal_at) }}</td>
              <td>
                <button class="btn small process-button" type="button" @click="openOrder(row)">去处理</button>
              </td>
            </tr>
            <tr v-if="!loading && !data.rows?.length">
              <td colspan="10" class="logistics-empty">{{ data.empty_message || "没有符合条件的异常订单" }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="pagination logistics-pagination">
        <button class="btn" :disabled="loading || page <= 1" type="button" @click="changePage(page - 1)">上一页</button>
        <span>第 {{ page }} / {{ data.page_count || 1 }} 页</span>
        <button class="btn" :disabled="loading || page >= (data.page_count || 1)" type="button" @click="changePage(page + 1)">下一页</button>
        <label class="page-size-control">
          每页显示
          <input
            v-model="pageSizeInput"
            class="input page-size-input"
            type="number"
            min="1"
            max="10000"
            inputmode="numeric"
            @change="applyPageSize"
            @keyup.enter="applyPageSize"
          />
          条
        </label>
        <span class="jump-label">跳转</span>
        <input
          v-model="jumpPageInput"
          class="input jump-input"
          type="number"
          min="1"
          :max="data.page_count || 1"
          inputmode="numeric"
          @keyup.enter="jumpToPage"
        />
        <button class="btn muted" type="button" :disabled="loading" @click="jumpToPage">确定</button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.logistics-page {
  padding-top: 18px;
}

.logistics-header {
  align-items: flex-start;
  margin-bottom: 10px;
}

.logistics-header p {
  max-width: 860px;
  line-height: 1.6;
}

.logistics-source {
  margin-bottom: 10px;
  color: #71808c;
  font-size: 10px;
}

.logistics-error {
  margin-bottom: 10px;
  padding: 8px 10px;
  border: 1px solid #efc8c5;
  border-radius: 5px;
  color: #b3423d;
  background: #fff7f6;
  font-size: 10px;
}

.logistics-metrics {
  display: grid;
  grid-template-columns: repeat(9, minmax(105px, 1fr));
  gap: 10px;
  margin-bottom: 15px;
}

.logistics-metric {
  display: block;
  width: 100%;
  min-height: 68px;
  padding: 10px 12px;
  border: 1px solid #dfe6ea;
  border-radius: 7px;
  background: white;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: border-color 140ms ease, box-shadow 140ms ease, transform 140ms ease;
}

.logistics-metric:hover,
.logistics-metric:focus-visible {
  border-color: #79a9df;
  box-shadow: 0 4px 12px rgba(39, 92, 145, .09);
  outline: none;
}

.logistics-metric:active {
  transform: translateY(1px);
}

.logistics-metric span,
.logistics-metric strong {
  display: block;
}

.logistics-metric span {
  min-height: 26px;
  color: #657381;
  font-size: 11px;
  line-height: 1.35;
}

.logistics-metric strong {
  margin-top: 5px;
  color: #1c2733;
  font-size: 22px;
  font-variant-numeric: tabular-nums;
}

.logistics-metric.danger strong {
  color: #df3f48;
}

.logistics-metric.selected {
  border-color: #29ba72;
  box-shadow: 0 0 0 1px #29ba72 inset;
}

.logistics-filter {
  display: flex;
  align-items: center;
  gap: 9px;
  margin-bottom: 8px;
}

.logistics-filter .select {
  width: 170px;
}

.logistics-count {
  margin-left: auto;
  color: #7f8c97;
  font-size: 10px;
}

.logistics-table-card {
  border-radius: 0;
}

.logistics-table {
  min-width: 1320px;
}

.logistics-table th,
.logistics-table td {
  white-space: nowrap;
}

.logistics-table td:nth-child(2),
.logistics-table td:nth-child(8) {
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.mono {
  font-family: Consolas, "SFMono-Regular", monospace;
  font-size: 9px;
}

.order-jump {
  padding: 0;
  border: 0;
  color: #1677d2;
  background: transparent;
  font: inherit;
  cursor: pointer;
}

.order-jump:hover,
.order-jump:focus-visible {
  text-decoration: underline;
  outline: none;
}

.process-button {
  min-width: 68px;
  white-space: nowrap;
}

.logistics-badge {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 0 9px;
  border-radius: 999px;
  color: #b24d2e;
  background: #fff0e9;
  font-size: 9px;
}

.logistics-badge.stagnant {
  color: #9e7211;
  background: #fff6d9;
}

.logistics-badge.after_sale {
  color: #8f4db1;
  background: #f6eafc;
}

.logistics-empty {
  height: 50px !important;
  color: #8b95a0;
  background: #fff;
  text-align: center;
}

.logistics-pagination {
  justify-content: flex-end;
  border-top: 0;
}

.page-size-control {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: #657381;
  font-size: 10px;
}

.page-size-input {
  width: 72px;
}

.jump-label {
  color: #657381;
  font-size: 10px;
}

.jump-input {
  width: 58px;
}

@media (max-width: 1280px) {
  .logistics-metrics {
    grid-template-columns: repeat(5, minmax(120px, 1fr));
  }
}
</style>
