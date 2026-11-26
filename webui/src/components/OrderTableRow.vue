<script setup>
import {
  computed,
  nextTick,
  onBeforeUnmount,
  reactive,
  ref,
  watch,
} from "vue";
import { call, formatDateTime, formatMoney } from "../api";
import { copyText } from "../clipboard";
import { hasEntitlement } from "../session";
import {
  fulfillmentStageLabel,
  orderStatusLabel,
  shippingCountdown,
} from "../orderStatus";
import {
  procurementAddressForm,
  procurementAddressText,
} from "../procurementAddress";
import {
  isOfficialDeliveryCode,
  officialDeliveryAction,
  supplierShipments,
} from "../orderProcurementFlow";
import Icon from "./Icon.vue";
import ModalDialog from "./ModalDialog.vue";
import OrderProcurementDialog from "./OrderProcurementDialog.vue";

const props = defineProps({
  order: { type: Object, required: true },
  selected: Boolean,
  refreshing: Boolean,
  openingOfficial: Boolean,
  autoProcess: Boolean,
  nowSeconds: { type: Number, default: 0 },
});
const emit = defineEmits([
  "changed",
  "contextmenu",
  "mark",
  "note",
  "official",
  "preview",
  "product",
  "refresh",
  "select",
  "toast",
]);

const actionBusy = ref("");
const detail = ref(null);
const detailLoading = ref(false);
const menuOpen = ref(false);
const purchaseOpen = ref(false);
const procurementDialogOpen = ref(false);
const deliveryOpen = ref(false);
const addressOpen = ref(false);
const priceOpen = ref(false);
const afterSalesOpen = ref(false);
const afterSalesLoading = ref(false);
const afterSales = ref([]);
const afterSaleProofInput = ref(null);
const afterSaleProofTarget = ref(null);
const afterSaleProofBusy = reactive({});
const afterSaleMediaIds = reactive({});
const afterSaleActionOpen = ref(false);
const afterSaleAction = reactive({
  row: null,
  operation: "",
  title: "",
  reject_reason_type: "",
  reject_reason: "",
  merchant_update_type: "",
  update_reason_type: "",
  type: "",
  amount: "",
  merchant_update_desc: "",
  delivery_id: "",
  waybill_id: "",
});
const shipment = ref(null);
const logsOpen = ref(false);
const logsLoading = ref(false);
const orderLogs = ref([]);
const logisticsOpen = ref(false);
const logisticsLoading = ref(false);
const logisticsError = ref("");
const logisticsData = ref(null);
const logisticsTarget = ref(null);
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
const purchaseForm = reactive({
  platform: "1688",
  purchase_order_no: "",
  purchase_amount: "",
  commission: "0",
  purchase_mode: "手动下单",
  transfer_warehouse: false,
  remember: false,
});
const deliveryPackages = ref([{ delivery_id: "", waybill_id: "" }]);
const deliveryUsesSupplier = ref(false);
const addressForm = reactive({
  user_name: "",
  tel_number: "",
  province_name: "",
  city_name: "",
  county_name: "",
  street_name: "",
  detail_info: "",
});
const addressRawText = ref("");
const addressParseResult = ref(null);
const addressParseLoading = ref(false);
const addressAutoParsed = ref(false);
let addressParseTimer = null;
const priceRows = ref([]);
const changeExpress = ref(false);
const expressFee = ref("0.00");

const firstPurchase = computed(() => props.order.purchase_orders?.[0] || null);
const purchaseCount = computed(() => Number(props.order.procurement_count || props.order.purchase_orders?.length || 0));
const displayProcurementState = computed(() => (
  props.order.fulfillment_stage === "shipped"
    ? "shipped"
    : props.order.procurement_state
));
const procurementStateLabel = computed(() => ({
  unlinked: "待关联",
  wait_purchase: "待拍单",
  wait_supplier: "已采购 / 等上家发货",
  auto_filling: "上家已发货 / 待官方发货",
  shipped: "店铺已发货",
  aftersale: "售后自动处理",
  error: "需人工核对",
}[displayProcurementState.value] || "待处理"));
const procurementActionLabel = computed(() => ({
  unlinked: "关联采购链接",
  wait_purchase: "去拍单",
  error: "查看异常",
  wait_supplier: "查看采购回读",
  auto_filling: "查看上家物流",
  shipped: "查看采购",
}[displayProcurementState.value] || ""));
const supplierShippingRows = computed(() => supplierShipments(props.order));
const deliveryAction = computed(() => officialDeliveryAction(props.order));
const afterSaleCount = computed(() => Number(props.order.after_sale_count || props.order.after_sales?.length || 0));
const canViewAfterSales = computed(() => hasEntitlement("module.after_sales"));
const waybill = computed(() => props.order.waybill_id || "");
const carrier = computed(() => (
  props.order.carrier
  || (waybill.value ? "物流信息" : "")
));
const displayStatus = computed(() => orderStatusLabel(props.order));
const statusClass = computed(() => `status-${displayStatus.value}`.replace(/\s+/g, "-"));
const noteFlag = computed(() => props.order.official_note_color || props.order.flag || "");
const noteFlagCode = computed(() => {
  const official = props.order.official_note_color_code;
  if (official !== null && official !== undefined && official !== "") {
    return Number(official);
  }
  return {
    "红": 1,
    "橙": 2,
    "绿": 3,
    "蓝": 4,
  }[props.order.flag] || 0;
});
const noteFlagClass = computed(() => {
  return {
    1: "flag-red",
    2: "flag-orange",
    3: "flag-green",
    4: "flag-blue",
  }[noteFlagCode.value] || "flag-none";
});
const noteFlagTitle = computed(() => {
  if (noteFlagCode.value === 0) {
    return props.order.merchant_notes
      ? "官方未设置旗帜颜色"
      : "官方无旗帜、无备注";
  }
  return `官方旗帜：${noteFlag.value}`;
});
const logisticsPackages = computed(() => {
  const rows = Array.isArray(props.order.packages) ? props.order.packages : [];
  if (rows.length) return rows;
  if (waybill.value) {
    return [{
      waybill_id: waybill.value,
      delivery_id: props.order.carrier || "",
      delivery_name: props.order.carrier || "物流信息",
      traces: [],
    }];
  }
  return [];
});

watch(
  () => props.autoProcess,
  (enabled) => {
    if (!enabled) return;
    nextTick(() => {
      menuOpen.value = true;
    });
  },
  { immediate: true },
);

function maskedPhone(value) {
  const text = String(value || "");
  return text ? `${text.slice(0, 1)}**********` : "暂无数据";
}

function notify(message) {
  emit("toast", String(message || ""));
}

async function run(label, work, successMessage = "") {
  if (actionBusy.value) return null;
  actionBusy.value = label;
  try {
    const result = await work();
    if (successMessage) notify(successMessage);
    return result;
  } catch (cause) {
    notify(cause instanceof Error ? cause.message : String(cause));
    return null;
  } finally {
    actionBusy.value = "";
  }
}

async function copyOrderId() {
  try {
    await copyText(props.order.order_id);
    notify("订单号已复制");
  } catch (cause) {
    notify(cause instanceof Error ? cause.message : String(cause));
  }
}

async function copyAddress() {
  const current = await ensureDetail();
  const address = current?.procurement_address || current?.order?.address_info || {};
  const value = [
    address.user_name || current?.order?.buyer_name || props.order.buyer_name,
    address.tel_number || current?.order?.phone || props.order.phone,
    [
      address.province_name,
      address.city_name,
      address.county_name,
      address.street_name,
      address.detail_info,
    ].filter(Boolean).join("") || current?.order?.address || props.order.address,
  ].filter(Boolean).join("，");
  if (!value) {
    notify("当前订单没有可复制的收货信息");
    return;
  }
  try {
    await copyText(value);
    notify("收货信息已复制");
  } catch (cause) {
    notify(cause instanceof Error ? cause.message : String(cause));
  }
}

async function copyWaybill(packageRow) {
  const value = String(packageRow?.waybill_id || "");
  if (!value) {
    notify("当前包裹没有可复制的物流单号");
    return;
  }
  try {
    await copyText(value);
    notify(`物流单号已复制：${value}`);
  } catch (cause) {
    notify(cause instanceof Error ? cause.message : String(cause));
  }
}

async function openLogistics(packageRow) {
  if (!packageRow?.waybill_id || logisticsLoading.value) return;
  logisticsTarget.value = packageRow;
  logisticsData.value = null;
  logisticsError.value = "";
  logisticsOpen.value = true;
  logisticsLoading.value = true;
  try {
    logisticsData.value = await call("orders.logistics.track", {
      store_id: props.order.store_id,
      order_id: props.order.order_id,
      waybill_id: packageRow.waybill_id,
      delivery_id: packageRow.delivery_id || "",
    });
    if (!logisticsData.value?.package?.traces?.length) {
      logisticsError.value = "官方接口未返回轨迹节点，请稍后刷新";
    }
  } catch (cause) {
    logisticsError.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    logisticsLoading.value = false;
  }
}

async function ensureDetail(force = false) {
  if (!force && detail.value) return detail.value;
  if (detailLoading.value) return null;
  detailLoading.value = true;
  try {
    detail.value = await call("orders.workbench.detail", {
      store_id: props.order.store_id,
      order_id: props.order.order_id,
    });
    Object.assign(
      addressForm,
      procurementAddressForm(detail.value, props.order),
    );
    priceRows.value = (detail.value?.items || []).map((item) => ({
      product_id: item.product_id,
      sku_id: item.sku_id,
      title: item.title,
      price: (Number(item.sale_price || 0) * Number(item.sku_count || 1) / 100).toFixed(2),
    }));
    return detail.value;
  } catch (cause) {
    notify(cause instanceof Error ? cause.message : String(cause));
    return null;
  } finally {
    detailLoading.value = false;
  }
}

function openManualPurchase() {
  const existing = firstPurchase.value || {};
  Object.assign(purchaseForm, {
    platform: existing.platform || "1688",
    purchase_order_no: existing.external_order_id || props.order.purchase_order_no || "",
    purchase_amount: Number(props.order.cost || existing.pay_amount || 0) > 0
      ? (Number(props.order.cost || existing.pay_amount) / 100).toFixed(2)
      : "",
    commission: "0",
    purchase_mode: "手动下单",
    transfer_warehouse: false,
    remember: false,
  });
  menuOpen.value = false;
  purchaseOpen.value = true;
}

async function submitManualPurchase() {
  if (!purchaseForm.purchase_order_no.trim()) {
    notify("请填写采购订单号");
    return;
  }
  const result = await run(
    "补录采购",
    () => call("orders.workbench.manual-purchase", {
      store_id: props.order.store_id,
      order_id: props.order.order_id,
      platform: purchaseForm.platform,
      purchase_order_no: purchaseForm.purchase_order_no.trim(),
      purchase_amount_cents: Math.round(Number(purchaseForm.purchase_amount || 0) * 100),
      commission: Number(purchaseForm.commission || 0),
      purchase_mode: purchaseForm.purchase_mode,
      transfer_warehouse: purchaseForm.transfer_warehouse,
      flag: noteFlag.value,
      remember: purchaseForm.remember,
      goods_title: props.order.item_summary || "",
      goods_count: 1,
    }),
    "采购信息已保存并关联订单",
  );
  if (!result) return;
  purchaseOpen.value = false;
  await ensureDetail(true);
  emit("changed");
}

async function preparePurchase() {
  const result = await run(
    "准备采购",
    () => call("orders.workbench.purchase-focus", {
      store_id: props.order.store_id,
      order_id: props.order.order_id,
      product_ids: props.order.item_product_id ? [props.order.item_product_id] : [],
      count: 1,
      mode: "manual",
    }),
  );
  if (!result) return;
  const detailData = await ensureDetail();
  const savedAddress = detailData?.procurement_address || {};
  const address = [
    savedAddress.user_name || props.order.buyer_name,
    savedAddress.tel_number || props.order.phone,
    [
      savedAddress.province_name,
      savedAddress.city_name,
      savedAddress.county_name,
      savedAddress.street_name,
      savedAddress.detail_info,
    ].filter(Boolean).join("") || props.order.address,
  ]
    .filter(Boolean)
    .join("，");
  if (address) {
    try {
      await copyText(address);
    } catch {
      // Clipboard permission is browser-controlled; the purchase focus is already saved.
    }
  }
  notify("采购信息已准备，收货地址已复制；请到左侧采购账号选择账号完成下单");
}

function openProcurementDialog() {
  menuOpen.value = false;
  procurementDialogOpen.value = true;
}

async function refreshAfterProcurement() {
  await ensureDetail(true);
  emit("changed");
}

async function syncProcurement() {
  const data = await ensureDetail();
  if (!data) return;
  const accounts = data.procurement_accounts || [];
  const account = accounts.find((item) => item.status === "已登录") || accounts[0];
  if (!account) {
    notify("请先在左侧“采购账号”中添加并登录采购账号");
    return;
  }
  const result = await run(
    "获取采购",
    () => call("orders.workbench.sync-procurement", {
      account_id: account.id,
      store_id: props.order.store_id,
      order_id: props.order.order_id,
    }),
    "采购数据已同步并重新匹配",
  );
  if (!result) return;
  await ensureDetail(true);
  emit("changed");
}

async function openProcurementPlatform() {
  const data = await ensureDetail();
  const accounts = data?.procurement_accounts || [];
  const account = accounts.find((item) => item.status === "已登录") || accounts[0];
  if (!account) {
    notify("请先在左侧“采购账号”中添加并登录采购账号");
    menuOpen.value = false;
    return;
  }
  menuOpen.value = false;
  const result = await run(
    "打开采购平台",
    () => call("orders.workbench.browse-procurement", { account_id: account.id }),
    "采购网页已关闭并保存最新采购记录",
  );
  if (!result) return;
  await ensureDetail(true);
  emit("changed");
}

function syncPurchaseNote() {
  askConfirmation({
    title: "同步采购备注",
    description: "将把当前订单的采购信息追加到微信店铺备注。",
    target: `${props.order.store_name} · ${props.order.order_id}`,
    risk: "该操作会写入真实店铺备注。",
    confirmLabel: "确认同步",
    action: async () => {
      const result = await run(
        "同步采购备注",
        () => call("orders.workbench.sync-procurement-note", {
          store_id: props.order.store_id,
          order_id: props.order.order_id,
        }),
        "采购备注已同步到微信订单",
      );
      if (result) emit("changed");
    },
  });
}

function openDelivery() {
  const officialPackages = logisticsPackages.value
    .filter((row) => row.waybill_id)
    .map((row) => ({
      delivery_id: row.delivery_id || "",
      waybill_id: row.waybill_id || "",
    }));
  const supplierPackages = deliveryAction.value.shipments.map((row) => ({
    delivery_id: isOfficialDeliveryCode(row.delivery_id)
      ? row.delivery_id
      : "",
    waybill_id: row.waybill_id,
  }));
  deliveryUsesSupplier.value = !officialPackages.length && supplierPackages.length > 0;
  deliveryPackages.value = officialPackages.length
    ? officialPackages
    : supplierPackages.length
      ? supplierPackages
      : [{ delivery_id: "", waybill_id: "" }];
  menuOpen.value = false;
  deliveryOpen.value = true;
}

function addDeliveryPackage() {
  deliveryPackages.value.push({ delivery_id: "", waybill_id: "" });
}

function removeDeliveryPackage(index) {
  if (deliveryPackages.value.length <= 1) return;
  deliveryPackages.value.splice(index, 1);
}

function submitDelivery() {
  const packages = deliveryPackages.value.filter((row) => (
    row.delivery_id.trim() && row.waybill_id.trim()
  ));
  if (!packages.length) {
    notify("请填写快递公司编码和运单号");
    return;
  }
  if (packages.some((row) => !isOfficialDeliveryCode(row.delivery_id))) {
    notify("请填写微信支持的快递公司编码，例如 SF、ZTO；上家承运商中文名称不能直接提交");
    return;
  }
  askConfirmation({
    title: deliveryAction.value.mode === "manual"
      ? "人工核对后提交官方发货"
      : "提交官方发货 / 回填物流",
    description: deliveryUsesSupplier.value
      ? "已从上家真实采购回读中预填运单号。请核对微信官方快递公司编码后提交。"
      : "将向微信小店提交当前订单的发货信息。",
    target: `${props.order.store_name} · ${props.order.order_id}`,
    risk: deliveryUsesSupplier.value
      ? "运单号来自 controller 回读的上家物流；承运商编码必须转换为微信小店支持的值。"
      : "提交后订单将进入已发货流程，快递公司和运单号必须准确。",
    confirmLabel: "确认官方发货",
    action: async () => {
      const result = await run(
        "提交发货",
        () => call("orders.workbench.delivery-update", {
          store_id: props.order.store_id,
          order_id: props.order.order_id,
          packages,
        }),
        "订单已提交发货",
      );
      if (!result) return;
      await ensureDetail(true);
      deliveryOpen.value = false;
      emit("changed");
    },
  });
}

async function openAddress() {
  menuOpen.value = false;
  if (!(await ensureDetail())) return;
  Object.assign(
    addressForm,
    procurementAddressForm(detail.value, props.order),
  );
  addressRawText.value = procurementAddressText(addressForm);
  addressParseResult.value = null;
  addressOpen.value = true;
}

async function requestProcurementAddress() {
  procurementDialogOpen.value = false;
  await openAddress();
}

async function executeAddressParse(silent = true) {
  if (!addressRawText.value.trim()) {
    if (!silent) notify("请先粘贴完整地址");
    return;
  }
  addressParseLoading.value = true;
  try {
    const result = await call("orders.address.parse", {
      text: addressRawText.value,
    });
    addressParseResult.value = result;
    addressAutoParsed.value = true;
    if (result.address) Object.assign(addressForm, result.address);
    if (!result.valid && !silent) {
      notify(result.reason || "地址仍需人工补全");
    } else if (result.valid && !silent) {
      notify("地址已解析，请核对字段后保存");
    }
  } catch (cause) {
    if (!silent) notify(cause instanceof Error ? cause.message : String(cause));
  } finally {
    addressParseLoading.value = false;
  }
}

async function parseAddress() {
  if (addressParseTimer) clearTimeout(addressParseTimer);
  addressParseTimer = null;
  await executeAddressParse(false);
}

watch(addressRawText, (value) => {
  if (!addressOpen.value || String(value || "").trim().length < 8) return;
  if (addressParseTimer) clearTimeout(addressParseTimer);
  addressAutoParsed.value = false;
  addressParseTimer = setTimeout(() => {
    addressParseTimer = null;
    executeAddressParse(true);
  }, 550);
});

watch(addressOpen, (open) => {
  if (!open && addressParseTimer) {
    clearTimeout(addressParseTimer);
    addressParseTimer = null;
  }
});

onBeforeUnmount(() => {
  if (addressParseTimer) clearTimeout(addressParseTimer);
});

async function submitAddress() {
  if (!addressForm.user_name.trim() || !addressForm.tel_number.trim()) {
    notify("收货人和电话不能为空");
    return;
  }
  const result = await run(
    "保存软件采购地址",
    () => call("orders.address.save-software", {
      store_id: props.order.store_id,
      order_id: props.order.order_id,
      address: { ...addressForm },
    }),
    "采购地址已保存，可继续打开购买",
  );
  if (!result) return;
  addressOpen.value = false;
  await ensureDetail(true);
  emit("changed");
}

function submitOfficialAddress() {
  askConfirmation({
    title: "提交改址申请",
    description: "改址申请会提交到微信小店，最终需要买家同意后才会生效。",
    target: `${props.order.store_name} · ${props.order.order_id}`,
    risk: "该操作会写入真实平台申请记录；不会立即修改买家地址。",
    confirmLabel: "提交申请",
    action: async () => {
      const result = await run(
        "提交改址申请",
        () => call("orders.workbench.address-update", {
          store_id: props.order.store_id,
          order_id: props.order.order_id,
          address: { ...addressForm },
        }),
        "改址申请已提交",
      );
      if (!result) return;
      addressOpen.value = false;
      emit("changed");
    },
  });
}

async function openPrice() {
  menuOpen.value = false;
  if (!(await ensureDetail())) return;
  priceOpen.value = true;
}

function submitPrice() {
  if (!priceRows.value.length) {
    notify("当前订单没有可修改价格的商品明细");
    return;
  }
  askConfirmation({
    title: "修改订单价格",
    description: "修改后的价格会直接提交到微信小店。",
    target: `${props.order.store_name} · ${props.order.order_id}`,
    risk: "该操作会改变真实订单金额，请确认商品、数量和价格无误。",
    confirmLabel: "提交改价",
    action: async () => {
      const result = await run(
        "修改价格",
        () => call("orders.workbench.price-update", {
          store_id: props.order.store_id,
          order_id: props.order.order_id,
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
      if (!result) return;
      priceOpen.value = false;
      emit("changed");
    },
  });
}

async function decryptAddress() {
  menuOpen.value = false;
  const result = await run(
    "解密收货信息",
    () => call("orders.decrypt-address", {
      store_id: props.order.store_id,
      order_id: props.order.order_id,
    }),
    "收货信息已解密并保存到本机",
  );
  if (!result) return;
  await ensureDetail(true);
  emit("changed");
}

async function openAfterSales() {
  menuOpen.value = false;
  afterSalesOpen.value = true;
  afterSalesLoading.value = true;
  try {
    const result = await call("orders.after-sales.list", {
      store_id: props.order.store_id,
      order_id: props.order.order_id,
    });
    afterSales.value = result.after_sales || [];
  } catch (cause) {
    notify(cause instanceof Error ? cause.message : String(cause));
    afterSales.value = [];
  } finally {
    afterSalesLoading.value = false;
  }
}

async function performAfterSale(row, operation, label, data = {}) {
  const result = await run(
    label,
    () => call("orders.after-sales.action", {
      store_id: row.store_id,
      after_sale_id: row.after_sale_order_id,
      operation,
      data: {
        ...data,
        ...(afterSaleMediaIds[row.after_sale_order_id]?.length
          ? { media_files: afterSaleMediaIds[row.after_sale_order_id] }
          : {}),
      },
    }),
    `${label}已提交`,
  );
  if (!result) return;
  afterSaleActionOpen.value = false;
  await openAfterSales();
  emit("changed");
}

function applyAfterSale(row, operation, label) {
  if (["reject", "negotiate", "reship", "update_reship", "accept_exchange"].includes(operation)) {
    Object.assign(afterSaleAction, {
      row,
      operation,
      title: label,
      reject_reason_type: "",
      reject_reason: "",
      merchant_update_type: "",
      update_reason_type: "",
      type: "",
      amount: "",
      merchant_update_desc: "",
      delivery_id: "",
      waybill_id: "",
    });
    afterSaleActionOpen.value = true;
    return;
  }
  askConfirmation({
    title: label,
    description: `即将对售后单 ${row.after_sale_order_id} 执行“${label}”。`,
    target: `${props.order.store_name} · ${props.order.order_id}`,
    risk: "该操作会真实修改微信平台售后状态。",
    confirmLabel: `确认${label}`,
    action: () => performAfterSale(row, operation, label),
  });
}

async function submitAfterSaleAction() {
  const row = afterSaleAction.row;
  const operation = afterSaleAction.operation;
  if (!row || !operation || actionBusy.value) return;
  const data = {};
  try {
    if (operation === "reject") {
      const reasonType = Number(afterSaleAction.reject_reason_type);
      if (!Number.isInteger(reasonType)) {
        throw new Error("拒绝原因类型必须填写数字");
      }
      data.reject_reason_type = reasonType;
      data.reject_reason = afterSaleAction.reject_reason.trim();
    } else if (operation === "negotiate") {
      const merchantUpdateType = Number(afterSaleAction.merchant_update_type);
      const updateReasonType = Number(afterSaleAction.update_reason_type);
      const type = Number(afterSaleAction.type);
      const amount = Number(afterSaleAction.amount || 0);
      if (
        !Number.isInteger(merchantUpdateType)
        || !Number.isInteger(updateReasonType)
        || !Number.isInteger(type)
        || !Number.isInteger(amount)
      ) {
        throw new Error("协商类型、原因类型、售后类型和金额必须是整数");
      }
      Object.assign(data, {
        merchant_update_type: merchantUpdateType,
        update_reason_type: updateReasonType,
        type,
        amount,
        merchant_update_desc: afterSaleAction.merchant_update_desc.trim(),
      });
    } else {
      const deliveryId = afterSaleAction.delivery_id.trim();
      const waybillId = afterSaleAction.waybill_id.trim();
      if (!deliveryId || !waybillId) {
        throw new Error("请填写快递公司编码和运单号");
      }
      Object.assign(data, {
        delivery_id: deliveryId,
        waybill_id: waybillId,
      });
    }
  } catch (cause) {
    notify(cause instanceof Error ? cause.message : String(cause));
    return;
  }
  await performAfterSale(row, operation, afterSaleAction.title, data);
}

function chooseAfterSaleProof(row) {
  if (afterSaleProofBusy[row.after_sale_order_id]) return;
  afterSaleProofTarget.value = row;
  if (afterSaleProofInput.value) {
    afterSaleProofInput.value.value = "";
    afterSaleProofInput.value.click();
  }
}

async function uploadAfterSaleProof(event) {
  const row = afterSaleProofTarget.value;
  const files = Array.from(event.target.files || []).slice(0, 9);
  event.target.value = "";
  if (!row || !files.length || afterSaleProofBusy[row.after_sale_order_id]) return;
  afterSaleProofBusy[row.after_sale_order_id] = true;
  try {
    const payloadFiles = await Promise.all(files.map(async (file) => {
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
      files: payloadFiles,
    });
    afterSaleMediaIds[row.after_sale_order_id] = result.media_ids || [];
    notify(`售后凭证上传完成：${result.count || 0} 张`);
  } catch (cause) {
    notify(cause instanceof Error ? cause.message : String(cause));
  } finally {
    afterSaleProofBusy[row.after_sale_order_id] = false;
    afterSaleProofTarget.value = null;
  }
}

async function refreshShippingOptions() {
  menuOpen.value = false;
  const result = await run(
    "刷新电子面单配置",
    () => call("orders.workbench.shipping-options", {
      store_id: props.order.store_id,
    }),
    "电子面单账号和模板已刷新",
  );
  if (result) notify(`面单账号 ${result.accounts?.length || 0} 个，模板 ${result.templates?.length || 0} 个`);
}

function requestCreateShipment() {
  menuOpen.value = false;
  askConfirmation({
    title: "生成电子面单",
    description: "将占用当前店铺电子面单库存并生成运单号；确认打印前不会自动发货。",
    target: `${props.order.store_name} · ${props.order.order_id}`,
    risk: "该操作会真实调用微信小店电子面单接口。",
    confirmLabel: "生成面单",
    action: async () => {
      const result = await run(
        "生成电子面单",
        () => call("orders.workbench.shipment-create", {
          store_id: props.order.store_id,
          order_id: props.order.order_id,
        }),
        "电子面单已生成",
      );
      if (!result) return;
      shipment.value = result.shipment || null;
      await ensureDetail(true);
      emit("changed");
    },
  });
}

function requestCompleteShipment() {
  menuOpen.value = false;
  askConfirmation({
    title: "完成电子面单发货",
    description: "将确认当前电子面单已打印，并把运单号提交到微信小店订单。",
    target: `${props.order.store_name} · ${props.order.order_id}`,
    risk: "提交后订单会进入已发货流程，请先核对快递和运单号。",
    confirmLabel: "确认并发货",
    action: async () => {
      const result = await run(
        "完成电子面单发货",
        () => call("orders.workbench.shipment-complete", {
          store_id: props.order.store_id,
          order_id: props.order.order_id,
        }),
        "订单已通过电子面单完成发货",
      );
      if (!result) return;
      shipment.value = result.shipment || null;
      await ensureDetail(true);
      emit("changed");
    },
  });
}

async function openLogs() {
  menuOpen.value = false;
  logsOpen.value = true;
  logsLoading.value = true;
  try {
    const result = await call("orders.workbench.logs", { limit: 1000 });
    orderLogs.value = (result.logs || []).filter((row) => (
      JSON.stringify(row).includes(props.order.order_id)
      || (
        String(row.store_id || "") === String(props.order.store_id)
        && String(row.order_id || "") === String(props.order.order_id)
      )
    ));
  } catch (cause) {
    notify(cause instanceof Error ? cause.message : String(cause));
    orderLogs.value = [];
  } finally {
    logsLoading.value = false;
  }
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

function runMenuAction(action) {
  menuOpen.value = false;
  if (action === "refresh") emit("refresh");
  if (action === "official") emit("official");
  if (action === "mark") emit("mark");
  if (action === "address") openAddress();
  if (action === "price") openPrice();
  if (action === "decrypt") decryptAddress();
  if (action === "logs") openLogs();
  if (action === "procurement") openProcurementPlatform();
}
</script>

<template>
  <tr
    class="order-a-row"
    :class="{ selected }"
    @contextmenu.prevent.stop="emit('contextmenu', $event)"
  >
    <td class="col-check">
      <input
        type="checkbox"
        :checked="selected"
        @change="emit('select', $event.target.checked)"
      />
    </td>
    <td class="col-order">
      <div class="order-identity">
        <div>
          <strong>{{ order.store_name }}</strong>
          <em :class="['status-chip', statusClass]">{{ displayStatus }}</em>
        </div>
        <time>{{ formatDateTime(order.create_time) }}</time>
        <div class="order-number">
          <button
            class="order-link"
            type="button"
            :disabled="openingOfficial"
            :title="openingOfficial ? '正在打开官方订单' : '在微信官方后台打开订单'"
            @click="emit('official')"
          >{{ openingOfficial ? "正在打开…" : order.order_id }}</button>
          <button type="button" title="复制订单号" @click="copyOrderId">复制</button>
        </div>
        <div class="row-quick-actions">
          <button
            type="button"
            :title="refreshing ? '正在刷新订单' : '刷新订单'"
            :disabled="refreshing"
            @click="emit('refresh')"
          >
            <Icon name="refresh" :size="14" :class="{ spinning: refreshing }" />
            刷新
          </button>
          <button
            type="button"
            title="标记订单"
            :class="{ marked: order.is_marked }"
            @click="emit('mark')"
          >
            <Icon name="star" :size="14" />
            {{ order.is_marked ? "取消标记" : "标记" }}
          </button>
        </div>
      </div>
    </td>
    <td class="col-product">
      <div class="product-summary">
        <button
          v-if="order.item_thumb_url"
          class="product-thumb"
          type="button"
          title="查看商品大图"
          @click="emit('preview', order.item_thumb_url)"
        >
          <img :src="order.item_thumb_url" alt="商品图片" />
        </button>
        <span v-else class="product-thumb empty">暂无图片</span>
        <div class="product-copy">
          <button
            class="product-title-button"
            type="button"
            :disabled="!order.item_product_id"
            :title="order.item_product_id ? `定位商品 ${order.item_product_id}` : '订单未返回平台商品 ID'"
            @click="emit('product', order)"
          >{{ order.item_summary || "暂无商品信息" }}</button>
          <small>数量 ×1 · 商品 ID：{{ order.item_product_id || "暂无数据" }}</small>
          <div class="product-prices">
            <span>实收 <b>{{ formatMoney(order.merchant_received || order.total_amount) }}</b></span>
            <span>优惠 {{ formatMoney(order.discount_amount) }}</span>
            <span>服务费 {{ formatMoney(order.service_fee) }}</span>
          </div>
        </div>
      </div>
    </td>
    <td class="col-money">
      <div class="money-stack">
        <small>销售额</small>
        <strong>{{ formatMoney(order.total_amount) }}</strong>
        <span>{{ order.cost ? `成本 ${formatMoney(order.cost)}` : "成本未填写" }}</span>
        <em>{{ order.profit ? `利润 ${formatMoney(order.profit)}` : "利润待核算" }}</em>
      </div>
    </td>
    <td class="col-recipient">
      <div class="recipient-stack">
        <strong>{{ order.buyer_name || "暂无数据" }}　{{ maskedPhone(order.phone) }}</strong>
        <span>{{ order.address || "暂无收货地址" }}</span>
        <div class="recipient-actions">
          <button type="button" @click="runMenuAction('decrypt')">解密</button>
          <button type="button" @click="copyAddress">复制地址</button>
          <button type="button" @click="openAddress">修改地址</button>
        </div>
      </div>
    </td>
    <td class="col-purchase">
      <div class="purchase-stack">
        <span class="procurement-state" :class="`state-${displayProcurementState || 'not_required'}`">{{ procurementStateLabel }}</span>
        <template v-if="firstPurchase">
          <strong>{{ firstPurchase.platform || "采购平台" }} · {{ firstPurchase.account_label || "采购账号" }}</strong>
          <span class="mono">{{ firstPurchase.external_order_id || order.purchase_order_no || "采购单号暂无" }}</span>
          <span v-if="Number(firstPurchase.pay_amount || 0)">实付 {{ formatMoney(firstPurchase.pay_amount) }}</span>
          <span>{{ firstPurchase.status_text || "采购状态暂无" }}</span>
          <span v-if="firstPurchase.mail_no" class="mono supplier-tracking">上家物流 {{ firstPurchase.mail_no }}</span>
          <span v-if="purchaseCount > 1">另有 {{ purchaseCount - 1 }} 条采购关联</span>
        </template>
        <template v-else>
          <span>{{ order.purchase_order_no || "尚未关联采购单" }}</span>
        </template>
        <button
          v-if="procurementActionLabel"
          class="btn blue"
          type="button"
          :class="{ danger: order.procurement_state === 'error' }"
          @click="openProcurementDialog"
        >{{ procurementActionLabel }}</button>
      </div>
    </td>
    <td class="col-fulfill">
      <div class="fulfill-stack">
        <strong :class="{ overdue: shippingCountdown(order, nowSeconds).startsWith('已超时') }">
          {{ shippingCountdown(order, nowSeconds) || (Number(order.status) === 20 ? "—" : `店铺：${displayStatus}`) }}
        </strong>
        <span>{{ fulfillmentStageLabel(order) || "履约状态暂无" }}</span>
        <span>{{ order.purchase_order_no ? `采购 ${order.purchase_order_no}` : "采购未关联" }}</span>
        <span v-if="order.pickup_code">取件码 <b class="mono">{{ order.pickup_code }}</b></span>
      </div>
    </td>
    <td class="col-logistics">
      <div class="logistics-stack">
        <template v-if="logisticsPackages.length">
          <div
            v-for="(packageRow, index) in logisticsPackages"
            :key="`${packageRow.waybill_id}:${index}`"
            class="waybill-row"
          >
            <button
              class="waybill-link"
              type="button"
              :title="`查看官方物流轨迹 ${packageRow.waybill_id}`"
              @click="openLogistics(packageRow)"
            >{{ packageRow.waybill_id || "无运单号" }}</button>
            <button
              class="waybill-copy"
              type="button"
              title="复制完整物流单号"
              @click="copyWaybill(packageRow)"
            >复制</button>
          </div>
          <span>{{ logisticsPackages[0].delivery_name || logisticsPackages[0].delivery_id || carrier }} · {{ displayStatus }}</span>
        </template>
        <template v-else>
          <strong class="empty">暂无物流</strong>
          <span>等待物流单号</span>
        </template>
        <div
          v-for="row in supplierShippingRows"
          :key="`supplier:${row.waybill_id}`"
          class="supplier-waybill"
        >
          <strong>上家已发货</strong>
          <button
            class="waybill-link"
            type="button"
            title="复制上家物流单号"
            @click="copyWaybill(row)"
          >{{ row.waybill_id }}</button>
          <span>{{ row.carrier || row.delivery_id || "承运商待核对" }} · {{ row.status_text || "已回读" }}</span>
        </div>
        <span v-if="deliveryAction.reason && deliveryAction.mode === 'wait'">
          {{ deliveryAction.reason }}
        </span>
        <span v-if="shipment?.waybill_id">电子面单 {{ shipment.waybill_id }}</span>
        <button
          v-if="deliveryAction.mode !== 'wait'"
          class="btn"
          :class="deliveryAction.mode === 'manual' ? 'danger' : 'soft'"
          type="button"
          :disabled="Boolean(actionBusy)"
          @click="openDelivery"
        >{{ deliveryAction.label }}</button>
      </div>
    </td>
    <td class="col-notes">
      <div class="note-stack">
        <span><b>店铺备注</b>{{ order.merchant_notes || "—" }}</span>
        <span><b>软件备注</b>{{ order.software_note || "—" }}</span>
        <span><b>官方旗帜</b>{{ noteFlagCode ? noteFlag : (order.merchant_notes ? "无色" : "无旗帜 · 无备注") }}</span>
      </div>
      <div class="note-actions">
        <button class="memo-button" type="button" @click="emit('note')">
          <span
            class="note-color-swatch"
            :class="noteFlagClass"
            :title="noteFlagTitle"
            :aria-label="noteFlagTitle"
          />
          <Icon name="tag" :size="14" />
          编辑备注
        </button>
        <button class="btn" type="button" :disabled="Boolean(actionBusy)" @click="syncPurchaseNote">
          {{ actionBusy === "同步采购备注" ? "同步中…" : "同步采购备注" }}
        </button>
      </div>
    </td>
    <td class="col-action">
      <div class="operation-stack">
        <button class="btn" type="button" :disabled="openingOfficial" @click="emit('official')">
          {{ openingOfficial ? "打开中…" : "官方订单" }}
        </button>
        <button v-if="canViewAfterSales" class="btn" type="button" @click="openAfterSales">
          售后{{ afterSaleCount ? ` ${afterSaleCount}` : "" }}
        </button>
        <button class="btn primary" type="button" @click="menuOpen = true">更多处理</button>
      </div>
    </td>
  </tr>

  <input
    ref="afterSaleProofInput"
    class="hidden-file-input"
    type="file"
    hidden
    aria-hidden="true"
    tabindex="-1"
    accept=".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp"
    multiple
    @change="uploadAfterSaleProof"
  />

  <ModalDialog :open="menuOpen" title="订单处理" :subtitle="`${order.store_name} · ${order.order_id}`" width="620px" @close="menuOpen = false">
    <div class="action-menu-grid">
      <button type="button" @click="runMenuAction('official')"><b>官方订单</b><span>打开微信官方订单详情</span></button>
      <button type="button" @click="runMenuAction('refresh')"><b>刷新订单</b><span>从真实后台重新同步当前店铺</span></button>
      <button type="button" @click="runMenuAction('mark')"><b>{{ order.is_marked ? "取消标记" : "标记订单" }}</b><span>更新澜琅本地星标</span></button>
      <button type="button" @click="runMenuAction('address')"><b>修改地址</b><span>解析并保存采购可用地址；默认不修改微信官方地址</span></button>
      <button type="button" @click="runMenuAction('price')"><b>修改价格</b><span>修改商品价格或运费</span></button>
      <button type="button" @click="runMenuAction('decrypt')"><b>解密收货信息</b><span>解密并保存到本机</span></button>
      <button
        v-if="procurementActionLabel"
        type="button"
        @click="openProcurementDialog"
      ><b>{{ procurementActionLabel }}</b><span>关联商品SKU、打开官方采购页或核实异常</span></button>
      <button type="button" @click="runMenuAction('logs')"><b>相关日志</b><span>查看当前订单的任务与异常记录</span></button>
      <button
        v-if="deliveryAction.mode !== 'wait'"
        type="button"
        :disabled="Boolean(actionBusy)"
        @click="openDelivery"
      ><b>{{ deliveryAction.label }}</b><span>{{ deliveryAction.reason }}</span></button>
      <button type="button" :disabled="Boolean(actionBusy)" @click="refreshShippingOptions"><b>刷新电子面单配置</b><span>读取当前店铺面单账号和模板</span></button>
      <button type="button" :disabled="Boolean(actionBusy)" @click="requestCreateShipment"><b>生成电子面单</b><span>真实取号，确认打印前不发货</span></button>
      <button type="button" :disabled="Boolean(actionBusy)" @click="requestCompleteShipment"><b>完成面单发货</b><span>确认已打印并提交运单号</span></button>
    </div>
  </ModalDialog>

  <ModalDialog :open="purchaseOpen" title="补录采购订单" :subtitle="`${order.store_name} · ${order.order_id}`" width="620px" @close="purchaseOpen = false">
    <div class="form-grid">
      <div class="field">
        <label>采购平台</label>
        <select v-model="purchaseForm.platform" class="select">
          <option>1688</option><option>淘宝</option><option>拼多多</option><option>其他</option>
        </select>
      </div>
      <div class="field">
        <label>采购模式</label>
        <select v-model="purchaseForm.purchase_mode" class="select">
          <option>手动下单</option><option>代发下单</option><option>中转仓</option>
        </select>
      </div>
      <div class="field full">
        <label>采购订单号</label>
        <input v-model="purchaseForm.purchase_order_no" class="input" placeholder="填写真实采购订单号" />
      </div>
      <div class="field">
        <label>采购金额（元）</label>
        <input v-model="purchaseForm.purchase_amount" class="input" inputmode="decimal" placeholder="0.00" />
      </div>
      <div class="field">
        <label>佣金比例（%）</label>
        <input v-model="purchaseForm.commission" class="input" inputmode="decimal" />
      </div>
      <label class="check-row"><input v-model="purchaseForm.transfer_warehouse" type="checkbox" /> 使用中转仓</label>
      <label class="check-row"><input v-model="purchaseForm.remember" type="checkbox" /> 记住本次采购设置</label>
    </div>
    <template #footer>
      <button class="btn" type="button" :disabled="Boolean(actionBusy)" @click="purchaseOpen = false">取消</button>
      <button class="btn primary" type="button" :disabled="Boolean(actionBusy)" @click="submitManualPurchase">
        {{ actionBusy === "补录采购" ? "保存中…" : "保存关联" }}
      </button>
    </template>
  </ModalDialog>

  <ModalDialog :open="deliveryOpen" :title="deliveryAction.label || '官方发货 / 更新物流'" :subtitle="`${order.store_name} · ${order.order_id}`" width="680px" @close="deliveryOpen = false">
    <div v-if="deliveryUsesSupplier" class="delivery-source-note">
      <strong>已使用上家真实回读物流</strong>
      <span>运单号来自 controller；快递公司编码仍需确认成微信小店支持的编码。</span>
    </div>
    <div class="delivery-list">
      <div v-for="(item, index) in deliveryPackages" :key="index" class="delivery-row">
        <div class="field"><label>快递公司编码</label><input v-model="item.delivery_id" class="input" placeholder="例如 SF" /></div>
        <div class="field"><label>运单号</label><input v-model="item.waybill_id" class="input" placeholder="填写真实运单号" /></div>
        <button v-if="deliveryPackages.length > 1" class="btn danger" type="button" @click="removeDeliveryPackage(index)">删除</button>
      </div>
      <button class="btn soft" type="button" @click="addDeliveryPackage">增加包裹</button>
    </div>
    <p class="dialog-risk">提交后会真实写入微信小店，请核对快递公司编码和运单号。</p>
    <template #footer>
      <button class="btn" type="button" :disabled="Boolean(actionBusy)" @click="deliveryOpen = false">取消</button>
      <button class="btn primary" type="button" :disabled="Boolean(actionBusy)" @click="submitDelivery">提交发货</button>
    </template>
  </ModalDialog>

  <ModalDialog :open="addressOpen" title="修改采购地址" :subtitle="`${order.store_name} · ${order.order_id}`" width="920px" @close="addressOpen = false">
    <div class="address-import-panel">
      <div class="address-import-head">
        <div>
          <strong>整段地址智能解析</strong>
          <span>粘贴后将自动识别姓名、电话、省、市、区县和详细地址</span>
        </div>
        <span
          class="parse-state"
          :class="{
            ready: addressParseResult?.valid,
            warning: addressParseResult && !addressParseResult.valid,
          }"
        >
          {{ addressParseLoading ? "正在自动解析…" : addressParseResult?.valid ? "已自动解析，可继续核对" : addressParseResult ? "已解析，部分字段需补全" : "等待粘贴地址" }}
        </span>
      </div>
      <textarea
        v-model="addressRawText"
        class="input address-raw-textarea"
        rows="8"
        placeholder="支持空格、换行、标签格式，例如：&#10;收货人：张三&#10;电话：13800138000&#10;河北省 邯郸市 邯山区 某街道 某小区 1号楼 101"
      ></textarea>
      <div class="address-import-actions">
        <span>{{ addressAutoParsed ? "输入停止后已自动解析，可点击重新解析" : "输入停止约半秒后自动解析" }}</span>
        <button class="btn soft" type="button" :disabled="addressParseLoading" @click="parseAddress">
          {{ addressParseLoading ? "解析中…" : "重新解析" }}
        </button>
      </div>
      <p v-if="addressParseResult && !addressParseResult.valid" class="dialog-risk address-parse-error">
        {{ addressParseResult.reason }}
      </p>
    </div>
    <div class="form-grid">
      <div class="field"><label>收货人</label><input v-model="addressForm.user_name" class="input" /></div>
      <div class="field"><label>联系电话</label><input v-model="addressForm.tel_number" class="input" /></div>
      <div class="field"><label>省份</label><input v-model="addressForm.province_name" class="input" /></div>
      <div class="field"><label>城市</label><input v-model="addressForm.city_name" class="input" /></div>
      <div class="field"><label>区县</label><input v-model="addressForm.county_name" class="input" /></div>
      <div class="field"><label>街道</label><input v-model="addressForm.street_name" class="input" /></div>
      <div class="field full"><label>详细地址</label><input v-model="addressForm.detail_info" class="input" /></div>
    </div>
    <p class="dialog-risk">“保存软件地址”只用于采购门禁和软件记录，不会修改微信官方订单地址。</p>
    <template #footer>
      <button class="btn" type="button" :disabled="Boolean(actionBusy)" @click="addressOpen = false">取消</button>
      <button class="btn" type="button" :disabled="Boolean(actionBusy)" @click="submitOfficialAddress">另行提交微信改址</button>
      <button class="btn primary" type="button" :disabled="Boolean(actionBusy)" @click="submitAddress">保存软件地址</button>
    </template>
  </ModalDialog>

  <ModalDialog :open="priceOpen" title="修改订单价格" :subtitle="`${order.store_name} · ${order.order_id}`" width="760px" @close="priceOpen = false">
    <div class="price-list">
      <label v-for="row in priceRows" :key="`${row.product_id}:${row.sku_id}`">
        <span><b>{{ row.title || row.product_id }}</b><small>商品 ID：{{ row.product_id }}</small></span>
        <input v-model="row.price" class="input" inputmode="decimal" />
      </label>
      <div v-if="!priceRows.length" class="empty-dialog">当前订单没有可修改价格的商品明细。</div>
    </div>
    <div class="express-row">
      <label><input v-model="changeExpress" type="checkbox" /> 同时修改运费</label>
      <input v-model="expressFee" class="input" :disabled="!changeExpress" inputmode="decimal" placeholder="运费（元）" />
    </div>
    <template #footer>
      <button class="btn" type="button" :disabled="Boolean(actionBusy)" @click="priceOpen = false">取消</button>
      <button class="btn primary" type="button" :disabled="Boolean(actionBusy)" @click="submitPrice">提交改价</button>
    </template>
  </ModalDialog>

  <ModalDialog :open="afterSalesOpen" title="当前订单售后" :subtitle="`${order.store_name} · ${order.order_id}`" width="880px" @close="afterSalesOpen = false">
    <div v-if="afterSalesLoading" class="empty-dialog">正在读取售后记录…</div>
    <div v-else-if="!afterSales.length" class="empty-dialog">当前订单暂无售后记录。</div>
    <div v-else class="after-sale-list">
      <article v-for="row in afterSales" :key="row.after_sale_order_id">
        <div>
          <strong>{{ row.status_text || "状态暂无" }}</strong>
          <span>{{ row.after_sale_type || "类型暂无" }} · {{ row.reason_text || "原因暂无" }}</span>
          <small>{{ row.after_sale_order_id }} · {{ formatMoney(row.refund_amount) }}</small>
        </div>
        <div class="after-sale-actions">
          <button
            class="btn soft"
            type="button"
            :disabled="Boolean(actionBusy) || Boolean(afterSaleProofBusy[row.after_sale_order_id])"
            @click="chooseAfterSaleProof(row)"
          >
            {{ afterSaleProofBusy[row.after_sale_order_id] ? "上传中…" : "上传凭证" }}
          </button>
          <button class="btn" type="button" :disabled="Boolean(actionBusy)" @click="applyAfterSale(row, 'accept', '同意售后')">同意</button>
          <button class="btn danger" type="button" :disabled="Boolean(actionBusy)" @click="applyAfterSale(row, 'reject', '拒绝售后')">拒绝</button>
          <button class="btn" type="button" :disabled="Boolean(actionBusy)" @click="applyAfterSale(row, 'negotiate', '协商售后')">协商</button>
          <button v-if="String(row.after_sale_type || '').includes('RESHIP')" class="btn" type="button" :disabled="Boolean(actionBusy)" @click="applyAfterSale(row, 'reship', '补寄发货')">补寄</button>
          <button v-if="String(row.after_sale_type || '').includes('RESHIP')" class="btn" type="button" :disabled="Boolean(actionBusy)" @click="applyAfterSale(row, 'update_reship', '更新补寄物流')">更新补寄</button>
          <button v-if="String(row.after_sale_type || '').includes('EXCHANGE')" class="btn" type="button" :disabled="Boolean(actionBusy)" @click="applyAfterSale(row, 'accept_exchange', '同意换货')">同意换货</button>
          <button v-if="String(row.after_sale_type || '').includes('EXCHANGE')" class="btn danger" type="button" :disabled="Boolean(actionBusy)" @click="applyAfterSale(row, 'reject_exchange', '拒绝换货')">拒绝换货</button>
        </div>
        <small v-if="afterSaleMediaIds[row.after_sale_order_id]?.length">
          已上传 {{ afterSaleMediaIds[row.after_sale_order_id].length }} 张凭证
        </small>
      </article>
    </div>
    <template #footer><button class="btn" type="button" @click="afterSalesOpen = false">关闭</button></template>
  </ModalDialog>

    <ModalDialog
      :open="afterSaleActionOpen"
      :title="afterSaleAction.title"
      :subtitle="afterSaleAction.row?.after_sale_order_id || ''"
      width="680px"
      :busy="Boolean(actionBusy)"
      @close="afterSaleActionOpen = false"
    >
      <div class="form-grid">
        <template v-if="afterSaleAction.operation === 'reject'">
          <div class="field">
            <label>拒绝原因类型编号</label>
            <input v-model="afterSaleAction.reject_reason_type" class="input" inputmode="numeric" />
          </div>
          <div class="field full">
            <label>拒绝说明</label>
            <input v-model="afterSaleAction.reject_reason" class="input" />
          </div>
        </template>
        <template v-else-if="afterSaleAction.operation === 'negotiate'">
          <div class="field"><label>协商类型 1/2/3</label><input v-model="afterSaleAction.merchant_update_type" class="input" inputmode="numeric" /></div>
          <div class="field"><label>协商原因类型编号</label><input v-model="afterSaleAction.update_reason_type" class="input" inputmode="numeric" /></div>
          <div class="field"><label>修改后售后类型 1/2/3/4</label><input v-model="afterSaleAction.type" class="input" inputmode="numeric" /></div>
          <div class="field"><label>金额（分）</label><input v-model="afterSaleAction.amount" class="input" inputmode="numeric" /></div>
          <div class="field full"><label>协商说明</label><input v-model="afterSaleAction.merchant_update_desc" class="input" /></div>
        </template>
        <template v-else>
          <div class="field"><label>快递公司编码</label><input v-model="afterSaleAction.delivery_id" class="input" /></div>
          <div class="field"><label>运单号</label><input v-model="afterSaleAction.waybill_id" class="input" /></div>
        </template>
      </div>
      <p v-if="afterSaleMediaIds[afterSaleAction.row?.after_sale_order_id]?.length">
        将同时提交已上传的 {{ afterSaleMediaIds[afterSaleAction.row.after_sale_order_id].length }} 张凭证
      </p>
      <template #footer>
        <button class="btn" type="button" :disabled="Boolean(actionBusy)" @click="afterSaleActionOpen = false">取消</button>
        <button class="btn primary" type="button" :disabled="Boolean(actionBusy)" @click="submitAfterSaleAction">{{ actionBusy ? "提交中…" : "确认提交" }}</button>
      </template>
    </ModalDialog>

  <ModalDialog :open="logsOpen" title="订单相关日志" :subtitle="`${order.store_name} · ${order.order_id}`" width="900px" @close="logsOpen = false">
    <div v-if="logsLoading" class="empty-dialog">正在读取日志…</div>
    <div v-else-if="!orderLogs.length" class="empty-dialog">暂无与当前订单直接关联的任务日志。</div>
    <div v-else class="order-log-list">
      <article v-for="(row, index) in orderLogs" :key="`${row.id || index}`">
        <strong>{{ row.operation || "操作暂无" }}</strong>
        <span>{{ row.started_at || "时间暂无" }} · {{ row.status || "状态暂无" }}</span>
        <p>{{ row.message || "结果暂无" }}</p>
      </article>
    </div>
    <template #footer><button class="btn" type="button" @click="logsOpen = false">关闭</button></template>
  </ModalDialog>

  <ModalDialog
    :open="logisticsOpen"
    title="官方物流轨迹"
    :subtitle="`${order.store_name} · ${order.order_id} · ${logisticsTarget?.waybill_id || ''}`"
    width="760px"
    @close="logisticsOpen = false"
  >
    <div v-if="logisticsLoading" class="empty-dialog">正在读取微信小店官方物流轨迹…</div>
    <div v-else-if="logisticsError" class="logistics-error">{{ logisticsError }}</div>
    <div v-else-if="logisticsData" class="logistics-detail">
      <header>
        <strong>{{ logisticsData.package?.status || "物流状态暂无" }}</strong>
        <span>{{ logisticsData.package?.delivery_name || logisticsData.package?.delivery_id }} · {{ logisticsData.waybill_id }}</span>
        <small>{{ logisticsData.source }} · {{ logisticsData.checked_at }}</small>
      </header>
      <ol v-if="logisticsData.package?.traces?.length">
        <li v-for="(trace, index) in logisticsData.package.traces" :key="`${trace.time}:${index}`">
          <time>{{ trace.time || "时间暂无" }}</time>
          <span>{{ trace.description || "轨迹描述暂无" }}</span>
        </li>
      </ol>
      <div v-else class="empty-dialog">官方暂无轨迹节点</div>
    </div>
    <template #footer>
      <button class="btn" type="button" @click="logisticsOpen = false">关闭</button>
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
      <button class="btn primary" type="button" :disabled="confirmBusy" @click="confirmPendingAction">
        {{ confirmBusy ? "处理中…" : confirmDialog.confirmLabel }}
      </button>
    </template>
  </ModalDialog>

  <OrderProcurementDialog
    :open="procurementDialogOpen"
    :order="order"
    @close="procurementDialogOpen = false"
    @changed="refreshAfterProcurement"
    @toast="notify"
    @request-address="requestProcurementAddress"
  />
</template>

<style scoped>
.hidden-file-input {
  display: none !important;
}
</style>

<style scoped>
.order-a-row td {
  height: 132px;
  padding: 10px 9px;
  border-bottom: 1px solid #e4ebf1;
  vertical-align: middle;
}
.order-a-row.selected td { background: #f2fbf7; }
.order-a-row:hover td { background: #f8fbfe; }
.col-check {
  position: sticky;
  left: 0;
  z-index: 3;
  width: 38px;
  background: #fff;
  text-align: center;
}
.col-order {
  position: sticky;
  left: 38px;
  z-index: 3;
  width: 232px;
  background: #fff;
  box-shadow: 8px 0 14px -13px rgba(18, 48, 68, 0.72);
}
.order-a-row.selected .col-check,
.order-a-row.selected .col-order { background: #f2fbf7; }
.order-a-row:hover .col-check,
.order-a-row:hover .col-order { background: #f8fbfe; }
.col-product { width: 355px; }
.col-money { width: 140px; }
.col-recipient { width: 220px; }
.col-purchase { width: 238px; }
.col-fulfill { width: 154px; }
.col-logistics { width: 166px; }
.col-notes { width: 230px; }
.col-action { width: 126px; }

.order-identity {
  position: relative;
  min-height: 108px;
  display: grid;
  align-content: center;
  gap: 4px;
}
.order-identity > div:first-child { display: flex; align-items: center; gap: 7px; }
.order-identity strong { color: #17324b; font-size: 12px; }
.status-chip {
  width: fit-content;
  padding: 2px 6px;
  border-radius: 3px;
  color: #18516d;
  background: #dff3fc;
  font-size: 9px;
  font-style: normal;
}
.status-售后退款完成,
.status-售后中 { color: #982732; background: #ffe2e5; }
.status-已取消 { color: #6d7884; background: #e9edf0; }
.status-已完成 { color: #126548; background: #ddf7eb; }
.order-identity time { color: #748698; font-size: 10px; }
.order-number { display: flex; align-items: center; gap: 5px; min-width: 0; }
.order-link {
  min-width: 0;
  padding: 0;
  overflow: hidden;
  border: 0;
  color: #0b8b5d;
  background: transparent;
  font-size: 10px;
  text-align: left;
  text-decoration: underline;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.order-number button:not(.order-link) {
  padding: 0;
  border: 0;
  color: #8998a6;
  background: transparent;
  font-size: 9px;
}
.row-quick-actions { display: flex; gap: 4px; }
.row-quick-actions button {
  min-height: 24px;
  padding: 0 7px;
  display: inline-flex;
  align-items: center;
  gap: 3px;
  border: 1px solid #d6e0e8;
  border-radius: 4px;
  color: #627587;
  background: #fff;
  font-size: 9px;
}
.row-quick-actions button.marked { color: #bd8100; background: #fff8df; }

.product-summary {
  display: grid;
  grid-template-columns: 76px minmax(0, 1fr);
  gap: 10px;
  align-items: center;
}
.product-thumb {
  width: 76px;
  height: 76px;
  padding: 0;
  display: grid;
  place-items: center;
  overflow: hidden;
  border: 1px solid #dfe6ec;
  border-radius: 7px;
  background: #f4f7f9;
}
.product-thumb img { width: 100%; height: 100%; object-fit: cover; }
.product-thumb.empty { color: #94a2af; font-size: 9px; }
.product-copy p,
.product-title-button {
  margin: 0 0 5px;
  display: -webkit-box;
  overflow: hidden;
  width: 100%;
  padding: 0;
  border: 0;
  color: #213648;
  background: transparent;
  font-size: 11px;
  font-weight: 600;
  line-height: 1.45;
  text-align: left;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.product-title-button:hover:not(:disabled),
.product-title-button:focus-visible {
  color: #087f59;
  text-decoration: underline;
}
.product-title-button:disabled { cursor: not-allowed; color: #87949e; }
.product-copy small { color: #738597; font-size: 9px; }
.product-prices { margin-top: 7px; display: flex; flex-wrap: wrap; gap: 4px 9px; color: #5f7284; font-size: 9px; }
.product-prices b { color: #11724e; font-size: 13px; }

.money-stack { display: grid; gap: 4px; }
.money-stack small,
.money-stack span,
.money-stack em { color: #758697; font-size: 9px; font-style: normal; }
.money-stack strong { color: #11724e; font-size: 17px; }
.money-stack em { width: fit-content; padding: 2px 5px; border-radius: 3px; color: #99620f; background: #fff6df; }

.recipient-stack { display: grid; gap: 5px; }
.recipient-stack strong { color: #2d4356; font-size: 11px; }
.recipient-stack > span {
  display: -webkit-box;
  overflow: hidden;
  color: #6f8192;
  font-size: 9px;
  line-height: 1.45;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.recipient-actions { display: flex; flex-wrap: wrap; gap: 4px; }
.recipient-actions button {
  height: 23px;
  padding: 0 7px;
  border: 1px solid #d4dfe8;
  border-radius: 4px;
  color: #326f9d;
  background: #f8fbfd;
  font-size: 9px;
}

.purchase-stack,
.fulfill-stack,
.logistics-stack,
.operation-stack { display: grid; gap: 5px; }
.purchase-stack strong { color: #8d5d13; font-size: 10px; }
.purchase-stack strong.warning { color: #bb7b13; }
.procurement-state {
  width: fit-content;
  padding: 2px 7px;
  border-radius: 99px;
  color: #6a7783;
  background: #edf2f5;
  font-size: 9px;
}
.procurement-state.state-unlinked,
.procurement-state.state-wait_purchase { color: #9a6710; background: #fff4df; }
.procurement-state.state-error { color: #b13a45; background: #fff0f1; }
.procurement-state.state-wait_supplier,
.procurement-state.state-auto_filling { color: #2c68a8; background: #eaf3ff; }
.procurement-state.state-shipped { color: #24744c; background: #eaf8f0; }
.procurement-state.state-aftersale { color: #7452a4; background: #f2edff; }
.purchase-stack span,
.fulfill-stack span,
.logistics-stack span { color: #768898; font-size: 9px; line-height: 1.4; }
.purchase-stack .supplier-tracking { color: #1f6d8e; }
.mono { font-family: "Cascadia Code", Consolas, monospace; overflow-wrap: anywhere; }
.purchase-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 4px; }
.purchase-actions .btn { min-height: 27px; padding: 0 6px; font-size: 9px; }
.fulfill-stack strong { color: #344a5e; font-size: 10px; }
.fulfill-stack strong.overdue { color: #d63c48; }
.logistics-stack strong { color: #31495b; font-size: 10px; }
.logistics-stack strong.empty { color: #87949e; }
.logistics-stack .btn { width: fit-content; min-height: 27px; padding: 0 9px; font-size: 9px; }
.waybill-row { display: flex; align-items: center; gap: 5px; min-width: 0; }
.waybill-link {
  min-width: 0;
  padding: 0;
  overflow: hidden;
  border: 0;
  color: #1477ad;
  background: transparent;
  font-family: "Cascadia Code", Consolas, monospace;
  font-size: 10px;
  text-align: left;
  text-decoration: underline;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.waybill-copy {
  flex: 0 0 auto;
  padding: 0 4px;
  border: 1px solid #cbdae4;
  border-radius: 3px;
  color: #5d7282;
  background: #f7fbfd;
  font-size: 9px;
}
.supplier-waybill {
  padding: 5px 6px;
  display: grid;
  gap: 2px;
  border: 1px solid #cfe5da;
  border-radius: 5px;
  background: #f3faf6;
}
.supplier-waybill strong { color: #216b48; font-size: 9px; }
.supplier-waybill span { color: #6f897b; font-size: 8px; }

.note-stack { display: grid; gap: 4px; }
.note-stack span { display: grid; grid-template-columns: 54px minmax(0, 1fr); gap: 5px; color: #6d7f8e; font-size: 9px; }
.note-stack b { color: #8795a1; font-weight: 400; }
.note-actions { margin-top: 7px; display: grid; gap: 4px; }
.memo-button {
  min-height: 28px;
  padding: 0 7px;
  display: flex;
  align-items: center;
  gap: 4px;
  border: 1px solid #d4dfe7;
  border-radius: 4px;
  color: #3f7299;
  background: #f7fbfe;
  font-size: 9px;
}
.note-color-swatch {
  flex: 0 0 auto;
  width: 12px;
  height: 12px;
  border: 1px solid #9ba8b2;
  border-radius: 3px;
  background: #eef2f5;
}
.note-color-swatch.flag-none { background: #eef2f5; }
.note-color-swatch.flag-red { border-color: #e14949; background: #fa5151; }
.note-color-swatch.flag-orange { border-color: #d98628; background: #fa9d3b; }
.note-color-swatch.flag-green { border-color: #07994c; background: #07c160; }
.note-color-swatch.flag-blue { border-color: #0b8cc7; background: #10aeff; }
.note-actions .btn { min-height: 27px; padding: 0 7px; font-size: 9px; }
.operation-stack .btn { min-height: 29px; padding: 0 8px; font-size: 9px; }

.btn.blue { border-color: #2b78c9; color: white; background: #2878d0; }
.btn.soft { border-color: #c7e3f3; color: #286b96; background: #eef8fd; }
.btn.danger { border-color: #edc5c9; color: #b5424d; background: #fff5f6; }
.spinning { animation: spin 0.9s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

.action-menu-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.action-menu-grid button {
  min-height: 62px;
  padding: 10px 12px;
  display: grid;
  gap: 4px;
  border: 1px solid #dbe4eb;
  border-radius: 7px;
  color: #29485e;
  background: #fbfdfe;
  text-align: left;
}
.action-menu-grid button:hover:not(:disabled) { border-color: #8bb7d4; background: #f5fbff; }
.action-menu-grid button:disabled { opacity: 0.48; cursor: not-allowed; }
.action-menu-grid b { font-size: 11px; }
.action-menu-grid span { color: #6f8191; font-size: 9px; }

.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.address-import-panel { margin-bottom: 16px; padding: 14px; display: grid; gap: 10px; border: 1px solid #d8e3eb; border-radius: 10px; background: linear-gradient(180deg, #f7fafc 0%, #fff 100%); }
.address-import-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.address-import-head > div { display: grid; gap: 4px; }
.address-import-head strong { color: #243e51; font-size: 13px; }
.address-import-head span { color: #7a8a97; font-size: 10px; }
.parse-state { flex: none; padding: 5px 9px; border-radius: 999px; color: #667888; background: #edf2f6; }
.parse-state.ready { color: #216b48; background: #e8f6ee; }
.parse-state.warning { color: #9a620d; background: #fff3dc; }
.address-raw-textarea { width: 100%; min-height: 150px; padding: 12px 13px; resize: vertical; line-height: 1.7; }
.address-import-actions { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.address-import-actions span { color: #84929e; font-size: 9px; }
.address-parse-error { margin: 0; }
.field { display: grid; gap: 5px; }
.field.full { grid-column: 1 / -1; }
.field label { color: #617487; font-size: 10px; }
.check-row { min-height: 32px; display: flex; align-items: center; gap: 6px; color: #536779; font-size: 10px; }
.delivery-list { display: grid; gap: 10px; }
.delivery-source-note {
  margin-bottom: 12px;
  padding: 10px 12px;
  display: grid;
  gap: 3px;
  border: 1px solid #cfe5da;
  border-radius: 7px;
  background: #f3faf6;
}
.delivery-source-note strong { color: #216b48; font-size: 11px; }
.delivery-source-note span { color: #627b6e; font-size: 9px; }
.delivery-row { display: grid; grid-template-columns: 1fr 1.4fr auto; gap: 8px; align-items: end; }
.dialog-risk { margin: 13px 0 0; color: #9a620d; font-size: 10px; }

.price-list { display: grid; gap: 8px; }
.price-list > label { padding: 9px 10px; display: grid; grid-template-columns: minmax(0, 1fr) 150px; gap: 10px; align-items: center; border: 1px solid #e0e8ee; border-radius: 6px; }
.price-list span { display: grid; gap: 3px; }
.price-list b { color: #2d4559; font-size: 10px; }
.price-list small { color: #81909c; font-size: 9px; }
.express-row { margin-top: 12px; display: flex; align-items: center; justify-content: space-between; gap: 12px; color: #526779; font-size: 10px; }
.express-row .input { width: 160px; }

.after-sale-list,
.order-log-list { display: grid; gap: 8px; }
.after-sale-list article {
  padding: 10px 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border: 1px solid #e0e8ee;
  border-radius: 7px;
}
.after-sale-list article > div:first-child { display: grid; gap: 4px; }
.after-sale-list strong { color: #2e485b; font-size: 11px; }
.after-sale-list span,
.after-sale-list small { color: #708191; font-size: 9px; }
.after-sale-actions { display: flex; gap: 5px; }
.after-sale-actions .btn { min-height: 27px; padding: 0 8px; font-size: 9px; }
.order-log-list article { padding: 10px 12px; display: grid; gap: 4px; border: 1px solid #e0e8ee; border-radius: 7px; }
.order-log-list strong { color: #314b5f; font-size: 11px; }
.order-log-list span { color: #7b8d9c; font-size: 9px; }
.order-log-list p { margin: 0; color: #5f7282; font-size: 10px; line-height: 1.6; }
.empty-dialog { padding: 34px 20px; color: #83919d; text-align: center; font-size: 11px; }
.logistics-error {
  padding: 14px;
  border: 1px solid #efc7ca;
  border-radius: 6px;
  color: #a43e47;
  background: #fff6f7;
  font-size: 10px;
  line-height: 1.7;
}
.logistics-detail { display: grid; gap: 13px; }
.logistics-detail header { display: grid; gap: 4px; }
.logistics-detail header strong { color: #1d5f49; font-size: 13px; }
.logistics-detail header span { color: #36576d; font-size: 11px; }
.logistics-detail header small { color: #7f8e9a; font-size: 9px; }
.logistics-detail ol {
  max-height: 430px;
  margin: 0;
  padding: 0 0 0 19px;
  overflow: auto;
}
.logistics-detail li { padding: 9px 0 9px 6px; border-bottom: 1px solid #e6edf2; }
.logistics-detail li::marker { color: #19a76b; }
.logistics-detail li time { display: block; margin-bottom: 4px; color: #7e8d98; font-size: 9px; }
.logistics-detail li span { color: #334c5e; font-size: 10px; line-height: 1.55; }
.confirm-copy { display: grid; gap: 12px; color: #4f6375; font-size: 11px; line-height: 1.7; }
.confirm-copy p { margin: 0; }
.confirm-copy > div { padding: 10px 12px; display: grid; gap: 4px; border: 1px solid #f0d7b1; border-radius: 6px; background: #fff9ee; }
.confirm-copy strong { color: #9a620d; }
.confirm-copy span { color: #715a34; }

@media (max-width: 1500px) {
  .product-prices span:nth-child(3) { display: none; }
}
</style>
