<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { call } from "../api";
import Icon from "../components/Icon.vue";
import ModalDialog from "../components/ModalDialog.vue";

const stores = ref([]);
const storeId = ref("");
const products = ref([]);
const loading = ref(false);
const note = ref("");
const confirmation = reactive({
  open: false,
  product: null,
  action: "",
  value: 1,
  label: "",
  description: "",
});

const currentStore = computed(() => stores.value.find((store) => String(store.id) === String(storeId.value)) || null);

async function loadStores() {
  stores.value = (await call("stores.list", {})).stores || [];
  if (!storeId.value && stores.value[0]) storeId.value = stores.value[0].id;
}

async function loadStorefront(force = false) {
  if (!storeId.value || (loading.value && !force)) return;
  loading.value = true;
  note.value = "正在读取微信小店店铺主页…";
  try {
    const result = await call("storefront.load", { store_id: storeId.value });
    products.value = result.products || [];
    note.value = result.errors?.length
      ? `店铺主页读取完成：${result.count || 0} 个商品，详情失败 ${result.errors.length} 个；${result.errors.slice(0, 2).join("；")}`
      : `店铺主页读取完成：${result.count || 0} 个商品`;
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    loading.value = false;
  }
}

async function changeStore() {
  products.value = [];
  await loadStorefront();
}

function requestAction(product, action, value, label, description) {
  Object.assign(confirmation, {
    open: true,
    product,
    action,
    value,
    label,
    description,
  });
}

function requestReorder(product, direction) {
  const index = products.value.findIndex((row) => row.product_id === product.product_id);
  const target = Math.max(0, index + direction);
  if (target === index) {
    note.value = direction < 0 ? "该商品已经在最前面" : "该商品已经在最后面";
    return;
  }
  requestAction(
    product,
    "reorder",
    target,
    "调整店铺主页排序",
    `将“${product.title || product.product_id}”移动到第 ${target + 1} 位。该操作会调用官方店铺主页接口。`,
  );
}

async function confirmAction() {
  if (loading.value || !confirmation.product) return;
  loading.value = true;
  try {
    await call("storefront.action", {
      store_id: storeId.value,
      product_id: confirmation.product.product_id,
      action: confirmation.action,
      value: confirmation.value,
    });
    confirmation.open = false;
    note.value = `${confirmation.label}完成`;
    await loadStorefront(true);
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  try {
    await loadStores();
    await loadStorefront();
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
  }
});
</script>

<template>
  <section class="page storefront-page">
    <header class="page-header">
      <div>
        <h1>店铺主页</h1>
        <p>读取官方店铺主页商品，并执行排序、隐藏和置顶。所有写操作都有确认和持久日志。</p>
      </div>
      <div class="toolbar">
        <select v-model="storeId" class="select store-select" :disabled="loading" @change="changeStore">
          <option v-for="store in stores" :key="store.id" :value="store.id">{{ store.name }}</option>
        </select>
        <button class="btn primary" type="button" :disabled="loading || !storeId" @click="loadStorefront">
          <Icon name="refresh" :size="15" /> {{ loading ? "处理中…" : "读取店铺主页" }}
        </button>
      </div>
    </header>

    <div class="storefront-summary card">
      <strong>{{ currentStore?.name || "未选择店铺" }}</strong>
      <span>{{ currentStore?.web_status || "登录态暂无数据" }}</span>
      <b>{{ products.length }} 个主页商品</b>
    </div>

    <div class="card table-card">
      <div class="table-scroll">
        <table class="data-table">
          <thead>
            <tr><th>顺序</th><th>商品</th><th>商品 ID</th><th>状态</th><th>排序</th><th>显示</th><th>置顶</th></tr>
          </thead>
          <tbody>
            <tr v-for="(product, index) in products" :key="product.product_id">
              <td>{{ index + 1 }}</td>
              <td><strong>{{ product.title || "暂无标题" }}</strong></td>
              <td class="mono">{{ product.product_id }}</td>
              <td>{{ product.status_text || "暂无数据" }}</td>
              <td class="actions">
                <button type="button" :disabled="loading || index === 0" @click="requestReorder(product, -1)">上移</button>
                <button type="button" :disabled="loading || index === products.length - 1" @click="requestReorder(product, 1)">下移</button>
              </td>
              <td class="actions">
                <button type="button" :disabled="loading" @click="requestAction(product, 'hide', 1, '隐藏主页商品', `确定在店铺主页隐藏“${product.title || product.product_id}”吗？`)">设为隐藏</button>
                <button type="button" :disabled="loading" @click="requestAction(product, 'hide', 0, '取消隐藏', `确定在店铺主页重新显示“${product.title || product.product_id}”吗？`)">取消隐藏</button>
              </td>
              <td class="actions">
                <button type="button" :disabled="loading" @click="requestAction(product, 'top', 1, '置顶主页商品', `确定将“${product.title || product.product_id}”置顶吗？`)">置顶</button>
                <button type="button" :disabled="loading" @click="requestAction(product, 'top', 0, '取消置顶', `确定取消“${product.title || product.product_id}”的置顶吗？`)">取消置顶</button>
              </td>
            </tr>
            <tr v-if="!loading && products.length === 0">
              <td colspan="7" class="empty-state">当前店铺暂无可展示的主页商品</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <ModalDialog :open="confirmation.open" :title="confirmation.label" :subtitle="confirmation.product?.title || confirmation.product?.product_id || ''" width="560px" :busy="loading" @close="confirmation.open = false">
      <p class="risk-copy">{{ confirmation.description }}</p>
      <p class="risk-note">该操作会真实调用当前店铺的微信小店店铺主页接口，结果写入持久操作日志。</p>
      <template #footer>
        <button class="btn" type="button" :disabled="loading" @click="confirmation.open = false">取消</button>
        <button class="btn primary" type="button" :disabled="loading" @click="confirmAction">{{ loading ? "执行中…" : "确认执行" }}</button>
      </template>
    </ModalDialog>

    <div v-if="note" class="toast">{{ note }}</div>
  </section>
</template>

<style scoped>
.storefront-page { display: grid; gap: 14px; }
.store-select { width: 300px; }
.storefront-summary { min-height: 76px; padding: 16px 20px; display: flex; align-items: center; gap: 18px; }
.storefront-summary span { color: #788692; }
.storefront-summary b { margin-left: auto; color: #1b9160; }
.table-card { min-height: 460px; }
.actions { white-space: nowrap; }
.actions button { margin-right: 6px; padding: 4px 7px; border: 0; color: #2e75a8; background: transparent; cursor: pointer; }
.mono { font-family: "Cascadia Code", Consolas, monospace; }
.risk-copy { line-height: 1.8; }
.risk-note { color: #8c641d; background: #fff8e7; padding: 10px; }
</style>
