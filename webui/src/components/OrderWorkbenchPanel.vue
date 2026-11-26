<script setup>
import { computed, reactive, ref, watch } from "vue";
import { call, formatDateTime, formatMoney } from "../api";
import Icon from "./Icon.vue";
import ModalDialog from "./ModalDialog.vue";
import ProcurementPage from "../pages/ProcurementPage.vue";
import ProfitReportPanel from "./ProfitReportPanel.vue";

const props = defineProps({
  open: Boolean,
  orders: { type: Array, default: () => [] },
  stores: { type: Array, default: () => [] },
  initialOrderId: { type: String, default: "" },
  initialTab: { type: String, default: "orders" },
  initialAction: { type: String, default: "" },
});
const emit = defineEmits(["close", "changed", "toast"]);

const tabs = [
  { key: "orders", label: "订单管理", icon: "package", detail: "订单核对、采购、备注、发货与售后" },
  { key: "procurement", label: "采购账号", icon: "shopping-cart", detail: "淘宝、1688、拼多多账号与同步" },
  { key: "after_sale", label: "店铺售后", icon: "return", detail: "售后列表、平台操作与自动售后" },
  { key: "transfer", label: "中转售后", icon: "truck", detail: "中转单、工单、招领与理赔" },
  { key: "profit", label: "利润统计", icon: "chart", detail: "成本、佣金、利润与报表" },
  { key: "messages", label: "消息管理", icon: "message", detail: "取件码通知与发送状态" },
  { key: "logs", label: "软件日志", icon: "clipboard", detail: "同步、写操作和异常记录" },
  { key: "settings", label: "设置中心", icon: "settings", detail: "订单、礼品与中转设置" },
];

const activeTab = ref("orders");
const selectedOrderId = ref("");
const detail = ref(null);
const detailLoading = ref(false);
const actionBusy = ref("");
const feedback = reactive({ type: "", message: "" });
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
const orderSearch = ref("");
const settingForms = reactive({
  print_mode: "代发打单",
  gift_auto_reship: true,
  gift_sender: { name: "", phone: "", address: "" },
});

const noteDialogOpen = ref(false);
const noteScope = ref("软件备注");
const noteFlag = ref("");
const noteText = ref("");
const noteSyncStore = ref(false);
const storeSyncConfirmOpen = ref(false);

const manualPurchaseOpen = ref(false);
const manualPurchase = reactive({
  platform: "1688",
  purchase_order_no: "",
  purchase_amount: "",
  commission: "0",
  purchase_mode: "手动下单",
  transfer_warehouse: false,
  flag: "",
  remember: false,
});

const addressOpen = ref(false);
const addressForm = reactive({
  user_name: "",
  tel_number: "",
  province_name: "",
  city_name: "",
  county_name: "",
  street_name: "",
  detail_info: "",
});

const deliveryOpen = ref(false);
const deliveryPackages = ref([{ delivery_id: "", waybill_id: "" }]);
const priceOpen = ref(false);
const priceRows = ref([]);
const changeExpress = ref(false);
const expressFee = ref("0.00");

const afterSales = ref([]);
const selectedAfterSaleId = ref("");
const selectedAfterSaleIds = ref([]);
const afterSaleFilter = ref("全部售后");
const afterSaleNoteOpen = ref(false);
const afterSaleNote = ref("");
const afterSaleFlag = ref("");
const afterSaleDetailOpen = ref(false);
const afterSaleDetailLoading = ref(false);
const afterSaleDetail = ref(null);
const afterSaleProofFiles = ref([]);
const afterSaleMediaIds = ref([]);
const afterSaleMediaBusy = ref(false);
const autoConfigOpen = ref(false);
const autoConfig = reactive({
  enabled: false,
  action: "accept",
  max_amount: "0.00",
  allowed_types: "仅退款,退货退款",
});

const transferCases = ref([]);
const transferSearch = ref("");
const transferCaseType = ref("internal");
const transferDialogOpen = ref(false);
const transferForm = reactive({
  case_id: null,
  store_id: "",
  order_id: "",
  source_order_no: "",
  transfer_order_no: "",
  work_order_no: "",
  tracking_no: "",
  pickup_code: "",
  warehouse: "",
  status: "待处理",
  note: "",
});

const messages = ref([]);
const selectedMessageKeys = ref([]);
const logs = ref([]);
let feedbackTimer = 0;

const filteredOrders = computed(() => {
  const term = orderSearch.value.trim().toLowerCase();
  if (!term) return props.orders;
  return props.orders.filter((order) => [
    order.order_id,
    order.store_name,
    order.item_summary,
    order.buyer_name,
    order.phone,
    order.purchase_order_no,
  ].some((value) => String(value || "").toLowerCase().includes(term)));
});
const selectedSummaryOrder = computed(() => (
  props.orders.find((order) => String(order.order_id) === selectedOrderId.value) || null
));
const selectedAfterSale = computed(() => (
  afterSales.value.find((row) => row.after_sale_order_id === selectedAfterSaleId.value) || null
));
const afterSaleTabs = computed(() => [
  { key: "全部售后", count: afterSales.value.length },
  {
    key: "待处理",
    count: afterSales.value.filter((row) => matchesAfterSaleTab(row, "待处理")).length,
  },
  {
    key: "待商家收货",
    count: afterSales.value.filter((row) => matchesAfterSaleTab(row, "待商家收货")).length,
  },
  {
    key: "仅退款",
    count: afterSales.value.filter((row) => matchesAfterSaleTab(row, "仅退款")).length,
  },
  {
    key: "退货退款",
    count: afterSales.value.filter((row) => matchesAfterSaleTab(row, "退货退款")).length,
  },
  {
    key: "换货",
    count: afterSales.value.filter((row) => matchesAfterSaleTab(row, "换货")).length,
  },
  {
    key: "退款成功",
    count: afterSales.value.filter((row) => matchesAfterSaleTab(row, "退款成功")).length,
  },
  {
    key: "拒绝售后",
    count: afterSales.value.filter((row) => matchesAfterSaleTab(row, "拒绝售后")).length,
  },
  {
    key: "店铺投诉",
    count: afterSales.value.filter((row) => matchesAfterSaleTab(row, "店铺投诉")).length,
  },
  {
    key: "回收站",
    count: afterSales.value.filter((row) => matchesAfterSaleTab(row, "回收站")).length,
  },
]);
const visibleAfterSales = computed(() => (
  afterSales.value.filter((row) => matchesAfterSaleTab(row, afterSaleFilter.value))
));

function matchesAfterSaleTab(row, tab) {
  const text = `${row.status || ""} ${row.status_text || ""} ${row.after_sale_type || ""}`.toUpperCase();
  if (tab === "全部售后") return true;
  if (tab === "待处理") {
    return ["MERCHANT_PROCESSING", "WAIT_PACKAGE_INTERCEPT", "商家受理中", "平台处理中", "待处理"]
      .some((marker) => text.includes(marker.toUpperCase()));
  }
  if (tab === "待商家收货") {
    return text.includes("MERCHANT_WAIT_RECEIPT") || text.includes("待商家收货");
  }
  if (tab === "仅退款") {
    return ["REFUND", "仅退款"].some((marker) => text.includes(marker.toUpperCase()))
      && !matchesAfterSaleTab(row, "退货退款");
  }
  if (tab === "退货退款") {
    return ["RETURN", "退货退款", "退货", "待买家退货"].some((marker) => text.includes(marker.toUpperCase()));
  }
  if (tab === "换货") {
    return ["EXCHANGE", "RESHIP", "换货", "补寄"].some((marker) => text.includes(marker.toUpperCase()));
  }
  if (tab === "退款成功") {
    return ["REFUND_SUCCESS", "RETURN_SUCCESS", "退款完成", "退款成功"].some((marker) => text.includes(marker.toUpperCase()));
  }
  if (tab === "拒绝售后") {
    return ["REJECT", "拒绝"].some((marker) => text.includes(marker.toUpperCase()));
  }
  if (tab === "店铺投诉") {
    return text.includes("投诉") || text.includes("COMPLAINT");
  }
  if (tab === "回收站") {
    return ["CANCEL", "取消", "关闭"].some((marker) => text.includes(marker.toUpperCase()));
  }
  return true;
}

function showFeedback(message, type = "info") {
  window.clearTimeout(feedbackTimer);
  feedback.type = type;
  feedback.message = message;
  emit("toast", message);
  if (type !== "loading") {
    const currentMessage = message;
    feedbackTimer = window.setTimeout(() => {
      if (feedback.message === currentMessage) clearFeedback();
    }, 6000);
  }
}

function clearFeedback() {
  feedback.type = "";
  feedback.message = "";
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

async function run(label, work, successMessage = "") {
  if (actionBusy.value) return null;
  actionBusy.value = label;
  showFeedback(`${label}处理中…`, "loading");
  try {
    const result = await work();
    showFeedback(successMessage || `${label}已完成`, "success");
    emit("changed");
    return result;
  } catch (cause) {
    const message = cause instanceof Error ? cause.message : String(cause);
    showFeedback(message, "error");
    return null;
  } finally {
    actionBusy.value = "";
  }
}

async function loadDetail(orderId = selectedOrderId.value) {
  const order = props.orders.find((item) => String(item.order_id) === String(orderId));
  if (!order) {
    detail.value = null;
    return;
  }
  selectedOrderId.value = String(order.order_id);
  detailLoading.value = true;
  try {
    detail.value = await call("orders.workbench.detail", {
      store_id: order.store_id,
      order_id: order.order_id,
    });
    const rawAddress = detail.value?.order?.address_info || {};
    Object.assign(addressForm, {
      user_name: detail.value?.order?.buyer_name || "",
      tel_number: detail.value?.order?.phone || "",
      province_name: rawAddress.province_name || "",
      city_name: rawAddress.city_name || "",
      county_name: rawAddress.county_name || "",
      street_name: rawAddress.street_name || "",
      detail_info: detail.value?.order?.address || "",
    });
    priceRows.value = (detail.value?.items || []).map((item) => ({
      product_id: item.product_id,
      sku_id: item.sku_id,
      title: item.title,
      price: (Number(item.sale_price || 0) * Number(item.sku_count || 1) / 100).toFixed(2),
    }));
  } catch (cause) {
    detail.value = null;
    showFeedback(cause instanceof Error ? cause.message : String(cause), "error");
  } finally {
    detailLoading.value = false;
  }
}

async function chooseOrder(order) {
  await loadDetail(order.order_id);
}

async function changeTab(key) {
  activeTab.value = key;
  clearFeedback();
  if (key === "orders" && selectedSummaryOrder.value) {
    await loadDetail(selectedSummaryOrder.value.order_id);
  } else if (key === "after_sale") {
    await loadAfterSales();
  } else if (key === "transfer") {
    await loadTransferCases();
  } else if (key === "messages") {
    await loadMessages();
  } else if (key === "logs") {
    await loadLogs();
  } else if (key === "settings") {
    await loadSettings();
  }
}

async function refreshCurrentOrder() {
  const order = selectedSummaryOrder.value;
  if (!order) return;
  const result = await run(
    "刷新订单",
    () => call("orders.sync", { store_id: order.store_id }),
    "订单已从真实后台刷新",
  );
  if (result) await loadDetail(order.order_id);
}

async function toggleMark() {
  const order = detail.value?.order || selectedSummaryOrder.value;
  if (!order) return;
  const marked = !Boolean(order.is_marked);
  const result = await run(
    marked ? "标记订单" : "取消标记",
    () => call("orders.mark", { order_id: order.order_id, marked }),
    marked ? "订单已标记" : "订单标记已取消",
  );
  if (result) await loadDetail(order.order_id);
}

async function copyText(value, successMessage) {
  if (!value) {
    showFeedback("当前没有可复制的内容", "error");
    return;
  }
  await navigator.clipboard.writeText(String(value));
  showFeedback(successMessage, "success");
}

async function copyAddress() {
  const order = detail.value?.order;
  const value = [
    order?.buyer_name,
    order?.phone,
    order?.address,
  ].filter(Boolean).join("，");
  await copyText(value, "收货人、电话和地址已复制");
}

async function decryptAddress() {
  const order = detail.value?.order;
  if (!order) return;
  await run(
    "解密收货信息",
    () => call("orders.decrypt-address", {
      store_id: order.store_id,
      order_id: order.order_id,
    }),
    "收货信息已解密并保存到本机",
  );
  await loadDetail(order.order_id);
}

async function openOfficialOrder() {
  const order = detail.value?.order;
  if (!order) return;
  await run(
    "打开官方订单",
    () => call("orders.workbench.open-official", {
      store_id: order.store_id,
      order_id: order.order_id,
    }),
    "已发起打开微信官方订单详情，浏览器关闭前可继续操作",
  );
}

async function openLegacyWorkbench() {
  const order = detail.value?.order;
  await run(
    "打开原版工作台",
    () => call("workbench.open", {
      store_id: order?.store_id,
      order_id: order?.order_id,
    }),
    "原版订单工作台已打开",
  );
}

function openNoteDialog() {
  const order = detail.value?.order;
  const workbench = detail.value?.workbench || {};
  noteScope.value = order?.merchant_notes ? "店铺备注" : "软件备注";
  noteFlag.value = workbench.flag_color || order?.official_note_color || "";
  noteText.value = noteScope.value === "店铺备注"
    ? (order?.merchant_notes || "")
    : (workbench.note || order?.software_note || "");
  noteSyncStore.value = false;
  noteDialogOpen.value = true;
}

function switchNoteScope(scope) {
  noteScope.value = scope;
  const order = detail.value?.order;
  const workbench = detail.value?.workbench || {};
  noteText.value = scope === "店铺备注"
    ? (order?.merchant_notes || "")
    : (workbench.note || order?.software_note || "");
}

function submitNote() {
  if (noteScope.value === "店铺备注" || noteSyncStore.value) {
    storeSyncConfirmOpen.value = true;
    return;
  }
  saveNote();
}

async function saveNote() {
  storeSyncConfirmOpen.value = false;
  const order = detail.value?.order;
  if (!order) return;
  const result = await run(
    "保存备注",
    () => call("orders.update-note-state", {
      store_id: order.store_id,
      order_id: order.order_id,
      scope: noteScope.value,
      flag: noteFlag.value,
      note: noteText.value,
      sync_store: noteSyncStore.value,
    }),
    "备注与旗帜已保存",
  );
  if (result) {
    noteDialogOpen.value = false;
    await loadDetail(order.order_id);
  }
}

function openAddressDialog() {
  addressOpen.value = true;
}

async function submitAddress() {
  const order = detail.value?.order;
  if (!order) return;
  askConfirmation({
    title: "提交改址申请",
    description: "改址申请会提交到微信小店，最终需要买家同意后才会生效。",
    target: `${order.store_name} · ${order.order_id}`,
    risk: "该操作会写入真实平台申请记录；不会立即修改买家地址。",
    confirmLabel: "提交申请",
    action: async () => {
      const result = await run(
        "提交改址申请",
        () => call("orders.workbench.address-update", {
          store_id: order.store_id,
          order_id: order.order_id,
          address: { ...addressForm },
        }),
        "改址申请已提交",
      );
      if (result) addressOpen.value = false;
    },
  });
}

function openPriceDialog() {
  priceOpen.value = true;
}

async function submitPrice() {
  const order = detail.value?.order;
  if (!order) return;
  askConfirmation({
    title: "修改订单价格",
    description: "修改后的价格会直接提交到微信小店。",
    target: `${order.store_name} · ${order.order_id}`,
    risk: "该操作会改变真实订单金额，请确认商品、数量和价格无误。",
    confirmLabel: "提交改价",
    action: performSubmitPrice,
  });
}

async function performSubmitPrice() {
  const order = detail.value?.order;
  if (!order) return;
  const result = await run(
    "修改价格",
    () => call("orders.workbench.price-update", {
      store_id: order.store_id,
      order_id: order.order_id,
      changes: priceRows.value.map((row) => ({
        product_id: row.product_id,
        sku_id: row.sku_id,
        change_price: Math.round(Number(row.price || 0) * 100),
      })),
      change_express: changeExpress.value,
      express_fee: Math.round(Number(expressFee.value || 0) * 100),
    }),
    "订单价格已提交",
  );
  if (result) priceOpen.value = false;
}

function openManualPurchase() {
  const order = detail.value?.order;
  manualPurchase.purchase_order_no = detail.value?.workbench?.purchase_order_no || "";
  manualPurchase.purchase_amount = detail.value?.workbench?.cost
    ? (Number(detail.value.workbench.cost) / 100).toFixed(2)
    : "";
  manualPurchase.flag = detail.value?.workbench?.flag_color || "";
  manualPurchaseOpen.value = true;
}

async function submitManualPurchase() {
  const order = detail.value?.order;
  if (!order) return;
  if (!manualPurchase.purchase_order_no.trim()) {
    showFeedback("请填写采购订单号", "error");
    return;
  }
  const result = await run(
    "补录采购信息",
    () => call("orders.workbench.manual-purchase", {
      store_id: order.store_id,
      order_id: order.order_id,
      platform: manualPurchase.platform,
      purchase_order_no: manualPurchase.purchase_order_no.trim(),
      purchase_amount_cents: Math.round(Number(manualPurchase.purchase_amount || 0) * 100),
      commission: Number(manualPurchase.commission || 0),
      purchase_mode: manualPurchase.purchase_mode,
      transfer_warehouse: manualPurchase.transfer_warehouse,
      flag: manualPurchase.flag,
      remember: manualPurchase.remember,
      goods_title: order.item_summary,
      goods_count: 1,
    }),
    "采购信息已保存并关联订单",
  );
  if (result) {
    manualPurchaseOpen.value = false;
    await loadDetail(order.order_id);
  }
}

async function preparePurchase() {
  const order = detail.value?.order;
  if (!order) return;
  const result = await run(
    "准备采购",
    () => call("orders.workbench.purchase-focus", {
      store_id: order.store_id,
      order_id: order.order_id,
      product_ids: order.item_product_id ? [order.item_product_id] : [],
      count: 1,
      mode: "manual",
    }),
    "采购信息已准备，可选择采购账号",
  );
  if (result) activeTab.value = "procurement";
}

async function preparePurchaseAddress() {
  const order = detail.value?.order;
  if (!order) return;
  await run(
    "准备采购地址",
    () => call("orders.workbench.purchase-focus", {
      store_id: order.store_id,
      order_id: order.order_id,
      product_ids: order.item_product_id ? [order.item_product_id] : [],
      count: 1,
      mode: "manual",
    }),
    "订单和收货信息已准备",
  );
  const address = [order.buyer_name, order.phone, order.address].filter(Boolean).join("，");
  if (address) await navigator.clipboard.writeText(address);
  activeTab.value = "procurement";
  showFeedback("收货地址已复制，可在采购账号中完成下单", "success");
}

async function browseProcurement() {
  const accounts = detail.value?.procurement_accounts || [];
  const account = accounts.find((item) => item.status === "已登录") || accounts[0];
  if (!account) {
    showFeedback("请先在采购账号中添加并登录采购账号", "error");
    activeTab.value = "procurement";
    return;
  }
  await run(
    "打开采购平台",
    () => call("orders.workbench.browse-procurement", { account_id: account.id }),
    "采购网页已关闭并保存最新采购记录",
  );
  await loadDetail(selectedOrderId.value);
}

async function syncProcurement() {
  const accounts = detail.value?.procurement_accounts || [];
  const account = accounts.find((item) => item.status === "已登录") || accounts[0];
  const order = detail.value?.order;
  if (!account || !order) {
    showFeedback("没有可用的采购账号", "error");
    return;
  }
  await run(
    "获取最新采购",
    () => call("orders.workbench.sync-procurement", {
      account_id: account.id,
      store_id: order.store_id,
      order_id: order.order_id,
    }),
    "采购数据已同步并重新匹配",
  );
  await loadDetail(order.order_id);
}

async function syncProcurementNote() {
  const order = detail.value?.order;
  if (!order) return;
  askConfirmation({
    title: "同步采购备注",
    description: "将把当前订单的采购信息追加到微信店铺备注。",
    target: `${order.store_name} · ${order.order_id}`,
    risk: "该操作会写入真实店铺备注。",
    confirmLabel: "确认同步",
    action: performSyncProcurementNote,
  });
}

async function performSyncProcurementNote() {
  const order = detail.value?.order;
  if (!order) return;
  await run(
    "同步采购备注",
    () => call("orders.workbench.sync-procurement-note", {
      store_id: order.store_id,
      order_id: order.order_id,
    }),
    "采购备注已同步到微信订单",
  );
  await loadDetail(order.order_id);
}

function openDeliveryDialog() {
  const shipment = detail.value?.shipment || {};
  deliveryPackages.value = [{
    delivery_id: shipment.delivery_id || "",
    waybill_id: shipment.waybill_id || "",
  }];
  deliveryOpen.value = true;
}

function addDeliveryPackage() {
  deliveryPackages.value.push({ delivery_id: "", waybill_id: "" });
}

function removeDeliveryPackage(index) {
  if (deliveryPackages.value.length <= 1) return;
  deliveryPackages.value.splice(index, 1);
}

async function submitDelivery() {
  const order = detail.value?.order;
  if (!order) return;
  const packages = deliveryPackages.value.filter((row) => (
    row.delivery_id.trim() && row.waybill_id.trim()
  ));
  if (!packages.length) {
    showFeedback("请填写快递公司编码和运单号", "error");
    return;
  }
  askConfirmation({
    title: "提交订单发货",
    description: "将向微信小店提交当前订单的发货信息。",
    target: `${order.store_name} · ${order.order_id}`,
    risk: "提交后订单将进入已发货流程，快递公司和运单号必须准确。",
    confirmLabel: "确认发货",
    action: async () => {
      const result = await run(
        "提交发货",
        () => call("orders.workbench.delivery-update", {
          store_id: order.store_id,
          order_id: order.order_id,
          packages,
        }),
        "订单已提交发货",
      );
      if (result) {
        deliveryOpen.value = false;
        await loadDetail(order.order_id);
      }
    },
  });
}

async function refreshShippingOptions() {
  const order = detail.value?.order;
  if (!order) return;
  const result = await run(
    "刷新电子面单配置",
    () => call("orders.workbench.shipping-options", {
      store_id: order.store_id,
    }),
    "电子面单账号和模板已刷新",
  );
  if (result) {
    showFeedback(
      `电子面单配置：${result.accounts?.length || 0} 个账号，${result.templates?.length || 0} 个模板`,
      "success",
    );
  }
}

function requestCreateShipment() {
  const order = detail.value?.order;
  if (!order) return;
  askConfirmation({
    title: "生成电子面单",
    description: "将使用当前店铺默认面单账号、模板和发货人信息向平台预取并生成运单。",
    target: `${order.store_name} · ${order.order_id}`,
    risk: "该操作会真实调用平台电子面单接口并产生运单号。",
    confirmLabel: "生成运单",
    action: async () => {
      const result = await run(
        "生成电子面单",
        () => call("orders.workbench.shipment-create", {
          store_id: order.store_id,
          order_id: order.order_id,
        }),
        "电子面单已生成",
      );
      if (result) await loadDetail(order.order_id);
    },
  });
}

function requestCompleteShipment() {
  const order = detail.value?.order;
  if (!order) return;
  askConfirmation({
    title: "完成电子面单发货",
    description: "将确认当前电子面单已打印并把运单号提交到微信小店。",
    target: `${order.store_name} · ${order.order_id}`,
    risk: "提交后订单会进入已发货状态，请先核对快递和运单号。",
    confirmLabel: "确认发货",
    action: async () => {
      const result = await run(
        "完成电子面单发货",
        () => call("orders.workbench.shipment-complete", {
          store_id: order.store_id,
          order_id: order.order_id,
        }),
        "订单已通过电子面单完成发货",
      );
      if (result) await loadDetail(order.order_id);
    },
  });
}

async function loadAfterSales() {
  const selected = selectedSummaryOrder.value;
  const rows = await call("orders.after-sales.list", {
    store_id: selected?.store_id || undefined,
  });
  afterSales.value = rows.after_sales || [];
  if (!selectedAfterSaleId.value && afterSales.value[0]) {
    selectedAfterSaleId.value = afterSales.value[0].after_sale_order_id;
  }
  selectedAfterSaleIds.value = selectedAfterSaleIds.value.filter((id) => (
    afterSales.value.some((row) => row.after_sale_order_id === id)
  ));
}

async function openAfterSaleTab(afterSaleId = "") {
  if (afterSaleId) {
    selectedAfterSaleId.value = afterSaleId;
    selectedAfterSaleIds.value = [afterSaleId];
  }
  activeTab.value = "after_sale";
  await loadAfterSales();
}

function selectAfterSale(row) {
  selectedAfterSaleId.value = row.after_sale_order_id;
}

function toggleAfterSale(row) {
  const id = row.after_sale_order_id;
  selectedAfterSaleIds.value = selectedAfterSaleIds.value.includes(id)
    ? selectedAfterSaleIds.value.filter((value) => value !== id)
    : [...selectedAfterSaleIds.value, id];
}

async function openAfterSaleDetail(row) {
  if (!row) return;
  selectedAfterSaleId.value = row.after_sale_order_id;
  afterSaleDetailOpen.value = true;
  afterSaleDetailLoading.value = true;
  afterSaleDetail.value = null;
  afterSaleProofFiles.value = [];
  afterSaleMediaIds.value = [];
  try {
    afterSaleDetail.value = await call("orders.after-sales.detail", {
      store_id: row.store_id,
      after_sale_id: row.after_sale_order_id,
    });
  } catch (cause) {
    showFeedback(cause instanceof Error ? cause.message : String(cause), "error");
  } finally {
    afterSaleDetailLoading.value = false;
  }
}

function selectAfterSaleProofFiles(event) {
  afterSaleProofFiles.value = Array.from(event.target.files || []).slice(0, 9);
}

async function uploadAfterSaleProofs() {
  const row = selectedAfterSale.value;
  if (!row || !afterSaleProofFiles.value.length || afterSaleMediaBusy.value) {
    if (!afterSaleProofFiles.value.length) {
      showFeedback("请先选择售后凭证图片", "error");
    }
    return;
  }
  afterSaleMediaBusy.value = true;
  try {
    const files = await Promise.all(afterSaleProofFiles.value.map(async (file) => {
      const bytes = new Uint8Array(await file.arrayBuffer());
      let binary = "";
      for (let offset = 0; offset < bytes.length; offset += 0x8000) {
        binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
      }
      return { name: file.name, content_base64: btoa(binary) };
    }));
    const result = await call("orders.after-sales.upload-media", {
      store_id: row.store_id,
      after_sale_id: row.after_sale_order_id,
      files,
    });
    afterSaleMediaIds.value = result.media_ids || [];
    afterSaleProofFiles.value = [];
    showFeedback(`售后凭证上传完成：${result.count || 0} 张`, "success");
  } catch (cause) {
    showFeedback(cause instanceof Error ? cause.message : String(cause), "error");
  } finally {
    afterSaleMediaBusy.value = false;
  }
}

async function syncAfterSales() {
  await run(
    "同步售后列表",
    () => call("orders.after-sales.sync", {
      store_id: selectedSummaryOrder.value?.store_id || undefined,
    }),
    "售后列表已同步",
  );
  await loadAfterSales();
}

async function applyAfterSale(operation, label) {
  const row = selectedAfterSale.value;
  if (!row) {
    showFeedback("请先选择一条售后单", "error");
    return;
  }
  askConfirmation({
    title: label,
    description: `即将对售后单 ${row.after_sale_order_id} 执行“${label}”。`,
    target: `${row.store_name || row.store_id} · ${row.order_id || "订单暂无数据"}`,
    risk: "该操作会真实修改微信平台售后状态。",
    confirmLabel: `确认${label}`,
    action: async () => {
      await run(
        label,
        () => call("orders.after-sales.action", {
          store_id: row.store_id,
          after_sale_id: row.after_sale_order_id,
          operation,
        }),
        `${label}已提交`,
      );
      await loadAfterSales();
    },
  });
}

async function removeSelectedAfterSales() {
  const rows = afterSales.value.filter((row) => (
    selectedAfterSaleIds.value.includes(row.after_sale_order_id)
  ));
  if (!rows.length) {
    showFeedback("请先勾选需要移除的售后本地记录", "error");
    return;
  }
  askConfirmation({
    title: "移除售后本地缓存",
    description: `将移除 ${rows.length} 条本地售后记录。`,
    target: rows.slice(0, 3).map((row) => row.after_sale_order_id).join("、"),
    risk: "该操作不会修改微信平台售后状态，但本地记录移除后需要重新同步才能恢复。",
    confirmLabel: "确认移除",
    action: performRemoveSelectedAfterSales,
  });
}

async function performRemoveSelectedAfterSales() {
  const rows = afterSales.value.filter((row) => (
    selectedAfterSaleIds.value.includes(row.after_sale_order_id)
  ));
  await run(
    "移除售后缓存",
    () => call("orders.after-sales.remove", {
      rows: rows.map((row) => ({
        store_id: row.store_id,
        after_sale_order_id: row.after_sale_order_id,
      })),
    }),
    "本地售后缓存已移除",
  );
  await loadAfterSales();
}

function openAfterSaleNote() {
  if (!selectedAfterSaleIds.value.length) {
    showFeedback("请先勾选需要备注的售后记录", "error");
    return;
  }
  afterSaleNote.value = "";
  afterSaleFlag.value = "";
  afterSaleNoteOpen.value = true;
}

async function saveAfterSaleNote() {
  const rows = afterSales.value.filter((row) => (
    selectedAfterSaleIds.value.includes(row.after_sale_order_id)
  ));
  if (!rows.length) return;
  let succeeded = 0;
  const errors = [];
  for (const row of rows) {
    try {
      await call("orders.workbench.local-update", {
        store_id: row.store_id,
        order_id: row.order_id,
        note: afterSaleNote.value,
        flag_color: afterSaleFlag.value,
      });
      succeeded += 1;
    } catch (cause) {
      errors.push(cause instanceof Error ? cause.message : String(cause));
    }
  }
  afterSaleNoteOpen.value = false;
  showFeedback(
    `售后备注完成：成功 ${succeeded}，失败 ${errors.length}`,
    errors.length ? "error" : "success",
  );
}

async function openAutoConfig() {
  const result = await call("orders.after-sales.auto-config.get", {});
  const config = result.config || {};
  Object.assign(autoConfig, {
    enabled: Boolean(config.enabled),
    action: config.action || "accept",
    max_amount: (Number(config.max_amount || 0) / 100).toFixed(2),
    allowed_types: config.allowed_types || "仅退款,退货退款",
  });
  autoConfigOpen.value = true;
}

async function saveAutoConfig() {
  const result = await run(
    "保存自动售后设置",
    () => call("orders.after-sales.auto-config.save", {
      config: {
        enabled: autoConfig.enabled,
        action: autoConfig.action,
        max_amount: Math.round(Number(autoConfig.max_amount || 0) * 100),
        allowed_types: autoConfig.allowed_types,
      },
    }),
    "自动售后设置已保存",
  );
  if (result) autoConfigOpen.value = false;
}

async function startAutoAfterSale() {
  askConfirmation({
    title: "开始自动售后",
    description: "将按已保存规则处理符合条件的待处理售后单。",
    target: selectedSummaryOrder.value
      ? `${selectedSummaryOrder.value.store_name} · 当前店铺`
      : "全部店铺",
    risk: "该操作会真实修改微信平台售后状态；金额超限、类型不符或状态不明的记录会跳过。",
    confirmLabel: "开始执行",
    action: performStartAutoAfterSale,
  });
}

async function performStartAutoAfterSale() {
  const result = await run(
    "开始自动售后",
    () => call("orders.after-sales.auto-start", {
      store_id: selectedSummaryOrder.value?.store_id || undefined,
    }),
    "自动售后执行完成",
  );
  if (result) await loadAfterSales();
}

async function loadTransferCases() {
  const result = await call("orders.transfer-cases.list", {
    case_type: transferCaseType.value || undefined,
    search: transferSearch.value,
  });
  transferCases.value = result.cases || [];
}

function openTransferDialog(row = null) {
  Object.assign(transferForm, {
    case_id: row?.id || null,
    store_id: row?.store_id || selectedSummaryOrder.value?.store_id || "",
    order_id: row?.order_id || selectedSummaryOrder.value?.order_id || "",
    source_order_no: row?.source_order_no || "",
    transfer_order_no: row?.transfer_order_no || "",
    work_order_no: row?.work_order_no || "",
    tracking_no: row?.tracking_no || "",
    pickup_code: row?.pickup_code || "",
    warehouse: row?.warehouse || "",
    status: row?.status_text || row?.status || "待处理",
    note: row?.note || "",
  });
  transferDialogOpen.value = true;
}

function openTransferType(type) {
  transferCaseType.value = type;
  openTransferDialog();
}

async function saveTransferCase() {
  const action = transferForm.case_id
    ? "orders.transfer-cases.update"
    : "orders.transfer-cases.create";
  const result = await run(
    transferForm.case_id ? "更新中转工单" : "创建中转工单",
    () => call(action, {
      ...transferForm,
      case_type: transferCaseType.value,
      status_text: transferForm.status,
    }),
    transferForm.case_id ? "中转工单已更新" : "中转工单已创建",
  );
  if (result) {
    transferDialogOpen.value = false;
    await loadTransferCases();
  }
}

async function loadMessages() {
  const result = await call("orders.workbench.messages", {});
  messages.value = result.messages || [];
  selectedMessageKeys.value = [];
}

function messageKey(row) {
  return `${row.platform}:${row.external_order_id}:${row.pickup_code}`;
}

function toggleMessage(row) {
  const key = messageKey(row);
  selectedMessageKeys.value = selectedMessageKeys.value.includes(key)
    ? selectedMessageKeys.value.filter((item) => item !== key)
    : [...selectedMessageKeys.value, key];
}

async function copyPickupCodes() {
  const rows = messages.value.filter((row) => selectedMessageKeys.value.includes(messageKey(row)));
  if (!rows.length) {
    showFeedback("请先选择带取件码的消息", "error");
    return;
  }
  await navigator.clipboard.writeText(rows.map((row) => row.pickup_code).join("\n"));
  await run(
    "记录发送状态",
    () => call("orders.workbench.mark-messages-sent", {
      links: rows.flatMap((row) => row.links || []),
    }),
    `已复制 ${rows.length} 条取件码并记录发送状态`,
  );
  await loadMessages();
}

async function loadLogs() {
  const result = await call("orders.workbench.logs", { limit: 500 });
  logs.value = result.logs || [];
}

async function loadSettings() {
  const result = await call("orders.settings.get", {});
  const settings = result.settings || {};
  settingForms.print_mode = settings.print_mode || "代发打单";
  settingForms.gift_auto_reship = settings.gift_auto_reship !== false;
  Object.assign(settingForms.gift_sender, settings.gift_sender || {});
}

async function saveWorkbenchSettings() {
  await run(
    "保存设置",
    () => call("orders.settings.save", {
      settings: {
        print_mode: settingForms.print_mode,
        gift_auto_reship: settingForms.gift_auto_reship,
        gift_sender: { ...settingForms.gift_sender },
      },
    }),
    "订单工作台设置已保存",
  );
}

function exportAfterSales() {
  if (!afterSales.value.length) {
    showFeedback("当前没有可导出的售后数据", "error");
    return;
  }
  const rows = [
    ["店铺", "订单号", "售后单号", "状态", "类型", "原因", "退款金额", "更新时间"],
    ...afterSales.value.map((row) => [
      row.store_name,
      row.order_id,
      row.after_sale_order_id,
      row.status_text,
      row.after_sale_type,
      row.reason_text,
      Number(row.refund_amount || 0) / 100,
      formatDateTime(row.update_time),
    ]),
  ];
  const csv = rows.map((row) => row.map((value) => (
    `"${String(value ?? "").replaceAll('"', '""')}"`
  )).join(",")).join("\r\n");
  const url = URL.createObjectURL(new Blob([`\ufeff${csv}`], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `店铺售后_${new Date().toISOString().slice(0, 10)}.csv`;
  link.click();
  URL.revokeObjectURL(url);
  showFeedback("售后数据已导出", "success");
}

function close() {
  activeTab.value = "orders";
  detail.value = null;
  clearFeedback();
  emit("close");
}

watch(
  () => props.open,
  async (open) => {
    if (!open) return;
    activeTab.value = tabs.some((tab) => tab.key === props.initialTab)
      ? props.initialTab
      : "orders";
    const order = props.orders.find((item) => (
      String(item.order_id) === String(props.initialOrderId)
    )) || props.orders[0];
    if (order) await loadDetail(order.order_id);
    if (activeTab.value === "transfer") await loadTransferCases();
    if (props.initialAction === "manual-purchase" && detail.value) {
      openManualPurchase();
    }
  },
);
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="workbench-overlay">
      <section class="workbench-shell">
        <header class="workbench-titlebar">
          <div>
            <strong>澜琅 · 订单工作台</strong>
            <span>真实订单、采购、物流、售后与本地经营数据</span>
          </div>
          <button type="button" class="workbench-close" @click="close">
            <Icon name="close" :size="20" />
          </button>
        </header>

        <div class="workbench-layout">
          <aside class="workbench-nav">
            <button
              v-for="tab in tabs"
              :key="tab.key"
              type="button"
              :class="{ active: activeTab === tab.key }"
              @click="changeTab(tab.key)"
            >
              <Icon :name="tab.icon" :size="18" />
              <span><strong>{{ tab.label }}</strong><small>{{ tab.detail }}</small></span>
            </button>
            <div class="workbench-nav-spacer" />
            <div v-if="selectedSummaryOrder" class="workbench-selected">
              <small>当前订单</small>
              <strong>{{ selectedSummaryOrder.store_name }}</strong>
              <span>{{ selectedSummaryOrder.order_id }}</span>
            </div>
          </aside>

          <main class="workbench-content">
            <section v-if="activeTab === 'orders'" class="wb-orders-tab">
              <div class="wb-order-queue">
                <div class="wb-panel-head">
                  <strong>订单队列</strong>
                  <span>{{ filteredOrders.length }} 单</span>
                </div>
                <input v-model="orderSearch" class="input" placeholder="搜索店铺、订单、商品、买家、采购号" />
                <div class="wb-queue-list">
                  <button
                    v-for="order in filteredOrders"
                    :key="`${order.store_id}:${order.order_id}`"
                    type="button"
                    class="wb-queue-item"
                    :class="{ active: String(order.order_id) === selectedOrderId }"
                    @click="chooseOrder(order)"
                  >
                    <img v-if="order.item_thumb_url" :src="order.item_thumb_url" alt="" />
                    <span v-else class="wb-thumb-empty">无图</span>
                    <span class="wb-queue-copy">
                      <strong>{{ order.store_name }}</strong>
                      <em>{{ order.status_text }}</em>
                      <b>{{ order.item_summary || "暂无商品信息" }}</b>
                      <small>{{ formatDateTime(order.create_time) }} · {{ order.order_id }}</small>
                    </span>
                    <strong class="wb-queue-money">{{ formatMoney(order.total_amount) }}</strong>
                  </button>
                  <div v-if="!filteredOrders.length" class="wb-empty">暂无订单数据</div>
                </div>
              </div>

              <div class="wb-order-detail">
                <div v-if="detailLoading" class="wb-loading">正在读取订单工作台数据…</div>
                <template v-else-if="detail">
                  <div class="wb-detail-header">
                    <div>
                      <span class="status-chip">{{ detail.order.status_text }}</span>
                      <h2>{{ detail.order.item_summary || "暂无商品信息" }}</h2>
                      <p>{{ detail.order.store_name }} · {{ formatDateTime(detail.order.create_time) }} · {{ detail.order.order_id }}</p>
                    </div>
                    <div class="wb-detail-amount">
                      <small>实收金额</small>
                      <strong>{{ formatMoney(detail.order.total_amount) }}</strong>
                    </div>
                  </div>

                  <div class="wb-metrics">
                    <div><span>成本</span><strong>{{ detail.workbench.cost ? formatMoney(detail.workbench.cost) : "暂无数据" }}</strong></div>
                    <div><span>利润</span><strong>{{ detail.workbench.profit ? formatMoney(detail.workbench.profit) : "利润待核算" }}</strong></div>
                    <div><span>采购单</span><strong>{{ detail.workbench.purchase_order_no || "未关联" }}</strong></div>
                    <div><span>取件码</span><strong>{{ detail.workbench.pickup_code || "暂无数据" }}</strong></div>
                  </div>

                  <div class="wb-action-strip">
                    <button class="btn" type="button" :disabled="Boolean(actionBusy)" @click="refreshCurrentOrder"><Icon name="refresh" :size="15" :class="{ spinning: actionBusy === '刷新订单' }" /> {{ actionBusy === "刷新订单" ? "刷新中..." : "刷新订单" }}</button>
                    <button class="btn" type="button" @click="openOfficialOrder">官方订单</button>
                    <button class="btn" type="button" @click="copyText(detail.order.order_id, '订单号已复制')">复制订单号</button>
                    <button class="btn" type="button" @click="toggleMark">{{ detail.order.is_marked ? "取消标记" : "标记订单" }}</button>
                    <button class="btn" type="button" @click="openNoteDialog">备注/旗帜</button>
                    <button class="btn" type="button" @click="openAddressDialog">申请改址</button>
                    <button class="btn" type="button" @click="openPriceDialog">修改价格</button>
                    <button class="btn" type="button" @click="openLegacyWorkbench">原版工作台</button>
                  </div>

                  <div class="wb-detail-grid">
                    <section class="wb-card">
                      <header><strong>收货信息</strong><button class="mini-link" type="button" @click="copyAddress">复制</button></header>
                      <dl>
                        <dt>收货人</dt><dd>{{ detail.order.buyer_name || "暂无数据" }}</dd>
                        <dt>电话</dt><dd>{{ detail.order.phone || "暂无数据" }}</dd>
                        <dt>地址</dt><dd>{{ detail.order.address || "暂无数据" }}</dd>
                      </dl>
                      <div class="wb-card-actions">
                        <button class="btn" type="button" @click="decryptAddress">解密收货信息</button>
                        <button class="btn" type="button" @click="copyAddress">复制完整地址</button>
                        <button class="btn" type="button" @click="preparePurchaseAddress">添加地址到采购</button>
                      </div>
                    </section>

                    <section class="wb-card">
                      <header><strong>采购信息</strong><span>{{ detail.procurement_links.length }} 条关联</span></header>
                      <div v-if="detail.procurement_links.length" class="wb-link-list">
                        <div v-for="link in detail.procurement_links" :key="link.id">
                          <strong>{{ link.platform }} · {{ link.external_order_id }}</strong>
                          <span>{{ link.account_label || "采购账号暂无数据" }}</span>
                          <span>物流 {{ link.mail_no || "暂无数据" }} · 取件码 {{ link.pickup_code || "暂无数据" }}</span>
                          <span>{{ link.status_text || "状态暂无数据" }}</span>
                        </div>
                      </div>
                      <div v-else class="wb-empty compact">暂无采购关联</div>
                      <div class="wb-card-actions">
                        <button class="btn" type="button" @click="openManualPurchase">手动添加/修改关联</button>
                        <button class="btn primary" type="button" @click="preparePurchase">立即采购</button>
                        <button class="btn" type="button" @click="browseProcurement">打开采购平台</button>
                        <button class="btn" type="button" @click="syncProcurement">获取最新</button>
                        <button class="btn" type="button" @click="syncProcurementNote">同步采购备注</button>
                      </div>
                    </section>

                    <section class="wb-card">
                      <header><strong>物流与发货</strong><span>{{ detail.shipment.waybill_id ? "已生成运单" : "暂无运单" }}</span></header>
                      <dl>
                        <dt>快递</dt><dd>{{ detail.shipment.delivery_id || detail.order.carrier || "暂无数据" }}</dd>
                        <dt>运单号</dt><dd>{{ detail.shipment.waybill_id || detail.order.waybill_id || "暂无数据" }}</dd>
                        <dt>物流状态</dt><dd>{{ detail.shipment.status || detail.order.status_text || "暂无数据" }}</dd>
                      </dl>
                      <div class="wb-card-actions">
                        <button class="btn" type="button" @click="refreshShippingOptions">刷新电子面单</button>
                        <button class="btn" type="button" @click="requestCreateShipment">生成电子面单</button>
                        <button class="btn blue" type="button" :disabled="!detail.shipment.waybill_id" :title="detail.shipment.waybill_id ? '提交电子面单发货' : '请先生成电子面单'" @click="requestCompleteShipment">完成发货</button>
                        <button class="btn primary" type="button" @click="openDeliveryDialog">发货/修改物流</button>
                        <button class="btn" type="button" @click="activeTab = 'transfer'">创建中转工单</button>
                      </div>
                    </section>

                    <section class="wb-card">
                      <header><strong>售后与异常</strong><span>{{ detail.after_sales.length }} 条</span></header>
                      <div v-if="detail.after_sales.length" class="wb-after-list">
                        <button
                          v-for="row in detail.after_sales"
                          :key="row.after_sale_order_id"
                          type="button"
                          @click="openAfterSaleTab(row.after_sale_order_id)"
                        >
                          <strong>{{ row.status_text || "状态暂无数据" }}</strong>
                          <span>{{ row.after_sale_type || "售后类型暂无数据" }} · {{ row.reason_text || "原因暂无数据" }}</span>
                          <em>{{ formatMoney(row.refund_amount) }}</em>
                        </button>
                      </div>
                      <div v-else class="wb-empty compact">当前订单暂无售后记录</div>
                      <div class="wb-card-actions">
                        <button class="btn" type="button" @click="openAfterSaleTab()">进入售后工作台</button>
                      </div>
                    </section>
                  </div>
                </template>
                <div v-else class="wb-empty">请选择一条订单查看工作台</div>
              </div>
            </section>

            <section v-else-if="activeTab === 'procurement'" class="wb-embedded-page">
              <ProcurementPage />
            </section>

            <section v-else-if="activeTab === 'profit'" class="wb-embedded-page">
              <ProfitReportPanel />
            </section>

            <section v-else-if="activeTab === 'after_sale'" class="wb-table-tab">
              <header class="wb-section-head">
                <div><h2>店铺售后</h2><p>真实售后记录与平台处理动作</p></div>
                <div class="wb-head-actions">
                  <button class="btn" type="button" :disabled="Boolean(actionBusy)" @click="syncAfterSales">{{ actionBusy === "同步售后列表" ? "同步中..." : "同步售后列表" }}</button>
                  <button class="btn" type="button" @click="openAutoConfig">自动售后设置</button>
                  <button class="btn primary" type="button" @click="startAutoAfterSale">开始自动售后</button>
                  <button class="btn" type="button" @click="activeTab = 'logs'; loadLogs()">自动售后日志</button>
                  <button class="btn" type="button" @click="exportAfterSales">导出</button>
                </div>
              </header>
              <div class="wb-subtabs">
                <button
                  v-for="tab in afterSaleTabs"
                  :key="tab.key"
                  type="button"
                  :class="{ active: afterSaleFilter === tab.key }"
                  @click="afterSaleFilter = tab.key"
                >{{ tab.key }} <b>{{ tab.count }}</b></button>
              </div>
              <div class="wb-table-actions">
                <button class="btn" type="button" @click="removeSelectedAfterSales">移除</button>
                <button class="btn" type="button" @click="openAfterSaleNote">批量备注插旗</button>
                <button class="btn" type="button" @click="applyAfterSale('accept', '同意售后')">同意售后</button>
                <button class="btn" type="button" @click="applyAfterSale('reject', '拒绝售后')">拒绝售后</button>
                <button class="btn" type="button" @click="applyAfterSale('negotiate', '协商售后')">协商售后</button>
                <button class="btn" type="button" @click="applyAfterSale('accept_exchange', '同意换货')">同意换货</button>
                <button class="btn" type="button" @click="applyAfterSale('reject_exchange', '拒绝换货')">拒绝换货</button>
              </div>
              <div class="wb-table-wrap">
                <table class="wb-data-table">
                  <thead><tr><th>选择</th><th>店铺</th><th>订单号</th><th>售后单号</th><th>状态</th><th>类型</th><th>原因</th><th>退款金额</th><th>更新时间</th></tr></thead>
                  <tbody>
                    <tr v-for="row in visibleAfterSales" :key="`${row.store_id}:${row.after_sale_order_id}`" :class="{ selected: selectedAfterSaleId === row.after_sale_order_id }" @click="selectAfterSale(row)" @dblclick="openAfterSaleDetail(row)">
                      <td><input type="checkbox" :checked="selectedAfterSaleIds.includes(row.after_sale_order_id)" @click.stop @change="toggleAfterSale(row)" /></td>
                      <td>{{ row.store_name || "暂无数据" }}</td>
                      <td>{{ row.order_id || "暂无数据" }}</td>
                      <td>{{ row.after_sale_order_id }}</td>
                      <td>{{ row.status_text || row.status || "暂无数据" }}</td>
                      <td>{{ row.after_sale_type || "暂无数据" }}</td>
                      <td>{{ row.reason_text || "暂无数据" }}</td>
                      <td>{{ formatMoney(row.refund_amount) }}</td>
                      <td>{{ formatDateTime(row.update_time) }}</td>
                    </tr>
                    <tr v-if="!visibleAfterSales.length"><td colspan="9" class="wb-empty-cell">暂无售后数据</td></tr>
                  </tbody>
                </table>
              </div>
            </section>

            <section v-else-if="activeTab === 'transfer'" class="wb-table-tab">
              <header class="wb-section-head">
                <div><h2>中转售后</h2><p>中转单、工单、取件和理赔记录；第三方礼品单供应商下单当前未接入。</p></div>
                <div class="wb-head-actions">
                  <select v-model="transferCaseType" class="select" @change="loadTransferCases">
                    <option value="">全部类型</option><option value="internal">内部中转单</option><option value="external">外部中转单</option><option value="claim">招领包裹</option><option value="detention">滞留管理</option><option value="compensation">理赔管理</option>
                  </select>
                  <input v-model="transferSearch" class="input" placeholder="订单、中转单、工单、物流、取件码" @keyup.enter="loadTransferCases" />
                  <button class="btn" type="button" @click="loadTransferCases">搜索</button>
                  <button class="btn primary" type="button" @click="openTransferDialog()">新建中转单</button>
                  <button class="btn" type="button" @click="openTransferType('claim')">招领登记</button>
                  <button class="btn" type="button" @click="openTransferType('compensation')">理赔登记</button>
                </div>
              </header>
              <div class="wb-table-wrap">
                <table class="wb-data-table">
                  <thead><tr><th>店铺</th><th>订单号</th><th>中转单号</th><th>工单号</th><th>物流</th><th>取件码</th><th>仓库</th><th>状态</th><th>备注</th><th>操作</th></tr></thead>
                  <tbody>
                    <tr v-for="row in transferCases" :key="row.id">
                      <td>{{ row.store_name || "暂无数据" }}</td>
                      <td>{{ row.order_id || "暂无数据" }}</td>
                      <td>{{ row.transfer_order_no || "暂无数据" }}</td>
                      <td>{{ row.work_order_no || "暂无数据" }}</td>
                      <td>{{ row.tracking_no || "暂无数据" }}</td>
                      <td>{{ row.pickup_code || "暂无数据" }}</td>
                      <td>{{ row.warehouse || "暂无数据" }}</td>
                      <td>{{ row.status_text || row.status || "暂无数据" }}</td>
                      <td>{{ row.note || "暂无数据" }}</td>
                      <td><button class="mini-link" type="button" @click="openTransferDialog(row)">编辑</button></td>
                    </tr>
                    <tr v-if="!transferCases.length"><td colspan="10" class="wb-empty-cell">暂无中转记录</td></tr>
                  </tbody>
                </table>
              </div>
            </section>

            <section v-else-if="activeTab === 'messages'" class="wb-table-tab">
              <header class="wb-section-head">
                <div><h2>消息管理</h2><p>取件码通知；复制后自动记录为已发送</p></div>
                <div class="wb-head-actions">
                  <button class="btn" type="button" @click="loadMessages">刷新</button>
                  <button class="btn primary" type="button" @click="copyPickupCodes">复制选中取件码</button>
                </div>
              </header>
              <div class="wb-table-wrap">
                <table class="wb-data-table">
                  <thead><tr><th>选择</th><th>采购账号</th><th>平台</th><th>采购单号</th><th>取件码</th><th>关联订单</th><th>发送状态</th></tr></thead>
                  <tbody>
                    <tr v-for="row in messages" :key="messageKey(row)">
                      <td><input type="checkbox" :checked="selectedMessageKeys.includes(messageKey(row))" @change="toggleMessage(row)" /></td>
                      <td>{{ row.account_label || "暂无数据" }}</td>
                      <td>{{ row.platform || "暂无数据" }}</td>
                      <td>{{ row.external_order_id || "暂无数据" }}</td>
                      <td>{{ row.pickup_code }}</td>
                      <td>{{ row.links?.map((item) => `${item.store_name || item.store_id}/${item.order_id}`).join("、") || "暂无数据" }}</td>
                      <td>{{ row.sent ? "已发送" : "待发送" }}</td>
                    </tr>
                    <tr v-if="!messages.length"><td colspan="7" class="wb-empty-cell">暂无带取件码的采购通知</td></tr>
                  </tbody>
                </table>
              </div>
            </section>

            <section v-else-if="activeTab === 'logs'" class="wb-table-tab">
              <header class="wb-section-head">
                <div><h2>软件日志</h2><p>任务、同步、真实写操作和异常记录</p></div>
                <button class="btn" type="button" @click="loadLogs">刷新</button>
              </header>
              <div class="wb-table-wrap">
                <table class="wb-data-table">
                  <thead><tr><th>开始时间</th><th>范围</th><th>操作</th><th>状态</th><th>结果</th></tr></thead>
                  <tbody>
                    <tr v-for="(row, index) in logs" :key="`${row.id}:${index}`">
                      <td>{{ row.started_at || "暂无数据" }}</td><td>{{ row.store_name || row.scope || "暂无数据" }}</td><td>{{ row.operation || "暂无数据" }}</td><td>{{ row.status || "暂无数据" }}</td><td class="wb-log-message">{{ row.message || "暂无数据" }}</td>
                    </tr>
                    <tr v-if="!logs.length"><td colspan="5" class="wb-empty-cell">暂无任务日志</td></tr>
                  </tbody>
                </table>
              </div>
            </section>

            <section v-else-if="activeTab === 'settings'" class="wb-settings-tab">
              <header class="wb-section-head"><div><h2>设置中心</h2><p>订单打印、礼品单补发与寄件信息</p></div></header>
              <div class="wb-settings-grid">
                <section class="wb-card">
                  <header><strong>打单设置</strong></header>
                  <div class="field"><label>电子面单方式</label><select v-model="settingForms.print_mode" class="select"><option>代发打单</option><option>本地打印</option></select></div>
                  <p class="wb-setting-note">电子面单供应商绑定仍由现有物流发货模块管理。</p>
                </section>
                <section class="wb-card">
                  <header><strong>礼品单设置</strong></header>
                  <label class="switch-row"><span>打印出单后自动补发货</span><input v-model="settingForms.gift_auto_reship" type="checkbox" /></label>
                  <div class="form-grid">
                    <div class="field"><label>寄件人姓名</label><input v-model="settingForms.gift_sender.name" class="input" /></div>
                    <div class="field"><label>寄件人电话</label><input v-model="settingForms.gift_sender.phone" class="input" /></div>
                    <div class="field full"><label>寄件地址</label><input v-model="settingForms.gift_sender.address" class="input" /></div>
                  </div>
                </section>
              </div>
              <div class="wb-settings-actions"><button class="btn primary" type="button" @click="saveWorkbenchSettings">保存设置</button></div>
            </section>

            <div v-if="feedback.message" class="wb-feedback" :class="feedback.type">
              <span v-if="feedback.type === 'loading'" class="wb-spinner" />
              {{ feedback.message }}
            </div>
          </main>
        </div>

        <ModalDialog
          :open="confirmDialog.open"
          :title="confirmDialog.title"
          :subtitle="confirmDialog.target"
          width="560px"
          :z-index="130"
          @close="closeConfirmation"
        >
          <div class="wb-confirm-copy">
            <p>{{ confirmDialog.description }}</p>
            <div v-if="confirmDialog.risk"><strong>风险提示</strong><span>{{ confirmDialog.risk }}</span></div>
          </div>
          <template #footer>
            <button class="btn" type="button" :disabled="confirmBusy" @click="closeConfirmation">取消</button>
            <button class="btn primary" type="button" :disabled="confirmBusy" @click="confirmPendingAction">
              {{ confirmBusy ? "处理中..." : confirmDialog.confirmLabel }}
            </button>
          </template>
        </ModalDialog>

        <ModalDialog :open="noteDialogOpen" title="标记旗帜与备注" width="620px" @close="noteDialogOpen = false">
          <div class="note-dialog">
            <div class="note-type-row"><strong>备注类型：</strong><label><input v-model="noteScope" type="radio" value="软件备注" @change="switchNoteScope('软件备注')" /> 软件备注</label><label><input v-model="noteScope" type="radio" value="店铺备注" @change="switchNoteScope('店铺备注')" /> 店铺备注</label></div>
            <div class="note-flag-row"><strong>标记：</strong><button type="button" class="note-flag-button none" :class="{ active: noteFlag === '' }" @click="noteFlag = ''">无色</button><button v-for="flag in ['红', '橙', '绿', '蓝']" :key="flag" type="button" class="note-flag-button" :class="[`flag-${flag}`, { active: noteFlag === flag }]" @click="noteFlag = flag">⚑ {{ flag }}</button></div>
            <textarea v-model="noteText" class="input wb-note-textarea" placeholder="填写订单备注" />
            <label class="note-sync-row"><input v-model="noteSyncStore" type="checkbox" /> 同时同步到店铺备注 <small>默认仅保存在澜琅</small></label>
          </div>
          <template #footer><button class="btn" type="button" @click="noteDialogOpen = false">取消</button><button class="btn primary" type="button" @click="submitNote">保存</button></template>
        </ModalDialog>

        <ModalDialog :open="storeSyncConfirmOpen" title="同步店铺备注" width="500px" @close="storeSyncConfirmOpen = false">
          <p class="action-dialog-copy">将把当前旗帜和备注写入微信店铺备注，并保存到本机。是否继续？</p>
          <template #footer><button class="btn" type="button" @click="storeSyncConfirmOpen = false">取消</button><button class="btn primary" type="button" @click="saveNote">确认同步</button></template>
        </ModalDialog>

        <ModalDialog :open="manualPurchaseOpen" title="补录采购信息" width="680px" @close="manualPurchaseOpen = false">
          <div class="form-grid">
            <div class="field"><label>采购平台</label><select v-model="manualPurchase.platform" class="select"><option>1688</option><option>淘宝</option><option>拼多多</option></select></div>
            <div class="field"><label>采购订单号</label><input v-model="manualPurchase.purchase_order_no" class="input" /></div>
            <div class="field"><label>采购金额</label><input v-model="manualPurchase.purchase_amount" class="input" inputmode="decimal" /></div>
            <div class="field"><label>佣金 %</label><input v-model="manualPurchase.commission" class="input" inputmode="decimal" /></div>
            <div class="field"><label>采购方式</label><select v-model="manualPurchase.purchase_mode" class="select"><option>手动下单</option><option>极速下单</option></select></div>
            <div class="field"><label>旗帜</label><select v-model="manualPurchase.flag" class="select"><option value="">无</option><option>灰</option><option>红</option><option>橙</option><option>绿</option><option>蓝</option></select></div>
            <label class="field full"><span><input v-model="manualPurchase.transfer_warehouse" type="checkbox" /> 使用中转仓</span></label>
            <label class="field full"><span><input v-model="manualPurchase.remember" type="checkbox" /> 记住本次采购设置</span></label>
          </div>
          <template #footer><button class="btn" type="button" @click="manualPurchaseOpen = false">取消</button><button class="btn primary" type="button" @click="submitManualPurchase">保存关联</button></template>
        </ModalDialog>

        <ModalDialog :open="addressOpen" title="申请修改收货地址" width="680px" @close="addressOpen = false">
          <p class="action-dialog-copy">提交后需要买家同意，平台不会立即修改成功。</p>
          <div class="form-grid">
            <div class="field"><label>收货人</label><input v-model="addressForm.user_name" class="input" /></div>
            <div class="field"><label>联系电话</label><input v-model="addressForm.tel_number" class="input" /></div>
            <div class="field"><label>省份</label><input v-model="addressForm.province_name" class="input" /></div>
            <div class="field"><label>城市</label><input v-model="addressForm.city_name" class="input" /></div>
            <div class="field"><label>区县</label><input v-model="addressForm.county_name" class="input" /></div>
            <div class="field"><label>街道</label><input v-model="addressForm.street_name" class="input" /></div>
            <div class="field full"><label>详细地址</label><input v-model="addressForm.detail_info" class="input" /></div>
          </div>
          <template #footer><button class="btn" type="button" @click="addressOpen = false">取消</button><button class="btn primary" type="button" @click="submitAddress">提交改址申请</button></template>
        </ModalDialog>

        <ModalDialog :open="priceOpen" title="修改订单价格" width="760px" @close="priceOpen = false">
          <div class="wb-price-list">
            <label v-for="row in priceRows" :key="`${row.product_id}:${row.sku_id}`" class="wb-price-row">
              <span>{{ row.title }} ×1</span>
              <input v-model="row.price" class="input" inputmode="decimal" />
            </label>
          </div>
          <label class="wb-inline-check"><input v-model="changeExpress" type="checkbox" /> 同时修改运费 <input v-model="expressFee" class="input" inputmode="decimal" /> 元</label>
          <template #footer><button class="btn" type="button" @click="priceOpen = false">取消</button><button class="btn primary" type="button" @click="submitPrice">提交改价</button></template>
        </ModalDialog>

        <ModalDialog :open="deliveryOpen" title="发货 / 修改物流" width="760px" @close="deliveryOpen = false">
          <div class="wb-package-list">
            <div v-for="(row, index) in deliveryPackages" :key="index" class="wb-package-row">
              <input v-model="row.delivery_id" class="input" placeholder="快递公司编码，例如 ZTO" />
              <input v-model="row.waybill_id" class="input" placeholder="运单号" />
              <button class="btn" type="button" :disabled="deliveryPackages.length <= 1" @click="removeDeliveryPackage(index)">移除</button>
            </div>
          </div>
          <button class="btn" type="button" @click="addDeliveryPackage">添加包裹</button>
          <template #footer><button class="btn" type="button" @click="deliveryOpen = false">取消</button><button class="btn primary" type="button" @click="submitDelivery">提交发货</button></template>
        </ModalDialog>

        <ModalDialog :open="transferDialogOpen" title="中转工单" width="720px" @close="transferDialogOpen = false">
          <div class="form-grid">
            <div class="field"><label>店铺</label><select v-model="transferForm.store_id" class="select"><option value="">未关联店铺</option><option v-for="store in stores" :key="store.id" :value="store.id">{{ store.name }}</option></select></div>
            <div class="field"><label>订单号</label><input v-model="transferForm.order_id" class="input" /></div>
            <div class="field"><label>中转单号</label><input v-model="transferForm.transfer_order_no" class="input" /></div>
            <div class="field"><label>工单号</label><input v-model="transferForm.work_order_no" class="input" /></div>
            <div class="field"><label>物流单号</label><input v-model="transferForm.tracking_no" class="input" /></div>
            <div class="field"><label>取件码</label><input v-model="transferForm.pickup_code" class="input" /></div>
            <div class="field"><label>仓库</label><input v-model="transferForm.warehouse" class="input" /></div>
            <div class="field"><label>状态</label><input v-model="transferForm.status" class="input" /></div>
            <div class="field full"><label>备注</label><input v-model="transferForm.note" class="input" /></div>
          </div>
          <template #footer><button class="btn" type="button" @click="transferDialogOpen = false">取消</button><button class="btn primary" type="button" @click="saveTransferCase">保存</button></template>
        </ModalDialog>

        <ModalDialog :open="autoConfigOpen" title="自动售后设置" width="620px" @close="autoConfigOpen = false">
          <label class="wb-inline-check"><input v-model="autoConfig.enabled" type="checkbox" /> 启用规则校验后的自动售后</label>
          <div class="form-grid">
            <div class="field"><label>默认处理动作</label><select v-model="autoConfig.action" class="select"><option value="accept">同意售后</option><option value="reject">拒绝售后</option><option value="negotiate">协商售后</option></select></div>
            <div class="field"><label>单笔金额上限（元）</label><input v-model="autoConfig.max_amount" class="input" inputmode="decimal" /></div>
            <div class="field full"><label>允许的售后类型</label><input v-model="autoConfig.allowed_types" class="input" placeholder="仅退款,退货退款" /></div>
          </div>
          <p class="action-dialog-copy">实际执行前仍会再次确认；金额超限、类型不符或状态不明的售后会跳过。</p>
          <template #footer><button class="btn" type="button" @click="autoConfigOpen = false">取消</button><button class="btn primary" type="button" @click="saveAutoConfig">保存设置</button></template>
        </ModalDialog>

        <ModalDialog :open="afterSaleNoteOpen" title="批量备注插旗" width="600px" @close="afterSaleNoteOpen = false">
          <div class="note-flag-row">
            <strong>旗帜：</strong>
            <button type="button" class="note-flag-button none" :class="{ active: afterSaleFlag === '' }" @click="afterSaleFlag = ''">无色</button>
            <button v-for="flag in ['红', '橙', '绿', '蓝']" :key="flag" type="button" class="note-flag-button" :class="[`flag-${flag}`, { active: afterSaleFlag === flag }]" @click="afterSaleFlag = flag">⚑ {{ flag }}</button>
          </div>
          <textarea v-model="afterSaleNote" class="input wb-note-textarea" placeholder="填写软件内售后备注" />
          <p class="action-dialog-copy">该操作仅更新澜琅本地售后标记与备注，不修改微信平台售后状态。</p>
          <template #footer><button class="btn" type="button" @click="afterSaleNoteOpen = false">取消</button><button class="btn primary" type="button" @click="saveAfterSaleNote">保存</button></template>
        </ModalDialog>

        <ModalDialog :open="afterSaleDetailOpen" title="售后详情" :subtitle="selectedAfterSale?.after_sale_order_id || ''" width="900px" @close="afterSaleDetailOpen = false">
          <div v-if="afterSaleDetailLoading" class="wb-empty">正在读取售后详情…</div>
          <div v-else-if="afterSaleDetail" class="after-sale-detail">
            <dl>
              <dt>店铺</dt><dd>{{ selectedAfterSale?.store_name || selectedAfterSale?.store_id || "暂无数据" }}</dd>
              <dt>订单号</dt><dd>{{ selectedAfterSale?.order_id || "暂无数据" }}</dd>
              <dt>售后类型</dt><dd>{{ selectedAfterSale?.after_sale_type || "暂无数据" }}</dd>
              <dt>当前状态</dt><dd>{{ selectedAfterSale?.status_text || selectedAfterSale?.status || "暂无数据" }}</dd>
              <dt>退款金额</dt><dd>{{ formatMoney(selectedAfterSale?.refund_amount) }}</dd>
              <dt>原因</dt><dd>{{ selectedAfterSale?.reason_text || "暂无数据" }}</dd>
            </dl>
            <section class="after-sale-proof-upload">
              <header>
                <strong>上传售后凭证</strong>
                <span>支持 JPG、PNG、WebP，单次最多 9 张、每张不超过 10MB</span>
              </header>
              <input type="file" accept=".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp" multiple @change="selectAfterSaleProofFiles" />
              <div class="after-sale-proof-actions">
                <span>已选择 {{ afterSaleProofFiles.length }} 张</span>
                <button class="btn primary" type="button" :disabled="afterSaleMediaBusy || !afterSaleProofFiles.length" @click="uploadAfterSaleProofs">
                  {{ afterSaleMediaBusy ? "上传中…" : "上传凭证" }}
                </button>
              </div>
              <p v-if="afterSaleMediaIds.length">已返回 media_id：{{ afterSaleMediaIds.join("、") }}</p>
            </section>
            <h3>原始售后详情</h3>
            <pre>{{ JSON.stringify(afterSaleDetail.raw, null, 2) }}</pre>
          </div>
          <template #footer><button class="btn" type="button" @click="afterSaleDetailOpen = false">关闭</button></template>
        </ModalDialog>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.workbench-overlay {
  position: fixed;
  inset: 0;
  z-index: 80;
  padding: 18px;
  background: rgba(11, 24, 38, 0.46);
  backdrop-filter: blur(4px);
}

.workbench-shell {
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border-radius: 10px;
  background: #f3f6f9;
  box-shadow: 0 24px 80px rgba(8, 20, 32, 0.34);
}

.workbench-titlebar {
  min-height: 54px;
  padding: 0 16px 0 18px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: white;
  background: #102a43;
}

.workbench-titlebar strong,
.workbench-titlebar span {
  display: block;
}

.workbench-titlebar strong { font-size: 15px; }
.workbench-titlebar span { margin-top: 2px; color: #9ab1c4; font-size: 10px; }
.workbench-close { width: 32px; height: 32px; border: 0; color: #dbe8f2; background: transparent; }

.workbench-layout {
  min-height: 0;
  flex: 1;
  display: grid;
  grid-template-columns: 190px minmax(0, 1fr);
}

.workbench-nav {
  min-height: 0;
  padding: 10px 8px;
  display: flex;
  flex-direction: column;
  gap: 3px;
  border-right: 1px solid #263e56;
  background: #12324d;
}

.workbench-nav > button {
  min-height: 48px;
  padding: 7px 9px;
  display: grid;
  grid-template-columns: 21px minmax(0, 1fr);
  align-items: center;
  gap: 7px;
  border: 0;
  border-radius: 6px;
  color: #aac0d3;
  background: transparent;
  text-align: left;
}

.workbench-nav > button:hover,
.workbench-nav > button.active {
  color: white;
  background: #1d4b6c;
}

.workbench-nav > button.active {
  box-shadow: inset 3px 0 #40c792;
}

.workbench-nav strong,
.workbench-nav small {
  display: block;
}

.workbench-nav strong { font-size: 12px; font-weight: 600; }
.workbench-nav small { margin-top: 2px; overflow: hidden; color: #819db4; font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }
.workbench-nav-spacer { flex: 1; }
.workbench-selected { padding: 10px; border: 1px solid rgba(255,255,255,.08); border-radius: 7px; background: rgba(255,255,255,.04); }
.workbench-selected small,
.workbench-selected strong,
.workbench-selected span { display: block; }
.workbench-selected small { color: #7f9ab1; font-size: 9px; }
.workbench-selected strong { margin-top: 5px; color: white; font-size: 11px; }
.workbench-selected span { margin-top: 3px; color: #92aec2; font-size: 9px; overflow-wrap: anywhere; }

.workbench-content {
  position: relative;
  min-width: 0;
  min-height: 0;
  overflow: auto;
}

.wb-orders-tab {
  height: 100%;
  display: grid;
  grid-template-columns: 330px minmax(0, 1fr);
}

.wb-order-queue {
  min-height: 0;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  border-right: 1px solid #dce4eb;
  background: #f8fafc;
}

.wb-panel-head {
  display: flex;
  justify-content: space-between;
  color: #566a7d;
  font-size: 11px;
}

.wb-queue-list { min-height: 0; flex: 1; overflow: auto; }

.wb-queue-item {
  width: 100%;
  min-height: 84px;
  padding: 9px;
  display: grid;
  grid-template-columns: 42px minmax(0, 1fr) auto;
  gap: 8px;
  border: 0;
  border-bottom: 1px solid #e3e9ef;
  color: #223445;
  background: transparent;
  text-align: left;
}

.wb-queue-item:hover { background: #eef5fb; }
.wb-queue-item.active { background: #e8f2fb; box-shadow: inset 3px 0 #2e7fc4; }
.wb-queue-item img,
.wb-thumb-empty { width: 42px; height: 42px; grid-row: 1 / span 2; border-radius: 5px; object-fit: cover; }
.wb-thumb-empty { display: grid; place-items: center; color: #96a3af; background: #e9eef2; font-size: 9px; }
.wb-queue-copy { min-width: 0; }
.wb-queue-copy strong,
.wb-queue-copy em,
.wb-queue-copy b,
.wb-queue-copy small { display: block; }
.wb-queue-copy strong { color: #17334d; font-size: 11px; }
.wb-queue-copy em { width: fit-content; margin-top: 2px; padding: 1px 5px; border-radius: 3px; color: #27637f; background: #dff2fc; font-size: 9px; font-style: normal; }
.wb-queue-copy b { margin-top: 4px; overflow: hidden; font-size: 10px; font-weight: 500; text-overflow: ellipsis; white-space: nowrap; }
.wb-queue-copy small { margin-top: 3px; color: #8594a2; font-size: 9px; }
.wb-queue-money { color: #11724e; font-size: 12px; white-space: nowrap; }

.wb-order-detail { min-width: 0; padding: 16px; overflow: auto; }
.wb-loading { min-height: 400px; display: grid; place-items: center; color: #7a8997; }
.wb-detail-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; padding-bottom: 14px; border-bottom: 1px solid #dce5ec; }
.wb-detail-header h2 { max-width: 850px; margin: 8px 0 5px; color: #1c3143; font-size: 16px; line-height: 1.45; }
.wb-detail-header p { margin: 0; color: #708294; font-size: 10px; }
.status-chip { padding: 2px 7px; border-radius: 4px; color: #145270; background: #def3ff; font-size: 9px; }
.wb-detail-amount { min-width: 130px; text-align: right; }
.wb-detail-amount small,
.wb-detail-amount strong { display: block; }
.wb-detail-amount small { color: #718396; font-size: 10px; }
.wb-detail-amount strong { margin-top: 3px; color: #11724e; font-size: 22px; }

.wb-metrics { margin: 12px 0; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border: 1px solid #dfe6ed; border-radius: 7px; background: white; overflow: hidden; }
.wb-metrics > div { min-height: 62px; padding: 10px 12px; border-right: 1px solid #e4eaf0; }
.wb-metrics > div:last-child { border-right: 0; }
.wb-metrics span,
.wb-metrics strong { display: block; }
.wb-metrics span { color: #758697; font-size: 9px; }
.wb-metrics strong { margin-top: 5px; font-size: 13px; overflow-wrap: anywhere; }

.wb-action-strip { margin-bottom: 12px; display: flex; flex-wrap: wrap; gap: 7px; }

.wb-detail-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.wb-card { min-width: 0; padding: 12px; border: 1px solid #dfe6ed; border-radius: 7px; background: white; }
.wb-card > header { min-height: 24px; display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 8px; }
.wb-card > header strong { font-size: 12px; }
.wb-card > header span { color: #7d8d9d; font-size: 9px; }
.wb-card dl { margin: 0; display: grid; grid-template-columns: 68px minmax(0, 1fr); gap: 6px 8px; font-size: 10px; }
.wb-card dt { color: #7c8d9d; }
.wb-card dd { margin: 0; color: #34495c; overflow-wrap: anywhere; }
.wb-card-actions { margin-top: 12px; display: flex; flex-wrap: wrap; gap: 5px; }
.wb-card-actions .btn { min-height: 28px; padding: 0 9px; font-size: 10px; }
.mini-link { padding: 0; border: 0; color: #2f70a3; background: transparent; font-size: 10px; }

.wb-link-list,
.wb-after-list { display: grid; gap: 6px; max-height: 150px; overflow: auto; }
.wb-link-list > div,
.wb-after-list button { padding: 7px; display: grid; gap: 2px; border: 1px solid #e3e9ef; border-radius: 5px; color: #425668; background: #fbfcfd; text-align: left; font-size: 9px; }
.wb-link-list strong,
.wb-after-list strong { color: #263d50; font-size: 10px; }
.wb-after-list em { color: #a33e48; font-style: normal; }

.wb-empty { min-height: 240px; display: grid; place-items: center; color: #96a3af; font-size: 11px; }
.wb-empty.compact { min-height: 58px; }

.wb-table-tab,
.wb-settings-tab { min-width: 1050px; padding: 16px; }
.wb-section-head { min-height: 52px; display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; margin-bottom: 10px; }
.wb-section-head h2 { margin: 0; font-size: 17px; }
.wb-section-head p { margin: 4px 0 0; color: #7b8c9b; font-size: 10px; }
.wb-head-actions,
.wb-table-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 6px; }
.wb-table-actions { margin-bottom: 9px; }
.wb-subtabs { margin-bottom: 8px; display: flex; flex-wrap: wrap; gap: 6px; }
.wb-subtabs button { height: 28px; padding: 0 10px; display: flex; align-items: center; gap: 5px; border: 1px solid #dce4eb; border-radius: 14px; color: #53677a; background: white; font-size: 10px; }
.wb-subtabs button.active { border-color: #173d5c; color: white; background: #173d5c; }
.wb-subtabs b { min-width: 17px; padding: 0 4px; border-radius: 8px; color: white; background: #8a99a7; font-size: 8px; text-align: center; }
.wb-subtabs button.active b { color: #173d5c; background: white; }
.wb-head-actions .input { width: 250px; }
.wb-table-wrap { overflow: auto; border: 1px solid #dfe6ed; border-radius: 7px; background: white; }
.wb-data-table { width: 100%; border-collapse: collapse; white-space: nowrap; font-size: 10px; }
.wb-data-table th { height: 36px; padding: 0 9px; color: #647485; background: #f7f9fb; text-align: left; }
.wb-data-table td { height: 40px; padding: 0 9px; border-top: 1px solid #e7edf2; }
.wb-data-table tr.selected { background: #edf7ff; }
.wb-empty-cell { height: 160px !important; color: #96a3af; text-align: center; }
.wb-log-message { max-width: 620px; overflow: hidden; text-overflow: ellipsis; }

.wb-settings-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.wb-settings-grid .wb-card { min-height: 220px; }
.wb-setting-note { color: #7b8c9b; font-size: 10px; line-height: 1.7; }
.wb-settings-actions { margin-top: 12px; display: flex; justify-content: flex-end; }

.wb-embedded-page { min-width: 1000px; }
.wb-embedded-page :deep(.page) { padding: 14px; }
.wb-embedded-page :deep(.page-header) { margin-bottom: 12px; }

.wb-feedback {
  position: sticky;
  left: 50%;
  bottom: 12px;
  z-index: 5;
  width: fit-content;
  max-width: 560px;
  margin: 10px auto 0;
  padding: 8px 12px;
  display: flex;
  align-items: center;
  gap: 7px;
  border-radius: 5px;
  color: #43566a;
  background: #fff;
  box-shadow: 0 6px 22px rgba(20, 43, 64, 0.16);
  font-size: 10px;
}
.wb-feedback.success { color: #126947; background: #ecfaf4; }
.wb-feedback.error { color: #a52f3a; background: #fff0f2; }
.wb-spinner { width: 12px; height: 12px; border: 2px solid #c6d2dc; border-top-color: #2f80b7; border-radius: 50%; animation: control-spin 700ms linear infinite; }

.wb-note-textarea { width: 100%; height: 180px; padding: 10px; resize: vertical; }
.note-type-row,
.note-flag-row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 12px; font-size: 11px; }
.note-flag-button { min-width: 58px; height: 30px; border: 2px solid transparent; border-radius: 4px; color: white; background: #77838e; }
.note-flag-button.none { color: #596775; background: #edf1f4; }
.note-flag-button.active { box-shadow: 0 0 0 2px #fff, 0 0 0 4px #6685a4; }
.flag-灰 { background: #7b8792 !important; }
.flag-红 { background: #e5484d !important; }
.flag-橙 { background: #f0761b !important; }
.flag-绿 { background: #22a06b !important; }
.flag-蓝 { background: #3478f6 !important; }
.note-sync-row { display: flex; align-items: center; gap: 6px; margin-top: 10px; font-size: 11px; }
.note-sync-row small { color: #84939f; }
.action-dialog-copy { margin: 0 0 14px; color: #667888; font-size: 11px; line-height: 1.7; }
.wb-confirm-copy { display: grid; gap: 12px; color: #4d6173; font-size: 11px; line-height: 1.7; }
.wb-confirm-copy p { margin: 0; }
.wb-confirm-copy > div { padding: 10px 12px; display: grid; gap: 4px; border: 1px solid #f0d7b1; border-radius: 6px; background: #fff9ee; }
.wb-confirm-copy strong { color: #9a620d; }
.wb-confirm-copy span { color: #715a34; }
.after-sale-detail { display: grid; gap: 12px; }
.after-sale-detail dl { margin: 0; display: grid; grid-template-columns: 100px minmax(0, 1fr); gap: 7px 10px; font-size: 11px; }
.after-sale-detail dt { color: #7a8b9b; }
.after-sale-detail dd { margin: 0; color: #2f4558; overflow-wrap: anywhere; }
.after-sale-detail h3 { margin: 4px 0 0; font-size: 12px; }
.after-sale-detail pre { margin: 0; max-height: 430px; overflow: auto; padding: 12px; border: 1px solid #dfe6ed; border-radius: 6px; color: #34495b; background: #f7f9fb; font: 10px/1.6 Consolas, monospace; white-space: pre-wrap; word-break: break-word; }
.after-sale-proof-upload { padding: 12px; display: grid; gap: 10px; border: 1px solid #dfe6ed; border-radius: 6px; background: #fbfcfd; }
.after-sale-proof-upload header { display: flex; justify-content: space-between; gap: 12px; }
.after-sale-proof-upload header span, .after-sale-proof-upload p { color: #788897; font-size: 10px; }
.after-sale-proof-actions { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.wb-price-list { display: grid; gap: 8px; }
.wb-price-row { display: grid; grid-template-columns: minmax(0, 1fr) 150px; align-items: center; gap: 10px; font-size: 11px; }
.wb-inline-check { margin-top: 12px; display: flex; align-items: center; gap: 7px; font-size: 11px; }
.wb-inline-check .input { width: 100px; }
.wb-package-list { display: grid; gap: 8px; margin-bottom: 9px; }
.wb-package-row { display: grid; grid-template-columns: 210px minmax(0, 1fr) 72px; gap: 7px; }

@keyframes control-spin {
  to { transform: rotate(360deg); }
}
</style>
