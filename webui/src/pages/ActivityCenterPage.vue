<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { call, formatMoney } from "../api";
import ModalDialog from "../components/ModalDialog.vue";

const tabs = [
  ["platform", "平台活动"],
  ["limited", "限时限量购活动"],
  ["coupon-activity", "优惠券活动"],
  ["snapup-renewal", "限时限量购续期"],
  ["coupon-manage", "优惠券管理"],
  ["records", "执行记录"],
];

const stores = ref([]);
const selectedStoreIds = ref([]);
const activeTab = ref("platform");
const sourceStoreId = ref("");
const couponSourceStoreId = ref("");
const note = ref("");
let noteTimer = 0;
let progressHideTimer = 0;
const progress = reactive({
  visible: false,
  title: "",
  status: "",
  current: 0,
  total: 0,
  message: "",
  logs: [],
});
const risk = reactive({
  open: false,
  title: "",
  description: "",
  confirmLabel: "确认",
  action: null,
  running: false,
});

const selectedStores = computed(() => stores.value.filter((store) => (
  selectedStoreIds.value.includes(store.id)
)));
const selectedStoreCount = computed(() => selectedStores.value.length);
const allStoresSelected = computed(() => (
  stores.value.length > 0
  && selectedStores.value.length === stores.value.length
));

function showNote(message) {
  note.value = String(message || "");
  window.clearTimeout(noteTimer);
  if (!note.value) return;
  const current = note.value;
  noteTimer = window.setTimeout(() => {
    if (note.value === current) note.value = "";
  }, 8000);
}

function errorText(cause) {
  return cause instanceof Error ? cause.message : String(cause);
}

function formatTime(value) {
  const stamp = Number(value || 0);
  if (!stamp) return "—";
  const normalized = stamp > 100_000_000_000 ? stamp : stamp * 1000;
  return new Date(normalized).toLocaleString("zh-CN", { hour12: false });
}

function formatDateInput(value) {
  const date = value ? new Date(value) : new Date();
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 16);
}

function selectAllStores() {
  selectedStoreIds.value = stores.value.map((store) => store.id);
}

function clearStores() {
  selectedStoreIds.value = [];
}

function startProgress(title, total = 0) {
  if (progressHideTimer) {
    window.clearTimeout(progressHideTimer);
    progressHideTimer = 0;
  }
  Object.assign(progress, {
    visible: true,
    title,
    status: "running",
    current: 0,
    total,
    message: title,
    logs: [],
  });
}

function logProgress(message, type = "info") {
  progress.logs.unshift({
    time: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
    type,
    message,
  });
  progress.logs = progress.logs.slice(0, 12);
}

function tickProgress(message, current = progress.current, total = progress.total) {
  progress.message = message;
  progress.current = current;
  progress.total = total;
  logProgress(message);
}

function finishProgress(message, status = "completed") {
  progress.status = status;
  progress.message = message;
  logProgress(message, status === "completed" ? "info" : "error");
  if (status === "completed") {
    if (progressHideTimer) window.clearTimeout(progressHideTimer);
    progressHideTimer = window.setTimeout(() => {
      progress.visible = false;
      progressHideTimer = 0;
    }, 4000);
  }
}

function dismissProgress() {
  if (progressHideTimer) {
    window.clearTimeout(progressHideTimer);
    progressHideTimer = 0;
  }
  progress.visible = false;
}

function confirmAction(title, description, confirmLabel, action) {
  Object.assign(risk, {
    open: true,
    title,
    description,
    confirmLabel,
    action,
    running: false,
  });
}

async function confirmRisk() {
  if (risk.running || !risk.action) return;
  risk.running = true;
  try {
    await risk.action();
    risk.open = false;
  } catch (cause) {
    showNote(errorText(cause));
  } finally {
    risk.running = false;
  }
}

async function loadStores() {
  const result = await call("stores.list", {});
  stores.value = result.stores || [];
  if (!selectedStoreIds.value.length) selectAllStores();
  else {
    const valid = new Set(stores.value.map((store) => store.id));
    selectedStoreIds.value = selectedStoreIds.value.filter((id) => valid.has(id));
  }
  const logged = stores.value.filter((store) => store.web_status === "已登录");
  if (!sourceStoreId.value) {
    sourceStoreId.value = (logged[0] || stores.value[0])?.id || "";
  }
  if (!couponSourceStoreId.value) {
    couponSourceStoreId.value = (logged[0] || stores.value[0])?.id || "";
  }
}

async function syncProductCatalogs(targetStores, label) {
  startProgress(label, targetStores.length);
  const errors = [];
  for (let index = 0; index < targetStores.length; index += 1) {
    const store = targetStores[index];
    tickProgress(`正在同步 ${store.name} 商品目录…`, index, targetStores.length);
    try {
      const started = await call("products.refresh.start", {
        store_id: store.id,
        tab_keys: ["sales", "offline", "recycle"],
      });
      let job = null;
      while (true) {
        job = await call("products.refresh.status", { job_id: started.job_id });
        tickProgress(
          `${store.name}：${job.message || "同步中…"}`,
          index,
          targetStores.length,
        );
        if (["completed", "partial", "failed"].includes(job.status)) break;
        await new Promise((resolve) => setTimeout(resolve, 180));
      }
      if (job.status === "failed") throw new Error(job.message || "商品同步失败");
      errors.push(...(job.errors || []));
    } catch (cause) {
      errors.push(`${store.name}：${errorText(cause)}`);
    }
    progress.current = index + 1;
  }
  finishProgress(
    errors.length
      ? `商品目录同步完成，失败或警告 ${errors.length} 项`
      : "商品目录同步完成",
    errors.length ? "partial" : "completed",
  );
  return errors;
}

// Platform activities
const platformFilter = ref("当前可报名");
const platformSearch = ref("");
const platformCatalog = ref([]);
const selectedPlatformId = ref("");
const platformDiscount = ref(9);
const platformMinimumYuan = ref(1);
const platformPreviewRows = ref([]);
const selectedPlatformKeys = ref([]);
const platformResults = ref([]);
const prefillActivityId = ref(1323);
const prefillProducts = ref([]);
const prefillProductId = ref("");
const prefillDrafts = ref([]);
const prefillBusy = ref(false);
const platformCatalogMeta = reactive({
  source_name: "",
  fetched_at: 0,
});

const filteredPlatformActivities = computed(() => {
  const keyword = platformSearch.value.trim().toLowerCase();
  return platformCatalog.value.filter((row) => {
    if (keyword && !`${row.name} ${row.activity_id}`.toLowerCase().includes(keyword)) {
      return false;
    }
    const state = Number(row.activity_state || 0);
    const applyState = Number(row.apply_state || 0);
    if (platformFilter.value === "当前可报名") {
      return state === 1 && applyState === 1;
    }
    if (platformFilter.value === "限时限量购") {
      return /限时|限量/.test(String(row.name || ""));
    }
    if (platformFilter.value === "已结束") return state === 2 || applyState === 3;
    return true;
  });
});
const selectedPlatform = computed(() => (
  platformCatalog.value.find((row) => (
    String(row.activity_id) === String(selectedPlatformId.value)
  )) || null
));
const selectedPlatformRows = computed(() => platformPreviewRows.value.filter((row) => (
  selectedPlatformKeys.value.includes(platformRowKey(row))
)));

function platformRowKey(row) {
  return `${row.store_id}:${row.product_id}:${row.sku_id}`;
}

async function loadPlatformCatalog() {
  if (!sourceStoreId.value) {
    showNote("请选择活动目录来源店铺");
    return;
  }
  try {
    progress.visible = true;
    startProgress("正在读取微信官方平台活动目录");
    const result = await call("activity.catalog", { store_id: sourceStoreId.value });
    platformCatalog.value = result.activities || [];
    platformCatalogMeta.source_name = result.source_store_name || "";
    platformCatalogMeta.fetched_at = Number(result.fetched_at || 0);
    selectedPlatformId.value = "";
    platformPreviewRows.value = [];
    selectedPlatformKeys.value = [];
    finishProgress(`官方平台活动读取完成：${platformCatalog.value.length} 个`);
  } catch (cause) {
    finishProgress(errorText(cause), "failed");
  }
}

function selectPlatform(row) {
  selectedPlatformId.value = String(row.activity_id);
  platformPreviewRows.value = [];
  selectedPlatformKeys.value = [];
}

async function previewPlatform() {
  if (!selectedPlatform.value) {
    showNote("请先选择平台活动");
    return;
  }
  if (!selectedStores.value.length) {
    showNote("请至少选择一家目标店铺");
    return;
  }
  const discount = Number(platformDiscount.value);
  const minimum = Number(platformMinimumYuan.value);
  if (!(discount > 0 && discount <= 10)) {
    showNote("统一折扣必须在 0–10 折之间");
    return;
  }
  if (!(minimum >= 1)) {
    showNote("最低优惠不能低于 1 元");
    return;
  }
  startProgress("正在逐店执行官方活动商品预检", selectedStores.value.length);
  const rows = [];
  const errors = [];
  for (let index = 0; index < selectedStores.value.length; index += 1) {
    const store = selectedStores.value[index];
    tickProgress(`正在预检 ${store.name}`, index, selectedStores.value.length);
    try {
      const result = await call("activity.preview", {
        store_id: store.id,
        activity_id: Number(selectedPlatform.value.activity_id),
        discount_basis_points: Math.round(discount * 1000),
        min_discount_cents: Math.round(minimum * 100),
      });
      rows.push(...(result.rows || []).map((row) => ({
        ...row,
        store_id: store.id,
        store_name: store.name,
      })));
      if (result.reason && !result.rows?.length) {
        errors.push(`${store.name}：${result.reason}`);
      }
    } catch (cause) {
      errors.push(`${store.name}：${errorText(cause)}`);
    }
    progress.current = index + 1;
  }
  platformPreviewRows.value = rows;
  selectedPlatformKeys.value = rows
    .filter((row) => row.status === "可报名")
    .map(platformRowKey);
  finishProgress(
    `活动预检完成：${rows.filter((row) => row.status === "可报名").length} 个可报名 SKU`
      + (errors.length ? `，失败 ${errors.length} 家` : ""),
    errors.length ? "partial" : "completed",
  );
}

function togglePlatformRow(row) {
  const key = platformRowKey(row);
  selectedPlatformKeys.value = selectedPlatformKeys.value.includes(key)
    ? selectedPlatformKeys.value.filter((value) => value !== key)
    : [...selectedPlatformKeys.value, key];
}

function selectAllPlatformRows() {
  selectedPlatformKeys.value = platformPreviewRows.value
    .filter((row) => row.status === "可报名")
    .map(platformRowKey);
}

function clearPlatformRows() {
  selectedPlatformKeys.value = [];
}

function exportPlatformPreview() {
  if (!platformPreviewRows.value.length) {
    showNote("当前没有可导出的活动预检结果");
    return;
  }
  const rows = [
    ["店铺", "商品ID", "商品名称", "SKU ID", "原价（元）", "活动价（元）", "库存", "预检结果"],
    ...platformPreviewRows.value.map((row) => [
      row.store_name,
      row.product_id,
      row.title,
      row.sku_id,
      (Number(row.current_price || 0) / 100).toFixed(2),
      (Number(row.activity_price || 0) / 100).toFixed(2),
      row.stock,
      row.status,
    ]),
  ];
  const csv = rows.map((line) => line.map((value) => (
    `"${String(value ?? "").replaceAll('"', '""')}"`
  )).join(",")).join("\r\n");
  const url = URL.createObjectURL(new Blob([`\ufeff${csv}`], { type: "text/csv;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `平台活动预检_${selectedPlatform.value?.activity_id || "unknown"}.csv`;
  anchor.click();
  URL.revokeObjectURL(url);
  showNote(`已导出 ${platformPreviewRows.value.length} 条平台活动预检结果`);
}

async function executePlatform(retryRows = null) {
  const rows = retryRows || selectedPlatformRows.value;
  if (!rows.length) {
    showNote("请至少选择一个可报名 SKU");
    return;
  }
  const activityId = Number(selectedPlatform.value?.activity_id);
  if (!activityId) {
    showNote("请先选择平台活动");
    return;
  }
  const byStore = groupRowsByStore(rows);
  const storesToRun = [...byStore.keys()];
  startProgress("正在逐店申报并提交平台活动 SKU", storesToRun.length);
  const results = [];
  for (let index = 0; index < storesToRun.length; index += 1) {
    const storeId = storesToRun[index];
    const store = stores.value.find((item) => item.id === storeId);
    try {
      tickProgress(`正在申报 ${store?.name || storeId}`, index, storesToRun.length);
      await call("activity.apply", {
        store_id: storeId,
        activity_id: activityId,
      });
      tickProgress(`正在提交 ${store?.name || storeId} 的 SKU`, index, storesToRun.length);
      const result = await call("activity.submit-items", {
        store_id: storeId,
        activity_id: activityId,
        items: byStore.get(storeId),
      });
      results.push({
        store_id: storeId,
        store_name: store?.name || String(storeId),
        ok: true,
        count: result.count || byStore.get(storeId).length,
        rows: byStore.get(storeId),
      });
    } catch (cause) {
      results.push({
        store_id: storeId,
        store_name: store?.name || String(storeId),
        ok: false,
        error: errorText(cause),
        rows: byStore.get(storeId),
      });
    }
    progress.current = index + 1;
  }
  platformResults.value = results;
  const failed = results.filter((item) => !item.ok);
  finishProgress(
    `平台活动执行完成：成功 ${results.length - failed.length}/${results.length} 家`
      + (failed.length ? `，可重试失败 ${failed.length} 家` : ""),
    failed.length ? "partial" : "completed",
  );
  if (!failed.length) await loadPlatformCatalog();
}

function requestExecutePlatform() {
  const rows = selectedPlatformRows.value;
  if (!rows.length) {
    showNote("请至少选择一个可报名 SKU");
    return;
  }
  confirmAction(
    "确认申报平台活动",
    `将向 ${new Set(rows.map((row) => row.store_id)).size} 家店铺真实申报活动 ${selectedPlatform.value?.activity_id}，并提交 ${rows.length} 个 SKU。`,
    "确认申报并提交",
    executePlatform,
  );
}

async function retryPlatformFailures() {
  const rows = platformResults.value
    .filter((item) => !item.ok)
    .flatMap((item) => item.rows || []);
  if (!rows.length) return;
  await executePlatform(rows);
}

async function loadPlatformPrefill() {
  const storeId = Number(sourceStoreId.value);
  if (!storeId) return showNote("请选择活动目录来源店铺");
  prefillBusy.value = true;
  try {
    const [products, drafts] = await Promise.all([
      call("products.list", { store_id: storeId }),
      call("activity.double11.drafts.list", { store_id: storeId }),
    ]);
    prefillProducts.value = products.products || [];
    if (!prefillProductId.value && prefillProducts.value[0]) {
      prefillProductId.value = prefillProducts.value[0].product_id;
    }
    prefillDrafts.value = (drafts.drafts || []).map((row) => ({
      ...row,
      activity_price_yuan: (Number(row.activity_price || 0) / 100).toFixed(2),
    }));
    await call("activity.double11.preview", {
      store_id: storeId,
      activity_id: Number(prefillActivityId.value),
    });
  } catch (cause) {
    showNote(errorText(cause));
  } finally {
    prefillBusy.value = false;
  }
}

async function savePrefillDrafts() {
  const drafts = prefillDrafts.value.map((row) => ({
    product_id: String(row.product_id),
    title: String(row.title || ""),
    activity_price: Math.round(Number(row.activity_price_yuan || 0) * 100),
    activity_stock: Number(row.activity_stock || 0),
  }));
  for (const row of drafts) {
    if (row.activity_price <= 0 || row.activity_stock < 0) {
      throw new Error(`商品 ${row.product_id} 的活动价或库存无效`);
    }
  }
  const result = await call("activity.double11.drafts.save", {
    store_id: Number(sourceStoreId.value),
    drafts,
  });
  prefillDrafts.value = (result.drafts || []).map((row) => ({
    ...row,
    activity_price_yuan: (Number(row.activity_price || 0) / 100).toFixed(2),
  }));
  showNote(`活动预填草稿已保存：${result.count || 0} 个商品`);
}

async function addPrefillDraft() {
  const product = prefillProducts.value.find((row) => (
    String(row.product_id) === String(prefillProductId.value)
  ));
  if (!product) return showNote("请选择商品");
  if (prefillDrafts.value.some((row) => String(row.product_id) === String(product.product_id))) {
    return showNote("该商品已在预填草稿中");
  }
  prefillDrafts.value.push({
    product_id: product.product_id,
    title: product.title || product.product_id,
    activity_price_yuan: (Number(product.min_price || 0) / 100).toFixed(2),
    activity_stock: Number(product.stock_total || 0),
  });
  await savePrefillDrafts();
}

async function removePrefillDraft(productId) {
  prefillDrafts.value = prefillDrafts.value.filter((row) => (
    String(row.product_id) !== String(productId)
  ));
  await savePrefillDrafts();
}

function exportPrefillDrafts() {
  if (!prefillDrafts.value.length) return showNote("当前没有活动预填草稿");
  const rows = [
    ["商品ID", "商品标题", "活动价（元）", "活动库存"],
    ...prefillDrafts.value.map((row) => [
      row.product_id,
      row.title,
      row.activity_price_yuan,
      row.activity_stock,
    ]),
  ];
  const csv = rows.map((line) => line.map((value) => (
    `"${String(value ?? "").replaceAll('"', '""')}"`
  )).join(",")).join("\r\n");
  const url = URL.createObjectURL(new Blob([`\ufeff${csv}`], { type: "text/csv;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `活动预填报_${sourceStoreId.value}.csv`;
  anchor.click();
  URL.revokeObjectURL(url);
}

async function submitPrefillDrafts() {
  await savePrefillDrafts();
  const result = await call("activity.double11.submit", {
    store_id: Number(sourceStoreId.value),
    activity_id: Number(prefillActivityId.value),
    drafts: prefillDrafts.value.map((row) => ({
      product_id: row.product_id,
      title: row.title,
      activity_price: Math.round(Number(row.activity_price_yuan) * 100),
      activity_stock: Number(row.activity_stock),
    })),
  });
  showNote(`活动预填草稿已提交：${result.count || 0} 个 SKU`);
}

async function removeAllPlatformItems() {
  const result = await call("activity.remove-all", {
    store_id: Number(sourceStoreId.value),
    activity_id: Number(prefillActivityId.value),
  });
  showNote(`已移除 ${result.count || 0} 个活动商品`);
}

function groupRowsByStore(rows) {
  const result = new Map();
  for (const row of rows) {
    const storeId = Number(row.store_id);
    if (!result.has(storeId)) result.set(storeId, []);
    result.get(storeId).push({
      productId: String(row.product_id || row.productId || ""),
      skuId: String(row.sku_id || row.skuId || ""),
      activityPrice: Number(row.activity_price || row.activityPrice || 0),
      activityStockNum: 0,
      sellingPointWording: "",
      stockMode: 1,
      applyType: 0,
    });
  }
  return result;
}

// Limited activities
const limitedProducts = ref([]);
const limitedSearch = ref("");
const selectedLimitedKeys = ref([]);
const limitedDiscount = ref(8.5);
const limitedMinimumYuan = ref(1);
const limitedDurationHours = ref(168);
const limitedStartTime = ref(formatDateInput(Date.now() + 5 * 60 * 1000));
const limitedEndTime = ref(formatDateInput(Date.now() + (168 + 5 / 60) * 3600 * 1000));
const limitedStockCap = ref(0);
const limitedPurchaseLimit = ref(0);
const limitedStartDelayMinutes = ref(5);
const limitedResults = ref([]);
const smartRulesBusy = ref(false);

const filteredLimitedProducts = computed(() => {
  const keyword = limitedSearch.value.trim().toLowerCase();
  if (!keyword) return limitedProducts.value;
  return limitedProducts.value.filter((row) => (
    `${row.title} ${row.product_id} ${row.store_name}`.toLowerCase().includes(keyword)
  ));
});
const selectedLimitedRows = computed(() => limitedProducts.value.filter((row) => (
  selectedLimitedKeys.value.includes(limitedKey(row))
)));

function limitedKey(row) {
  return `${row.store_id}:${row.product_id}`;
}

function limitedPreviewPrice(row) {
  const current = Number(row.min_price || 0);
  const discount = Number(limitedDiscount.value || 0);
  const minimum = Math.round(Number(limitedMinimumYuan.value || 0) * 100);
  if (!current || !(discount >= 1 && discount <= 8.5) || minimum < 100) return null;
  const discounted = Math.round(current * discount / 10);
  return Math.max(1, Math.min(discounted, current - minimum));
}

async function loadLimitedProducts({ syncFirst = false } = {}) {
  if (!selectedStores.value.length) {
    showNote("请至少选择一家目标店铺");
    return;
  }
  try {
    if (syncFirst) await syncProductCatalogs(selectedStores.value, "同步限时活动商品");
    startProgress("正在读取所选店铺在售商品", selectedStores.value.length);
    const rows = [];
    for (let index = 0; index < selectedStores.value.length; index += 1) {
      const store = selectedStores.value[index];
      tickProgress(`正在读取 ${store.name} 在售商品`, index, selectedStores.value.length);
      const result = await call("products.list", { store_id: store.id });
      rows.push(...(result.products || [])
        .filter((product) => Number(product.status) === 5)
        .map((product) => ({ ...product, store_id: store.id, store_name: store.name })));
      progress.current = index + 1;
    }
    limitedProducts.value = rows;
    selectedLimitedKeys.value = selectedLimitedKeys.value.filter((key) => (
      rows.some((row) => limitedKey(row) === key)
    ));
    finishProgress(`在售商品读取完成：${rows.length} 个`);
  } catch (cause) {
    finishProgress(errorText(cause), "failed");
  }
}

function toggleLimited(row) {
  const key = limitedKey(row);
  selectedLimitedKeys.value = selectedLimitedKeys.value.includes(key)
    ? selectedLimitedKeys.value.filter((value) => value !== key)
    : [...selectedLimitedKeys.value, key];
}

function selectAllLimited() {
  selectedLimitedKeys.value = filteredLimitedProducts.value.map(limitedKey);
}

function clearLimited() {
  selectedLimitedKeys.value = [];
}

function validateLimitedSettings() {
  const discount = Number(limitedDiscount.value);
  const minimum = Number(limitedMinimumYuan.value);
  const start = Math.floor(new Date(limitedStartTime.value).getTime() / 1000);
  const end = Math.floor(new Date(limitedEndTime.value).getTime() / 1000);
  if (!(discount >= 1 && discount <= 8.5)) throw new Error("折扣必须在 1–8.5 折之间");
  if (!(minimum >= 1)) throw new Error("最低优惠不能低于 1 元");
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
    throw new Error("活动结束时间必须晚于开始时间");
  }
  if (end - start > 7 * 86400) throw new Error("活动持续时间不能超过 7 天");
  if (Number(limitedStockCap.value || 0) !== 0) {
    throw new Error("官方限时抢购接口禁止设置抢购库存，请保持为 0");
  }
  if (Number(limitedPurchaseLimit.value || 0) < 0) throw new Error("每人限购不能为负数");
  return { discount, minimum, start, end };
}

async function createLimitedActivities() {
  const rows = selectedLimitedRows.value;
  if (!rows.length) {
    showNote("请选择要创建限时活动的商品");
    return;
  }
  const settings = validateLimitedSettings();
  const byStore = groupLimitedByStore(rows);
  startProgress("正在逐店创建限时限量购", byStore.size);
  const results = [];
  let index = 0;
  for (const [storeId, productIds] of byStore.entries()) {
    const store = stores.value.find((item) => item.id === storeId);
    tickProgress(`正在创建 ${store?.name || storeId}`, index, byStore.size);
    try {
      const result = await call("activity.snapup.create", {
        store_id: storeId,
        product_ids: productIds,
        discount: settings.discount / 10,
        start_time: settings.start,
        end_time: settings.end,
        min_discount_cents: Math.round(settings.minimum * 100),
        stock_limit_per_sku: Number(limitedStockCap.value || 0),
        limit_purchase_num: Number(limitedPurchaseLimit.value || 0),
        create_all: true,
      });
      results.push({ store_id: storeId, store_name: store?.name || String(storeId), ok: true, result });
    } catch (cause) {
      results.push({ store_id: storeId, store_name: store?.name || String(storeId), ok: false, error: errorText(cause) });
    }
    index += 1;
    progress.current = index;
  }
  limitedResults.value = results;
  const failed = results.filter((row) => !row.ok);
  finishProgress(
    `限时活动创建完成：成功 ${results.length - failed.length}/${results.length} 家`,
    failed.length ? "partial" : "completed",
  );
  if (!failed.length) await loadLimitedProducts();
}

function requestCreateLimited() {
  try {
    validateLimitedSettings();
    const rows = selectedLimitedRows.value;
    if (!rows.length) throw new Error("请选择要创建限时活动的商品");
    confirmAction(
      "确认创建限时限量购",
      `将为 ${new Set(rows.map((row) => row.store_id)).size} 家店铺、${rows.length} 个商品真实创建限时限量购活动。`,
      "确认创建",
      createLimitedActivities,
    );
  } catch (cause) {
    showNote(errorText(cause));
  }
}

function groupLimitedByStore(rows) {
  const result = new Map();
  for (const row of rows) {
    const storeId = Number(row.store_id);
    if (!result.has(storeId)) result.set(storeId, []);
    result.get(storeId).push(String(row.product_id));
  }
  return result;
}

// Coupons
const sourceCoupons = ref([]);
const manageCoupons = ref([]);
const selectedCouponKeys = ref([]);
const couponTypeFilter = ref("");
const couponSearch = ref("");
const couponFormOpen = ref(false);
const couponTemplate = ref(null);
const couponForm = reactive({
  name: "店铺直减券",
  type: 104,
  promote_type: 1,
  discount_fee_yuan: "5.00",
  discount_num: 0,
  total_num: 100,
  limit_num_one_person: 1,
  start_time: "",
  end_time: "",
  valid_type: 2,
  valid_day_num: 7,
});
const couponMatches = ref([]);
const couponOperationResults = ref([]);

const filteredCoupons = computed(() => {
  const keyword = couponSearch.value.trim().toLowerCase();
  return manageCoupons.value.filter((row) => {
    if (couponTypeFilter.value && String(row.coupon_type) !== couponTypeFilter.value) {
      return false;
    }
    return !keyword || `${row.name} ${row.coupon_id} ${row.store_name}`.toLowerCase().includes(keyword);
  });
});
const selectedCouponRows = computed(() => manageCoupons.value.filter((row) => (
  selectedCouponKeys.value.includes(couponKey(row))
)));

function couponKey(row) {
  return `${row.store_id}:${row.coupon_id}`;
}

async function loadCouponsFor(storesToLoad, targetRef = manageCoupons) {
  const rows = [];
  const errors = [];
  for (const store of storesToLoad) {
    try {
      const result = await call("activity.coupons.list", { store_id: store.id });
      rows.push(...(result.coupons || []));
    } catch (cause) {
      errors.push(`${store.name}：${errorText(cause)}`);
    }
  }
  targetRef.value = rows;
  return errors;
}

async function loadManagedCoupons() {
  const targets = selectedStores.value;
  if (!targets.length) {
    showNote("请至少选择一家店铺");
    return;
  }
  startProgress("正在读取所选店铺优惠券", targets.length);
  const errors = [];
  const rows = [];
  for (let index = 0; index < targets.length; index += 1) {
    const store = targets[index];
    tickProgress(`正在读取 ${store.name} 优惠券`, index, targets.length);
    try {
      const result = await call("activity.coupons.list", { store_id: store.id });
      rows.push(...(result.coupons || []));
    } catch (cause) {
      errors.push(`${store.name}：${errorText(cause)}`);
    }
    progress.current = index + 1;
  }
  manageCoupons.value = rows;
  selectedCouponKeys.value = selectedCouponKeys.value.filter((key) => (
    rows.some((row) => couponKey(row) === key)
  ));
  finishProgress(`优惠券读取完成：${rows.length} 张` + (errors.length ? `，失败 ${errors.length} 家` : ""), errors.length ? "partial" : "completed");
}

async function syncManagedCoupons() {
  const targets = selectedStores.value;
  if (!targets.length) return;
  startProgress("正在同步所选店铺优惠券", targets.length);
  const errors = [];
  for (let index = 0; index < targets.length; index += 1) {
    const store = targets[index];
    tickProgress(`正在同步 ${store.name} 优惠券`, index, targets.length);
    try {
      await call("activity.coupons.sync", { store_id: store.id });
    } catch (cause) {
      errors.push(`${store.name}：${errorText(cause)}`);
    }
    progress.current = index + 1;
  }
  await loadManagedCoupons();
  finishProgress(`优惠券同步完成` + (errors.length ? `，失败 ${errors.length} 家` : ""), errors.length ? "partial" : "completed");
}

function toggleCoupon(row) {
  const key = couponKey(row);
  selectedCouponKeys.value = selectedCouponKeys.value.includes(key)
    ? selectedCouponKeys.value.filter((value) => value !== key)
    : [...selectedCouponKeys.value, key];
}

function selectAllCoupons() {
  selectedCouponKeys.value = filteredCoupons.value.map(couponKey);
}

function clearCoupons() {
  selectedCouponKeys.value = [];
}

async function syncSourceCoupons() {
  const store = stores.value.find((row) => row.id === sourceStoreId.value);
  if (!store) {
    showNote("请选择来源店铺");
    return;
  }
  startProgress(`正在预读 ${store.name} 优惠券模板`, 1);
  try {
    await call("activity.coupons.sync", { store_id: store.id });
    const result = await call("activity.coupons.list", { store_id: store.id });
    sourceCoupons.value = result.coupons || [];
    finishProgress(`来源店铺优惠券预读完成：${sourceCoupons.value.length} 张`);
  } catch (cause) {
    finishProgress(errorText(cause), "failed");
  }
}

function useCouponTemplate(row) {
  couponTemplate.value = row;
  Object.assign(couponForm, {
    name: `${row.name} 续期`,
    type: Number(row.coupon_type || 104),
    promote_type: Number(row.promote_type || 1),
    discount_fee_yuan: (Number(row.discount_fee || 0) / 100).toFixed(2),
    discount_num: Number(row.discount_num || 0),
    total_num: Number(row.total_num || 100),
    limit_num_one_person: 1,
    start_time: formatDateInput(Date.now() + 3600 * 1000),
    end_time: formatDateInput(Date.now() + 8 * 86400 * 1000),
    valid_type: 2,
    valid_day_num: 7,
  });
  couponFormOpen.value = true;
}

function openBlankCouponForm() {
  couponTemplate.value = null;
  Object.assign(couponForm, {
    name: "店铺直减券",
    type: 104,
    promote_type: 1,
    discount_fee_yuan: "5.00",
    discount_num: 0,
    total_num: 100,
    limit_num_one_person: 1,
    start_time: formatDateInput(Date.now() + 3600 * 1000),
    end_time: formatDateInput(Date.now() + 8 * 86400 * 1000),
    valid_type: 2,
    valid_day_num: 7,
  });
  couponFormOpen.value = true;
}

function couponPayload() {
  const discountFee = Math.round(Number(couponForm.discount_fee_yuan || 0) * 100);
  const start = Math.floor(new Date(couponForm.start_time).getTime() / 1000);
  const end = Math.floor(new Date(couponForm.end_time).getTime() / 1000);
  if (!couponForm.name.trim()) throw new Error("优惠券名称不能为空");
  if (discountFee <= 0) throw new Error("优惠金额必须大于 0");
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
    throw new Error("优惠券结束时间必须晚于开始时间");
  }
  return {
    type: Number(couponForm.type || 104),
    name: couponForm.name.trim(),
    promote_info: { promote_type: Number(couponForm.promote_type || 1) },
    discount_info: {
      discount_fee: discountFee,
      discount_num: Number(couponForm.discount_num || 0),
    },
    receive_info: {
      total_num: Number(couponForm.total_num || 0),
      limit_num_one_person: Number(couponForm.limit_num_one_person || 1),
      start_time: start,
      end_time: end,
    },
    valid_info: {
      valid_type: Number(couponForm.valid_type || 2),
      valid_day_num: Number(couponForm.valid_day_num || 7),
    },
  };
}

async function createCouponsForSelected() {
  const coupon = couponPayload();
  const targets = selectedStores.value;
  startProgress("正在逐店创建优惠券", targets.length);
  const results = [];
  for (let index = 0; index < targets.length; index += 1) {
    const store = targets[index];
    tickProgress(`正在创建 ${store.name} 优惠券`, index, targets.length);
    try {
      const result = await call("activity.coupons.create", {
        store_id: store.id,
        coupon,
      });
      results.push({ store_id: store.id, store_name: store.name, ok: true, count: result.coupons?.length || 0 });
    } catch (cause) {
      results.push({ store_id: store.id, store_name: store.name, ok: false, error: errorText(cause) });
    }
    progress.current = index + 1;
  }
  couponOperationResults.value = results;
  finishProgress(`优惠券创建完成：成功 ${results.filter((row) => row.ok).length}/${results.length} 家`, results.some((row) => !row.ok) ? "partial" : "completed");
  couponFormOpen.value = false;
  await loadManagedCoupons();
}

function requestCreateCoupons() {
  try {
    const coupon = couponPayload();
    if (!selectedStores.value.length) throw new Error("请至少选择一家目标店铺");
    confirmAction(
      "确认创建优惠券",
      `将向 ${selectedStores.value.length} 家店铺真实创建“${coupon.name}”，发放 ${coupon.receive_info.total_num} 张。`,
      "确认创建",
      createCouponsForSelected,
    );
  } catch (cause) {
    showNote(errorText(cause));
  }
}

async function renewCoupons(rows = selectedCouponRows.value) {
  if (!rows.length) {
    showNote("请选择要续期的优惠券");
    return;
  }
  startProgress("正在逐张复制并续期优惠券", rows.length);
  const results = [];
  for (let index = 0; index < rows.length; index += 1) {
    const row = rows[index];
    tickProgress(`正在续期 ${row.store_name} · ${row.name}`, index, rows.length);
    try {
      const oldEnd = Number(row.end_time || 0);
      const result = await call("activity.coupons.renew", {
        store_id: row.store_id,
        coupon_id: row.coupon_id,
        coupon_type: row.coupon_type,
        promote_type: row.promote_type,
        start_time: Math.max(Math.floor(Date.now() / 1000) + 60, oldEnd + 1),
        days: 7,
      });
      results.push({ ...row, ok: true, result });
    } catch (cause) {
      results.push({ ...row, ok: false, error: errorText(cause) });
    }
    progress.current = index + 1;
  }
  couponOperationResults.value = results;
  finishProgress(`优惠券续期完成：成功 ${results.filter((row) => row.ok).length}/${results.length} 张`, results.some((row) => !row.ok) ? "partial" : "completed");
  await loadManagedCoupons();
}

function requestRenewCoupons() {
  const rows = selectedCouponRows.value;
  if (!rows.length) return showNote("请选择要续期的优惠券");
  confirmAction(
    "确认复制并续期优惠券",
    `将通过微信官方后台复制 ${rows.length} 张优惠券并创建新的 7 天领取期；原券不会修改。`,
    "确认续期",
    () => renewCoupons(rows),
  );
}

async function changeCouponStatus(status, rows = selectedCouponRows.value) {
  if (!rows.length) return;
  startProgress(status === 4 ? "正在停用优惠券" : "正在删除优惠券", rows.length);
  const results = [];
  for (let index = 0; index < rows.length; index += 1) {
    const row = rows[index];
    tickProgress(`正在处理 ${row.store_name} · ${row.name}`, index, rows.length);
    try {
      await call("activity.coupons.status", {
        store_id: row.store_id,
        coupon_id: row.coupon_id,
        status,
      });
      results.push({ ...row, ok: true });
    } catch (cause) {
      results.push({ ...row, ok: false, error: errorText(cause) });
    }
    progress.current = index + 1;
  }
  couponOperationResults.value = results;
  finishProgress(`优惠券操作完成：成功 ${results.filter((row) => row.ok).length}/${results.length} 张`, results.some((row) => !row.ok) ? "partial" : "completed");
  await loadManagedCoupons();
}

function requestCouponStatus(status) {
  const rows = selectedCouponRows.value;
  if (!rows.length) return showNote("请选择优惠券");
  confirmAction(
    status === 4 ? "确认停用优惠券" : "确认删除优惠券",
    `将${status === 4 ? "停用" : "删除"} ${rows.length} 张优惠券。${status === 5 ? "删除可能不可恢复。" : ""}`,
    status === 4 ? "确认停用" : "确认删除",
    () => changeCouponStatus(status, rows),
  );
}

async function matchCouponTemplates() {
  const template = couponTemplate.value;
  if (!template) return showNote("请先从来源店铺选择一个优惠券模板");
  const targets = selectedStores.value;
  if (!targets.length) return showNote("请选择目标店铺");
  startProgress("正在同步并匹配目标店铺优惠券", targets.length);
  const matches = [];
  for (let index = 0; index < targets.length; index += 1) {
    const store = targets[index];
    tickProgress(`正在匹配 ${store.name}`, index, targets.length);
    try {
      await call("activity.coupons.sync", { store_id: store.id });
      const result = await call("activity.coupons.list", { store_id: store.id });
      matches.push(...(result.coupons || []).filter((row) => (
        Number(row.coupon_type) === Number(template.coupon_type)
        && String(row.name) === String(template.name)
      )));
    } catch (cause) {
      logProgress(`${store.name}：${errorText(cause)}`, "error");
    }
    progress.current = index + 1;
  }
  couponMatches.value = matches;
  await loadManagedCoupons();
  finishProgress(`优惠券模板匹配完成：${matches.length} 张`);
}

// Snapup renewal and automatic management
const snapupRows = ref([]);
const renewal = reactive({
  enabled: false,
  interval_minutes: 10,
  running: false,
  last_scan_at: 0,
  next_scan_at: 0,
  stores: [],
  runs: [],
  auto_create_rules: {},
  auto_create_runs: [],
  auto_create_summary: {},
  cloud_status: {},
  active_job: null,
});
const renewalStoreConfigs = ref({});
const renewalBusy = ref(false);

async function loadRenewalStatus() {
  try {
    const result = await call("activity.renewal.status", {});
    Object.assign(renewal, result);
    const next = { ...renewalStoreConfigs.value };
    for (const row of result.stores || []) {
      next[row.store_id] = {
        enabled: Boolean(row.enabled),
        hours_before: Number(row.hours_before || 6),
        duration_days: Number(row.duration_days || 7),
      };
    }
    renewalStoreConfigs.value = next;
    const rules = result.auto_create_rules || {};
    if (rules.saved_at) {
      limitedDiscount.value = Number(rules.discount || 0.85) * 10;
      limitedMinimumYuan.value = Number(
        rules.min_discount_cents || 100
      ) / 100;
      limitedPurchaseLimit.value = Number(
        rules.limit_purchase_num || 0
      );
      limitedStartDelayMinutes.value = Number(
        rules.start_delay_minutes || 5
      );
      limitedDurationHours.value = Math.max(
        1,
        Number(rules.duration_seconds || 7 * 86400) / 3600,
      );
    }
  } catch (cause) {
    showNote(errorText(cause));
  }
}

async function loadSnapupActivities() {
  if (!selectedStores.value.length) return showNote("请至少选择一家店铺");
  startProgress("正在读取官方限时抢购活动", selectedStores.value.length);
  const rows = [];
  for (let index = 0; index < selectedStores.value.length; index += 1) {
    const store = selectedStores.value[index];
    tickProgress(`正在读取 ${store.name} 限时活动`, index, selectedStores.value.length);
    try {
      const result = await call("activity.snapup.list", { store_id: store.id });
      rows.push(...(result.activities || []).map((row) => ({ ...row, store_id: store.id, store_name: store.name })));
    } catch (cause) {
      logProgress(`${store.name}：${errorText(cause)}`, "error");
    }
    progress.current = index + 1;
  }
  snapupRows.value = rows;
  finishProgress(`官方限时抢购读取完成：${rows.length} 场`);
  await loadRenewalStatus();
}

async function saveRenewalConfig() {
  if (renewalBusy.value) return;
  renewalBusy.value = true;
  try {
    const storesPayload = Object.entries(renewalStoreConfigs.value).map(([storeId, config]) => ({
      store_id: Number(storeId),
      enabled: Boolean(config.enabled),
      hours_before: Number(config.hours_before || 6),
      duration_days: Number(config.duration_days || 7),
    }));
    const result = await call("activity.renewal.save", {
      enabled: Boolean(renewal.enabled),
      interval_minutes: Number(renewal.interval_minutes || 10),
      stores: storesPayload,
    });
    Object.assign(renewal, result);
    showNote("智能限时活动托管配置已保存；后台调度器将按绝对时间恢复执行");
  } catch (cause) {
    showNote(errorText(cause));
  } finally {
    renewalBusy.value = false;
  }
}

function cloudStatusLabel() {
  const status = String(renewal.cloud_status?.status || "local_only");
  return {
    synced: "云端已同步",
    pending: "等待云端同步",
    failed: "云端同步失败，本地已保存",
    sync_failed: "云端读取失败，使用本地配置",
    local_only: "仅本地保存",
  }[status] || status;
}

function autoCreateRunLabel(status) {
  return {
    pending: "待处理",
    running: "执行中",
    success: "成功",
    skipped: "已跳过",
    failed: "失败",
    verification_pending: "待核实",
    manual_stopped: "已人工停止",
  }[status] || status;
}

async function saveSmartAutoRules() {
  if (smartRulesBusy.value) return;
  smartRulesBusy.value = true;
  try {
    const settings = validateLimitedSettings();
    const durationSeconds = settings.end - settings.start;
    const startDelayMinutes = Math.max(
      5,
      Math.min(
        60,
        Math.ceil((settings.start - Date.now() / 1000) / 60),
      ),
    );
    const result = await call("activity.smart-auto.rules.save", {
      rules: {
        start_delay_minutes: startDelayMinutes,
        duration_seconds: durationSeconds,
        discount: settings.discount / 10,
        min_discount_cents: Math.round(settings.minimum * 100),
        stock_limit_per_sku: 0,
        limit_purchase_num: Number(limitedPurchaseLimit.value || 0),
      },
    });
    Object.assign(renewal, result);
    showNote("智能限时活动规则快照已保存");
  } catch (cause) {
    showNote(errorText(cause));
  } finally {
    smartRulesBusy.value = false;
  }
}

async function stopAutoCreateRun(row) {
  if (!row?.id || renewalBusy.value) return;
  renewalBusy.value = true;
  try {
    const result = await call("activity.smart-auto.stop", {
      run_id: row.id,
    });
    Object.assign(renewal, result);
    showNote("该商品今日自动活动已人工停止");
  } catch (cause) {
    showNote(errorText(cause));
  } finally {
    renewalBusy.value = false;
  }
}

async function scanRenewalNow() {
  const storeIds = selectedStores.value
    .filter((store) => renewalStoreConfigs.value[store.id]?.enabled)
    .map((store) => store.id);
  if (!storeIds.length) return showNote("没有已启用自动续期的所选店铺");
  startProgress("正在执行自动续期检查", storeIds.length);
  try {
    const started = await call("activity.renewal.scan.start", { store_ids: storeIds });
    while (true) {
      const job = await call("activity.renewal.scan.status", { job_id: started.job_id });
      tickProgress(job.message || "自动续期检查中…", job.current || 0, job.total || storeIds.length);
      if (["completed", "failed"].includes(job.status)) {
        finishProgress(job.message || "自动续期检查结束", job.status === "failed" ? "failed" : "completed");
        for (const row of job.results || []) {
          if (row.status !== "success" && row.error) logProgress(`${row.store_name}：${row.error}`, "error");
        }
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    await Promise.all([loadSnapupActivities(), loadRenewalStatus(), loadOperations()]);
  } catch (cause) {
    finishProgress(errorText(cause), "failed");
  }
}

function requestManualSnapupRenew(row) {
  confirmAction(
    "确认复制并续期限时活动",
    `将复制 ${row.store_name} 的商家自建活动“${row.title || row.activity_id}”，从原结束时间后创建 7 天新活动；平台活动不会被修改。`,
    "确认续期",
    async () => {
      startProgress("正在创建限时活动续期", 1);
      try {
        const oldEnd = Number(row.end_time || row.endTime || 0);
        const result = await call("activity.snapup.renew", {
          store_id: row.store_id,
          activity_id: row.activity_id,
          start_time: Math.max(Math.floor(Date.now() / 1000) + 60, oldEnd + 1),
          days: 7,
        });
        finishProgress(`续期创建完成：${result.new_activity_id || result.activity_id || "已提交"}`);
        await loadSnapupActivities();
      } catch (cause) {
        finishProgress(errorText(cause), "failed");
        throw cause;
      }
    },
  );
}

function requestDeleteSnapup(row) {
  confirmAction(
    "确认删除限时活动",
    `将删除“${row.title || row.activity_id}”。进行中的活动会先停止，此操作不可恢复。`,
    "确认删除",
    async () => {
      startProgress("正在删除限时活动", 1);
      try {
        await call("activity.snapup.delete", { store_id: row.store_id, activity_id: row.activity_id });
        finishProgress("限时活动已删除");
        await loadSnapupActivities();
      } catch (cause) {
        finishProgress(errorText(cause), "failed");
        throw cause;
      }
    },
  );
}

async function openSnapupOfficial(row) {
  try {
    await call("operations.open-backend", { store_id: row.store_id, target: "snapup" });
    showNote("已打开对应店铺的官方限时活动页面");
  } catch (cause) {
    showNote(errorText(cause));
  }
}

// Execution records
const operationRecords = ref([]);

async function loadOperations() {
  try {
    const result = await call("activity.operations", {
      store_id: "__all__",
      limit: 500,
    });
    operationRecords.value = result.operations || [];
  } catch (cause) {
    showNote(errorText(cause));
  }
}

function renewalRunCount(status) {
  return renewal.runs.filter((row) => row.status === status).length;
}

onMounted(async () => {
  try {
    await loadStores();
    await Promise.all([loadRenewalStatus(), loadOperations()]);
  } catch (cause) {
    showNote(errorText(cause));
  }
});

onBeforeUnmount(() => {
  if (progressHideTimer) {
    window.clearTimeout(progressHideTimer);
    progressHideTimer = 0;
  }
  window.clearTimeout(noteTimer);
});
</script>

<template>
  <section class="activity-center-page">
    <header class="activity-head">
      <div>
        <h1>活动管理</h1>
        <p>平台活动、限时限量购、优惠券与自动续期的真实后台工作流</p>
      </div>
      <strong>已选 {{ selectedStoreCount }} / {{ stores.length }} 家店铺</strong>
    </header>

    <section class="card store-selector">
      <header>
        <div>
          <h2>第 1 步 · 选择执行店铺</h2>
          <p>下方所有申报、创建、同步、续期和停用均严格按勾选店铺逐店执行。</p>
        </div>
        <div>
          <button class="btn" type="button" @click="selectAllStores">{{ allStoresSelected ? "已全选" : "全选" }}</button>
          <button class="btn" type="button" @click="clearStores">清空</button>
        </div>
      </header>
      <div class="store-selector-scroll">
        <table>
          <thead><tr><th>选择</th><th>店铺名称</th><th>网页登录</th><th>API 状态</th><th>商品数</th><th>分组</th></tr></thead>
          <tbody>
            <tr v-for="store in stores" :key="store.id" :class="{ selected: selectedStoreIds.includes(store.id) }" @click="selectedStoreIds.includes(store.id) ? selectedStoreIds = selectedStoreIds.filter((id) => id !== store.id) : selectedStoreIds = [...selectedStoreIds, store.id]">
              <td><input v-model="selectedStoreIds" type="checkbox" :value="store.id" @click.stop /></td>
              <td><strong>{{ store.name }}</strong><small>#{{ store.id }}</small></td>
              <td>{{ store.web_status || "—" }}</td>
              <td>{{ store.status || "—" }}</td>
              <td>{{ store.metrics?.product_total ?? store.product_total ?? "—" }}</td>
              <td>{{ store.group_name || "未分组" }}</td>
            </tr>
            <tr v-if="!stores.length"><td colspan="6" class="empty">暂无店铺</td></tr>
          </tbody>
        </table>
      </div>
    </section>

    <nav class="activity-tabs" aria-label="活动管理一级标签">
      <button v-for="[key, label] in tabs" :key="key" type="button" :class="{ active: activeTab === key }" @click="activeTab = key">
        {{ label }}
      </button>
    </nav>

    <section v-if="activeTab === 'platform'" class="card activity-panel">
      <header class="panel-title"><div><h2>平台活动</h2><p>官方目录读取、商品同步、上下架预检、逐店申报与 SKU 价格/库存提交。</p></div></header>
      <div class="toolbar-row">
        <label>目录来源店铺
          <select v-model="sourceStoreId" class="select">
            <option v-for="store in stores.filter((row) => row.web_status === '已登录')" :key="store.id" :value="store.id">{{ store.name }}</option>
          </select>
        </label>
        <button class="btn primary" type="button" @click="loadPlatformCatalog">读取官方平台活动</button>
        <button class="btn" type="button" :disabled="!selectedStores.length" @click="syncProductCatalogs(selectedStores, '同步所选店铺商品目录')">同步所选商品</button>
        <select v-model="platformFilter" class="select">
          <option>当前可报名</option><option>限时限量购</option><option>全部活动</option><option>已结束</option>
        </select>
        <input v-model="platformSearch" class="input" placeholder="搜索活动名称或 ID" />
      </div>
      <p class="meta-line">来源：{{ platformCatalogMeta.source_name || "—" }} · 更新时间：{{ formatTime(platformCatalogMeta.fetched_at) }}</p>
      <div class="split-grid">
        <div class="table-scroll">
          <table class="data-table">
            <thead><tr><th>选择</th><th>活动名称</th><th>活动 ID</th><th>状态</th><th>报名状态</th><th>报名截止</th></tr></thead>
            <tbody>
              <tr v-for="row in filteredPlatformActivities" :key="row.activity_id" :class="{ selected: String(row.activity_id) === String(selectedPlatformId) }" @click="selectPlatform(row)">
                <td><input type="radio" :checked="String(row.activity_id) === String(selectedPlatformId)" /></td>
                <td>{{ row.name || "未命名活动" }}</td><td class="mono">{{ row.activity_id }}</td>
                <td>{{ { 0: "未开始", 1: "进行中", 2: "已结束" }[row.activity_state] || "未知" }}</td>
                <td>{{ { 0: "不可报名", 1: "可报名", 2: "已报名", 3: "已结束" }[row.apply_state] || "未知" }}</td>
                <td>{{ formatTime(row.apply_end_time) }}</td>
              </tr>
              <tr v-if="!filteredPlatformActivities.length"><td colspan="6" class="empty">暂无符合条件的平台活动</td></tr>
            </tbody>
          </table>
        </div>
        <aside class="settings-box">
          <label>统一折扣（折）<input v-model.number="platformDiscount" class="input" type="number" min="0.1" max="10" step="0.1" /></label>
          <label>最低优惠（元）<input v-model.number="platformMinimumYuan" class="input" type="number" min="1" step="0.01" /></label>
          <button class="btn primary" type="button" :disabled="!selectedPlatform" @click="previewPlatform">上下架商品预检</button>
          <button class="btn" type="button" :disabled="!platformPreviewRows.length" @click="selectAllPlatformRows">全选可报名</button>
          <button class="btn" type="button" @click="clearPlatformRows">清空选择</button>
          <button class="btn" type="button" :disabled="!platformPreviewRows.length" @click="exportPlatformPreview">导出预检</button>
          <button class="btn primary" type="button" :disabled="!selectedPlatformRows.length" @click="requestExecutePlatform">逐店申报并提交</button>
          <button class="btn" type="button" :disabled="!platformResults.some((row) => !row.ok)" @click="retryPlatformFailures">重试失败店铺</button>
        </aside>
      </div>
      <div class="table-scroll result-table">
        <table class="data-table">
          <thead><tr><th>店铺</th><th>商品</th><th>SKU</th><th>原价</th><th>活动价</th><th>库存</th><th>预检</th></tr></thead>
          <tbody>
            <tr v-for="row in platformPreviewRows" :key="platformRowKey(row)" :class="{ selected: selectedPlatformKeys.includes(platformRowKey(row)) }" @click="togglePlatformRow(row)">
              <td>{{ row.store_name }}</td><td>{{ row.title || row.product_id }}</td><td class="mono">{{ row.sku_id }}</td>
              <td>{{ formatMoney(row.current_price) }}</td><td>{{ formatMoney(row.activity_price) }}</td><td>{{ row.stock }}</td><td>{{ row.status }}</td>
            </tr>
            <tr v-if="!platformPreviewRows.length"><td colspan="7" class="empty">选择活动后执行商品预检</td></tr>
          </tbody>
        </table>
      </div>
      <div v-if="platformResults.length" class="result-list">
        <p v-for="row in platformResults" :key="row.store_id" :class="{ error: !row.ok }">{{ row.store_name }}：{{ row.ok ? `成功提交 ${row.count} 个 SKU` : row.error }}</p>
      </div>
      <details class="prefill-panel">
        <summary>活动预填报（兼容旧版草稿与移除全部报名）</summary>
        <div class="toolbar-row">
          <label>活动 ID<input v-model.number="prefillActivityId" class="input compact" type="number" min="1" /></label>
          <select v-model="prefillProductId" class="select">
            <option v-for="product in prefillProducts" :key="product.product_id" :value="product.product_id">{{ product.title || product.product_id }}</option>
          </select>
          <button class="btn" type="button" :disabled="prefillBusy" @click="loadPlatformPrefill">读取商品与草稿</button>
          <button class="btn" type="button" :disabled="prefillBusy" @click="addPrefillDraft">加入预填报</button>
          <button class="btn" type="button" :disabled="prefillBusy" @click="savePrefillDrafts">保存草稿</button>
          <button class="btn" type="button" :disabled="prefillBusy" @click="exportPrefillDrafts">导出草稿</button>
        </div>
        <div class="table-scroll result-table">
          <table class="data-table">
            <thead><tr><th>商品</th><th>商品 ID</th><th>活动价（元）</th><th>活动库存</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="row in prefillDrafts" :key="row.product_id">
                <td>{{ row.title || row.product_id }}</td><td class="mono">{{ row.product_id }}</td>
                <td><input v-model="row.activity_price_yuan" class="input compact" inputmode="decimal" /></td>
                <td><input v-model.number="row.activity_stock" class="input compact" type="number" min="0" /></td>
                <td><button class="text-button danger-text" type="button" @click="removePrefillDraft(row.product_id)">删除</button></td>
              </tr>
              <tr v-if="!prefillDrafts.length"><td colspan="5" class="empty">暂无预填报草稿</td></tr>
            </tbody>
          </table>
        </div>
        <div class="toolbar-row">
          <button class="btn primary" type="button" :disabled="prefillBusy || !prefillDrafts.length" @click="submitPrefillDrafts">提交本地预填草稿</button>
          <button class="btn danger" type="button" :disabled="prefillBusy" @click="removeAllPlatformItems">移除全部报名</button>
        </div>
      </details>
    </section>

    <section v-else-if="activeTab === 'limited'" class="card activity-panel">
      <header class="panel-title"><div><h2>限时限量购活动</h2><p>从官方在售商品中筛选，设置时间、折扣、最低优惠和每人限购后逐店创建。</p></div></header>
      <div class="smart-auto-strip" :class="{ enabled: renewal.enabled }">
        <label class="switch-label">
          <input v-model="renewal.enabled" type="checkbox" :disabled="renewalBusy" @change="saveRenewalConfig" />
          智能限时活动托管：{{ renewal.enabled ? "已开启" : "已关闭" }}
        </label>
        <span>上次扫描 {{ formatTime(renewal.last_scan_at) }}</span>
        <span>下次扫描 {{ formatTime(renewal.next_scan_at) }}</span>
        <span>待处理 {{ renewal.auto_create_summary?.pending || 0 }}</span>
        <span>成功 {{ renewal.auto_create_summary?.success || 0 }}</span>
        <span>失败 {{ renewal.auto_create_summary?.failed || 0 }}</span>
        <span>{{ cloudStatusLabel() }}</span>
      </div>
      <div class="form-strip">
        <label>开始时间<input v-model="limitedStartTime" class="input" type="datetime-local" /></label>
        <label>结束时间<input v-model="limitedEndTime" class="input" type="datetime-local" /></label>
        <label>折扣（折）<input v-model.number="limitedDiscount" class="input compact" type="number" min="1" max="8.5" step="0.1" /></label>
        <label>最低优惠（元）<input v-model.number="limitedMinimumYuan" class="input compact" type="number" min="1" step="0.01" /></label>
        <label>商品库存<input v-model.number="limitedStockCap" class="input compact" type="number" min="0" max="0" disabled title="官方接口禁止本活动设置抢购库存，固定沿用 SKU 当前库存" /></label>
        <label>每人限购<input v-model.number="limitedPurchaseLimit" class="input compact" type="number" min="0" title="0 表示由平台默认规则控制" /></label>
      </div>
      <p class="meta-line">官方限时抢购接口禁止设置抢购库存，固定为 0 并沿用官方 SKU 当前库存；限购填 0 时提交平台默认值。</p>
      <div class="toolbar-row">
        <button class="btn primary" type="button" @click="loadLimitedProducts({ syncFirst: true })">同步并读取官方在售商品</button>
        <button class="btn" type="button" @click="syncProductCatalogs(selectedStores, '同步限时活动商品目录')">仅同步商品</button>
        <input v-model="limitedSearch" class="input" placeholder="搜索商品、商品 ID 或店铺" />
        <button class="btn" type="button" @click="selectAllLimited">全选当前筛选</button>
        <button class="btn" type="button" @click="clearLimited">清空</button>
        <button class="btn" type="button" :disabled="smartRulesBusy" @click="saveSmartAutoRules">{{ smartRulesBusy ? "保存中…" : "保存为智能活动规则" }}</button>
        <button class="btn primary" type="button" :disabled="!selectedLimitedRows.length" @click="requestCreateLimited">预览并批量创建</button>
      </div>
      <div class="table-scroll large-table">
        <table class="data-table">
          <thead><tr><th>选择</th><th>店铺</th><th>商品</th><th>商品 ID</th><th>当前价</th><th>库存</th><th>预览活动价</th></tr></thead>
          <tbody>
            <tr v-for="row in filteredLimitedProducts" :key="limitedKey(row)" :class="{ selected: selectedLimitedKeys.includes(limitedKey(row)) }" @click="toggleLimited(row)">
              <td><input type="checkbox" :checked="selectedLimitedKeys.includes(limitedKey(row))" /></td>
              <td>{{ row.store_name }}</td><td>{{ row.title || "—" }}</td><td class="mono">{{ row.product_id }}</td>
              <td>{{ formatMoney(row.min_price) }}</td><td>{{ row.stock_total ?? "—" }}</td>
              <td>{{ limitedPreviewPrice(row) === null ? "—" : formatMoney(limitedPreviewPrice(row)) }}</td>
            </tr>
            <tr v-if="!filteredLimitedProducts.length"><td colspan="7" class="empty">请先同步并读取官方在售商品</td></tr>
          </tbody>
        </table>
      </div>
      <div v-if="limitedResults.length" class="result-list">
        <p v-for="row in limitedResults" :key="row.store_id" :class="{ error: !row.ok }">{{ row.store_name }}：{{ row.ok ? `创建 ${row.result.activity_count || 0} 场，商品 ${row.result.product_count || 0} 个` : row.error }}</p>
      </div>
    </section>

    <section v-else-if="activeTab === 'coupon-activity'" class="card activity-panel">
      <header class="panel-title"><div><h2>优惠券活动</h2><p>来源店铺预读、模板复制、批量创建到所选目标店铺。</p></div></header>
      <div class="toolbar-row">
        <label>来源店铺<select v-model="couponSourceStoreId" class="select"><option v-for="store in stores" :key="store.id" :value="store.id">{{ store.name }}</option></select></label>
        <button class="btn primary" type="button" @click="syncSourceCoupons">预读来源优惠券</button>
        <button class="btn" type="button" @click="openBlankCouponForm">新建活动模板</button>
      </div>
      <div class="split-grid">
        <div class="table-scroll">
          <table class="data-table">
            <thead><tr><th>来源优惠券</th><th>类型</th><th>状态</th><th>优惠</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="row in sourceCoupons" :key="row.coupon_id">
                <td>{{ row.name }}</td><td>{{ row.coupon_type }}</td><td>{{ row.status }}</td>
                <td>{{ row.discount_fee ? formatMoney(row.discount_fee) : row.discount_num }}</td>
                <td><button class="text-button" type="button" @click="useCouponTemplate(row)">作为模板</button></td>
              </tr>
              <tr v-if="!sourceCoupons.length"><td colspan="5" class="empty">尚未预读来源店铺优惠券</td></tr>
            </tbody>
          </table>
        </div>
        <aside class="settings-box">
          <strong>{{ couponTemplate ? `已选模板：${couponTemplate.name}` : "未选择来源模板" }}</strong>
          <p>目标店铺：{{ selectedStoreCount }} 家</p>
          <button class="btn" type="button" :disabled="!couponTemplate" @click="matchCouponTemplates">同步并匹配目标同款</button>
          <button class="btn primary" type="button" :disabled="!selectedStores.length" @click="openBlankCouponForm">批量创建优惠券</button>
          <p>匹配结果：{{ couponMatches.length }} 张</p>
        </aside>
      </div>
      <div v-if="couponMatches.length" class="table-scroll result-table">
        <table class="data-table"><thead><tr><th>店铺</th><th>优惠券</th><th>状态</th><th>结束时间</th></tr></thead><tbody><tr v-for="row in couponMatches" :key="couponKey(row)"><td>{{ row.store_name }}</td><td>{{ row.name }}</td><td>{{ row.status }}</td><td>{{ formatTime(row.end_time) }}</td></tr></tbody></table>
      </div>
    </section>

    <section v-else-if="activeTab === 'snapup-renewal'" class="card activity-panel">
      <header class="panel-title"><div><h2>限时限量购续期</h2><p>与智能新商品活动共用账户级总开关；后台按绝对时间扫描续期和今日首次观测上新商品。</p></div></header>
      <div class="renewal-config">
        <label class="switch-label"><input v-model="renewal.enabled" type="checkbox" :disabled="renewalBusy" @change="saveRenewalConfig" /> 智能限时活动托管总开关</label>
        <label>扫描周期（分钟）<input v-model.number="renewal.interval_minutes" class="input compact" type="number" min="1" max="1440" /></label>
        <button class="btn primary" type="button" :disabled="renewalBusy" @click="saveRenewalConfig">保存自动续期配置</button>
        <button class="btn" type="button" @click="loadRenewalStatus">刷新状态</button>
        <button class="btn" type="button" @click="scanRenewalNow">立即检查所选店铺</button>
        <button class="btn" type="button" @click="loadSnapupActivities">刷新官方活动</button>
      </div>
      <div class="renewal-stats">
        <span>调度器：{{ renewal.running ? "运行中" : "未运行" }}</span>
        <span>上次检查：{{ formatTime(renewal.last_scan_at) }}</span>
        <span>下次检查：{{ formatTime(renewal.next_scan_at) }}</span>
        <span>成功 {{ renewalRunCount("success") }}</span>
        <span>失败 {{ renewalRunCount("failed") }}</span>
        <span>待核实 {{ renewalRunCount("verification_pending") }}</span>
        <span>云端：{{ cloudStatusLabel() }}</span>
        <span>规则版本：{{ renewal.auto_create_rules?.rule_version || "未保存" }}</span>
        <span>今日自动创建：{{ renewal.auto_create_summary?.daily_success || 0 }}/{{ renewal.auto_create_summary?.daily_limit || 20 }}</span>
      </div>
      <div class="store-renewal-grid">
        <article v-for="store in renewal.stores" :key="store.store_id">
          <label><input v-model="renewalStoreConfigs[store.store_id].enabled" type="checkbox" /> {{ store.store_name }}</label>
          <span>网页：{{ store.web_status }} · API：{{ store.api_status }}</span>
          <label>提前 <input v-model.number="renewalStoreConfigs[store.store_id].hours_before" class="input tiny" type="number" min="1" max="168" /> 小时进入续期检查</label>
          <label>续期 <input v-model.number="renewalStoreConfigs[store.store_id].duration_days" class="input tiny" type="number" min="1" max="7" /> 天</label>
          <small>最后检查 {{ formatTime(store.last_checked_at) }} · 下次 {{ formatTime(store.next_scan_at) }}</small>
          <small v-if="store.last_error" class="error">{{ store.last_error }}</small>
        </article>
      </div>
      <div class="table-scroll large-table">
        <table class="data-table">
          <thead><tr><th>店铺</th><th>活动</th><th>状态</th><th>开始</th><th>结束</th><th>商品数</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="row in snapupRows" :key="`${row.store_id}:${row.activity_id}`">
              <td>{{ row.store_name }}</td><td>{{ row.title || row.activity_id }}</td><td>{{ row.status }}</td>
              <td>{{ formatTime(row.start_time || row.startTime) }}</td><td>{{ formatTime(row.end_time || row.endTime) }}</td>
              <td>{{ row.product_count || 0 }}</td>
              <td class="actions"><button type="button" @click="requestManualSnapupRenew(row)">复制续期</button><button type="button" @click="openSnapupOfficial(row)">官方页面</button><button class="danger-text" type="button" @click="requestDeleteSnapup(row)">删除</button></td>
            </tr>
            <tr v-if="!snapupRows.length"><td colspan="7" class="empty">尚未读取官方限时活动</td></tr>
          </tbody>
        </table>
      </div>
      <div class="table-scroll renewal-runs">
        <table class="data-table">
          <thead><tr><th>店铺</th><th>源活动</th><th>源结束</th><th>状态</th><th>尝试</th><th>新活动</th><th>新时间</th><th>错误</th></tr></thead>
          <tbody>
            <tr v-for="row in renewal.runs" :key="row.id">
              <td>{{ row.store_name }}</td><td class="mono">{{ row.source_activity_id }}</td><td>{{ formatTime(row.source_end_time) }}</td>
              <td>{{ row.status }}</td><td>{{ row.attempts }}</td><td class="mono">{{ row.new_activity_id || "—" }}</td>
              <td>{{ formatTime(row.new_start_time) }}<br />{{ formatTime(row.new_end_time) }}</td><td class="error">{{ row.error || "—" }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <header class="panel-title compact-panel-title"><div><h2>自动新商品活动记录</h2><p>今日首次观测上新商品、规则版本、官方确认活动和重试状态。</p></div></header>
      <div class="table-scroll renewal-runs">
        <table class="data-table">
          <thead><tr><th>店铺</th><th>商品</th><th>观测日</th><th>规则版本</th><th>状态</th><th>尝试</th><th>活动</th><th>时间</th><th>错误 / 原因</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="row in renewal.auto_create_runs" :key="row.id">
              <td>{{ row.store_name }}</td><td class="mono">{{ row.product_id }}</td><td>{{ row.observed_date }}</td>
              <td class="mono">{{ row.rule_version }}</td><td>{{ autoCreateRunLabel(row.status) }}</td><td>{{ row.attempts }}</td>
              <td class="mono">{{ row.activity_id || "—" }}</td>
              <td>{{ formatTime(row.start_time) }}<br />{{ formatTime(row.end_time) }}</td>
              <td class="error">{{ row.error || "—" }}</td>
              <td><button v-if="!['manual_stopped','success'].includes(row.status)" class="danger-text" type="button" :disabled="renewalBusy" @click="stopAutoCreateRun(row)">停止</button><span v-else>—</span></td>
            </tr>
            <tr v-if="!renewal.auto_create_runs?.length"><td colspan="10" class="empty">暂无自动新商品活动记录</td></tr>
          </tbody>
        </table>
      </div>
    </section>

    <section v-else-if="activeTab === 'coupon-manage'" class="card activity-panel">
      <header class="panel-title"><div><h2>优惠券管理</h2><p>所选店铺同步、筛选、续期、停用、删除和结果记录。</p></div></header>
      <div class="toolbar-row">
        <button class="btn primary" type="button" @click="syncManagedCoupons">同步所选店铺</button>
        <button class="btn" type="button" @click="loadManagedCoupons">读取已同步优惠券</button>
        <button class="btn" type="button" @click="openBlankCouponForm">新建到所选店铺</button>
        <select v-model="couponTypeFilter" class="select"><option value="">全部类型</option><option value="1">商品条件折扣</option><option value="2">商品满减</option><option value="3">商品统一折扣</option><option value="4">商品直减</option><option value="101">店铺条件折扣</option><option value="102">店铺满减</option><option value="103">店铺统一折扣</option><option value="104">店铺直减</option></select>
        <input v-model="couponSearch" class="input" placeholder="搜索名称、ID 或店铺" />
        <button class="btn" type="button" @click="selectAllCoupons">全选当前筛选</button>
        <button class="btn" type="button" @click="clearCoupons">清空</button>
      </div>
      <div class="toolbar-row">
        <button class="btn" type="button" :disabled="!selectedCouponRows.length" @click="requestRenewCoupons">复制并续期 7 天</button>
        <button class="btn" type="button" :disabled="!selectedCouponRows.length" @click="requestCouponStatus(4)">停用选中</button>
        <button class="btn danger" type="button" :disabled="!selectedCouponRows.length" @click="requestCouponStatus(5)">删除选中</button>
      </div>
      <div class="table-scroll large-table">
        <table class="data-table">
          <thead><tr><th>选择</th><th>店铺</th><th>名称</th><th>类型</th><th>状态</th><th>结束</th><th>优惠</th><th>发行 / 领取 / 使用</th><th>优惠券 ID</th></tr></thead>
          <tbody>
            <tr v-for="row in filteredCoupons" :key="couponKey(row)" :class="{ selected: selectedCouponKeys.includes(couponKey(row)) }" @click="toggleCoupon(row)">
              <td><input type="checkbox" :checked="selectedCouponKeys.includes(couponKey(row))" /></td>
              <td>{{ row.store_name }}</td><td>{{ row.name }}</td><td>{{ row.coupon_type }}</td><td>{{ row.status }}</td>
              <td>{{ formatTime(row.end_time) }}</td><td>{{ row.discount_fee ? formatMoney(row.discount_fee) : row.discount_num }}</td>
              <td>{{ row.total_num }} / {{ row.receive_num }} / {{ row.used_num }}</td><td class="mono">{{ row.coupon_id }}</td>
            </tr>
            <tr v-if="!filteredCoupons.length"><td colspan="9" class="empty">暂无已同步优惠券</td></tr>
          </tbody>
        </table>
      </div>
      <div v-if="couponOperationResults.length" class="result-list">
        <p v-for="row in couponOperationResults" :key="`${row.store_id}:${row.coupon_id || row.store_name}`" :class="{ error: !row.ok }">{{ row.store_name }} · {{ row.name || "" }}：{{ row.ok ? "成功" : row.error }}</p>
      </div>
    </section>

    <section v-else class="card activity-panel">
      <header class="panel-title"><div><h2>执行记录</h2><p>平台活动、限时活动、优惠券、自动续期和失败重试均持久记录。</p></div><button class="btn" type="button" @click="loadOperations">刷新记录</button></header>
      <div class="table-scroll records-table">
        <table class="data-table">
          <thead><tr><th>时间</th><th>操作</th><th>店铺</th><th>活动 ID</th><th>结果</th><th>数量</th><th>说明</th></tr></thead>
          <tbody>
            <tr v-for="row in operationRecords" :key="row.id">
              <td>{{ row.created_at }}</td><td>{{ row.action }}</td><td>{{ row.store_name }}</td><td>{{ row.activity_id || "—" }}</td>
              <td>{{ row.status }}</td><td>{{ row.item_count }}</td><td>{{ row.message }}</td>
            </tr>
            <tr v-if="!operationRecords.length"><td colspan="7" class="empty">暂无执行记录</td></tr>
          </tbody>
        </table>
      </div>
    </section>

    <section v-if="progress.visible" class="activity-progress" :class="progress.status" role="status" aria-live="polite">
      <header>
        <strong>{{ progress.title }}</strong>
        <span>{{ progress.current }}/{{ progress.total }}</span>
        <button class="progress-close" type="button" title="关闭进度" aria-label="关闭进度" @click="dismissProgress">×</button>
      </header>
      <div class="progress-track"><span :style="{ width: `${progress.total ? Math.min(100, progress.current / progress.total * 100) : 8}%` }" /></div>
      <p>{{ progress.message }}</p>
      <small v-for="entry in progress.logs.slice(0, 5)" :key="`${entry.time}:${entry.message}`" :class="entry.type">{{ entry.time }} · {{ entry.message }}</small>
    </section>

    <ModalDialog :open="couponFormOpen" title="优惠券活动配置" :subtitle="selectedStoreCount ? `目标 ${selectedStoreCount} 家店铺` : ''" width="760px" :busy="risk.running" @close="couponFormOpen = false">
      <div class="form-grid">
        <label class="field full"><span>优惠券名称</span><input v-model="couponForm.name" class="input" /></label>
        <label class="field"><span>优惠券类型</span><input v-model.number="couponForm.type" class="input" type="number" /></label>
        <label class="field"><span>促销类型</span><input v-model.number="couponForm.promote_type" class="input" type="number" /></label>
        <label class="field"><span>优惠金额（元）</span><input v-model="couponForm.discount_fee_yuan" class="input" inputmode="decimal" /></label>
        <label class="field"><span>折扣数值</span><input v-model.number="couponForm.discount_num" class="input" type="number" /></label>
        <label class="field"><span>发放总量</span><input v-model.number="couponForm.total_num" class="input" type="number" min="1" /></label>
        <label class="field"><span>每人限领</span><input v-model.number="couponForm.limit_num_one_person" class="input" type="number" min="1" /></label>
        <label class="field"><span>开始时间</span><input v-model="couponForm.start_time" class="input" type="datetime-local" /></label>
        <label class="field"><span>结束时间</span><input v-model="couponForm.end_time" class="input" type="datetime-local" /></label>
        <label class="field"><span>有效期类型</span><input v-model.number="couponForm.valid_type" class="input" type="number" /></label>
        <label class="field"><span>领取后有效天数</span><input v-model.number="couponForm.valid_day_num" class="input" type="number" min="1" /></label>
      </div>
      <template #footer>
        <button class="btn" type="button" @click="couponFormOpen = false">取消</button>
        <button class="btn primary" type="button" @click="requestCreateCoupons">下一步确认</button>
      </template>
    </ModalDialog>

    <ModalDialog :open="risk.open" :title="risk.title" width="580px" :busy="risk.running" @close="risk.open = false">
      <p class="risk-copy">{{ risk.description }}</p>
      <template #footer>
        <button class="btn" type="button" :disabled="risk.running" @click="risk.open = false">取消</button>
        <button class="btn danger" type="button" :disabled="risk.running" @click="confirmRisk">{{ risk.running ? "执行中…" : risk.confirmLabel }}</button>
      </template>
    </ModalDialog>

    <div v-if="note" class="toast">{{ note }}</div>
  </section>
</template>

<style scoped>
.activity-center-page { height: 100%; min-height: 0; display: flex; flex-direction: column; gap: 10px; overflow: auto; padding: 14px; background: #f4f7f9; color: #20374a; }
.activity-head { display: flex; align-items: center; justify-content: space-between; }
.activity-head h1 { margin: 0; font-size: 22px; }
.activity-head p { margin: 4px 0 0; color: #718292; font-size: 11px; }
.activity-head > strong { color: #147452; font-size: 12px; }
.card { border: 1px solid #dce4ea; border-radius: 9px; background: #fff; box-shadow: 0 7px 18px rgba(28, 55, 76, .04); }
.store-selector { flex: 0 0 auto; padding: 12px; }
.store-selector > header, .panel-title { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.store-selector h2, .panel-title h2 { margin: 0; font-size: 15px; }
.store-selector p, .panel-title p { margin: 4px 0 0; color: #748595; font-size: 10px; }
.store-selector-scroll { max-height: 185px; margin-top: 9px; overflow: auto; }
table { width: 100%; border-collapse: collapse; }
th { position: sticky; top: 0; z-index: 2; height: 34px; padding: 0 8px; border-bottom: 1px solid #dce4ea; color: #637587; background: #f7f9fb; font-size: 10px; font-weight: 600; text-align: left; white-space: nowrap; }
td { min-height: 36px; padding: 7px 8px; border-bottom: 1px solid #edf1f4; font-size: 10px; vertical-align: middle; }
tbody tr { cursor: pointer; }
tbody tr.selected { background: #eff8f4; }
td small { display: block; margin-top: 2px; color: #83919d; font-size: 9px; }
.mono { font-family: "Cascadia Code", Consolas, monospace; overflow-wrap: anywhere; }
.empty { height: 110px; color: #8a98a4; text-align: center; }
.activity-tabs { flex: 0 0 auto; display: flex; gap: 5px; overflow-x: auto; }
.activity-tabs button { min-width: 132px; height: 38px; padding: 0 15px; border: 1px solid #d8e1e8; border-radius: 7px; color: #566a7c; background: #fff; font-size: 11px; cursor: pointer; white-space: nowrap; }
.activity-tabs button.active { border-color: #167953; color: #fff; background: #167953; }
.activity-panel { flex: 0 0 auto; padding: 13px; }
.toolbar-row, .form-strip, .renewal-config { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 8px; margin: 10px 0; }
.toolbar-row label, .form-strip label, .renewal-config label, .settings-box label, .field { display: grid; gap: 4px; color: #596d7e; font-size: 10px; }
.input, .select { height: 31px; padding: 0 8px; border: 1px solid #cfd9e2; border-radius: 5px; color: #29485f; background: #fff; font-size: 10px; }
.toolbar-row .input { min-width: 200px; }
.compact { width: 90px; }
.tiny { width: 58px; }
.btn { min-height: 31px; padding: 0 11px; border: 1px solid #d3dde5; border-radius: 5px; color: #38566d; background: #fff; font-size: 10px; cursor: pointer; }
.btn.primary { border-color: #15845b; color: #fff; background: #15845b; }
.btn.danger { border-color: #d95a64; color: #fff; background: #d95a64; }
.btn:disabled { opacity: .45; cursor: not-allowed; }
.meta-line { margin: 6px 0; color: #7a8a98; font-size: 9px; }
.split-grid { display: grid; grid-template-columns: minmax(0, 1fr) 220px; gap: 10px; align-items: start; }
.settings-box { padding: 11px; display: grid; gap: 8px; border: 1px solid #e1e8ed; border-radius: 7px; background: #f9fbfc; }
.table-scroll { overflow: auto; border: 1px solid #e2e8ed; border-radius: 7px; }
.result-table { max-height: 260px; margin-top: 10px; }
.large-table { max-height: 520px; }
.result-list { margin-top: 8px; padding: 8px 10px; border: 1px solid #dbe7e1; border-radius: 6px; background: #f5fbf8; }
.result-list p { margin: 3px 0; color: #317258; font-size: 10px; }
.result-list p.error, .error { color: #bd4650 !important; }
.prefill-panel { margin-top: 10px; padding: 9px 11px; border: 1px solid #e0e7ec; border-radius: 7px; background: #fbfcfd; }
.prefill-panel summary { color: #35566e; font-size: 11px; cursor: pointer; }
.actions { white-space: nowrap; }
.actions button, .text-button { padding: 0; border: 0; color: #23709d; background: transparent; font-size: 9px; cursor: pointer; }
.actions button + button { margin-left: 8px; }
.danger-text { color: #c14852 !important; }
.renewal-stats { display: flex; flex-wrap: wrap; gap: 8px; margin: 8px 0; }
.renewal-stats span { padding: 6px 9px; border-radius: 5px; color: #496274; background: #f2f7fa; font-size: 9px; }
.smart-auto-strip { display: flex; flex-wrap: wrap; align-items: center; gap: 7px 16px; margin: 10px 0; padding: 10px 12px; border: 1px solid #e2b9bf; border-radius: 7px; color: #7c5960; background: #fff7f8; font-size: 10px; }
.smart-auto-strip.enabled { border-color: #b9ddc8; color: #286b49; background: #f2faf5; }
.smart-auto-strip .switch-label { font-weight: 600; }
.compact-panel-title { margin-top: 14px; }
.store-renewal-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 9px; margin: 10px 0; }
.store-renewal-grid article { padding: 10px; display: grid; gap: 6px; border: 1px solid #e0e7ec; border-radius: 7px; background: #fafcfd; }
.switch-label { display: flex !important; align-items: center; gap: 6px; }
.renewal-runs { max-height: 280px; margin-top: 10px; }
.records-table { max-height: 560px; }
.activity-progress { position: sticky; bottom: 8px; z-index: 20; max-height: 180px; overflow: auto; padding: 10px 12px; display: grid; gap: 5px; border: 1px solid #bdd7ca; border-radius: 7px; background: #f4fcf8; box-shadow: 0 8px 24px rgba(26, 64, 47, .12); }
.activity-progress header { display: flex; justify-content: space-between; gap: 12px; }
.activity-progress header span { color: #5f776b; font-variant-numeric: tabular-nums; }
.activity-progress header .progress-close { margin-left: auto; padding: 0 3px; border: 0; color: #527365; background: transparent; font-size: 18px; line-height: 1; cursor: pointer; }
.progress-track { height: 5px; overflow: hidden; border-radius: 9px; background: #e2eee8; }
.progress-track span { display: block; height: 100%; background: linear-gradient(90deg, #17835a, #56bb8c); transition: width 150ms ease; }
.activity-progress p { margin: 0; color: #36584a; font-size: 10px; }
.activity-progress small { color: #6e8176; font-size: 9px; }
.activity-progress.failed, .activity-progress.partial { border-color: #ecc6ca; background: #fff7f8; }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.field.full { grid-column: 1 / -1; }
.risk-copy { color: #53697a; line-height: 1.8; }
@media (max-width: 1000px) { .split-grid { grid-template-columns: 1fr; } }
</style>
