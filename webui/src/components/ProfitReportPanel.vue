<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { call, formatDateTime, formatMoney } from "../api";
import Icon from "./Icon.vue";
import ModalDialog from "./ModalDialog.vue";

const loading = ref(false);
const error = ref("");
const busy = ref("");
const detailRow = ref(null);
const logOpen = ref(false);
const logs = ref([]);
const report = ref({
  orders: [],
  aggregate: [],
  summary: {},
  choices: {
    platforms: [],
    stores: [],
    groups: [],
    accounts: [],
    statuses: [],
  },
});
const filters = reactive({
  view: "利润统计",
  period: "订单报表",
  platform: "全部平台",
  store_id: "",
  group_name: "全部分组",
  account_label: "全部采购账号",
  service_name: "",
  order_no: "",
  product_id: "",
  source: "全部数据",
  status: "全部状态",
  start_date: dateOffset(-30),
  end_date: dateOffset(0),
});

const rows = computed(() => (
  filters.view === "利润统计" ? report.value.orders : report.value.aggregate
));
const summary = computed(() => report.value.summary || {});

function dateOffset(days) {
  const value = new Date();
  value.setDate(value.getDate() + days);
  return value.toISOString().slice(0, 10);
}

function optionalMoney(value, pending = false) {
  if (pending) return "待核算";
  if (value === null || value === undefined || value === "") return "暂无数据";
  return formatMoney(value);
}

function optionalMargin(value, pending = false) {
  if (pending || value === null || value === undefined) return "暂无数据";
  return `${Number(value).toFixed(2)}%`;
}

function textValue(value) {
  return value === null || value === undefined || value === "" ? "暂无数据" : String(value);
}

function summaryCalculatedMoney(value) {
  if (Number(summary.value.orders || 0) > 0
      && Number(summary.value.pending || 0) === Number(summary.value.orders || 0)) {
    return "待核算";
  }
  if (!Number(summary.value.orders || 0)) return "暂无数据";
  return formatMoney(value);
}

function aggregateCalculatedMoney(row, value) {
  if (Number(row.orders || 0) > 0 && Number(row.pending || 0) === Number(row.orders || 0)) {
    return "待核算";
  }
  return formatMoney(value);
}

async function load() {
  loading.value = true;
  error.value = "";
  try {
    report.value = await call("orders.profit.report", { ...filters });
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    loading.value = false;
  }
}

async function setView(view) {
  filters.view = view;
  await load();
}

async function setPeriod(period) {
  filters.period = period;
  await load();
}

async function exportRows() {
  if (!rows.value.length) {
    error.value = "当前筛选结果没有可导出的利润数据";
    return;
  }
  busy.value = "export";
  try {
    await call("orders.profit.download-log", {
      view: filters.view,
      period: filters.period,
      count: rows.value.length,
    });
    const headers = filters.view === "利润统计"
      ? [
          "店铺", "订单号", "成交时间", "商品ID", "商品标题", "商品数量",
          "子订单号", "交易状态", "买家", "实收款", "佣金", "平台费用",
          "采购订单号", "采购时间", "采购ID", "采购数量", "采购状态",
          "采购账号", "采购价", "利润", "利润率", "核算状态",
        ]
      : [
          "统计周期", "对象", "订单数", "商品件数", "实收款",
          "采购成本", "佣金", "平台费用", "利润", "利润率", "待核算订单",
        ];
    const body = filters.view === "利润统计"
      ? rows.value.map((row) => [
          row.store_name,
          row.order_id,
          formatDateTime(row.create_time),
          row.product_ids?.join(", "),
          row.product_title,
          row.item_count,
          row.sub_order_id,
          row.status_text,
          row.buyer_name,
          Number(row.sale_amount || 0) / 100,
          Number(row.commission || 0) / 100,
          Number(row.platform_fee || 0) / 100,
          row.purchase_order_no,
          row.purchase_time,
          row.purchase_id,
          row.purchase_count,
          row.purchase_status,
          row.purchase_account,
          row.pending ? "待核算" : Number(row.purchase_price || 0) / 100,
          row.pending ? "待核算" : Number(row.profit || 0) / 100,
          row.pending ? "待核算" : row.margin,
          row.pending ? "待核算" : "已核算",
        ])
      : rows.value.map((row) => [
          row.period,
          row.label,
          row.orders,
          row.item_count,
          Number(row.sale_amount || 0) / 100,
          Number(row.cost || 0) / 100,
          Number(row.commission || 0) / 100,
          Number(row.platform_fee || 0) / 100,
          Number(row.profit || 0) / 100,
          row.margin,
          row.pending,
        ]);
    const csv = [headers, ...body].map((row) => row.map((value) => (
      `"${String(value ?? "").replaceAll('"', '""')}"`
    )).join(",")).join("\r\n");
    const url = URL.createObjectURL(new Blob([`\ufeff${csv}`], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `${filters.view}_${filters.period}_${filters.start_date}_${filters.end_date}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    busy.value = "";
  }
}

async function showDownloadLogs() {
  busy.value = "logs";
  try {
    const result = await call("orders.workbench.logs", { limit: 1000 });
    logs.value = (result.logs || []).filter((row) => (
      String(row.scope || "") === "profit"
      || String(row.operation || "").includes("利润")
    ));
    logOpen.value = true;
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    busy.value = "";
  }
}

function closeError() {
  error.value = "";
}

onMounted(load);
</script>

<template>
  <section class="profit-report-panel">
    <header class="profit-head">
      <div>
        <h2>利润统计</h2>
        <p>基于订单、采购关联和工作台成本核算真实利润；未关联采购显示待核算。</p>
      </div>
      <div class="profit-head-actions">
        <button class="btn" type="button" :disabled="Boolean(busy)" @click="showDownloadLogs">{{ busy === "logs" ? "读取中..." : "下载记录" }}</button>
        <button class="btn" type="button" :disabled="Boolean(busy)" @click="exportRows">{{ busy === "export" ? "导出中..." : "导出" }}</button>
        <button class="btn primary" type="button" :disabled="loading" @click="load">{{ loading ? "统计中..." : "开始统计" }}</button>
      </div>
    </header>

    <div class="profit-view-tabs">
      <button v-for="view in ['利润统计', '店铺统计', '商品统计']" :key="view" type="button" :class="{ active: filters.view === view }" @click="setView(view)">{{ view }}</button>
    </div>
    <div class="profit-period-tabs">
      <button v-for="period in ['订单报表', '日报表', '周报表', '月报表', '年报表']" :key="period" type="button" :class="{ active: filters.period === period }" @click="setPeriod(period)">{{ period }}</button>
    </div>

    <div class="profit-filters">
      <label>平台<select v-model="filters.platform" class="select"><option>全部平台</option><option v-for="item in report.choices.platforms" :key="item">{{ item }}</option></select></label>
      <label>店铺<select v-model="filters.store_id" class="select"><option value="">全部店铺</option><option v-for="store in report.choices.stores" :key="store.id" :value="store.id">{{ store.name }}</option></select></label>
      <label>分组<select v-model="filters.group_name" class="select"><option>全部分组</option><option v-for="group in report.choices.groups" :key="group">{{ group }}</option></select></label>
      <label>采购账号<select v-model="filters.account_label" class="select"><option>全部采购账号</option><option v-for="account in report.choices.accounts" :key="account">{{ account }}</option></select></label>
      <label>客服名称<input v-model="filters.service_name" class="input" placeholder="订单或店铺备注包含" /></label>
      <label>卖出单号<input v-model="filters.order_no" class="input" /></label>
      <label>商品 ID<input v-model="filters.product_id" class="input" /></label>
      <label>数据来源<select v-model="filters.source" class="select"><option>全部数据</option><option>已采购</option><option>未采购</option></select></label>
      <label>交易状态<select v-model="filters.status" class="select"><option v-for="status in report.choices.statuses" :key="status">{{ status }}</option></select></label>
      <label>成交开始<input v-model="filters.start_date" class="input" type="date" /></label>
      <label>成交结束<input v-model="filters.end_date" class="input" type="date" /></label>
    </div>

    <div v-if="error" class="profit-error">
      <span>{{ error }}</span><button type="button" @click="closeError">关闭</button>
    </div>

    <div class="profit-summary">
      <div><span>订单数</span><strong>{{ summary.orders ?? 0 }}</strong></div>
      <div><span>实收款</span><strong>{{ formatMoney(summary.sale) }}</strong></div>
      <div><span>采购成本</span><strong>{{ summaryCalculatedMoney(summary.cost) }}</strong></div>
      <div><span>佣金</span><strong>{{ formatMoney(summary.commission) }}</strong></div>
      <div><span>平台费用</span><strong>{{ formatMoney(summary.platform_fee) }}</strong></div>
      <div><span>已核算利润</span><strong>{{ summaryCalculatedMoney(summary.profit) }}</strong></div>
      <div><span>待核算</span><strong>{{ summary.pending ?? 0 }} 单</strong></div>
    </div>

    <div class="profit-table-wrap">
      <div v-if="loading" class="profit-loading"><span class="profit-spinner" />正在统计真实利润数据…</div>
      <table v-if="filters.view === '利润统计'" class="profit-table order-profit-table">
        <thead>
          <tr>
            <th>店铺名称</th><th>订单号 / 成交时间</th><th>商品 ID / 名称</th><th>卖出数量</th><th>子订单号</th><th>交易状态</th><th>买家</th><th>实收款</th><th>佣金</th><th>平台费用</th><th>采购订单 / 时间</th><th>采购 ID / 数量</th><th>采购状态</th><th>采购账号</th><th>采购价</th><th>利润</th><th>利润率</th><th>详情</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="`${row.store_id}:${row.order_id}`">
            <td>{{ textValue(row.store_name) }}</td>
            <td><strong>{{ textValue(row.order_id) }}</strong><span>{{ formatDateTime(row.create_time) }}</span></td>
            <td><strong>{{ row.product_ids?.join(", ") || "暂无数据" }}</strong><span>{{ textValue(row.product_title) }}</span></td>
            <td>{{ row.item_count }}</td>
            <td>{{ textValue(row.sub_order_id) }}</td>
            <td>{{ textValue(row.status_text) }}</td>
            <td>{{ textValue(row.buyer_name) }}</td>
            <td>{{ formatMoney(row.sale_amount) }}</td>
            <td>{{ row.commission ? formatMoney(row.commission) : "暂无数据" }}</td>
            <td>{{ row.platform_fee ? formatMoney(row.platform_fee) : "暂无数据" }}</td>
            <td><strong>{{ textValue(row.purchase_order_no) }}</strong><span>{{ row.purchase_time || "暂无数据" }}</span></td>
            <td><strong>{{ textValue(row.purchase_id) }}</strong><span>数量 {{ row.purchase_count || "暂无数据" }}</span></td>
            <td>{{ textValue(row.purchase_status) }}</td>
            <td>{{ textValue(row.purchase_account) }}</td>
            <td>{{ optionalMoney(row.purchase_price, row.pending) }}</td>
            <td :class="{ pending: row.pending }">{{ optionalMoney(row.profit, row.pending) }}</td>
            <td>{{ optionalMargin(row.margin, row.pending) }}</td>
            <td><button class="mini-link" type="button" @click="detailRow = row">查看</button></td>
          </tr>
          <tr v-if="!loading && !rows.length"><td colspan="18" class="profit-empty">暂无利润数据</td></tr>
        </tbody>
      </table>

      <table v-else class="profit-table">
        <thead><tr><th>统计周期</th><th>{{ filters.view === "店铺统计" ? "店铺名称" : "商品 ID / 名称" }}</th><th>订单数</th><th>商品件数</th><th>实收款</th><th>采购成本</th><th>佣金</th><th>平台费用</th><th>利润</th><th>利润率</th><th>待核算订单</th><th>详情</th></tr></thead>
        <tbody>
          <tr v-for="row in rows" :key="`${row.period}:${row.label}`">
            <td>{{ row.period }}</td><td>{{ row.label }}</td><td>{{ row.orders }}</td><td>{{ row.item_count }}</td><td>{{ formatMoney(row.sale_amount) }}</td><td>{{ aggregateCalculatedMoney(row, row.cost) }}</td><td>{{ formatMoney(row.commission) }}</td><td>{{ formatMoney(row.platform_fee) }}</td><td>{{ aggregateCalculatedMoney(row, row.profit) }}</td><td>{{ optionalMargin(row.margin) }}</td><td>{{ row.pending }}</td><td><button class="mini-link" type="button" @click="detailRow = row">查看</button></td>
          </tr>
          <tr v-if="!loading && !rows.length"><td colspan="12" class="profit-empty">暂无统计数据</td></tr>
        </tbody>
      </table>
    </div>

    <ModalDialog :open="Boolean(detailRow)" title="利润统计详情" width="760px" @close="detailRow = null">
      <pre class="profit-detail-json">{{ JSON.stringify(detailRow, null, 2) }}</pre>
      <template #footer><button class="btn" type="button" @click="detailRow = null">关闭</button></template>
    </ModalDialog>

    <ModalDialog :open="logOpen" title="利润统计下载记录" width="900px" @close="logOpen = false">
      <div class="profit-log-list">
        <div v-for="(row, index) in logs" :key="index"><time>{{ row.started_at || "暂无数据" }}</time><strong>{{ row.operation || "暂无数据" }}</strong><span>{{ row.status || "暂无数据" }}</span><p>{{ row.message || "暂无数据" }}</p></div>
        <div v-if="!logs.length" class="profit-empty">暂无利润统计下载记录</div>
      </div>
      <template #footer><button class="btn" type="button" @click="logOpen = false">关闭</button></template>
    </ModalDialog>
  </section>
</template>

<style scoped>
.profit-report-panel { min-width: 1280px; padding: 16px; }
.profit-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 10px; }
.profit-head h2 { margin: 0; font-size: 17px; }
.profit-head p { margin: 4px 0 0; color: #738598; font-size: 10px; }
.profit-head-actions { display: flex; gap: 7px; }
.profit-view-tabs,
.profit-period-tabs { margin-bottom: 8px; display: flex; gap: 6px; }
.profit-view-tabs button,
.profit-period-tabs button { height: 29px; padding: 0 11px; border: 1px solid #d9e2e9; border-radius: 15px; color: #526577; background: #fff; font-size: 10px; }
.profit-view-tabs button.active,
.profit-period-tabs button.active { border-color: #173d5c; color: #fff; background: #173d5c; }
.profit-filters { margin-bottom: 9px; padding: 9px; display: grid; grid-template-columns: repeat(6, minmax(130px, 1fr)); gap: 7px; border: 1px solid #dce4eb; border-radius: 7px; background: white; }
.profit-filters label { display: grid; gap: 4px; color: #718395; font-size: 9px; }
.profit-filters .input,
.profit-filters .select { width: 100%; height: 29px; font-size: 9px; }
.profit-error { margin-bottom: 8px; padding: 8px 10px; display: flex; justify-content: space-between; border: 1px solid #efc7cb; border-radius: 6px; color: #a8323c; background: #fff3f5; font-size: 10px; }
.profit-error button { border: 0; color: #a8323c; background: transparent; }
.profit-summary { margin-bottom: 9px; display: grid; grid-template-columns: repeat(7, 1fr); border: 1px solid #dce4eb; border-radius: 7px; background: #fff; overflow: hidden; }
.profit-summary > div { min-height: 64px; padding: 10px; border-right: 1px solid #e4eaf0; }
.profit-summary > div:last-child { border-right: 0; }
.profit-summary span,
.profit-summary strong { display: block; }
.profit-summary span { color: #758797; font-size: 9px; }
.profit-summary strong { margin-top: 5px; color: #11724e; font-size: 14px; }
.profit-table-wrap { max-height: 590px; overflow: auto; border: 1px solid #dce4eb; border-radius: 7px; background: #fff; }
.profit-loading { position: sticky; top: 0; left: 0; z-index: 4; height: 34px; display: flex; align-items: center; justify-content: center; gap: 7px; color: #4e6980; background: #eef7ff; font-size: 10px; }
.profit-spinner { width: 12px; height: 12px; border: 2px solid #c2d0dc; border-top-color: #2d7daf; border-radius: 50%; animation: profit-spin 700ms linear infinite; }
@keyframes profit-spin { to { transform: rotate(360deg); } }
.profit-table { width: 100%; border-collapse: collapse; white-space: nowrap; font-size: 9px; }
.order-profit-table { min-width: 2100px; }
.profit-table th { position: sticky; top: 0; z-index: 2; height: 36px; padding: 0 7px; color: #637587; background: #f7f9fb; text-align: left; }
.profit-table td { height: 43px; padding: 5px 7px; border-top: 1px solid #e5ebf0; }
.profit-table td strong,
.profit-table td span { display: block; }
.profit-table td strong { max-width: 230px; overflow: hidden; color: #2c4052; text-overflow: ellipsis; }
.profit-table td span { margin-top: 2px; color: #8291a0; }
.profit-table td.pending { color: #a46b13; }
.mini-link { padding: 0; border: 0; color: #2e6f9f; background: transparent; font-size: 9px; }
.profit-empty { height: 180px; color: #94a1ad; text-align: center; }
.profit-detail-json { margin: 0; max-height: 60vh; overflow: auto; white-space: pre-wrap; word-break: break-word; color: #32485b; background: #f7f9fb; font: 10px/1.6 Consolas, monospace; }
.profit-log-list { max-height: 520px; overflow: auto; display: grid; gap: 6px; }
.profit-log-list > div { padding: 8px; display: grid; grid-template-columns: 160px 170px 80px minmax(0, 1fr); gap: 8px; border: 1px solid #e1e7ed; border-radius: 5px; color: #5f7284; font-size: 9px; }
.profit-log-list p { margin: 0; }
</style>
