<script setup>
import {
  computed,
  onActivated,
  onBeforeUnmount,
  onMounted,
  reactive,
  ref,
  watch,
} from "vue";
import { call, formatMoney } from "../api";
import { useAutoRefresh } from "../autoRefresh";
import { copyText } from "../clipboard";
import Icon from "../components/Icon.vue";
import ModalDialog from "../components/ModalDialog.vue";
import StoreExposureValue from "../components/StoreExposureValue.vue";
import StoreSalesValue from "../components/StoreSalesValue.vue";
import {
  getStoreInteractionState,
  pollStoreRefreshJob,
  runStoreDateRolloverReload,
  startRepeatingSync,
} from "../storeListSync";
import {
  readRuntimeNoticeVisible,
  runtimeNoticeStorageKey as makeRuntimeNoticeStorageKey,
  writeRuntimeNoticeVisible,
} from "../runtimeNotice";
import { hasEntitlement, sessionState } from "../session";
import {
  depositPresentation,
  scorePresentation,
  STORE_SCORE_FIELDS,
} from "../storeScores";
import { APP_ID_REVEAL_TIMEOUT_MS, maskAppId } from "../storeIdentity";
import {
  exposurePresentation,
  fundPresentation,
  isRefundVisible,
  newProductsTodayPresentation,
  onlineProductsPresentation,
  salesPresentation,
  shanghaiDateKey,
} from "../storeMetrics";
import {
  readStoreViewMode,
  STORE_VIEW_FULL,
  STORE_VIEW_OVERVIEW,
  storeViewStorageKey as makeStoreViewStorageKey,
  writeStoreViewMode,
} from "../storeViewMode";
import {
  depositDisplay,
  exposureDisplay,
  plainMoneyText,
  salesDisplay,
} from "../storeTableDisplay";

const STORE_TABLE_COLUMN_COUNT = 23;
const STORE_OVERVIEW_COLUMN_COUNT = 12;
const SALES_PERIODS = [
  { key: "today", label: "今日" },
  { key: "yesterday", label: "昨日" },
  { key: "seven_days", label: "7日" },
  { key: "thirty_days", label: "30日" },
];
const EXPOSURE_PERIODS = SALES_PERIODS.slice(0, 3);
const stores = ref([]);
const groups = ref([]);
const loading = ref(false);
const storeActionBusy = ref(false);
const storeSaveBusy = ref(false);
const message = ref("");
const runtimeNoticeStorageKey = computed(() => (
  makeRuntimeNoticeStorageKey(sessionState.bootstrap?.account)
));
const runtimeNoticeVisible = ref(true);
const storeViewStorageKey = computed(() => (
  makeStoreViewStorageKey(sessionState.bootstrap?.account)
));
const storeViewMode = ref(STORE_VIEW_OVERVIEW);
const expandedStoreIds = ref([]);
const contextMenu = reactive({
  open: false,
  x: 0,
  y: 0,
  storeIds: [],
  currentStoreId: 0,
});
const batchGroupDialog = reactive({
  open: false,
  storeIds: [],
  groupName: "",
  busy: false,
});
const activeFilter = ref("all");
const search = ref("");
const groupFilter = ref("全部店铺");
const dialogMode = ref("");
const selectedStore = ref(null);
const selectedStoreIds = ref([]);
const batchDialog = ref(false);
const batchSourceKind = ref("paste");
const batchText = ref("");
const batchFileName = ref("");
const batchFileBase64 = ref("");
const batchBusy = ref(false);
const batchPreview = ref(null);
const batchFilter = ref("all");
const batchSearch = ref("");
const batchSelectedRows = ref([]);
const batchNameOverrides = reactive({});
const batchFileInput = ref(null);
const batchInspectRow = ref(null);
const credentialDialog = ref(false);
const credentialLoading = ref(false);
const credentialProbeBusy = ref(false);
const credentialDetail = ref(null);
const cookieImportText = ref("");
const cookieImportBusy = ref(false);
const confirmDialog = reactive({
  open: false,
  title: "",
  message: "",
  confirmLabel: "确认",
  busy: false,
  action: null,
});
const refreshProgress = reactive({
  visible: false,
  status: "",
  current: 0,
  total: 0,
  succeeded: 0,
  failed: 0,
  credential_succeeded: 0,
  credential_superseded: 0,
  credential_failed: 0,
  credential_deferred: 0,
  message: "",
});
const autoRefresh = useAutoRefresh({
  storageKey: "langlang.auto-refresh.stores",
  refresh: async () => {
    const result = await refreshAll({ automatic: true });
    if (!result.ok) throw new Error(result.message);
    return result;
  },
  isBusy: () => (
    loading.value
    || storeActionBusy.value
    || storeSaveBusy.value
    || batchBusy.value
  ),
});
const revealedSecrets = reactive({});
const secretLoading = reactive({});
const secretTimers = new Map();
const revealedAppIds = reactive({});
const appIdTimers = new Map();
const newGroupName = ref("");
const groupBusy = ref(false);
let liveStoreSyncStop = null;
let liveStoreSyncUsers = 0;
let loadedDateKey = "";
let dateRolloverTimer = null;
const form = reactive({
  name: "",
  app_id: "",
  secret: "",
  group_name: "",
});

function loadRuntimeNoticePreference() {
  runtimeNoticeVisible.value = readRuntimeNoticeVisible(
    globalThis.localStorage,
    runtimeNoticeStorageKey.value,
  );
}

function toggleRuntimeNotice() {
  runtimeNoticeVisible.value = !runtimeNoticeVisible.value;
  writeRuntimeNoticeVisible(
    globalThis.localStorage,
    runtimeNoticeStorageKey.value,
    runtimeNoticeVisible.value,
  );
}

watch(runtimeNoticeStorageKey, loadRuntimeNoticePreference, { immediate: true });

const filteredStores = computed(() => {
  const term = search.value.trim().toLowerCase();
  return stores.value.filter((store) => {
    const loggedIn = isStoreLoggedIn(store);
    if (activeFilter.value === "logged" && !loggedIn) return false;
    if (activeFilter.value === "unlogged" && loggedIn) return false;
    if (groupFilter.value !== "全部店铺" && store.group_name !== groupFilter.value) return false;
    if (!term) return true;
    return [store.name, store.app_id]
      .some((value) => String(value || "").toLowerCase().includes(term));
  });
});

function isStoreLoggedIn(store) {
  return [store.web_status, store.status].some((value) => (
    String(value || "").includes("已登录")
  ));
}

const loggedCount = computed(() => stores.value.filter(isStoreLoggedIn).length);
const unloggedCount = computed(() => stores.value.length - loggedCount.value);
const storeGroups = computed(() => (
  [...new Set(groups.value.map((group) => String(group || "").trim()).filter(Boolean))]
));
const canUseStoreGroups = computed(() => hasEntitlement("module.store_groups"));
const allFilteredSelected = computed(() => (
  filteredStores.value.length > 0
  && filteredStores.value.every((store) => selectedStoreIds.value.includes(store.id))
));
const storeInteractions = computed(() => getStoreInteractionState({
  hasSelection: selectedStoreIds.value.length > 0,
  storeActionBusy: storeActionBusy.value,
}));
const refreshPercent = computed(() => {
  if (!refreshProgress.total) return refreshProgress.visible ? 8 : 0;
  return Math.max(0, Math.min(100, refreshProgress.current / refreshProgress.total * 100));
});
const filteredBatchRows = computed(() => {
  const rows = batchPreview.value?.rows || [];
  const term = batchSearch.value.trim().toLowerCase();
  return rows.filter((row) => {
    if (batchFilter.value !== "all") {
      if (batchFilter.value === "exception") {
        if (!["invalid", "duplicate", "name_conflict"].includes(row.status)) return false;
      } else if (row.status !== batchFilter.value) {
        return false;
      }
    }
    if (!term) return true;
    return [row.name, row.app_id]
      .some((value) => String(value || "").toLowerCase().includes(term));
  });
});
const selectableBatchRows = computed(() => (
  (batchPreview.value?.rows || []).filter((row) => row.selectable)
));
const allSelectableBatchRowsSelected = computed(() => (
  selectableBatchRows.value.length > 0
  && selectableBatchRows.value.every((row) => batchSelectedRows.value.includes(row.row_number))
));
const batchCounts = computed(() => batchPreview.value?.summary || {
  new: 0,
  skip: 0,
  replace_candidate: 0,
  invalid: 0,
  duplicate: 0,
  name_conflict: 0,
});

function groupStoreCount(groupName) {
  if (groupName === "全部店铺") return stores.value.length;
  return stores.value.filter((store) => store.group_name === groupName).length;
}

function setGroupFilter(groupName) {
  groupFilter.value = groupName;
}

function toggleStoreSelection(storeId) {
  selectedStoreIds.value = selectedStoreIds.value.includes(storeId)
    ? selectedStoreIds.value.filter((id) => id !== storeId)
    : [...selectedStoreIds.value, storeId];
}

function toggleFilteredSelection() {
  const visibleIds = filteredStores.value.map((store) => store.id);
  if (allFilteredSelected.value) {
    selectedStoreIds.value = selectedStoreIds.value.filter((id) => !visibleIds.includes(id));
  } else {
    selectedStoreIds.value = [...new Set([...selectedStoreIds.value, ...visibleIds])];
  }
}

const summary = computed(() => {
  const completeSum = (values) => {
    if (!values.length) return null;
    if (values.some((value) => value === null || value === undefined)) {
      return null;
    }
    return values.reduce((total, value) => total + Number(value), 0);
  };
  const presentations = filteredStores.value.map((store) => ({
    today: salesPresentation(store, "today"),
    yesterday: salesPresentation(store, "yesterday"),
    seven_days: salesPresentation(store, "seven_days"),
    thirty_days: salesPresentation(store, "thirty_days"),
  }));
  const result = {};
  for (const [period, summaryKey] of [
    ["today", "today"],
    ["yesterday", "yesterday"],
    ["seven_days", "week"],
    ["thirty_days", "month"],
  ]) {
    result[summaryKey] = completeSum(
      presentations.map((item) => item[period].value),
    );
    result[`${summaryKey}_orders`] = completeSum(
      presentations.map((item) => item[period].orderValue),
    );
    result[`refund_${summaryKey}`] = completeSum(
      presentations.map((item) => item[period].refundAmount),
    );
    result[`refund_${summaryKey}_orders`] = completeSum(
      presentations.map((item) => item[period].refundOrderCount),
    );
  }
  result.cash = completeSum(
    filteredStores.value.map((store) => numericMetric(store.metrics?.cash)),
  );
  result.settle = completeSum(
    filteredStores.value.map((store) => numericMetric(store.metrics?.settle)),
  );
  return result;
});

function numericMetric(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function summaryMoney(value) {
  return value === null || value === undefined
    ? "—"
    : formatMoney(value);
}

function summaryCount(value) {
  return value === null || value === undefined
    ? "—单"
    : `${new Intl.NumberFormat("zh-CN").format(value)}单`;
}

function isStoreExpanded(storeId) {
  return expandedStoreIds.value.includes(storeId);
}

function toggleStoreDetails(storeId) {
  expandedStoreIds.value = isStoreExpanded(storeId)
    ? expandedStoreIds.value.filter((value) => value !== storeId)
    : [...expandedStoreIds.value, storeId];
}

function closeStoreContextMenu() {
  contextMenu.open = false;
}

function openContextMenuAt(x, y, storeIds) {
  contextMenu.storeIds = [...storeIds];
  contextMenu.x = Math.max(
    8,
    Math.min(Number(x || 0), window.innerWidth - 236),
  );
  contextMenu.y = Math.max(
    8,
    Math.min(Number(y || 0), window.innerHeight - 470),
  );
  contextMenu.open = true;
}

function openStoreContextMenu(event, store) {
  if (!selectedStoreIds.value.includes(store.id)) {
    selectedStoreIds.value = [store.id];
  }
  contextMenu.currentStoreId = store.id;
  openContextMenuAt(
    event.clientX,
    event.clientY,
    selectedStoreIds.value,
  );
}

function selectCurrentContextStore() {
  if (contextMenu.currentStoreId) {
    selectedStoreIds.value = [contextMenu.currentStoreId];
  }
  closeStoreContextMenu();
}

function selectAllCurrentPage() {
  const ids = filteredStores.value.map((store) => store.id);
  selectedStoreIds.value = [...new Set([...selectedStoreIds.value, ...ids])];
  closeStoreContextMenu();
}

function selectAllFiltered() {
  selectedStoreIds.value = filteredStores.value.map((store) => store.id);
  closeStoreContextMenu();
}

function invertFilteredSelection() {
  const visible = new Set(filteredStores.value.map((store) => store.id));
  const retained = selectedStoreIds.value.filter((id) => !visible.has(id));
  const inverted = filteredStores.value
    .filter((store) => !selectedStoreIds.value.includes(store.id))
    .map((store) => store.id);
  selectedStoreIds.value = [...retained, ...inverted];
  closeStoreContextMenu();
}

function clearStoreSelection() {
  selectedStoreIds.value = [];
  closeStoreContextMenu();
}

async function copySelectedStoreIds() {
  const ids = stores.value
    .filter((store) => contextMenu.storeIds.includes(store.id))
    .map((store) => String(store.app_id || store.id));
  closeStoreContextMenu();
  if (!ids.length) return;
  try {
    await copyText(ids.join("\n"));
    message.value = `已复制 ${ids.length} 家店铺编号`;
  } catch (cause) {
    message.value = cause instanceof Error ? cause.message : String(cause);
  }
}

async function refreshContextStores() {
  selectedStoreIds.value = [...contextMenu.storeIds];
  closeStoreContextMenu();
  await manualRefreshAll();
}

async function runContextStoreAction(action) {
  selectedStoreIds.value = [...contextMenu.storeIds];
  closeStoreContextMenu();
  await runStoreAction(action);
}

function openBatchContextMenu(event) {
  if (!selectedStoreIds.value.length) {
    message.value = "请先勾选要批量操作的店铺";
    return;
  }
  const rect = event.currentTarget.getBoundingClientRect();
  openContextMenuAt(rect.right - 210, rect.bottom + 4, selectedStoreIds.value);
}

function openBatchGroupDialog(createNew = false) {
  const ids = [...contextMenu.storeIds];
  closeStoreContextMenu();
  if (!ids.length) {
    message.value = "请先选择店铺";
    return;
  }
  batchGroupDialog.storeIds = ids;
  batchGroupDialog.groupName = createNew
    ? ""
    : String(stores.value.find((store) => ids.includes(store.id))?.group_name || "");
  batchGroupDialog.busy = false;
  batchGroupDialog.open = true;
}

async function saveBatchGroup({ allowEmpty = false } = {}) {
  if (batchGroupDialog.busy) return;
  const groupName = batchGroupDialog.groupName.trim();
  if (!groupName && !allowEmpty) {
    message.value = "请输入新分组名称或选择已有分组";
    return;
  }
  batchGroupDialog.busy = true;
  try {
    const result = await call("stores.batch-assign-group", {
      store_ids: batchGroupDialog.storeIds,
      group_name: groupName,
    });
    batchGroupDialog.open = false;
    await load();
    message.value = `已将 ${result.updated} 家店铺设为${groupName || "未分组"}`;
  } catch (cause) {
    message.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    batchGroupDialog.busy = false;
  }
}

function askBatchDelete() {
  const ids = [...contextMenu.storeIds];
  closeStoreContextMenu();
  if (!ids.length) {
    message.value = "请先选择店铺";
    return;
  }
  const names = stores.value
    .filter((store) => ids.includes(store.id))
    .map((store) => store.name);
  askConfirm({
    title: `批量删除本地店铺（${ids.length} 家）`,
    message:
      `将删除本软件本地店铺及关联本地数据/登录缓存：${names.slice(0, 8).join("、")}` +
      `${names.length > 8 ? ` 等 ${names.length} 家` : ""}。` +
      "此操作不可撤销，且不会删除微信官方店铺。",
    confirmLabel: "确认批量删除",
    action: async () => {
      const result = await call("stores.batch-delete", {
        store_ids: ids,
      });
      selectedStoreIds.value = selectedStoreIds.value.filter(
        (storeId) => !ids.includes(storeId),
      );
      expandedStoreIds.value = expandedStoreIds.value.filter(
        (storeId) => !ids.includes(storeId),
      );
      await load();
      message.value =
        `已删除 ${result.deleted} 家本地店铺；微信官方店铺未删除` +
        `${result.cache_cleanup_errors?.length
          ? `；登录缓存清理异常 ${result.cache_cleanup_errors.length} 项`
          : ""}`;
    },
  });
}

function handleContextPointer(event) {
  if (!contextMenu.open) return;
  if (event.target.closest(".store-context-menu")) return;
  if (event.target.closest(".store-batch-trigger")) return;
  closeStoreContextMenu();
}

function handleContextKey(event) {
  if (event.key === "Escape") closeStoreContextMenu();
}

function loadStoreViewMode() {
  storeViewMode.value = readStoreViewMode(
    globalThis.localStorage,
    storeViewStorageKey.value,
  );
}

function setStoreViewMode(value) {
  storeViewMode.value = value === STORE_VIEW_FULL
    ? STORE_VIEW_FULL
    : STORE_VIEW_OVERVIEW;
  writeStoreViewMode(
    globalThis.localStorage,
    storeViewStorageKey.value,
    storeViewMode.value,
  );
}

watch(storeViewStorageKey, loadStoreViewMode, { immediate: true });

function metricNumber(store, primary, fallback) {
  const value = store.metrics?.[primary] ?? store.metrics?.[fallback];
  return value === null || value === undefined || value === "" ? null : Number(value);
}

function moneyMetric(store, primary, fallback) {
  const value = metricNumber(store, primary, fallback);
  return plainMoneyText(value);
}

function moneyMetricTitle(store, primary, fallback) {
  if (primary === "cash" || primary === "settle") {
    return fundPresentation(store, primary).title;
  }
  return metricNumber(store, primary, fallback) === null
    ? "数据不可用"
    : "";
}

function exposureDisplayFor(store, key) {
  return exposureDisplay(
    store,
    key,
    exposurePresentation(store, key),
  );
}

function salesDisplayFor(store, key) {
  return salesDisplay(salesPresentation(store, key));
}

function depositDisplayFor(store) {
  const display = depositDisplay(store);
  const presentation = depositPresentation(store);
  return {
    ...display,
    title: [display.title, presentation.statusTitle]
      .filter(Boolean)
      .join(" · "),
  };
}

function resetForm(store = null) {
  form.name = store?.name || "";
  form.app_id = store?.app_id || "";
  form.secret = "";
  form.group_name = store?.group_name || "";
  selectedStore.value = store;
}

function hideSecret(storeId) {
  const timer = secretTimers.get(storeId);
  if (timer) clearTimeout(timer);
  secretTimers.delete(storeId);
  delete revealedSecrets[storeId];
}

function hideAppId(storeId) {
  const timer = appIdTimers.get(storeId);
  if (timer) clearTimeout(timer);
  appIdTimers.delete(storeId);
  delete revealedAppIds[storeId];
}

function toggleAppId(store) {
  if (Object.prototype.hasOwnProperty.call(revealedAppIds, store.id)) {
    hideAppId(store.id);
    return;
  }
  revealedAppIds[store.id] = true;
  const timer = setTimeout(() => hideAppId(store.id), APP_ID_REVEAL_TIMEOUT_MS);
  appIdTimers.set(store.id, timer);
}

async function toggleSecret(store) {
  if (Object.prototype.hasOwnProperty.call(revealedSecrets, store.id)) {
    hideSecret(store.id);
    return;
  }
  if (secretLoading[store.id]) return;
  secretLoading[store.id] = true;
  try {
    const result = await call("stores.reveal-secret", { store_id: store.id });
    revealedSecrets[store.id] = result.secret || "";
    const timer = setTimeout(() => hideSecret(store.id), 60000);
    secretTimers.set(store.id, timer);
  } catch (cause) {
    message.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    secretLoading[store.id] = false;
  }
}

function batchStatusLabel(status) {
  return {
    new: "新增",
    skip: "跳过",
    replace_candidate: "待替换",
    invalid: "格式错误",
    duplicate: "重复行",
    name_conflict: "名称冲突",
  }[status] || status;
}

function credentialStateLabel(state) {
  return {
    unknown: "尚未判定",
    valid: "可用",
    invalid: "已失效",
    probing: "验证中",
  }[state] || "尚未判定";
}

function candidateStateLabel(state) {
  return {
    pending: "待刷新验证",
    validating: "验证中",
    valid: "验证成功",
    invalid: "候选无效",
    superseded: "已放弃",
    applied: "已替换",
  }[state] || state;
}

function askConfirm({ title, message, confirmLabel = "确认", action }) {
  Object.assign(confirmDialog, {
    open: true,
    title,
    message,
    confirmLabel,
    busy: false,
    action,
  });
}

async function runConfirm() {
  if (!confirmDialog.action || confirmDialog.busy) return;
  confirmDialog.busy = true;
  try {
    await confirmDialog.action();
    confirmDialog.open = false;
  } catch (cause) {
    message.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    confirmDialog.busy = false;
  }
}

function openBatchImport() {
  batchDialog.value = true;
  batchSourceKind.value = "paste";
  batchText.value = "";
  batchFileName.value = "";
  batchFileBase64.value = "";
  batchPreview.value = null;
  batchFilter.value = "all";
  batchSearch.value = "";
  batchSelectedRows.value = [];
  batchInspectRow.value = null;
  Object.keys(batchNameOverrides).forEach((key) => delete batchNameOverrides[key]);
}

function resetBatchPreview() {
  batchPreview.value = null;
  batchSelectedRows.value = [];
  Object.keys(batchNameOverrides).forEach((key) => delete batchNameOverrides[key]);
}

function selectBatchSource(kind) {
  batchSourceKind.value = kind;
  batchFileName.value = "";
  batchFileBase64.value = "";
  Object.keys(batchNameOverrides).forEach((key) => delete batchNameOverrides[key]);
  resetBatchPreview();
}

function chooseBatchFile() {
  if (!batchFileInput.value) return;
  batchFileInput.value.value = "";
  batchFileInput.value.click();
}

function bufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const chunkSize = 0x8000;
  for (let index = 0; index < bytes.length; index += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize));
  }
  return btoa(binary);
}

async function batchFileChanged(event) {
  const file = event.target.files?.[0];
  event.target.value = "";
  if (!file) return;
  if (file.size > 20_000_000) {
    message.value = "店铺导入文件不能超过 20 MB";
    return;
  }
  batchFileName.value = file.name;
  batchFileBase64.value = bufferToBase64(await file.arrayBuffer());
  Object.keys(batchNameOverrides).forEach((key) => delete batchNameOverrides[key]);
  resetBatchPreview();
}

function batchSourcePayload() {
  return {
    source_kind: batchSourceKind.value,
    source_name:
      batchSourceKind.value === "paste"
        ? "粘贴文本"
        : batchFileName.value,
    text: batchSourceKind.value === "paste" ? batchText.value : "",
    content_base64:
      batchSourceKind.value === "paste" ? "" : batchFileBase64.value,
    name_overrides: { ...batchNameOverrides },
  };
}

async function previewBatch() {
  if (batchBusy.value) return;
  if (batchSourceKind.value !== "paste" && !batchFileBase64.value) {
    message.value = "请先选择 CSV 或 XLSX 文件";
    return;
  }
  if (batchSourceKind.value === "paste" && !batchText.value.trim()) {
    message.value = "请粘贴店铺凭据文本";
    return;
  }
  batchBusy.value = true;
  message.value = "";
  try {
    const preview = await call("stores.batch.preview", batchSourcePayload());
    batchPreview.value = preview;
    batchSelectedRows.value = preview.rows
      .filter((row) => row.selectable)
      .map((row) => row.row_number);
    message.value = `本地预检完成：${preview.summary.new} 新增、${preview.summary.replace_candidate} 待替换；未访问平台`;
  } catch (cause) {
    message.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    batchBusy.value = false;
  }
}

function toggleBatchRow(rowNumber) {
  batchSelectedRows.value = batchSelectedRows.value.includes(rowNumber)
    ? batchSelectedRows.value.filter((value) => value !== rowNumber)
    : [...batchSelectedRows.value, rowNumber];
}

function toggleAllBatchRows() {
  batchSelectedRows.value = allSelectableBatchRowsSelected.value
    ? []
    : selectableBatchRows.value.map((row) => row.row_number);
}

async function commitBatchImport() {
  if (!batchPreview.value || batchBusy.value) return;
  if (!batchSelectedRows.value.length) {
    message.value = "请至少选择一条可保存记录";
    return;
  }
  batchBusy.value = true;
  message.value = "";
  try {
    const result = await call("stores.batch.commit", {
      ...batchSourcePayload(),
      source_hash: batchPreview.value.source_hash,
      row_numbers: batchSelectedRows.value,
      background_refresh_enabled: autoRefresh.enabled.value,
    });
    batchDialog.value = false;
    await load();
    if (result.profile_hydration?.started) {
      beginLiveStoreSync();
      message.value =
        `已新增 ${result.created_count} 家店铺，后台正在获取真实店铺名称；` +
        `替换候选 ${result.candidate_count} 个已保存。`;
      window.setTimeout(async () => {
        endLiveStoreSync();
        await load();
        message.value = "新增店铺名称同步已刷新";
      }, Math.max(15000, result.created_count * 3000));
    } else {
      message.value =
        `本地候选已保存：新增 ${result.created_count} 家、` +
        `替换候选 ${result.candidate_count} 个。`;
    }
  } catch (cause) {
    message.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    batchBusy.value = false;
  }
}

function downloadStoreTemplate() {
  const csv = "\ufeff店铺名称,AppID,AppSecret,分组\n示例店铺,wx0000000000000000,你的AppSecret,默认分组\n";
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "店铺批量导入模板.csv";
  anchor.click();
  URL.revokeObjectURL(url);
}

async function openCredentialDetail(store) {
  credentialDialog.value = true;
  credentialLoading.value = true;
  credentialDetail.value = null;
  cookieImportText.value = "";
  try {
    credentialDetail.value = await call("stores.credentials.detail", {
      store_id: store.id,
    });
  } catch (cause) {
    message.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    credentialLoading.value = false;
  }
}

async function probeCurrentCredential() {
  if (!credentialDetail.value || credentialProbeBusy.value) return;
  credentialProbeBusy.value = true;
  try {
    const result = await call("stores.credentials.probe", {
      store_id: credentialDetail.value.store_id,
    });
    await openCredentialDetail({ id: credentialDetail.value.store_id });
    await load();
    message.value = `当前密钥探测成功：${result.message}`;
  } catch (cause) {
    await openCredentialDetail({ id: credentialDetail.value.store_id });
    message.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    credentialProbeBusy.value = false;
  }
}

async function importStoreCookie() {
  if (!credentialDetail.value || cookieImportBusy.value) return;
  const header = cookieImportText.value.trim();
  if (!header) {
    message.value = "请粘贴 Cookie Header";
    return;
  }
  cookieImportBusy.value = true;
  try {
    const result = await call("stores.import-cookie", {
      store_id: credentialDetail.value.store_id,
      cookie_header: header,
      background_refresh_enabled: autoRefresh.enabled.value,
    });
    cookieImportText.value = "";
    message.value = `Cookie 已导入：${result.status || "登录态待验证"}${result.detail ? ` · ${result.detail}` : ""}`;
    await openCredentialDetail({ id: credentialDetail.value.store_id });
  } catch (cause) {
    message.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    cookieImportBusy.value = false;
  }
}

async function refreshCredentialStore() {
  const storeId = credentialDetail.value?.store_id;
  credentialDialog.value = false;
  if (!storeId) return;
  selectedStoreIds.value = [storeId];
  await refreshAll();
  await openCredentialDetail({ id: storeId });
}

function openCreate() {
  resetForm();
  dialogMode.value = "create";
}

async function openEdit(store) {
  resetForm(store);
  dialogMode.value = "edit";
  try {
    const result = await call("stores.reveal-secret", { store_id: store.id });
    form.secret = result.secret || "";
  } catch (cause) {
    message.value = cause instanceof Error ? cause.message : String(cause);
  }
}

function openDelete(store) {
  selectedStore.value = store;
  dialogMode.value = "delete";
}

function openGroup(store) {
  resetForm(store);
  newGroupName.value = "";
  dialogMode.value = "group";
}

function closeDialog() {
  dialogMode.value = "";
  selectedStore.value = null;
  message.value = "";
}

function applyStoreData(data) {
  stores.value = data.stores || [];
  groups.value = data.groups || [];
  const validIds = new Set(stores.value.map((store) => store.id));
  selectedStoreIds.value = selectedStoreIds.value.filter((id) => validIds.has(id));
  expandedStoreIds.value = expandedStoreIds.value.filter((id) => validIds.has(id));
}

async function syncStoresQuietly() {
  const data = await call("stores.list", { search: "" });
  applyStoreData(data);
  if (storeActionBusy.value) {
    message.value =
      `登录进行中：已登录 ${loggedCount.value} 家，未登录 ${unloggedCount.value} 家`;
  }
}

function beginLiveStoreSync() {
  liveStoreSyncUsers += 1;
  if (liveStoreSyncStop !== null) return;
  liveStoreSyncStop = startRepeatingSync({
    synchronize: syncStoresQuietly,
  });
}

function endLiveStoreSync() {
  liveStoreSyncUsers = Math.max(0, liveStoreSyncUsers - 1);
  if (liveStoreSyncUsers === 0 && liveStoreSyncStop !== null) {
    liveStoreSyncStop();
    liveStoreSyncStop = null;
  }
}

async function load() {
  loading.value = true;
  message.value = "";
  try {
    const data = await call("stores.list", { search: "" });
    applyStoreData(data);
    loadedDateKey = shanghaiDateKey();
  } catch (cause) {
    message.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    loading.value = false;
  }
}

async function reloadAfterDateRollover() {
  await runStoreDateRolloverReload({
    loadedDateKey,
    currentDateKey: shanghaiDateKey(),
    loading: loading.value,
    automatic: autoRefresh.enabled.value,
    load,
    refresh: refreshAll,
    onLoaded: (dateKey) => {
      loadedDateKey = dateKey;
    },
  });
}

async function refreshAll({ automatic = false } = {}) {
  loading.value = true;
  if (!automatic) message.value = "";
  try {
    const storeIds = [...selectedStoreIds.value];
    Object.assign(refreshProgress, {
      visible: !automatic,
      status: "starting",
      current: 0,
      total: storeIds.length || stores.value.length,
      succeeded: 0,
      failed: 0,
      credential_succeeded: 0,
      credential_superseded: 0,
      credential_failed: 0,
      credential_deferred: 0,
      message: "正在准备刷新店铺…",
    });
    const started = await call("stores.refresh.start", {
      store_ids: storeIds.length ? storeIds : undefined,
    });
    if (started.already_running) {
      message.value = "已有相同店铺范围的后台刷新，正在复用该任务";
    }
    const result = await pollStoreRefreshJob({
      jobId: started.job_id,
      getStatus: (jobId) => call(
        "stores.refresh.status",
        { job_id: jobId },
      ),
      onStatus: (status) => {
        Object.assign(refreshProgress, status, { visible: true });
      },
    });
    await load();
    const credentialSummary =
      `凭据恢复 ${result.credential_succeeded || 0}，` +
      `放弃 ${result.credential_superseded || 0}，` +
      `拒绝 ${result.credential_failed || 0}，` +
      `延后 ${result.credential_deferred || 0}`;
    const summaryMessage = result.errors?.length
      ? `${storeIds.length ? "选中店铺" : "全部店铺"}数据刷新 ${result.succeeded}/${result.total} 家；${credentialSummary}；${result.errors.slice(0, 2).join("；")}`
      : `${storeIds.length ? "选中店铺" : "全部店铺"}数据刷新 ${result.succeeded} 家；${credentialSummary}`;
    if (!automatic) message.value = summaryMessage;
    refreshProgress.message = summaryMessage;
    return { ok: true, message: summaryMessage };
  } catch (cause) {
    const errorMessage = cause instanceof Error ? cause.message : String(cause);
    if (!automatic) message.value = errorMessage;
    refreshProgress.status = "failed";
    refreshProgress.message = errorMessage;
    return { ok: false, message: errorMessage };
  } finally {
    loading.value = false;
    if (!automatic) {
      refreshProgress.visible = true;
      window.setTimeout(() => {
        if (!loading.value && ["completed", "partial", "failed"].includes(refreshProgress.status)) {
          refreshProgress.visible = false;
        }
      }, 2600);
    } else {
      refreshProgress.visible = false;
    }
  }
}

async function manualRefreshAll() {
  if (!autoRefresh.beginManual()) {
    message.value = "自动刷新正在进行，请等待本轮结束";
    return;
  }
  try {
    await refreshAll();
  } finally {
    autoRefresh.endManual();
  }
}

async function saveStore() {
  if (storeSaveBusy.value) return;
  const name = form.name.trim() || form.app_id.trim();
  if (!name || !form.app_id.trim()) {
    message.value = "店铺名称和 AppID 不能为空";
    return;
  }
  storeSaveBusy.value = true;
  message.value = "";
  try {
    let result = null;
    if (dialogMode.value === "create") {
      result = await call("stores.create", {
        name,
        app_id: form.app_id.trim(),
        secret: form.secret.trim(),
        ...(canUseStoreGroups.value
          ? { group_name: form.group_name.trim() }
          : {}),
        background_refresh_enabled: autoRefresh.enabled.value,
      });
    } else {
      result = await call("stores.update", {
        store_id: selectedStore.value.id,
        name,
        ...(canUseStoreGroups.value
          ? { group_name: form.group_name.trim() }
          : {}),
        secret: form.secret.trim(),
        app_id: form.app_id.trim(),
      });
    }
    closeDialog();
    await load();
    message.value = result?.profile_hydration?.started
      ? "店铺已保存，正在后台获取官方店铺资料"
      : "店铺已保存";
  } catch (cause) {
    message.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    storeSaveBusy.value = false;
  }
}

async function deleteStore() {
  if (storeSaveBusy.value) return;
  storeSaveBusy.value = true;
  try {
    await call("stores.delete", { store_id: selectedStore.value.id });
    closeDialog();
    await load();
    message.value = "店铺已删除";
  } catch (cause) {
    message.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    storeSaveBusy.value = false;
  }
}

async function saveGroup() {
  try {
    await call("stores.assign-group", {
      store_id: selectedStore.value.id,
      group_name: form.group_name,
    });
    closeDialog();
    await load();
  } catch (cause) {
    message.value = cause instanceof Error ? cause.message : String(cause);
  }
}

async function createGroup() {
  const groupName = newGroupName.value.trim();
  if (!groupName || groupBusy.value) return;
  groupBusy.value = true;
  try {
    const result = await call("stores.groups.create", { group_name: groupName });
    groups.value = result.groups || groups.value;
    form.group_name = groupName;
    newGroupName.value = "";
  } catch (cause) {
    message.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    groupBusy.value = false;
  }
}

async function deleteGroup() {
  const groupName = form.group_name.trim();
  if (!groupName || groupBusy.value) return;
  askConfirm({
    title: "删除店铺分组",
    message: `删除分组“${groupName}”会清空该分组下所有店铺的分组信息，但不会删除店铺、订单、商品或售后数据。`,
    confirmLabel: "删除分组",
    action: async () => {
      groupBusy.value = true;
      try {
        const result = await call("stores.groups.delete", { group_name: groupName });
        groups.value = result.groups || groups.value;
        if (groupFilter.value === groupName) groupFilter.value = "全部店铺";
        form.group_name = "";
        await load();
      } finally {
        groupBusy.value = false;
      }
    },
  });
}

async function clearGroup() {
  if (groupBusy.value) return;
  groupBusy.value = true;
  try {
    await call("stores.assign-group", {
      store_id: selectedStore.value.id,
      group_name: "",
    });
    closeDialog();
    await load();
  } catch (cause) {
    message.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    groupBusy.value = false;
  }
}

async function runStoreAction(action) {
  if (action === "stores.clear-cache") {
    try {
      const result = await call(action, {});
      await load();
      message.value = `已清理 ${Number(result.removed || 0)} 项本地缓存`;
    } catch (cause) {
      message.value = cause instanceof Error ? cause.message : String(cause);
    }
    return;
  }
  const storeIds = [...selectedStoreIds.value];
  if (!storeIds.length) {
    message.value = "请先勾选要操作的店铺";
    return;
  }
  if (storeActionBusy.value) return;
  const isLogin = action === "stores.login";
  storeActionBusy.value = true;
  if (isLogin) {
    beginLiveStoreSync();
    message.value =
      `登录进行中：已登录 ${loggedCount.value} 家，未登录 ${unloggedCount.value} 家`;
  }
  try {
    const results = [];
    const errors = [];
    if (isLogin) {
      const result = await call("stores.login", {
        store_ids: storeIds,
        background_refresh_enabled: autoRefresh.enabled.value,
      });
      await load();
      if (result.busy) {
        message.value = result.message || "网页登录任务正在进行，请等待当前窗口结束";
        return;
      }
      const matchedIds = new Set(
        (result.matched_store_ids || []).map((value) => Number(value)),
      );
      const notLoggedInIds = result.not_logged_in_store_ids
        || storeIds.filter((storeId) => !matchedIds.has(Number(storeId)));
      const missingIds = result.missing_store_ids || [];
      const savedCount = Number(result.saved_store_count || matchedIds.size);
      const notes = [];
      if (notLoggedInIds.length) {
        notes.push(
          `${notLoggedInIds.length} 家所选店铺不在该微信账号的店铺列表中`,
        );
      }
      if (missingIds.length) {
        notes.push(`${missingIds.length} 家本地店铺记录不存在`);
      }
      message.value = notes.length
        ? `扫码登录已完成：${notes.join("，")}；平台本次确认 ${savedCount} 家，未再重复打开登录窗口。`
        : `登录完成：所选 ${storeIds.length} 家店铺已纳入本次扫码登录。`;
      return;
    }
    for (const [index, storeId] of storeIds.entries()) {
      message.value = `正在处理店铺 ${index + 1}/${storeIds.length}`;
      try {
        results.push(await call(action, { store_id: storeId }));
      } catch (cause) {
        errors.push(
          `${storeId}: ${cause instanceof Error ? cause.message : String(cause)}`,
        );
      }
    }
    await load();
    message.value = errors.length
      ? `店铺操作完成：成功 ${results.length}/${storeIds.length}，失败 ${errors.length}；${errors.slice(0, 2).join("；")}`
      : `已完成 ${results.length} 家店铺操作`;
  } catch (cause) {
    message.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    if (isLogin) endLiveStoreSync();
    storeActionBusy.value = false;
  }
}

onMounted(async () => {
  document.addEventListener("pointerdown", handleContextPointer, true);
  document.addEventListener("keydown", handleContextKey);
  await load();
  if (stores.value.length) await refreshAll();
  dateRolloverTimer = window.setInterval(reloadAfterDateRollover, 60000);
});
onActivated(reloadAfterDateRollover);
onBeforeUnmount(() => {
  document.removeEventListener("pointerdown", handleContextPointer, true);
  document.removeEventListener("keydown", handleContextKey);
  liveStoreSyncUsers = 0;
  endLiveStoreSync();
  if (dateRolloverTimer !== null) {
    window.clearInterval(dateRolloverTimer);
    dateRolloverTimer = null;
  }
  for (const timer of secretTimers.values()) clearTimeout(timer);
  secretTimers.clear();
  for (const timer of appIdTimers.values()) clearTimeout(timer);
  appIdTimers.clear();
});
</script>

<template>
  <section class="page store-page">
    <header class="page-header">
      <div>
        <h1>店铺管理</h1>
      </div>
      <div class="toolbar">
        <button
          class="btn muted"
          :class="{ 'is-running': loading && refreshProgress.visible }"
          type="button"
          :disabled="loading"
          @click="manualRefreshAll"
        >
          <Icon name="refresh" :size="16" :class="{ spinning: loading }" />
          {{
            loading
              ? "刷新中..."
              : (selectedStoreIds.length ? `刷新选中 (${selectedStoreIds.length})` : "刷新全部店铺")
          }}
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
        <button class="btn muted" type="button" disabled title="提现接口尚未接入">一键提现</button>
        <button class="btn primary" type="button" @click="openCreate">
          <Icon name="plus" :size="16" /> 添加店铺
        </button>
        <button class="btn muted" type="button" @click="openBatchImport">
          批量添加店铺
        </button>
        <button
          class="btn muted store-batch-trigger"
          type="button"
          :disabled="!selectedStoreIds.length"
          :aria-label="`批量操作，已选 ${selectedStoreIds.length} 家店铺`"
          @click="openBatchContextMenu"
        >批量操作{{ selectedStoreIds.length ? ` (${selectedStoreIds.length})` : "" }}</button>
        <button class="btn muted" type="button" :disabled="storeInteractions.selectionActionDisabled" @click="runStoreAction('stores.login')">{{ storeActionBusy ? "处理中..." : "登录" }}</button>
        <button class="btn muted" type="button" :disabled="storeInteractions.selectionActionDisabled" @click="runStoreAction('stores.open-backend')">打开后台</button>
        <button class="btn amber" type="button" @click="runStoreAction('stores.clear-cache')">清理缓存</button>
      </div>
    </header>

    <div class="store-filter-row status-filter-row">
      <div class="store-tabs">
      <button
        class="chip"
        :class="{ active: activeFilter === 'all' }"
        :disabled="!storeInteractions.filtersEnabled"
        type="button"
        @click="activeFilter = 'all'"
      >全部状态 {{ stores.length }}</button>
      <button
        class="chip"
        :class="{ active: activeFilter === 'logged' }"
        :disabled="!storeInteractions.filtersEnabled"
        type="button"
        @click="activeFilter = 'logged'"
      >已登录店铺 {{ loggedCount }}</button>
      <button
        class="chip"
        :class="{ active: activeFilter === 'unlogged' }"
        :disabled="!storeInteractions.filtersEnabled"
        type="button"
        @click="activeFilter = 'unlogged'"
      >未登录店铺 {{ unloggedCount }}</button>
      </div>
      <label class="store-search">
        <span>店铺筛选</span>
        <input v-model="search" class="input" placeholder="输入店铺名称" />
      </label>
      <div class="store-view-switch" role="group" aria-label="店铺表格视图">
        <button
          type="button"
          :class="{ active: storeViewMode === STORE_VIEW_OVERVIEW }"
          :aria-pressed="storeViewMode === STORE_VIEW_OVERVIEW"
          @click="setStoreViewMode(STORE_VIEW_OVERVIEW)"
        >经营总览</button>
        <button
          type="button"
          :class="{ active: storeViewMode === STORE_VIEW_FULL }"
          :aria-pressed="storeViewMode === STORE_VIEW_FULL"
          @click="setStoreViewMode(STORE_VIEW_FULL)"
        >完整表格</button>
      </div>
      <span v-if="selectedStoreIds.length" class="selected-hint">
        已选 {{ selectedStoreIds.length }} 家，刷新时只更新选中店铺
      </span>
    </div>

    <div v-if="canUseStoreGroups" class="store-group-row" aria-label="店铺分组筛选">
      <button
        class="group-chip"
        :class="{ active: groupFilter === '全部店铺' }"
        :disabled="!storeInteractions.groupsEnabled"
        type="button"
        @click="setGroupFilter('全部店铺')"
      >全部店铺 {{ groupStoreCount("全部店铺") }}</button>
      <button
        v-for="group in storeGroups"
        :key="group"
        class="group-chip"
        :class="{ active: groupFilter === group }"
        :disabled="!storeInteractions.groupsEnabled"
        type="button"
        @click="setGroupFilter(group)"
      >{{ group }} {{ groupStoreCount(group) }}</button>
    </div>

    <div
      v-if="refreshProgress.visible"
      class="store-refresh-progress"
      :class="refreshProgress.status"
      role="status"
      aria-live="polite"
    >
      <div class="store-refresh-track">
        <span :style="{ width: `${refreshPercent}%` }" />
      </div>
      <strong>{{ refreshProgress.message }}</strong>
      <small>{{ refreshProgress.current }}/{{ refreshProgress.total }}</small>
      <span class="credential-progress">
        凭据：恢复 {{ refreshProgress.credential_succeeded || 0 }} ·
        放弃 {{ refreshProgress.credential_superseded || 0 }} ·
        拒绝 {{ refreshProgress.credential_failed || 0 }} ·
        延后 {{ refreshProgress.credential_deferred || 0 }}
      </span>
    </div>

    <div class="store-table-legend" aria-label="销售退款颜色图例">
      <span class="legend-sales">绿色：销售额/单量</span>
      <span class="legend-refund">红色：退款金额/单量</span>
    </div>

    <div v-if="storeViewMode === STORE_VIEW_OVERVIEW" class="card data-card store-overview-card">
      <div class="table-scroll overview-table-scroll">
        <table class="store-overview-table" @contextmenu.prevent.stop>
          <thead>
            <tr>
              <th rowspan="2" class="selection-column sticky-select sticky-column">
                <input
                  type="checkbox"
                  :checked="allFilteredSelected"
                  title="全选当前筛选结果"
                  @change="toggleFilteredSelection"
                />
              </th>
              <th rowspan="2" class="sticky-index sticky-column">序号</th>
              <th rowspan="2" class="sticky-name sticky-column">店铺</th>
              <th rowspan="2">商品</th>
              <th colspan="4" class="overview-group-head"><span class="sales-header-tone">销售/</span><span class="refund-header-tone">退款</span><span class="metric-header-neutral">（金额/单量）</span></th>
              <th colspan="3" class="overview-group-head">凭据与登录</th>
              <th rowspan="2">操作</th>
            </tr>
            <tr>
              <th>今日</th>
              <th>昨日</th>
              <th>7日</th>
              <th>30日</th>
              <th>AppID</th>
              <th>AppSecret</th>
              <th>登录状态</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="(store, index) in filteredStores" :key="store.id">
              <tr
                class="overview-main-row"
                :class="{
                  selected: selectedStoreIds.includes(store.id),
                  'row-alternate': index % 2 === 1,
                }"
                @contextmenu.prevent.stop="openStoreContextMenu($event, store)"
              >
                <td class="selection-column sticky-select sticky-column" data-label="选择">
                  <input
                    type="checkbox"
                    :checked="selectedStoreIds.includes(store.id)"
                    :disabled="!storeInteractions.selectionEnabled"
                    @click.stop
                    @change="toggleStoreSelection(store.id)"
                  />
                </td>
                <td class="sticky-index sticky-column" data-label="序号">{{ index + 1 }}</td>
                <td class="sticky-name sticky-column overview-store-cell" :title="store.name" data-label="店铺">
                  <strong>{{ store.name }}</strong>
                  <small>{{ store.group_name || "未分组" }}</small>
                </td>
                <td class="overview-product-cell" data-label="商品">
                  <span :title="onlineProductsPresentation(store).title">在线 {{ onlineProductsPresentation(store).text }}</span>
                  <span :title="newProductsTodayPresentation(store).title">上新 {{ newProductsTodayPresentation(store).text }}</span>
                </td>
                <td
                  v-for="key in ['today', 'yesterday', 'seven_days', 'thirty_days']"
                  :key="key"
                  class="overview-sales-cell"
                  :data-label="{ today: '今日', yesterday: '昨日', seven_days: '7日', thirty_days: '30日' }[key]"
                >
                  <StoreSalesValue :display="salesDisplayFor(store, key)" />
                </td>
                <td class="overview-credential-cell" data-label="AppID">
                  <div class="overview-credential">
                    <button
                      class="app-id-show"
                      type="button"
                      :aria-label="revealedAppIds[store.id] ? '隐藏完整 AppID' : '显示完整 AppID'"
                      :title="revealedAppIds[store.id] ? '隐藏完整 AppID' : '显示完整 AppID'"
                      @click="toggleAppId(store)"
                    >{{ revealedAppIds[store.id] ? "hide" : "show" }}</button>
                    <span class="overview-code">{{ revealedAppIds[store.id] ? store.app_id : maskAppId(store.app_id) }}</span>
                  </div>
                </td>
                <td class="overview-credential-cell" data-label="AppSecret">
                  <div class="overview-credential">
                    <button
                      class="secret-show"
                      type="button"
                      :disabled="secretLoading[store.id]"
                      @click="toggleSecret(store)"
                    >{{ Object.prototype.hasOwnProperty.call(revealedSecrets, store.id) ? "hide" : "show" }}</button>
                    <span class="overview-code">{{ revealedSecrets[store.id] ?? "*******" }}</span>
                  </div>
                </td>
                  <td
                    class="overview-login-cell"
                    data-label="登录状态"
                    :title="store.web_status_detail || store.web_status || store.status || ''"
                  >{{ store.web_status || store.status || "—" }}</td>
                <td class="overview-action-cell" data-label="操作">
                  <button
                    class="pill-button"
                    type="button"
                    :aria-expanded="isStoreExpanded(store.id)"
                    :title="isStoreExpanded(store.id) ? '收起店铺详情' : '展开店铺详情'"
                    @click="toggleStoreDetails(store.id)"
                  >{{ isStoreExpanded(store.id) ? "收起" : "详情" }}</button>
                </td>
              </tr>
              <tr
                v-if="isStoreExpanded(store.id)"
                class="overview-details-row"
                :class="{ 'row-alternate': index % 2 === 1 }"
              >
                <td :colspan="STORE_OVERVIEW_COLUMN_COUNT">
                  <div class="store-detail-grid">
                    <section>
                      <h4>商品曝光人数/商品点击次数</h4>
                      <div v-for="period in EXPOSURE_PERIODS" :key="period.key" class="detail-line">
                        <span>{{ period.label }}</span>
                        <StoreExposureValue :display="exposureDisplayFor(store, period.key)" />
                      </div>
                    </section>
                    <section class="store-detail-sales">
                      <h4>销售/退款（金额/单量）</h4>
                      <div v-for="period in SALES_PERIODS" :key="period.key" class="detail-line">
                        <span>{{ period.label }}</span>
                        <StoreSalesValue :display="salesDisplayFor(store, period.key)" />
                      </div>
                    </section>
                    <section>
                      <h4>资金与保证金</h4>
                      <div class="detail-line"><span>可提现</span><strong :title="moneyMetricTitle(store, 'cash')">{{ moneyMetric(store, "cash") }}</strong></div>
                      <div class="detail-line"><span>待结算</span><strong :title="moneyMetricTitle(store, 'settle')">{{ moneyMetric(store, "settle") }}</strong></div>
                      <div class="detail-line">
                        <span>保证金</span>
                        <strong :title="depositDisplayFor(store).title">{{ depositDisplayFor(store).text }}</strong>
                      </div>
                    </section>
                    <section>
                      <h4>体验分</h4>
                      <div v-for="key in STORE_SCORE_FIELDS" :key="key" class="detail-line">
                        <span>{{ scorePresentation(store, key).label }}</span>
                        <strong :title="scorePresentation(store, key).valueTitle">
                          {{ scorePresentation(store, key).valueText }}
                          <small
                            v-if="scorePresentation(store, key).changeText"
                            :class="scorePresentation(store, key).changeDirection"
                            :title="scorePresentation(store, key).changeTitle"
                          >
                            {{ scorePresentation(store, key).changeText }}
                          </small>
                        </strong>
                      </div>
                    </section>
                    <section>
                      <h4>店铺操作</h4>
                      <div class="action-group detail-actions">
                        <button class="pill-button" type="button" @click="openEdit(store)">修改</button>
                        <button class="pill-button" type="button" @click="openCredentialDetail(store)">凭据</button>
                        <button class="pill-button" type="button" disabled title="提现接口尚未接入">提现</button>
                        <button class="pill-button red" type="button" @click="openDelete(store)">删除</button>
                        <button v-if="canUseStoreGroups" class="pill-button gray" type="button" @click="openGroup(store)">分组</button>
                      </div>
                      <small class="detail-freshness">数据更新时间：{{ store.metrics?.updated_at || "—" }}</small>
                    </section>
                  </div>
                </td>
              </tr>
            </template>
            <tr v-if="!loading && filteredStores.length === 0">
              <td :colspan="STORE_OVERVIEW_COLUMN_COUNT" class="empty-state">
                {{ activeFilter === "all" && !search ? "暂无店铺" : "当前筛选条件下没有店铺" }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div v-else class="card data-card store-table-card">
      <div class="table-scroll">
        <table
          class="data-table store-table"
          @contextmenu.prevent.stop
        >
          <thead>
            <tr>
              <th class="selection-column sticky-select sticky-column" rowspan="2">
                <input
                  type="checkbox"
                  :checked="allFilteredSelected"
                  title="全选当前筛选结果"
                  @change="toggleFilteredSelection"
                />
              </th>
              <th class="sticky-index sticky-column" rowspan="2">序号</th>
              <th class="sticky-name sticky-column" rowspan="2">店铺名称</th>
              <th colspan="3" class="exposure-group-head">商品曝光人数/商品点击次数</th>
              <th rowspan="2">在线商品数</th>
              <th rowspan="2">今日上新</th>
              <th colspan="4" class="sales-group-head"><span class="sales-header-tone">销售/</span><span class="refund-header-tone">退款</span><span class="metric-header-neutral">（金额/单量）</span></th>
              <th rowspan="2">可提现余额</th>
              <th rowspan="2">待结算余额</th>
              <th rowspan="2">体验分</th>
              <th rowspan="2">商品分</th>
              <th rowspan="2">物流分</th>
              <th rowspan="2">服务分</th>
              <th rowspan="2">保证金</th>
              <th rowspan="2">AppID</th>
              <th rowspan="2">AppSecret</th>
              <th rowspan="2">登录状态</th>
              <th rowspan="2">操作</th>
            </tr>
            <tr>
              <th class="count-cell">今日</th>
              <th class="count-cell">昨日</th>
              <th class="count-cell">7日</th>
              <th class="count-cell">今日</th>
              <th class="count-cell">昨日</th>
              <th class="count-cell">7日</th>
              <th class="count-cell">30日</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="(store, index) in filteredStores"
              :key="store.id"
              :class="{
                selected: selectedStoreIds.includes(store.id),
                'row-alternate': index % 2 === 1,
              }"
              @contextmenu.prevent.stop="openStoreContextMenu($event, store)"
            >
              <td class="selection-column sticky-select sticky-column">
                <input
                  type="checkbox"
                  :checked="selectedStoreIds.includes(store.id)"
                  :disabled="!storeInteractions.selectionEnabled"
                  @change="toggleStoreSelection(store.id)"
                />
              </td>
              <td class="sticky-index sticky-column">{{ index + 1 }}</td>
              <td class="sticky-name sticky-column" :title="store.name">
                {{ store.name }}
              </td>
              <td v-for="key in ['today', 'yesterday', 'seven_days']" :key="key" class="count-cell">
                <StoreExposureValue :display="exposureDisplayFor(store, key)" />
              </td>
              <td class="online-product-cell" :title="onlineProductsPresentation(store).title">
                {{ onlineProductsPresentation(store).text }}
              </td>
              <td class="new-products-cell" :title="newProductsTodayPresentation(store).title">
                {{ newProductsTodayPresentation(store).text }}
              </td>
              <td v-for="key in ['today', 'yesterday', 'seven_days', 'thirty_days']" :key="key" class="money full-sales-cell">
                <StoreSalesValue :display="salesDisplayFor(store, key)" />
              </td>
              <td class="money" :title="moneyMetricTitle(store, 'cash')">{{ moneyMetric(store, "cash") }}</td>
              <td class="money" :title="moneyMetricTitle(store, 'settle')">{{ moneyMetric(store, "settle") }}</td>
              <td v-for="key in STORE_SCORE_FIELDS" :key="key" class="score-cell">
                <span :title="scorePresentation(store, key).valueTitle">{{ scorePresentation(store, key).valueText }}</span>
                <small
                  v-if="scorePresentation(store, key).changeText"
                  :class="scorePresentation(store, key).changeDirection"
                  :title="scorePresentation(store, key).changeTitle"
                >{{ scorePresentation(store, key).changeText }}</small>
                <em
                  v-if="scorePresentation(store, key).statusLabel"
                  :title="scorePresentation(store, key).statusTitle"
                >{{ scorePresentation(store, key).statusLabel }}</em>
              </td>
              <td class="deposit-cell">
                <span :title="depositDisplayFor(store).title">
                  {{ depositDisplayFor(store).text }}
                </span>
              </td>
              <td class="app-id-cell">
                <div class="secret-reveal">
                  <button
                    class="app-id-show"
                    type="button"
                    :aria-label="revealedAppIds[store.id] ? '隐藏完整 AppID' : '显示完整 AppID'"
                    :title="revealedAppIds[store.id] ? '隐藏完整 AppID' : '显示完整 AppID'"
                    @click="toggleAppId(store)"
                  >{{ revealedAppIds[store.id] ? "hide" : "show" }}</button>
                  <span class="app-id-placeholder">{{ revealedAppIds[store.id] ? store.app_id : maskAppId(store.app_id) }}</span>
                </div>
              </td>
              <td class="secret-cell">
                <div class="secret-reveal">
                  <button
                    class="secret-show"
                    type="button"
                    :disabled="secretLoading[store.id]"
                    @click="toggleSecret(store)"
                  >{{ Object.prototype.hasOwnProperty.call(revealedSecrets, store.id) ? "hide" : "show" }}</button>
                  <span class="secret-placeholder">{{ revealedSecrets[store.id] ?? "*******" }}</span>
                </div>
              </td>
              <td :title="store.web_status_detail || store.web_status || store.status || ''">{{ store.web_status || store.status || "—" }}</td>
              <td>
                <div class="action-group">
                  <button class="pill-button" type="button" @click="openEdit(store)">修改</button>
                  <button class="pill-button" type="button" @click="openCredentialDetail(store)">凭据</button>
                  <button class="pill-button" type="button" disabled title="提现接口尚未接入">提现</button>
                  <button class="pill-button red" type="button" @click="openDelete(store)">删除</button>
                  <button v-if="canUseStoreGroups" class="pill-button gray" type="button" @click="openGroup(store)">分组</button>
                </div>
              </td>
            </tr>
            <tr v-if="!loading && filteredStores.length === 0">
              <td :colspan="STORE_TABLE_COLUMN_COUNT" class="empty-state">
                {{ activeFilter === "all" && !search ? "暂无店铺" : "当前筛选条件下没有店铺" }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <footer class="summary-strip store-summary">
      <div class="summary-cell" title="销售额与订单量来自官方成交统计，并扣除未领取礼物">
        <span>今日销售合计</span>
        <strong>{{ summaryMoney(summary.today) }} · {{ summaryCount(summary.today_orders) }}</strong>
        <small v-if="isRefundVisible(summary.refund_today, summary.refund_today_orders)">退款 {{ summaryMoney(summary.refund_today) }} · {{ summaryCount(summary.refund_today_orders) }}</small>
      </div>
      <div class="summary-cell" title="销售额与订单量来自官方成交统计，并扣除未领取礼物">
        <span>昨日销售合计</span>
        <strong>{{ summaryMoney(summary.yesterday) }} · {{ summaryCount(summary.yesterday_orders) }}</strong>
        <small v-if="isRefundVisible(summary.refund_yesterday, summary.refund_yesterday_orders)">退款 {{ summaryMoney(summary.refund_yesterday) }} · {{ summaryCount(summary.refund_yesterday_orders) }}</small>
      </div>
      <div class="summary-cell" title="销售额与订单量来自官方成交统计，并扣除未领取礼物">
        <span>7日销售合计</span>
        <strong>{{ summaryMoney(summary.week) }} · {{ summaryCount(summary.week_orders) }}</strong>
        <small v-if="isRefundVisible(summary.refund_week, summary.refund_week_orders)">退款 {{ summaryMoney(summary.refund_week) }} · {{ summaryCount(summary.refund_week_orders) }}</small>
      </div>
      <div class="summary-cell" title="销售额与订单量来自官方成交统计，并扣除未领取礼物">
        <span>30日销售合计</span>
        <strong>{{ summaryMoney(summary.month) }} · {{ summaryCount(summary.month_orders) }}</strong>
        <small v-if="isRefundVisible(summary.refund_month, summary.refund_month_orders)">退款 {{ summaryMoney(summary.refund_month) }} · {{ summaryCount(summary.refund_month_orders) }}</small>
      </div>
      <div class="summary-cell"><span>可提现余额</span><strong>{{ summaryMoney(summary.cash) }}</strong></div>
      <div class="summary-cell"><span>待结算余额</span><strong>{{ summaryMoney(summary.settle) }}</strong></div>
    </footer>

    <div
      v-if="contextMenu.open"
      class="store-context-menu"
      role="menu"
      aria-label="店铺批量操作菜单"
      :style="{ left: `${contextMenu.x}px`, top: `${contextMenu.y}px` }"
      @contextmenu.prevent.stop
    >
      <header>已选 {{ contextMenu.storeIds.length }} 家店铺</header>
      <button role="menuitem" type="button" @click="selectCurrentContextStore">选择当前项</button>
      <button role="menuitem" type="button" @click="selectAllCurrentPage">全选当前页</button>
      <button role="menuitem" type="button" @click="selectAllFiltered">全选筛选结果</button>
      <button role="menuitem" type="button" @click="invertFilteredSelection">反选</button>
      <button role="menuitem" type="button" @click="clearStoreSelection">取消选择</button>
      <button role="menuitem" type="button" @click="refreshContextStores">刷新选中项</button>
      <button role="menuitem" type="button" @click="copySelectedStoreIds">复制选中项编号</button>
      <hr />
      <button role="menuitem" type="button" :disabled="storeActionBusy" @click="runContextStoreAction('stores.login')">批量登录</button>
      <button v-if="canUseStoreGroups" role="menuitem" type="button" @click="openBatchGroupDialog(false)">批量设置分组…</button>
      <button v-if="canUseStoreGroups" role="menuitem" type="button" @click="openBatchGroupDialog(true)">新建分组…</button>
      <button role="menuitem" type="button" :disabled="storeActionBusy" @click="runContextStoreAction('stores.open-backend')">打开后台</button>
      <button class="danger" role="menuitem" type="button" @click="askBatchDelete">批量删除所选…</button>
    </div>

    <ModalDialog
      v-if="canUseStoreGroups"
      :open="batchGroupDialog.open"
      :title="`批量设置分组（${batchGroupDialog.storeIds.length} 家）`"
      width="520px"
      :busy="batchGroupDialog.busy"
      @close="batchGroupDialog.open = false"
    >
      <div class="batch-group-dialog">
        <p>所选 {{ batchGroupDialog.storeIds.length }} 家店铺将统一使用同一分组。</p>
        <div class="batch-group-options">
          <button
            v-for="group in storeGroups"
            :key="group"
            type="button"
            :class="{ active: batchGroupDialog.groupName === group }"
            @click="batchGroupDialog.groupName = group"
          >{{ group }}</button>
        </div>
        <label>
          <span>新分组或已有分组名称</span>
          <input
            v-model="batchGroupDialog.groupName"
            class="input"
            maxlength="80"
            placeholder="输入分组名称"
            :disabled="batchGroupDialog.busy"
          />
        </label>
        <p class="batch-group-note">“设为未分组”会清空这些店铺的分组，不会删除店铺。</p>
      </div>
      <template #footer>
        <button class="btn" type="button" :disabled="batchGroupDialog.busy" @click="batchGroupDialog.open = false">取消</button>
        <button class="btn" type="button" :disabled="batchGroupDialog.busy" @click="saveBatchGroup({ allowEmpty: true })">设为未分组</button>
        <button class="btn primary" type="button" :disabled="batchGroupDialog.busy" @click="saveBatchGroup()">{{ batchGroupDialog.busy ? "保存中…" : "保存分组" }}</button>
      </template>
    </ModalDialog>

    <ModalDialog
      :open="['create', 'edit'].includes(dialogMode)"
      :title="dialogMode === 'create' ? '添加店铺' : '修改店铺信息'"
      @close="closeDialog"
    >
      <div class="form-grid">
        <div class="field">
          <label>Appid</label>
          <input v-model.trim="form.app_id" class="input" />
        </div>
        <div class="field">
          <label>AppSecret</label>
          <input v-model="form.secret" class="input" />
        </div>
      </div>
      <p v-if="message" class="form-error">{{ message }}</p>
      <template #footer>
        <button class="btn" type="button" :disabled="storeSaveBusy" @click="closeDialog">取消</button>
        <button class="btn primary" type="button" :disabled="storeSaveBusy || !form.app_id.trim()" @click="saveStore">{{ storeSaveBusy ? "保存中…" : "保存" }}</button>
      </template>
    </ModalDialog>

    <ModalDialog
      :open="dialogMode === 'delete'"
      title="删除店铺"
      :subtitle="selectedStore ? `确定删除“${selectedStore.name}”吗？` : ''"
      width="520px"
      @close="closeDialog"
    >
      <p class="delete-warning">店铺本地数据和登录缓存会一并移除，此操作不可撤销。</p>
      <template #footer>
        <button class="btn" type="button" :disabled="storeSaveBusy" @click="closeDialog">取消</button>
        <button class="btn danger" type="button" :disabled="storeSaveBusy" @click="deleteStore">{{ storeSaveBusy ? "删除中…" : "确认删除" }}</button>
      </template>
    </ModalDialog>

    <ModalDialog
      v-if="canUseStoreGroups"
      :open="dialogMode === 'group'"
      title="修改分组"
      :subtitle="`店铺分组 - ${selectedStore?.name || ''}`"
      width="560px"
      @close="closeDialog"
    >
      <div class="group-manager">
        <section>
          <label>已有分组（点击选择）</label>
          <div class="group-list">
            <button
              v-for="group in groups"
              :key="group"
              type="button"
              :class="{ active: form.group_name === group }"
              @click="form.group_name = group"
            >{{ group }}</button>
            <span v-if="groups.length === 0">暂无分组</span>
          </div>
        </section>
        <section class="new-group-row">
          <label>新建/删除分组</label>
          <div class="new-group-controls">
            <input
              v-model.trim="newGroupName"
              class="input"
              placeholder="新分组名称"
              @keyup.enter="createGroup"
            />
            <button class="btn primary" type="button" @click="createGroup">添加分组</button>
            <button class="btn danger" type="button" :disabled="!form.group_name" @click="deleteGroup">删除分组</button>
          </div>
        </section>
      </div>
      <template #footer>
        <button class="btn" type="button" @click="clearGroup">清除分组</button>
        <button class="btn" type="button" @click="closeDialog">取消</button>
        <button class="btn primary" type="button" :disabled="!form.group_name" @click="saveGroup">
          移动到 {{ form.group_name || "..." }}
        </button>
      </template>
    </ModalDialog>

    <Teleport to="body">
      <div v-if="batchDialog" class="batch-store-overlay">
        <section class="batch-store-drawer" aria-label="批量添加店铺">
          <header class="batch-store-head">
            <div>
              <h2>批量添加店铺</h2>
              <p>导入与预检均在本地完成，确认前不会访问平台。</p>
            </div>
            <button class="round-close" type="button" aria-label="关闭" @click="batchDialog = false">×</button>
          </header>
          <div class="batch-store-body">
            <div class="batch-steps">
              <div class="done"><b>01 导入来源</b>粘贴、CSV 或 XLSX</div>
              <div :class="{ active: batchPreview }"><b>02 本地预检</b>按 AppID 与失效状态分类</div>
              <div :class="{ active: batchPreview }"><b>03 保存候选</b>刷新时验证替换</div>
            </div>

            <section class="batch-source-card">
              <p class="batch-section-label">凭据来源</p>
              <div class="batch-source-tabs">
                <button type="button" :class="{ active: batchSourceKind === 'paste' }" @click="selectBatchSource('paste')">粘贴文本</button>
                <button type="button" :class="{ active: batchSourceKind === 'csv' }" @click="selectBatchSource('csv')">CSV</button>
                <button type="button" :class="{ active: batchSourceKind === 'xlsx' }" @click="selectBatchSource('xlsx')">XLSX</button>
              </div>
              <textarea
                v-if="batchSourceKind === 'paste'"
                v-model="batchText"
                class="input batch-source-text"
                spellcheck="false"
                placeholder="店铺名称  AppID  AppSecret  分组&#10;也支持表头：店铺名称,AppID,AppSecret,分组&#10;或每两行一组：第一行 AppID，第二行 AppSecret"
                @input="resetBatchPreview"
              ></textarea>
              <div v-else class="batch-file-picker">
                <button class="btn" type="button" @click="chooseBatchFile">选择 {{ batchSourceKind.toUpperCase() }} 文件</button>
                <span>{{ batchFileName || `支持 UTF-8、UTF-8 BOM${batchSourceKind === 'csv' ? '、GB18030' : ''}` }}</span>
                <input ref="batchFileInput" type="file" hidden :accept="batchSourceKind === 'csv' ? '.csv,text/csv' : '.xlsx'" @change="batchFileChanged" />
              </div>
              <div class="batch-source-tools">
                <button class="btn primary" type="button" :disabled="batchBusy" @click="previewBatch">{{ batchBusy ? "本地解析中…" : "解析并重新预检" }}</button>
                <button class="btn" type="button" @click="downloadStoreTemplate">下载 CSV 模板</button>
                <span>{{ batchPreview ? `${batchPreview.rows.length} 行 · 未联网` : "密钥只在本地内存与加密数据库中处理" }}</span>
              </div>
            </section>

            <div v-if="batchPreview" class="batch-summary-grid">
              <article class="new"><span>新增</span><strong>{{ batchCounts.new }}</strong></article>
              <article><span>跳过</span><strong>{{ batchCounts.skip }}</strong></article>
              <article class="replace"><span>待替换</span><strong>{{ batchCounts.replace_candidate }}</strong></article>
              <article class="invalid"><span>格式错误</span><strong>{{ batchCounts.invalid }}</strong></article>
              <article><span>重复行</span><strong>{{ batchCounts.duplicate }}</strong></article>
              <article class="conflict"><span>名称冲突</span><strong>{{ batchCounts.name_conflict }}</strong></article>
            </div>

            <template v-if="batchPreview">
              <div class="batch-preview-tools">
                <button v-for="item in [['all','全部'],['new','新增'],['skip','跳过'],['replace_candidate','待替换'],['exception','异常']]" :key="item[0]" type="button" class="chip" :class="{ active: batchFilter === item[0] }" @click="batchFilter = item[0]">{{ item[1] }}</button>
                <input v-model="batchSearch" class="input" placeholder="筛选店铺名称或 AppID" />
              </div>
              <div class="batch-preview-table-wrap">
                <table class="batch-preview-table">
                  <thead><tr><th><input type="checkbox" :checked="allSelectableBatchRowsSelected" @change="toggleAllBatchRows" /></th><th>结果</th><th>店铺 / AppID</th><th>预检说明</th><th>分组</th><th>来源</th><th></th></tr></thead>
                  <tbody>
                    <tr v-for="row in filteredBatchRows" :key="row.row_number">
                      <td><input type="checkbox" :checked="batchSelectedRows.includes(row.row_number)" :disabled="!row.selectable" @change="toggleBatchRow(row.row_number)" /></td>
                      <td><span class="batch-status" :class="row.status">{{ batchStatusLabel(row.status) }}</span></td>
                      <td>
                        <strong>{{ row.name || "未填写" }}</strong>
                        <small class="mono">{{ row.app_id || "无 AppID" }}</small>
                        <small>密钥 {{ row.secret_masked || "未填写" }} · 指纹 {{ row.secret_fingerprint_prefix || "-" }}</small>
                        <input
                          v-if="row.status === 'name_conflict'"
                          v-model="batchNameOverrides[String(row.row_number)]"
                          class="input conflict-name-input"
                          placeholder="输入新的店铺显示名称"
                        />
                      </td>
                      <td>{{ row.reason }}</td>
                      <td>{{ row.group_name || "-" }}</td>
                      <td>{{ row.source_name }} · 第 {{ row.row_number }} 行</td>
                      <td><button type="button" class="link-button" @click="batchInspectRow = row">查看</button></td>
                    </tr>
                    <tr v-if="!filteredBatchRows.length"><td colspan="7" class="batch-empty">当前筛选条件下没有记录</td></tr>
                  </tbody>
                </table>
              </div>
              <p class="batch-name-hint">同名不同 AppID 需先在行内改名，再点击“解析并重新预检”。</p>
            </template>
          </div>
          <footer class="batch-store-foot">
            <span>保存后只新增店铺或写入“替换候选”。旧密钥可用或未知时不会修改；新密钥仅在后续刷新验证成功后替换。</span>
            <i></i>
            <button class="btn" type="button" :disabled="batchBusy" @click="batchDialog = false">取消</button>
            <button class="btn primary" type="button" :disabled="batchBusy || !batchSelectedRows.length" @click="commitBatchImport">
              {{ batchBusy ? "保存中…" : `确认保存 ${batchSelectedRows.length} 条` }}
            </button>
          </footer>
        </section>
      </div>
    </Teleport>

    <ModalDialog
      :open="credentialDialog"
      title="店铺凭据状态"
      :subtitle="credentialDetail ? `${credentialDetail.store_name} · ${credentialDetail.app_id}` : '读取本地凭据状态与候选'"
      width="760px"
      @close="credentialDialog = false"
    >
      <div v-if="credentialLoading" class="credential-loading">正在读取本地凭据记录…</div>
      <div v-else-if="credentialDetail" class="credential-detail">
        <div class="credential-state-card" :class="credentialDetail.state.state">
          <span>当前密钥状态</span>
          <strong>{{ credentialStateLabel(credentialDetail.state.state) }}</strong>
          <small>最近验证 {{ credentialDetail.state.last_probe_at || "暂无记录" }}</small>
          <small v-if="credentialDetail.state.last_error_message">{{ credentialDetail.state.last_error_message }}</small>
        </div>
        <section>
          <h3>候选密钥</h3>
          <div v-if="!credentialDetail.candidates.length" class="credential-empty">暂无候选密钥</div>
          <div v-for="candidate in credentialDetail.candidates" :key="candidate.id" class="credential-candidate">
            <span class="batch-status" :class="candidate.state">{{ candidateStateLabel(candidate.state) }}</span>
            <div><strong>指纹 {{ candidate.fingerprint_prefix }}</strong><small>{{ candidate.source_name }} · 第 {{ candidate.source_row }} 行 · {{ candidate.updated_at }}</small></div>
            <small v-if="candidate.last_error_message">{{ candidate.last_error_message }}</small>
          </div>
        </section>
        <section>
          <h3>最近凭据审计</h3>
          <div v-if="!credentialDetail.audit.length" class="credential-empty">暂无审计记录</div>
          <div v-for="row in credentialDetail.audit" :key="row.id" class="credential-audit-row">
            <strong>{{ row.operation }}</strong>
            <span>{{ row.message }}</span>
            <small>{{ row.started_at }}</small>
          </div>
        </section>
        <section class="cookie-import-section">
          <h3>导入网页 Cookie Header</h3>
          <p>仅用于恢复当前店铺网页登录态。内容提交后立即清空，不显示、不回传原始 Cookie。</p>
          <textarea v-model="cookieImportText" class="input cookie-import-input" placeholder="粘贴 Cookie Header；不会保存到前端缓存" :disabled="cookieImportBusy" />
          <button class="btn primary" type="button" :disabled="cookieImportBusy || !cookieImportText.trim()" @click="importStoreCookie">{{ cookieImportBusy ? "导入中…" : "导入并验证登录态" }}</button>
        </section>
      </div>
      <template #footer>
        <button class="btn" type="button" :disabled="credentialProbeBusy" @click="probeCurrentCredential">{{ credentialProbeBusy ? "检查中…" : "检查当前密钥" }}</button>
        <button class="btn" type="button" @click="refreshCredentialStore">刷新验证候选</button>
        <button class="btn primary" type="button" @click="credentialDialog = false">关闭</button>
      </template>
    </ModalDialog>

    <ModalDialog
      :open="Boolean(batchInspectRow)"
      title="批量导入行详情"
      width="560px"
      @close="batchInspectRow = null"
    >
      <div v-if="batchInspectRow" class="batch-inspect">
        <div><span>结果</span><strong>{{ batchStatusLabel(batchInspectRow.status) }}</strong></div>
        <div><span>店铺名称</span><strong>{{ batchInspectRow.name || "未填写" }}</strong></div>
        <div><span>AppID</span><strong class="mono">{{ batchInspectRow.app_id || "未填写" }}</strong></div>
        <div><span>AppSecret</span><strong>{{ batchInspectRow.secret_masked || "未填写" }}</strong></div>
        <div><span>来源</span><strong>{{ batchInspectRow.source_name }} · 第 {{ batchInspectRow.row_number }} 行</strong></div>
        <div><span>原因</span><strong>{{ batchInspectRow.reason }}</strong></div>
      </div>
      <template #footer>
        <button class="btn primary" type="button" @click="batchInspectRow = null">关闭</button>
      </template>
    </ModalDialog>

    <ModalDialog
      :open="confirmDialog.open"
      :title="confirmDialog.title"
      width="520px"
      :busy="confirmDialog.busy"
      @close="confirmDialog.open = false"
    >
      <p class="delete-warning">{{ confirmDialog.message }}</p>
      <template #footer>
        <button class="btn" type="button" :disabled="confirmDialog.busy" @click="confirmDialog.open = false">取消</button>
        <button class="btn danger" type="button" :disabled="confirmDialog.busy" @click="runConfirm">{{ confirmDialog.busy ? "处理中…" : confirmDialog.confirmLabel }}</button>
      </template>
    </ModalDialog>

    <button
      class="runtime-notice-toggle"
      type="button"
      :aria-pressed="runtimeNoticeVisible ? 'true' : 'false'"
      :title="runtimeNoticeVisible ? '运行提示已开启，点击隐藏提示框' : '运行提示已关闭，点击恢复提示框'"
      :aria-label="runtimeNoticeVisible ? '关闭运行提示' : '开启运行提示'"
      @click="toggleRuntimeNotice"
    >
      <span aria-hidden="true">{{ runtimeNoticeVisible ? "◉" : "○" }}</span>
      运行提示 {{ runtimeNoticeVisible ? "开" : "关" }}
    </button>
    <div v-if="message && !dialogMode && runtimeNoticeVisible" class="toast">{{ message }}</div>
  </section>
</template>

<style scoped>
.store-tabs {
  display: flex;
  gap: 8px;
}

 .store-view-switch {
   margin-left: auto;
   padding: 2px;
   display: inline-flex;
   gap: 2px;
   border: 1px solid #dbe3e9;
   border-radius: 6px;
   background: #f4f7f9;
 }

 .store-view-switch button {
   height: 27px;
   padding: 0 10px;
   border: 0;
   border-radius: 4px;
   color: #637381;
   background: transparent;
   font-size: 10px;
   white-space: nowrap;
 }

 .store-view-switch button.active {
   color: #175f45;
   background: #fff;
   box-shadow: 0 1px 4px rgba(23, 52, 73, 0.13);
 }

 .store-context-menu {
   position: fixed;
   z-index: 140;
   width: 246px;
   max-height: min(470px, calc(100vh - 16px));
   overflow-y: auto;
   padding: 5px;
   border: 1px solid #cfd9e1;
   border-radius: 8px;
   background: #fff;
   box-shadow: 0 14px 36px rgba(19, 42, 61, 0.22);
 }

 .store-context-menu hr {
   margin: 4px 8px;
   border: 0;
   border-top: 1px solid #e3e9ef;
 }

 .store-context-menu header {
   padding: 7px 9px 6px;
   color: #84919c;
   font-size: 9px;
   border-bottom: 1px solid #edf1f4;
 }

 .store-context-menu button {
   width: 100%;
   min-height: 31px;
   padding: 0 9px;
   border: 0;
   border-radius: 4px;
   color: #3f5363;
   background: transparent;
   font-size: 11px;
   text-align: left;
 }

 .store-context-menu button:hover,
 .store-context-menu button:focus-visible {
   background: #eef5fb;
 }

 .store-context-menu button.danger {
   color: #b23846;
 }

 .store-context-menu button:disabled {
   opacity: .45;
   cursor: not-allowed;
 }

 .batch-group-dialog {
   display: grid;
   gap: 13px;
 }

 .batch-group-dialog > p {
   margin: 0;
   color: #5d7080;
   font-size: 11px;
   line-height: 1.6;
 }

 .batch-group-dialog > label {
   display: grid;
   gap: 6px;
   color: #556a79;
   font-size: 10px;
 }

 .batch-group-options {
   max-height: 130px;
   padding: 5px;
   display: flex;
   flex-wrap: wrap;
   gap: 5px;
   overflow: auto;
   border: 1px solid #e0e7ec;
   border-radius: 6px;
   background: #fafcfd;
 }

 .batch-group-options button {
   min-height: 27px;
   padding: 0 9px;
   border: 1px solid #dce4ea;
   border-radius: 999px;
   color: #617383;
   background: #fff;
   font-size: 9px;
 }

 .batch-group-options button.active {
   border-color: #1b6b50;
   color: #fff;
   background: #1b6b50;
 }

 .batch-group-note {
   color: #8a6a2c !important;
   font-size: 9px !important;
 }

 .store-table-legend {
   min-height: 32px;
   padding: 0 12px;
   display: flex;
   align-items: center;
   justify-content: flex-end;
   gap: 16px;
   border-bottom: 1px solid #e3e9ed;
   background: #fbfcfd;
   font-size: 11px;
   white-space: nowrap;
 }

 .legend-sales {
   color: var(--store-sales-color);
 }

 .legend-refund {
   color: var(--store-refund-color);
 }

 .sales-header-tone {
   color: var(--store-sales-color);
 }

 .refund-header-tone {
   color: var(--store-refund-color);
 }

 .metric-header-neutral {
   color: #5e6f7e;
 }

 .store-overview-card {
   flex: 1;
   min-height: 0;
   display: flex;
   flex-direction: column;
   overflow: hidden;
 }

 .overview-table-scroll {
   width: 100%;
   min-width: 0;
   overflow-x: auto;
 }

 .store-overview-table {
   --store-table-heading-font-size: 11px;
   --store-table-data-font-size: 11px;
   --store-table-secondary-font-size: 10px;
   --store-table-sales-single-font-size: 14.5px;
   --store-table-sales-double-font-size: 12px;
   width: max-content;
   min-width: 100%;
   table-layout: auto;
   border-collapse: separate;
   border-spacing: 0;
 }

 .store-overview-table th {
   height: 36px;
   padding: 0 5px;
   border-bottom: 1px solid #dce4ea;
   color: #5e6f7e;
   background: #f7f9fa;
   font-size: var(--store-table-heading-font-size);
   font-weight: 600;
   text-align: center;
   white-space: nowrap;
 }

 .store-overview-table thead tr:first-child th {
   position: sticky;
   top: 0;
   z-index: 6;
 }

 .store-overview-table thead tr:nth-child(2) th {
   position: sticky;
   top: 36px;
   z-index: 6;
 }

 .store-overview-table th.sticky-column,
 .store-overview-table td.sticky-column {
   position: sticky;
   background: var(--store-row-bg, #fff);
 }

 .store-overview-table th.sticky-column {
   z-index: 9;
   background: #f7f9fa;
 }

 .store-overview-table .sticky-select { left: 0; }
 .store-overview-table .sticky-index { left: 34px; }
 .store-overview-table .sticky-name {
   left: 72px;
   box-shadow: 8px 0 12px -11px rgba(28, 48, 66, 0.72);
 }

 .store-overview-table td {
   height: 60px;
   padding: 5px;
   border-bottom: 1px solid #edf1f4;
   color: #4a5d6c;
   background: var(--store-row-bg, #fff);
   font-size: var(--store-table-data-font-size);
   text-align: center;
   vertical-align: middle;
 }

 .store-overview-table th,
 .store-overview-table td {
   white-space: nowrap;
 }

 .store-overview-table .selection-column {
   width: 34px;
   min-width: 34px;
 }

 .store-overview-table .sticky-index {
   width: 38px;
   min-width: 38px;
 }

 .store-overview-table .sticky-name {
   min-width: 150px;
 }

 .store-overview-table .overview-product-cell {
   min-width: 112px;
 }

 .store-overview-table .overview-sales-cell {
    min-width: 120px;
 }

 .store-overview-table .overview-credential-cell {
    min-width: 112px;
 }

 .store-overview-table tbody tr {
   --store-row-bg: #fff;
 }

 .store-overview-table tbody tr.row-alternate {
   --store-row-bg: #f7f9fb;
 }

 .store-overview-table tbody tr:hover {
   --store-row-bg: #f1f6f9;
 }

 .store-overview-table tbody tr.selected {
   --store-row-bg: #eaf3fb;
 }

 .overview-main-row:hover + .overview-details-row {
   --store-row-bg: #f1f6f9;
 }

 .overview-main-row.selected + .overview-details-row {
   --store-row-bg: #eaf3fb;
 }

 .overview-store-cell {
   text-align: center !important;
 }

 .overview-store-cell strong,
 .overview-store-cell small,
 .overview-product-cell span {
   display: block;
   white-space: nowrap;
 }

 .overview-store-cell strong {
   color: #243746;
   font-size: var(--store-table-data-font-size);
 }

 .overview-store-cell small {
   margin-top: 3px;
   color: #87949f;
   font-size: var(--store-table-secondary-font-size);
 }

 .overview-product-cell span + span {
   margin-top: 3px;
 }

 .overview-sales-cell {
   font-variant-numeric: tabular-nums;
   line-height: 1.45;
 }

 .overview-credential {
   min-width: 0;
   display: flex;
   align-items: center;
   gap: 4px;
   justify-content: center;
 }

 .overview-code {
   min-width: 0;
   white-space: nowrap;
   color: #657682;
   font-family: "Cascadia Code", Consolas, monospace;
   font-size: var(--store-table-secondary-font-size);
   line-height: 1.25;
 }

 .overview-credential button {
   flex: 0 0 auto;
   height: 23px;
   padding: 0 5px;
   font-size: var(--store-table-secondary-font-size);
 }

 .overview-login-cell {
   white-space: nowrap;
 }

 .overview-details-row > td {
   padding: 0;
   background: var(--store-row-bg, #fff);
 }

 .store-detail-grid {
   padding: 12px 14px 14px;
   display: grid;
   grid-template-columns: repeat(5, minmax(180px, 1fr));
   gap: 10px;
   border-bottom: 1px solid #dbe4eb;
 }

 .store-detail-grid section {
   min-width: 0;
   padding: 10px;
   border: 1px solid #e1e8ed;
   border-radius: 7px;
   background: #fff;
   text-align: left;
 }

 .store-detail-grid h4 {
   margin: 0 0 8px;
   color: #405565;
   font-size: var(--store-table-data-font-size);
 }

 .detail-line {
   min-height: 25px;
   display: flex;
   align-items: center;
   justify-content: space-between;
   gap: 8px;
   border-bottom: 1px solid #f0f3f5;
   color: #758491;
   font-size: var(--store-table-secondary-font-size);
   white-space: nowrap;
 }

 .store-detail-sales .detail-line {
   min-height: 45px;
 }

 .detail-line:last-child { border-bottom: 0; }
 .detail-line strong { color: #384e5f; font-weight: 600; text-align: right; }
 .detail-line small.up,
 .detail-line small.down {
   color: #d94350;
   font-size: var(--store-table-secondary-font-size);
 }
 .detail-line small.down { color: #15905d; }
 .detail-actions { display: flex; flex-wrap: wrap; gap: 5px; }
 .detail-freshness {
   margin-top: 8px;
   display: block;
   color: #8a96a0;
   font-size: var(--store-table-secondary-font-size);
 }

.store-filter-row {
  min-height: 46px;
  margin-bottom: 0;
  display: flex;
  align-items: center;
  gap: 12px;
}

.status-filter-row {
  padding-bottom: 9px;
}

.btn.is-running {
  border-color: #35404c;
  background: #4b5662;
  box-shadow: inset 0 1px 2px rgba(0, 0, 0, 0.16);
}

 .runtime-notice-toggle {
   position: fixed;
   right: 16px;
   bottom: 14px;
   z-index: 82;
   min-width: 92px;
   height: 30px;
   padding: 0 11px;
   display: inline-flex;
   align-items: center;
   justify-content: center;
   gap: 5px;
   border: 1px solid #cbd6df;
   border-radius: 999px;
   color: #415565;
   background: rgba(255, 255, 255, 0.96);
   box-shadow: 0 7px 20px rgba(20, 43, 63, 0.15);
   font-size: 11px;
   white-space: nowrap;
 }

 .runtime-notice-toggle[aria-pressed="false"] {
   color: #647482;
   background: rgba(247, 249, 251, 0.96);
 }

 .toast {
   right: 16px;
   bottom: 54px;
   z-index: 81;
   width: fit-content;
   max-width: min(560px, calc(100vw - 132px));
   max-height: 160px;
   overflow: auto;
   white-space: normal;
   overflow-wrap: anywhere;
   line-height: 1.55;
 }

.store-group-row {
  min-height: 42px;
  margin-bottom: 10px;
  padding: 5px 0 8px;
  display: flex;
  align-items: center;
  gap: 8px;
  overflow-x: auto;
  border-bottom: 1px solid #edf1f4;
  scrollbar-width: thin;
}

.store-refresh-progress {
  min-height: 30px;
  margin: -3px 0 8px;
  display: grid;
  grid-template-columns: minmax(120px, 1fr) auto auto minmax(260px, auto);
  align-items: center;
  gap: 10px;
  color: #607080;
  font-size: 12px;
}

.store-refresh-track {
  height: 5px;
  overflow: hidden;
  border-radius: 999px;
  background: #edf1f4;
}

.store-refresh-track span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, #2d86dd, #15c56b);
  transition: width 180ms ease;
}

.store-refresh-progress strong {
  max-width: 540px;
  overflow: hidden;
  font-weight: 500;
  white-space: nowrap;
}

.store-refresh-progress small {
  min-width: 46px;
  color: #8a96a2;
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.credential-progress {
  color: #6f7d89;
  font-size: 11px;
  white-space: nowrap;
}

.store-refresh-progress.completed strong {
  color: #168a58;
}

.store-refresh-progress.partial strong,
.store-refresh-progress.failed strong {
  color: #c44b55;
}

.group-chip {
  min-width: max-content;
  height: 28px;
  padding: 0 12px;
  border: 1px solid #dfe5e9;
  border-radius: 999px;
  color: #526170;
  background: #fff;
  font-size: 12px;
  white-space: nowrap;
}

.group-chip.active {
  border-color: var(--green);
  color: #fff;
  background: var(--green);
}

.store-search {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #53616f;
  font-size: 12px;
}

.store-search .input {
  width: 220px;
}

.selected-hint {
  margin-left: auto;
  color: #457e64;
  font-size: 12px;
}

.selection-column {
  width: 44px;
  min-width: 44px !important;
  text-align: center;
}

.selection-column input {
  width: 14px;
  height: 14px;
}

.store-page {
   --store-sales-color: #236347;
   --store-refund-color: #963F47;
   --store-table-unknown-color: #929aa2;
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: #fff;
}

.store-table-card {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border-radius: 0;
}

.store-table-card > .table-scroll {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
}

.store-table {
   --store-table-heading-font-size: 13px;
   --store-table-data-font-size: 13px;
   --store-table-secondary-font-size: 12px;
   --store-table-sales-single-font-size: 14.5px;
   --store-table-sales-double-font-size: 12px;
  width: max-content;
  min-width: 100%;
  table-layout: auto;
}

.store-table th {
  position: sticky;
  top: 0;
  z-index: 2;
  height: 44px;
  padding: 0 6px;
  box-sizing: border-box;
  font-size: var(--store-table-heading-font-size);
  background: #f7f9fa;
  text-align: center;
  white-space: nowrap;
}

.store-table th.sticky-column,
.store-table td.sticky-column {
  position: sticky;
  z-index: 4;
  background: var(--store-row-bg, #fff);
}

.store-table th.sticky-column {
  z-index: 9;
  background: #f7f9fa;
}

.store-table .sticky-select {
  left: 0;
}

.store-table .sticky-index {
  left: 44px;
}

.store-table .sticky-name {
  left: 98px;
}

.store-table td.sticky-name {
  box-shadow: 9px 0 12px -10px rgba(25, 55, 76, 0.72);
}

.store-table thead tr:nth-child(2) th {
  top: 44px;
}

.store-table td {
  height: 58px;
  padding: 5px 10px;
  box-sizing: border-box;
  background: var(--store-row-bg, #fff);
  font-size: var(--store-table-data-font-size);
  line-height: 1.3;
  text-align: center;
  white-space: nowrap;
}

.store-table td:nth-child(3) {
  min-width: 150px;
  white-space: nowrap;
}

.store-table th:first-child,
.store-table td:first-child {
  width: 44px;
  min-width: 44px;
}

.store-table th:nth-child(2),
.store-table td:nth-child(2) {
  width: 54px;
  min-width: 54px;
}

.store-table tbody tr {
  --store-row-bg: #fff;
}

.store-table tbody tr.row-alternate {
  --store-row-bg: #f7f9fb;
}

.store-table tbody tr:hover {
  --store-row-bg: #f1f6f9;
}

.store-table tbody tr.selected {
  --store-row-bg: #eaf3fb;
  box-shadow: inset 3px 0 #4a9fe8;
}

.app-id-cell {
  min-width: 0;
  white-space: nowrap;
}

.app-id-placeholder {
  display: block;
  min-width: 0;
  overflow: visible;
  text-overflow: clip;
  vertical-align: middle;
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  color: #65717c;
}

.exposure-group-head {
  text-align: center;
}

.count-cell,
.online-product-cell,
.new-products-cell {
  min-width: 0;
  text-align: center;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.online-product-cell {
  min-width: 0;
}

.new-products-cell {
  min-width: 0;
}

.score-cell {
  min-width: 0;
  white-space: nowrap;
}

.score-cell small {
  margin-left: 4px;
  font-size: var(--store-table-secondary-font-size);
  font-weight: 600;
}

.score-cell small.up {
  color: #ef3f50;
}

.score-cell small.down {
  color: #16b968;
}

.score-cell em,
.deposit-cell em {
  display: block;
  margin-top: 3px;
  color: #8a641e;
  font-size: var(--store-table-secondary-font-size);
  font-style: normal;
}

.deposit-cell {
  min-width: 0;
  white-space: nowrap;
}

.secret-placeholder {
  display: block;
  min-width: 0;
  overflow: visible;
  text-overflow: clip;
  white-space: nowrap;
  color: #8a949e;
}

.secret-cell {
  min-width: 0;
  white-space: nowrap;
}

.secret-reveal {
  width: 100%;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  justify-content: center;
  white-space: nowrap;
}

.secret-reveal > button {
  flex: 0 0 auto;
}

.action-group {
  justify-content: center;
}

.secret-show,
.app-id-show {
  height: 24px;
  margin-left: 0;
  padding: 0 8px;
  border: 0;
  border-radius: 3px;
  color: white;
  background: #707b87;
  font-size: var(--store-table-secondary-font-size);
}

.group-manager {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.group-manager section > label {
  display: block;
  margin-bottom: 8px;
  color: #66717b;
  font-size: 13px;
}

.group-list {
  max-height: 190px;
  padding: 5px;
  display: flex;
  flex-direction: column;
  gap: 3px;
  overflow: auto;
  border: 1px solid #dfe5e9;
  background: #fafafa;
}

.group-list button {
  min-height: 31px;
  padding: 6px 10px;
  border: 0;
  border-radius: 4px;
  color: #333;
  background: #f3f3f3;
  text-align: left;
}

.group-list button.active {
  color: #fff;
  background: #409eff;
}

.group-list span {
  padding: 8px;
  color: #90969d;
  font-size: 12px;
}

.new-group-controls {
  display: grid;
  grid-template-columns: 1fr auto auto;
  gap: 8px;
}

.store-summary {
   flex: 0 0 58px;
   min-height: 58px;
  margin: 0 -12px -18px;
  padding: 0 18px;
  border: 0;
  border-radius: 0;
  color: #fff;
  background: #081a2d;
}

.store-summary .summary-cell {
  min-width: 120px;
  padding: 0 14px;
  border-color: rgba(255, 255, 255, 0.08);
}

.store-summary .summary-cell span {
  margin: 0 6px 0 0;
  display: inline;
  color: #d7e0e8;
}

.store-summary .summary-cell strong {
  color: #17df86;
  font-size: 14px;
}

 .store-summary .summary-cell {
   min-width: 175px;
   display: grid;
   align-content: center;
   gap: 1px;
 }

 .store-summary .summary-cell small {
   color: #d5e1e8;
   font-size: 9px;
   font-variant-numeric: tabular-nums;
   white-space: nowrap;
 }

.form-error,
.delete-warning {
  margin: 14px 0 0;
  color: #c13a4b;
  font-size: 12px;
}

.delete-warning {
  margin: 0;
  line-height: 1.7;
}

.batch-store-overlay {
  position: fixed;
  z-index: 90;
  inset: 0;
  padding-left: 184px;
  background: rgba(9, 25, 40, .28);
  backdrop-filter: blur(1px);
}

.batch-store-drawer {
  position: absolute;
  inset: 0 0 0 auto;
  width: min(920px, calc(100vw - 184px));
  display: flex;
  flex-direction: column;
  background: #fff;
  box-shadow: -20px 0 60px rgba(18, 36, 53, .2);
}

.batch-store-head {
  min-height: 74px;
  padding: 15px 20px;
  display: flex;
  align-items: flex-start;
  gap: 14px;
  border-bottom: 1px solid #e2e8ed;
}

.batch-store-head h2 {
  margin: 0;
  color: #1a2c3b;
  font-size: 20px;
}

.batch-store-head p {
  margin: 4px 0 0;
  color: #788692;
  font-size: 11px;
}

.round-close {
  width: 30px;
  height: 30px;
  margin-left: auto;
  border: 1px solid #dfe6eb;
  border-radius: 50%;
  color: #667581;
  background: #fff;
  font-size: 19px;
}

.batch-store-body {
  flex: 1;
  min-height: 0;
  padding: 16px 20px 90px;
  overflow: auto;
}

.batch-steps {
  margin-bottom: 14px;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
}

.batch-steps div {
  padding: 9px 10px;
  border: 1px solid #e3e9ee;
  border-radius: 6px;
  color: #7a8792;
  background: #fafcfd;
  font-size: 10px;
}

.batch-steps b {
  display: block;
  margin-bottom: 2px;
  color: #344756;
  font-size: 11px;
}

.batch-steps .active {
  border-color: #93c6f5;
  color: #47739d;
  background: #edf6ff;
}

.batch-steps .done {
  border-color: #b6e3cb;
  color: #4e8269;
  background: #edfaf3;
}

.batch-source-card {
  padding: 13px;
  border: 1px solid #e1e8ed;
  border-radius: 8px;
  background: #fbfcfd;
}

.batch-section-label {
  margin: 0 0 8px;
  color: #465867;
  font-size: 11px;
  font-weight: 700;
}

.batch-source-tabs {
  margin-bottom: 8px;
  display: flex;
  gap: 5px;
}

.batch-source-tabs button {
  min-height: 29px;
  padding: 0 10px;
  border: 1px solid #dbe3e9;
  border-radius: 4px;
  color: #5f6e7a;
  background: #fff;
  font-size: 11px;
}

.batch-source-tabs button.active {
  border-color: #73afe7;
  color: #1762aa;
  background: #edf6ff;
}

.batch-source-text {
  width: 100%;
  height: 104px;
  padding: 9px 10px;
  resize: vertical;
  font-family: "Cascadia Code", Consolas, monospace;
  font-size: 10px;
  line-height: 1.55;
}

.batch-file-picker {
  min-height: 76px;
  padding: 13px;
  display: flex;
  align-items: center;
  gap: 12px;
  border: 1px dashed #b8c6d0;
  border-radius: 7px;
  color: #788692;
  background: #fff;
  font-size: 11px;
}

.batch-source-tools {
  margin-top: 8px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.batch-source-tools span {
  margin-left: auto;
  color: #88959f;
  font-size: 10px;
}

.batch-summary-grid {
  margin: 13px 0;
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 7px;
}

.batch-summary-grid article {
  padding: 9px 10px;
  border: 1px solid #e2e8ed;
  border-radius: 7px;
  background: #fff;
}

.batch-summary-grid span,
.batch-summary-grid strong {
  display: block;
}

.batch-summary-grid span {
  color: #7e8b96;
  font-size: 9px;
}

.batch-summary-grid strong {
  margin-top: 2px;
  color: #324553;
  font-size: 18px;
}

.batch-summary-grid .new strong { color: #11875a; }
.batch-summary-grid .replace strong { color: #ad6b08; }
.batch-summary-grid .invalid strong { color: #c43e54; }
.batch-summary-grid .conflict strong { color: #7d5cbb; }

.batch-preview-tools {
  margin: 11px 0 7px;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.batch-preview-tools .input {
  width: 220px;
  margin-left: auto;
}

.batch-preview-table-wrap {
  max-height: 410px;
  overflow: auto;
  border: 1px solid #e1e7ec;
  border-radius: 7px;
}

.batch-preview-table {
  width: 100%;
  min-width: 820px;
  border-collapse: collapse;
}

.batch-preview-table th,
.batch-preview-table td {
  padding: 8px 9px;
  border-bottom: 1px solid #edf1f4;
  color: #536473;
  font-size: 10px;
  text-align: left;
  vertical-align: top;
}

.batch-preview-table th {
  position: sticky;
  top: 0;
  z-index: 2;
  color: #788590;
  background: #f8fafb;
}

.batch-preview-table td strong,
.batch-preview-table td small {
  display: block;
}

.batch-preview-table td small {
  margin-top: 2px;
  color: #87949f;
  font-size: 9px;
}

.mono {
  font-family: "Cascadia Code", Consolas, monospace;
}

.batch-status {
  display: inline-flex;
  min-height: 21px;
  padding: 2px 7px;
  align-items: center;
  border-radius: 999px;
  color: #5e6c78;
  background: #eef2f4;
  font-size: 9px;
  font-weight: 600;
  white-space: nowrap;
}

.batch-status.new,
.batch-status.applied {
  color: #117c52;
  background: #e9f8f0;
}

.batch-status.replace_candidate,
.batch-status.pending,
.batch-status.validating {
  color: #9b650b;
  background: #fff4df;
}

.batch-status.invalid,
.batch-status.name_conflict {
  color: #bd3e50;
  background: #ffedf0;
}

.batch-status.duplicate,
.batch-status.skip,
.batch-status.superseded {
  color: #617381;
  background: #edf2f5;
}

.conflict-name-input {
  width: 100%;
  min-width: 180px;
  height: 28px;
  margin-top: 5px;
}

.link-button {
  padding: 0;
  border: 0;
  color: #2875c5;
  background: transparent;
  font-size: 10px;
}

.batch-empty {
  height: 120px;
  color: #87949f !important;
  text-align: center !important;
  vertical-align: middle !important;
}

.batch-name-hint {
  margin: 7px 0 0;
  color: #8a651d;
  font-size: 10px;
}

.batch-store-foot {
  position: absolute;
  inset: auto 0 0;
  min-height: 68px;
  padding: 11px 20px;
  display: flex;
  align-items: center;
  gap: 9px;
  border-top: 1px solid #e1e7ec;
  background: rgba(255,255,255,.97);
  backdrop-filter: blur(10px);
}

.batch-store-foot span {
  max-width: 520px;
  color: #73818c;
  font-size: 10px;
  line-height: 1.55;
}

.batch-store-foot i {
  flex: 1;
}

.credential-detail {
  display: grid;
  gap: 14px;
}

.credential-state-card {
  padding: 12px;
  display: grid;
  gap: 3px;
  border: 1px solid #dfe6eb;
  border-radius: 8px;
  background: #fafcfd;
}

.credential-state-card > span,
.credential-state-card small {
  color: #7e8b96;
  font-size: 10px;
}

.credential-state-card strong {
  color: #334654;
  font-size: 18px;
}

.credential-state-card.invalid {
  border-color: #efc8cd;
  background: #fff4f5;
}

.credential-state-card.valid {
  border-color: #b9e2cd;
  background: #f1fbf6;
}

.credential-detail section h3 {
  margin: 0 0 8px;
  color: #3b4d5b;
  font-size: 12px;
}

.cookie-import-section {
  padding: 13px;
  display: grid;
  gap: 9px;
  border: 1px solid #e3e8ed;
  border-radius: 6px;
  background: #fbfcfd;
}

.cookie-import-section p {
  margin: 0;
  color: #788692;
  font-size: 10px;
  line-height: 1.6;
}

.cookie-import-input {
  min-height: 88px;
  resize: vertical;
  font-family: Consolas, monospace;
}

.credential-empty,
.credential-loading {
  padding: 18px;
  color: #85929d;
  background: #fafcfd;
  text-align: center;
  font-size: 11px;
}

.credential-candidate,
.credential-audit-row {
  padding: 8px 0;
  display: grid;
  grid-template-columns: 100px minmax(0, 1fr);
  gap: 8px;
  border-bottom: 1px solid #edf1f4;
  font-size: 10px;
}

.credential-candidate > div,
.credential-audit-row {
  color: #657481;
}

.credential-candidate strong,
.credential-candidate small,
.credential-audit-row strong,
.credential-audit-row span,
.credential-audit-row small {
  display: block;
}

.credential-candidate small,
.credential-audit-row small {
  margin-top: 2px;
  color: #8c98a2;
  font-size: 9px;
}

.credential-audit-row {
  grid-template-columns: 190px minmax(0, 1fr) 150px;
}

.batch-inspect {
  display: grid;
  gap: 9px;
}

.batch-inspect div {
  display: grid;
  grid-template-columns: 110px minmax(0, 1fr);
  gap: 10px;
  padding-bottom: 7px;
  border-bottom: 1px solid #edf1f4;
  font-size: 11px;
}

.batch-inspect span {
  color: #7f8c97;
}

 @media (max-width: 1180px) {
   .store-view-switch {
     margin-left: 0;
   }

   .store-overview-table,
   .store-overview-table tbody {
     display: block;
   }

   .store-overview-table thead {
     display: none;
   }

   .store-overview-table .overview-main-row {
     margin: 8px;
     display: grid;
     grid-template-columns: 34px 38px repeat(4, minmax(0, 1fr));
     gap: 1px;
     border: 1px solid #dfe7ed;
     border-radius: 8px;
     overflow: hidden;
     background: #e8eef2;
   }

   .store-overview-table .overview-main-row td {
     position: static !important;
     height: auto;
     min-height: 44px;
     padding: 8px;
     display: block;
     border: 0;
     background: #fff;
     text-align: left;
   }

   .store-overview-table .overview-main-row td[data-label]::before {
     margin-bottom: 3px;
     display: block;
     color: #929ea7;
     content: attr(data-label);
     font-size: var(--store-table-secondary-font-size);
     text-align: left;
   }

   .store-overview-table .overview-main-row td:nth-child(3) {
     grid-column: 3 / -1;
   }

   .store-overview-table .overview-main-row td:nth-child(4) {
     grid-column: 1 / -1;
   }

   .store-overview-table .overview-main-row td:nth-child(9) {
     grid-column: 1 / 3;
   }

   .store-overview-table .overview-main-row td:nth-child(10) {
     grid-column: 3 / 5;
   }

   .store-overview-table .overview-main-row td:nth-child(11) {
     grid-column: 5 / -1;
   }

   .store-overview-table .overview-main-row td:nth-child(12) {
     grid-column: 1 / -1;
   }

   .store-overview-table .selection-column,
   .store-overview-table .sticky-index {
     text-align: center !important;
   }

   .store-overview-table .overview-sales-cell {
     min-height: 53px !important;
   }

   .store-overview-table .overview-details-row {
     display: block;
     margin: -8px 8px 8px;
   }

   .store-overview-table .overview-details-row > td {
     width: auto;
     display: block;
   }

   .store-detail-grid {
     grid-template-columns: repeat(2, minmax(0, 1fr));
   }

   .store-summary {
     height: auto;
     min-height: 62px;
     padding: 8px 12px;
     flex-wrap: wrap;
   }

   .store-summary .summary-cell {
     min-width: 150px;
   }
 }

@media (max-width: 980px) {
  .batch-store-overlay {
    padding-left: 64px;
  }

  .batch-store-drawer {
    width: calc(100vw - 64px);
  }

  .batch-summary-grid {
    grid-template-columns: repeat(3, 1fr);
  }
}

@media (max-width: 760px) {
  .store-view-switch {
    width: 100%;
  }

  .store-view-switch button {
    flex: 1;
  }

  .store-overview-table .overview-main-row {
    grid-template-columns: 34px 38px repeat(2, minmax(0, 1fr));
  }

  .store-overview-table .overview-main-row td:nth-child(3) {
    grid-column: 3 / -1;
  }

  .store-overview-table .overview-main-row td:nth-child(9),
  .store-overview-table .overview-main-row td:nth-child(10),
  .store-overview-table .overview-main-row td:nth-child(11) {
    grid-column: auto;
  }

  .store-overview-table .overview-main-row td:nth-child(12) {
    grid-column: 1 / -1;
  }

  .store-detail-grid {
    grid-template-columns: 1fr;
  }
}
</style>
