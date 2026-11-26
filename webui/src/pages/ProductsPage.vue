<script setup>
import { computed, nextTick, onMounted, reactive, ref, watch } from "vue";
import { call, formatMoney } from "../api";
import Icon from "../components/Icon.vue";
import ModalDialog from "../components/ModalDialog.vue";
import { productMetricText } from "../productMetrics";
import {
  DEFAULT_PRODUCT_PAGE_SIZE,
  parseProductPageSize,
  productPageCount,
} from "../productPaging";
import {
  nextProductSort,
  sortProducts,
} from "../productSorting";

const stores = ref([]);
const products = ref([]);
const loading = ref(false);
const note = ref("");
const activeStatus = ref("all");
const selectedIds = ref([]);
const confirmAction = ref(null);
const currentPage = ref(1);
const perPage = ref(DEFAULT_PRODUCT_PAGE_SIZE);
const perPageInput = ref(String(DEFAULT_PRODUCT_PAGE_SIZE));
const operationLogs = ref([]);
const operationLogFilter = ref("");
const OPERATION_LOG_EXPANDED_KEY = "langlang.products.operation-log-expanded";
function storedOperationLogExpanded() {
  try {
    const value = localStorage.getItem(OPERATION_LOG_EXPANDED_KEY);
    if (value === "0") return false;
    if (value === "1") return true;
  } catch {
    // Default to expanded when local storage is unavailable.
  }
  return true;
}
const operationLogExpanded = ref(storedOperationLogExpanded());
const stockDialogOpen = ref(false);
const stockLoading = ref(false);
const stockDetail = ref(null);
const productImageInput = ref(null);
const productImageBusy = ref(false);
const stockForm = reactive({
  sku_id: "",
  diff_type: 1,
  amount: 1,
});
const actionProgress = reactive({
  busy: false,
  type: "",
  label: "",
  current: 0,
  total: 0,
  productId: "",
  status: "",
});
const operationBusy = computed(() => actionProgress.busy);
const filters = reactive({ store_id: "", search: "", min_price: "", max_price: "" });
const ALL_STORES = "__all__";
const sortState = reactive({ key: "", direction: "" });
const focusedProductId = ref("");
const productFocusMessage = ref("");
const productProgress = reactive({
  status: "",
  current: 0,
  total: 0,
  message: "",
});
const statusTabs = [
  ["all", "全部"],
  ["sales", "销售中"],
  ["offline", "已下架"],
  ["audit", "审核中"],
  ["audit_pending", "审核待处理"],
  ["draft", "草稿箱"],
  ["failed", "审核失败"],
  ["strategy", "策略生效"],
  ["recycle", "回收站"],
];

const filtered = computed(() => products.value.filter((item) => {
  const status = Number(item.status || 0);
  const statusMatches = {
    all: true,
    sales: status === 5,
    offline: [11, 12, 13, 14, 15, 20].includes(status),
    audit: status === 0 && [1, 2, 3].includes(Number(item.edit_status)),
    audit_pending: status === 0,
    draft: status === 0 && Number(item.edit_status) === 0,
    failed: status === 3,
    strategy: status === 7,
    recycle: status === 6,
  }[activeStatus.value];
  if (!statusMatches) return false;
  const price = Number(item.min_price || 0);
  if (filters.min_price && price < Number(filters.min_price) * 100) return false;
  if (filters.max_price && price > Number(filters.max_price) * 100) return false;
  const search = filters.search.trim().toLowerCase();
  if (search) {
    return [item.title, item.product_id, item.spu_code, item.out_product_id]
      .some((value) => String(value || "").toLowerCase().includes(search));
  }
  return true;
}));

const sortedFiltered = computed(() => sortProducts(filtered.value, sortState));
const selectedStore = computed(() => (
  stores.value.find((store) => Number(store.id) === Number(filters.store_id)) || null
));
const isAllStores = computed(() => filters.store_id === ALL_STORES);
const selectedStoreLabel = computed(() => (
  isAllStores.value ? "全部店铺" : (selectedStore.value?.name || "未选择店铺")
));
const totalPages = computed(() => productPageCount(sortedFiltered.value.length, perPage.value));
const pagedProducts = computed(() => {
  const start = (currentPage.value - 1) * perPage.value;
  return sortedFiltered.value.slice(start, start + perPage.value);
});
const displayedCount = computed(() => pagedProducts.value.length);
const displayStart = computed(() => (
  displayedCount.value ? (currentPage.value - 1) * perPage.value + 1 : 0
));
const displayEnd = computed(() => displayStart.value + Math.max(0, displayedCount.value - 1));
const selectedProducts = computed(() => products.value.filter((item) => (
  selectedIds.value.includes(item.product_id)
)));
const allSelectedInRecycle = computed(() => (
  selectedProducts.value.length > 0
  && selectedProducts.value.every((item) => Number(item.status) === 6)
));
const batchDeleteLabel = computed(() => (
  allSelectedInRecycle.value
    ? "批量彻底删除"
    : selectedProducts.value.some((item) => Number(item.status) === 6)
      ? "批量删除（混合）"
      : "批量移入回收站"
));
const actionProgressPercent = computed(() => (
  actionProgress.total > 0
    ? Math.round(actionProgress.current / actionProgress.total * 100)
    : 0
));

const statusNotice = computed(() => {
  if (!filters.store_id) return "请选择店铺";
  return `${selectedStoreLabel.value}：共 ${filtered.value.length} 个商品，当前显示 ${displayedCount.value} 个`;
});

watch([() => filters.search, () => filters.min_price, () => filters.max_price], () => {
  currentPage.value = 1;
});

watch(totalPages, (pages) => {
  if (currentPage.value > pages) currentPage.value = pages;
});
watch(operationLogExpanded, (expanded) => {
  try {
    localStorage.setItem(OPERATION_LOG_EXPANDED_KEY, expanded ? "1" : "0");
  } catch {
    // Keep the in-memory preference when local storage is unavailable.
  }
});

function toggleOperationLog() {
  operationLogExpanded.value = !operationLogExpanded.value;
}

function setStatus(status) {
  activeStatus.value = status;
  currentPage.value = 1;
}

function toggleSort(key) {
  Object.assign(sortState, nextProductSort(sortState, key));
  currentPage.value = 1;
}

function sortIndicator(key) {
  if (sortState.key !== key) return "";
  return sortState.direction === "asc" ? "▲" : "▼";
}

function setPage(page) {
  currentPage.value = Math.max(1, Math.min(totalPages.value, Number(page) || 1));
}

function applyPerPage() {
  const parsed = parseProductPageSize(perPageInput.value, perPage.value);
  perPageInput.value = String(parsed.value);
  if (parsed.error) {
    note.value = parsed.error;
    return;
  }
  perPage.value = parsed.value;
  currentPage.value = 1;
  note.value = `每页显示 ${perPage.value} 条`;
}

function moneyOrDash(value) {
  return value === null || value === undefined || value === ""
    ? "—"
    : formatMoney(value);
}

function recordLog(message) {
  operationLogs.value.unshift({
    id: `${Date.now()}-${Math.random()}`,
    time: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
    message,
  });
  operationLogs.value = operationLogs.value.slice(0, 100);
}

function productOperationStatusLabel(status) {
  return {
    success: "成功",
    failed: "失败",
    partial: "部分完成",
    pending: "待核实",
    running: "处理中",
  }[status] || status || "记录";
}

async function loadOperationLogs() {
  if (!filters.store_id) {
    operationLogs.value = [];
    return;
  }
  try {
    const result = await call("products.operation-logs", {
      store_id: filters.store_id,
      status: operationLogFilter.value,
    });
    operationLogs.value = (result.logs || []).map((row) => ({
      id: `db-${row.id}`,
      time: String(row.finished_at || row.started_at || "").replace("T", " ").slice(0, 19),
      message: row.message,
      title: row.title,
      productId: row.product_id,
      action: row.action,
      status: row.status,
      error: row.error,
    }));
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
  }
}

async function changeStore() {
  currentPage.value = 1;
  await loadProducts();
  await loadOperationLogs();
}

async function loadStores() {
  try {
    stores.value = (await call("stores.list", {})).stores || [];
    const query = routeQuery();
    const requestedStoreId = String(query.get("store_id") || "");
    if (
      requestedStoreId
      && stores.value.some(
        (store) => String(store.id) === requestedStoreId,
      )
    ) {
      filters.store_id = requestedStoreId;
      return;
    }
    const selectedStillExists = stores.value.some((store) => (
      Number(store.id) === Number(filters.store_id)
    ));
    if (!filters.store_id || (!selectedStillExists && filters.store_id !== ALL_STORES)) {
      filters.store_id = stores.value[0]?.id ?? ALL_STORES;
    }
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
  }
}

function routeQuery() {
  const hash = String(window.location.hash || "");
  const queryIndex = hash.indexOf("?");
  return new URLSearchParams(queryIndex >= 0 ? hash.slice(queryIndex + 1) : "");
}

async function locateQueriedProduct() {
  const query = routeQuery();
  const productId = String(query.get("product_id") || "").trim();
  if (!productId) return false;
  if (!filters.store_id || isAllStores.value) {
    products.value = [];
    focusedProductId.value = "";
    productFocusMessage.value = "无法定位商品：必须指定具体店铺，不能使用全部店铺。";
    note.value = productFocusMessage.value;
    return true;
  }
  loading.value = true;
  note.value = `正在定位商品 ${productId}…`;
  try {
    const result = await call("products.locate", {
      store_id: filters.store_id,
      product_id: productId,
    });
    const product = result.product;
    if (!product || String(product.product_id || "") !== productId) {
      throw new Error(
        `店铺 ${selectedStoreLabel.value} 未返回商品 ${productId}`,
      );
    }
    products.value = [product];
    filters.search = productId;
    activeStatus.value = "all";
    selectedIds.value = [productId];
    focusedProductId.value = productId;
    currentPage.value = 1;
    productFocusMessage.value = (
      `已定位：${selectedStoreLabel.value} · ${product.title || productId}`
      + ` · 商品 ID ${productId}`
    );
    note.value = productFocusMessage.value;
    await nextTick();
    Array.from(document.querySelectorAll("[data-product-id]"))
      .find((element) => element.dataset.productId === productId)
      ?.scrollIntoView({ block: "center", behavior: "smooth" });
  } catch (cause) {
    products.value = [];
    focusedProductId.value = "";
    productFocusMessage.value = (
      `定位商品失败：${selectedStoreLabel.value} · ${productId} · `
      + (cause instanceof Error ? cause.message : String(cause))
    );
    note.value = productFocusMessage.value;
  } finally {
    loading.value = false;
  }
  return true;
}

async function clearProductFocus() {
  focusedProductId.value = "";
  productFocusMessage.value = "";
  filters.search = "";
  await loadProducts();
}

async function loadProducts() {
  if (!filters.store_id) return;
  loading.value = true;
  note.value = "";
  try {
    products.value = (await call("products.list", {
      store_id: filters.store_id,
      search: filters.search,
    })).products || [];
    selectedIds.value = [];
    currentPage.value = 1;
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    loading.value = false;
  }
}

async function refreshProducts() {
  if (!filters.store_id) return;
  loading.value = true;
  note.value = "正在连接微信小店并读取商品，请稍候…";
  productProgress.status = "starting";
  productProgress.current = 0;
  productProgress.total = 0;
  productProgress.message = "正在获取商品目录…";
  recordLog(`开始获取商品，当前状态：${statusTabs.find(([key]) => key === activeStatus.value)?.[1] || "全部"}`);
  try {
    const statusToTab = {
      sales: "sales",
      offline: "offline",
      audit: "audit",
      audit_pending: "audit",
      draft: "draft",
      failed: "failed",
      strategy: "strategy",
      recycle: "recycle",
    };
    const tabKeys = activeStatus.value === "all"
      ? ["sales", "audit", "failed", "draft", "offline", "strategy", "recycle"]
      : [statusToTab[activeStatus.value] || "sales"];
    const started = await call("products.refresh.start", {
      store_id: filters.store_id,
      search: filters.search,
      tab_keys: tabKeys,
    });
    let job = null;
    let lastLoggedPercent = -1;
    while (true) {
      job = await call("products.refresh.status", { job_id: started.job_id });
      productProgress.status = job.status;
      productProgress.current = Number(job.current || 0);
      productProgress.total = Number(job.total || 0);
      productProgress.message = job.message || "";
      note.value = job.message || "正在读取商品…";
      if (job.total > 0) {
        const percent = Math.floor((Number(job.current || 0) / Number(job.total)) * 10) * 10;
        if (percent > lastLoggedPercent) {
          lastLoggedPercent = percent;
          recordLog(`正在读取商品详情 ${job.current}/${job.total}`);
        }
      }
      if (["completed", "partial"].includes(job.status)) break;
      if (job.status === "failed") {
        throw new Error(job.message || "商品刷新失败");
      }
      await new Promise((resolve) => setTimeout(resolve, 350));
    }
    products.value = job.products || [];
    const errors = job.errors || [];
    note.value = errors.length
      ? errors.join("；")
      : (job.message || `商品数据已更新，共 ${products.value.length} 件`);
    recordLog(
      errors.length
        ? `商品获取结束：读取成功 ${job.current || products.value.length} 个，失败 ${errors.length} 个`
        : `商品获取完成：读取 ${products.value.length} 个，当前显示 ${Math.min(perPage.value, products.value.length)} 个`,
    );
    currentPage.value = 1;
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
    recordLog(`商品获取失败：${note.value}`);
  } finally {
    loading.value = false;
  }
}

function isSelected(productId) {
  return selectedIds.value.includes(productId);
}

function toggleSelected(productId) {
  selectedIds.value = isSelected(productId)
    ? selectedIds.value.filter((id) => id !== productId)
    : [...selectedIds.value, productId];
}

function togglePage() {
  const pageIds = pagedProducts.value.map((item) => item.product_id);
  selectedIds.value = pageIds.every((id) => isSelected(id))
    ? selectedIds.value.filter((id) => !pageIds.includes(id))
    : [...new Set([...selectedIds.value, ...pageIds])];
}

function toggleAllPages() {
  const pageIds = filtered.value.map((item) => item.product_id);
  selectedIds.value = pageIds.every((id) => isSelected(id))
    ? selectedIds.value.filter((id) => !pageIds.includes(id))
    : [...new Set(selectedIds.value.concat(pageIds))];
}

async function setListing(listed) {
  if (!selectedIds.value.length) {
    note.value = "请先选择商品";
    return;
  }
  confirmAction.value = { type: "listing", listed };
}

function setSingleListing(product, listed) {
  selectedIds.value = [product.product_id];
  confirmAction.value = { type: "listing", listed };
}

function setSingleDelete(product) {
  selectedIds.value = [product.product_id];
  confirmAction.value = { type: "delete" };
}

function singleDeleteLabel(product) {
  return Number(product.status) === 6 ? "彻底删除" : "删除";
}

async function performListing() {
  if (operationBusy.value) return;
  const rows = selectedProducts.value.slice();
  if (!rows.length) {
    note.value = "请先选择商品";
    return;
  }
  const listed = confirmAction.value.listed;
  confirmAction.value = null;
  actionProgress.busy = true;
  actionProgress.type = listed ? "listing" : "delisting";
  actionProgress.label = listed ? "批量上架" : "批量下架";
  actionProgress.current = 0;
  actionProgress.total = rows.length;
  actionProgress.productId = "";
  actionProgress.status = "running";
  const failures = [];
  try {
    for (const [index, row] of rows.entries()) {
      actionProgress.productId = row.product_id;
      try {
        const result = await call("products.set-listing", {
          store_id: row.store_id,
          product_ids: [row.product_id],
          listed,
        });
        if (Number(result.failed || 0) > 0 || result.cancelled) {
          throw new Error(
            result.errors?.[0]
            || result.message
            || `商品 ${row.product_id} 上架状态未确认成功`,
          );
        }
      } catch (cause) {
        failures.push(`${row.product_id}: ${cause instanceof Error ? cause.message : String(cause)}`);
      }
      actionProgress.current = index + 1;
    }
    await loadProducts();
    await loadOperationLogs();
    actionProgress.status = failures.length
      ? (failures.length === rows.length ? "failed" : "partial")
      : "success";
    note.value = failures.length
      ? `${listed ? "批量上架" : "批量下架"}完成：成功 ${rows.length - failures.length} 个，失败 ${failures.length} 个：${failures.slice(0, 2).join("；")}`
      : `${listed ? "批量上架" : "批量下架"}完成：成功 ${rows.length} 个`;
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    actionProgress.busy = false;
    actionProgress.productId = "";
  }
}

async function performDelete() {
  if (operationBusy.value) return;
  const rows = selectedProducts.value.slice();
  if (!rows.length) {
    note.value = "请先选择商品";
    return;
  }
  confirmAction.value = null;
  actionProgress.busy = true;
  actionProgress.type = "delete";
  const recycleCount = rows.filter((row) => Number(row.status) === 6).length;
  const operationLabel = recycleCount === rows.length
    ? "批量彻底删除"
    : recycleCount > 0
      ? "批量删除（回收站彻底删除 / 其他移入回收站）"
      : "批量移入回收站";
  actionProgress.label = operationLabel;
  actionProgress.current = 0;
  actionProgress.total = rows.length;
  actionProgress.productId = "";
  actionProgress.status = "running";
  const failures = [];
  try {
    for (const [index, row] of rows.entries()) {
      actionProgress.productId = row.product_id;
      try {
        await call("products.delete", {
          store_id: row.store_id,
          product_id: row.product_id,
        });
      } catch (cause) {
        failures.push(`${row.product_id}: ${cause instanceof Error ? cause.message : String(cause)}`);
      }
      actionProgress.current = index + 1;
    }
    await loadProducts();
    await loadOperationLogs();
    actionProgress.status = failures.length
      ? (failures.length === rows.length ? "failed" : "partial")
      : "success";
    note.value = failures.length
      ? `${operationLabel}完成：成功 ${rows.length - failures.length} 个，失败或待核实 ${failures.length} 个：${failures.slice(0, 2).join("；")}`
      : `${operationLabel}完成：成功 ${rows.length} 个`;
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    actionProgress.busy = false;
    actionProgress.productId = "";
  }
}

function setCancelAudit(product = null) {
  if (product) {
    selectedIds.value = [product.product_id];
  }
  if (!selectedIds.value.length) {
    note.value = "请先选择商品";
    return;
  }
  confirmAction.value = { type: "cancel_audit" };
}

async function performCancelAudit() {
  if (operationBusy.value) return;
  const productIds = selectedIds.value.slice();
  if (!productIds.length) {
    note.value = "请先选择商品";
    return;
  }
  confirmAction.value = null;
  actionProgress.busy = true;
  actionProgress.type = "cancel_audit";
  actionProgress.label = "批量撤回审核";
  actionProgress.current = 0;
  actionProgress.total = productIds.length;
  actionProgress.productId = "";
  actionProgress.status = "running";
  const failures = [];
  try {
    const rows = selectedProducts.value.filter((row) => productIds.includes(row.product_id));
    for (const [index, row] of rows.entries()) {
      const productId = row.product_id;
      actionProgress.productId = productId;
      try {
        await call("products.cancel-audit", {
          store_id: row.store_id,
          product_id: productId,
        });
      } catch (cause) {
        failures.push(`${productId}: ${cause instanceof Error ? cause.message : String(cause)}`);
      }
      actionProgress.current = index + 1;
    }
    await loadProducts();
    await loadOperationLogs();
    actionProgress.status = failures.length
      ? (failures.length === productIds.length ? "failed" : "partial")
      : "success";
    note.value = failures.length
      ? `批量撤回审核完成：成功 ${productIds.length - failures.length} 个，失败 ${failures.length} 个：${failures.slice(0, 2).join("；")}`
      : `已撤回 ${productIds.length} 个商品的审核`;
    recordLog(note.value);
  } catch (cause) {
    actionProgress.status = "failed";
    note.value = cause instanceof Error ? cause.message : String(cause);
    recordLog(note.value);
  } finally {
    actionProgress.busy = false;
    actionProgress.productId = "";
  }
}

async function openProductStock(product) {
  if (stockLoading.value) return;
  stockLoading.value = true;
  note.value = "正在读取商品 SKU 与库存…";
  try {
    const result = await call("products.detail", {
      store_id: product.store_id,
      product_id: product.product_id,
    });
    stockDetail.value = result.product || null;
    const firstSku = stockDetail.value?.skus?.find((row) => row.sku_id);
    Object.assign(stockForm, {
      sku_id: firstSku?.sku_id || "",
      diff_type: 1,
      amount: 1,
    });
    stockDialogOpen.value = Boolean(firstSku);
    if (!firstSku) {
      note.value = "该商品没有可修改的 SKU 库存数据";
    }
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    stockLoading.value = false;
  }
}

async function submitProductStock() {
  if (stockLoading.value || !stockDetail.value || !stockForm.sku_id) return;
  stockLoading.value = true;
  try {
    await call("products.update-stock", {
      store_id: stockDetail.value.store_id || filters.store_id,
      product_id: stockDetail.value.product_id,
      sku_id: stockForm.sku_id,
      diff_type: stockForm.diff_type,
      amount: stockForm.amount,
    });
    stockDialogOpen.value = false;
    await refreshProducts();
    await loadOperationLogs();
    note.value = `商品 ${stockDetail.value.product_id} 的 SKU ${stockForm.sku_id} 库存已更新`;
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    stockLoading.value = false;
  }
}

async function copyTagLink(product) {
  if (operationBusy.value) return;
  actionProgress.busy = true;
  actionProgress.type = "taglink";
  actionProgress.label = "读取商品短链";
  actionProgress.current = 0;
  actionProgress.total = 1;
  actionProgress.productId = product.product_id;
  actionProgress.status = "running";
  try {
    const result = await call("products.taglink", {
      store_id: product.store_id,
      product_id: product.product_id,
    });
    if (!result.link) throw new Error("官方接口没有返回商品短链");
    try {
      await navigator.clipboard.writeText(result.link);
      note.value = `商品短链已复制：${result.link}`;
    } catch {
      note.value = `商品短链：${result.link}`;
    }
    actionProgress.current = 1;
    actionProgress.status = "success";
  } catch (cause) {
    actionProgress.status = "failed";
    note.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    actionProgress.busy = false;
    actionProgress.productId = "";
  }
}

async function openOfficialProduct(product) {
  if (actionProgress.busy) return;
  actionProgress.busy = true;
  actionProgress.type = "open_official";
  actionProgress.label = "打开商品后台";
  actionProgress.current = 0;
  actionProgress.total = 1;
  actionProgress.productId = product.product_id;
  actionProgress.status = "running";
  note.value = "正在定位并打开商品官方后台…";
  try {
    const started = await call("products.open-official", {
      store_id: product.store_id,
      product_id: product.product_id,
    });
    let job = null;
    while (true) {
      job = await call("products.open-official.status", { job_id: started.job_id });
      note.value = job.message || "正在打开商品官方后台…";
      if (job.status === "completed") break;
      if (job.status === "failed") {
        throw new Error(job.error || job.message || "打开商品后台失败");
      }
      await new Promise((resolve) => setTimeout(resolve, 300));
    }
    actionProgress.current = 1;
    actionProgress.status = "success";
    note.value = job.message || `已打开商品 ${product.product_id} 的官方后台`;
  } catch (cause) {
    actionProgress.status = "failed";
    note.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    actionProgress.busy = false;
    actionProgress.productId = "";
  }
}

function chooseProductImages() {
  if (!filters.store_id || productImageBusy.value) return;
  productImageInput.value?.click();
}

async function uploadProductImages(event) {
  const files = Array.from(event.target.files || []);
  event.target.value = "";
  if (!files.length || productImageBusy.value) return;
  productImageBusy.value = true;
  try {
    const payloadFiles = await Promise.all(files.map(async (file) => {
      const bytes = new Uint8Array(await file.arrayBuffer());
      let binary = "";
      for (let offset = 0; offset < bytes.length; offset += 0x8000) {
        binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
      }
      return { name: file.name, content_base64: btoa(binary) };
    }));
    const result = await call("products.upload-images", {
      store_id: filters.store_id,
      files: payloadFiles,
    });
    note.value = `商品图片上传完成：${result.count || 0} 张；${(result.images || []).slice(0, 3).join("；")}`;
    await loadOperationLogs();
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    productImageBusy.value = false;
  }
}

onMounted(async () => {
  await loadStores();
  if (!(await locateQueriedProduct())) {
    await loadProducts();
  }
  await loadOperationLogs();
});
</script>

<template>
  <section class="page product-page">
    <header class="page-header card product-title-card">
      <div><h1>商品管理</h1></div>
    </header>

    <div class="card products-filter">
      <div class="filter-row">
        <label>目标店铺</label>
        <select v-model="filters.store_id" class="select store-select" @change="changeStore">
          <option :value="ALL_STORES">全部店铺</option>
          <option v-for="store in stores" :key="store.id" :value="store.id">{{ store.name }}</option>
        </select>
        <label>商品状态</label>
        <button
          v-for="[key, label] in statusTabs"
          :key="key"
          class="status-button"
          :class="{ active: activeStatus === key }"
          type="button"
          @click="setStatus(key)"
        >{{ label }}</button>
        <button class="btn muted get-button" type="button" :disabled="!filters.store_id || isAllStores || loading" :title="isAllStores ? '请选择具体店铺后刷新' : '刷新当前店铺'" @click="refreshProducts">
          {{ loading ? "正在获取…" : "获取 / 刷新" }}
        </button>
        <span v-if="loading || productProgress.total" class="product-progress-count">
          {{ selectedStoreLabel }} · 页签 {{ productProgress.current }} / {{ productProgress.total || "?" }}
        </span>
      </div>
      <div class="filter-row product-search-row">
        <span class="product-count">{{ statusNotice }}</span>
        <span class="selected-count">已选择 {{ selectedIds.length }} 个商品</span>
        <input v-model="filters.search" class="input product-search" placeholder="搜索名称或商品 ID，多个关键词空格分隔" />
        <input v-model="filters.min_price" class="input price-input" type="number" placeholder="最低价" />
        <span>至</span>
        <input v-model="filters.max_price" class="input price-input" type="number" placeholder="最高价" />
        <button class="btn primary" type="button" @click="note = `筛选中显示 ${filtered.length} 个商品`">筛选</button>
        <button class="btn muted" type="button" @click="filters.search = ''; filters.min_price = ''; filters.max_price = ''">重置</button>
      </div>
    </div>

    <div v-if="loading" class="product-loading-bar">
      <span class="loading-dot" />
      <span>{{ productProgress.message || "正在从微信小店实时读取商品…" }}</span>
    </div>

    <div v-if="productFocusMessage" class="product-focus-banner" role="status">
      <strong>{{ productFocusMessage }}</strong>
      <button class="btn muted" type="button" @click="clearProductFocus">显示全部商品</button>
    </div>

    <div class="product-toolbar card">
      <label>
        <input
          type="checkbox"
          :checked="pagedProducts.length > 0 && pagedProducts.every((item) => isSelected(item.product_id))"
          @change="togglePage"
        />
        全选当前页
      </label>
      <button class="btn muted" type="button" @click="toggleAllPages">全选所有页面</button>
      <button class="btn muted" type="button" :disabled="!selectedIds.length" @click="selectedIds = []">清空选择</button>
      <input ref="productImageInput" class="hidden-file-input" type="file" accept=".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp" multiple @change="uploadProductImages" />
      <button class="btn muted" type="button" :disabled="!filters.store_id || isAllStores || productImageBusy" :title="isAllStores ? '请选择具体店铺后上传图片' : '上传商品图片'" @click="chooseProductImages">{{ productImageBusy ? "上传图片中…" : "上传商品图片" }}</button>
      <button class="btn primary" type="button" :disabled="!selectedIds.length || operationBusy" @click="setListing(true)">批量上架</button>
      <button class="btn muted" type="button" :disabled="!selectedIds.length || operationBusy" @click="setListing(false)">{{ actionProgress.type === "delisting" && operationBusy ? `下架中 ${actionProgress.current}/${actionProgress.total}` : "批量下架" }}</button>
       <button class="btn muted" type="button" :disabled="!selectedIds.length || operationBusy" @click="setCancelAudit()">{{ actionProgress.type === "cancel_audit" && operationBusy ? `撤回审核中 ${actionProgress.current}/${actionProgress.total}` : "批量撤回审核" }}</button>
      <button class="btn danger" type="button" :disabled="!selectedIds.length || operationBusy" @click="confirmAction = { type: 'delete' }">{{ actionProgress.type === "delete" && operationBusy ? `${batchDeleteLabel}中 ${actionProgress.current}/${actionProgress.total}` : batchDeleteLabel }}</button>
      <button class="btn amber" type="button" disabled title="当前服务端未提供跨店复制接口">复制到其他店铺</button>
    </div>

    <div v-if="actionProgress.status" class="product-operation-banner" :class="actionProgress.status">
      <span v-if="actionProgress.busy" class="loading-dot" />
      <strong>{{ actionProgress.label }} · {{ productOperationStatusLabel(actionProgress.status) }}</strong>
      <span>{{ actionProgress.current }} / {{ actionProgress.total }}</span>
      <small v-if="actionProgress.productId">当前商品 {{ actionProgress.productId }}</small>
      <div class="operation-progress-track"><i :style="{ width: `${actionProgressPercent}%` }" /></div>
    </div>

    <div
      class="product-content-grid"
      :class="{ 'logs-collapsed': !operationLogExpanded }"
    >
      <div class="card product-list-card">
        <div class="table-scroll">
          <table class="data-table product-table">
            <colgroup>
              <col style="width: 64px" />
              <col style="width: 465px" />
              <col style="width: 190px" />
              <col style="width: 140px" />
              <col style="width: 130px" />
              <col style="width: 110px" />
              <col style="width: 110px" />
              <col style="width: 110px" />
              <col style="width: 110px" />
              <col style="width: 110px" />
              <col style="width: 100px" />
              <col style="width: 330px" />
            </colgroup>
            <thead>
              <tr>
                <th rowspan="2">选择</th>
                <th rowspan="2"><button class="sort-button" type="button" @click="toggleSort('title')">商品 <span>{{ sortIndicator("title") }}</span></button></th>
                <th rowspan="2"><button class="sort-button" type="button" @click="toggleSort('product_id')">商品 ID <span>{{ sortIndicator("product_id") }}</span></button></th>
                <th rowspan="2"><button class="sort-button" type="button" @click="toggleSort('status_text')">状态 <span>{{ sortIndicator("status_text") }}</span></button></th>
                <th rowspan="2"><button class="sort-button" type="button" @click="toggleSort('min_price')">价格 <span>{{ sortIndicator("min_price") }}</span></button></th>
                <th rowspan="2"><button class="sort-button" type="button" @click="toggleSort('stock_total')">库存 <span>{{ sortIndicator("stock_total") }}</span></button></th>
                <th colspan="3" class="business-metric-head">近30天经营概况</th>
                <th rowspan="2"><button class="sort-button" type="button" @click="toggleSort('total_sales')">总销量 <span>{{ sortIndicator("total_sales") }}</span></button></th>
                <th rowspan="2"><button class="sort-button" type="button" @click="toggleSort('review_count')">评价 <span>{{ sortIndicator("review_count") }}</span></button></th>
                <th rowspan="2">操作</th>
              </tr>
              <tr>
                <th><button class="sort-button" type="button" @click="toggleSort('sold30')">30日销量 <span>{{ sortIndicator("sold30") }}</span></button></th>
                <th><button class="sort-button" type="button" @click="toggleSort('views30')">30日曝光 <span>{{ sortIndicator("views30") }}</span></button></th>
                <th><button class="sort-button" type="button" @click="toggleSort('recommend_views30')">平台推荐 <span>{{ sortIndicator("recommend_views30") }}</span></button></th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="product in pagedProducts"
                :key="`${product.store_id}:${product.product_id}`"
                :class="{ 'focused-product-row': focusedProductId === product.product_id }"
                :data-product-id="product.product_id"
              >
                <td><input type="checkbox" :checked="isSelected(product.product_id)" @change="toggleSelected(product.product_id)" /></td>
                <td class="product-title-cell">
                  <div class="product-title">
                    <span class="product-thumb">
                      <img v-if="product.head_img" :src="product.head_img" alt="" />
                    </span>
                    <span>{{ product.title || "—" }}</span>
                  </div>
                </td>
                <td>{{ product.product_id || "—" }}</td>
                <td><span class="status-chip">{{ product.status_text || "—" }}</span></td>
                <td>{{ moneyOrDash(product.min_price) }}</td>
                <td>{{ productMetricText(product.stock_total) }}</td>
                <td>{{ productMetricText(product.sold30) }}</td>
                <td>{{ productMetricText(product.views30) }}</td>
                <td>{{ productMetricText(product.recommend_views30) }}</td>
                <td>{{ productMetricText(product.total_sales) }}</td>
                <td>{{ productMetricText(product.review_count) }}</td>
                <td class="product-row-actions">
                  <button type="button" :disabled="operationBusy" @click="setSingleListing(product, true)">上架</button>
                  <button type="button" :disabled="operationBusy" @click="setSingleListing(product, false)">下架</button>
                  <button type="button" :disabled="operationBusy || stockLoading" @click="openProductStock(product)">库存</button>
                  <button type="button" :disabled="operationBusy" @click="copyTagLink(product)">短链</button>
                  <button type="button" :disabled="operationBusy" @click="openOfficialProduct(product)">后台</button>
                  <button type="button" :disabled="operationBusy" @click="setCancelAudit(product)">撤回审核</button>
                  <button class="red" type="button" :disabled="operationBusy" @click="setSingleDelete(product)">{{ singleDeleteLabel(product) }}</button>
                </td>
              </tr>
              <tr v-if="!loading && filtered.length === 0">
                <td colspan="12" class="empty-state">请选择店铺后获取商品</td>
              </tr>
            </tbody>
          </table>
        </div>
        <footer class="product-pagination">
          <button class="btn muted" type="button" :disabled="currentPage <= 1" @click="setPage(currentPage - 1)">上一页</button>
          <span>第 {{ currentPage }} / {{ totalPages }} 页，共 {{ filtered.length }} 个商品</span>
          <button class="btn muted" type="button" :disabled="currentPage >= totalPages" @click="setPage(currentPage + 1)">下一页</button>
          <label class="page-size-control">
            每页显示
            <input
              v-model="perPageInput"
              class="input page-size-input"
              type="number"
              min="1"
              max="10000"
              inputmode="numeric"
              @change="applyPerPage"
              @keyup.enter="applyPerPage"
            />
            条
          </label>
          <span>跳转</span>
          <input class="input jump-input" type="number" min="1" :max="totalPages" :value="currentPage" @change="setPage($event.target.value)" />
          <button class="btn muted" type="button" @click="setPage(currentPage)">确定</button>
        </footer>
      </div>

      <aside
        class="card operation-log"
        :class="{ collapsed: !operationLogExpanded }"
        :aria-hidden="operationLogExpanded ? 'false' : 'true'"
      >
        <header>
          <div class="operation-log-heading">
            <strong>操作日志</strong>
            <button
              class="operation-log-collapse"
              type="button"
              title="缩小操作日志面板"
              aria-label="缩小操作日志面板"
              @click="toggleOperationLog"
            >
              <Icon name="chevron" :size="14" />
              <span>缩小</span>
            </button>
          </div>
          <div>
            <select v-model="operationLogFilter" class="select" @change="loadOperationLogs">
              <option value="">全部结果</option>
              <option value="success">成功</option>
              <option value="failed">失败</option>
              <option value="pending">待核实</option>
            </select>
            <button type="button" @click="loadOperationLogs">刷新</button>
          </div>
        </header>
        <div v-if="operationLogs.length" class="operation-log-list">
          <article v-for="entry in operationLogs" :key="entry.id">
            <div class="operation-log-meta">
              <time>{{ entry.time }}</time>
              <span :class="entry.status">{{ productOperationStatusLabel(entry.status) }}</span>
            </div>
            <strong v-if="entry.title || entry.productId">{{ entry.action }} · {{ entry.title || entry.productId }}</strong>
            <p>{{ entry.message }}</p>
            <small v-if="entry.error">{{ entry.error }}</small>
          </article>
        </div>
        <div v-else class="empty-state">暂无操作日志</div>
      </aside>
      <button
        v-if="!operationLogExpanded"
        class="operation-log-expand"
        type="button"
        title="展开操作日志面板"
        aria-label="展开操作日志面板"
        @click="toggleOperationLog"
      >
        <Icon name="chevron" :size="15" />
        <span>日志</span>
      </button>
    </div>

    <ModalDialog
      :open="Boolean(confirmAction)"
      :title="confirmAction?.type === 'delete'
        ? (allSelectedInRecycle ? '彻底删除商品' : '删除商品')
        : (confirmAction?.type === 'cancel_audit'
          ? '批量撤回审核'
          : (confirmAction?.listed ? '批量上架' : '批量下架'))"
      :subtitle="`已选择 ${selectedIds.length} 个商品`"
      width="520px"
      @close="confirmAction = null"
    >
      <p v-if="confirmAction?.type === 'delete' && allSelectedInRecycle">所选商品已在回收站。彻底删除操作不可恢复，将逐件调用官方真实删除并重新读取回收站确认；平台仍存在时不会显示成功。</p>
      <p v-else-if="confirmAction?.type === 'delete'">不在回收站的商品会移入回收站；已在回收站的商品会执行不可恢复的彻底删除。系统将逐件远程复核并记录结果。</p>
      <p v-else-if="confirmAction?.type === 'cancel_audit'">将从微信小店撤回所选商品当前提交中的审核，并记录每个商品的执行结果。</p>
      <p v-else>该操作会调用所选店铺的实时商品接口，并记录执行结果。</p>
      <template #footer>
        <button class="btn" type="button" @click="confirmAction = null">取消</button>
        <button
          class="btn"
          :class="confirmAction?.type === 'delete' ? 'danger' : 'primary'"
          type="button"
          :disabled="operationBusy"
          @click="confirmAction?.type === 'delete'
            ? performDelete()
            : (confirmAction?.type === 'cancel_audit' ? performCancelAudit() : performListing())"
        >{{ confirmAction?.type === 'delete'
          ? (allSelectedInRecycle ? '确认彻底删除' : '确认删除')
          : '确认执行' }}</button>
      </template>
    </ModalDialog>

    <ModalDialog
      :open="stockDialogOpen"
      title="修改商品库存"
      :subtitle="stockDetail ? `${stockDetail.title || stockDetail.product_id} · ${stockDetail.product_id}` : ''"
      width="680px"
      :busy="stockLoading"
      @close="stockDialogOpen = false"
    >
      <div v-if="stockDetail" class="stock-editor">
        <div class="stock-product">
          <img v-if="stockDetail.head_img" :src="stockDetail.head_img" alt="" />
          <div>
            <strong>{{ stockDetail.title || stockDetail.product_id }}</strong>
            <span>{{ stockDetail.status_text || "状态暂无数据" }} · 总库存 {{ stockDetail.stock_total ?? "暂无数据" }}</span>
          </div>
        </div>
        <div class="form-grid">
          <div class="field full">
            <label>SKU</label>
            <select v-model="stockForm.sku_id" class="select">
              <option v-for="sku in stockDetail.skus" :key="sku.sku_id" :value="sku.sku_id">
                {{ sku.sku_name || sku.sku_id }} · 库存 {{ sku.stock_num ?? "暂无数据" }}
              </option>
            </select>
          </div>
          <div class="field">
            <label>修改方式</label>
            <select v-model.number="stockForm.diff_type" class="select">
              <option :value="1">增加库存</option>
              <option :value="2">减少库存</option>
              <option :value="3">设为指定库存</option>
            </select>
          </div>
          <div class="field"><label>数量</label><input v-model.number="stockForm.amount" class="input" type="number" min="0" /></div>
        </div>
        <p class="stock-risk">该操作会真实调用微信小店库存接口。成功后刷新商品列表和操作日志；失败时保留原列表。</p>
      </div>
      <template #footer>
        <button class="btn" type="button" :disabled="stockLoading" @click="stockDialogOpen = false">取消</button>
        <button class="btn primary" type="button" :disabled="stockLoading || !stockForm.sku_id" @click="submitProductStock">{{ stockLoading ? "提交中…" : "确认修改库存" }}</button>
      </template>
    </ModalDialog>
    <div v-if="note" class="toast">{{ note }}</div>
  </section>
</template>

<style scoped>
.product-page {
  height: 100%;
  min-height: 0;
  padding: 20px 0 12px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: #f5f7f9;
}

.product-title-card {
  flex: 0 0 108px;
  min-height: 108px;
  margin: 0 72px 8px 0;
  padding: 0 32px;
  border-radius: 7px;
}

.product-title-card h1 {
  font-size: 24px;
}

.products-filter {
  flex: 0 0 auto;
  padding: 21px 32px 20px;
  margin: 0 72px 8px 0;
  border-radius: 7px;
}

.store-select {
  width: 282px;
  height: 42px;
  font-size: 14px;
}

.status-button {
  min-width: 72px;
  height: 42px;
  padding: 0 15px;
  border: 1px solid #d8e0e7;
  border-radius: 4px;
  color: #526170;
  background: white;
  font-size: 14px;
}

.status-button.active {
  border-color: var(--green);
  color: white;
  background: var(--green);
}

.get-button {
  margin-left: 4px;
  height: 42px;
  padding: 0 18px;
}

.product-search-row {
  margin-top: 18px;
}

.product-count {
  color: #526170;
  font-size: 14px;
  white-space: nowrap;
}

.selected-count {
  color: var(--green);
  font-size: 14px;
  white-space: nowrap;
}

.product-search {
  width: 380px;
  height: 42px;
  margin-left: auto;
}

.price-input {
  width: 126px;
  height: 42px;
}

.product-loading-bar {
  min-height: 38px;
  margin: -4px 0 10px;
  padding: 0 13px;
  display: flex;
  align-items: center;
  gap: 9px;
  border: 1px solid #b8d9ee;
  color: #2d6c91;
  background: #edf8ff;
  font-size: 12px;
}

.product-focus-banner {
  min-height: 38px;
  margin: -4px 0 10px;
  padding: 7px 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border: 1px solid #91cbb0;
  border-radius: 6px;
  color: #1b6849;
  background: #f1fbf5;
  font-size: 12px;
}

.product-progress-count {
  min-width: 76px;
  color: #2677ad;
  font-size: 14px;
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.loading-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: #2f8fd4;
  animation: loading-pulse 0.8s ease-in-out infinite alternate;
}

@keyframes loading-pulse {
  from { opacity: 0.3; transform: scale(0.75); }
  to { opacity: 1; transform: scale(1); }
}

.product-toolbar {
  flex: 0 0 94px;
  min-height: 94px;
  padding: 20px 32px;
  display: flex;
  align-items: center;
  gap: 7px;
  margin: 0 72px 8px 0;
  border-radius: 7px;
}

.product-toolbar .btn {
  height: 42px;
  padding: 0 16px;
  font-size: 14px;
}

.hidden-file-input {
  display: none;
}

.product-toolbar label {
  margin-right: 8px;
  color: #526170;
  font-size: 14px;
}

.product-operation-banner {
  min-height: 46px;
  margin: -4px 72px 8px 0;
  padding: 9px 14px;
  display: grid;
  grid-template-columns: auto auto auto minmax(120px, 1fr);
  align-items: center;
  gap: 10px;
  border: 1px solid #b9d5f7;
  color: #315f8d;
  background: #f4f9ff;
  font-size: 12px;
}

.product-operation-banner.success {
  border-color: #b8dfca;
  color: #236b4c;
  background: #f1fbf6;
}

.product-operation-banner.partial,
.product-operation-banner.failed {
  border-color: #efc4c8;
  color: #a53d47;
  background: #fff5f6;
}

.product-operation-banner small {
  grid-column: 1 / -1;
  color: #70808e;
}

.operation-progress-track {
  height: 5px;
  overflow: hidden;
  border-radius: 99px;
  background: #dce8f2;
}

.operation-progress-track i {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: currentColor;
  transition: width .2s ease;
}

.product-content-grid {
  flex: 1 1 auto;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 426px;
  gap: 8px;
  position: relative;
  transition:
    grid-template-columns 210ms ease,
    gap 210ms ease;
}

.product-content-grid.logs-collapsed {
  grid-template-columns: minmax(0, 1fr) 44px;
  gap: 0;
}

.product-list-card,
.operation-log {
  min-height: 0;
  overflow: hidden;
  border-radius: 7px;
}

.product-list-card {
  display: flex;
  flex-direction: column;
}

.operation-log {
  opacity: 1;
  transform: translateX(0);
  transition:
    opacity 160ms ease,
    transform 210ms ease,
    visibility 210ms step-start;
}

.operation-log.collapsed {
  opacity: 0;
  visibility: hidden;
  pointer-events: none;
  transform: translateX(18px);
  transition:
    opacity 120ms ease,
    transform 180ms ease,
    visibility 180ms step-end;
}

.product-table {
  width: 1969px;
  min-width: 1969px;
  table-layout: fixed;
}

.product-table th {
  position: sticky;
  top: 0;
  z-index: 2;
  height: 58px;
  padding: 0 18px;
  box-sizing: border-box;
  font-size: 14px;
  text-align: center;
  background: #f7f9fa;
}

.product-table thead tr:nth-child(2) th {
  top: 58px;
}

.product-table td {
  height: 78px;
  padding: 7px 18px;
  box-sizing: border-box;
  font-size: 14px;
  line-height: 1.45;
  text-align: center;
}

.product-table tr.focused-product-row td {
  background: #e9f8ef !important;
  box-shadow: inset 0 1px 0 #61b98c, inset 0 -1px 0 #61b98c;
}

.business-metric-head {
  text-align: center;
}

.sort-button {
  width: 100%;
  height: 100%;
  padding: 0;
  border: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 3px;
  color: inherit;
  background: transparent;
  font: inherit;
  cursor: pointer;
}

.sort-button:hover {
  color: #1676bc;
}

.sort-button span {
  min-width: 10px;
  color: #1676bc;
}

.product-list-card > .table-scroll {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
}

.product-title-cell {
  min-width: 420px;
  max-width: 470px;
  white-space: normal;
  text-align: left;
}

.product-title {
  display: flex;
  align-items: center;
  gap: 8px;
  overflow: hidden;
}

.product-thumb {
  width: 56px;
  height: 56px;
  flex: 0 0 56px;
  border-radius: 4px;
  overflow: hidden;
  background: #edf1f4;
}

.product-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.status-chip {
  display: inline-flex;
  min-width: 62px;
  height: 28px;
  align-items: center;
  justify-content: center;
  border: 1px solid #dce3e9;
  border-radius: 4px;
  color: #5c6873;
  background: #f8fafb;
  font-size: 12px;
}

.product-row-actions {
  min-width: 330px;
  white-space: nowrap;
  text-align: center;
}

.product-row-actions button {
  padding: 5px 7px;
  border: 0;
  color: #3370a5;
  background: transparent;
  font-size: 13px;
}

.product-row-actions button.red {
  color: #d45a60;
}

.product-pagination {
  flex: 0 0 70px;
  min-height: 70px;
  padding: 12px 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  border-top: 1px solid var(--line);
  color: #687582;
  font-size: 14px;
}

.jump-input {
  width: 66px;
  height: 38px;
}

.page-size-control {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
}

.page-size-input {
  width: 88px;
  height: 38px;
  text-align: center;
}

.operation-log header {
  height: 58px;
  padding: 0 26px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--line);
}

.operation-log-heading {
  display: flex;
  align-items: center;
  gap: 8px;
}

.operation-log-collapse {
  height: 26px;
  padding: 0 7px;
  display: inline-flex;
  align-items: center;
  gap: 3px;
  border: 1px solid #dfe6eb;
  border-radius: 999px;
  color: #6f7e8b;
  background: #f8fafb;
  cursor: pointer;
  font-size: 10px;
}

.operation-log-collapse svg {
  transform: rotate(-90deg);
}

.operation-log-expand {
  position: absolute;
  z-index: 6;
  top: 50%;
  right: 3px;
  width: 38px;
  height: 66px;
  padding: 7px 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  border: 1px solid #b9d7e9;
  border-right: 0;
  border-radius: 9px 0 0 9px;
  color: #276b91;
  background: #edf8ff;
  box-shadow: -5px 5px 14px rgba(36, 89, 122, 0.12);
  cursor: pointer;
  transform: translateY(-50%);
  font-size: 10px;
}

.operation-log-expand svg {
  transform: rotate(90deg);
}

.operation-log header button {
  border: 0;
  color: #99a4ae;
  background: transparent;
}

.operation-log header > div {
  display: flex;
  align-items: center;
  gap: 7px;
}

.operation-log header .select {
  width: 88px;
  height: 30px;
  font-size: 11px;
}

.operation-log .empty-state {
  min-height: calc(100% - 58px);
}

.operation-log-list {
  height: calc(100% - 58px);
  overflow-y: auto;
  padding: 18px 28px;
}

.operation-log-list article {
  padding: 14px 0 16px;
  border-bottom: 1px dashed #e4e9ed;
}

.operation-log-list time {
  color: #a1abb4;
  font-size: 12px;
}

.operation-log-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.operation-log-meta span {
  padding: 2px 7px;
  border-radius: 99px;
  color: #5c6d7c;
  background: #eef2f5;
  font-size: 10px;
}

.operation-log-meta span.success {
  color: #176543;
  background: #e9f8f0;
}

.operation-log-meta span.failed,
.operation-log-meta span.partial {
  color: #aa3d48;
  background: #fdecf0;
}

.operation-log-meta span.pending {
  color: #926514;
  background: #fff7e2;
}

.operation-log-list article > strong {
  display: block;
  margin-top: 7px;
  color: #2e3e4c;
  font-size: 13px;
}

.operation-log-list p {
  margin: 8px 0 0;
  color: #596774;
  font-size: 14px;
  line-height: 1.65;
}

.operation-log-list article > small {
  display: block;
  margin-top: 6px;
  color: #b14851;
  font-size: 11px;
  line-height: 1.55;
}

.stock-editor {
  display: grid;
  gap: 16px;
}

.stock-product {
  padding: 12px;
  display: flex;
  align-items: center;
  gap: 12px;
  border: 1px solid #e2e8ed;
  background: #f8fafb;
}

.stock-product img {
  width: 58px;
  height: 58px;
  object-fit: cover;
  border-radius: 5px;
}

.stock-product strong,
.stock-product span {
  display: block;
}

.stock-product span {
  margin-top: 5px;
  color: #7a8792;
  font-size: 11px;
}

.stock-risk {
  margin: 0;
  padding: 10px 12px;
  color: #8a641e;
  background: #fff8e8;
  line-height: 1.7;
}
</style>
