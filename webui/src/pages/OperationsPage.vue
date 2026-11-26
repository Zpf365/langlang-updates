<script setup>
import {
  computed,
  onMounted,
  reactive,
  ref,
  watch,
} from "vue";
import { useRoute, useRouter } from "vue-router";
import { call, formatDateTime } from "../api";
import Icon from "../components/Icon.vue";
import ModalDialog from "../components/ModalDialog.vue";
import {
  calculateRefundScenario,
  calculateReviewScenario,
  calculateServiceNoReply,
  calculateTrafficRequirement,
} from "../operationsCalculations";
import {
  clearOperationsAccess,
  sessionState,
} from "../session";

const route = useRoute();
const router = useRouter();
const section = ref(String(route.params.section || "overview"));
const loading = ref(true);
const productsLoading = ref(false);
const pageError = ref("");
const accessLost = ref(false);
const feedback = reactive({ message: "", tone: "" });
const inspection = reactive({
  active: false,
  current: 0,
  total: 0,
  message: "",
  failed: 0,
});
const dashboard = ref(null);
const productsPayload = ref(null);
const tasks = ref([]);
const rules = ref(null);
const drawer = reactive({
  open: false,
  kind: "",
  loading: false,
  data: null,
  error: "",
  showAllProductAfterSales: false,
});
const modal = reactive({
  open: false,
  title: "",
  subtitle: "",
  body: "",
  confirmLabel: "",
  cancelLabel: "取消",
  busy: false,
  action: null,
});
const form = reactive({
  taskStoreId: "",
  taskProductId: "",
  taskTitle: "",
  taskDetail: "",
  taskPriority: "P1",
  taskDueAt: "",
  taskCategory: "运营动作",
  taskAlertKey: "",
  taskEvidence: {},
});
const filters = reactive({
  search: "",
  range: "7d",
  status: "all",
  opportunity: "",
  productSearch: "",
  taskStatus: "all",
  taskPriority: "",
  taskStoreId: "",
  taskCategory: "",
  taskSearch: "",
});
const playbook = ref("traffic");
const calculator = ref("review");
const calculatorData = reactive({
  reviewTotal: "",
  reviewNegative: "",
  reviewTarget: 2,
  reviewRate: 30,
  reviewNewNegative: 0,
  reviewOrders: 10,
  refundOrders: "",
  refundCount: "",
  refundTarget: 5,
  refundNewRate: 2,
  refundNewOrders: 10,
  trafficOrders: 5,
  trafficClick: "",
  trafficPay: "",
  serviceTotal: "",
  serviceMissed: "",
  serviceTarget: 10,
});

const permission = computed(() => sessionState.operationsPermissions || {});
const pageMeta = {
  overview: ["运营中心", "体验分、流量、商品机会和行动任务统一运营"],
  stores: ["运营中心 · 店铺健康", "核验店铺体验分、流量、售后和服务信号"],
  products: ["运营中心 · 商品机会", "从真实商品、点击、支付和售后数据识别机会"],
  playbooks: ["运营中心 · 运营打法", "固定诊断顺序、动作矩阵和干预测算"],
  actions: ["运营中心 · 行动中心", "持久化任务、到期提醒、延期和完成记录"],
};
const navItems = [
  ["overview", "运营总览", "chart"],
  ["stores", "店铺健康", "store"],
  ["products", "商品机会", "package"],
  ["playbooks", "运营打法", "workflow"],
  ["actions", "行动中心", "clipboard"],
];
const playbooks = [
  {
    key: "traffic",
    title: "访客下降",
    lead: "先区分全店、单商品和单渠道下降，再核对商品状态与硬阻断。",
    steps: [
      ["0–30 分", "排除下架、售罄、违规与价格库存错误", "确认问题层级"],
      ["2–6 小时", "保存基线，一次只改一个变量", "找到最早下跌指标"],
      ["24–72 小时", "补真实内容与合规流量入口", "曝光恢复且转化不降"],
    ],
  },
  {
    key: "launch",
    title: "新品破零",
    lead: "先完成发布体检，再做真实内容承接和 14 天分层。",
    steps: [
      ["0–24 小时", "确认可搜索、可打开、可加购、可结算", "链路完整"],
      ["1–3 天", "补齐商品卡、二维码、短链和真实触点", "出现可追踪来源"],
      ["14–30 天", "保留、重做或淘汰", "进入生命周期层级"],
    ],
  },
  {
    key: "click",
    title: "点击率低",
    lead: "先判断曝光人群匹配，再单变量测试主图、标题或到手价。",
    steps: [
      ["判断", "拆分推荐、搜索和其他渠道", "定位低点击集中渠道"],
      ["首轮", "只改主图或首屏标题", "点击率提升且退款不升"],
      ["延伸", "补齐价格、库存和信任信息", "加购率同步改善"],
    ],
  },
  {
    key: "conversion",
    title: "转化低",
    lead: "按浏览、点击、加购、创建订单和支付逐层判断。",
    steps: [
      ["详情", "修正图文、SKU、尺寸和材质承诺", "加购率改善"],
      ["加购", "检查价值感、运费和优惠门槛", "加购率改善"],
      ["支付", "修复结算、库存和地址支付摩擦", "支付转化提升"],
    ],
  },
  {
    key: "review",
    title: "差评 / 品退",
    lead: "先聚类止损，再修正商品、包装和服务，不删除评价、不诱导好评。",
    steps: [
      ["30 分钟", "按商品、SKU、原因和批次聚类", "形成问题桶"],
      ["24 小时", "处理售后、改图改描述、修正包装", "新增问题下降"],
      ["7–30 天", "提高低退款商品成交占比", "差评率和品退率下降"],
    ],
  },
  {
    key: "service",
    title: "客服超时",
    lead: "优先处理仍在有效窗口、尚未回复的售后和高客单会话。",
    steps: [
      ["今日", "按超时、售后和金额排序", "未回复数下降"],
      ["24 小时", "高频问题前置到详情和快捷回复", "首响 ≤90 秒"],
      ["7 天", "按真实咨询热力排班", "不回复率 ≤10%"],
    ],
  },
  {
    key: "lifecycle",
    title: "零销量 / 未上货",
    lead: "区分没曝光、没点击、没支付，以及供给和商品池老化。",
    steps: [
      ["零销量", "判断卡在漏斗哪一层", "恢复可持续成交"],
      ["未上货", "核对供应链、状态和在售 SKU", "有效在售商品恢复"],
      ["生命周期", "分层潜力、畅销和衰退商品", "商品池结构改善"],
    ],
  },
];
const calculatorTabs = [
  ["review", "差评率"],
  ["refund", "品质退款率"],
  ["traffic", "成交流量"],
  ["service", "客服未回复率"],
];

const visibleStores = computed(() => dashboard.value?.stores || []);
const visibleProducts = computed(() => productsPayload.value?.products || []);
const activeAlerts = computed(() => dashboard.value?.alerts || []);
const activePlaybook = computed(
  () => playbooks.find((item) => item.key === playbook.value) || playbooks[0],
);
const reviewScenario = computed(() =>
  calculateReviewScenario({
    total: calculatorData.reviewTotal,
    negative: calculatorData.reviewNegative,
    targetPercent: calculatorData.reviewTarget,
    reviewRatePercent: calculatorData.reviewRate,
    newNegativeRatePercent: calculatorData.reviewNewNegative,
    orders: calculatorData.reviewOrders,
  }),
);
const refundScenario = computed(() =>
  calculateRefundScenario({
    orders: calculatorData.refundOrders,
    refunds: calculatorData.refundCount,
    targetPercent: calculatorData.refundTarget,
    newRefundRatePercent: calculatorData.refundNewRate,
    newOrders: calculatorData.refundNewOrders,
  }),
);
const trafficScenario = computed(() =>
  calculateTrafficRequirement({
    targetOrders: calculatorData.trafficOrders,
    clickRatePercent: calculatorData.trafficClick,
    payConversionPercent: calculatorData.trafficPay,
  }),
);
const serviceScenario = computed(() =>
  calculateServiceNoReply({
    total: calculatorData.serviceTotal,
    missed: calculatorData.serviceMissed,
    targetPercent: calculatorData.serviceTarget,
  }),
);
const chartSeries = computed(() => {
  const points = new Map();
  for (const store of visibleStores.value) {
    for (const row of store.exposure_series || []) {
      const current = points.get(row.business_date) || {
        date: row.business_date,
        clicks: 0,
        pay: 0,
      };
      current.clicks += Number(row.product_click_uv || 0);
      current.pay += Number(row.pay_uv || 0);
      points.set(row.business_date, current);
    }
  }
  return [...points.values()].slice(-14);
});
const chartMax = computed(() =>
  Math.max(1, ...chartSeries.value.map((row) => row.clicks)),
);
const pendingTaskCount = computed(
  () => tasks.value.filter((item) => item.status !== "done").length,
);
const doneTaskCount = computed(
  () => tasks.value.filter((item) => item.status === "done").length,
);
const currentStoreDetail = computed(() =>
  drawer.kind === "store" ? drawer.data?.store : null,
);
const currentProductDetail = computed(() =>
  drawer.kind === "product" ? drawer.data?.product : null,
);

function showFeedback(message, tone = "success") {
  feedback.message = message;
  feedback.tone = tone;
  window.setTimeout(() => {
    if (feedback.message === message) {
      feedback.message = "";
      feedback.tone = "";
    }
  }, 3600);
}

function formatNumber(value, digits = 0) {
  if (value === null || value === undefined || value === "") return "暂无数据";
  const number = Number(value);
  if (!Number.isFinite(number)) return "暂无数据";
  return number.toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatScore(value) {
  return value === null || value === undefined ? "暂无数据" : `${Number(value).toFixed(2)} / 5.00`;
}

function formatPercent(value) {
  return value === null || value === undefined ? "暂无数据" : `${Number(value).toFixed(2)}%`;
}

function formatMoney(value) {
  if (value === null || value === undefined) return "暂无数据";
  return `¥${(Number(value) / 100).toFixed(2)}`;
}

function compactJson(value) {
  if (value === null || value === undefined || value === "") return "暂无数据";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return "暂无数据";
  }
}

function isTaskOverdue(task) {
  if (!task.due_at || task.status === "done") return false;
  const due = new Date(String(task.due_at).replace(" ", "T"));
  return Number.isFinite(due.getTime()) && due < new Date();
}

function trendLabel(value) {
  if (value === null || value === undefined) return "暂无趋势";
  if (Number(value) > 0) return `↑ ${Number(value).toFixed(2)}`;
  if (Number(value) < 0) return `↓ ${Math.abs(Number(value)).toFixed(2)}`;
  return "持平";
}

function trendClass(value) {
  return Number(value) > 0 ? "up" : Number(value) < 0 ? "down" : "";
}

function statusLabel(row) {
  if (row.alert_level === "risk") return "高风险";
  if (row.alert_level === "watch") return "需观察";
  return "正常";
}

function statusTone(row) {
  if (row.alert_level === "risk") return "danger";
  if (row.alert_level === "watch") return "warn";
  return "ok";
}

function openModal(config) {
  modal.open = true;
  modal.title = config.title || "";
  modal.subtitle = config.subtitle || "";
  modal.body = config.body || "";
  modal.confirmLabel = config.confirmLabel || "";
  modal.cancelLabel = config.cancelLabel ?? "取消";
  modal.busy = false;
  modal.action = config.action || null;
}

function closeModal() {
  if (modal.busy) return;
  modal.open = false;
  modal.action = null;
}

async function confirmModal() {
  if (!modal.action || modal.busy) return;
  modal.busy = true;
  try {
    await modal.action();
    modal.open = false;
  } catch (cause) {
    showFeedback(cause instanceof Error ? cause.message : String(cause), "error");
  } finally {
    modal.busy = false;
  }
}

async function guardedCall(action, payload = {}) {
  try {
    return await call(action, payload);
  } catch (cause) {
    if (cause?.status === 403) {
      accessLost.value = true;
      clearOperationsAccess();
      drawer.open = false;
    }
    throw cause;
  }
}

async function loadDashboard({ quiet = false } = {}) {
  if (!quiet) loading.value = true;
  pageError.value = "";
  try {
    dashboard.value = await guardedCall("operations.dashboard", {
      search: filters.search,
      range: filters.range,
      status: filters.status,
    });
  } catch (cause) {
    pageError.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    loading.value = false;
  }
}

async function loadProducts() {
  productsLoading.value = true;
  try {
    productsPayload.value = await guardedCall("operations.products", {
      search: filters.productSearch,
      opportunity: filters.opportunity,
    });
  } finally {
    productsLoading.value = false;
  }
}

async function loadTasks() {
  const payload = await guardedCall("operations.tasks.list", {
    status: filters.taskStatus === "all" ? "" : filters.taskStatus,
    priority: filters.taskPriority,
    store_id: filters.taskStoreId || undefined,
    category: filters.taskCategory,
    search: filters.taskSearch,
  });
  tasks.value = payload.tasks || [];
}

async function loadRules() {
  rules.value = await guardedCall("operations.rules.get");
}

async function loadSectionData() {
  try {
    await loadDashboard({ quiet: section.value !== "overview" });
    if (section.value === "products") await loadProducts();
    if (section.value === "actions") {
      await loadTasks();
      await loadRules();
    }
  } catch (cause) {
    pageError.value = cause instanceof Error ? cause.message : String(cause);
  }
}

function switchSection(key) {
  section.value = key;
  router.replace(
    key === "overview" ? "/operations" : `/operations/${key}`,
  );
  window.scrollTo({ top: 0, behavior: "smooth" });
  loadSectionData();
}

async function refreshCurrent() {
  try {
    await loadSectionData();
    showFeedback("已刷新本地真实数据");
  } catch (cause) {
    showFeedback(cause instanceof Error ? cause.message : String(cause), "error");
  }
}

async function applyFilters() {
  try {
    if (section.value === "products") {
      await loadProducts();
    } else if (section.value === "actions") {
      await loadTasks();
    } else {
      await loadDashboard();
    }
  } catch (cause) {
    showFeedback(cause instanceof Error ? cause.message : String(cause), "error");
  }
}

async function openStoreDetail(storeId) {
  drawer.open = true;
  drawer.kind = "store";
  drawer.loading = true;
  drawer.data = null;
  drawer.error = "";
  try {
    drawer.data = await guardedCall("operations.store-detail", {
      store_id: storeId,
      range: filters.range,
    });
    const calculator = drawer.data.calculators?.quality_refund;
    if (calculator) {
      calculatorData.refundOrders = calculator.denominator || "";
      calculatorData.refundCount = calculator.current_count || "";
    }
  } catch (cause) {
    drawer.error = cause instanceof Error ? cause.message : String(cause);
  } finally {
    drawer.loading = false;
  }
}

async function openProductDetail(row) {
  drawer.open = true;
  drawer.kind = "product";
  drawer.loading = true;
  drawer.data = null;
  drawer.error = "";
  drawer.showAllProductAfterSales = false;
  try {
    drawer.data = await guardedCall("operations.product-detail", {
      store_id: row.store_id,
      product_id: row.product_id,
      range: filters.range,
    });
  } catch (cause) {
    drawer.error = cause instanceof Error ? cause.message : String(cause);
  } finally {
    drawer.loading = false;
  }
}

function closeDrawer() {
  drawer.open = false;
  drawer.data = null;
  drawer.error = "";
}

function showSources() {
  const sources = dashboard.value?.data_sources || [];
  openModal({
    title: "数据可信度与更新时间",
    subtitle: "缺失字段不填充 0，官方 T+1 不标记为实时",
    body: "",
    confirmLabel: "关闭",
    action: async () => {
      modal.open = false;
    },
  });
  modal.body = [
    '<div class="ops-source-list">',
    ...sources.map(
      (source) =>
        `<div class="ops-source-row"><strong>${escapeHtml(source.label)}</strong>` +
        `<span>${escapeHtml((source.fields || []).join("、"))}</span>` +
        `<small>更新时间：${escapeHtml(source.updated_at || "暂无数据")}；` +
        `最后成功：${escapeHtml(source.last_success_at || "暂无数据")}` +
        `${source.error ? `；${escapeHtml(source.error)}` : ""}</small></div>`,
    ),
    "</div>",
  ].join("");
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function openBackend({ storeId, target, productId = "", label = "" }) {
  if (!permission.value.operations_open_backend) {
    showFeedback("当前账号没有打开运营后台的权限", "error");
    return;
  }
  openModal({
    title: "打开微信官方后台",
    subtitle: "使用现有店铺登录会话并校验店铺 AppID",
    body:
      `<div class="ops-confirm-target"><strong>${escapeHtml(label || storeId)}</strong>` +
      `<span>目标：${escapeHtml(target)}${productId ? ` · 商品 ${escapeHtml(productId)}` : ""}</span></div>` +
      (productId
        ? '<p class="ops-modal-note">商品页面会先打开商品列表，按商品 ID 精确检索并确认唯一结果；找不到时停留在列表，不会误开其他商品。</p>'
        : '<p class="ops-modal-note">打开前后校验目标店铺 AppID，避免多店会话串号。</p>'),
    confirmLabel: "打开官方页面",
    action: async () => {
      const result = await guardedCall("operations.open-backend", {
        store_id: storeId,
        target,
        product_id: productId,
      });
      showFeedback(`已发起打开官方后台：${result.target}`);
    },
  });
}

async function runInspect() {
  try {
    const result = await guardedCall("operations.inspect", {
      search: filters.search,
      range: filters.range,
    });
    if (!result.job_id) {
      await loadDashboard({ quiet: true });
      showFeedback(
        result.refresh_error ||
          `本地巡检完成，发现 ${result.alert_count} 项带证据告警`,
        result.refresh_error ? "error" : "success",
      );
      return;
    }
    inspection.active = true;
    inspection.current = 0;
    inspection.total = 0;
    inspection.failed = 0;
    inspection.message = result.message;
    while (inspection.active) {
      const job = await guardedCall("operations.inspect.status", {
        job_id: result.job_id,
      });
      inspection.current = Number(job.current || 0);
      inspection.total = Number(job.total || 0);
      inspection.failed = Number(job.failed || 0);
      inspection.message = job.message || "正在巡检店铺";
      if (["completed", "partial", "failed"].includes(job.status)) {
        inspection.active = false;
        await loadDashboard({ quiet: true });
        if (section.value === "actions") await loadTasks();
        showFeedback(
          `${job.message}；发现 ${result.alert_count} 项带证据告警`,
          job.failed ? "error" : "success",
        );
        break;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 1200));
    }
  } catch (cause) {
    inspection.active = false;
    showFeedback(cause instanceof Error ? cause.message : String(cause), "error");
  }
}

function downloadText(content, filename) {
  const blob = new Blob([content], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

async function exportData(scope) {
  if (!permission.value.operations_export) {
    showFeedback("当前账号没有导出运营数据的权限", "error");
    return;
  }
  try {
    const payload = await guardedCall("operations.export", {
      scope,
      search: filters.search,
      range: filters.range,
      status:
        scope === "tasks"
          ? filters.taskStatus === "all"
            ? ""
            : filters.taskStatus
          : filters.status === "all"
            ? ""
            : filters.status,
      store_id: scope === "tasks" ? filters.taskStoreId || undefined : undefined,
      category: scope === "tasks" ? filters.taskCategory : "",
      priority: scope === "tasks" ? filters.taskPriority : "",
    });
    downloadText(payload.content, payload.filename);
    showFeedback(`已导出 ${payload.count} 条真实记录`);
  } catch (cause) {
    showFeedback(cause instanceof Error ? cause.message : String(cause), "error");
  }
}

function openTaskCreate(options = {}) {
  if (!permission.value.operations_task_manage) {
    showFeedback("当前账号没有管理行动任务的权限", "error");
    return;
  }
  const alert = options.alert || {};
  form.taskStoreId = alert.store_id || options.storeId || "";
  form.taskProductId = alert.product_id || options.productId || "";
  form.taskTitle = alert.title || options.title || "";
  form.taskDetail = alert.suggested_action || options.detail || "";
  form.taskPriority = alert.priority || options.priority || "P1";
  form.taskCategory = alert.category || options.category || "运营动作";
  form.taskAlertKey = alert.alert_key || options.alertKey || "";
  form.taskEvidence = { ...alert };
  form.taskDueAt = "";
  openModal({
    title: "新建运营行动任务",
    subtitle: "任务会写入本机 SQLite，不会只改变前端状态",
    body: "",
    confirmLabel: "创建任务",
    action: submitTask,
  });
}

async function submitTask() {
  if (!form.taskTitle.trim()) throw new Error("请输入任务标题");
  await guardedCall("operations.tasks.create", {
    store_id: form.taskStoreId || null,
    product_id: form.taskProductId,
    title: form.taskTitle,
    detail: form.taskDetail,
    priority: form.taskPriority,
    due_at: form.taskDueAt || null,
    category: form.taskCategory,
    alert_key: form.taskAlertKey || undefined,
    evidence_json: form.taskEvidence,
  });
  await loadTasks();
  await loadDashboard({ quiet: true });
  showFeedback("行动任务已写入 SQLite");
}

async function completeTask(task, completed) {
  if (!permission.value.operations_task_manage) {
    showFeedback("当前账号没有管理行动任务的权限", "error");
    return;
  }
  try {
    await guardedCall("operations.tasks.update", {
      task_id: task.id,
      status: completed ? "done" : "open",
    });
    await loadTasks();
    showFeedback(completed ? "任务已完成并持久化" : "任务已恢复");
  } catch (cause) {
    showFeedback(cause instanceof Error ? cause.message : String(cause), "error");
  }
}

function delayTask(task) {
  if (!permission.value.operations_task_manage) {
    showFeedback("当前账号没有管理行动任务的权限", "error");
    return;
  }
  openModal({
    title: "延期行动任务",
    subtitle: task.title,
    body: "",
    confirmLabel: "保存延期",
    action: async () => {
      const dueAt = document.querySelector("#ops-task-delay")?.value || "";
      if (!dueAt) throw new Error("请选择新的到期时间");
      await guardedCall("operations.tasks.update", {
        task_id: task.id,
        status: "delayed",
        due_at: `${dueAt}T23:59:59`,
      });
      await loadTasks();
      showFeedback("任务到期时间已保存");
    },
  });
  modal.body =
    '<div class="ops-field-grid"><label>新的到期时间<input class="ops-input" id="ops-task-delay" type="date" /></label></div>';
}

function showRules() {
  if (!rules.value) return;
  openModal({
    title: "巡检规则设置",
    subtitle: "阈值只用于本地运营提醒，不代表平台处罚或体验分算法",
    body: "",
    confirmLabel: permission.value.operations_rule_manage ? "保存并生效" : "",
    cancelLabel: "关闭",
    action: permission.value.operations_rule_manage ? saveRules : null,
  });
  modal.body = `<div class="ops-field-grid">${rulesFields()}</div>`;
}

function rulesFields() {
  const values = rules.value?.rules || {};
  return [
    ["score_drop_threshold", "体验分单日下降", "0.01"],
    ["zero_exposure_hours", "连续零曝光小时", "1"],
    ["zero_sales_days", "连续零销量天数", "1"],
    ["no_new_product_days", "连续未观察到上新天数", "1"],
    ["first_response_seconds", "客服平均首响上限（秒）", "1"],
    ["no_reply_rate", "未回复率上限（%）", "0.1"],
    ["quality_refund_watch_rate", "质量退款率观察阈值（%）", "0.1"],
    ["high_views_threshold", "高浏览商品阈值", "1"],
    ["low_pay_threshold", "低支付人数阈值", "1"],
  ]
    .map(
      ([key, label, step]) =>
        `<label>${escapeHtml(label)}<input class="ops-input" id="ops-rule-${escapeHtml(key)}" type="number" min="0" step="${step}" value="${escapeHtml(values[key] ?? "")}" /></label>`,
    )
    .join("");
}

async function saveRules() {
  if (!permission.value.operations_rule_manage) {
    throw new Error("当前账号没有修改巡检规则的权限");
  }
  const values = {};
  for (const key of Object.keys(rules.value?.defaults || {})) {
    values[key] =
      document.querySelector(`#ops-rule-${key}`)?.value || "";
  }
  rules.value = await guardedCall("operations.rules.save", values);
  await loadDashboard({ quiet: true });
  showFeedback("巡检规则已写入 SQLite");
}

function openProductTask() {
  if (!currentProductDetail.value) return;
  openTaskCreate({
    storeId: currentProductDetail.value.store_id,
    productId: currentProductDetail.value.product_id,
    title: `诊断商品：${currentProductDetail.value.title}`,
    detail:
      `今日有效支付订单 ${currentProductDetail.value.orders_today ?? "暂无数据"} 单，` +
      `最近连续出单 ${currentProductDetail.value.consecutive_order_days ?? "暂无数据"} 天，` +
      `当前库存 ${currentProductDetail.value.stock ?? "暂无数据"}；` +
      "先核对库存、采购利润、退款/品质与履约能力，再做真实内容曝光或合规投流。",
    category: "商品机会",
  });
}

function createTaskFromPlaybook() {
  const selected = currentStoreDetail.value || visibleStores.value[0];
  openTaskCreate({
    storeId: selected?.store_id,
    title: `执行${activePlaybook.value.title}诊断`,
    detail: `${activePlaybook.value.lead} ${activePlaybook.value.steps.map((step) => step[1]).join("；")}`,
    category: "运营打法",
  });
}

watch(
  () => route.params.section,
  (value) => {
    const next = String(value || "overview");
    if (next !== section.value) {
      section.value = next;
      loadSectionData();
    }
  },
);

onMounted(loadSectionData);
</script>

<template>
  <section class="ops-page">
    <div v-if="accessLost" class="ops-state-card danger">
      <Icon name="alert" :size="24" />
      <div>
        <h1>运营中心访问权限已关闭</h1>
        <p>请联系主账号重新授权。当前页面已停止加载，也没有继续保留运营摘要。</p>
        <button class="ops-primary" type="button" @click="router.replace('/shops')">返回店铺管理</button>
      </div>
    </div>

    <template v-else>
      <header class="ops-topbar">
        <div>
          <div class="ops-title-row">
            <h1>{{ pageMeta[section]?.[0] || "运营中心" }}</h1>
            <span v-if="dashboard" class="ops-freshness">
              <i></i>{{ dashboard.generated_at }} · 本地真实数据
            </span>
          </div>
          <p>{{ pageMeta[section]?.[1] }}</p>
        </div>
        <div class="ops-toolbar">
          <label class="ops-search">
            <Icon name="search" :size="15" />
            <input
              v-model="filters.search"
              type="search"
              placeholder="搜索店铺名称，多个名称用空格分隔"
              @keyup.enter="applyFilters"
            />
          </label>
          <select v-model="filters.range" class="ops-select" @change="applyFilters">
            <option value="today">今日</option>
            <option value="7d">近 7 日</option>
            <option value="30d">近 30 日</option>
          </select>
          <button class="ops-icon-button" type="button" title="刷新本地真实数据" @click="refreshCurrent">
            <Icon name="refresh" :size="17" :class="{ spinning: loading }" />
          </button>
        </div>
      </header>

      <nav class="ops-subnav" aria-label="运营中心页面">
        <button
          v-for="[key, label, icon] in navItems"
          :key="key"
          type="button"
          :class="{ active: section === key }"
          @click="switchSection(key)"
        >
          <Icon :name="icon" :size="16" />
          <span>{{ label }}</span>
          <b v-if="key === 'overview' && activeAlerts.length">{{ activeAlerts.length }} 项重点</b>
          <b v-else-if="key === 'actions' && pendingTaskCount">{{ pendingTaskCount }}</b>
        </button>
      </nav>

      <div class="ops-source-legend">
        <button type="button" @click="showSources"><i class="realtime"></i><strong>官方后台实时</strong>体验分、店铺近况、曝光</button>
        <button type="button" @click="showSources"><i class="api"></i><strong>官方 API T+1</strong>成交、点击、退款、商品</button>
        <button type="button" @click="showSources"><i class="sqlite"></i><strong>本地 SQLite</strong>订单、售后、任务、规则</button>
        <button type="button" @click="showSources"><i class="calc"></i><strong>本地计算</strong>趋势、阈值、干预测算</button>
        <button type="button" @click="showSources">查看数据可信度</button>
      </div>

      <div v-if="feedback.message" class="ops-toast" :class="feedback.tone">
        {{ feedback.message }}
      </div>
      <div v-if="inspection.active" class="ops-inspection">
        <div><span class="ops-loading-dot"></span><strong>{{ inspection.message }}</strong></div>
        <div class="ops-progress"><i :style="{ width: `${inspection.total ? inspection.current / inspection.total * 100 : 3}%` }"></i></div>
        <small>{{ inspection.current }} / {{ inspection.total || "准备中" }} · 失败 {{ inspection.failed }}</small>
      </div>

      <div v-if="loading" class="ops-state-card">
        <span class="ops-loading-dot"></span>
        <div><h2>正在读取真实运营数据</h2><p>不会用示例值填充缺失指标。</p></div>
      </div>
      <div v-else-if="pageError" class="ops-state-card danger">
        <Icon name="alert" :size="22" />
        <div>
          <h2>运营数据加载失败</h2>
          <p>{{ pageError }}</p>
          <button class="ops-secondary" type="button" @click="refreshCurrent">重新加载</button>
        </div>
      </div>

      <template v-else>
        <div v-if="section === 'overview'" class="ops-panel">
          <div class="ops-kpis">
            <article>
              <span>今日推荐曝光</span>
              <strong>{{ formatNumber(dashboard?.summary?.exposure_today) }}</strong>
              <small>官方后台同步</small>
            </article>
            <article>
              <span>店铺体验分均值</span>
              <strong>{{ formatScore(dashboard?.summary?.avg_dsr) }}</strong>
              <small>{{ dashboard?.summary?.stores_total ?? 0 }} 家店铺 · 5 分制</small>
            </article>
            <article>
              <span>近 30 日支付转化</span>
              <strong>{{ formatPercent(dashboard?.summary?.conversion_30d) }}</strong>
              <small>支付 {{ formatNumber(dashboard?.summary?.pay_uv_30d) }} / 点击 {{ formatNumber(dashboard?.summary?.click_uv_30d) }} · T+1</small>
            </article>
            <article>
              <span>待处理运营动作</span>
              <strong>{{ formatNumber(dashboard?.summary?.open_tasks) }}</strong>
              <small>{{ dashboard?.summary?.due_tasks || 0 }} 项已到期</small>
            </article>
          </div>

          <section class="ops-card">
            <div class="ops-card-head">
              <div>
                <h2>今日优先处理</h2>
                <p>告警必须包含真实证据、时间和建议动作，不把“疑似”写成平台处罚。</p>
              </div>
              <button class="ops-secondary" type="button" @click="runInspect">
                <Icon name="radar" :size="15" />立即巡检
              </button>
            </div>
            <div v-if="!activeAlerts.length" class="ops-empty">当前没有达到本地规则阈值的告警。</div>
            <div v-else class="ops-alert-grid">
              <article
                v-for="alert in activeAlerts.slice(0, 8)"
                :key="alert.alert_key"
                class="ops-alert-item"
                :class="alert.priority.toLowerCase()"
              >
                <button type="button" class="ops-alert-open" @click="alert.product_id ? openProductDetail(alert) : openStoreDetail(alert.store_id)">
                  <span>{{ alert.priority }}</span>
                  <strong>{{ alert.title }}</strong>
                  <small>{{ alert.store_name }} · {{ alert.event_at }}</small>
                  <em>{{ alert.evidence.join(" · ") }} · 置信度 {{ alert.confidence }}</em>
                  <em>{{ alert.suggested_action }}</em>
                </button>
                <div class="ops-alert-actions">
                  <button
                    type="button"
                    :disabled="!permission.operations_task_manage"
                    @click="openTaskCreate({ alert })"
                  >创建任务</button>
                  <button
                    type="button"
                    :disabled="!permission.operations_open_backend"
                    @click="openBackend({ storeId: alert.store_id, target: alert.backend_target, productId: alert.product_id, label: alert.product_title || alert.store_name })"
                  >打开后台</button>
                </div>
              </article>
            </div>
          </section>

          <div class="ops-grid-main">
            <section class="ops-card">
              <div class="ops-card-head">
                <div>
                  <h2>流量与成交趋势</h2>
                  <p>仅展示已缓存官方 T+1 日数据；分时曝光缺失时不生成曲线。</p>
                </div>
              </div>
              <div v-if="!chartSeries.length" class="ops-empty">暂无官方 T+1 日分析数据。</div>
              <div v-else class="ops-chart">
                <div v-for="row in chartSeries" :key="row.date" class="ops-chart-col">
                  <div class="ops-chart-bars">
                    <i :style="{ height: `${Math.max(2, row.clicks / chartMax * 100)}%` }"></i>
                  </div>
                  <span>{{ row.date.slice(5) }}</span>
                  <small>点击 {{ formatNumber(row.clicks) }}</small>
                </div>
              </div>
            </section>
            <section class="ops-card">
              <div class="ops-card-head">
                <div><h2>缺口与边界</h2><p>当前数据源没有可靠性证明的字段会明确留空。</p></div>
              </div>
              <ul class="ops-gap-list">
                <li><strong>分时曝光</strong><span>当前缓存只有店铺级推荐曝光总量，暂无分时可信数据。</span></li>
                <li><strong>访客与加购</strong><span>官方公开 T+1 接口未提供时显示“暂无数据”，不从销量反推。</span></li>
                <li><strong>客服首响与未回复率</strong><span>需要客服消息事件；未接入时计算器只做人工情景推演。</span></li>
                <li><strong>体验分预测</strong><span>平台未公开映射公式，系统不输出“加 N 单上涨 X 分”。</span></li>
              </ul>
            </section>
          </div>

          <section class="ops-card">
            <div class="ops-card-head">
              <div><h2>店铺健康</h2><p>点击诊断查看真实分项、售后原因、趋势和可执行动作。</p></div>
              <div class="ops-segment">
                <button v-for="item in [['all','全部'],['risk','风险'],['watch','观察']]" :key="item[0]" type="button" :class="{ active: filters.status === item[0] }" @click="filters.status = item[0]; applyFilters()">{{ item[1] }}</button>
              </div>
            </div>
            <div class="ops-table-wrap">
              <table class="ops-table">
                <thead>
                  <tr><th>店铺</th><th>状态</th><th>体验分</th><th>今日曝光</th><th>30日访客</th><th>支付转化</th><th>今日订单</th><th>未完成售后</th><th>查看</th></tr>
                </thead>
                <tbody>
                  <tr v-for="row in visibleStores" :key="row.store_id">
                    <td><strong>{{ row.store_name }}</strong><small>{{ row.group_name || "未分组" }} · {{ row.product_total ?? 0 }} 件商品</small></td>
                    <td><span class="ops-status" :class="statusTone(row)">{{ statusLabel(row) }}</span></td>
                    <td><b class="ops-score">{{ formatScore(row.scores.dsr) }}</b><small :class="trendClass(row.score_trends.dsr)">{{ trendLabel(row.score_trends.dsr) }}</small></td>
                    <td>{{ formatNumber(row.exposure_today) }}</td>
                    <td>{{ formatNumber(row.visitors_30d) }}</td>
                    <td>{{ formatPercent(row.conversion_30d) }}</td>
                    <td>{{ formatNumber(row.orders_today) }}</td>
                    <td>{{ formatNumber(row.aftersale_open) }}</td>
                    <td class="ops-row-actions">
                      <button type="button" @click="openStoreDetail(row.store_id)">诊断</button>
                      <button type="button" :disabled="!permission.operations_open_backend" @click="openBackend({ storeId: row.store_id, target: 'score', label: row.store_name })">体验分</button>
                    </td>
                  </tr>
                  <tr v-if="!visibleStores.length"><td colspan="9" class="ops-empty-cell">暂无符合筛选条件的店铺。</td></tr>
                </tbody>
              </table>
            </div>
          </section>
        </div>

        <div v-else-if="section === 'stores'" class="ops-panel">
          <div class="ops-toolbar-left">
            <button class="ops-secondary" type="button" :disabled="!permission.operations_export" @click="exportData('stores')"><Icon name="download" :size="15" />导出诊断</button>
            <button class="ops-primary" type="button" @click="runInspect"><Icon name="radar" :size="15" />立即巡检</button>
          </div>
          <div class="ops-health-grid" v-if="visibleStores.length">
            <section v-for="row in visibleStores.slice(0, 3)" :key="row.store_id" class="ops-card">
              <div class="ops-card-head">
                <div><h2>{{ row.store_name }}</h2><p>官方 5 分制 · {{ row.stats_at || "更新时间暂无数据" }}</p></div>
                <span class="ops-status" :class="statusTone(row)">{{ statusLabel(row) }}</span>
              </div>
              <div class="ops-dimensions">
                <div><span>商品体验</span><strong>{{ formatScore(row.scores.score_product) }}</strong><i :style="{ width: `${(row.scores.score_product || 0) / 5 * 100}%` }"></i></div>
                <div><span>物流体验</span><strong>{{ formatScore(row.scores.score_deliver) }}</strong><i :style="{ width: `${(row.scores.score_deliver || 0) / 5 * 100}%` }"></i></div>
                <div><span>服务体验</span><strong>{{ formatScore(row.scores.score_service) }}</strong><i :style="{ width: `${(row.scores.score_service || 0) / 5 * 100}%` }"></i></div>
              </div>
              <p class="ops-rule-note">平台没有公开原始指标到维度分的精确映射，此处只展示已同步分数和公式指标。</p>
              <button type="button" class="ops-row-link" @click="openStoreDetail(row.store_id)">打开完整体检</button>
            </section>
          </div>
          <section class="ops-card">
            <div class="ops-card-head">
              <div><h2>店铺健康矩阵</h2><p>质量退款率与售后单来自官方售后缓存和本地公式。</p></div>
              <span class="ops-muted">最近更新时间会逐行显示</span>
            </div>
            <div class="ops-table-wrap">
              <table class="ops-table">
                <thead><tr><th>店铺</th><th>体验分</th><th>商品</th><th>物流</th><th>服务</th><th>昨日 → 今日曝光</th><th>质量退款率</th><th>连续零销量</th><th>连续未观察上新</th><th>诊断</th></tr></thead>
                <tbody>
                  <tr v-for="row in visibleStores" :key="row.store_id">
                    <td><strong>{{ row.store_name }}</strong><small>{{ row.web_status }} · {{ row.stats_at || "暂无更新时间" }}</small></td>
                    <td>{{ formatScore(row.scores.dsr) }}</td>
                    <td>{{ formatScore(row.scores.score_product) }}</td>
                    <td>{{ formatScore(row.scores.score_deliver) }}</td>
                    <td>{{ formatScore(row.scores.score_service) }}</td>
                    <td>{{ formatNumber(row.exposure_yesterday) }} → {{ formatNumber(row.exposure_today) }}</td>
                    <td>{{ formatPercent(row.quality_refund_ratio) }}</td>
                    <td>{{ row.zero_sales_days == null ? "暂无数据" : `${row.zero_sales_days} 天` }}</td>
                    <td>{{ row.no_new_product_days == null ? "暂无数据" : `${row.no_new_product_days} 天` }}</td>
                    <td class="ops-row-actions"><button type="button" @click="openStoreDetail(row.store_id)">详情</button><button type="button" :disabled="!permission.operations_open_backend" @click="openBackend({ storeId: row.store_id, target: 'service', label: row.store_name })">客服</button></td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
        </div>

        <div v-else-if="section === 'products'" class="ops-panel">
          <div class="ops-kpis product-kpis">
            <article><span>潜力商品</span><strong>{{ formatNumber(dashboard?.product_summary?.potential) }}</strong><small>真实指标触发</small></article>
            <article><span>畅销/有成交</span><strong>{{ formatNumber(dashboard?.product_summary?.hot) }}</strong><small>近 30 日支付</small></article>
            <article><span>销售升温</span><strong>{{ formatNumber(dashboard?.product_summary?.sales_momentum) }}</strong><small>今日 ≥2 单或连续 ≥2 天</small></article>
            <article><span>售后关注</span><strong>{{ formatNumber(dashboard?.product_summary?.risk) }}</strong><small>关联售后记录</small></article>
            <article><span>指标缺失</span><strong>{{ formatNumber(dashboard?.product_summary?.missing_metrics) }}</strong><small>不按 0 处理</small></article>
          </div>
          <section class="ops-card">
            <div class="ops-products-toolbar">
              <div class="ops-filter-chips">
                <button v-for="item in [['','全部机会'],['sales_momentum','销售升温'],['potential','高浏览低成交'],['hot','有真实成交'],['risk','售后关注']]" :key="item[0]" type="button" :class="{ active: filters.opportunity === item[0] }" @click="filters.opportunity = item[0]; applyFilters()">{{ item[1] }}</button>
              </div>
              <label class="ops-search">
                <Icon name="search" :size="15" />
                <input v-model="filters.productSearch" type="search" placeholder="商品标题、ID 或店铺" @keyup.enter="applyFilters" />
              </label>
              <button class="ops-secondary" type="button" :disabled="!permission.operations_export" @click="exportData('products')">导出商品榜</button>
            </div>
            <div class="ops-table-wrap tall">
              <table class="ops-table">
                <thead><tr><th>商品</th><th>状态</th><th>价格</th><th>库存</th><th>销量</th><th>今日订单</th><th>连续出单</th><th>30日浏览</th><th>点击人数</th><th>支付人数</th><th>点击率</th><th>支付转化</th><th>售后单</th><th>机会 / 风险</th><th>分析</th></tr></thead>
                <tbody>
                  <tr v-if="productsLoading"><td colspan="15" class="ops-empty-cell"><span class="ops-loading-dot inline"></span>正在读取真实商品缓存…</td></tr>
                  <tr v-for="row in visibleProducts" :key="`${row.store_id}:${row.product_id}`">
                    <td class="ops-product-cell"><img v-if="row.head_img" :src="row.head_img" alt="" /><div><strong>{{ row.title }}</strong><small>{{ row.store_name }} · {{ row.product_id }}</small></div></td>
                    <td>{{ row.status_text || "暂无数据" }}</td>
                    <td>{{ formatMoney(row.price) }}</td>
                    <td>{{ formatNumber(row.stock) }}</td>
                    <td>{{ formatNumber(row.sales) }}</td>
                    <td>{{ formatNumber(row.orders_today) }}</td>
                    <td>{{ formatNumber(row.consecutive_order_days) }} 天</td>
                    <td>{{ formatNumber(row.metrics.views30) }}</td>
                    <td>{{ formatNumber(row.metrics.click_uv) }}</td>
                    <td>{{ formatNumber(row.metrics.pay_uv) }}</td>
                    <td>{{ formatPercent(row.click_rate) }}</td>
                    <td>{{ formatPercent(row.pay_conversion) }}</td>
                    <td>{{ formatNumber(row.after_sale_count) }}</td>
                    <td><span v-for="tag in row.tags" :key="tag" class="ops-tag">{{ tag }}</span></td>
                    <td class="ops-row-actions"><button type="button" @click="openProductDetail(row)">查看漏斗</button><button type="button" :disabled="!permission.operations_open_backend" @click="openBackend({ storeId: row.store_id, target: 'product', productId: row.product_id, label: row.title })">商品</button></td>
                  </tr>
                  <tr v-if="!productsLoading && !visibleProducts.length"><td colspan="15" class="ops-empty-cell">当前没有符合筛选且带真实商品缓存的记录。</td></tr>
                </tbody>
              </table>
            </div>
          </section>
        </div>

        <div v-else-if="section === 'playbooks'" class="ops-panel">
          <div class="ops-grid-main playbook-grid">
            <section class="ops-card">
              <div class="ops-card-head"><div><h2>问题诊断与处置顺序</h2><p>七类固定诊断：0–30 分、24 小时、7 天和成功指标。</p></div></div>
              <div class="ops-playbook-tabs">
                <button v-for="item in playbooks" :key="item.key" type="button" :class="{ active: playbook === item.key }" @click="playbook = item.key">{{ item.title }}</button>
              </div>
              <div class="ops-playbook-body">
                <p class="ops-lead">{{ activePlaybook.lead }}</p>
                <article v-for="(step, index) in activePlaybook.steps" :key="index">
                  <b>{{ step[0] }}</b>
                  <div><strong>{{ step[1] }}</strong><small>成功指标：{{ step[2] }}</small></div>
                </article>
                <button class="ops-primary" type="button" @click="createTaskFromPlaybook">从本打法创建任务</button>
              </div>
            </section>
            <section class="ops-card">
              <div class="ops-card-head"><div><h2>干预测算器</h2><p>公式可修改，输入来源明确；结果不代表体验分预测。</p></div></div>
              <div class="ops-playbook-tabs">
                <button v-for="[key, label] in calculatorTabs" :key="key" type="button" :class="{ active: calculator === key }" @click="calculator = key">{{ label }}</button>
              </div>
              <div class="ops-calculator">
                <template v-if="calculator === 'review'">
                  <div class="ops-field-grid">
                    <label>现有有效评价<input v-model="calculatorData.reviewTotal" class="ops-input" type="number" min="0" placeholder="暂无数据" /></label>
                    <label>现有差评<input v-model="calculatorData.reviewNegative" class="ops-input" type="number" min="0" placeholder="暂无数据" /></label>
                    <label>目标差评率 %<input v-model="calculatorData.reviewTarget" class="ops-input" type="number" min="0.1" /></label>
                    <label>订单→有效评价 %<input v-model="calculatorData.reviewRate" class="ops-input" type="number" min="0" /></label>
                    <label>新增差评率 %<input v-model="calculatorData.reviewNewNegative" class="ops-input" type="number" min="0" /></label>
                    <label>拟新增真实订单<input v-model="calculatorData.reviewOrders" class="ops-input" type="number" min="0" /></label>
                  </div>
                  <p class="ops-formula">{{ reviewScenario.formula }}。订单不等于评价，只有实际产生的有效主动评价才进入公式。</p>
                  <div class="ops-calc-answer"><span>达到目标至少需要新增订单</span><strong>{{ reviewScenario.required === null ? "无法按当前假设达成" : `${reviewScenario.required} 单` }}</strong></div>
                  <p v-if="reviewScenario.warning" class="ops-warning">{{ reviewScenario.warning }}</p>
                </template>
                <template v-else-if="calculator === 'refund'">
                  <div class="ops-field-grid">
                    <label>已发货有效订单<input v-model="calculatorData.refundOrders" class="ops-input" type="number" min="0" placeholder="暂无数据" /></label>
                    <label>品质退款单<input v-model="calculatorData.refundCount" class="ops-input" type="number" min="0" placeholder="暂无数据" /></label>
                    <label>目标品质退款率 %<input v-model="calculatorData.refundTarget" class="ops-input" type="number" min="0.1" /></label>
                    <label>新增订单品质退款率 %<input v-model="calculatorData.refundNewRate" class="ops-input" type="number" min="0" /></label>
                    <label>拟新增已发货订单<input v-model="calculatorData.refundNewOrders" class="ops-input" type="number" min="0" /></label>
                  </div>
                  <p class="ops-formula">{{ refundScenario.formula }}</p>
                  <div class="ops-calc-answer"><span>达到目标至少需要新增已发货订单</span><strong>{{ refundScenario.required === null ? "无法按当前假设达成" : `${refundScenario.required} 单` }}</strong></div>
                  <p v-if="refundScenario.warning" class="ops-warning">{{ refundScenario.warning }}</p>
                </template>
                <template v-else-if="calculator === 'traffic'">
                  <div class="ops-field-grid">
                    <label>目标新增订单<input v-model="calculatorData.trafficOrders" class="ops-input" type="number" min="1" /></label>
                    <label>商品点击率 %<input v-model="calculatorData.trafficClick" class="ops-input" type="number" min="0.1" placeholder="请输入真实或假设值" /></label>
                    <label>访客支付转化 %<input v-model="calculatorData.trafficPay" class="ops-input" type="number" min="0.01" placeholder="请输入真实或假设值" /></label>
                  </div>
                  <p class="ops-formula">{{ trafficScenario.formula }}</p>
                  <div class="ops-calc-answer"><span>需要新增曝光 / 商品访客</span><strong>{{ trafficScenario.exposures }} / {{ trafficScenario.visits }}</strong></div>
                </template>
                <template v-else>
                  <div class="ops-field-grid">
                    <label>有效咨询轮次<input v-model="calculatorData.serviceTotal" class="ops-input" type="number" min="0" placeholder="暂无客服事件数据" /></label>
                    <label>未回复轮次<input v-model="calculatorData.serviceMissed" class="ops-input" type="number" min="0" placeholder="暂无客服事件数据" /></label>
                    <label>目标未回复率 %<input v-model="calculatorData.serviceTarget" class="ops-input" type="number" min="0.1" max="99.9" /></label>
                  </div>
                  <p class="ops-formula">{{ serviceScenario.formula }}。现有未回复轮次保持不变，只新增已经有效回复的新咨询轮次。</p>
                  <div class="ops-calc-answer"><span>还需新增且已回复的咨询轮次</span><strong>{{ serviceScenario.invalid ? "输入无效" : `${serviceScenario.required} 轮` }}</strong></div>
                  <p v-if="serviceScenario.warning" class="ops-warning">{{ serviceScenario.warning }}</p>
                  <table class="ops-scenario-table">
                    <thead><tr><th>新增且已回复咨询</th><th>新有效咨询总量</th><th>新不回复率</th></tr></thead>
                    <tbody>
                      <tr v-for="row in serviceScenario.rows" :key="row.newReplies">
                        <td>{{ row.newReplies }} 轮</td>
                        <td>{{ formatNumber(row.newTotal) }}</td>
                        <td>{{ formatPercent(row.rate) }}</td>
                      </tr>
                    </tbody>
                  </table>
                </template>
              </div>
            </section>
          </div>
          <section class="ops-card">
            <div class="ops-card-head"><div><h2>问题 → 动作矩阵</h2><p>运营执行顺序，不是平台官方法规原文。</p></div></div>
            <div class="ops-table-wrap">
              <table class="ops-table matrix">
                <thead><tr><th>问题</th><th>第一判断</th><th>24 小时动作</th><th>7 天动作</th><th>成功指标</th></tr></thead>
                <tbody>
                  <tr><td>全店曝光下降</td><td>状态、违规、售罄、价格库存、渠道</td><td>止损并保存快照，一次只改一个变量</td><td>真实内容承接、合规投流和上新测试</td><td>曝光恢复且转化不降</td></tr>
                  <tr><td>有曝光点击低</td><td>曝光人群与渠道差异</td><td>单变量测试主图或首屏卖点</td><td>测试价格表达与真实信任信息</td><td>点击率提升且加购不降</td></tr>
                  <tr><td>有点击转化低</td><td>详情、SKU、到手价、运费、评价</td><td>补齐规格、场景图和售后信息</td><td>修正价格结构与商品承诺</td><td>支付转化提升且退款不升</td></tr>
                  <tr><td>差评与品退</td><td>商品、SKU、原因、批次聚类</td><td>处理售后并暂停高风险 SKU</td><td>改图改描述、换包装、优化供应</td><td>新增问题下降</td></tr>
                  <tr><td>客服超时</td><td>可回复会话、时段热力、高频问题</td><td>处理售后和高客单超时会话</td><td>快捷回复与排班</td><td>首响 ≤90 秒、不回复率 ≤10%</td></tr>
                </tbody>
              </table>
            </div>
          </section>
        </div>

        <div v-else-if="section === 'actions'" class="ops-panel">
          <div class="ops-toolbar-left">
            <select v-model="filters.taskStatus" class="ops-select" @change="applyFilters">
              <option value="all">全部任务</option><option value="open">未处理</option><option value="done">已完成</option><option value="delayed">已延期</option>
            </select>
            <select v-model="filters.taskPriority" class="ops-select" @change="applyFilters">
              <option value="">全部优先级</option><option value="P0">P0</option><option value="P1">P1</option><option value="P2">P2</option>
            </select>
            <select v-model="filters.taskStoreId" class="ops-select" @change="applyFilters">
              <option value="">全部店铺</option>
              <option v-for="store in visibleStores" :key="store.store_id" :value="store.store_id">{{ store.store_name }}</option>
            </select>
            <select v-model="filters.taskCategory" class="ops-select" @change="applyFilters">
              <option value="">全部类别</option>
              <option>店铺健康</option><option>商品机会</option><option>商品售后</option><option>运营打法</option><option>运营动作</option>
            </select>
            <label class="ops-search"><Icon name="search" :size="15" /><input v-model="filters.taskSearch" type="search" placeholder="搜索任务、店铺或商品" @keyup.enter="applyFilters" /></label>
            <button class="ops-primary" type="button" :disabled="!permission.operations_task_manage" @click="openTaskCreate()">新建任务</button>
            <button class="ops-secondary" type="button" :disabled="!permission.operations_export" @click="exportData('tasks')">导出</button>
          </div>
          <div class="ops-actions-layout">
            <section class="ops-card">
              <div class="ops-card-head">
                <div><h2>行动任务</h2><p>已完成 {{ doneTaskCount }} / {{ tasks.length }}，按优先级和到期时间排序。</p></div>
              </div>
              <div v-if="!tasks.length" class="ops-empty">暂无持久化行动任务。可从总览告警或运营打法创建。</div>
              <article v-for="task in tasks" :key="task.id" class="ops-task" :class="{ done: task.status === 'done', overdue: isTaskOverdue(task) }">
                <input type="checkbox" :checked="task.status === 'done'" :disabled="!permission.operations_task_manage" @change="completeTask(task, $event.target.checked)" />
                <div>
                  <div class="ops-task-title"><span :class="task.priority.toLowerCase()">{{ task.priority }}</span><strong>{{ task.title }}</strong></div>
                  <p>{{ task.detail || "暂无补充说明" }}</p>
                  <small>{{ task.store_name || "无指定店铺" }} · {{ task.product_title || task.category }} · {{ task.due_at || "未设置到期时间" }} · 创建人 {{ task.created_by || "暂无记录" }}</small>
                </div>
                <button type="button" :disabled="!permission.operations_task_manage" @click="delayTask(task)">延期</button>
              </article>
            </section>
            <section class="ops-card rules-card">
              <div class="ops-card-head"><div><h2>巡检规则</h2><p>阈值写入 SQLite，仅用于本地提醒。</p></div><button type="button" class="ops-icon-button" :disabled="!permission.operations_rule_manage" @click="showRules"><Icon name="settings" :size="16" /></button></div>
              <div v-for="(value, key) in (rules?.rules || {})" :key="key" class="ops-rule-row"><span>{{ ({
                score_drop_threshold:'体验分单日下降',
                zero_exposure_hours:'连续零曝光小时',
                zero_sales_days:'连续零销量天数',
                no_new_product_days:'连续未观察到上新',
                first_response_seconds:'客服首响上限（秒）',
                no_reply_rate:'不回复率上限（%）',
                quality_refund_watch_rate:'质量退款率观察阈值（%）',
                high_views_threshold:'高浏览阈值',
                low_pay_threshold:'低支付人数阈值'
              })[key] || key }}</span><strong>{{ value }}</strong></div>
              <p class="ops-rule-note">规则来自本地配置；体验分、流量与客服平台阈值均不用于预测最终分数。</p>
            </section>
          </div>
        </div>
      </template>
    </template>

    <div v-if="drawer.open" class="ops-drawer-mask" @click.self="closeDrawer"></div>
    <aside v-if="drawer.open" class="ops-drawer" aria-label="运营诊断详情">
      <header><div><h2>{{ drawer.kind === "product" ? "商品机会详情" : "店铺健康详情" }}</h2><p>所有指标附来源和更新时间；缺失值保持“暂无数据”。</p></div><button type="button" aria-label="关闭" @click="closeDrawer"><Icon name="close" :size="18" /></button></header>
      <div v-if="drawer.loading" class="ops-drawer-state"><span class="ops-loading-dot"></span>正在读取真实详情…</div>
      <div v-else-if="drawer.error" class="ops-drawer-state danger">{{ drawer.error }}</div>
      <div v-else-if="currentStoreDetail" class="ops-drawer-body">
        <div class="ops-drawer-hero">
          <div><span>{{ currentStoreDetail.web_status }}</span><h3>{{ currentStoreDetail.store_name }}</h3><p>{{ currentStoreDetail.group_name || "未分组" }} · {{ currentStoreDetail.stats_at || "更新时间暂无数据" }}</p></div>
          <strong>{{ formatScore(currentStoreDetail.scores.dsr) }}</strong>
        </div>
        <div class="ops-detail-grid">
          <div><span>商品体验</span><b>{{ formatScore(currentStoreDetail.scores.score_product) }}</b></div>
          <div><span>物流体验</span><b>{{ formatScore(currentStoreDetail.scores.score_deliver) }}</b></div>
          <div><span>服务体验</span><b>{{ formatScore(currentStoreDetail.scores.score_service) }}</b></div>
          <div><span>质量退款率</span><b>{{ formatPercent(currentStoreDetail.quality_refund_ratio) }}</b></div>
          <div><span>连续未观察上新</span><b>{{ currentStoreDetail.no_new_product_days == null ? "无本地观测历史" : `${currentStoreDetail.no_new_product_days} 天` }}</b></div>
          <div><span>最近首次观测</span><b>{{ currentStoreDetail.latest_new_product_observed_at || "暂无数据" }}</b></div>
        </div>
        <section v-if="drawer.data.logistics" class="ops-logistics-diagnosis">
          <div class="ops-section-head">
            <h3>物流体验诊断</h3>
            <span class="ops-logistics-score">{{ formatScore(drawer.data.logistics.score) }}</span>
          </div>
          <p class="ops-rule-note">最近 14 天已发货有效订单 {{ drawer.data.logistics.observed_shipped_orders_14d }} 单；原因为真实后台待办、物流售后或本地可计算证据。</p>
          <div v-if="drawer.data.logistics.evidence?.length" class="ops-logistics-evidence-list">
            <article v-for="item in drawer.data.logistics.evidence" :key="item.code" :class="['ops-logistics-evidence', item.severity]">
              <div><strong>{{ item.title }}</strong><em>{{ item.severity === 'high' ? '优先处理' : '持续观察' }}</em></div>
              <p>证据：{{ item.evidence }}</p>
              <p>干预：{{ item.action }}</p>
              <small>来源：{{ item.source }}</small>
            </article>
          </div>
          <div v-else class="ops-empty small">暂无可用物流诊断证据。</div>
          <div v-if="drawer.data.logistics.dilution?.length" class="ops-logistics-dilution">
            <h4>分数稀释测算</h4>
            <table>
              <thead><tr><th>目标分</th><th>新增 5.00 分订单</th><th>新增 4.80 分订单</th></tr></thead>
              <tbody>
                <tr v-for="row in drawer.data.logistics.dilution" :key="row.target">
                  <td>{{ row.target.toFixed(2) }}</td>
                  <td>{{ row.new_full_score_orders }} 单</td>
                  <td>{{ row.new_4_8_score_orders == null ? "不适用" : `${row.new_4_8_score_orders} 单` }}</td>
                </tr>
              </tbody>
            </table>
            <p>{{ drawer.data.logistics.formula }}</p>
            <small>{{ drawer.data.logistics.assumption }}</small>
          </div>
          <div v-else class="ops-empty small">缺少最近 14 天已发货有效订单或当前物流体验分，无法做数量测算。</div>
        </section>
        <section>
          <h3>售后原因</h3>
          <div v-if="!drawer.data.after_sale_reasons?.length" class="ops-empty small">暂无售后原因记录。</div>
          <div v-for="reason in drawer.data.after_sale_reasons" :key="reason.reason" class="ops-reason-row"><span>{{ reason.reason }}</span><strong>{{ reason.count }} 单</strong></div>
        </section>
        <section>
          <h3>指标计算</h3>
          <div class="ops-calc-summary">
            <strong>品质退款率 {{ formatPercent(drawer.data.calculators?.quality_refund?.rate) }}</strong>
            <span>{{ drawer.data.calculators?.quality_refund?.current_count ?? "暂无数据" }} / {{ drawer.data.calculators?.quality_refund?.denominator ?? "暂无数据" }}</span>
            <small>{{ drawer.data.calculators?.quality_refund?.formula }}</small>
          </div>
          <div class="ops-calc-summary">
            <strong>客服不回复率 {{ formatPercent(drawer.data.calculators?.service_reply?.unreplied_rate) }}</strong>
            <span>{{ drawer.data.calculators?.service_reply?.source }}</span>
            <small>{{ drawer.data.calculators?.service_reply?.formula }}</small>
          </div>
        </section>
        <section>
          <h3>建议动作</h3>
          <p v-if="currentStoreDetail.alert_reasons.length">{{ currentStoreDetail.alert_reasons.join("；") }}</p>
          <p v-else>暂无达到本地规则阈值的异常。</p>
          <div class="ops-drawer-actions">
            <button class="ops-primary" type="button" :disabled="!permission.operations_task_manage" @click="openTaskCreate({ storeId: currentStoreDetail.store_id, title: `处理 ${currentStoreDetail.store_name} 健康异常`, detail: currentStoreDetail.alert_reasons.join('；'), category: '店铺健康' })">创建任务</button>
            <button class="ops-secondary" type="button" :disabled="!permission.operations_open_backend" @click="openBackend({ storeId: currentStoreDetail.store_id, target: 'score', label: currentStoreDetail.store_name })">打开体验分</button>
            <button class="ops-secondary" type="button" :disabled="!permission.operations_open_backend" @click="openBackend({ storeId: currentStoreDetail.store_id, target: 'review', label: currentStoreDetail.store_name })">打开评价</button>
            <button class="ops-secondary" type="button" :disabled="!permission.operations_open_backend" @click="openBackend({ storeId: currentStoreDetail.store_id, target: 'traffic', label: currentStoreDetail.store_name })">打开流量</button>
            <button class="ops-secondary" type="button" :disabled="!permission.operations_open_backend" @click="openBackend({ storeId: currentStoreDetail.store_id, target: 'aftersale', label: currentStoreDetail.store_name })">打开售后</button>
          </div>
        </section>
      </div>
      <div v-else-if="currentProductDetail" class="ops-drawer-body">
        <div class="ops-drawer-hero">
          <div><span>{{ currentProductDetail.status_text || "状态暂无数据" }}</span><h3>{{ currentProductDetail.title }}</h3><p>{{ currentProductDetail.store_name }} · {{ currentProductDetail.product_id }}</p></div>
          <strong>{{ formatMoney(currentProductDetail.price) }}</strong>
        </div>
        <div class="ops-detail-grid">
          <div><span>实际价格</span><b>{{ formatMoney(currentProductDetail.price) }}</b></div>
          <div><span>真实库存</span><b>{{ formatNumber(currentProductDetail.stock) }}</b></div>
          <div><span>真实销量</span><b>{{ formatNumber(currentProductDetail.sales) }}</b></div>
          <div><span>SKU 数</span><b>{{ formatNumber(currentProductDetail.sku_count) }}</b></div>
          <div><span>30日浏览</span><b>{{ formatNumber(currentProductDetail.metrics.views30) }}</b></div>
          <div><span>点击人数</span><b>{{ formatNumber(currentProductDetail.metrics.click_uv) }}</b></div>
          <div><span>支付人数</span><b>{{ formatNumber(currentProductDetail.metrics.pay_uv) }}</b></div>
          <div><span>售后单</span><b>{{ currentProductDetail.after_sale_count }}</b></div>
        </div>
        <section>
          <h3>商品漏斗</h3>
          <div class="ops-funnel">
            <div><span>浏览</span><b>{{ formatNumber(drawer.data.funnel?.views30) }}</b></div>
            <i></i><div><span>点击</span><b>{{ formatNumber(drawer.data.funnel?.click_uv) }}</b></div>
            <i></i><div><span>支付人数</span><b>{{ formatNumber(drawer.data.funnel?.pay_uv) }}</b></div>
          </div>
          <p class="ops-rule-note">加购数据当前没有可靠字段，因此不推算、不填充 0。</p>
        </section>
        <section>
          <h3>商品判断</h3>
          <p>{{ currentProductDetail.tags.join("、") }}</p>
          <p>关联订单 {{ drawer.data.order_count }} 单，关联售后 {{ drawer.data.after_sales?.length || 0 }} 单。</p>
          <div class="ops-detail-grid">
            <div><span>信息质量</span><b>{{ compactJson(currentProductDetail.info_score) }}</b></div>
            <div><span>高价/比价信息</span><b>{{ compactJson(currentProductDetail.cmp_price_info) }}</b></div>
            <div><span>售卖限制</span><b>{{ compactJson(currentProductDetail.sale_limit_info) }}</b></div>
            <div><span>审核信息</span><b>{{ compactJson(currentProductDetail.audit_info) }}</b></div>
          </div>
        </section>
        <section>
          <h3>销售升温证据</h3>
          <div class="ops-detail-grid">
            <div><span>今日有效支付订单</span><b>{{ formatNumber(currentProductDetail.orders_today) }} 单</b></div>
            <div><span>最近连续出单</span><b>{{ formatNumber(currentProductDetail.consecutive_order_days) }} 天</b></div>
            <div><span>近 7 日有效支付订单</span><b>{{ formatNumber(currentProductDetail.orders_7d) }} 单</b></div>
            <div><span>当前可用库存</span><b>{{ formatNumber(currentProductDetail.stock) }}</b></div>
          </div>
          <p>{{ currentProductDetail.opportunity_categories?.includes("sales_momentum") ? "已命中销售升温：先核对库存、采购利润、退款/品质和履约能力，再决定是否做真实内容曝光或合规投流。" : "当前未命中“今日 ≥2 单或连续 ≥2 天出单”；同一订单内多件商品只计 1 单。" }}</p>
          <p class="ops-rule-note">退款、取消、待付款和礼物待收下订单不计入；不提供补单、朋友下单、虚假评价或诱导评价建议。</p>
        </section>
        <section>
          <h3>售后原因聚类</h3>
          <div v-if="!drawer.data.after_sale_reason_clusters?.length" class="ops-empty small">暂无关联售后记录。</div>
          <div v-for="reason in drawer.data.after_sale_reason_clusters" :key="reason.reason" class="ops-reason-row">
            <span>{{ reason.reason }}</span><strong>{{ reason.count }} 单</strong>
          </div>
        </section>
        <section>
          <div class="ops-section-head">
            <h3>最近售后记录</h3>
            <button
              v-if="(drawer.data.after_sales?.length || 0) > 5"
              type="button"
              @click="drawer.showAllProductAfterSales = !drawer.showAllProductAfterSales"
            >{{ drawer.showAllProductAfterSales ? "收起" : "查看全部" }}</button>
          </div>
          <div v-if="!drawer.data.after_sales?.length" class="ops-empty small">暂无关联售后记录。</div>
          <div v-else class="ops-aftersale-list">
            <article
              v-for="item in (drawer.showAllProductAfterSales ? drawer.data.after_sales : drawer.data.after_sales.slice(0, 5))"
              :key="item.after_sale_order_id"
              class="ops-aftersale-item"
            >
              <div><strong>{{ item.reason_text }}</strong><span>{{ item.status_text || item.status || "状态暂无数据" }}</span></div>
              <p>{{ item.after_sale_type || "类型暂无数据" }} · 退款 {{ formatMoney(item.refund_amount) }}</p>
              <small>售后单 {{ item.after_sale_order_id || "暂无数据" }} · 更新 {{ formatDateTime(item.update_time) }}</small>
            </article>
          </div>
        </section>
        <section>
          <h3>官方评价概览</h3>
          <p>{{ drawer.data.evaluation_overview?.detail_status || "评价明细尚未接入，仅显示官方评价概览" }}</p>
          <div class="ops-detail-grid">
            <div><span>店铺商品好评率</span><b>{{ formatPercent(drawer.data.evaluation_overview?.good_eval_ratio) }}</b></div>
            <div><span>店铺质量退款率</span><b>{{ formatPercent(drawer.data.evaluation_overview?.quality_refund_ratio) }}</b></div>
          </div>
          <p class="ops-rule-note">以上为店铺级官方概览，更新时间 {{ drawer.data.evaluation_overview?.stats_at || "暂无数据" }}；不是该商品的逐条差评内容，也不用于预测最终评分。</p>
        </section>
        <section>
          <h3>建议动作</h3>
          <ul class="ops-advice-list">
            <li v-for="item in drawer.data.after_sale_advice || []" :key="item">{{ item }}</li>
          </ul>
          <div class="ops-drawer-actions">
            <button class="ops-primary" type="button" @click="openProductTask">创建诊断任务</button>
            <button class="ops-secondary" type="button" :disabled="!permission.operations_open_backend" @click="openBackend({ storeId: currentProductDetail.store_id, target: 'product', productId: currentProductDetail.product_id, label: currentProductDetail.title })">打开商品</button>
            <button class="ops-secondary" type="button" :disabled="!permission.operations_open_backend" @click="openBackend({ storeId: currentProductDetail.store_id, target: 'product_stats', productId: currentProductDetail.product_id, label: currentProductDetail.title })">商品数据</button>
          </div>
        </section>
      </div>
    </aside>

    <ModalDialog
      :open="modal.open"
      :title="modal.title"
      :subtitle="modal.subtitle"
      :confirm-label="modal.busy ? '处理中…' : modal.confirmLabel"
      :cancel-label="modal.cancelLabel"
      :busy="modal.busy"
      :z-index="140"
      @close="closeModal"
      @confirm="confirmModal"
    >
      <div v-if="modal.title === '新建运营行动任务'" class="ops-field-grid">
        <label>店铺
          <select v-model="form.taskStoreId" class="ops-select">
            <option value="">不指定店铺</option>
            <option v-for="store in visibleStores" :key="store.store_id" :value="store.store_id">{{ store.store_name }}</option>
          </select>
        </label>
        <label>优先级<select v-model="form.taskPriority" class="ops-select"><option>P0</option><option>P1</option><option>P2</option></select></label>
        <label class="span-2">任务标题<input v-model="form.taskTitle" class="ops-input" /></label>
        <label class="span-2">动作说明<textarea v-model="form.taskDetail" class="ops-textarea"></textarea></label>
        <label>类别<input v-model="form.taskCategory" class="ops-input" /></label>
        <label>到期时间<input v-model="form.taskDueAt" class="ops-input" type="date" /></label>
      </div>
      <div v-else v-html="modal.body"></div>
    </ModalDialog>
  </section>
</template>

<style scoped>
.ops-page {
  --ops-blue: #2f6fed;
  --ops-green: #0da66b;
  --ops-amber: #e39a22;
  --ops-red: #dc4c58;
  --ops-ink: #263342;
  --ops-muted: #788695;
  --ops-line: #e2e8ef;
  min-height: 100%;
  padding: 20px 24px 28px;
  color: var(--ops-ink);
  background: #f3f6f9;
}
.ops-page button, .ops-page input, .ops-page select, .ops-page textarea { font: inherit; }
.ops-topbar { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; margin-bottom: 14px; }
.ops-topbar h1 { margin: 0; font-size: 23px; letter-spacing: -.02em; }
.ops-topbar p { margin: 5px 0 0; color: var(--ops-muted); font-size: 13px; }
.ops-title-row { display: flex; align-items: center; gap: 12px; }
.ops-freshness { display: inline-flex; align-items: center; gap: 6px; color: #708090; font-size: 11px; }
.ops-freshness i { width: 7px; height: 7px; border-radius: 50%; background: var(--ops-green); box-shadow: 0 0 0 4px #dcf8eb; }
.ops-toolbar, .ops-toolbar-left { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.ops-search { min-width: 260px; height: 34px; display: flex; align-items: center; gap: 7px; padding: 0 10px; border: 1px solid var(--ops-line); border-radius: 8px; background: white; }
.ops-search input { width: 100%; border: 0; outline: 0; background: transparent; }
.ops-select, .ops-input, .ops-textarea { min-height: 34px; padding: 7px 9px; border: 1px solid var(--ops-line); border-radius: 7px; background: white; color: var(--ops-ink); }
.ops-textarea { min-height: 86px; resize: vertical; width: 100%; }
.ops-icon-button { width: 34px; height: 34px; display: grid; place-items: center; border: 1px solid var(--ops-line); border-radius: 8px; background: white; color: #52616f; cursor: pointer; }
.ops-icon-button:hover { border-color: #a9c7bb; color: var(--ops-green); }
.ops-primary, .ops-secondary { min-height: 34px; display: inline-flex; align-items: center; justify-content: center; gap: 6px; padding: 0 13px; border-radius: 7px; border: 1px solid transparent; cursor: pointer; }
.ops-primary { color: white; background: var(--ops-green); }
.ops-secondary { color: #40505f; background: white; border-color: var(--ops-line); }
.ops-primary:hover, .ops-secondary:hover, .ops-icon-button:hover { transform: translateY(-1px); box-shadow: 0 4px 10px rgba(36, 58, 70, .08); }
.ops-page button:focus-visible, .ops-page input:focus-visible, .ops-page select:focus-visible, .ops-page textarea:focus-visible { outline: 2px solid #62c69d; outline-offset: 2px; }
.ops-page button:disabled { opacity: .48; cursor: not-allowed; transform: none !important; box-shadow: none !important; }
.ops-subnav { display: flex; align-items: center; gap: 7px; padding-bottom: 10px; border-bottom: 1px solid var(--ops-line); }
.ops-subnav button { min-height: 33px; display: flex; align-items: center; gap: 7px; padding: 0 12px; border: 0; border-radius: 8px; background: transparent; color: #637180; cursor: pointer; }
.ops-subnav button:hover { background: #eaf3ef; color: #087e53; }
.ops-subnav button.active { background: #153c52; color: white; }
.ops-subnav b { padding: 1px 6px; border-radius: 99px; background: rgba(255,255,255,.16); font-size: 10px; font-weight: 600; }
.ops-source-legend { display: flex; align-items: center; flex-wrap: wrap; gap: 9px; margin: 10px 0 15px; color: #778592; font-size: 11px; }
.ops-source-legend > button { display: inline-flex; align-items: center; gap: 5px; border: 0; background: transparent; color: #778592; cursor: pointer; padding: 0; }
.ops-source-legend i { width: 6px; height: 6px; border-radius: 50%; }
.ops-source-legend .realtime { background: var(--ops-green); }
.ops-source-legend .api { background: var(--ops-blue); }
.ops-source-legend .sqlite { background: #8a63d2; }
.ops-source-legend .calc { background: var(--ops-amber); }
.ops-source-legend > button:last-child { margin-left: auto; color: #196b53; }
.ops-toast { position: sticky; top: 8px; z-index: 30; width: fit-content; max-width: 80%; margin: 0 0 10px auto; padding: 9px 13px; border-radius: 8px; background: #e9f9f1; border: 1px solid #b8e7d1; color: #08754e; box-shadow: 0 8px 24px rgba(27, 72, 56, .1); }
.ops-toast.error { background: #fff0f1; border-color: #f2c8cc; color: #a52d38; }
.ops-inspection { margin-bottom: 12px; padding: 11px 13px; border: 1px solid #cce8dc; border-radius: 8px; background: #f0faf5; }
.ops-inspection > div:first-child { display: flex; align-items: center; gap: 8px; font-size: 11px; }
.ops-inspection small { display: block; margin-top: 6px; color: #6f8278; font-size: 9px; }
.ops-progress { height: 5px; margin-top: 8px; overflow: hidden; border-radius: 99px; background: #d9eee4; }
.ops-progress i { display: block; height: 100%; border-radius: inherit; background: linear-gradient(90deg, #5cce9c, #0b9d66); transition: width .25s ease; }
.ops-state-card { min-height: 170px; display: flex; align-items: center; gap: 16px; padding: 25px; border: 1px solid var(--ops-line); border-radius: 11px; background: white; }
.ops-state-card.danger { border-color: #efc7ca; background: #fff8f8; }
.ops-state-card h1, .ops-state-card h2 { margin: 0 0 7px; }
.ops-state-card p { margin: 0 0 12px; color: var(--ops-muted); }
.ops-loading-dot { width: 22px; height: 22px; border: 2px solid #c7e4d8; border-top-color: var(--ops-green); border-radius: 50%; animation: spin .75s linear infinite; }
.ops-loading-dot.inline { display: inline-block; width: 14px; height: 14px; margin-right: 7px; vertical-align: -2px; }
.ops-panel { display: grid; gap: 14px; }
.ops-kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 11px; }
.product-kpis { grid-template-columns: repeat(5, minmax(0, 1fr)); }
.ops-kpis article { min-height: 106px; padding: 14px 16px; border: 1px solid var(--ops-line); border-radius: 10px; background: white; }
.ops-kpis span { color: #687684; font-size: 12px; }
.ops-kpis strong { display: block; margin: 11px 0 5px; font-size: 27px; letter-spacing: -.03em; }
.ops-kpis small { color: #94a0ab; font-size: 11px; }
.ops-card { min-width: 0; padding: 15px; border: 1px solid var(--ops-line); border-radius: 10px; background: white; box-shadow: 0 1px 2px rgba(25, 47, 62, .025); }
.ops-card-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; margin-bottom: 12px; }
.ops-card-head h2 { margin: 0; font-size: 15px; }
.ops-card-head p { margin: 4px 0 0; color: var(--ops-muted); font-size: 11px; }
.ops-alert-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 9px; }
.ops-alert-item { overflow: hidden; display: grid; grid-template-columns: minmax(0, 1fr) auto; border: 1px solid var(--ops-line); border-left: 3px solid var(--ops-blue); border-radius: 8px; background: #fbfcfd; }
.ops-alert-item.p0 { border-left-color: var(--ops-red); }
.ops-alert-item.p1 { border-left-color: var(--ops-amber); }
.ops-alert-open { min-width: 0; display: flex; flex-direction: column; gap: 4px; padding: 11px 12px; border: 0; background: transparent; text-align: left; cursor: pointer; }
.ops-alert-open span { width: fit-content; padding: 1px 6px; border-radius: 4px; background: #fce8ea; color: #b83a45; font-size: 10px; font-weight: 700; }
.ops-alert-open strong { font-size: 12px; }
.ops-alert-open small, .ops-alert-open em { color: #86919b; font-size: 10px; font-style: normal; overflow-wrap: anywhere; }
.ops-alert-actions { display: flex; flex-direction: column; justify-content: center; gap: 5px; padding: 8px 9px 8px 0; }
.ops-alert-actions button { border: 0; background: transparent; color: #1c7258; font-size: 10px; cursor: pointer; white-space: nowrap; }
.ops-grid-main { display: grid; grid-template-columns: minmax(0, 1.6fr) minmax(280px, 1fr); gap: 14px; }
.ops-chart { height: 220px; display: flex; align-items: flex-end; gap: 7px; padding: 12px 5px 0; border-top: 1px solid #f0f3f6; }
.ops-chart-col { flex: 1; min-width: 18px; display: flex; flex-direction: column; align-items: center; gap: 5px; }
.ops-chart-bars { width: 100%; height: 155px; display: flex; align-items: flex-end; justify-content: center; }
.ops-chart-bars i { width: min(24px, 70%); border-radius: 3px 3px 0 0; background: linear-gradient(#5aafeb, #2877e7); }
.ops-chart-col span { color: #61717f; font-size: 9px; }
.ops-chart-col small { color: #9aa4ad; font-size: 8px; writing-mode: vertical-rl; }
.ops-gap-list { display: grid; gap: 8px; margin: 0; padding: 0; list-style: none; }
.ops-gap-list li { padding: 8px 0; border-bottom: 1px solid #f0f3f6; }
.ops-gap-list strong, .ops-gap-list span { display: block; }
.ops-gap-list strong { font-size: 11px; }
.ops-gap-list span { margin-top: 3px; color: var(--ops-muted); font-size: 10px; line-height: 1.55; }
.ops-segment, .ops-filter-chips { display: flex; align-items: center; gap: 3px; padding: 2px; border-radius: 6px; background: #f2f5f8; }
.ops-segment button, .ops-filter-chips button, .ops-playbook-tabs button { border: 0; border-radius: 5px; padding: 5px 9px; background: transparent; color: #6a7682; font-size: 11px; cursor: pointer; }
.ops-segment button.active, .ops-filter-chips button.active, .ops-playbook-tabs button.active { background: white; color: #166847; box-shadow: 0 1px 3px rgba(35, 59, 73, .1); }
.ops-table-wrap { max-width: 100%; overflow: auto; border: 1px solid #edf1f5; border-radius: 8px; }
.ops-table-wrap.tall { max-height: 620px; }
.ops-table { width: 100%; min-width: 1000px; border-collapse: collapse; font-size: 11px; }
.ops-table th { position: sticky; top: 0; z-index: 2; padding: 8px 9px; background: #f7f9fb; color: #71808e; font-weight: 500; text-align: left; white-space: nowrap; border-bottom: 1px solid #e8edf2; }
.ops-table td { padding: 8px 9px; border-bottom: 1px solid #f0f3f6; vertical-align: middle; }
.ops-table tbody tr:hover { background: #fbfdfc; }
.ops-table td strong, .ops-table td small { display: block; }
.ops-table td small { margin-top: 3px; color: #8d98a2; font-size: 9px; }
.ops-status { display: inline-flex; align-items: center; padding: 2px 7px; border-radius: 99px; font-size: 10px; }
.ops-status.danger { color: #b32d39; background: #fde9eb; }
.ops-status.warn { color: #9d6b0f; background: #fff4dc; }
.ops-status.ok { color: #08744d; background: #e9f8f0; }
.ops-score { font-size: 13px; }
.ops-row-actions { white-space: nowrap; }
.ops-row-actions button, .ops-row-link { border: 0; background: transparent; color: #08794f; cursor: pointer; padding: 4px; }
.ops-row-actions button + button { margin-left: 5px; }
.up { color: #d84a36 !important; }
.down { color: #12915f !important; }
.ops-empty, .ops-empty-cell { padding: 28px 16px; color: #8e99a2; text-align: center; }
.ops-empty.small { padding: 12px; font-size: 11px; }
.ops-health-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 11px; }
.ops-dimensions { display: grid; gap: 9px; }
.ops-dimensions > div { position: relative; display: grid; grid-template-columns: 1fr auto; gap: 3px 10px; padding-bottom: 9px; }
.ops-dimensions span { color: #697785; font-size: 10px; }
.ops-dimensions strong { font-size: 13px; }
.ops-dimensions i { grid-column: 1 / -1; height: 4px; max-width: 100%; border-radius: 2px; background: linear-gradient(90deg, var(--ops-green), #7fd4ad); }
.ops-rule-note { margin: 11px 0 0; padding: 9px 10px; border-radius: 6px; background: #f6f8fa; color: #7c8892; font-size: 10px; line-height: 1.6; }
.ops-products-toolbar { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; margin-bottom: 11px; }
.ops-products-toolbar .ops-search { margin-left: auto; }
.ops-product-cell { display: flex; align-items: center; gap: 9px; min-width: 230px; }
.ops-product-cell img { width: 42px; height: 42px; object-fit: cover; border-radius: 6px; border: 1px solid #edf0f2; }
.ops-tag { display: inline-block; margin: 1px 3px 1px 0; padding: 2px 6px; border-radius: 4px; background: #eef4f8; color: #466272; font-size: 9px; }
.ops-playbook-grid { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
.ops-playbook-tabs { display: flex; flex-wrap: wrap; gap: 3px; padding-bottom: 10px; border-bottom: 1px solid #edf1f4; }
.ops-playbook-tabs button { background: #f4f7f9; }
.ops-playbook-body { padding-top: 12px; }
.ops-lead { margin: 0 0 12px; color: #53616e; font-size: 12px; line-height: 1.7; }
.ops-playbook-body article { display: grid; grid-template-columns: 76px 1fr; gap: 9px; padding: 9px 0; border-top: 1px solid #f0f3f5; }
.ops-playbook-body article b { color: #21825e; font-size: 10px; }
.ops-playbook-body article strong, .ops-playbook-body article small { display: block; }
.ops-playbook-body article strong { font-size: 11px; }
.ops-playbook-body article small { margin-top: 5px; color: #8b96a0; font-size: 10px; }
.ops-calculator { padding-top: 12px; }
.ops-field-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 9px; }
.ops-field-grid label { display: grid; gap: 5px; color: #60707d; font-size: 10px; }
.ops-field-grid .span-2 { grid-column: 1 / -1; }
.ops-formula { margin: 11px 0; padding: 10px; border-radius: 6px; background: #f4f8f6; color: #5f756b; font-size: 10px; line-height: 1.6; }
.ops-calc-answer { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 11px; border: 1px solid #dceee5; border-radius: 7px; background: #f0faf5; font-size: 11px; }
.ops-calc-answer strong { color: #08764e; font-size: 18px; }
.ops-warning { margin: 9px 0 0; padding: 9px; border: 1px solid #f2d7a9; border-radius: 6px; background: #fff8eb; color: #956818; font-size: 10px; line-height: 1.55; }
.ops-scenario-table { width: 100%; margin-top: 10px; border-collapse: collapse; font-size: 10px; }
.ops-scenario-table th, .ops-scenario-table td { padding: 7px 8px; border-bottom: 1px solid #edf1f4; text-align: left; }
.ops-scenario-table th { color: #72808c; background: #f7f9fb; font-weight: 500; }
.ops-actions-layout { display: grid; grid-template-columns: minmax(0, 1.6fr) minmax(280px, .8fr); gap: 14px; align-items: start; }
.ops-task { display: grid; grid-template-columns: 18px minmax(0, 1fr) auto; gap: 10px; align-items: start; padding: 12px 3px; border-bottom: 1px solid #edf1f4; }
.ops-task.done { opacity: .58; }
.ops-task.overdue { border-left: 3px solid #dc4c58; padding-left: 8px; background: #fffafb; }
.ops-task-title { display: flex; align-items: center; gap: 7px; }
.ops-task-title span { padding: 1px 5px; border-radius: 4px; background: #fff0d8; color: #9e6a13; font-size: 9px; font-weight: 700; }
.ops-task-title span.p0 { background: #fde8ea; color: #ad3440; }
.ops-task-title strong { font-size: 11px; }
.ops-task p { margin: 5px 0; color: #74818d; font-size: 10px; }
.ops-task small { color: #929ca4; font-size: 9px; }
.ops-task > button { border: 0; background: transparent; color: #39755f; font-size: 10px; cursor: pointer; }
.rules-card { position: sticky; top: 12px; }
.ops-rule-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 8px 0; border-bottom: 1px solid #f0f3f5; color: #60707d; font-size: 10px; }
.ops-rule-row strong { color: #314452; }
.ops-drawer-mask { position: fixed; inset: 0; z-index: 110; background: rgba(13, 28, 39, .28); }
.ops-drawer { position: fixed; top: 0; right: 0; bottom: 0; z-index: 111; width: min(560px, 94vw); display: flex; flex-direction: column; background: #f6f8fa; box-shadow: -18px 0 40px rgba(18, 36, 48, .18); }
.ops-drawer > header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 18px; background: #17394f; color: white; }
.ops-drawer > header h2 { margin: 0; font-size: 17px; }
.ops-drawer > header p { margin: 5px 0 0; color: #c4d2dc; font-size: 10px; }
.ops-drawer > header button { width: 32px; height: 32px; border: 0; border-radius: 7px; background: rgba(255,255,255,.12); color: white; cursor: pointer; }
.ops-drawer-body { overflow: auto; padding: 15px; display: grid; gap: 11px; }
.ops-drawer-state { padding: 35px 20px; color: #71808d; text-align: center; }
.ops-drawer-state.danger { color: #b23b46; }
.ops-drawer-hero { display: flex; align-items: center; justify-content: space-between; gap: 14px; padding: 14px; border-radius: 9px; background: white; border: 1px solid var(--ops-line); }
.ops-drawer-hero span { color: #7f8d98; font-size: 10px; }
.ops-drawer-hero h3 { margin: 4px 0; font-size: 16px; }
.ops-drawer-hero p { margin: 0; color: #8b97a0; font-size: 10px; }
.ops-drawer-hero > strong { font-size: 22px; color: #08774f; }
.ops-detail-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.ops-detail-grid > div, .ops-drawer section { padding: 12px; border: 1px solid var(--ops-line); border-radius: 8px; background: white; }
.ops-detail-grid span, .ops-detail-grid b { display: block; }
.ops-detail-grid span { color: #7a8792; font-size: 10px; }
.ops-detail-grid b { margin-top: 5px; font-size: 12px; overflow-wrap: anywhere; }
.ops-drawer section h3 { margin: 0 0 9px; font-size: 12px; }
.ops-drawer section p { color: #64727f; font-size: 10px; line-height: 1.65; }
.ops-reason-row { display: flex; justify-content: space-between; gap: 12px; padding: 7px 0; border-bottom: 1px solid #f0f3f5; font-size: 10px; }
.ops-logistics-score { padding: 4px 9px; border-radius: 999px; color: #9b4b37; background: #fff0eb; font-size: 16px; font-weight: 700; }
.ops-logistics-evidence-list { display: grid; gap: 8px; }
.ops-logistics-evidence { padding: 10px; border: 1px solid #e5eaee; border-left: 4px solid #e2a23b; border-radius: 6px; background: #fffdf8; }
.ops-logistics-evidence.high { border-left-color: #df665e; background: #fff8f7; }
.ops-logistics-evidence > div { display: flex; justify-content: space-between; gap: 8px; }
.ops-logistics-evidence strong { color: #3c4c59; font-size: 11px; }
.ops-logistics-evidence em { color: #b04d46; font-size: 9px; font-style: normal; }
.ops-logistics-evidence p { margin: 6px 0 0; color: #62717d; font-size: 10px; line-height: 1.55; }
.ops-logistics-evidence small { display: block; margin-top: 6px; color: #91a0aa; font-size: 9px; }
.ops-logistics-dilution { margin-top: 12px; }
.ops-logistics-dilution h4 { margin: 0 0 8px; color: #42525f; font-size: 11px; }
.ops-logistics-dilution table { width: 100%; border-collapse: collapse; }
.ops-logistics-dilution th, .ops-logistics-dilution td { padding: 7px 8px; border-bottom: 1px solid #edf1f4; font-size: 10px; text-align: left; }
.ops-logistics-dilution th { color: #7b8893; background: #f8fafb; }
.ops-logistics-dilution p { margin: 8px 0 3px; color: #64727e; font-size: 9px; }
.ops-logistics-dilution small { color: #8b98a3; font-size: 9px; line-height: 1.55; }
.ops-section-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.ops-section-head button { border: 0; background: transparent; color: #39755f; font-size: 10px; cursor: pointer; }
.ops-aftersale-list { display: grid; gap: 7px; max-height: 445px; overflow: auto; }
.ops-aftersale-item { padding: 9px; border: 1px solid #edf1f3; border-radius: 7px; background: #fbfcfd; }
.ops-aftersale-item > div { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.ops-aftersale-item strong { font-size: 10px; }
.ops-aftersale-item span { color: #6e7f89; font-size: 9px; }
.ops-aftersale-item p { margin: 5px 0; }
.ops-aftersale-item small { color: #8a96a0; font-size: 9px; overflow-wrap: anywhere; }
.ops-advice-list { margin: 0; padding-left: 17px; color: #64727f; font-size: 10px; line-height: 1.7; }
.ops-calc-summary { display: grid; gap: 3px; padding: 8px 0; border-bottom: 1px solid #f0f3f5; }
.ops-calc-summary strong { font-size: 11px; }
.ops-calc-summary span, .ops-calc-summary small { color: #7e8a94; font-size: 9px; }
.ops-drawer-actions { display: flex; gap: 7px; flex-wrap: wrap; margin-top: 11px; }
.ops-funnel { display: flex; align-items: center; gap: 7px; }
.ops-funnel div { flex: 1; padding: 10px; border-radius: 7px; background: #eff7f3; text-align: center; }
.ops-funnel span, .ops-funnel b { display: block; }
.ops-funnel span { color: #75867d; font-size: 9px; }
.ops-funnel b { margin-top: 4px; font-size: 15px; }
.ops-funnel i { width: 16px; height: 1px; background: #acd5c1; }
.ops-source-list { display: grid; gap: 8px; }
.ops-source-row { padding: 10px; border: 1px solid var(--ops-line); border-radius: 7px; background: #fafcfd; }
.ops-source-row strong, .ops-source-row span, .ops-source-row small { display: block; }
.ops-source-row strong { font-size: 11px; }
.ops-source-row span { margin-top: 4px; color: #4f606d; font-size: 10px; }
.ops-source-row small { margin-top: 4px; color: #8a96a0; font-size: 9px; }
.ops-confirm-target { padding: 12px; border-radius: 8px; background: #f1f8f5; border: 1px solid #d9ece3; }
.ops-confirm-target strong, .ops-confirm-target span { display: block; }
.ops-confirm-target span { margin-top: 4px; color: #61736a; font-size: 10px; }
.ops-modal-note { color: #71808a; font-size: 10px; line-height: 1.65; }
.spinning { animation: spin .8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
@media (max-width: 1100px) {
  .ops-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .ops-grid-main, .ops-actions-layout, .ops-playbook-grid { grid-template-columns: 1fr; }
  .ops-health-grid { grid-template-columns: 1fr; }
}
@media (max-width: 720px) {
  .ops-page { padding: 12px; }
  .ops-topbar { flex-direction: column; }
  .ops-toolbar { width: 100%; flex-wrap: wrap; }
  .ops-search { min-width: 0; width: 100%; }
  .ops-subnav { overflow-x: auto; }
  .ops-kpis, .ops-alert-grid, .ops-field-grid, .ops-detail-grid { grid-template-columns: 1fr; }
  .ops-source-legend { align-items: flex-start; }
  .ops-source-legend button { margin-left: 0; }
}
</style>

<style>
/* v-html confirm bodies live in the teleported modal, so their layout
   primitives must be global rather than component-scoped. */
.modal-body .ops-field-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}
.modal-body .ops-field-grid label {
  display: grid;
  gap: 5px;
  color: #60707d;
  font-size: 10px;
}
.modal-body .ops-field-grid .span-2 { grid-column: 1 / -1; }
.modal-body .ops-input,
.modal-body .ops-select {
  width: 100%;
  min-height: 34px;
  padding: 7px 9px;
  border: 1px solid #e2e8ef;
  border-radius: 7px;
  background: #fff;
  color: #263342;
}
.modal-body .ops-source-list { display: grid; gap: 8px; }
.modal-body .ops-source-row {
  padding: 10px;
  border: 1px solid #e2e8ef;
  border-radius: 7px;
  background: #fafcfd;
}
.modal-body .ops-source-row strong,
.modal-body .ops-source-row span,
.modal-body .ops-source-row small { display: block; }
.modal-body .ops-source-row span,
.modal-body .ops-source-row small {
  margin-top: 4px;
  color: #7e8a94;
  font-size: 10px;
}
.modal-body .ops-confirm-target {
  padding: 12px;
  border: 1px solid #d9ece3;
  border-radius: 8px;
  background: #f1f8f5;
}
.modal-body .ops-confirm-target strong,
.modal-body .ops-confirm-target span { display: block; }
.modal-body .ops-confirm-target span {
  margin-top: 4px;
  color: #61736a;
  font-size: 10px;
}
.modal-body .ops-modal-note {
  color: #71808a;
  font-size: 10px;
  line-height: 1.65;
}
</style>
