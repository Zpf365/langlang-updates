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
const savingRules = ref(false);
const error = ref("");
const note = ref("");
const applyDialogOpen = ref(false);
const selectedTemplateIds = ref([]);

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
const selectedTemplates = computed(() => (
  templates.value.filter((template) => (
    selectedTemplateIds.value.includes(String(template.templateId))
  ))
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

async function loadOverview() {
  if (!selectedStoreId.value) return;
  loading.value = true;
  error.value = "";
  note.value = "";
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
  await loadOverview();
}

async function saveRules() {
  if (savingRules.value) return;
  savingRules.value = true;
  error.value = "";
  try {
    await call("transit.shipping.rules.save", {
      rules: rulesPayload(),
    });
    note.value = "参数已保存到本机；尚未修改平台模板";
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    savingRules.value = false;
  }
}

function toggleTemplate(templateId) {
  const key = String(templateId);
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
    error.value = "请至少选择一个运费模板";
    return;
  }
  applyDialogOpen.value = true;
}

async function applyChanges() {
  if (applying.value) return;
  applying.value = true;
  error.value = "";
  note.value = "";
  try {
    const result = await call("transit.shipping.apply", {
      store_id: Number(selectedStoreId.value),
      template_ids: selectedTemplateIds.value,
      rules: rulesPayload(),
      open_service: true,
      set_all_products_transit: Boolean(
        form.set_all_products_transit,
      ),
    });
    applyDialogOpen.value = false;
    const failed = result.templates?.filter(
      (row) => row.status === "failed",
    ).length || 0;
    const updated = result.templates?.filter(
      (row) => row.status === "updated",
    ).length || 0;
    note.value = (
      `模板成功 ${updated} 个，失败 ${failed} 个；`
      + `商品中转结果 ${result.products?.length || 0} 条`
    );
    if (result.errors?.length) {
      error.value = result.errors.join("；");
    }
    await loadOverview();
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
          <strong>{{ overview.transit_product_count }} / {{ products.length }}</strong>
          <small>执行后重新从官方回读</small>
        </section>
      </div>

      <section class="card rules-card">
        <div class="section-heading">
          <div>
            <h2>运费参数</h2>
            <p>新疆会从直邮指定地区和“不配送地区”移除，只使用中转运费。</p>
          </div>
          <button class="btn" type="button" :disabled="savingRules" @click="saveRules">
            {{ savingRules ? "保存中…" : "仅保存参数" }}
          </button>
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
        <label class="wide-check">
          <input v-model="form.set_all_products_transit" type="checkbox" />
          执行时同时把当前店铺所有可中转商品改为“中转”
        </label>
      </section>

      <section class="card template-card">
        <div class="section-heading">
          <div>
            <h2>运费模板</h2>
            <p>可单独勾选任意模板，包括新建但尚未关联商品的模板。</p>
          </div>
          <button class="btn primary" type="button" @click="requestApply">
            {{ applying ? "执行中…" : "应用模板并设置商品" }}
          </button>
        </div>
        <div class="template-list">
          <article
            v-for="template in templates"
            :key="template.templateId"
            class="template-row"
            :class="{ selected: selectedTemplateIds.includes(String(template.templateId)) }"
            @click="toggleTemplate(template.templateId)"
          >
            <input
              :checked="selectedTemplateIds.includes(String(template.templateId))"
              type="checkbox"
              @click.stop
              @change="toggleTemplate(template.templateId)"
            />
            <div>
              <strong>{{ template.name }}</strong>
              <small>模板 ID：{{ template.templateId }} · 关联商品 {{ template.relatedProductsNum || 0 }} 件</small>
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
            <p>商品列表来自微信小店官方；不可中转的违规或超限商品会跳过。</p>
          </div>
          <span>{{ products.length }} 件商品</span>
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
      title="确认修改官方运费模板"
      :subtitle="currentStore?.name || ''"
      width="720px"
      :busy="applying"
      @close="applyDialogOpen = false"
    >
      <p>即将修改 {{ selectedTemplates.length }} 个运费模板，并可能把 {{ products.length }} 件商品改为中转。</p>
      <ul>
        <li>直邮默认：{{ form.direct_first_quantity }}件内¥{{ form.direct_first_price }}，续件加¥{{ form.direct_second_price }}</li>
        <li>西藏、青海：{{ form.special_first_quantity }}件内¥{{ form.special_first_price }}，续件加¥{{ form.special_second_price }}</li>
        <li>中转运费：{{ form.transit_first_quantity }}件内¥{{ form.transit_first_price }}，续件加¥{{ form.transit_second_price }}</li>
        <li>新疆从直邮和不配送地区移除</li>
      </ul>
      <p class="dialog-risk">这是真实平台写操作。执行后以官方回读为准，不会伪造成功。</p>
      <template #footer>
        <button class="btn" type="button" :disabled="applying" @click="applyDialogOpen = false">取消</button>
        <button class="btn primary" type="button" :disabled="applying" @click="applyChanges">
          {{ applying ? "执行中…" : "确认执行" }}
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
.wide-check { margin-top: 10px; display: flex; align-items: center; gap: 7px; font-size: 11px; }
.template-list { display: grid; gap: 8px; }
.template-row { padding: 10px; display: grid; grid-template-columns: 20px minmax(220px, 0.8fr) minmax(0, 1.6fr); align-items: center; gap: 9px; border: 1px solid #dfe6eb; border-radius: 7px; cursor: pointer; }
.template-row.selected { border-color: #5aa4dc; background: #f3f9fe; }
.template-row strong, .template-row small { display: block; }
.template-row small { margin-top: 4px; color: #7d8c98; font-size: 10px; }
.template-rules { display: grid; gap: 4px; color: #596c7b; font-size: 10px; }
.table-scroll { overflow: auto; }
.data-table td strong, .data-table td small { display: block; }
.data-table td small { margin-top: 3px; color: #8996a1; }
.empty-state { padding: 38px; color: #7f8d98; text-align: center; }
@media (max-width: 1180px) {
  .transit-overview-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .rule-grid { grid-template-columns: 1fr; }
  .template-row { grid-template-columns: 20px minmax(0, 1fr); }
  .template-rules { grid-column: 2; }
}
</style>
