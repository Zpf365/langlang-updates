<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { call, formatMoney } from "../api";
import Icon from "../components/Icon.vue";
import ModalDialog from "../components/ModalDialog.vue";
import UploadTemplateDialog from "../components/UploadTemplateDialog.vue";
import UploadStoreSelectionDialog from "../components/UploadStoreSelectionDialog.vue";
import { mergeUploadStoreOptions } from "../uploadStoreOptions";
import { applyUploadTemplateTitleRules } from "../uploadTitleRules";

const route = useRoute();
const router = useRouter();

const tabs = [
  ["templates", "上货模板"],
  ["upload", "上传商品"],
  ["tasks", "上传任务"],
];
const terminalJobStatuses = new Set([
  "completed",
  "partial",
  "failed",
  "paused",
  "cancelled",
]);
const uploadJobStatuses = new Set([
  "starting",
  "queued",
  "running",
  "pausing",
  "cancelling",
  "submitting",
]);
const taskStatusLabels = {
  pending: "待执行",
  queued: "排队中",
  running: "执行中",
  pausing: "正在暂停",
  cancelling: "正在取消",
  submitting: "提交中",
  paused: "已暂停",
  partial: "需处理",
  needs_verification: "待核实",
  needs_category: "待类目",
  failed: "失败",
  cancelled: "已取消",
  completed: "已完成",
};
const itemStatusLabels = {
  ...taskStatusLabels,
  success: "成功",
  skipped: "已跳过",
  failed: "失败",
  verification_pending: "待核实",
  waiting_category: "等待类目",
};

const activeTab = ref("templates");
const loading = ref(false);
const templates = ref([]);
const batch = ref([]);
const tasks = ref([]);
const stores = ref([]);
const templateDefaults = ref({});
const defaultTemplateId = ref("");
const cloudViolationTypes = ref([]);
const violationTitles = ref([]);
const violationTitleCount = ref(0);
const categoryAutoResumeEnabled = ref(true);
const logs = ref([]);
const logsLoading = ref(false);
const selectedBatchIds = ref([]);
const batchStoreAssignments = reactive({});
const storeOptions = ref({});
const selectedTaskIds = ref([]);
const selectedItemIds = ref([]);
const selectedTemplateId = ref("");
const note = reactive({ message: "", tone: "" });
const packageInput = ref(null);
const folderInput = ref(null);
const filters = reactive({ keyword: "", minPrice: "", maxPrice: "" });
const logFilters = reactive({ taskId: "", status: "", keyword: "" });
const importState = reactive({ busy: false, result: null });
const preflight = ref(null);
const reparseResult = ref(null);
const uploadJob = reactive({
  jobId: "",
  status: "",
  message: "",
  completed: 0,
  total: 0,
});
const templateModal = reactive({
  open: false,
  busy: false,
  template: null,
});
const taskModal = reactive({
  open: false,
  busy: false,
  storeIds: [],
  templateId: "",
  configs: {},
  productCount: 0,
});
const riskModal = reactive({
  open: false,
  busy: false,
  title: "",
  description: "",
  confirmLabel: "确认",
  action: null,
});
let noteTimer = 0;
let unmounted = false;
let pollingJobId = "";
const legacyCloudViolationTemplateIds = new Set();

const filteredBatch = computed(() => {
  const keyword = filters.keyword.trim().toLowerCase();
  const min = Number(filters.minPrice || 0) * 100;
  const max = Number(filters.maxPrice || 0) * 100;
  return batch.value.filter((item) => {
    if (min && Number(item.price || 0) < min) return false;
    if (max && Number(item.price || 0) > max) return false;
    if (!keyword) return true;
    return [
      item.title,
      item.source_product_id,
      item.platform,
      item.source_file,
    ].some((value) => String(value || "").toLowerCase().includes(keyword));
  });
});
const allFilteredBatchSelected = computed(() => (
  filteredBatch.value.length > 0
  && filteredBatch.value.every((row) => selectedBatchIds.value.includes(row.batch_id))
));
const selectedBatchItems = computed(() => (
  batch.value.filter((row) => selectedBatchIds.value.includes(row.batch_id))
));
const selectedTasks = computed(() => (
  tasks.value.filter((task) => selectedTaskIds.value.includes(task.id))
));
const runnableTaskIds = computed(() => {
  if (selectedTaskIds.value.length) return selectedTaskIds.value;
  const ids = new Set();
  for (const task of tasks.value) {
    if ((task.items || []).some((item) => selectedItemIds.value.includes(item.item_id))) {
      ids.add(task.id);
    }
  }
  return [...ids];
});
const failedItemCount = computed(() => tasks.value.reduce(
  (count, task) => count + (task.items || []).filter((item) => item.status === "failed").length,
  0,
));
const retryableItemCount = computed(() => tasks.value.reduce(
  (count, task) => count + (task.items || []).filter(isRetryableItem).length,
  0,
));
const completedTaskCount = computed(() => (
  tasks.value.filter((task) => task.status === "completed").length
));
const allTasksSelected = computed(() => (
  tasks.value.length > 0
  && tasks.value.every((task) => selectedTaskIds.value.includes(task.id))
));
const activeRun = computed(() => (
  Boolean(uploadJob.jobId) && uploadJobStatuses.has(uploadJob.status)
));
const uploadProgress = computed(() => (
  uploadJob.total > 0
    ? Math.min(100, Math.round(uploadJob.completed / uploadJob.total * 100))
    : 0
));
const preflightMap = computed(() => new Map(
  (preflight.value?.rows || []).map((row) => [row.batch_id, row]),
));
const defaultUploadStoreId = computed(() => {
  const store = stores.value.find((row) => row.web_status === "已登录")
    || stores.value[0];
  return store ? Number(store.id) : 0;
});
const assignedSelectedStoreIds = computed(() => (
  [...new Set(
    selectedBatchItems.value
      .map((row) => Number(batchStoreAssignments[row.batch_id] || 0))
      .filter(Boolean),
  )]
));
function showNote(message, tone = "") {
  note.message = String(message || "");
  note.tone = tone;
  window.clearTimeout(noteTimer);
  if (message) {
    noteTimer = window.setTimeout(() => {
      note.message = "";
      note.tone = "";
    }, 7000);
  }
}

function errorText(cause) {
  return cause instanceof Error ? cause.message : String(cause);
}

function isLoggedInUploadStore(store) {
  return /已登录/.test(`${store?.web_status || ""} ${store?.status || ""}`);
}

function normalizeViolationTitle(value) {
  return String(value || "")
    .normalize("NFKC")
    .replace(/[\u200b-\u200d\ufeff]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .toLocaleLowerCase("zh-CN");
}

const violationTitleIndex = computed(() => new Map(
  violationTitles.value
    .map((row) => [
      normalizeViolationTitle(row.normalized || row.title),
      row,
    ])
    .filter(([title]) => title),
));

function violationMatchForTitle(title) {
  const normalized = normalizeViolationTitle(title);
  return normalized ? violationTitleIndex.value.get(normalized) || null : null;
}

function violationIssueText(match) {
  return `云违规标题过滤命中：${match?.reason || "出售假冒商品"}`;
}

function blockSelectedViolationTitles(items = selectedBatchItems.value, context = "创建任务") {
  const matched = items.find((row) => (
    violationMatchForTitle(row.title || row.payload?.title)
  ));
  if (!matched) return false;
  const violation = violationMatchForTitle(matched.title || matched.payload?.title);
  showNote(
    `${context}已禁止：${matched.title || matched.source_product_id}；${violationIssueText(violation)}`,
    "danger",
  );
  return true;
}

function blockTaskViolationTitles(taskIds = runnableTaskIds.value, itemIds = selectedItemIds.value) {
  const selected = new Set((taskIds || []).map(String));
  const selectedItems = new Set((itemIds || []).map(String));
  for (const task of tasks.value) {
    if (selected.size && !selected.has(String(task.id))) continue;
    for (const item of task.items || []) {
      if (
        selectedItems.size
        && !selectedItems.has(String(item.item_id))
      ) continue;
      const finalTitle = applyUploadTemplateTitleRules(
        item.title || item.payload?.title,
        task.template || {},
      );
      const violation = violationMatchForTitle(finalTitle);
      if (!violation) continue;
      showNote(
        `提交前已禁止：${item.title || item.source_product_id}；${violationIssueText(violation)}`,
        "danger",
      );
      return true;
    }
  }
  return false;
}

function enforceViolationPreflight(result) {
  if (!result || !Array.isArray(result.rows)) return 0;
  let blocked = 0;
  result.rows = result.rows.map((row) => {
    const title = row.mapping?.title || row.title;
    const violation = violationMatchForTitle(title);
    if (!violation) return row;
    const issue = violationIssueText(violation);
    const issues = Array.isArray(row.issues) ? [...row.issues] : [];
    if (!issues.includes(issue)) issues.push(issue);
    blocked += 1;
    return {
      ...row,
      ready: false,
      issues,
    };
  });
  result.ready = result.rows.filter((row) => row.ready).length;
  result.blocked = result.rows.length - result.ready;
  return blocked;
}

async function ensureLegacyTemplateCloudViolation(templateId) {
  const id = String(templateId || "").trim();
  if (!id || !legacyCloudViolationTemplateIds.has(id)) return;
  const template = templates.value.find((row) => String(row.id) === id);
  if (!template) return;
  const saved = await call("upload.templates.save", {
    template: {
      ...template,
      cloud_violation: true,
    },
  });
  legacyCloudViolationTemplateIds.delete(id);
  const index = templates.value.findIndex((row) => String(row.id) === id);
  if (index >= 0 && saved?.template) {
    templates.value[index] = saved.template;
  }
}

function statusLabel(status) {
  return taskStatusLabels[status] || status || "待执行";
}

function itemStatusLabel(status, row = null) {
  const categoryState = String(row?.category_state || "");
  if (categoryState === "deposit_required") return "等待补缴保证金";
  if (categoryState === "pending") return "等待类目审核";
  if (categoryState === "qualification_required") return "缺少类目资质";
  if (categoryState === "rejected") return "类目申请被拒";
  if (categoryState === "unavailable") return "类目不可申请";
  if (categoryState === "unknown") return "类目状态待确认";
  if (status === "failed" && row?.category_reprocess_available) {
    return "类目失败·可重试";
  }
  if (status === "submitting" && row) {
    const message = String(row.message || "");
    if (message.includes("正在提交类目开通申请")) return "自动申请中";
    if (message.includes("正在复查类目")) return "审核中";
    if (message.includes("类目已生效")) return "类目生效·重试中";
  }
  return itemStatusLabels[status] || status || "待执行";
}

function categoryStatusDetail(row) {
  if (
    row.status !== "waiting_category"
    && !String(row.category_state || "")
  ) return "";
  const reasons = Object.entries(row.category_checks || {})
    .filter(([, check]) => check?.reason)
    .map(([categoryId, check]) => (
      `${categoryId}:${check.reason}`
    ));
  const statuses = Object.entries(row.category_statuses || {})
    .map(([categoryId, status]) => `${categoryId}:${status}`)
    .join(" · ");
  const deposit = row.category_deposit_snapshot || {};
  const depositDetail = [
    deposit.required_deposit != null
      ? `官方门槛 ${formatMoney(deposit.required_deposit)}`
      : "",
    deposit.operation_required_deposit != null
      ? `当前操作要求 ${formatMoney(deposit.operation_required_deposit)}`
      : "",
    deposit.deposit_balance != null
      ? `保证金余额 ${formatMoney(deposit.deposit_balance)}`
      : "",
    deposit.deposit_free_trial
      ? "平台支持免押试用"
      : "",
  ].filter(Boolean).join(" · ");
  const officialError = String(
    row.category_previous_failure_message
      || row.category_error
      || "",
  ).trim();
  const next = row.category_next_check_at
    ? `下次检查 ${String(row.category_next_check_at).replace("T", " ").slice(0, 19)}`
    : "等待自动检查";
  const selection = row.category_selection || {};
  const selected = selection.category_id
    ? [
        `${selection.selection_source === "fallback" ? "兜底" : "自动"} ${selection.path || selection.category_id}`,
        `匹配 ${Number(selection.score || 0).toFixed(1)}`,
        selection.deposit_amount != null
          ? `保证金 ${formatMoney(selection.deposit_amount)}`
          : "",
        selection.deposit_shortfall > 0
          ? `需补缴 ${formatMoney(selection.deposit_shortfall)}`
          : "",
      ]
    : [];
  const candidateText = (row.category_candidates || [])
    .slice(0, 3)
    .map((candidate) => (
      `${candidate.selected ? "已选" : "候选"} `
      + `${candidate.path || candidate.category_id}:`
      + `匹配 ${Number(candidate.match_score || 0).toFixed(1)}`
      + `${candidate.deposit_amount != null ? `，保证金 ${formatMoney(candidate.deposit_amount)}` : ""}`
      + `，${candidate.application_label || candidate.application_state || "状态未知"}`
      + `${candidate.unselected_reason ? `，未选 ${candidate.unselected_reason}` : ""}`
    ));
  return [
    selected.filter(Boolean).join(" · "),
    statuses,
    reasons.join(" · "),
    depositDetail,
    officialError ? `官方原文 ${officialError}` : "",
    candidateText.join(" · "),
    row.status === "waiting_category" ? next : "",
  ].filter(Boolean).join(" · ");
}

function statusTone(status) {
  if (["completed"].includes(status)) return "success";
  if (["failed", "partial", "needs_verification"].includes(status)) return "danger";
  if (["running", "submitting", "pausing", "cancelling"].includes(status)) return "running";
  if (["needs_category", "waiting_category", "paused"].includes(status)) return "warning";
  return "";
}

function taskProgress(task) {
  const items = task.items || [];
  const done = items.filter((item) => (
    ["success", "failed", "skipped", "cancelled"].includes(item.status)
  )).length;
  return `${done}/${items.length}`;
}

function taskSourceCount(task) {
  return new Set((task.items || []).map((item) => item.source_product_id)).size;
}

function taskCategoryLabel(task) {
  const template = task?.template || {};
  const category = String(
    template.specified_category_path
    || template.category_path
    || "",
  ).trim();
  const brand = template.brand_mode === "store"
    ? `品牌 ${template.brand_id || "未选择"}`
    : template.brand_mode === "none"
      ? "无品牌"
      : "";
  return [category ? `指定类目 ${category}` : "", brand].filter(Boolean).join(" · ");
}

function preflightMappingText(row) {
  const mapping = row.mapping || {};
  const price = mapping.price_min == null
    ? "价格暂无"
    : mapping.price_min === mapping.price_max
      ? formatMoney(mapping.price_min)
      : `${formatMoney(mapping.price_min)}–${formatMoney(mapping.price_max)}`;
  const category = mapping.category_mode === "auto"
    ? "类目自动匹配"
    : mapping.category_path || "来源类目";
  const warnings = (row.warnings || []).join("；");
  const conflicts = (mapping.adapter_conflicts || []).join("；");
  return `${mapping.title || row.title || row.source_product_id}：SKU ${mapping.sku_count ?? 0} · 主图 ${mapping.head_image_count ?? 0} · 详情 ${mapping.detail_image_count ?? 0} · ${price} · 库存 ${mapping.stock_total ?? "暂无"} · ${category}${warnings ? `；${warnings}` : ""}${conflicts ? `；适配：${conflicts}` : ""}`;
}

function isRetryableItem(item) {
  if (item.status === "failed") return true;
  if (item.status !== "verification_pending" || item.product_id) return false;
  const message = String(item.message || "");
  return (
    message.includes("真实平台写操作缺少当前会话安全门，已拒绝执行")
    && ["product_write", "product_add", "product_image_upload"]
      .some((action) => message.includes(`已拒绝执行：${action}`))
  );
}

function templateListingLabel(template) {
  return {
    immediate: "出售中",
    warehouse: "仓库",
    draft: "草稿",
  }[template.listing_mode] || template.listing_mode || "出售中";
}

function templatePriceRule(template) {
  if (template.fixed_price_enabled) {
    return `固定 ¥${(Number(template.fixed_price || 0) / 100).toFixed(2)}`;
  }
  const offset = Number(template.price_offset || 0) / 100;
  const base = `×${Number(template.price_multiplier ?? 1).toFixed(2)} ${offset >= 0 ? "+" : "-"} ¥${Math.abs(offset).toFixed(2)}`;
  return template.ladder_enabled
    ? `${base} · 阶梯 ${String(template.ladder_rules || "").split(/\r?\n/).filter(Boolean).length} 条`
    : base;
}

function templateTitleRule(template) {
  const parts = [
    template.title_prefix ? `前缀：${template.title_prefix}` : "",
    template.title_suffix ? `后缀：${template.title_suffix}` : "",
    template.strip_title_brackets ? "删除【】开头内容" : "",
  ].filter(Boolean);
  return parts.join(" · ") || "未设置标题规则";
}

function templateUpdatedAt(template) {
  return String(template.updated_at || template.created_at || "-")
    .replace("T", " ")
    .slice(0, 19);
}

async function load({ keepNote = false } = {}) {
  loading.value = true;
  try {
    const data = await call("upload.list", {});
    legacyCloudViolationTemplateIds.clear();
    templates.value = (data.templates || []).map((template) => {
      if (typeof template.cloud_violation === "boolean") return template;
      legacyCloudViolationTemplateIds.add(String(template.id || ""));
      return {
        ...template,
        cloud_violation: true,
      };
    });
    batch.value = data.batch || [];
    tasks.value = data.tasks || [];
    stores.value = data.stores || [];
    templateDefaults.value = data.template_defaults || {};
    defaultTemplateId.value = data.default_template_id || "";
    cloudViolationTypes.value = data.cloud_violation_types || [];
    const activeStoreIds = new Set(
      stores.value
        .filter(isLoggedInUploadStore)
        .map((store) => Number(store.id)),
    );
    violationTitles.value = (data.violation_titles || []).filter((row) => (
      row.source === "cloud_counterfeit"
      || activeStoreIds.has(Number(row.store_id || 0))
    ));
    violationTitleCount.value = violationTitles.value.length;
    alignBatchStoreAssignments();
    categoryAutoResumeEnabled.value = (
      data.category_auto_resume_enabled !== false
    );
    const activeJob = data.active_run_job;
    if (activeJob?.job_id) {
      applyUploadJob(activeJob);
      window.setTimeout(() => pollJob(), 0);
    } else if (
      uploadJob.jobId
      && !terminalJobStatuses.has(uploadJob.status)
    ) {
      clearUploadJob();
    }
    const batchIds = new Set(batch.value.map((row) => row.batch_id));
    const taskIds = new Set(tasks.value.map((task) => task.id));
    const itemIds = new Set(tasks.value.flatMap((task) => (
      (task.items || []).map((item) => item.item_id)
    )));
    selectedBatchIds.value = selectedBatchIds.value.filter((id) => batchIds.has(id));
    selectedTaskIds.value = selectedTaskIds.value.filter((id) => taskIds.has(id));
    selectedItemIds.value = selectedItemIds.value.filter((id) => itemIds.has(id));
    selectedTemplateId.value = selectedTemplateId.value
      && templates.value.some((row) => row.id === selectedTemplateId.value)
      ? selectedTemplateId.value
      : (templates.value.find((row) => row.id === defaultTemplateId.value)?.id
        || templates.value[0]?.id
        || "");
    if (!keepNote) showNote("");
  } catch (cause) {
    showNote(errorText(cause), "danger");
  } finally {
    loading.value = false;
  }
}

async function toggleCategoryAutoResume() {
  const enabled = !categoryAutoResumeEnabled.value;
  try {
    const result = await call(
      "upload.category-auto-resume.set",
      { enabled },
    );
    categoryAutoResumeEnabled.value = (
      result.enabled !== false
    );
    if (result.tasks) tasks.value = result.tasks;
    showNote(
      enabled
        ? "类目审核通过后自动续传已开启"
        : "已关闭类目自动续传，当前等待项保持暂停",
      enabled ? "success" : "",
    );
  } catch (cause) {
    showNote(errorText(cause), "danger");
  }
}

async function cancelCategoryWait(row) {
  try {
    const result = await call(
      "upload.waiting-category.cancel",
      {
        task_ids: [row.task_id],
        item_ids: [row.item_id],
      },
    );
    await load({ keepNote: true });
    await loadLogs();
    showNote(
      `已取消 ${result.cancelled || 0} 个商品的类目等待`,
      "success",
    );
  } catch (cause) {
    showNote(errorText(cause), "danger");
  }
}

function applyUploadJob(job) {
  uploadJob.jobId = String(job.job_id || "");
  uploadJob.status = String(job.status || "");
  uploadJob.message = String(job.message || "");
  uploadJob.completed = Number(job.completed || job.current || 0);
  uploadJob.total = Number(job.total || 0);
}

function clearUploadJob() {
  uploadJob.jobId = "";
  uploadJob.status = "";
  uploadJob.message = "";
  uploadJob.completed = 0;
  uploadJob.total = 0;
}

async function selectTab(tab) {
  activeTab.value = tab;
  if (tab === "tasks") await loadLogs();
}

function toggleBatch(id) {
  selectedBatchIds.value = selectedBatchIds.value.includes(id)
    ? selectedBatchIds.value.filter((value) => value !== id)
    : [...selectedBatchIds.value, id];
}

function toggleAllFilteredBatch() {
  const visible = filteredBatch.value.map((row) => row.batch_id);
  selectedBatchIds.value = allFilteredBatchSelected.value
    ? selectedBatchIds.value.filter((id) => !visible.includes(id))
    : [...new Set([...selectedBatchIds.value, ...visible])];
}

function toggleTask(id) {
  selectedItemIds.value = [];
  selectedTaskIds.value = selectedTaskIds.value.includes(id)
    ? selectedTaskIds.value.filter((value) => value !== id)
    : [...selectedTaskIds.value, id];
}

function toggleAllTasks() {
  selectedItemIds.value = [];
  selectedTaskIds.value = allTasksSelected.value
    ? []
    : tasks.value.map((task) => task.id);
}

function itemCanRun(row) {
  if (row.status === "verification_pending") return isRetryableItem(row);
  return !["success", "skipped", "waiting_category"].includes(row.status);
}

function toggleLogItem(row) {
  if (!itemCanRun(row)) return;
  selectedTaskIds.value = [];
  selectedItemIds.value = selectedItemIds.value.includes(row.item_id)
    ? selectedItemIds.value.filter((id) => id !== row.item_id)
    : [...selectedItemIds.value, row.item_id];
}

function resetBatchFilters() {
  filters.keyword = "";
  filters.minPrice = "";
  filters.maxPrice = "";
}

function storeName(storeId) {
  return stores.value.find(
    (store) => Number(store.id) === Number(storeId),
  )?.name || "未分配";
}

function assignedStoreItemCount(storeId) {
  return selectedBatchItems.value.filter(
    (row) => Number(batchStoreAssignments[row.batch_id]) === Number(storeId),
  ).length;
}

function alignBatchStoreAssignments() {
  const known = new Set(stores.value.map((store) => Number(store.id)));
  const fallback = defaultUploadStoreId.value;
  for (const key of Object.keys(batchStoreAssignments)) {
    if (!batch.value.some((row) => row.batch_id === key)) {
      delete batchStoreAssignments[key];
    }
  }
  for (const row of batch.value) {
    const current = Number(batchStoreAssignments[row.batch_id] || 0);
    if (!known.has(current)) {
      batchStoreAssignments[row.batch_id] = fallback;
    }
  }
}

async function loadStoreOptions(storeIds) {
  const result = await call("upload.store-options", {
    store_ids: [...new Set(storeIds.map(Number).filter(Boolean))],
  });
  storeOptions.value = mergeUploadStoreOptions(
    storeOptions.value,
    result.stores || {},
  );
}

async function loadStoreCategories(storeIds, refresh = false) {
  const ids = [...new Set(storeIds.map(Number).filter(Boolean))];
  if (!ids.length) return;
  const result = await call("upload.categories", {
    store_ids: ids,
    refresh: Boolean(refresh),
  });
  const categories = Object.fromEntries(
    Object.entries(result.stores || {}).map(([storeId, options]) => [
      storeId,
      { category_options: options },
    ]),
  );
  storeOptions.value = mergeUploadStoreOptions(
    storeOptions.value,
    categories,
  );
}

async function dialogStoreCategories(storeId, refresh = false) {
  if (!storeId) return;
  try {
    await loadStoreCategories([storeId], refresh);
  } catch (cause) {
    showNote(
      `官方类目读取失败：${errorText(cause)}`,
      "danger",
    );
  }
}

async function dialogStoreOptions(storeIds) {
  if (!storeIds.length) return;
  try {
    await loadStoreOptions(storeIds);
  } catch (cause) {
    showNote(
      `店铺配置读取失败：${errorText(cause)}；可选择“来源或平台默认”并继续`,
      "warning",
    );
  }
}

function taskModalConfig(storeId) {
  return taskModal.configs[String(storeId)] || {};
}

function askRisk({ title, description, confirmLabel = "确认", action }) {
  riskModal.open = true;
  riskModal.busy = false;
  riskModal.title = title;
  riskModal.description = description;
  riskModal.confirmLabel = confirmLabel;
  riskModal.action = action;
}

async function confirmRisk() {
  if (!riskModal.action || riskModal.busy) return;
  riskModal.busy = true;
  try {
    await riskModal.action();
    riskModal.open = false;
  } catch (cause) {
    showNote(errorText(cause), "danger");
  } finally {
    riskModal.busy = false;
  }
}

function openCreateTemplate() {
  templateModal.template = null;
  templateModal.open = true;
}

function openEditTemplate(template) {
  if (!template?.id) return;
  selectedTemplateId.value = template.id;
  templateModal.template = { ...template };
  templateModal.open = true;
}

async function saveTemplate(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    showNote("模板数据无效", "danger");
    return;
  }
  templateModal.busy = true;
  try {
    const saved = await call("upload.templates.save", { template: payload });
    templateModal.open = false;
    selectedTemplateId.value = saved.template.id;
    await load({ keepNote: true });
    showNote(`模板“${saved.template.name}”已保存`, "success");
  } catch (cause) {
    showNote(errorText(cause), "danger");
  } finally {
    templateModal.busy = false;
  }
}

function copyTemplate(template) {
  askRisk({
    title: "复制上货模板",
    description: `将完整复制模板“${template.name || "未命名模板"}”的全部字段，并生成一个新模板。`,
    confirmLabel: "复制模板",
    action: async () => {
      const result = await call("upload.templates.copy", { template_id: template.id });
      selectedTemplateId.value = result.template.id;
      await load({ keepNote: true });
      showNote(`已复制为“${result.template.name}”`, "success");
    },
  });
}

function setDefaultTemplate(template) {
  askRisk({
    title: "设为默认模板",
    description: `新创建上传任务时将默认选择“${template.name || "未命名模板"}”。`,
    confirmLabel: "设为默认",
    action: async () => {
      await call("upload.templates.set-default", { template_id: template.id });
      defaultTemplateId.value = template.id;
      selectedTemplateId.value = template.id;
      await load({ keepNote: true });
      showNote(`已将“${template.name}”设为默认模板`, "success");
    },
  });
}

function deleteTemplate(template) {
  askRisk({
    title: "删除上货模板",
    description: `将删除模板“${template.name || "未命名模板"}”。已创建任务保存的是模板快照，不受影响。`,
    confirmLabel: "删除模板",
    action: async () => {
      await call("upload.templates.delete", { template_id: template.id });
      await load({ keepNote: true });
      showNote("模板已删除", "success");
    },
  });
}

function openPackagePicker() {
  packageInput.value?.click();
}

function openFolderPicker() {
  folderInput.value?.click();
}

async function importFiles(event) {
  const selectedFiles = Array.from(event.target?.files || []);
  event.target.value = "";
  if (!selectedFiles.length) return;
  importState.busy = true;
  try {
    const files = [];
    const localErrors = [];
    for (const file of selectedFiles) {
      if (file.size > 20_000_000) {
        localErrors.push({ file: file.name, error: "文件超过 20 MB" });
        continue;
      }
      files.push({ name: file.name, content: await file.text() });
    }
    const result = files.length
      ? await call("upload.batch.import", { files, append: true })
      : { imported: 0, skipped: 0, parsed: 0, errors: [] };
    result.errors = [...localErrors, ...(result.errors || [])];
    importState.result = result;
    reparseResult.value = null;
    preflight.value = null;
    await load({ keepNote: true });
    showNote(
      `已识别 ${(result.formats || []).join("、") || "未知格式"}；导入 ${result.imported} 个商品，跳过重复 ${result.skipped} 个，失败文件 ${result.errors.length} 个`,
      result.errors.length ? "warning" : "success",
    );
  } catch (cause) {
    showNote(errorText(cause), "danger");
  } finally {
    importState.busy = false;
  }
}

async function preflightBatch() {
  try {
    const templateId = taskModal.templateId
      || defaultTemplateId.value
      || selectedTemplateId.value
      || templates.value[0]?.id
      || "";
    await ensureLegacyTemplateCloudViolation(templateId);
    preflight.value = await call("upload.batch.preflight", {
      batch_ids: selectedBatchIds.value,
      template_id: templateId,
    });
    const titleBlocked = enforceViolationPreflight(preflight.value);
    showNote(
      `本地预检完成：可创建 ${preflight.value.ready} 个，需处理 ${preflight.value.blocked} 个`
        + (titleBlocked ? `；其中 ${titleBlocked} 个命中售假标题库` : ""),
      preflight.value.blocked ? "warning" : "success",
    );
  } catch (cause) {
    showNote(errorText(cause), "danger");
  }
}

async function reparseBatch() {
  try {
    reparseResult.value = await call("upload.batch.reparse", {
      batch_ids: selectedBatchIds.value,
    });
    await load({ keepNote: true });
    await preflightBatch();
    showNote(
      `重新解析完成：批次可恢复 ${reparseResult.value.batch_recoverable} 个，`
      + `任务明细可恢复 ${reparseResult.value.task_recoverable} 个；`
      + "尚未向平台提交",
      "success",
    );
  } catch (cause) {
    showNote(errorText(cause), "danger");
  }
}

async function exportBatch() {
  try {
    const exported = await call("upload.batch.export", {
      batch_ids: selectedBatchIds.value,
    });
    const blob = new Blob([exported.content], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = exported.filename;
    anchor.click();
    URL.revokeObjectURL(url);
    showNote(`已导出 ${exported.count} 个商品（敏感字段已脱敏）`, "success");
  } catch (cause) {
    showNote(errorText(cause), "danger");
  }
}

function clearBatch() {
  askRisk({
    title: "清空当前导入批次",
    description: "只清空尚未创建任务的数据预览；已经创建的上传任务、历史和商品结果不会删除。",
    confirmLabel: "清空批次",
    action: async () => {
      await call("upload.batch.clear", {});
      selectedBatchIds.value = [];
      preflight.value = null;
      reparseResult.value = null;
      await load({ keepNote: true });
      showNote("当前导入批次已清空", "success");
    },
  });
}

async function openTaskCreator() {
  if (!selectedBatchItems.value.length) {
    showNote("请先勾选要创建任务的商品", "warning");
    return;
  }
  if (blockSelectedViolationTitles(selectedBatchItems.value, "选择店铺")) return;
  if (!templates.value.length) {
    showNote("请先创建完整的上货模板", "warning");
    return;
  }
  taskModal.storeIds = assignedSelectedStoreIds.value.length
    ? assignedSelectedStoreIds.value
    : [defaultUploadStoreId.value].filter(Boolean);
  taskModal.templateId = defaultTemplateId.value
    || selectedTemplateId.value
    || templates.value[0]?.id
    || "";
  taskModal.productCount = selectedBatchItems.value.length;
  taskModal.configs = Object.fromEntries(
    taskModal.storeIds.map((storeId) => [
      String(storeId),
      {
        freight_template_id: "",
        brand_mode: "none",
        brand_id: "2100000000",
        after_sale_address_id: "",
      },
    ]),
  );
  taskModal.open = true;
}

async function createTasks(payload = null) {
  if (blockSelectedViolationTitles(selectedBatchItems.value, "创建任务")) return;
  const storeIds = (payload?.storeIds || taskModal.storeIds)
    .map(Number)
    .filter(Boolean);
  const templateId = payload?.templateId || taskModal.templateId;
  const configs = payload?.configs || taskModal.configs;
  const mode = payload?.mode || "average";
  if (!storeIds.length) {
    showNote("请选择至少一个目标店铺", "warning");
    return;
  }
  taskModal.storeIds = storeIds;
  taskModal.templateId = templateId;
  taskModal.configs = configs;
  taskModal.busy = true;
  try {
    await ensureLegacyTemplateCloudViolation(templateId);
    const titlePreview = await call("upload.batch.preflight", {
      batch_ids: selectedBatchItems.value.map((row) => row.batch_id),
      template_id: templateId,
    });
    const titleBlocked = enforceViolationPreflight(titlePreview);
    if (titleBlocked) {
      showNote(
        `创建任务已禁止：${titleBlocked} 个商品命中真实售假标题库`,
        "danger",
      );
      return;
    }
    const assignments = mode === "all"
      ? {}
      : Object.fromEntries(
          selectedBatchItems.value.map((row, index) => [
            row.batch_id,
            storeIds[index % storeIds.length],
          ]),
        );
    const selectedStoreOptions = Object.fromEntries(
      storeIds.map((storeId) => [
        String(storeId),
        { ...(configs[String(storeId)] || {}) },
      ]),
    );
    const result = await call("upload.tasks.create", {
      batch_ids: selectedBatchItems.value.map((row) => row.batch_id),
      store_ids: storeIds,
      template_id: templateId,
      assignments,
      store_options: selectedStoreOptions,
    });
    const blockedStores = result.blocked_stores || [];
    if (!result.created && blockedStores.length) {
      showNote(
        `未创建任务：${blockedStores.map((row) => row.error || row.store_name).join("；")}`,
        "danger",
      );
      return;
    }
    taskModal.open = false;
    await load({ keepNote: true });
    showNote(
      blockedStores.length
        ? `已创建 ${result.created} 个店铺任务；${blockedStores.map((row) => row.store_name).join("、")} 因指定类目不可用被阻止，请修改模板`
        : `已创建 ${result.created} 个店铺任务；仅写入本地，尚未提交到平台`,
      blockedStores.length ? "warning" : "success",
    );
    await goToTasks();
  } catch (cause) {
    showNote(errorText(cause), "danger");
  } finally {
    taskModal.busy = false;
  }
}

async function goToTasks() {
  activeTab.value = "tasks";
  await loadLogs();
  if (String(route.query.tab || "") !== "tasks") {
    await router.replace({
      query: {
        ...route.query,
        tab: "tasks",
      },
    });
  }
}

function confirmStartUpload() {
  if (!runnableTaskIds.value.length) {
    showNote("请先勾选上传任务或任务中的商品", "warning");
    return;
  }
  if (blockTaskViolationTitles(runnableTaskIds.value, selectedItemIds.value)) return;
  const itemCount = selectedItemIds.value.length || tasks.value
    .filter((task) => runnableTaskIds.value.includes(task.id))
    .reduce(
      (count, task) => count + (task.items || []).filter((item) => (
        !["success", "skipped", "verification_pending", "waiting_category"].includes(item.status)
      )).length,
      0,
    );
  const selectedTaskRows = tasks.value.filter(
    (task) => runnableTaskIds.value.includes(task.id),
  );
  const targetStores = [...new Set(
    selectedTaskRows.map(
      (task) => task.store_name || `店铺 ${task.store_id}`,
    ),
  )];
  const selectedTemplates = [...new Set(
    selectedTaskRows.map(
      (task) => task.template?.name || "未命名模板",
    ),
  )];
  const firstTemplate = selectedTaskRows[0]?.template || {};
  const deposit = Number(firstTemplate.deposit_limit || 0);
  const dedupe = firstTemplate.duplicate_filter_mode === "off"
    ? "关闭去重（允许重复）"
    : firstTemplate.duplicate_filter_mode === "computer"
      ? "本机去重"
      : "本店去重";
  askRisk({
    title: "确认提交微信小店",
    description:
      `目标店铺：${targetStores.join("、") || "—"}\n` +
      `商品数量：${itemCount} 件\n` +
      `上货模板：${selectedTemplates.join("、") || "—"}\n` +
      `价格规则：${templatePriceRule(firstTemplate)}\n` +
      `最高保证金：${deposit > 0 ? `¥${deposit.toLocaleString("zh-CN")}` : "不限"}\n` +
      `去重方式：${dedupe}\n\n` +
      "这是真实平台写操作，可能消耗提审额度；结果未知的商品不会自动重复提交。",
    confirmLabel: "开始真实提交",
    action: async () => {
      if (blockTaskViolationTitles(runnableTaskIds.value, selectedItemIds.value)) return;
      const started = await call("upload.run.start", {
        task_ids: runnableTaskIds.value,
        item_ids: selectedItemIds.value,
      });
      uploadJob.jobId = started.job_id;
      uploadJob.status = "starting";
      uploadJob.message = "正在准备上传任务…";
      uploadJob.completed = 0;
      uploadJob.total = itemCount;
      await pollJob();
    },
  });
}

async function pollJob() {
  const jobId = uploadJob.jobId;
  if (!jobId || pollingJobId === jobId) return;
  pollingJobId = jobId;
  try {
    while (!unmounted && uploadJob.jobId === jobId) {
      let job;
      try {
        job = await call("upload.run.status", { job_id: jobId });
      } catch (cause) {
        showNote(errorText(cause), "danger");
        break;
      }
      applyUploadJob(job);
      if (terminalJobStatuses.has(job.status)) {
        const tone = ["failed", "partial"].includes(job.status)
          ? "danger"
          : ["paused", "cancelled"].includes(job.status)
            ? "warning"
            : "success";
        showNote(job.message || "任务结束", tone);
        await load({ keepNote: true });
        await loadLogs();
        break;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 650));
    }
  } finally {
    if (pollingJobId === jobId) pollingJobId = "";
  }
}

async function pauseUpload() {
  try {
    const job = await call("upload.run.pause", { job_id: uploadJob.jobId });
    uploadJob.status = job.status;
    uploadJob.message = job.message;
    showNote(job.message, "warning");
  } catch (cause) {
    showNote(errorText(cause), "danger");
  }
}

function cancelUpload() {
  askRisk({
    title: "取消上传任务",
    description: "取消只会阻止尚未开始提交的商品；当前在途商品处理结束前不会显示为已停止。",
    confirmLabel: "请求取消",
    action: async () => {
      const job = await call("upload.run.cancel", { job_id: uploadJob.jobId });
      uploadJob.status = job.status;
      uploadJob.message = job.message;
      showNote(job.message, "warning");
    },
  });
}

async function retryFailed() {
  try {
    const result = await call("upload.tasks.retry", {
      task_ids: runnableTaskIds.value,
      item_ids: selectedItemIds.value,
    });
    await load({ keepNote: true });
    await loadLogs();
    showNote(
      result.retried
        ? `已将 ${result.retried} 个失败或本地授权前被拒绝的商品放回队列；其他待核实商品未重试`
        : "没有可重试的失败或本地授权前被拒绝商品",
      result.retried ? "success" : "warning",
    );
  } catch (cause) {
    showNote(errorText(cause), "danger");
  }
}

function clearTasks(scope) {
  if (scope === "selected" && !runnableTaskIds.value.length) {
    showNote("请先勾选要删除的上传任务或商品", "warning");
    return;
  }
  const description = scope === "all"
    ? "将删除全部本地任务记录，但保留已成功上货历史。"
    : scope === "completed"
      ? "将删除已完成任务记录，但保留已成功上货历史。"
      : `将删除涉及当前选中商品的 ${runnableTaskIds.value.length} 个任务，但保留已成功上货历史。`;
  askRisk({
    title: scope === "all" ? "一键清空任务" : "清空任务记录",
    description: `${description} 运行中的任务必须先暂停并等待在途商品结束。`,
    confirmLabel: "确认清空",
    action: async () => {
      const result = await call("upload.tasks.clear", {
        scope,
        task_ids: runnableTaskIds.value,
      });
      selectedTaskIds.value = [];
      selectedItemIds.value = [];
      await load({ keepNote: true });
      await loadLogs();
      showNote(`已清空 ${result.deleted} 个任务记录`, "success");
    },
  });
}

async function refreshTask(task) {
  await load({ keepNote: true });
  await loadLogs();
  showNote(`任务“${task.store_name || task.id}”已从本地持久化记录刷新`, "success");
}

async function loadLogs() {
  logsLoading.value = true;
  try {
    const result = await call("upload.logs", {
      task_id: logFilters.taskId,
      status: logFilters.status,
      keyword: logFilters.keyword,
    });
    logs.value = result.logs || [];
  } catch (cause) {
    showNote(errorText(cause), "danger");
  } finally {
    logsLoading.value = false;
  }
}

async function refreshLogs() {
  await load({ keepNote: true });
  await loadLogs();
  showNote("上传日志已刷新", "success");
}

onMounted(async () => {
  const routedTab = String(route.query.tab || "");
  if (tabs.some(([key]) => key === routedTab)) {
    activeTab.value = routedTab;
  }
  await load();
  if (activeTab.value === "tasks") await loadLogs();
});
onBeforeUnmount(() => {
  unmounted = true;
  window.clearTimeout(noteTimer);
});
</script>

<template>
  <section class="upload-page">
    <nav class="page-tabs" aria-label="上传商品页面">
      <button
        v-for="[key, label] in tabs"
        :key="key"
        type="button"
        :class="{ active: activeTab === key }"
        @click="selectTab(key)"
      >{{ label }}</button>
    </nav>

    <div class="upload-body">
      <template v-if="activeTab === 'templates'">
        <header class="section-head">
          <div>
            <h1>上货模板</h1>
            <p>把标题、价格、库存和发布规则保存成可复用模板；任务创建后使用独立快照。</p>
          </div>
          <div class="template-head-actions">
            <button class="btn primary" type="button" @click="openCreateTemplate">添加上货模板</button>
            <button class="btn" type="button" @click="selectTab('upload')">进入上传商品</button>
          </div>
        </header>

        <section class="violation-title-library" aria-label="云违规标题库">
          <div>
            <strong>云违规标题库</strong>
            <span>来自违规中心“出售假冒商品”等记录 · 同标题禁止上传</span>
          </div>
          <b>{{ violationTitleCount }} 条</b>
          <div v-if="violationTitles.length" class="violation-title-samples">
            <span v-for="row in violationTitles.slice(0, 8)" :key="row.normalized || row.title">{{ row.title }}</span>
            <span v-if="violationTitleCount > 8">另有 {{ violationTitleCount - 8 }} 条</span>
          </div>
          <p v-else>尚未同步违规标题。请先到违规中心刷新明确违规商品。</p>
        </section>

        <div class="template-table-shell">
          <table class="data-table template-table">
            <thead>
              <tr>
                <th class="template-select-column">选择</th>
                <th>模板名称</th>
                <th>标题规则</th>
                <th>价格规则</th>
                <th>默认库存</th>
                <th>发布方式</th>
                <th>更新时间</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="template in templates"
                :key="template.id"
                :class="{ selected: selectedTemplateId === template.id }"
                @click="selectedTemplateId = template.id"
                @dblclick="openEditTemplate(template)"
              >
                <td class="template-select-column">
                  <input v-model="selectedTemplateId" type="radio" :value="template.id" @click.stop />
                </td>
                <td>
                  <div class="template-name-cell">
                    <strong>{{ template.name || "未命名模板" }}</strong>
                    <span v-if="template.id === defaultTemplateId" class="default-template-badge">默认</span>
                  </div>
                </td>
                <td :title="templateTitleRule(template)">{{ templateTitleRule(template) }}</td>
                <td :title="templatePriceRule(template)">{{ templatePriceRule(template) }}</td>
                <td>{{ Number(template.stock || 0) > 0 ? template.stock : "按来源库存" }}</td>
                <td>{{ templateListingLabel(template) }}</td>
                <td>{{ templateUpdatedAt(template) }}</td>
              </tr>
              <tr v-if="!loading && templates.length === 0">
                <td colspan="7" class="upload-empty">暂无上货模板。点击“添加上货模板”创建第一条规则。</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="template-actions-bar">
          <button class="btn" type="button" :disabled="!selectedTemplateId" @click="openEditTemplate(templates.find((row) => row.id === selectedTemplateId))">编辑模板</button>
          <button class="btn" type="button" :disabled="!selectedTemplateId" @click="copyTemplate(templates.find((row) => row.id === selectedTemplateId))">复制模板</button>
          <button class="btn" type="button" :disabled="!selectedTemplateId || selectedTemplateId === defaultTemplateId" @click="setDefaultTemplate(templates.find((row) => row.id === selectedTemplateId))">设为默认</button>
          <button class="btn danger" type="button" :disabled="!selectedTemplateId" @click="deleteTemplate(templates.find((row) => row.id === selectedTemplateId))">删除模板</button>
          <span>双击模板行也可直接编辑。</span>
        </div>
      </template>

      <template v-else-if="activeTab === 'upload'">
        <div class="upload-toolbar">
          <button
            class="btn reference-blue"
            type="button"
            :disabled="activeRun || !runnableTaskIds.length"
            :title="activeRun ? '已有上传任务运行中' : runnableTaskIds.length ? '开始执行已选中的上传任务或商品' : '请先到上传任务页勾选任务或商品'"
            @click="confirmStartUpload"
          >
            {{ activeRun ? "正在上传..." : (runnableTaskIds.length ? `开始上传（${runnableTaskIds.length}）` : "开始上传") }}
          </button>
          <button
            class="btn reference-blue"
            type="button"
            :disabled="!selectedBatchIds.length || !templates.length"
            :title="!selectedBatchIds.length ? '请先勾选商品' : !templates.length ? '请先创建上货模板' : '选择店铺并逐店核对运费模板、品牌和售后地址'"
            @click="openTaskCreator"
          >选择店铺并创建任务</button>
          <button class="btn reference-light-blue" type="button" :disabled="importState.busy" @click="openPackagePicker">导入数据包</button>
          <button class="btn reference-light-blue" type="button" :disabled="importState.busy" @click="openFolderPicker">导入文件夹</button>
          <button class="btn reference-light-blue" type="button" :disabled="!batch.length" @click="preflightBatch">本地预检</button>
          <button class="btn reference-light-blue" type="button" :disabled="!batch.length" @click="reparseBatch">重新解析预览</button>
          <button class="btn reference-light-blue" type="button" :disabled="activeRun || !failedItemCount" @click="retryFailed">重试失败</button>
          <button class="btn reference-orange" type="button" :disabled="!batch.length" @click="exportBatch">导出数据包</button>
          <button class="btn reference-gray" type="button" :disabled="!batch.length" @click="clearBatch">清空列表</button>
          <input ref="packageInput" class="visually-hidden" type="file" accept=".json,.jsonl,.csv,.tsv,.txt" multiple @change="importFiles" />
          <input ref="folderInput" class="visually-hidden" type="file" accept=".json,.jsonl,.csv,.tsv,.txt" multiple webkitdirectory @change="importFiles" />
          <span class="upload-tip">支持 TXT / 微信 JSON 数据包及 1688、淘宝 JSON/CSV/TSV；按内容自动识别，仅在本机做格式校验和字段标准化。</span>
        </div>

        <div v-if="importState.busy" class="job-banner running"><span class="spin-dot"></span>正在本地读取并校验数据包…</div>
        <div v-if="importState.result?.errors?.length" class="import-errors">
          <strong>部分文件未导入</strong>
          <span v-for="error in importState.result.errors.slice(0, 8)" :key="`${error.file}-${error.error}`">
            {{ error.file }}：{{ error.error }}
          </span>
        </div>
        <div v-if="preflight" class="preflight-summary">
          <strong>本地预检：{{ preflight.ready }} 可创建 / {{ preflight.blocked }} 需处理</strong>
          <span v-for="row in preflight.rows.filter((item) => !item.ready).slice(0, 5)" :key="row.batch_id">
            {{ row.title || row.source_product_id }}：{{ row.issues.join("；") }}
          </span>
          <span v-for="row in preflight.rows.slice(0, 5)" :key="`mapping-${row.batch_id}`">
            {{ preflightMappingText(row) }}
          </span>
        </div>
        <div v-if="reparseResult" class="preflight-summary">
          <strong>
            重新解析：批次可恢复 {{ reparseResult.batch_recoverable }} /
            阻断 {{ reparseResult.batch_blocked }}；任务明细可恢复
            {{ reparseResult.task_recoverable }}
          </strong>
          <span>
            仅更新本地规范化预览，未向平台提交；需确认后再创建或执行任务。
          </span>
          <span v-for="row in reparseResult.batch_preview.slice(0, 5)" :key="`reparse-${row.batch_id}`">
            {{ row.title || row.source_product_id }}：SKU {{ row.sku_count }} · 主图 {{ row.head_image_count }} · {{ row.recoverable ? "可预览" : row.adapter_errors.join("；") }}
          </span>
        </div>
        <div v-if="uploadJob.jobId" class="job-banner" :class="statusTone(uploadJob.status)">
          <strong>上传任务 · {{ statusLabel(uploadJob.status) }}</strong>
          <span>{{ uploadJob.message }}</span>
          <div class="progress-track"><i :style="{ width: `${uploadProgress}%` }"></i></div>
          <small>{{ uploadJob.completed }} / {{ uploadJob.total }}</small>
        </div>

        <div class="upload-filter">
          <input v-model="filters.keyword" class="input keyword-input" placeholder="关键词（空格分隔）" />
          <input v-model="filters.minPrice" class="input price-input" type="number" placeholder="最低价" />
          <span>至</span>
          <input v-model="filters.maxPrice" class="input price-input" type="number" placeholder="最高价" />
          <span class="upload-count">显示 {{ filteredBatch.length }} / {{ batch.length }} 条 · 已选 {{ selectedBatchIds.length }}</span>
          <button class="btn" type="button" @click="resetBatchFilters">重置筛选</button>
        </div>

        <div class="upload-table-card">
          <div class="table-scroll">
            <table class="data-table upload-table">
              <thead>
                <tr>
                  <th class="check-column"><input type="checkbox" :checked="allFilteredBatchSelected" @change="toggleAllFilteredBatch" /></th>
                  <th>序号</th>
                  <th>平台</th>
                  <th>来源商品 ID</th>
                  <th>商品标题</th>
                  <th>价格</th>
                  <th>库存</th>
                  <th>SKU</th>
                  <th>来源文件</th>
                  <th>本地预检</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="(item, index) in filteredBatch"
                  :key="item.batch_id"
                  :class="{ selected: selectedBatchIds.includes(item.batch_id) }"
                >
                  <td class="check-column"><input type="checkbox" :checked="selectedBatchIds.includes(item.batch_id)" @change="toggleBatch(item.batch_id)" /></td>
                  <td>{{ index + 1 }}</td>
                  <td><span class="platform-chip">{{ item.platform || "未识别" }}</span></td>
                  <td class="mono-cell" :title="item.source_product_id">{{ item.source_product_id || "-" }}</td>
                  <td :title="item.title">{{ item.title || "-" }}</td>
                  <td>{{ formatMoney(item.price) }}</td>
                  <td>{{ item.stock == null ? "暂无数据" : item.stock }}</td>
                  <td>{{ (item.payload?.skus || item.payload?.skuList?.skuInfo || []).length }}</td>
                  <td :title="item.source_file">{{ item.source_file || "-" }}</td>
                  <td>
                    <span v-if="!preflightMap.get(item.batch_id)" class="status-pill">未校验</span>
                    <span v-else-if="preflightMap.get(item.batch_id).ready" class="status-pill success">可创建</span>
                    <span v-else class="status-pill danger" :title="preflightMap.get(item.batch_id).issues.join('；')">需处理</span>
                  </td>
                </tr>
                <tr v-if="!loading && filteredBatch.length === 0">
                  <td colspan="10" class="upload-empty">
                    <span>暂无导入商品。请导入 1688、淘宝或微信小店数据包。</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </template>

      <template v-else>
        <div class="task-summary">
          <span>任务 {{ tasks.length }}</span>
          <span>已完成 {{ completedTaskCount }}</span>
          <span>失败商品 {{ failedItemCount }}</span>
          <span>可重试商品 {{ retryableItemCount }}</span>
          <span>已选任务 {{ selectedTaskIds.length }} · 已选商品 {{ selectedItemIds.length }}</span>
          <span>最多并发遵循现有上传设置</span>
          <button
            class="category-auto-resume"
            type="button"
            :aria-pressed="categoryAutoResumeEnabled"
            :title="categoryAutoResumeEnabled ? '点击关闭类目自动续传' : '点击开启类目自动续传'"
            @click="toggleCategoryAutoResume"
          >
            类目通过后自动续传：{{ categoryAutoResumeEnabled ? "开" : "关" }}
          </button>
        </div>
        <div v-if="uploadJob.jobId" class="job-banner" :class="statusTone(uploadJob.status)">
          <strong>当前上传 · {{ statusLabel(uploadJob.status) }}</strong>
          <span>{{ uploadJob.message }}</span>
          <div class="progress-track"><i :style="{ width: `${uploadProgress}%` }"></i></div>
          <small>{{ uploadJob.completed }} / {{ uploadJob.total }}</small>
        </div>
        <div class="task-layout">
          <section class="task-list-panel">
            <header>
              <div>
                <strong>上传任务</strong>
                <small>任务和 item 状态持久化于 SQLite 设置快照</small>
              </div>
              <div class="task-head-actions">
                <button type="button" class="inline-link" @click="toggleAllTasks">{{ allTasksSelected ? "取消全选" : "全选" }}</button>
                <span class="task-count">{{ tasks.length }}</span>
              </div>
            </header>
            <div class="task-toolbar">
              <button
                type="button"
                :title="activeRun ? '已有上传任务运行中，可在下方查看实时进度' : '开始或继续选中任务或商品'"
                :disabled="!runnableTaskIds.length || activeRun"
                @click="confirmStartUpload"
              >{{ activeRun ? `运行中 ${uploadJob.completed}/${uploadJob.total}` : `开始 / 继续${runnableTaskIds.length ? `（${runnableTaskIds.length}）` : ""}` }}</button>
              <button type="button" :disabled="!activeRun" title="当前商品处理结束后暂停" @click="pauseUpload">暂停</button>
              <button type="button" class="danger-text" :disabled="!activeRun" @click="cancelUpload">取消</button>
              <button type="button" :disabled="activeRun || !retryableItemCount" @click="retryFailed">重试失败</button>
              <button type="button" :disabled="activeRun || !completedTaskCount" @click="clearTasks('completed')">清空已完成</button>
              <button type="button" :disabled="activeRun || (!selectedTaskIds.length && !selectedItemIds.length)" @click="clearTasks('selected')">删除选中任务</button>
              <button class="danger-text" type="button" :disabled="activeRun || !tasks.length" @click="clearTasks('all')">一键清空任务</button>
            </div>
            <div v-if="tasks.length" class="task-items">
              <label
                v-for="task in tasks"
                :key="task.id"
                class="task-item"
                :class="{ selected: selectedTaskIds.includes(task.id) }"
              >
                <input
                  type="checkbox"
                  :checked="selectedTaskIds.includes(task.id)"
                  @change="toggleTask(task.id)"
                />
                <span class="task-main">
                  <strong>{{ task.store_name || `店铺 ${task.store_id}` }}</strong>
                  <small>
                    {{ task.template?.name || "无模板" }} · {{ taskSourceCount(task) }} 个商品
                    <template v-if="taskCategoryLabel(task)"> · {{ taskCategoryLabel(task) }}</template>
                    · {{ task.message }}
                  </small>
                </span>
                <span class="task-state">
                  <em class="status-pill" :class="statusTone(task.status)">{{ statusLabel(task.status) }}</em>
                  <small>{{ taskProgress(task) }}</small>
                  <button type="button" class="inline-link" @click.prevent.stop="refreshTask(task)">刷新</button>
                </span>
              </label>
            </div>
            <div v-else class="task-empty">选择店铺并分配后生成任务</div>
          </section>

          <section class="task-log-panel">
            <header>
              <strong>上传日志</strong>
              <button type="button" :disabled="logsLoading" @click="refreshLogs">{{ logsLoading ? "刷新中…" : "全部刷新" }}</button>
            </header>
            <div class="log-filter">
              <select v-model="logFilters.taskId" class="select" @change="loadLogs">
                <option value="">全部任务</option>
                <option v-for="task in tasks" :key="task.id" :value="task.id">{{ task.store_name }} · {{ task.created_at }}</option>
              </select>
              <select v-model="logFilters.status" class="select" @change="loadLogs">
                <option value="">全部状态</option>
                <option value="pending">待执行</option>
                <option value="running">执行中</option>
                <option value="success">成功</option>
                <option value="failed">失败</option>
                <option value="verification_pending">待核实</option>
                <option value="waiting_category">等待类目</option>
              </select>
              <input v-model="logFilters.keyword" class="input" placeholder="店铺、商品或错误关键词" @keyup.enter="loadLogs" />
              <button type="button" @click="loadLogs">筛选</button>
            </div>
            <div class="log-table-wrap">
              <table class="log-table">
                <thead><tr><th>选</th><th>时间</th><th>店铺 / 商品</th><th>状态</th><th>日志</th><th>远端 ID</th><th>操作</th></tr></thead>
                <tbody>
                  <tr v-for="row in logs" :key="row.id">
                    <td><input type="checkbox" :checked="selectedItemIds.includes(row.item_id)" :disabled="!itemCanRun(row)" :title="itemCanRun(row) ? '仅执行该商品' : '成功、跳过、待核实或等待类目的商品不可重复提交'" @change="toggleLogItem(row)" /></td>
                    <td>{{ row.finished_at || row.started_at || "-" }}</td>
                    <td><strong>{{ row.store_name }}</strong><small>{{ row.title }} · {{ row.source_product_id }}</small></td>
                    <td>
                      <span class="status-pill" :class="statusTone(row.status)">{{ itemStatusLabel(row.status, row) }}</span>
                      <small v-if="categoryStatusDetail(row)" class="category-status-detail">{{ categoryStatusDetail(row) }}</small>
                    </td>
                    <td :title="row.message">{{ row.message || "-" }}</td>
                    <td class="mono-cell">{{ row.product_id || "-" }}</td>
                    <td>
                      <button type="button" class="inline-link" @click="refreshLogs">刷新状态</button>
                      <button
                        v-if="row.status === 'waiting_category'"
                        type="button"
                        class="inline-link danger-text"
                        @click="cancelCategoryWait(row)"
                      >取消等待</button>
                    </td>
                  </tr>
                  <tr v-if="!logsLoading && !logs.length"><td colspan="7" class="task-empty">当前筛选条件下暂无上传日志</td></tr>
                  <tr v-if="logsLoading"><td colspan="7" class="task-empty">正在读取本地持久化日志…</td></tr>
                </tbody>
              </table>
            </div>
          </section>
        </div>
      </template>
    </div>

    <UploadTemplateDialog
      :open="templateModal.open"
      :template="templateModal.template"
      :defaults="templateDefaults"
      :violation-types="cloudViolationTypes"
      :stores="stores"
      :store-options="storeOptions"
      :busy="templateModal.busy"
      @close="templateModal.open = false"
      @load-categories="dialogStoreCategories"
      @save="saveTemplate"
      @toast="showNote"
    />

    <UploadStoreSelectionDialog
      :open="taskModal.open"
      :stores="stores"
      :templates="templates"
      :default-template-id="defaultTemplateId"
      :initial-store-ids="taskModal.storeIds"
      :options="storeOptions"
      :product-count="selectedBatchItems.length"
      :busy="taskModal.busy"
      @close="taskModal.open = false"
      @load-options="dialogStoreOptions"
      @submit="createTasks"
      @toast="showNote"
    />

    <ModalDialog
      :open="riskModal.open"
      :title="riskModal.title"
      subtitle="请核对目标和影响范围"
      width="780px"
      :busy="riskModal.busy"
      @close="riskModal.open = false"
    >
      <p class="risk-copy">{{ riskModal.description }}</p>
      <template #footer>
        <button class="btn" type="button" :disabled="riskModal.busy" @click="riskModal.open = false">取消</button>
        <button class="btn danger" type="button" :disabled="riskModal.busy" @click="confirmRisk">{{ riskModal.busy ? "处理中…" : riskModal.confirmLabel }}</button>
      </template>
    </ModalDialog>

    <div v-if="note.message" class="toast" :class="note.tone">{{ note.message }}</div>
  </section>
</template>

<style scoped>
.upload-page {
  width: 100%;
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: #f2f4f7;
}

.page-tabs {
  flex: 0 0 52px;
  padding: 0 38px;
  display: flex;
  gap: 38px;
  border-bottom: 1px solid #d9e2ea;
  background: #fff;
}

.page-tabs button {
  position: relative;
  padding: 0;
  border: 0;
  color: #556575;
  background: transparent;
  font-size: 14px;
}

.page-tabs button.active {
  color: #2475e8;
  font-weight: 600;
}

.page-tabs button.active::after {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 3px;
  background: #2475e8;
}

.upload-body {
  flex: 1 1 auto;
  min-height: 0;
  padding: 20px;
  overflow: auto;
}

.section-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 20px;
}

.section-head h1 {
  margin: 0;
  color: #17202a;
  font-size: 24px;
  font-weight: 600;
}

.section-head p {
  margin: 7px 0 0;
  color: #84909c;
  font-size: 12px;
}

.template-head-actions { display: flex; gap: 8px; }

.violation-title-library {
  margin-bottom: 16px;
  padding: 13px 15px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 9px 14px;
  border: 1px solid #ead6b2;
  border-radius: 5px;
  background: #fffaf1;
}

.violation-title-library > div:first-child {
  display: grid;
  gap: 4px;
}

.violation-title-library strong {
  color: #805923;
}

.violation-title-library span,
.violation-title-library p {
  color: #8b7453;
  font-size: 11px;
}

.violation-title-library > b {
  color: #b16f13;
  font-size: 18px;
}

.violation-title-library > p,
.violation-title-samples {
  grid-column: 1 / -1;
}

.violation-title-library > p {
  margin: 0;
}

.violation-title-samples {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.violation-title-samples span {
  max-width: 320px;
  padding: 4px 7px;
  overflow: hidden;
  border-radius: 99px;
  color: #795a2e;
  background: #fff;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.template-table-shell {
  min-height: 390px;
  overflow: auto;
  border: 1px solid #dce2e8;
  background: #fff;
}
.template-table { width: 100%; table-layout: fixed; }
.template-table th { height: 40px; color: #5b6d7c; background: #f7f9fb; font-size: 10px; text-align: left; }
.template-table td { height: 48px; padding: 7px 10px; border-bottom: 1px solid #edf1f4; color: #536676; font-size: 10px; }
.template-table tbody tr { cursor: default; }
.template-table tbody tr:hover td { background: #f8fbfd; }
.template-table tbody tr.selected td { background: #eef7ff; }
.template-table th:nth-child(2) { width: 190px; }
.template-table th:nth-child(3) { width: 260px; }
.template-table th:nth-child(4) { width: 250px; }
.template-table th:nth-child(5) { width: 110px; }
.template-table th:nth-child(6) { width: 110px; }
.template-table th:nth-child(7) { width: 160px; }
.template-select-column { width: 48px; text-align: center !important; }
.template-name-cell { display: flex; align-items: center; gap: 7px; }
.template-name-cell strong { color: #263c4d; font-size: 11px; }
.default-template-badge { padding: 1px 6px; border-radius: 999px; color: #126447; background: #dcf6e9; font-size: 8px; }
.template-actions-bar {
  padding: 9px 0 0;
  display: flex;
  align-items: center;
  gap: 7px;
}
.template-actions-bar span { margin-left: auto; color: #85939e; font-size: 9px; }

.upload-toolbar,
.upload-filter {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;
}

.upload-tip {
  margin-left: 6px;
  color: #505e6b;
  font-size: 12px;
}

.upload-filter {
  min-height: 58px;
  padding: 11px 14px;
  flex-wrap: wrap;
  border: 1px solid #dce2e8;
  background: #fff;
}

.keyword-input {
  width: 188px;
}

.price-input {
  width: 110px;
}

.upload-count {
  color: #6e7b88;
  font-size: 12px;
}

.upload-table-card {
  min-height: 470px;
  overflow: hidden;
  border: 1px solid #dce2e8;
  background: #fff;
}

.upload-table {
  width: 100%;
}

.upload-table th {
  height: 42px;
  background: #f8fafb;
  text-align: center;
}

.upload-table tr.selected td {
  background: #edf6ff;
}

.check-column {
  width: 44px;
}

.upload-empty {
  height: 330px !important;
  color: #637283;
  text-align: left;
  vertical-align: top;
}

.upload-empty span {
  display: block;
  padding: 12px 24px 0;
}

.task-layout {
  height: 100%;
  min-height: 600px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(400px, 0.9fr);
  gap: 14px;
}

.task-list-panel,
.task-log-panel {
  display: flex;
  flex-direction: column;
  border: 1px solid #dce2e8;
  background: #fff;
}

.task-list-panel > header,
.task-log-panel > header {
  min-height: 54px;
  padding: 0 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid #dce2e8;
}

.task-list-panel > header div {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.task-list-panel small {
  color: #8d98a3;
}

.task-count {
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  color: #fff;
  background: #9fb3c8;
}

.task-head-actions {
  display: flex;
  align-items: center;
  gap: 9px;
}

.task-toolbar {
  padding: 12px 14px;
  display: flex;
  gap: 7px;
  border-bottom: 1px solid #e8edf1;
}

.task-toolbar button,
.task-log-panel select {
  height: 30px;
  padding: 0 9px;
  border: 1px solid #d8dfe6;
  background: #fff;
}

.task-toolbar .danger-text {
  margin-left: auto;
  color: #dc5c52;
  background: #fff4f3;
}

.task-empty {
  flex: 1;
  min-height: 240px;
  display: grid;
  place-items: center;
  color: #8995a1;
}

.task-items {
  flex: 1;
  max-height: 500px;
  padding: 8px;
  overflow: auto;
}

.task-item {
  min-height: 54px;
  padding: 8px 9px;
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
  border-bottom: 1px solid #e5ebef;
  background: #fff;
  font-size: 12px;
}

.task-item span,
.task-item strong,
.task-item small {
  display: block;
  min-width: 0;
}

.task-item strong,
.task-item small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-item small {
  margin-top: 4px;
  color: #8794a0;
}

.task-item em {
  color: #13855b;
  font-style: normal;
}

.task-item.selected {
  background: #eef6ff;
  box-shadow: inset 3px 0 #2c6fec;
}

.task-log-panel > header div {
  display: flex;
  gap: 6px;
}

.reference-blue {
  border-color: #276fe7;
  color: #fff;
  background: #2c6fec;
}

.reference-light-blue {
  border-color: #9cbff0;
  color: #477dc5;
  background: #eef5ff;
}

.reference-orange {
  border-color: #eba675;
  color: #bd6827;
  background: #f8b47e;
}

.reference-gray {
  border-color: #d6cbc4;
  color: #7d726a;
  background: #eee8e3;
}

.template-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.upload-toolbar {
  flex-wrap: wrap;
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.template-rules {
  margin: 10px 0 0 !important;
  padding-top: 9px;
  border-top: 1px solid #edf0f3;
  color: #7d8995 !important;
  font-size: 11px !important;
}

.task-summary {
  margin-bottom: 12px;
  padding: 10px 14px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px 20px;
  border: 1px solid #dce2e8;
  border-radius: 5px;
  background: #fff;
  color: #61717f;
  font-size: 12px;
}

.job-banner,
.import-errors,
.preflight-summary {
  margin: 0 0 12px;
  padding: 12px 14px;
  display: grid;
  gap: 6px;
  border: 1px solid #d8e2ea;
  border-radius: 6px;
  background: #fff;
  color: #51616f;
  font-size: 12px;
}

.job-banner.running {
  border-color: #b9d5f7;
  background: #f4f9ff;
}

.job-banner.success {
  border-color: #bde0ce;
  background: #f3fbf7;
}

.job-banner.warning,
.import-errors,
.preflight-summary {
  border-color: #efd7ad;
  background: #fffbf2;
}

.job-banner.danger {
  border-color: #efc6c9;
  background: #fff7f8;
}

.job-banner span,
.import-errors span,
.preflight-summary span {
  display: block;
}

.progress-track {
  height: 5px;
  overflow: hidden;
  border-radius: 99px;
  background: #e7edf2;
}

.progress-track i {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: #2c6fec;
  transition: width .25s ease;
}

.spin-dot {
  width: 9px;
  height: 9px;
  display: inline-block;
  margin-right: 7px;
  border: 2px solid #9fc3ee;
  border-top-color: #2c6fec;
  border-radius: 50%;
  animation: spin .75s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.table-scroll,
.log-table-wrap {
  max-width: 100%;
  overflow: auto;
}

.upload-table {
  min-width: 1080px;
}

.mono-cell {
  max-width: 170px;
  overflow: hidden;
  color: #516171;
  font-family: "Cascadia Code", Consolas, monospace;
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.platform-chip,
.status-pill {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 2px 7px;
  border-radius: 99px;
  color: #526273;
  background: #edf1f4;
  font-size: 10px;
  font-style: normal;
  white-space: nowrap;
}

.platform-chip {
  color: #2f628f;
  background: #eaf3fb;
}

.status-pill.success {
  color: #16704b;
  background: #e9f8f0;
}

.status-pill.danger {
  color: #b23b46;
  background: #fdecef;
}

.status-pill.warning {
  color: #9b6712;
  background: #fff4dd;
}

.status-pill.running {
  color: #2561a8;
  background: #e9f2ff;
}

.category-status-detail {
  display: block;
  margin-top: 4px;
  color: #8a641e;
  font-size: 9px;
  line-height: 1.4;
}

.task-main {
  min-width: 0;
}

.task-state {
  min-width: 86px;
  display: grid !important;
  justify-items: end;
  gap: 3px;
}

.inline-link {
  padding: 0 !important;
  border: 0 !important;
  color: #2c6fec !important;
  background: transparent !important;
  font-size: 10px;
  cursor: pointer;
}

.log-filter {
  padding: 10px;
  display: grid;
  grid-template-columns: minmax(130px, 1fr) 110px minmax(120px, 1fr) auto;
  gap: 7px;
  border-bottom: 1px solid #e8edf1;
}

.log-filter .input,
.log-filter .select,
.log-filter button {
  min-width: 0;
  height: 31px;
  border: 1px solid #d8dfe6;
  background: #fff;
}

.log-table {
  width: 100%;
  min-width: 760px;
  border-collapse: collapse;
}

.log-table th,
.log-table td {
  padding: 8px 9px;
  border-bottom: 1px solid #edf1f4;
  color: #526272;
  font-size: 10px;
  text-align: left;
  vertical-align: top;
}

.log-table th {
  position: sticky;
  top: 0;
  z-index: 1;
  color: #72808d;
  background: #f8fafb;
}

.log-table td small {
  display: block;
  margin-top: 3px;
  color: #87939e;
}

.template-form label,
.task-create-form > label {
  color: #4d5d6a;
  font-size: 12px;
}

.code-area {
  width: 100%;
  margin-top: 7px;
  padding: 11px;
  resize: vertical;
  line-height: 1.55;
}

.code-area {
  min-height: 430px;
  font-family: "Cascadia Code", Consolas, monospace;
  font-size: 11px;
}

.target-store-list {
  max-height: 360px;
  overflow: auto;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 7px;
}

.assignment-head {
  color: #5e7080;
  background: #f5f8fa;
  font-size: 10px;
  font-weight: 600;
}

.assignment-row {
  border-top: 1px solid #edf1f4;
  color: #4f6170;
  font-size: 10px;
}

.assignment-row strong,
.assignment-row small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.assignment-row small {
  color: #87949f;
}

.assignment-row .select {
  width: 100%;
  min-width: 0;
  height: 30px;
}

.target-store-list label {
  min-width: 0;
  padding: 9px;
  display: grid;
  grid-template-columns: 17px minmax(0, 1fr);
  gap: 3px 7px;
  border: 1px solid #e1e7ec;
  border-radius: 6px;
  background: #fafcfd;
}

.target-store-list input {
  grid-row: 1 / 3;
}

.target-store-list span,
.target-store-list small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.target-store-list small {
  color: #8a96a0;
  font-size: 9px;
}

.risk-copy {
  margin: 0;
  color: #526371;
  font-size: 12px;
  line-height: 1.8;
  white-space: pre-line;
}

.toast.success {
  border-color: #b7ddca;
  background: #edf9f2;
}

.toast.warning {
  border-color: #ecd3a8;
  background: #fff8e9;
}

.toast.danger {
  border-color: #efc4c8;
  background: #fff3f4;
}

@media (max-width: 980px) {
  .task-layout {
    grid-template-columns: 1fr;
  }

  .task-log-panel {
    min-height: 480px;
  }

  .log-filter {
    grid-template-columns: 1fr 1fr;
  }

  .target-store-list {
    grid-template-columns: 1fr;
  }
}
</style>
