<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { call, formatMoney } from "../api";
import Icon from "../components/Icon.vue";

const stores = ref([]);
const rows = ref([]);
const loading = ref(false);
const note = ref("");
const dateRange = reactive({ start_date: "", end_date: "" });

function formatDateOffset(days) {
  const value = new Date();
  value.setDate(value.getDate() - days);
  return [
    value.getFullYear(),
    String(value.getMonth() + 1).padStart(2, "0"),
    String(value.getDate()).padStart(2, "0"),
  ].join("-");
}

dateRange.start_date = formatDateOffset(30);
dateRange.end_date = formatDateOffset(0);

const aggregate = computed(() => {
  const complete = rows.value.length > 0 && rows.value.every((row) => (
    row.available_amount !== null
    && row.available_amount !== undefined
    && row.pending_amount !== null
    && row.pending_amount !== undefined
  ));
  if (!complete) {
    return {
      available_amount: null,
      pending_amount: null,
      flow_count: rows.value.reduce((sum, row) => sum + Number(row.flow_count || 0), 0),
    };
  }
  return {
    available_amount: rows.value.reduce((sum, row) => sum + Number(row.available_amount || 0), 0),
    pending_amount: rows.value.reduce((sum, row) => sum + Number(row.pending_amount || 0), 0),
    flow_count: rows.value.reduce((sum, row) => sum + Number(row.flow_count || 0), 0),
  };
});

function moneyOrEmpty(value) {
  return value === null || value === undefined ? "暂无数据" : formatMoney(value);
}

function exportFlows() {
  const records = rows.value.flatMap((row) => (
    (row.flow_list || []).map((flow) => ({
      store_name: row.store_name,
      ...flow,
    }))
  ));
  if (!records.length) {
    note.value = "当前查询结果没有可导出的资金流水";
    return;
  }
  const header = ["店铺", "流水编号", "类型", "金额（分）", "发生时间", "状态"];
  const lines = records.map((row) => [
    row.store_name,
    row.flow_id,
    row.type,
    row.amount ?? "",
    row.create_time,
    row.status,
  ]);
  const csv = [header, ...lines]
    .map((line) => line.map((value) => `"${String(value ?? "").replaceAll('"', '""')}"`).join(","))
    .join("\r\n");
  const url = URL.createObjectURL(new Blob([`\uFEFF${csv}`], { type: "text/csv;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `澜琅资金流水_${dateRange.start_date}_${dateRange.end_date}.csv`;
  anchor.click();
  URL.revokeObjectURL(url);
  note.value = `已导出 ${records.length} 条资金流水`;
}

async function loadStores() {
  const result = await call("stores.list", {});
  stores.value = result.stores || [];
}

async function loadFinance() {
  if (loading.value) return;
  loading.value = true;
  note.value = `正在读取 ${stores.value.length || "全部"} 家店铺的官方资金接口…`;
  try {
    const result = await call("finance.summary", {
      start_date: dateRange.start_date,
      end_date: dateRange.end_date,
    });
    rows.value = result.rows || [];
    note.value = result.errors?.length
      ? `资金查询完成：成功 ${result.succeeded}/${result.store_count} 家；${result.errors.slice(0, 2).join("；")}`
      : `资金查询完成：${result.succeeded} 家店铺，${aggregate.value.flow_count} 条流水`;
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  try {
    await loadStores();
    await loadFinance();
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
  }
});
</script>

<template>
  <section class="page finance-page">
    <header class="page-header">
      <div>
        <h1>资金账户</h1>
        <p>余额和流水来自各店铺微信小店官方资金接口，不使用本地估算值。</p>
      </div>
      <div class="toolbar">
        <input v-model="dateRange.start_date" class="input" type="date" :disabled="loading" />
        <span>至</span>
        <input v-model="dateRange.end_date" class="input" type="date" :disabled="loading" />
        <button class="btn primary" type="button" :disabled="loading || stores.length === 0" @click="loadFinance">
          <Icon name="refresh" :size="15" /> {{ loading ? "读取中…" : "刷新资金" }}
        </button>
        <button class="btn" type="button" :disabled="loading || aggregate.flow_count === 0" @click="exportFlows">
          导出流水
        </button>
      </div>
    </header>

    <div class="finance-summary">
      <article class="card">
        <span>可提现总额</span>
        <strong>{{ moneyOrEmpty(aggregate.available_amount) }}</strong>
        <small>任一家店铺缺少字段时不做合计</small>
      </article>
      <article class="card">
        <span>暂预留总额</span>
        <strong>{{ moneyOrEmpty(aggregate.pending_amount) }}</strong>
        <small>来自官方余额接口 pending_amount</small>
      </article>
      <article class="card">
        <span>资金流水</span>
        <strong>{{ aggregate.flow_count }} 条</strong>
        <small>{{ dateRange.start_date }} 至 {{ dateRange.end_date }}</small>
      </article>
      <article class="card">
        <span>查询店铺</span>
        <strong>{{ rows.length }} / {{ stores.length }}</strong>
        <small>失败店铺在提示中单独列出</small>
      </article>
    </div>

    <div class="card finance-table-card">
      <div class="table-scroll">
        <table class="data-table">
          <thead>
            <tr>
              <th>店铺</th>
              <th>登录状态</th>
              <th>可提现</th>
              <th>暂预留</th>
              <th>待结算</th>
              <th>预计结算</th>
              <th>流水</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="row in rows" :key="row.store_id">
              <tr>
                <td><strong>{{ row.store_name }}</strong></td>
                <td>{{ row.web_status || "暂无数据" }}</td>
                <td>{{ moneyOrEmpty(row.available_amount) }}</td>
                <td>{{ moneyOrEmpty(row.pending_amount) }}</td>
                <td>{{ moneyOrEmpty(row.settle_amount) }}</td>
                <td>{{ moneyOrEmpty(row.estimated_amount) }}</td>
                <td>{{ row.flow_count }} 条</td>
              </tr>
              <tr v-if="row.flow_list?.length" class="flow-detail-row">
                <td colspan="7">
                  <details>
                    <summary>查看 {{ row.store_name }} 的资金流水</summary>
                    <table class="data-table flow-table">
                      <thead>
                        <tr><th>流水编号</th><th>类型</th><th>金额</th><th>发生时间</th><th>状态</th></tr>
                      </thead>
                      <tbody>
                        <tr v-for="flow in row.flow_list" :key="`${row.store_id}:${flow.flow_id}`">
                          <td>{{ flow.flow_id || "暂无数据" }}</td>
                          <td>{{ flow.type || "暂无数据" }}</td>
                          <td>{{ moneyOrEmpty(flow.amount) }}</td>
                          <td>{{ flow.create_time || "暂无数据" }}</td>
                          <td>{{ flow.status || "暂无数据" }}</td>
                        </tr>
                      </tbody>
                    </table>
                  </details>
                </td>
              </tr>
            </template>
            <tr v-if="!loading && rows.length === 0">
              <td colspan="7" class="empty-state">当前没有可展示的真实资金数据</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div v-if="loading" class="loading-banner"><span class="loading-dot" />{{ note }}</div>
    <div v-else-if="note" class="toast">{{ note }}</div>
  </section>
</template>

<style scoped>
.finance-page { display: grid; gap: 14px; }
.finance-summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.finance-summary article { min-height: 112px; padding: 18px; display: grid; align-content: space-between; }
.finance-summary span, .finance-summary small { color: #7b8995; font-size: 12px; }
.finance-summary strong { color: #203040; font-size: 24px; font-variant-numeric: tabular-nums; }
.finance-table-card { min-height: 420px; }
.flow-detail-row > td { padding: 0; }
.flow-detail-row details { padding: 12px 20px 18px; background: #f8fbfd; }
.flow-detail-row summary { margin-bottom: 10px; color: #2874a6; cursor: pointer; }
.flow-table { font-size: 12px; }
.loading-dot { width: 9px; height: 9px; border-radius: 50%; background: #1fb36b; animation: pulse .8s ease-in-out infinite alternate; }
.loading-banner { min-height: 42px; padding: 10px 14px; display: flex; align-items: center; gap: 9px; border: 1px solid #b8dcc9; color: #26714f; background: #f1fbf6; }
@keyframes pulse { from { opacity: .3; } to { opacity: 1; } }
</style>
