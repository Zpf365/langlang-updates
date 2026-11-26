<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { call } from "../api";
import Icon from "../components/Icon.vue";
import ModalDialog from "../components/ModalDialog.vue";

const stores = ref([]);
const loading = ref(false);
const message = ref("");
const configOpen = ref(false);
const scheduleOpen = ref(false);
const executeConfirmOpen = ref(false);
const executeTargets = ref([]);
const editingStore = ref(null);
const bulkMode = ref(false);
const schedule = reactive({ enabled: false, time: "09:30", store_ids: [] });
const RETIRED_ACTION_DEFAULTS = {
  revoke_audit: false,
  delete_audit_failed: false,
  list_products: false,
  prefill_11: false,
  enable_latest_league: false,
  enable_invite: false,
  enable_revoke: false,
  enable_delete_failed: false,
  enable_list: false,
  enable_flash_sale: false,
  enable_price: false,
  enable_no_traffic: false,
  enable_cancel_flash_sale: false,
  enable_cancel_price_drop: false,
  enable_prefill_11: false,
  enable_direct_discount: false,
};
const config = reactive({
  enabled: false,
  enable_league: false,
  enable_commission: true,
  league_commission_percent: 20,
  product_commission_percent: 20,
});

const configuredCount = computed(() => stores.value.filter((store) => {
  return Boolean(store.config?.enable_league);
}).length);

const runnableCount = computed(() => stores.value.filter((store) => (
  store.config?.enabled
  && store.config?.enable_league
  && store.web_status !== "未登录"
)).length);

function leagueEnabled(store) {
  return Boolean(store.config?.enable_league);
}

function existingCommissionEnabled(store) {
  return Boolean(store.config?.enable_commission);
}

function commissionPercent(store) {
  const value = Number(store.config?.league_commission_percent);
  return Number.isFinite(value) ? value : 20;
}

function storeConfigured(store) {
  return leagueEnabled(store) && Boolean(store.config?.enabled);
}

function validateCommission(value) {
  const ratio = Number(value);
  if (!Number.isFinite(ratio) || ratio < 0 || ratio > 90) {
    throw new Error("联盟佣金比例必须在 0–90% 之间");
  }
  return ratio;
}

async function load() {
  loading.value = true;
  message.value = "";
  try {
    const data = await call("hosted.list");
    stores.value = data.stores || [];
    Object.assign(schedule, data.schedule || {});
  } catch (cause) {
    message.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    loading.value = false;
  }
}

function applyConfig(source = {}) {
  const ratio = validateCommission(
    source.league_commission_percent ?? 20,
  );
  const hasLeagueSetting = Object.hasOwn(source, "enable_league");
  config.enable_league = hasLeagueSetting
    ? Boolean(source.enable_league)
    : true;
  config.enabled = Boolean(
    source.enabled || config.enable_league,
  );
  config.enable_commission = source.enable_commission === undefined
    ? config.enable_league
    : Boolean(source.enable_commission);
  config.league_commission_percent = ratio;
  config.product_commission_percent = Number(
    source.product_commission_percent ?? ratio,
  );
}

function openConfig(store) {
  editingStore.value = store;
  bulkMode.value = false;
  applyConfig(store.config || {});
  configOpen.value = true;
}

function openBulkConfig() {
  editingStore.value = stores.value[0] || null;
  bulkMode.value = true;
  applyConfig(stores.value[0]?.config || {});
  configOpen.value = true;
}

async function saveConfig(executeAfter = false) {
  message.value = "";
  const targets = bulkMode.value ? stores.value : [editingStore.value].filter(Boolean);
  const targetIds = new Set(targets.map((store) => store.id));
  let ratio;
  try {
    ratio = validateCommission(config.league_commission_percent);
  } catch (cause) {
    message.value = cause instanceof Error ? cause.message : String(cause);
    return;
  }
  const payload = {
    ...RETIRED_ACTION_DEFAULTS,
    enabled: Boolean(config.enable_league),
    enable_league: Boolean(config.enable_league),
    enable_commission: Boolean(config.enable_league && config.enable_commission),
    league_commission_percent: ratio,
    product_commission_percent: ratio,
  };
  try {
    await Promise.all(targets.map((store) => call("hosted.save-config", {
      store_id: store.id,
      ...payload,
    })));
    configOpen.value = false;
    await load();
    message.value = config.enable_league
      ? `已保存 ${targets.length} 家店铺的全店 ${ratio}% 联盟佣金配置`
      : `已停用 ${targets.length} 家店铺的联盟佣金托管`;
    if (executeAfter && config.enable_league) {
      const selected = stores.value.filter((store) => (
        targetIds.has(store.id) && storeConfigured(store)
      ));
      if (selected.length) {
        executeTargets.value = selected;
        executeConfirmOpen.value = true;
      }
    }
  } catch (cause) {
    message.value = cause instanceof Error ? cause.message : String(cause);
  }
}

function openSchedule() {
  schedule.store_ids = [...(schedule.store_ids || [])];
  scheduleOpen.value = true;
}

async function saveSchedule() {
  try {
    const saved = await call("hosted.save-schedule", {
      enabled: schedule.enabled,
      time: schedule.time,
      store_ids: schedule.store_ids,
    });
    Object.assign(schedule, saved);
    scheduleOpen.value = false;
    message.value = "定时执行配置已保存";
  } catch (cause) {
    message.value = cause instanceof Error ? cause.message : String(cause);
  }
}

function toggleAllSchedule() {
  schedule.store_ids = schedule.store_ids.length === stores.value.length
    ? []
    : stores.value.map((store) => store.id);
}

function requestExecute(store = null) {
  const selected = store
    ? [store]
    : stores.value.filter((item) => storeConfigured(item) && item.web_status !== "未登录");
  if (!selected.length) {
    message.value = "没有已启用且可执行的全店联盟佣金店铺";
    return;
  }
  executeTargets.value = selected;
  executeConfirmOpen.value = true;
}

async function execute() {
  const selected = executeTargets.value;
  if (!selected.length) return;
  executeConfirmOpen.value = false;
  loading.value = true;
  message.value = `正在执行 ${selected.length} 家店铺托管项目…`;
  try {
    const result = await call("hosted.execute", {
      store_ids: selected.map((item) => item.id),
    });
    message.value = result.errors?.length
      ? `全店联盟佣金执行完成：成功 ${result.succeeded}/${result.executed}；${result.errors.slice(0, 2).join("；")}`
      : `全店联盟佣金执行完成：成功 ${result.succeeded} 家`;
    await load();
  } catch (cause) {
    message.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <section class="page hosted-page">
    <header class="page-header hosted-header">
      <div>
        <h1>全店联盟佣金</h1>
        <p>{{ stores.length }} 个店铺 · {{ configuredCount }} 家已设置 · {{ runnableCount }} 家可立即开通</p>
      </div>
      <div class="toolbar">
        <button class="btn" type="button" @click="load">
          <Icon name="refresh" :size="15" /> 刷新
        </button>
        <button class="btn" type="button" @click="openSchedule">定时执行</button>
        <button class="btn" type="button" @click="openBulkConfig">一键设置佣金</button>
        <button class="btn blue" type="button" :disabled="!runnableCount || loading" @click="requestExecute()">
          {{ loading ? "开通中..." : "开通全部店铺佣金" }}
        </button>
      </div>
    </header>

    <div class="card hosted-summary-card">
      <div>
        <span class="hosted-summary-kicker">联盟推广</span>
        <h2>设置一次佣金，全店在售商品自动分批开通</h2>
        <p>例如设置 20%，软件会处理该店铺全部在售商品；微信接口每批最多 20 个，软件自动连续提交直到全店完成。</p>
      </div>
      <div class="hosted-summary-badge">自动同步已有联盟商品佣金</div>
    </div>

    <div class="card hosted-table-card">
      <div class="table-scroll">
        <table class="data-table hosted-table">
          <thead>
            <tr>
              <th>店铺</th>
              <th>全店佣金</th>
              <th>已有联盟商品</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="store in stores" :key="store.id">
              <td class="hosted-store-cell">
                <strong>{{ store.name }}</strong>
                <span>{{ store.web_status || "登录态待验证" }}</span>
              </td>
              <td>
                <span class="hosted-status" :class="{ on: leagueEnabled(store) }">
                  {{ leagueEnabled(store) ? `${commissionPercent(store)}% 已设置` : "未设置" }}
                </span>
              </td>
              <td>
                <span class="hosted-status" :class="{ on: existingCommissionEnabled(store) }">
                  {{ existingCommissionEnabled(store) ? "同步为相同比例" : "仅新增商品" }}
                </span>
              </td>
              <td class="hosted-actions">
                <button class="hosted-config-button" type="button" @click="openConfig(store)">设置</button>
                <button
                  class="hosted-run-button"
                  type="button"
                  :disabled="!storeConfigured(store) || loading || store.web_status === '未登录'"
                  @click="requestExecute(store)"
                >立即开通</button>
              </td>
            </tr>
            <tr v-if="!loading && stores.length === 0">
              <td colspan="4" class="empty-state">暂无店铺</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <ModalDialog
      :open="configOpen"
      :title="bulkMode ? '设置全部店铺佣金' : `${editingStore?.name || '当前店铺'} · 全店联盟佣金`"
      :subtitle="bulkMode ? `保存后应用到全部 ${stores.length} 家店铺` : '统一设置全店商品与已有联盟计划的佣金'"
      width="720px"
      @close="configOpen = false"
    >
      <div class="hosted-config-form">
        <section class="config-section league-config-panel">
          <div class="league-config-head">
            <div>
              <h3>一键开启联盟佣金</h3>
              <p>开启后，执行时会处理该店铺全部在售商品，不需要逐个商品设置。</p>
            </div>
            <label class="switch-line league-enable">
              <input v-model="config.enable_league" type="checkbox" />
              启用
            </label>
          </div>
          <div class="form-grid compact league-ratio-grid">
            <div class="field">
              <label>全店统一联盟佣金</label>
              <div class="suffix-input">
                <input
                  v-model.number="config.league_commission_percent"
                  class="input"
                  type="number"
                  min="0"
                  max="90"
                  step="1"
                  :disabled="!config.enable_league"
                />
                <span>%</span>
              </div>
            </div>
            <label class="switch-line league-sync">
              <input
                v-model="config.enable_commission"
                type="checkbox"
                :disabled="!config.enable_league"
              />
              同时修改已加入联盟商品的佣金
            </label>
          </div>
          <p class="config-help">
            执行范围仅限可在平台参与联盟推广的在售商品。每 20 个商品自动分成一批，
            全部批次完成后才汇总展示成功和失败数量。
          </p>
        </section>
      </div>
      <template #footer>
        <button class="btn" type="button" @click="configOpen = false">取消</button>
        <button class="btn" type="button" @click="saveConfig()">
          只保存
        </button>
        <button class="btn primary" type="button" @click="saveConfig(true)">
          保存并立即开通
        </button>
      </template>
    </ModalDialog>

    <ModalDialog
      :open="scheduleOpen"
      title="定时执行"
      subtitle="按每天设定时间处理所选店铺的全店联盟佣金"
      width="680px"
      @close="scheduleOpen = false"
    >
      <div class="schedule-form">
        <label class="switch-line"><input v-model="schedule.enabled" type="checkbox" /> 开启定时执行</label>
        <div class="schedule-time">
          <span>每天执行时间</span>
          <input v-model="schedule.time" class="input" type="time" />
        </div>
        <p class="config-help">到点后按店铺执行全店在售商品联盟佣金开通，并同步已加入联盟商品的佣金比例。</p>
        <h3>执行店铺</h3>
        <label class="switch-line"><input type="checkbox" :checked="schedule.store_ids.length === stores.length" @change="toggleAllSchedule" /> 全选</label>
        <div class="schedule-stores">
          <label v-for="store in stores" :key="store.id">
            <input v-model="schedule.store_ids" :value="store.id" type="checkbox" />
            <span>{{ store.name }}</span>
            <small>{{ storeConfigured(store) ? "已设置" : "未设置" }}</small>
          </label>
        </div>
        <p class="muted">定时执行当前为{{ schedule.enabled ? '开启' : '关闭' }}状态</p>
      </div>
      <template #footer>
        <button class="btn" type="button" @click="scheduleOpen = false">取消</button>
        <button class="btn blue" type="button" @click="saveSchedule">保存配置</button>
      </template>
    </ModalDialog>

    <ModalDialog
      :open="executeConfirmOpen"
      title="确认开通全店联盟佣金"
      :subtitle="`即将执行 ${executeTargets.length} 家店铺`"
      width="560px"
      :busy="loading"
      @close="executeConfirmOpen = false"
    >
      <p class="config-help">
        软件会对每家店铺的全部在售商品开通联盟推广，并按设置比例同步已有联盟商品。
        这是真实微信小店写入操作，每批最多 20 个商品，执行结果会写入托管日志。是否继续？
      </p>
      <template #footer>
        <button class="btn" type="button" :disabled="loading" @click="executeConfirmOpen = false">取消</button>
        <button class="btn blue" type="button" :disabled="loading" @click="execute">{{ loading ? "执行中…" : "确认执行" }}</button>
      </template>
    </ModalDialog>

    <div v-if="message" class="toast">{{ message }}</div>
  </section>
</template>

<style scoped>
.hosted-header {
  margin-bottom: 16px;
}

.hosted-summary-card {
  margin-bottom: 14px;
  padding: 18px 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  border-color: #a7dfc2;
  background: linear-gradient(120deg, #f5fcf7 0%, #ffffff 62%);
}

.hosted-summary-card h2 {
  margin: 5px 0 7px;
  color: #194f34;
  font-size: 19px;
}

.hosted-summary-card p {
  max-width: 760px;
  margin: 0;
  color: #61766a;
  font-size: 12px;
  line-height: 1.7;
}

.hosted-summary-kicker {
  color: #18a860;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.12em;
}

.hosted-summary-badge {
  flex: none;
  padding: 8px 12px;
  border: 1px solid #b7e4ca;
  border-radius: 999px;
  color: #257044;
  background: #eefaf2;
  font-size: 11px;
}

.hosted-table-card {
  min-height: 320px;
}

.hosted-table {
  min-width: 760px;
}

.hosted-table th {
  background: #f5f7f9;
}

.hosted-table td {
  height: 48px;
}

.hosted-store-cell strong,
.hosted-store-cell span {
  display: block;
}

.hosted-store-cell span {
  margin-top: 3px;
  color: #8b97a2;
  font-size: 10px;
}

.hosted-status {
  display: inline-flex;
  min-width: 66px;
  height: 24px;
  align-items: center;
  justify-content: center;
  border: 1px solid #bed6ee;
  border-radius: 3px;
  color: #58a1e6;
  background: #ecf6ff;
  font-size: 11px;
}

.hosted-status.on {
  border-color: #a7e1c3;
  color: #18a860;
  background: #eefbf4;
}

.hosted-config-button {
  height: 29px;
  padding: 0 13px;
  border: 1px solid #d1dae2;
  border-radius: 3px;
  background: #fff;
}

.hosted-run-button {
  height: 31px;
  padding: 0 17px;
  border: 0;
  border-radius: 3px;
  color: white;
  background: #6ca7cf;
}

.hosted-run-button:disabled {
  color: #a1adb7;
  background: #c8d7e2;
  cursor: not-allowed;
}

.hosted-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.hosted-config-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.config-section {
  padding: 13px 15px 15px;
  border: 1px solid #a7dfc2;
  border-radius: 3px;
}

.config-section h3 {
  margin: 0 0 13px;
  color: #257044;
  font-size: 14px;
}

.league-config-panel {
  padding: 20px;
  background: #fbfffc;
}

.league-config-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
}

.league-config-head h3 {
  margin-bottom: 6px;
  font-size: 17px;
}

.league-config-head p {
  margin: 0;
  color: #687889;
  font-size: 12px;
}

.league-enable {
  flex: none;
  min-width: 82px;
  padding: 8px 11px;
  border: 1px solid #b7e4ca;
  border-radius: 4px;
  color: #257044;
  background: #eefaf2;
  font-weight: 600;
}

.league-ratio-grid {
  margin-top: 18px;
}

.league-sync {
  align-self: end;
  min-height: 36px;
}

.form-grid.compact {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.suffix-input {
  display: flex;
}

.suffix-input .input {
  border-radius: 4px 0 0 4px;
}

.suffix-input span {
  min-width: 38px;
  display: grid;
  place-items: center;
  border: 1px solid #d7dee5;
  border-left: 0;
  border-radius: 0 4px 4px 0;
  color: #778492;
  background: #f8fafb;
}

.config-help {
  margin: 8px 0 0;
  color: #7b8895;
  font-size: 11px;
}

.schedule-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.switch-line {
  display: flex;
  align-items: center;
  gap: 8px;
}

.schedule-time {
  padding: 12px 14px;
  display: flex;
  align-items: center;
  gap: 24px;
  border: 1px solid #e2e7eb;
  background: #fafbfc;
}

.schedule-time .input {
  width: 130px;
}

.schedule-form h3 {
  margin: 4px 0 0;
  font-size: 13px;
}

.schedule-stores {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 22px;
}

.schedule-stores label {
  display: grid;
  grid-template-columns: 18px 1fr auto;
  align-items: center;
  gap: 7px;
}

.schedule-stores small {
  color: #93a0ab;
}

@media (max-width: 820px) {
  .hosted-summary-card {
    align-items: flex-start;
    flex-direction: column;
  }

  .league-config-head {
    flex-direction: column;
  }

  .league-ratio-grid,
  .schedule-stores {
    grid-template-columns: 1fr;
  }
}
</style>
