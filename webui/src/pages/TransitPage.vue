<script setup>
import {
  computed,
  onMounted,
  reactive,
  ref,
} from "vue";
import { call } from "../api";
import Icon from "../components/Icon.vue";
import ModalDialog from "../components/ModalDialog.vue";

const stores = ref([]);
const selectedStoreId = ref("");
const overview = ref(null);
const loading = ref(false);
const applying = ref(false);
const error = ref("");
const note = ref("");
const applyDialogOpen = ref(false);
const selectedTemplateIds = ref([]);
const execution = ref(null);

const form = reactive({
  direct_first_quantity: 1,
  direct_first_price: 0,
  direct_second_quantity: 1,
  direct_second_price: 0,
  special_first_quantity: 1,
  special_first_price: 10,
  special_second_quantity: 1,
  special_second_price: 8,
  transit_first_quantity: 1,
  transit_first_price: 3,
  transit_second_quantity: 1,
  transit_second_price: 1,
  set_all_products_transit: true,
});

const currentStore = computed(() => (
  stores.value.find(
    (store) => String(store.id) === String(selectedStoreId.value),
  ) || null
));
const serviceOpen = computed(
  () => Boolean(overview.value?.service?.open),
);
const templates = computed(() => overview.value?.templates || []);
const linkedTemplates = computed(() => (
  templates.value.filter(
    (template) => Number(template.relatedProductsNum || 0) > 0,
  )
));
const products = computed(() => overview.value?.products || []);
const eligibleProducts = computed(() => (
  products.value.filter((product) => Boolean(product.canTransit))
));
const selectedTemplates = computed(() => (
  templates.value.filter((template) => (
    selectedTemplateIds.value.includes(String(template.templateId))
  ))
));
const workflowSteps = computed(() => (
  execution.value?.steps || [
    { key: "service", title: "服务开通", status: "pending", detail: "" },
    { key: "products", title: "商品切换", status: "pending", detail: "" },
    { key: "templates", title: "模板更新", status: "pending", detail: "" },
    { key: "verification", title: "最终回读", status: "pending", detail: "" },
  ]
));
const productFailures = computed(
  () => execution.value?.product_failures || [],
);
const templateFailures = computed(
  () => execution.value?.template_failures || [],
);
const aggregate = computed(() => (
  execution.value?.aggregate || null
));

function rulesPayload() {
  const asCents = (value) => Math.round(Number(value || 0) * 100);
  return {
    direct_first_quantity: Number(form.direct_first_quantity || 1),
    direct_first_price_cents: asCents(form.direct_first_price),
    direct_second_quantity: Number(form.direct_second_quantity || 1),
    direct_second_price_cents: asCents(form.direct_second_price),
    special_first_quantity: Number(form.special_first_quantity || 1),
    special_first_price_cents: asCents(form.special_first_price),
    special_second_quantity: Number(form.special_second_quantity || 1),
    special_second_price_cents: asCents(form.special_second_price),
    transit_first_quantity: Number(form.transit_first_quantity || 1),
    transit_first_price_cents: asCents(form.transit_first_price),
    transit_second_quantity: Number(form.transit_second_quantity || 1),
    transit_second_price_cents: asCents(form.transit_second_price),
    set_all_products_transit: Boolean(
      form.set_all_products_transit,
    ),
  };
}

function assignRules(rules) {
  if (!rules) return;
  const yuan = (value) => Number(value || 0) / 100;
  Object.assign(form, {
    direct_first_quantity: Number(rules.direct_first_quantity || 1),
    direct_first_price: yuan(rules.direct_first_price_cents),
    direct_second_quantity: Number(rules.direct_second_quantity || 1),
    direct_second_price: yuan(rules.direct_second_price_cents),
    special_first_quantity: Number(rules.special_first_quantity || 1),
    special_first_price: yuan(rules.special_first_price_cents),
    special_second_quantity: Number(rules.special_second_quantity || 1),
    special_second_price: yuan(rules.special_second_price_cents),
    transit_first_quantity: Number(rules.transit_first_quantity || 1),
    transit_first_price: yuan(rules.transit_first_price_cents),
    transit_second_quantity: Number(rules.transit_second_quantity || 1),
    transit_second_price: yuan(rules.transit_second_price_cents),
    set_all_products_transit: Boolean(
      rules.set_all_products_transit !== false,
    ),
  });
}

async function loadStores() {
  const result = await call("stores.list", {});
  stores.value = (result.stores || []).filter(
    (store) => store.web_status === "已登录",
  );
  if (
    !selectedStoreId.value
    || !stores.value.some(
      (store) => String(store.id) === String(selectedStoreId.value),
    )
  ) {
    selectedStoreId.value = String(
      stores.value.find((store) => store.id === 19)?.id
      || stores.value[0]?.id
      || "",
    );
  }
}

async function loadOverview({ preserveExecution = false } = {}) {
  if (!selectedStoreId.value) return;
  loading.value = true;
  if (!preserveExecution) {
    error.value = "";
    note.value = "";
  }
  try {
    const result = await call("transit.shipping.overview", {
      store_id: Number(selectedStoreId.value),
    });
    overview.value = result;
    assignRules(result.rules);
    selectedTemplateIds.value = (
      result.templates || []
    )
      .filter((template) => Number(template.relatedProductsNum || 0) > 0)
      .map((template) => String(template.templateId));
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause);
    overview.value = null;
  } finally {
    loading.value = false;
  }
}

async function changeStore() {
  overview.value = null;
  selectedTemplateIds.value = [];
  execution.value = null;
  await loadOverview();
}

function toggleTemplate(templateId) {
  const key = String(templateId);
  const template = templates.value.find(
    (item) => String(item.templateId) === key,
  );
  if (Number(template?.relatedProductsNum || 0) <= 0) return;
  if (selectedTemplateIds.value.includes(key)) {
    selectedTemplateIds.value = selectedTemplateIds.value.filter(
      (item) => item !== key,
    );
  } else {
    selectedTemplateIds.value = [
      ...selectedTemplateIds.value,
      key,
    ];
  }
}

function formatCalculation(row) {
  if (!row) return "未读取";
  const unit = "件";
  return (
    `${row.firstValAmount || 1}${unit}内`
    + `¥${Number(row.firstPrice || 0) / 100}`
    + `，每增加${row.secondValAmount || 1}${unit}`
    + `加¥${Number(row.secondPrice || 0) / 100}`
  );
}

function directSummary(template) {
  const rows = (
    template.allFreightCalcMethod?.freightCalcMethodList || []
  );
  const fallback = rows.find((row) => row.isDefault);
  const special = rows.filter((row) => !row.isDefault);
  return [
    `默认：${formatCalculation(fallback)}`,
    ...special.map((row) => {
      const regions = (row.addressInfo || [])
        .map((item) => item.provinceName)
        .filter(Boolean)
        .join("、");
      return `${regions || "指定地区"}：${formatCalculation(row)}`;
    }),
  ].join("；");
}

function transitSummary(template) {
  const rows = (
    template.consolidateConfig?.allFreightCalcMethod
      ?.freightCalcMethodList || []
  );
  return formatCalculation(
    rows.find((row) => row.isDefault) || rows[0],
  );
}

function requestApply() {
  error.value = "";
  if (!selectedTemplateIds.value.length) {
    error.value = "请至少选择一个已关联当前店铺商品的运费模板";
    return;
  }
  applyDialogOpen.value = true;
}

async function applyChanges() {
  if (applying.value) return;
  applying.value = true;
  error.value = "";
  note.value = "";
  execution.value = null;
  try {
    const result = await call("transit.shipping.apply", {
      store_id: Number(selectedStoreId.value),
      template_ids: selectedTemplateIds.value,
      rules: rulesPayload(),
      confirmed: true,
    });
    execution.value = result;
    applyDialogOpen.value = false;
    if (result.success) {
      note.value = "全店中转连续流程完成";
    } else {
      const stageLabels = {
        service: "服务开通",
        products: "商品切换",
        templates: "模板更新",
        verification: "最终回读",
      };
      const stage = stageLabels[result.failed_stage]
        || result.failed_stage
        || "未知阶段";
      error.value = (
        `连续流程在“${stage}”阶段停止：`
        + (result.errors?.join("；") || "官方回读未通过")
      );
    }
    await loadOverview({ preserveExecution: true });
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    applying.value = false;
  }
}

onMounted(async () => {
  try {
    await loadStores();
    await loadOverview();
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause);
  }
});
</script>

<template>
  <section class="page transit-page">
    <header class="page-header transit-header">
      <div>
        <h1>偏远地区中转</h1>
        <p>官方开通、运费模板和商品中转方式的真实批量工作区</p>
      </div>
      <div class="toolbar">
        <select v-model="selectedStoreId" class="select" @change="changeStore">
          <option value="">选择已登录店铺</option>
          <option v-for="store in stores" :key="store.id" :value="String(store.id)">
            {{ store.name }}
          </option>
        </select>
        <button class="btn" type="button" :disabled="loading" @click="loadOverview">
          <Icon name="refresh" :size="15" />
          {{ loading ? "读取中…" : "重新预检" }}
        </button>
      </div>
    </header>

    <div v-if="error" class="transit-message danger">{{ error }}</div>
    <div v-if="note" class="transit-message success">{{ note }}</div>

    <div v-if="loading" class="card transit-loading">正在读取官方服务状态、运费模板和商品关联…</div>

    <template v-else-if="overview">
      <section v-if="applying || execution" class="card flow-card">
        <div class="section-heading">
          <div>
            <h2>连续流程状态</h2>
            <p v-if="applying">一次确认已生效，后台正在按顺序连续执行，中途无需再次点击。</p>
            <p v-else-if="execution?.success">服务、全部可中转商品和所选模板均已通过最终官方聚合回读。</p>
            <p v-else>流程已停止，未完成的后续阶段不会继续执行。</p>
          </div>
          <span
            v-if="execution"
            class="flow-verdict"
            :class="execution.success ? 'success' : 'failed'"
          >
            {{ execution.success ? "全店中转连续流程完成" : `停止于 ${execution.failed_stage}` }}
          </span>
        </div>

        <div class="flow-steps">
          <article
            v-for="step in workflowSteps"
            :key="step.key"
            class="flow-step"
            :class="step.status"
          >
            <span class="flow-index">{{ workflowSteps.indexOf(step) + 1 }}</span>
            <div>
              <strong>{{ step.title }}</strong>
              <small>{{ step.detail || (applying ? "等待连续执行" : "尚未执行") }}</small>
            </div>
            <span class="step-status">
              {{
                {
                  pending: "待执行",
                  running: "执行中",
                  success: "已通过",
                  failed: "失败",
                  skipped: "已跳过",
                }[step.status]
              }}
            </span>
          </article>
        </div>

        <div v-if="aggregate" class="aggregate-grid">
          <div :class="{ ok: aggregate.service_open }">
            <span>服务 open</span>
            <strong>{{ aggregate.service_open ? "通过" : "未通过" }}</strong>
          </div>
          <div :class="{ ok: aggregate.products_ok }">
            <span>全部可中转商品 transitType=1</span>
            <strong>{{ aggregate.products_ok ? "通过" : "未通过" }}</strong>
          </div>
          <div :class="{ ok: aggregate.templates_ok }">
            <span>所有所选模板字段一致</span>
            <strong>{{ aggregate.templates_ok ? "通过" : "未通过" }}</strong>
          </div>
        </div>

        <div v-if="execution && !execution.success" class="flow-failure">
          <div>
            <strong>已完成步骤</strong>
            <p>{{ execution.completed_steps?.join("、") || "无" }}</p>
          </div>
          <div v-if="execution.backup_key">
            <strong>执行前官方快照</strong>
            <p>已保存在本机：<code>{{ execution.backup_key }}</code></p>
          </div>
          <div v-if="execution.manual_guidance?.length">
            <strong>人工处理建议</strong>
            <ul>
              <li v-for="item in execution.manual_guidance" :key="item">{{ item }}</li>
            </ul>
          </div>
        </div>

        <div v-if="productFailures.length" class="failure-block">
          <h3>商品失败明细</h3>
          <div class="table-scroll">
            <table class="data-table">
              <thead><tr><th>商品</th><th>官方回读原因</th><th>人工入口</th></tr></thead>
              <tbody>
                <tr v-for="item in productFailures" :key="item.product_id">
                  <td><strong>{{ item.name || "未命名商品" }}</strong><small>ID：{{ item.product_id }}</small></td>
                  <td>{{ item.reason }}</td>
                  <td><a :href="item.manual_url" target="_blank" rel="noreferrer">打开中转商品页</a></td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div v-if="templateFailures.length" class="failure-block">
          <h3>模板官方回读差异</h3>
          <article
            v-for="item in templateFailures"
            :key="item.template_id"
            class="template-failure"
          >
            <strong>{{ item.name || item.template_id }}</strong>
            <small>模板 ID：{{ item.template_id }}</small>
            <p v-if="item.error">{{ item.error }}</p>
            <ul v-if="item.differences?.length">
              <li v-for="difference in item.differences" :key="difference">
                <code>{{ difference }}</code>
              </li>
            </ul>
          </article>
        </div>
      </section>

      <div class="transit-overview-grid">
        <section class="card overview-card">
          <span>当前店铺</span>
          <strong>{{ currentStore?.name || overview.store.name }}</strong>
          <small>{{ overview.store.app_id }}</small>
        </section>
        <section class="card overview-card">
          <span>偏远地区中转服务</span>
          <strong :class="{ ok: serviceOpen }">{{ serviceOpen ? "已开通" : "未开通" }}</strong>
          <small>{{ serviceOpen ? "可设置中转运费与中转商品" : "执行时将先调用官方开通接口" }}</small>
        </section>
        <section class="card overview-card">
          <span>已关联商品的模板</span>
          <strong>{{ linkedTemplates.length }}</strong>
          <small>默认只修改这些模板</small>
        </section>
        <section class="card overview-card">
          <span>已设置中转商品</span>
          <strong>{{ overview.transit_product_count }} / {{ eligibleProducts.length }}</strong>
          <small>分子为已中转，分母为全部可中转商品</small>
        </section>
      </div>

      <section class="card rules-card">
        <div class="section-heading">
          <div>
            <h2>运费参数</h2>
            <p>这些仅作为连续流程参数；新疆会从直邮指定地区和“不配送地区”移除。</p>
          </div>
        </div>

        <div class="rule-grid">
          <div class="rule-group">
            <h3>直邮默认运费</h3>
            <label>首件数量<input v-model.number="form.direct_first_quantity" class="input" type="number" min="1" /></label>
            <label>首件金额（元）<input v-model.number="form.direct_first_price" class="input" type="number" min="0" step="0.01" /></label>
            <label>续件数量<input v-model.number="form.direct_second_quantity" class="input" type="number" min="1" /></label>
            <label>续件金额（元）<input v-model.number="form.direct_second_price" class="input" type="number" min="0" step="0.01" /></label>
          </div>
          <div class="rule-group">
            <h3>西藏、青海直邮</h3>
            <label>首件数量<input v-model.number="form.special_first_quantity" class="input" type="number" min="1" /></label>
            <label>首件金额（元）<input v-model.number="form.special_first_price" class="input" type="number" min="0" step="0.01" /></label>
            <label>续件数量<input v-model.number="form.special_second_quantity" class="input" type="number" min="1" /></label>
            <label>续件金额（元）<input v-model.number="form.special_second_price" class="input" type="number" min="0" step="0.01" /></label>
          </div>
          <div class="rule-group accent">
            <h3>中转运费默认</h3>
            <label>首件数量<input v-model.number="form.transit_first_quantity" class="input" type="number" min="1" /></label>
            <label>首件金额（元）<input v-model.number="form.transit_first_price" class="input" type="number" min="0" step="0.01" /></label>
            <label>续件数量<input v-model.number="form.transit_second_quantity" class="input" type="number" min="1" /></label>
            <label>续件金额（元）<input v-model.number="form.transit_second_price" class="input" type="number" min="0" step="0.01" /></label>
          </div>
        </div>
        <div class="flow-note">
          确认执行后将自动开通/确认服务、切换全部可中转商品、更新所选模板并做最终聚合回读。
        </div>
      </section>

      <section class="card template-card">
        <div class="section-heading">
          <div>
            <h2>运费模板</h2>
            <p>只能选择已关联当前店铺商品的模板；未关联商品的模板始终不动。</p>
          </div>
          <button class="btn primary" type="button" :disabled="applying" @click="requestApply">
            {{ applying ? "连续执行中…" : "执行全店中转连续流程" }}
          </button>
        </div>
        <div class="template-list">
          <article
            v-for="template in templates"
            :key="template.templateId"
            class="template-row"
            :class="{
              selected: selectedTemplateIds.includes(String(template.templateId)),
              disabled: Number(template.relatedProductsNum || 0) <= 0,
            }"
            @click="toggleTemplate(template.templateId)"
          >
            <input
              :checked="selectedTemplateIds.includes(String(template.templateId))"
              type="checkbox"
              :disabled="Number(template.relatedProductsNum || 0) <= 0"
              @click.stop
              @change="toggleTemplate(template.templateId)"
            />
            <div>
              <strong>{{ template.name }}</strong>
              <small>
                模板 ID：{{ template.templateId }} · 关联商品 {{ template.relatedProductsNum || 0 }} 件
                {{ Number(template.relatedProductsNum || 0) <= 0 ? " · 未关联商品，不参与" : "" }}
              </small>
            </div>
            <div class="template-rules">
              <span>直邮：{{ directSummary(template) }}</span>
              <span>中转：{{ transitSummary(template) }}</span>
            </div>
          </article>
        </div>
      </section>

      <section class="card product-card">
        <div class="section-heading">
          <div>
            <h2>商品中转状态</h2>
            <p>商品目录来自微信小店官方完整分页；本次要求全部可中转商品回读为中转。</p>
          </div>
          <span>{{ eligibleProducts.length }} 件可中转 / {{ products.length }} 件总计</span>
        </div>
        <div class="table-scroll">
          <table class="data-table">
            <thead><tr><th>商品</th><th>价格</th><th>运费模板</th><th>运输方式</th><th>可否中转</th></tr></thead>
            <tbody>
              <tr v-for="product in products" :key="product.id">
                <td><strong>{{ product.name }}</strong><small>ID：{{ product.id }}</small></td>
                <td>¥{{ product.price }}</td>
                <td>{{ product.templateId }}</td>
                <td>{{ Number(product.transitType) === 1 ? "中转" : "直邮" }}</td>
                <td>{{ product.canTransit ? "可中转" : (product.transferTip || "不可中转") }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>

    <div v-else-if="!loading" class="card empty-state">请选择一家已登录店铺</div>

    <ModalDialog
      :open="applyDialogOpen"
      title="确认执行全店中转连续流程"
      :subtitle="currentStore?.name || ''"
      width="720px"
      :busy="applying"
      @close="applyDialogOpen = false"
    >
      <p>
        确认后将对 {{ eligibleProducts.length }} 件可中转商品和
        {{ selectedTemplates.length }} 个已关联商品模板连续执行，中途不再要求确认。
      </p>
      <ul>
        <li>先确认或开通偏远地区中转服务，官方 open=true 后才继续</li>
        <li>读取完整官方商品目录，分批切换并逐商品回读 transitType=1</li>
        <li>全部商品通过后才更新已绑定商品的官方运费模板</li>
        <li>直邮默认：{{ form.direct_first_quantity }}件内¥{{ form.direct_first_price }}，续件加¥{{ form.direct_second_price }}</li>
        <li>西藏、青海：{{ form.special_first_quantity }}件内¥{{ form.special_first_price }}，续件加¥{{ form.special_second_price }}</li>
        <li>中转运费：{{ form.transit_first_quantity }}件内¥{{ form.transit_first_price }}，续件加¥{{ form.transit_second_price }}</li>
        <li>新疆从直邮和不配送地区移除</li>
        <li>最后聚合回读服务、全部商品和所有所选模板；任一项失败都不标记成功</li>
      </ul>
      <p class="dialog-risk">
        这是真实平台写操作。这是整条流程唯一一次用户确认；
        执行后以官方字段回读为准，不会伪造成功。
      </p>
      <template #footer>
        <button class="btn" type="button" :disabled="applying" @click="applyDialogOpen = false">取消</button>
        <button class="btn primary" type="button" :disabled="applying" @click="applyChanges">
          {{ applying ? "连续执行中…" : "确认并连续执行" }}
        </button>
      </template>
    </ModalDialog>
  </section>
</template>

<style scoped>
.transit-page { padding-top: 18px; }
.transit-header h1 { margin: 0; }
.transit-header p { margin: 4px 0 0; color: #71808d; font-size: 12px; }
.transit-message { margin-bottom: 10px; padding: 9px 12px; border-radius: 6px; font-size: 12px; }
.transit-message.danger { border: 1px solid #efc8cc; color: #a23d47; background: #fff5f6; }
.transit-message.success { border: 1px solid #bfe0cd; color: #24704a; background: #f2faf5; }
.transit-loading { min-height: 260px; display: grid; place-items: center; color: #70808d; }
.flow-card { margin-bottom: 12px; padding: 14px; }
.flow-verdict { padding: 5px 9px; border-radius: 999px; font-size: 11px; font-weight: 600; }
.flow-verdict.success { color: #176b44; background: #e8f7ef; }
.flow-verdict.failed { color: #9b3440; background: #fff0f2; }
.flow-steps { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; }
.flow-step { min-height: 76px; padding: 10px; display: grid; grid-template-columns: 24px minmax(0, 1fr); align-content: start; gap: 7px; border: 1px solid #dfe6eb; border-radius: 8px; background: #fafcfd; }
.flow-step strong, .flow-step small { display: block; }
.flow-step strong { color: #304b5e; font-size: 12px; }
.flow-step small { margin-top: 5px; color: #7b8b97; font-size: 10px; line-height: 1.5; }
.flow-index { width: 22px; height: 22px; display: grid; place-items: center; border-radius: 50%; color: #607786; background: #e9eff3; font-size: 10px; font-weight: 700; }
.step-status { grid-column: 2; color: #81909b; font-size: 10px; font-weight: 600; }
.flow-step.running { border-color: #80b7df; background: #f3f9fe; }
.flow-step.running .step-status { color: #1c76b7; }
.flow-step.success { border-color: #b9dfca; background: #f3faf6; }
.flow-step.success .flow-index { color: #fff; background: #2a9a64; }
.flow-step.success .step-status { color: #218052; }
.flow-step.failed { border-color: #efc1c7; background: #fff6f7; }
.flow-step.failed .flow-index { color: #fff; background: #c44350; }
.flow-step.failed .step-status { color: #ad3542; }
.flow-step.skipped { opacity: .66; }
.aggregate-grid { margin-top: 10px; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
.aggregate-grid > div { padding: 9px 11px; display: flex; align-items: center; justify-content: space-between; gap: 12px; border: 1px solid #efc6cb; border-radius: 7px; background: #fff7f8; font-size: 10px; }
.aggregate-grid > div.ok { border-color: #c4e2d2; background: #f3faf6; }
.aggregate-grid span { color: #6e7e8a; }
.aggregate-grid strong { color: #b23f4c; }
.aggregate-grid .ok strong { color: #218052; }
.flow-failure { margin-top: 10px; padding: 11px; display: grid; gap: 9px; border: 1px solid #edd3d6; border-radius: 7px; background: #fffafa; font-size: 11px; }
.flow-failure p { margin: 4px 0 0; color: #6f7e89; }
.flow-failure ul { margin: 5px 0 0; padding-left: 18px; color: #6f7e89; }
.flow-failure code { word-break: break-all; }
.failure-block { margin-top: 12px; }
.failure-block h3 { margin: 0 0 8px; color: #97343f; font-size: 12px; }
.failure-block a { color: #1f78b4; }
.template-failure { margin-bottom: 7px; padding: 10px; border: 1px solid #efd1d5; border-radius: 7px; background: #fffafa; font-size: 11px; }
.template-failure small { display: block; margin-top: 3px; color: #7c8b96; }
.template-failure p { color: #9f3541; }
.template-failure ul { margin: 6px 0 0; padding-left: 18px; color: #6d5760; }
.template-failure code { word-break: break-word; }
.transit-overview-grid { margin-bottom: 12px; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.overview-card { padding: 13px; display: grid; gap: 5px; }
.overview-card span, .overview-card small { color: #758591; font-size: 11px; }
.overview-card strong { color: #273f51; font-size: 20px; }
.overview-card strong.ok { color: #1f8b54; }
.rules-card, .template-card, .product-card { margin-bottom: 12px; padding: 14px; }
.section-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
.section-heading h2 { margin: 0; color: #263d4f; font-size: 15px; }
.section-heading p { margin: 4px 0 0; color: #7c8b97; font-size: 11px; }
.rule-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
.rule-group { padding: 11px; display: grid; grid-template-columns: 1fr 1fr; gap: 8px; border: 1px solid #dde5eb; border-radius: 7px; background: #f9fbfc; }
.rule-group h3 { grid-column: 1 / -1; margin: 0 0 2px; color: #395569; font-size: 12px; }
.rule-group.accent { border-color: #b7dbca; background: #f2faf6; }
.rule-group label { display: grid; gap: 4px; color: #72818d; font-size: 10px; }
.flow-note { margin-top: 10px; padding: 8px 10px; border-radius: 6px; color: #3f6653; background: #eef8f2; font-size: 11px; }
.template-list { display: grid; gap: 8px; }
.template-row { padding: 10px; display: grid; grid-template-columns: 20px minmax(220px, 0.8fr) minmax(0, 1.6fr); align-items: center; gap: 9px; border: 1px solid #dfe6eb; border-radius: 7px; cursor: pointer; }
.template-row.selected { border-color: #5aa4dc; background: #f3f9fe; }
.template-row.disabled { opacity: .56; cursor: not-allowed; background: #f5f7f8; }
.template-row strong, .template-row small { display: block; }
.template-row small { margin-top: 4px; color: #7d8c98; font-size: 10px; }
.template-rules { display: grid; gap: 4px; color: #596c7b; font-size: 10px; }
.table-scroll { overflow: auto; }
.data-table td strong, .data-table td small { display: block; }
.data-table td small { margin-top: 3px; color: #8996a1; }
.empty-state { padding: 38px; color: #7f8d98; text-align: center; }
@media (max-width: 1180px) {
  .transit-overview-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .flow-steps, .aggregate-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .rule-grid { grid-template-columns: 1fr; }
  .template-row { grid-template-columns: 20px minmax(0, 1fr); }
  .template-rules { grid-column: 2; }
}
</style>
