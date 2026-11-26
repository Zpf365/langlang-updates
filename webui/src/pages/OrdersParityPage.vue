<script setup>
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  reactive,
  ref,
  watch,
} from "vue";
import { useRoute } from "vue-router";
import { call, formatDateTime } from "../api";
import { useAutoRefresh } from "../autoRefresh";
import { copyText } from "../clipboard";
import Icon from "../components/Icon.vue";
import ModalDialog from "../components/ModalDialog.vue";
import OrderTableRow from "../components/OrderTableRow.vue";
import { hasEntitlement } from "../session";
import { createOrderCountdownClock } from "../orderCountdownClock";
import { beginOrderRefresh, finishOrderRefresh } from "../orderRefreshState";
import {
  countOrdersForTab,
  dateBoundary,
  filterOrdersForTabScope,
  matchesOrderTab,
} from "../orderTabScope";

const route = useRoute();
const orders = ref([]);
const ordersTotal = ref(0);
const ordersTruncated = ref(false);
const stores = ref([]);
const loading = ref(false);
const syncing = ref(false);
const quickRefreshing = ref(false);
const importing = ref(false);
const quickRefresh = reactive({
  visible: false,
  status: "",
  current: 0,
  total: 0,
  succeeded: 0,
  failed: 0,
  updated: 0,
  message: "",
  logs: [],
});
const note = ref("");
let noteTimer = 0;
const selectedOrderIds = ref([]);
const batchMarking = ref(false);
const orderContextMenuElement = ref(null);
const orderContextMenu = reactive({
  open: false,
  x: 0,
  y: 0,
  rawX: 0,
  rawY: 0,
  anchorOrderId: "",
});
const refreshingOrderIds = ref(new Set());
const openingOfficialOrderId = ref("");
const orderTableScroll = ref(null);
const horizontalMax = ref(1);
const horizontalValue = ref(0);
let tableResizeObserver = null;
let horizontalFrame = 0;
const filterOpen = ref(false);
const hiddenProductIds = ref("");
const imagePreview = ref("");
const orderFileInput = ref(null);
const noteDialogOpen = ref(false);
const noteTarget = ref(null);
const noteScope = ref("软件备注");
const noteFlag = ref("");
const noteSyncStore = ref(false);
const noteText = ref("");
const noteSaving = ref(false);
const storeSyncConfirmOpen = ref(false);
const confirmBusy = ref(false);
const confirmDialog = reactive({
  open: false,
  title: "",
  description: "",
  target: "",
  risk: "",
  confirmLabel: "确认",
  action: null,
});
const activeTab = ref("");
const page = ref(1);
const pageSize = 50;
const nowSeconds = ref(Math.floor(Date.now() / 1000));
const countdownClock = createOrderCountdownClock();
countdownClock.subscribe((value) => {
  nowSeconds.value = value;
});
const settingsOpen = ref("");
const giftSettings = reactive({
  gift_auto_reship: true,
  gift_sender: { name: "", phone: "", address: "" },
});
const printMode = ref("代发打单");
const filters = reactive({
  store_id: "",
  search: "",
  start_date: "",
  end_date: "",
  mark: "全部标识",
});
const autoRefresh = useAutoRefresh({
  storageKey: "langlang.auto-refresh.orders",
  refresh: async () => {
    const result = await syncOrders({ automatic: true });
    if (!result.ok) throw new Error(result.message);
    return result;
  },
  isBusy: () => (
    loading.value
    || syncing.value
    || quickRefreshing.value
    || importing.value
    || refreshingOrderIds.value.size > 0
  ),
});

const allTabs = [
  { key: "", label: "全部", tone: "all" },
  { key: "10", label: "待付款", tone: "pending-pay" },
  { key: "12", label: "礼物待收下", tone: "gift" },
  { key: "wait_purchase", label: "待采购", tone: "purchase" },
  { key: "20", label: "待发货", tone: "wait-send" },
  { key: "21", label: "部分发货", tone: "partial-sent" },
  { key: "30", label: "待收货", tone: "sent" },
  { key: "100", label: "已完成", tone: "done" },
  { key: "after_sale", label: "售后订单", tone: "after-sale" },
  { key: "marked", label: "标记", tone: "marked" },
];
const tabs = computed(() => (
  allTabs.filter((tab) => (
    tab.key !== "after_sale"
    || hasEntitlement("module.after_sales")
  ))
));

const tabScopeOrders = computed(() => (
  filterOrdersForTabScope(orders.value, filters, hiddenProductIds.value)
));
const visibleOrders = computed(() => (
  tabScopeOrders.value.filter((order) => matchesOrderTab(order, activeTab.value))
));
const pageCount = computed(() => Math.max(1, Math.ceil(visibleOrders.value.length / pageSize)));
const pagedOrders = computed(() => {
  const start = (page.value - 1) * pageSize;
  return visibleOrders.value.slice(start, start + pageSize);
});
const quickRefreshPercent = computed(() => {
  if (!quickRefresh.total) return quickRefresh.visible ? 6 : 0;
  return Math.max(0, Math.min(100, quickRefresh.current / quickRefresh.total * 100));
});
const allPageSelected = computed(() => (
  pagedOrders.value.length > 0
  && pagedOrders.value.every((order) => selectedOrderIds.value.includes(order.order_id))
));

function appendQuickLog(message, type = "info") {
  quickRefresh.logs.unshift({
    time: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
    type,
    message,
  });
  quickRefresh.logs = quickRefresh.logs.slice(0, 8);
}

function tabCount(key) {
  return countOrdersForTab(tabScopeOrders.value, key);
}

function setSelectedOrder(order, checked) {
  if (checked) {
    selectedOrderIds.value = [...new Set([...selectedOrderIds.value, order.order_id])];
  } else {
    selectedOrderIds.value = selectedOrderIds.value.filter((id) => id !== order.order_id);
  }
}

function togglePageSelection() {
  const ids = pagedOrders.value.map((order) => order.order_id);
  selectedOrderIds.value = allPageSelected.value
    ? selectedOrderIds.value.filter((id) => !ids.includes(id))
    : [...new Set([...selectedOrderIds.value, ...ids])];
}

function closeOrderContextMenu() {
  orderContextMenu.open = false;
}

function positionOrderContextMenu() {
  const menu = orderContextMenuElement.value;
  if (!menu) return;
  const margin = 8;
  const maxX = Math.max(margin, window.innerWidth - menu.offsetWidth - margin);
  const maxY = Math.max(margin, window.innerHeight - menu.offsetHeight - margin);
  orderContextMenu.x = Math.max(margin, Math.min(orderContextMenu.rawX, maxX));
  orderContextMenu.y = Math.max(margin, Math.min(orderContextMenu.rawY, maxY));
}

async function openOrderContextMenu(event, order = null) {
  if (order && !selectedOrderIds.value.includes(order.order_id)) {
    selectedOrderIds.value = [order.order_id];
  }
  Object.assign(orderContextMenu, {
    open: true,
    rawX: Math.max(0, Number(event?.clientX || 0)),
    rawY: Math.max(0, Number(event?.clientY || 0)),
    x: Math.max(0, Number(event?.clientX || 0)),
    y: Math.max(0, Number(event?.clientY || 0)),
    anchorOrderId: String(order?.order_id || ""),
  });
  await nextTick();
  positionOrderContextMenu();
  orderContextMenuElement.value
    ?.querySelector('[role="menuitem"]:not(:disabled)')
    ?.focus();
}

function selectCurrentContextOrder() {
  if (!orderContextMenu.anchorOrderId) return;
  selectedOrderIds.value = [orderContextMenu.anchorOrderId];
}

function selectAllCurrentPageOrders() {
  selectedOrderIds.value = pagedOrders.value.map((order) => order.order_id);
}

function selectAllFilteredOrders() {
  selectedOrderIds.value = visibleOrders.value.map((order) => order.order_id);
}

function invertFilteredOrderSelection() {
  const selected = new Set(selectedOrderIds.value);
  selectedOrderIds.value = visibleOrders.value
    .filter((order) => !selected.has(order.order_id))
    .map((order) => order.order_id);
}

function clearOrderSelection() {
  selectedOrderIds.value = [];
}

function handleOrderContextMenuKeydown(event) {
  if (event.key === "Escape") {
    event.preventDefault();
    closeOrderContextMenu();
    return;
  }
  if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
  const items = [
    ...(orderContextMenuElement.value?.querySelectorAll(
      '[role="menuitem"]:not(:disabled)',
    ) || []),
  ];
  if (!items.length) return;
  event.preventDefault();
  const current = items.indexOf(document.activeElement);
  let target = 0;
  if (event.key === "End") target = items.length - 1;
  else if (event.key === "ArrowUp") {
    target = current <= 0 ? items.length - 1 : current - 1;
  } else if (event.key === "ArrowDown") {
    target = current < 0 || current >= items.length - 1 ? 0 : current + 1;
  }
  items[target].focus();
}

function handleOrderContextPointerDown(event) {
  if (
    orderContextMenu.open
    && !orderContextMenuElement.value?.contains(event.target)
  ) {
    closeOrderContextMenu();
  }
}

function handleOrderContextDocumentKeydown(event) {
  if (orderContextMenu.open && event.key === "Escape") {
    event.preventDefault();
    closeOrderContextMenu();
  }
}

async function refreshSelectedOrders() {
  const selected = orders.value.filter((order) => (
    selectedOrderIds.value.includes(order.order_id)
  ));
  if (!selected.length) {
    note.value = "请先选择需要刷新的订单";
    return;
  }
  let succeeded = 0;
  const errors = [];
  for (const order of selected) {
    if (refreshingOrderIds.value.has(order.order_id)) continue;
    const begin = beginOrderRefresh(
      refreshingOrderIds.value,
      order.order_id,
    );
    if (!begin.started) continue;
    refreshingOrderIds.value = begin.activeIds;
    try {
      await call("orders.refresh-one", {
        store_id: order.store_id,
        order_id: order.order_id,
      });
      succeeded += 1;
    } catch (cause) {
      errors.push(
        `${order.order_id}: ${cause instanceof Error ? cause.message : String(cause)}`,
      );
    } finally {
      refreshingOrderIds.value = finishOrderRefresh(
        refreshingOrderIds.value,
        order.order_id,
      );
    }
  }
  await load();
  note.value = errors.length
    ? `所选订单刷新完成：成功 ${succeeded}，失败 ${errors.length}`
    : `已刷新 ${succeeded} 条所选订单`;
}

async function copySelectedOrderNumbers() {
  const selected = orders.value.filter((order) => (
    selectedOrderIds.value.includes(order.order_id)
  ));
  if (!selected.length) {
    note.value = "请先选择需要复制的订单";
    return;
  }
  try {
    await copyText(
      selected.map((order) => order.order_id).join("\n"),
    );
    note.value = `已复制 ${selected.length} 个完整订单号`;
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
  }
}

async function batchMarkSelected(marked) {
  if (batchMarking.value) return;
  const selected = orders.value.filter((order) => (
    selectedOrderIds.value.includes(order.order_id)
  ));
  if (!selected.length) {
    note.value = "请先选择需要标记的订单";
    return;
  }
  batchMarking.value = true;
  let succeeded = 0;
  const failures = [];
  try {
    for (const order of selected) {
      try {
        await call("orders.mark", {
          order_id: order.order_id,
          marked,
        });
        succeeded += 1;
      } catch (cause) {
        failures.push(
          `${order.order_id}: ${cause instanceof Error ? cause.message : String(cause)}`,
        );
      }
    }
    await load();
    const action = marked ? "批量标记" : "批量取消标记";
    note.value = failures.length
      ? `${action}完成：成功 ${succeeded}，失败 ${failures.length}；${failures[0]}`
      : `${action}完成：成功 ${succeeded}`;
  } finally {
    batchMarking.value = false;
  }
}

function openSelectedPendingOrder() {
  const selected = orders.value.filter((order) => (
    selectedOrderIds.value.includes(order.order_id)
  ));
  const target = (
    selected.find((order) => (
      order.procurement_state === "unlinked"
      || order.procurement_state === "wait_purchase"
      || order.procurement_state === "error"
      || Number(order.status) === 20
    ))
    || selected[0]
  );
  if (!target) {
    note.value = "请先选择需要处理的订单";
    return;
  }
  selectedOrderIds.value = [target.order_id];
  const query = new URLSearchParams({
    store_id: String(target.store_id),
    order_id: String(target.order_id),
    process: "1",
  });
  window.location.hash = `#/orders?${query.toString()}`;
}

async function runOrderContextAction(action) {
  closeOrderContextMenu();
  if (action === "select-current") selectCurrentContextOrder();
  else if (action === "select-page") selectAllCurrentPageOrders();
  else if (action === "select-filtered") selectAllFilteredOrders();
  else if (action === "invert") invertFilteredOrderSelection();
  else if (action === "clear") clearOrderSelection();
  else if (action === "refresh") await refreshSelectedOrders();
  else if (action === "pending") openSelectedPendingOrder();
  else if (action === "copy") await copySelectedOrderNumbers();
  else if (action === "mark") await batchMarkSelected(true);
  else if (action === "unmark") await batchMarkSelected(false);
  else if (action === "export") exportOrders(true);
}

async function load() {
  loading.value = true;
  note.value = "";
  try {
    const [orderData, storeData, filterData, settingsData] = await Promise.all([
      call("orders.list", { limit: 100000 }),
      call("stores.list", {}),
      call("orders.filters.get", {}),
      call("orders.settings.get", {}),
    ]);
    countdownClock.setServerTime(orderData.server_time);
    orders.value = orderData.orders || [];
    ordersTotal.value = Number(orderData.total || orders.value.length);
    ordersTruncated.value = Boolean(orderData.truncated);
    stores.value = storeData.stores || [];
    hiddenProductIds.value = (filterData.hidden_product_ids || []).join("\n");
    const settings = settingsData.settings || {};
    printMode.value = settings.print_mode || "代发打单";
    giftSettings.gift_auto_reship = settings.gift_auto_reship !== false;
    Object.assign(giftSettings.gift_sender, settings.gift_sender || {});
    selectedOrderIds.value = selectedOrderIds.value.filter((id) => (
      orders.value.some((order) => order.order_id === id)
    ));
    if (ordersTruncated.value) {
      note.value = `当前显示 ${orders.value.length} / ${ordersTotal.value} 条订单；超过单次加载范围，请使用店铺或筛选条件缩小后查询。`;
    }
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    loading.value = false;
  }
}

function startOrderCountdownClock() {
  if (document.visibilityState !== "hidden") countdownClock.start();
}

function stopOrderCountdownClock() {
  countdownClock.stop();
}

function handleOrderCountdownVisibility() {
  if (document.visibilityState === "hidden") {
    stopOrderCountdownClock();
  } else {
    startOrderCountdownClock();
  }
}

async function syncOrders({ automatic = false } = {}) {
  if (syncing.value) {
    return { ok: false, message: "订单同步正在进行" };
  }
  syncing.value = true;
  loading.value = true;
  if (!automatic) note.value = "";
  Object.assign(quickRefresh, {
    visible: true,
    status: "starting",
    current: 0,
    total: 0,
    succeeded: 0,
    failed: 0,
    updated: 0,
    message: automatic ? "自动刷新订单…" : "正在同步订单…",
    logs: [],
  });
  try {
    const targetStores = filters.store_id
      ? stores.value.filter((store) => String(store.id) === String(filters.store_id))
      : stores.value;
    const tracked = orders.value.filter((order) => (
      targetStores.some((store) => String(store.id) === String(order.store_id))
    ));
    const idsByStore = {};
    for (const order of tracked) {
      const storeId = String(order.store_id);
      idsByStore[storeId] ||= [];
      if (!idsByStore[storeId].includes(order.order_id)) {
        idsByStore[storeId].push(order.order_id);
      }
    }
    quickRefresh.total = targetStores.length;
    let succeeded = 0;
    const errors = [];
    const statusChanges = [];
    for (let index = 0; index < targetStores.length; index += 1) {
      const store = targetStores[index];
      quickRefresh.current = index;
      quickRefresh.message =
        `正在刷新 ${store.name}（${index + 1}/${targetStores.length}）`;
      appendQuickLog(quickRefresh.message);
      try {
        const result = await call("orders.sync", {
          store_id: store.id,
          current_order_ids: {
            [store.id]: idsByStore[String(store.id)] || [],
          },
        }, { origin: automatic ? "auto_refresh" : "manual" });
        succeeded += Number(result.succeeded || 0);
        statusChanges.push(...(result.status_changes || []));
      } catch (cause) {
        errors.push(`${store.name}: ${cause instanceof Error ? cause.message : String(cause)}`);
      }
      quickRefresh.current = index + 1;
      quickRefresh.succeeded = succeeded;
      quickRefresh.failed = errors.length;
      quickRefresh.status_changes = statusChanges;
      quickRefresh.status = errors.length ? "partial" : "running";
    }
    const syncMessage = errors.length
      ? `已同步 ${succeeded}/${targetStores.length} 家；${errors.slice(0, 2).join("；")}`
      : (
        `已同步 ${succeeded} 家店铺订单`
        + (statusChanges.length ? `，状态变化 ${statusChanges.length} 条` : "")
      );
    if (!automatic) note.value = syncMessage;
    Object.assign(quickRefresh, {
      visible: true,
      status: errors.length ? "partial" : "completed",
      message: syncMessage,
      errors,
      status_changes: statusChanges,
    });
    appendQuickLog(syncMessage, errors.length ? "error" : "info");
    await load();
    return { ok: true, message: syncMessage };
  } catch (cause) {
    const errorMessage = cause instanceof Error ? cause.message : String(cause);
    if (!automatic) note.value = errorMessage;
    return { ok: false, message: errorMessage };
  } finally {
    syncing.value = false;
    loading.value = false;
    window.setTimeout(() => {
      if (!syncing.value && !quickRefreshing.value) quickRefresh.visible = false;
    }, 4000);
  }
}

async function manualSyncOrders() {
  if (!autoRefresh.beginManual()) {
    note.value = "自动刷新正在进行，请等待本轮结束";
    return;
  }
  try {
    await syncOrders();
  } finally {
    autoRefresh.endManual();
  }
}

async function quickRefreshOrders() {
  if (quickRefreshing.value) return;
  if (!autoRefresh.beginManual()) {
    note.value = "自动刷新正在进行，请等待本轮结束";
    return;
  }
  quickRefreshing.value = true;
  Object.assign(quickRefresh, {
    visible: true,
    status: "starting",
    current: 0,
    total: 0,
    succeeded: 0,
    failed: 0,
    updated: 0,
    message: "开始快速刷新最近订单...",
    logs: [],
  });
  appendQuickLog("开始快速刷新最近订单...");
  try {
    const started = await call("orders.quick-refresh.start", {
      store_id: filters.store_id || undefined,
      current_order_ids: Object.fromEntries(
        [...new Set(visibleOrders.value.map((order) => String(order.store_id)))]
          .map((storeId) => [
            storeId,
            visibleOrders.value
              .filter((order) => String(order.store_id) === storeId)
              .map((order) => order.order_id),
          ]),
      ),
    });
    let job = null;
    let lastCurrent = -1;
    while (true) {
      job = await call("orders.quick-refresh.status", { job_id: started.job_id });
      Object.assign(quickRefresh, { ...job, visible: true });
      if (job.current !== lastCurrent && job.message) {
        appendQuickLog(job.message);
        lastCurrent = job.current;
      }
      if (["completed", "partial"].includes(job.status)) break;
      if (job.status === "failed") throw new Error(job.message || "快速刷新失败");
      await new Promise((resolve) => setTimeout(resolve, 160));
    }
    orders.value = job.orders || orders.value;
    note.value = job.message || "快速刷新完成";
    for (const error of (job.errors || []).slice(0, 2)) appendQuickLog(error, "error");
    appendQuickLog(note.value, job.failed ? "error" : "info");
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
    quickRefresh.status = "failed";
    quickRefresh.message = note.value;
    appendQuickLog(note.value, "error");
  } finally {
    quickRefreshing.value = false;
    autoRefresh.endManual();
    window.setTimeout(() => {
      if (!quickRefreshing.value && ["completed", "partial", "failed"].includes(quickRefresh.status)) {
        quickRefresh.visible = false;
      }
    }, 5000);
  }
}

async function refreshOrder(order) {
  if (autoRefresh.running.value) {
    note.value = "自动刷新正在进行，请等待本轮结束";
    return;
  }
  const begin = beginOrderRefresh(refreshingOrderIds.value, order.order_id);
  if (!begin.started) return;
  refreshingOrderIds.value = begin.activeIds;
  note.value = "";
  try {
    const result = await call("orders.refresh-one", {
      store_id: order.store_id,
      order_id: order.order_id,
    });
    await load();
    note.value = (
      `已刷新订单 ${order.order_id}`
      + `；官方旗帜 ${result.platform_detail?.merchant_notes_tag_color ?? 0}`
      + `；包裹 ${result.platform_detail?.packages?.length || 0} 个`
    );
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    refreshingOrderIds.value = finishOrderRefresh(
      refreshingOrderIds.value,
      order.order_id,
    );
  }
}

async function openOfficialOrder(order) {
  if (!order?.order_id || openingOfficialOrderId.value) return;
  openingOfficialOrderId.value = order.order_id;
  note.value = `正在打开官方订单 ${order.order_id}…`;
  try {
    const started = await call("orders.workbench.open-official", {
      store_id: order.store_id,
      order_id: order.order_id,
    });
    let job = null;
    while (true) {
      job = await call("orders.workbench.open-official.status", {
        job_id: started.job_id,
      });
      note.value = job.message || `正在打开微信官方订单 ${order.order_id}…`;
      if (job.status === "completed") break;
      if (job.status === "failed") {
        throw new Error(job.error || job.message || "打开官方订单失败");
      }
      await new Promise((resolve) => setTimeout(resolve, 300));
    }
    note.value = job.message || `微信官方订单 ${order.order_id} 已关闭`;
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    openingOfficialOrderId.value = "";
  }
}

function isRowRefreshing(orderId) {
  return refreshingOrderIds.value.has(orderId);
}

async function toggleMark(order) {
  try {
    const result = await call("orders.mark", {
      order_id: order.order_id,
      marked: !order.is_marked,
    });
    order.is_marked = result.is_marked;
    note.value = result.is_marked ? "订单已标记" : "订单标记已取消";
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
  }
}

function measureHorizontalScroll() {
  const element = orderTableScroll.value;
  if (!element) return;
  horizontalMax.value = Math.max(1, element.scrollWidth - element.clientWidth);
  horizontalValue.value = Math.max(0, Math.min(horizontalMax.value, element.scrollLeft));
}

function scheduleHorizontalSync() {
  if (horizontalFrame) return;
  horizontalFrame = requestAnimationFrame(() => {
    horizontalFrame = 0;
    measureHorizontalScroll();
  });
}

function handleTableScroll() {
  scheduleHorizontalSync();
  closeOrderContextMenu();
}

function moveHorizontal(position) {
  const element = orderTableScroll.value;
  if (!element) return;
  const next = Math.max(0, Math.min(horizontalMax.value, Number(position || 0)));
  element.scrollLeft = next;
  horizontalValue.value = next;
}

function bindTableResizeObserver() {
  tableResizeObserver?.disconnect();
  if (!orderTableScroll.value || typeof ResizeObserver === "undefined") return;
  tableResizeObserver = new ResizeObserver(() => scheduleHorizontalSync());
  tableResizeObserver.observe(orderTableScroll.value);
}

function navigateLogistics() {
  window.location.hash = "#/logistics";
}

function openNoteDialog(order) {
  noteTarget.value = order;
  const hasOfficialState = (
    Boolean(order.merchant_notes)
    || (
      order.official_note_color_code !== null
      && order.official_note_color_code !== undefined
    )
  );
  noteScope.value = hasOfficialState ? "店铺备注" : "软件备注";
  noteFlag.value = order.official_note_color || order.flag || "";
  noteSyncStore.value = false;
  noteText.value = order.merchant_notes || order.software_note || "";
  noteDialogOpen.value = true;
}

function openOrderProduct(order) {
  const productId = String(order?.item_product_id || "").trim();
  if (!productId) {
    note.value = "订单未返回平台商品 ID，无法可靠定位商品";
    return;
  }
  const query = new URLSearchParams({
    store_id: String(order.store_id),
    product_id: productId,
    from_order: String(order.order_id),
  });
  window.location.hash = `#/managed-products?${query.toString()}`;
}

function switchNoteScope(scope) {
  noteScope.value = scope;
  noteText.value = scope === "软件备注"
    ? (noteTarget.value?.software_note || "")
    : (noteTarget.value?.merchant_notes || "");
}

async function saveOrderNoteState() {
  if (!noteTarget.value || noteSaving.value) return;
  noteSaving.value = true;
  try {
    const result = await call("orders.update-note-state", {
      store_id: noteTarget.value.store_id,
      order_id: noteTarget.value.order_id,
      scope: noteScope.value,
      flag: noteFlag.value,
      note: noteText.value,
      sync_store: noteSyncStore.value,
    });
    noteTarget.value.software_note = result.software_note;
    noteTarget.value.flag = result.flag;
    if (result.synced_to_store) {
      noteTarget.value.merchant_notes = result.merchant_notes;
      noteTarget.value.official_note_color = result.flag;
      noteTarget.value.official_note_color_code = result.merchant_notes_tag_color;
      noteTarget.value.official_flag_present = Boolean(result.merchant_notes_tag_color);
    }
    await load();
    noteDialogOpen.value = false;
    note.value = result.synced_to_store
      ? "旗帜、软件备注和店铺备注已保存"
      : "旗帜和软件备注已保存";
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    noteSaving.value = false;
  }
}

function submitOrderNoteState() {
  if (noteScope.value === "店铺备注" || noteSyncStore.value) {
    storeSyncConfirmOpen.value = true;
    return;
  }
  saveOrderNoteState();
}

function confirmStoreNoteSync() {
  storeSyncConfirmOpen.value = false;
  saveOrderNoteState();
}

function askConfirmation({
  title,
  description,
  target = "",
  risk = "",
  confirmLabel = "确认",
  action,
}) {
  Object.assign(confirmDialog, {
    open: true,
    title,
    description,
    target,
    risk,
    confirmLabel,
    action,
  });
}

async function confirmPendingAction() {
  if (confirmBusy.value || !confirmDialog.action) return;
  confirmBusy.value = true;
  try {
    await confirmDialog.action();
  } finally {
    confirmBusy.value = false;
    confirmDialog.open = false;
    confirmDialog.action = null;
  }
}

function closeConfirmation() {
  if (confirmBusy.value) return;
  confirmDialog.open = false;
  confirmDialog.action = null;
}

async function saveVisibleFilters() {
  try {
    const hiddenProductIdsList = hiddenProductIds.value
      .split(/[\s,，;；]+/)
      .map((value) => value.trim())
      .filter(Boolean);
    await call("orders.filters.save", { hidden_product_ids: hiddenProductIdsList });
    filterOpen.value = false;
    note.value = hiddenProductIdsList.length
      ? `已隐藏 ${hiddenProductIdsList.length} 个商品 ID 对应订单`
      : "商品过滤已清空";
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
  }
}

function exportOrders(onlySelected = false) {
  const source = onlySelected
    ? orders.value.filter((order) => selectedOrderIds.value.includes(order.order_id))
    : visibleOrders.value;
  if (!source.length) {
    note.value = "当前没有可导出的订单";
    return;
  }
  const rows = [
    [
      "店铺", "订单号", "状态", "下单时间", "商品", "实收金额",
      "成本", "利润", "采购单号", "物流", "取件码", "备注",
    ],
    ...source.map((order) => [
      order.store_name,
      order.order_id,
      order.status_text,
      formatDateTime(order.create_time),
      order.item_summary,
      Number(order.total_amount || 0) / 100,
      Number(order.cost || 0) / 100,
      Number(order.profit || 0) / 100,
      order.purchase_order_no,
      [order.carrier, order.waybill_id].filter(Boolean).join(" "),
      order.pickup_code,
      order.merchant_notes || order.software_note,
    ]),
  ];
  const csv = rows.map((row) => row.map((value) => (
    `"${String(value ?? "").replaceAll('"', '""')}"`
  )).join(",")).join("\r\n");
  const url = URL.createObjectURL(new Blob([`\ufeff${csv}`], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `澜琅订单_${new Date().toISOString().slice(0, 10)}.csv`;
  link.click();
  URL.revokeObjectURL(url);
  note.value = `已导出 ${source.length} 条订单`;
}

async function refreshSelectedStores() {
  const selected = orders.value.filter((order) => selectedOrderIds.value.includes(order.order_id));
  const storeIds = [...new Set(selected.map((order) => order.store_id))];
  if (!storeIds.length) return;
  if (syncing.value) return;
  syncing.value = true;
  try {
    let succeeded = 0;
    const errors = [];
    for (const storeId of storeIds) {
      note.value = `正在刷新所选订单，店铺 ${succeeded + errors.length + 1}/${storeIds.length}`;
      try {
        await call("orders.sync", { store_id: storeId });
        succeeded += 1;
      } catch (cause) {
        errors.push(cause instanceof Error ? cause.message : String(cause));
      }
    }
    await load();
    note.value = `所选订单刷新完成：成功 ${succeeded}/${storeIds.length}${errors.length ? `，失败 ${errors.length}` : ""}`;
  } finally {
    syncing.value = false;
  }
}

async function savePrintSettings() {
  try {
    await call("orders.settings.save", { settings: { print_mode: printMode.value } });
    settingsOpen.value = "";
    note.value = "打单设置已保存";
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
  }
}

async function saveGiftSettings() {
  try {
    await call("orders.settings.save", {
      settings: {
        gift_auto_reship: giftSettings.gift_auto_reship,
        gift_sender: { ...giftSettings.gift_sender },
      },
    });
    settingsOpen.value = "";
    note.value = "礼品/中转设置已保存";
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
  }
}

function chooseOrderFile() {
  orderFileInput.value?.click();
}

function readFileBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || "");
      resolve(result.includes(",") ? result.split(",", 2)[1] : result);
    };
    reader.onerror = () => reject(reader.error || new Error("文件读取失败"));
    reader.readAsDataURL(file);
  });
}

function firstValue(record, keys) {
  return keys.map((key) => record[key]).find((value) => String(value || "").trim()) || "";
}

async function orderFileSelected(event) {
  const file = event.target.files?.[0];
  event.target.value = "";
  if (!file || importing.value) return;
  importing.value = true;
  note.value = `正在导入订单补充数据：${file.name}`;
  try {
    const parsed = await call("orders.import-file.parse", {
      file_name: file.name,
      content_base64: await readFileBase64(file),
    });
    const records = (parsed.records || []).map((record) => ({
      order_id: firstValue(record, ["订单号", "店铺订单号", "订单编号"]),
      purchase_order_no: firstValue(record, ["采购单号", "1688订单号", "采购订单号"]),
      pickup_code: firstValue(record, ["取件码"]),
      note: firstValue(record, ["备注"]),
      is_marked_sent: ["1", "true", "yes", "是", "已发"].includes(
        String(firstValue(record, ["标记已发", "已发"]) || "").toLowerCase(),
      ) ? 1 : 0,
    })).filter((record) => record.order_id);
    const result = await call("orders.workbench.import", {
      store_id: filters.store_id || undefined,
      records,
    });
    await load();
    note.value = `订单补充数据导入完成：成功 ${result.imported} 条${result.missing?.length ? `，未匹配 ${result.missing.length} 条` : ""}`;
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    importing.value = false;
  }
}

function resetFilters() {
  filters.store_id = "";
  filters.search = "";
  filters.start_date = "";
  filters.end_date = "";
  filters.mark = "全部标识";
  activeTab.value = "";
  page.value = 1;
}

async function applyRoutedOrderTarget() {
  const routedOrderId = String(route.query.order_id || "");
  if (!routedOrderId) return;
  if (route.query.store_id) {
    filters.store_id = String(route.query.store_id);
  }
  activeTab.value = "";
  const index = filterOrdersForTabScope(
    orders.value,
    filters,
    hiddenProductIds.value,
  ).findIndex((order) => String(order.order_id) === routedOrderId);
  page.value = index >= 0 ? Math.floor(index / pageSize) + 1 : 1;
  await nextTick();
}

onMounted(async () => {
  document.addEventListener("visibilitychange", handleOrderCountdownVisibility);
  document.addEventListener("pointerdown", handleOrderContextPointerDown);
  document.addEventListener("keydown", handleOrderContextDocumentKeydown);
  window.addEventListener("resize", closeOrderContextMenu);
  window.addEventListener("blur", closeOrderContextMenu);
  startOrderCountdownClock();
  await load();
  if (String(route.query.after_sales || "") === "1") {
    activeTab.value = "after_sale";
    page.value = 1;
  }
  await applyRoutedOrderTarget();
  await nextTick();
  measureHorizontalScroll();
  bindTableResizeObserver();
  await syncOrders({ automatic: true });
});
watch(activeTab, async (value) => {
  page.value = 1;
  if (value === "marked") {
    await load();
    return;
  }
  if (value !== "after_sale") return;
  const targetStores = filters.store_id
    ? stores.value.filter((store) => String(store.id) === String(filters.store_id))
    : stores.value;
  if (!targetStores.length) return;
  Object.assign(quickRefresh, {
    visible: true,
    status: "running",
    current: 0,
    total: targetStores.length,
    succeeded: 0,
    failed: 0,
    updated: 0,
    message: "正在同步真实售后订单…",
    logs: [],
  });
  let failed = 0;
  for (let index = 0; index < targetStores.length; index += 1) {
    const store = targetStores[index];
    quickRefresh.current = index;
    quickRefresh.message = `正在同步 ${store.name} 售后订单…`;
    try {
      await call("orders.after-sales.sync", { store_id: store.id });
    } catch (cause) {
      failed += 1;
      appendQuickLog(
        `${store.name}: ${cause instanceof Error ? cause.message : String(cause)}`,
        "error",
      );
    }
    quickRefresh.current = index + 1;
    quickRefresh.failed = failed;
  }
  await load();
  quickRefresh.status = failed ? "partial" : "completed";
  quickRefresh.message = failed
    ? `售后订单同步完成，失败 ${failed} 家`
    : "售后订单同步完成";
});
watch(pagedOrders, async () => {
  await nextTick();
  measureHorizontalScroll();
}, { flush: "post" });
watch(() => route.query.after_sales, (value) => {
  if (String(value || "") !== "1") return;
  activeTab.value = "after_sale";
  page.value = 1;
});
watch(
  () => [
    route.query.order_id,
    route.query.store_id,
    route.query.process,
  ],
  async () => {
    await applyRoutedOrderTarget();
  },
);
onBeforeUnmount(() => {
  document.removeEventListener("visibilitychange", handleOrderCountdownVisibility);
  document.removeEventListener("pointerdown", handleOrderContextPointerDown);
  document.removeEventListener("keydown", handleOrderContextDocumentKeydown);
  window.removeEventListener("resize", closeOrderContextMenu);
  window.removeEventListener("blur", closeOrderContextMenu);
  stopOrderCountdownClock();
  tableResizeObserver?.disconnect();
  if (horizontalFrame) cancelAnimationFrame(horizontalFrame);
});
watch(note, (value) => {
  window.clearTimeout(noteTimer);
  if (!value) return;
  const current = value;
  noteTimer = window.setTimeout(() => {
    if (note.value === current) note.value = "";
  }, 6500);
});
</script>

<template>
  <section class="order-page-a">
    <input ref="orderFileInput" class="hidden-file-input" type="file" accept=".xlsx,.csv" @change="orderFileSelected" />
    <header class="a-page-head">
      <div>
        <h1>订单管理</h1>
        <p>按履约阶段组织订单信息，一屏展示更多可用业务数据。</p>
      </div>
      <div class="a-toolbar">
        <button class="btn" type="button" @click="filterOpen = true">过滤订单</button>
        <button class="btn" type="button" @click="exportOrders(false)">导出</button>
        <button class="btn" type="button" :disabled="importing" @click="chooseOrderFile">导入订单信息</button>
        <button class="btn" type="button" disabled title="当前项目没有可调用的信封单供应商接口">发信封单（未接入）</button>
        <button class="btn" type="button" @click="settingsOpen = 'print'">打单设置</button>
        <button class="btn" type="button" @click="settingsOpen = 'gift'">礼品/中转设置</button>
        <button class="btn" type="button" @click="navigateLogistics">监控发货</button>
        <button class="btn primary" type="button" :disabled="syncing" @click="manualSyncOrders">
          <Icon name="refresh" :size="15" :class="{ spinning: syncing }" />
          {{ syncing ? "同步中..." : "同步订单" }}
        </button>
        <button
          class="auto-refresh-toggle"
          :class="{ active: autoRefresh.enabled.value }"
          type="button"
          :title="`${autoRefresh.statusText.value}；点击${autoRefresh.enabled.value ? '关闭' : '开启'}自动刷新`"
          @click="autoRefresh.toggle"
        >
          <span>自动刷新 {{ autoRefresh.enabled.value ? "开" : "关" }}</span>
          <small>{{ autoRefresh.countdownText.value }}</small>
        </button>
      </div>
    </header>

    <nav class="a-tabs">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        type="button"
        :class="{ active: activeTab === tab.key }"
        @click="activeTab = tab.key; page = 1"
      >
        {{ tab.label }}
        <b v-if="tabCount(tab.key)" :class="`tone-${tab.tone}`">{{ tabCount(tab.key) }}</b>
      </button>
    </nav>

    <div class="a-filterbar">
      <label>店铺</label>
      <select v-model="filters.store_id" class="select a-store-select" @change="page = 1">
        <option value="">全部店铺</option>
        <option v-for="store in stores" :key="store.id" :value="store.id">{{ store.name }}</option>
      </select>
      <label>时间</label>
      <input v-model="filters.start_date" class="input a-date" type="date" />
      <span>至</span>
      <input v-model="filters.end_date" class="input a-date" type="date" />
      <select v-model="filters.mark" class="select a-mark">
        <option>全部标识</option><option>已标记</option><option>未标记</option>
      </select>
      <input
        v-model="filters.search"
        class="input a-search"
        placeholder="订单号、采购单号、物流单号；多个完整单号用空格分隔"
      />
      <button class="btn soft" type="button" :disabled="quickRefreshing" @click="quickRefreshOrders">
        {{ quickRefreshing ? "刷新中..." : "快速刷新" }}
      </button>
      <button class="btn" type="button" @click="resetFilters">重置</button>
    </div>

    <div
      v-if="quickRefresh.visible"
      class="refresh-feedback"
      :class="quickRefresh.status"
      role="status"
      aria-live="polite"
    >
      <div class="refresh-track"><span :style="{ width: `${quickRefreshPercent}%` }" /></div>
      <strong>{{ quickRefresh.message }}</strong>
      <span>{{ quickRefresh.current }}/{{ quickRefresh.total }}</span>
      <div class="refresh-logs">
        <small v-for="(entry, index) in quickRefresh.logs.slice(0, 4)" :key="index" :class="entry.type">
          {{ entry.time }} [{{ entry.type === "error" ? "错误" : "信息" }}] {{ entry.message }}
        </small>
      </div>
    </div>

    <div v-if="ordersTruncated" class="data-range-warning">
      当前仅加载 {{ orders.length }} / {{ ordersTotal }} 条订单。请按店铺、时间或关键词缩小范围后再刷新、导出或选择。
    </div>

    <div v-if="selectedOrderIds.length" class="batch-strip">
      <strong>已选 {{ selectedOrderIds.length }} 条订单</strong>
      <button class="btn small" type="button" :disabled="syncing" @click="refreshSelectedStores">刷新所选店铺</button>
      <button class="btn small" type="button" @click="exportOrders(true)">导出所选</button>
      <button class="btn small" type="button" @click="clearOrderSelection">取消选择</button>
    </div>

    <section class="order-table-panel">
      <div
        ref="orderTableScroll"
        class="order-table-scroll"
        @scroll.passive="handleTableScroll"
        @contextmenu.prevent.stop="openOrderContextMenu($event)"
      >
        <table class="order-table-a">
          <thead>
            <tr>
              <th class="col-check sticky-check"><input type="checkbox" :checked="allPageSelected" @change="togglePageSelection" /></th>
              <th class="col-order sticky-order">订单信息</th>
              <th class="col-product">商品</th>
              <th class="col-money">结算 / 利润</th>
              <th class="col-recipient">收货人与地址</th>
              <th class="col-purchase">采购</th>
              <th class="col-fulfill">履约</th>
              <th class="col-logistics">物流</th>
              <th class="col-notes">备注与留言</th>
              <th class="col-action">操作</th>
            </tr>
          </thead>
          <tbody>
            <OrderTableRow
              v-for="order in pagedOrders"
              :key="`${order.store_id}:${order.order_id}`"
              :order="order"
              :now-seconds="nowSeconds"
              :selected="selectedOrderIds.includes(order.order_id)"
              :refreshing="isRowRefreshing(order.order_id)"
              :opening-official="openingOfficialOrderId === order.order_id"
              :auto-process="String(route.query.order_id || '') === order.order_id && String(route.query.process || '') === '1'"
              @select="setSelectedOrder(order, $event)"
              @refresh="refreshOrder(order)"
              @official="openOfficialOrder(order)"
              @mark="toggleMark(order)"
              @note="openNoteDialog(order)"
              @product="openOrderProduct"
              @preview="imagePreview = $event"
              @changed="load"
              @toast="note = $event"
              @contextmenu.prevent.stop="openOrderContextMenu($event, order)"
            />
            <tr v-if="!loading && pagedOrders.length === 0">
              <td colspan="10" class="empty-row">暂无订单数据</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="order-horizontal-bar">
        <span>横向定位</span>
        <button class="btn" type="button" title="滚动到最左侧" @click="moveHorizontal(0)">左端</button>
        <input
          class="order-horizontal-range"
          type="range"
          min="0"
          :max="horizontalMax"
          step="1"
          :value="horizontalValue"
          aria-label="订单表格横向滚动"
          @input="moveHorizontal($event.target.value)"
        />
        <button class="btn" type="button" title="滚动到最右侧" @click="moveHorizontal(horizontalMax)">右端</button>
        <strong>{{ horizontalMax > 1 ? `${Math.round(horizontalValue)} / ${horizontalMax}px` : "当前宽度无需横向滚动" }}</strong>
      </div>
      <footer class="order-pagination">
        <span>共 {{ visibleOrders.length }} 条，第 {{ page }} / {{ pageCount }} 页</span>
        <button class="btn" type="button" :disabled="page <= 1" @click="page -= 1">上一页</button>
        <button class="btn" type="button" :disabled="page >= pageCount" @click="page += 1">下一页</button>
      </footer>
    </section>

    <ModalDialog :open="filterOpen" title="过滤订单" width="560px" @close="filterOpen = false">
      <div class="field">
        <label>需要隐藏的商品 ID（一行一个）</label>
        <textarea v-model="hiddenProductIds" class="input filter-textarea" placeholder="输入商品 ID" />
      </div>
      <template #footer>
        <button class="btn" type="button" @click="hiddenProductIds = ''">清空过滤</button>
        <button class="btn" type="button" @click="filterOpen = false">取消</button>
        <button class="btn primary" type="button" @click="saveVisibleFilters">保存</button>
      </template>
    </ModalDialog>

    <ModalDialog
      :open="confirmDialog.open"
      :title="confirmDialog.title"
      :subtitle="confirmDialog.target"
      width="560px"
      :z-index="130"
      @close="closeConfirmation"
    >
      <div class="confirm-copy">
        <p>{{ confirmDialog.description }}</p>
        <div v-if="confirmDialog.risk"><strong>风险提示</strong><span>{{ confirmDialog.risk }}</span></div>
      </div>
      <template #footer>
        <button class="btn" type="button" :disabled="confirmBusy" @click="closeConfirmation">取消</button>
        <button class="btn primary" type="button" :disabled="confirmBusy" @click="confirmPendingAction">{{ confirmBusy ? "处理中..." : confirmDialog.confirmLabel }}</button>
      </template>
    </ModalDialog>

    <ModalDialog :open="noteDialogOpen" title="标记旗帜与备注" width="620px" @close="noteDialogOpen = false">
      <div class="note-dialog">
        <div class="note-type-row"><strong>备注类型：</strong><label><input v-model="noteScope" type="radio" value="软件备注" @change="switchNoteScope('软件备注')" /> 软件备注</label><label><input v-model="noteScope" type="radio" value="店铺备注" @change="switchNoteScope('店铺备注')" /> 店铺备注</label></div>
        <div class="note-flag-row"><strong>标记：</strong><button type="button" class="note-flag-button none" :class="{ active: noteFlag === '' }" @click="noteFlag = ''">无色</button><button v-for="flag in ['红', '橙', '绿', '蓝']" :key="flag" type="button" class="note-flag-button" :class="[`flag-${flag}`, { active: noteFlag === flag }]" @click="noteFlag = flag">⚑ {{ flag }}</button></div>
        <textarea v-model="noteText" class="input note-textarea" placeholder="填写订单备注" />
        <label class="note-sync-row"><input v-model="noteSyncStore" type="checkbox" /> 同时同步到店铺备注 <small>默认仅保存在澜琅</small></label>
      </div>
      <template #footer>
        <button class="btn" type="button" @click="noteDialogOpen = false">取消</button>
        <button class="btn primary" type="button" :disabled="noteSaving" @click="submitOrderNoteState">{{ noteSaving ? "保存中..." : "保存" }}</button>
      </template>
    </ModalDialog>

    <ModalDialog :open="storeSyncConfirmOpen" title="同步店铺备注" width="500px" @close="storeSyncConfirmOpen = false">
      <p class="dialog-copy">将把当前旗帜和备注写入微信店铺备注，并保存到本机。是否继续？</p>
      <template #footer>
        <button class="btn" type="button" @click="storeSyncConfirmOpen = false">取消</button>
        <button class="btn primary" type="button" @click="confirmStoreNoteSync">确认同步</button>
      </template>
    </ModalDialog>

    <ModalDialog :open="settingsOpen === 'print'" title="打单设置" width="540px" @close="settingsOpen = ''">
      <div class="field"><label>电子面单方式</label><select v-model="printMode" class="select"><option>代发打单</option><option>本地打印</option></select></div>
      <p class="setting-copy">供应商绑定、电子面单账号和模板由物流发货模块管理。</p>
      <template #footer>
        <button class="btn" type="button" @click="settingsOpen = ''">取消</button>
        <button class="btn primary" type="button" @click="savePrintSettings">保存设置</button>
      </template>
    </ModalDialog>

    <ModalDialog :open="settingsOpen === 'gift'" title="礼品 / 中转设置" width="660px" @close="settingsOpen = ''">
      <label class="switch-row"><span>礼品单打印出单后自动补发货</span><input v-model="giftSettings.gift_auto_reship" type="checkbox" /></label>
      <div class="form-grid">
        <div class="field"><label>寄件人姓名</label><input v-model="giftSettings.gift_sender.name" class="input" /></div>
        <div class="field"><label>寄件人电话</label><input v-model="giftSettings.gift_sender.phone" class="input" /></div>
        <div class="field full"><label>寄件地址</label><input v-model="giftSettings.gift_sender.address" class="input" /></div>
      </div>
      <template #footer>
        <button class="btn" type="button" @click="settingsOpen = ''">取消</button>
        <button class="btn primary" type="button" @click="saveGiftSettings">保存设置</button>
      </template>
    </ModalDialog>

    <ModalDialog :open="Boolean(imagePreview)" title="商品图片" width="760px" @close="imagePreview = ''">
      <div class="image-preview"><img v-if="imagePreview" :src="imagePreview" alt="商品大图" /></div>
      <template #footer><button class="btn" type="button" @click="imagePreview = ''">关闭</button></template>
    </ModalDialog>

    <div v-if="note" class="toast">{{ note }}</div>

    <Teleport to="body">
      <div
        v-if="orderContextMenu.open"
        ref="orderContextMenuElement"
        class="order-context-menu"
        role="menu"
        aria-label="订单批量操作菜单"
        :style="{ left: `${orderContextMenu.x}px`, top: `${orderContextMenu.y}px` }"
        tabindex="-1"
        @contextmenu.prevent.stop
        @keydown="handleOrderContextMenuKeydown"
      >
        <header>已选 {{ selectedOrderIds.length }} 条订单</header>
        <button role="menuitem" type="button" @click="runOrderContextAction('select-current')">选择当前项</button>
        <button role="menuitem" type="button" @click="runOrderContextAction('select-page')">全选当前页</button>
        <button role="menuitem" type="button" @click="runOrderContextAction('select-filtered')">全选筛选结果</button>
        <button role="menuitem" type="button" @click="runOrderContextAction('invert')">反选</button>
        <button role="menuitem" type="button" @click="runOrderContextAction('clear')">取消选择</button>
        <hr />
        <button
          role="menuitem"
          type="button"
          :disabled="!selectedOrderIds.length"
          @click="runOrderContextAction('refresh')"
        >刷新所选订单</button>
        <button
          role="menuitem"
          type="button"
          :disabled="!selectedOrderIds.length || batchMarking"
          @click="runOrderContextAction('pending')"
        >打开待处理</button>
        <button
          role="menuitem"
          type="button"
          :disabled="!selectedOrderIds.length"
          @click="runOrderContextAction('copy')"
        >复制订单号</button>
        <button
          role="menuitem"
          type="button"
          :disabled="!selectedOrderIds.length || batchMarking"
          @click="runOrderContextAction('mark')"
        >批量标记</button>
        <button
          role="menuitem"
          type="button"
          :disabled="!selectedOrderIds.length || batchMarking"
          @click="runOrderContextAction('unmark')"
        >批量取消标记</button>
        <button
          role="menuitem"
          type="button"
          :disabled="!selectedOrderIds.length"
          @click="runOrderContextAction('export')"
        >导出所选</button>
      </div>
    </Teleport>
  </section>
</template>

<style scoped>
.order-page-a {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  color: #1b2b3a;
  background: #f3f6f9;
}

.hidden-file-input { display: none; }

.a-page-head {
  min-height: 66px;
  padding: 12px 16px 8px;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
}

.a-page-head h1 { margin: 0; font-size: 20px; }
.a-page-head p { margin: 4px 0 0; color: #6f8091; font-size: 10px; }
.a-toolbar { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 7px; }
.a-toolbar .btn { height: 32px; padding: 0 12px; font-size: 11px; }
.btn.soft { border-color: #c8e8da; color: #126947; background: #ecf8f3; }
.btn.primary { border-color: #16865f; background: #16865f; }

.a-tabs {
  min-height: 39px;
  padding: 3px 12px 6px;
  display: flex;
  align-items: center;
  gap: 6px;
  overflow-x: auto;
}

.a-tabs > button {
  height: 29px;
  padding: 0 12px;
  display: flex;
  align-items: center;
  gap: 6px;
  border: 1px solid #d9e1e8;
  border-radius: 15px;
  color: #506476;
  background: white;
  white-space: nowrap;
  font-size: 11px;
}

.a-tabs > button.active { border-color: #173d5c; color: white; background: #173d5c; }
.a-tabs b { min-width: 18px; padding: 1px 5px; border-radius: 9px; color: white; background: #8292a1; font-size: 9px; text-align: center; }
.a-tabs button.active b { color: #173d5c; background: white; }
.tone-pending-pay { color: #3f2c00 !important; background: #ffd05c !important; }
.tone-purchase { background: #2d8c65 !important; }
.tone-wait-send { color: #164e60 !important; background: #84dff0 !important; }
.tone-after-sale { background: #d94a55 !important; }

.a-filterbar {
  min-height: 47px;
  margin: 0 10px 9px;
  padding: 7px 9px;
  display: flex;
  align-items: center;
  gap: 7px;
  overflow-x: auto;
  border: 1px solid #dce4eb;
  border-radius: 8px;
  background: white;
  white-space: nowrap;
}
.a-filterbar label { color: #66798a; font-size: 10px; }
.a-filterbar .input,
.a-filterbar .select { height: 31px; font-size: 10px; }
.a-store-select { width: 140px; }
.a-date { width: 122px; }
.a-mark { width: 100px; }
.a-search { flex: 1; min-width: 240px; }
.a-filterbar .btn { height: 31px; padding: 0 10px; font-size: 10px; }
.refresh-feedback {
  min-height: 43px;
  margin: 0 10px 9px;
  padding: 7px 10px;
  display: grid;
  grid-template-columns: minmax(100px, 1fr) auto auto;
  align-items: center;
  gap: 9px;
  border: 1px solid #cfe0ef;
  border-radius: 7px;
  color: #526475;
  background: #f7fbff;
  font-size: 10px;
}
.refresh-track { height: 5px; overflow: hidden; border-radius: 9px; background: #e8eff5; }
.refresh-track span { display: block; height: 100%; background: linear-gradient(90deg, #208aef, #17c997); transition: width 140ms ease; }
.refresh-feedback strong { min-width: 180px; font-weight: 500; white-space: nowrap; }
.refresh-feedback > span { min-width: 42px; text-align: right; font-variant-numeric: tabular-nums; }
.refresh-logs { grid-column: 1 / -1; display: flex; gap: 12px; overflow: hidden; }
.refresh-logs small { overflow: hidden; color: #7c8995; text-overflow: ellipsis; white-space: nowrap; }
.refresh-feedback.completed { border-color: #b9e4d0; background: #f4fcf8; }
.refresh-feedback.partial,
.refresh-feedback.failed { border-color: #efc7cb; background: #fff7f7; }
.data-range-warning { margin: 0 10px 8px; padding: 8px 10px; border: 1px solid #f0d7b1; border-radius: 6px; color: #805817; background: #fff9ee; font-size: 10px; }

.batch-strip {
  min-height: 38px;
  margin: 0 10px 8px;
  padding: 5px 9px;
  display: flex;
  align-items: center;
  gap: 8px;
  border: 1px solid #cfe3d9;
  border-radius: 6px;
  background: #f0faf5;
  font-size: 10px;
}
.batch-strip strong { margin-right: auto; color: #176448; }

.order-table-panel {
  min-height: 0;
  flex: 1;
  margin: 0 10px;
  display: flex;
  flex-direction: column;
  border: 1px solid #dce4eb;
  border-radius: 8px;
  background: white;
  box-shadow: 0 8px 24px rgba(23, 44, 66, 0.05);
  overflow: hidden;
}
.order-table-scroll { min-height: 0; flex: 1; overflow: auto; }
.order-table-a { width: 100%; min-width: 1900px; table-layout: fixed; border-collapse: separate; border-spacing: 0; }
.order-table-a thead { background: #f7f9fb; }
.order-table-a th {
  position: sticky;
  top: 0;
  z-index: 5;
  height: 39px;
  padding: 0 9px;
  border-bottom: 1px solid #cfd9e2;
  color: #5d6f80;
  background: #f7f9fb;
  text-align: left;
  font-size: 10px;
  font-weight: 600;
}
.order-table-a th.sticky-check { left: 0; z-index: 7; }
.order-table-a th.sticky-order { left: 38px; z-index: 7; box-shadow: 8px 0 14px -13px rgba(18, 48, 68, 0.75); }
.order-table-a .col-check { width: 38px; text-align: center; }
.order-table-a .col-order { width: 232px; }
.order-table-a .col-product { width: 355px; }
.order-table-a .col-money { width: 140px; }
.order-table-a .col-recipient { width: 220px; }
.order-table-a .col-purchase { width: 238px; }
.order-table-a .col-fulfill { width: 154px; }
.order-table-a .col-logistics { width: 166px; }
.order-table-a .col-notes { width: 230px; }
.order-table-a .col-action { width: 126px; }
.empty-row { height: 280px !important; color: #93a1ad; text-align: center; }

.order-horizontal-bar {
  min-height: 42px;
  padding: 6px 12px;
  display: grid;
  grid-template-columns: auto auto minmax(180px, 1fr) auto auto;
  align-items: center;
  gap: 8px;
  border-top: 1px solid #dfe7ed;
  color: #647687;
  background: #f8fafb;
  font-size: 9px;
}
.order-horizontal-bar .btn { height: 27px; padding: 0 8px; font-size: 9px; }
.order-horizontal-bar strong { min-width: 100px; color: #546879; font-size: 9px; text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.order-horizontal-range { width: 100%; accent-color: #168b61; cursor: ew-resize; }

.order-pagination {
  min-height: 41px;
  padding: 6px 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  border-top: 1px solid #e2e8ee;
  color: #617385;
  background: white;
  font-size: 10px;
}

.filter-textarea { width: 100%; height: 180px; padding: 10px; resize: vertical; }
.note-dialog { display: grid; gap: 12px; }
.note-type-row,
.note-flag-row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; color: #4f6274; font-size: 11px; }
.note-flag-button { min-width: 60px; height: 31px; border: 2px solid transparent; border-radius: 4px; color: white; background: #77838e; font-size: 10px; }
.note-flag-button.none { color: #596775; background: #edf1f4; }
.note-flag-button.flag-红 { background: #fa5151; }
.note-flag-button.flag-橙 { background: #fa9d3b; }
.note-flag-button.flag-绿 { background: #07c160; }
.note-flag-button.flag-蓝 { background: #10aeff; }
.note-flag-button.active { box-shadow: 0 0 0 2px white, 0 0 0 4px #6685a4; }
.note-textarea { width: 100%; height: 175px; padding: 10px; resize: vertical; }
.note-sync-row { display: flex; align-items: center; gap: 6px; color: #4f6274; font-size: 10px; }
.note-sync-row small { margin-left: 5px; color: #8997a4; }
.dialog-copy,
.setting-copy { margin: 0; color: #66798a; font-size: 11px; line-height: 1.7; }
.confirm-copy { display: grid; gap: 12px; color: #4f6375; font-size: 11px; line-height: 1.7; }
.confirm-copy p { margin: 0; }
.confirm-copy > div { padding: 10px 12px; display: grid; gap: 4px; border: 1px solid #f0d7b1; border-radius: 6px; background: #fff9ee; }
.confirm-copy strong { color: #9a620d; }
.confirm-copy span { color: #715a34; }
.switch-row { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; color: #2b7458; font-size: 11px; }
.image-preview { min-height: 420px; display: grid; place-items: center; background: #f5f7f9; }
.image-preview img { max-width: 100%; max-height: 70vh; object-fit: contain; }

.order-context-menu {
  position: fixed;
  z-index: 220;
  width: 190px;
  padding: 5px;
  display: grid;
  gap: 2px;
  border: 1px solid #cfd9e3;
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 14px 38px rgba(21, 43, 63, 0.24);
  outline: none;
}
.order-context-menu header {
  padding: 6px 9px 7px;
  color: #71808e;
  font-size: 9px;
}
.order-context-menu button {
  width: 100%;
  min-height: 29px;
  padding: 0 9px;
  border: 0;
  border-radius: 4px;
  color: #30485a;
  background: transparent;
  font-size: 10px;
  text-align: left;
}
.order-context-menu button:hover:not(:disabled),
.order-context-menu button:focus-visible {
  color: #176447;
  background: #eef8f3;
  outline: none;
}
.order-context-menu button:disabled {
  color: #a4adb5;
  cursor: not-allowed;
}
.order-context-menu hr {
  height: 1px;
  margin: 3px 5px;
  border: 0;
  background: #e5eaef;
}

@media (max-width: 900px) {
  .order-horizontal-bar { grid-template-columns: auto minmax(150px, 1fr) auto; }
  .order-horizontal-bar > span,
  .order-horizontal-bar .btn,
  .order-horizontal-bar strong { display: none; }
}
</style>
