<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { call, formatDateTime, formatMoney } from "../api";
import Icon from "../components/Icon.vue";
import ModalDialog from "../components/ModalDialog.vue";

const orders = ref([]);
const stores = ref([]);
const loading = ref(false);
const note = ref("");
const selected = ref(null);
const filtersOpen = ref(true);
const activeTab = ref("");
const page = ref(1);
const pageSize = 50;
const filters = reactive({
  store_id: "",
  search: "",
});

const tabs = [
  { key: "", label: "全部" },
  { key: "10", label: "待付款" },
  { key: "12", label: "礼物待收下" },
  { key: "20", label: "待采购" },
  { key: "21", label: "待发货" },
  { key: "30", label: "已发货" },
  { key: "100", label: "已完成" },
  { key: "after_sale", label: "售后订单" },
  { key: "marked", label: "标记" },
];

const visibleOrders = computed(() => {
  const text = filters.search.trim().toLowerCase();
  return orders.value.filter((order) => {
    if (
      filters.store_id
      && String(order.store_id) !== String(filters.store_id)
    ) return false;
    if (activeTab.value && activeTab.value !== "after_sale" && activeTab.value !== "marked") {
      if (activeTab.value === "20") {
        if (![17, 20].includes(Number(order.status))) return false;
      } else if (String(order.status) !== activeTab.value) {
        return false;
      }
    }
    if (!text) return true;
    return [
      order.order_id,
      order.buyer_name,
      order.phone,
      order.item_summary,
      order.store_name,
    ].some((value) => String(value || "").toLowerCase().includes(text));
  });
});

const pageCount = computed(() => Math.max(1, Math.ceil(visibleOrders.value.length / pageSize)));
const pagedOrders = computed(() => {
  const start = (page.value - 1) * pageSize;
  return visibleOrders.value.slice(start, start + pageSize);
});

function tabCount(key) {
  if (!key) return orders.value.length;
  if (key === "20") {
    return orders.value.filter((order) => [17, 20].includes(Number(order.status))).length;
  }
  if (key === "after_sale" || key === "marked") return 0;
  return orders.value.filter((order) => String(order.status) === key).length;
}

async function load() {
  loading.value = true;
  note.value = "";
  try {
    const [orderData, storeData] = await Promise.all([
      call("orders.list", { limit: 1000 }),
      call("stores.list", {}),
    ]);
    orders.value = orderData.orders || [];
    stores.value = storeData.stores || [];
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    loading.value = false;
  }
}

async function syncOrders() {
  loading.value = true;
  note.value = "";
  try {
    const result = await call("orders.sync", {
      store_id: filters.store_id || undefined,
    });
    orders.value = result.orders || [];
    note.value = result.errors?.length
      ? `已同步 ${result.succeeded}/${result.synced} 家；${result.errors.slice(0, 2).join("；")}`
      : `已同步 ${result.succeeded} 家店铺订单`;
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    loading.value = false;
  }
}

function applyFilters() {
  page.value = 1;
}

function openOrder(order) {
  selected.value = order;
}

async function openWorkbench(order = null) {
  try {
    await call("workbench.open", {
      store_id: order?.store_id,
      order_id: order?.order_id,
    });
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
  }
}

function featureNotice(label) {
  note.value = `${label}入口已接通，后台业务尚在迁移。`;
}

onMounted(load);
</script>

<template>
  <section class="page orders-page">
    <header class="page-header">
      <div>
        <h1>订单管理</h1>
      </div>
      <div class="toolbar order-toolbar">
        <button class="btn muted" type="button" @click="filtersOpen = !filtersOpen">过滤订单</button>
        <button class="btn muted" type="button" @click="featureNotice('导出')">导出</button>
        <button class="btn muted" type="button" @click="featureNotice('导入订单信息')">导入订单信息</button>
        <button class="btn muted" type="button" @click="featureNotice('发轨迹单')">发轨迹单</button>
        <button class="btn muted" type="button" @click="featureNotice('手动添加')">手动添加</button>
        <button class="btn primary" type="button" @click="featureNotice('打单设置')">打单设置</button>
        <button class="btn primary" type="button" @click="featureNotice('同步/礼品设置')">同步/礼品设置</button>
        <button class="btn muted" type="button" @click="featureNotice('监控发货')">监控发货</button>
        <button class="btn primary" type="button" :disabled="loading" @click="syncOrders">
          <Icon name="refresh" :size="15" /> 同步订单
        </button>
      </div>
    </header>

    <div class="order-tabs">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="chip"
        :class="{ active: activeTab === tab.key }"
        type="button"
        @click="activeTab = tab.key; page = 1"
      >{{ tab.label }} <span v-if="tabCount(tab.key)">{{ tabCount(tab.key) }}</span></button>
    </div>

    <div v-if="filtersOpen" class="card filter-card order-filter-card">
      <div class="filter-row">
        <label>店铺:</label>
        <select v-model="filters.store_id" class="select" @change="applyFilters">
          <option value="">全部店铺</option>
          <option v-for="store in stores" :key="store.id" :value="store.id">
            {{ store.name }}
          </option>
        </select>
        <label>时间:</label>
        <input class="input date-input" type="date" />
        <span>至</span>
        <input class="input date-input" type="date" />
        <label>标识:</label>
        <select class="select">
          <option>全部标识</option>
        </select>
      </div>
      <div class="filter-row order-search-row">
        <label>搜索:</label>
        <input v-model="filters.search" class="input order-search" placeholder="订单号支持多个，空格分隔" />
        <button class="btn primary" type="button" @click="applyFilters">搜索</button>
        <button class="btn muted" type="button" @click="filters.search = ''; filters.store_id = ''; activeTab = ''; page = 1">重置</button>
        <button class="btn primary" type="button" @click="load">快速刷新</button>
        <button class="btn order-workbench" type="button" @click="openWorkbench()">
          <Icon name="desktop" :size="15" /> 订单工作台
        </button>
      </div>
    </div>

    <div class="card order-table-card">
      <div class="table-scroll order-table-scroll">
        <table class="data-table order-table">
          <thead>
            <tr>
              <th>序号</th>
              <th>订单</th>
              <th>商品信息</th>
              <th>利润/成本</th>
              <th>收货地址</th>
              <th>备注</th>
              <th>物流信息</th>
              <th>采购订单</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(order, index) in pagedOrders" :key="`${order.store_id}:${order.order_id}`">
              <td>{{ (page - 1) * pageSize + index + 1 }}</td>
              <td class="order-main-cell">
                <strong>{{ order.order_id }}</strong>
                <span>{{ order.status_text }} · {{ order.store_name }}</span>
              </td>
              <td class="item-cell" :title="order.item_summary">{{ order.item_summary || "-" }}</td>
              <td>
                <strong class="green">{{ formatMoney(order.total_amount) }}</strong>
                <span class="muted">成本 -</span>
              </td>
              <td :title="order.address">{{ order.address || "-" }}</td>
              <td :title="order.merchant_notes">{{ order.merchant_notes || "-" }}</td>
              <td>
                <span>{{ order.carrier || "未发货" }}</span>
                <small>{{ order.waybill_id || "-" }}</small>
              </td>
              <td class="muted">-</td>
              <td>
                <div class="action-group">
                  <button class="pill-button" type="button" @click="openOrder(order)">详情</button>
                  <button class="pill-button gray" type="button" @click="openWorkbench(order)">工作台</button>
                </div>
              </td>
            </tr>
            <tr v-if="!loading && pagedOrders.length === 0">
              <td colspan="9" class="empty-state">暂无订单</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="pagination">
        <span>共 {{ visibleOrders.length }} 条，第 {{ page }} / {{ pageCount }} 页</span>
        <button class="btn" :disabled="page <= 1" type="button" @click="page -= 1">上一页</button>
        <button class="btn" :disabled="page >= pageCount" type="button" @click="page += 1">下一页</button>
      </div>
    </div>

    <ModalDialog
      :open="Boolean(selected)"
      title="订单详情"
      :subtitle="selected?.order_id"
      width="760px"
      @close="selected = null"
    >
      <div v-if="selected" class="order-detail">
        <div><span>店铺</span><strong>{{ selected.store_name }}</strong></div>
        <div><span>状态</span><strong>{{ selected.status_text }}</strong></div>
        <div><span>下单时间</span><strong>{{ formatDateTime(selected.create_time) }}</strong></div>
        <div><span>实付金额</span><strong class="green">{{ formatMoney(selected.total_amount) }}</strong></div>
        <div class="full"><span>商品</span><strong>{{ selected.item_summary || "-" }}</strong></div>
        <div class="full"><span>收货地址</span><strong>{{ selected.address || "-" }}</strong></div>
        <div><span>收货人</span><strong>{{ selected.buyer_name || "-" }}</strong></div>
        <div><span>电话</span><strong>{{ selected.phone || "-" }}</strong></div>
        <div class="full"><span>备注</span><strong>{{ selected.merchant_notes || "-" }}</strong></div>
      </div>
      <template #footer>
        <button class="btn" type="button" @click="selected = null">关闭</button>
        <button class="btn primary" type="button" @click="openWorkbench(selected)">打开订单工作台</button>
      </template>
    </ModalDialog>

    <div v-if="note" class="toast">{{ note }}</div>
  </section>
</template>

<style scoped>
.order-toolbar {
  justify-content: flex-end;
}

.order-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  margin-bottom: 14px;
}

.order-tabs .chip span {
  margin-left: 3px;
  opacity: 0.8;
}

.order-filter-card {
  padding: 12px 14px;
}

.order-filter-card .filter-row + .filter-row {
  margin-top: 10px;
}

.order-search {
  width: 290px;
}

.date-input {
  width: 142px;
}

.order-workbench {
  margin-left: auto;
  border-color: #778594;
  color: white;
  background: #7a8795;
}

.order-table-card {
  display: flex;
  flex-direction: column;
  min-height: 535px;
}

.order-table-scroll {
  min-height: 470px;
}

.order-table {
  min-width: 1540px;
}

.order-main-cell,
.item-cell {
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.order-main-cell strong,
.order-main-cell span,
td strong + span {
  display: block;
}

.order-main-cell span,
td small {
  margin-top: 3px;
  color: var(--muted);
  font-size: 11px;
}

.pagination {
  min-height: 52px;
  padding: 8px 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  border-top: 1px solid var(--line);
  color: #5e6b78;
}

.order-detail {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px 22px;
}

.order-detail > div {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding-bottom: 10px;
  border-bottom: 1px solid #edf0f3;
}

.order-detail .full {
  grid-column: 1 / -1;
}

.order-detail span {
  color: var(--muted);
  font-size: 12px;
}
</style>
