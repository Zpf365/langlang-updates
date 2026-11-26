<script setup>
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { call } from "../api";
import Icon from "../components/Icon.vue";

const router = useRouter();
const loading = ref(false);
const error = ref("");
const ledger = ref(null);
const statusFilter = ref("all");
const search = ref("");

const statusLabels = {
  completed: "已完成",
  partial: "部分完成",
  unverified: "未验证",
  blocked: "阻塞",
  not_implemented: "未实施",
};

const modules = computed(() => {
  const term = search.value.trim().toLowerCase();
  return (ledger.value?.modules || []).filter((item) => {
    if (statusFilter.value !== "all" && item.status !== statusFilter.value) return false;
    if (!term) return true;
    return [item.title, item.summary, item.boundary]
      .some((value) => String(value || "").toLowerCase().includes(term));
  });
});

const statusOptions = computed(() => (
  ["all", ...Object.keys(statusLabels)].map((key) => ({
    key,
    label: key === "all" ? "全部" : statusLabels[key],
    count: key === "all"
      ? (ledger.value?.modules || []).length
      : (ledger.value?.modules || []).filter((item) => item.status === key).length,
  }))
));

async function load() {
  loading.value = true;
  error.value = "";
  try {
    ledger.value = await call("delivery.ledger", {});
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    loading.value = false;
  }
}

function openRoute(path) {
  if (path) router.push(path);
}

function statusClass(status) {
  return `status-${status}`;
}

onMounted(load);
</script>

<template>
  <section class="ledger-page">
    <header class="ledger-head">
      <div>
        <span class="ledger-kicker">真实交付状态</span>
        <h1>未完成与边界账本</h1>
        <p v-if="ledger">当前版本 {{ ledger.version }} · 生成于 {{ ledger.generated_at }} · {{ ledger.account?.username || "当前账户" }}</p>
      </div>
      <button class="btn" type="button" :disabled="loading" @click="load">
        <Icon name="refresh" :size="16" :class="{ spinning: loading }" />
        {{ loading ? "刷新中…" : "刷新状态" }}
      </button>
    </header>

    <div v-if="error" class="ledger-state danger">
      <strong>账本读取失败</strong>
      <span>{{ error }}</span>
      <button class="btn" type="button" @click="load">重试</button>
    </div>
    <div v-else-if="loading && !ledger" class="ledger-state">正在读取真实交付记录…</div>

    <template v-else-if="ledger">
      <div class="ledger-counts">
        <article><span>店铺</span><strong>{{ ledger.counts.stores }}</strong></article>
        <article><span>订单</span><strong>{{ ledger.counts.orders }}</strong></article>
        <article><span>商品</span><strong>{{ ledger.counts.products }}</strong></article>
        <article><span>售后</span><strong>{{ ledger.counts.after_sales }}</strong></article>
        <article><span>上货任务</span><strong>{{ ledger.counts.upload_tasks }}</strong></article>
        <article><span>失效密钥</span><strong>{{ ledger.counts.credential_invalid }}</strong></article>
        <article><span>待验证候选</span><strong>{{ ledger.counts.credential_candidates_pending }}</strong></article>
      </div>

      <div class="ledger-filter">
        <button
          v-for="item in statusOptions"
          :key="item.key"
          type="button"
          class="chip"
          :class="{ active: statusFilter === item.key }"
          @click="statusFilter = item.key"
        >{{ item.label }} {{ item.count }}</button>
        <input v-model="search" class="input" placeholder="搜索模块、说明或边界" />
      </div>

      <section class="ledger-section">
        <div class="ledger-section-head">
          <div><h2>模块交付状态</h2><p>状态由后端 registry、数据库计数和最近审计生成，不写死“全部完成”。</p></div>
        </div>
        <div v-if="modules.length" class="ledger-grid">
          <article v-for="item in modules" :key="item.id" class="ledger-card">
            <div class="ledger-card-head">
              <h3>{{ item.title }}</h3>
              <span class="ledger-status" :class="statusClass(item.status)">{{ statusLabels[item.status] }}</span>
            </div>
            <p>{{ item.summary }}</p>
            <ul v-if="item.evidence?.length">
              <li v-for="evidence in item.evidence" :key="evidence">{{ evidence }}</li>
            </ul>
            <div v-if="item.boundary" class="ledger-boundary">{{ item.boundary }}</div>
            <button v-if="item.route" class="ledger-link" type="button" @click="openRoute(item.route)">
              打开关联页面
              <Icon name="chevron" :size="14" />
            </button>
          </article>
        </div>
        <div v-else class="ledger-state">当前筛选条件下没有模块。</div>
      </section>

      <div class="ledger-bottom-grid">
        <section class="ledger-section">
          <div class="ledger-section-head"><div><h2>真实环境与数据源边界</h2><p>未验证和阻塞项不会计为已完成。</p></div></div>
          <article v-for="item in ledger.boundaries" :key="item.id" class="boundary-row">
            <span class="ledger-status" :class="statusClass(item.status)">{{ statusLabels[item.status] }}</span>
            <div><strong>{{ item.title }}</strong><p>{{ item.detail }}</p></div>
          </article>
        </section>

        <section class="ledger-section">
          <div class="ledger-section-head"><div><h2>最近审计</h2><p>来自 SQLite `task_logs` 的真实记录。</p></div></div>
          <article v-for="row in ledger.recent_audit" :key="row.id" class="audit-row">
            <div><strong>{{ row.operation }}</strong><span>{{ row.message || "无说明" }}</span></div>
            <small>{{ row.started_at }} · {{ row.status }}</small>
          </article>
          <div v-if="!ledger.recent_audit.length" class="ledger-state">暂无审计记录。</div>
        </section>
      </div>
    </template>
  </section>
</template>

<style scoped>
.ledger-page {
  min-height: 100%;
  padding: 24px 28px 40px;
  background: #f4f7f9;
}

.ledger-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
}

.ledger-kicker {
  color: #16835a;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: .12em;
}

.ledger-head h1 {
  margin: 5px 0 6px;
  color: #1a2b39;
  font-size: 26px;
}

.ledger-head p {
  margin: 0;
  color: #778590;
  font-size: 11px;
}

.ledger-counts {
  margin: 18px 0 14px;
  display: grid;
  grid-template-columns: repeat(7, minmax(100px, 1fr));
  gap: 8px;
}

.ledger-counts article {
  padding: 11px 12px;
  border: 1px solid #dfe6eb;
  border-radius: 8px;
  background: #fff;
}

.ledger-counts span,
.ledger-counts strong {
  display: block;
}

.ledger-counts span {
  color: #788691;
  font-size: 10px;
}

.ledger-counts strong {
  margin-top: 3px;
  color: #243745;
  font-size: 20px;
}

.ledger-filter {
  margin-bottom: 13px;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.ledger-filter .input {
  width: 260px;
  margin-left: auto;
}

.ledger-section {
  padding: 15px;
  border: 1px solid #dfe6eb;
  border-radius: 9px;
  background: #fff;
}

.ledger-section-head {
  margin-bottom: 12px;
}

.ledger-section-head h2 {
  margin: 0;
  color: #263846;
  font-size: 15px;
}

.ledger-section-head p {
  margin: 4px 0 0;
  color: #7b8893;
  font-size: 10px;
}

.ledger-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 9px;
}

.ledger-card {
  min-height: 180px;
  padding: 13px;
  display: flex;
  flex-direction: column;
  border: 1px solid #e2e8ed;
  border-radius: 8px;
  background: #fbfcfd;
}

.ledger-card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}

.ledger-card h3 {
  margin: 0;
  color: #2b3d4a;
  font-size: 12px;
}

.ledger-card p {
  margin: 8px 0;
  color: #647481;
  font-size: 10px;
  line-height: 1.6;
}

.ledger-card ul {
  margin: 0;
  padding-left: 16px;
  color: #71808b;
  font-size: 9px;
  line-height: 1.65;
}

.ledger-boundary {
  margin-top: 9px;
  padding: 8px;
  border-radius: 6px;
  color: #88611b;
  background: #fff7e7;
  font-size: 9px;
  line-height: 1.55;
}

.ledger-link {
  margin-top: auto;
  padding: 8px 0 0;
  display: flex;
  align-items: center;
  gap: 4px;
  border: 0;
  color: #2a6fb0;
  background: transparent;
  font-size: 9px;
  text-align: left;
}

.ledger-status {
  display: inline-flex;
  min-height: 21px;
  padding: 2px 7px;
  align-items: center;
  border-radius: 99px;
  font-size: 9px;
  font-weight: 700;
  white-space: nowrap;
}

.status-completed { color: #137b52; background: #e8f8ef; }
.status-partial { color: #93610b; background: #fff3da; }
.status-unverified { color: #2e668f; background: #e9f3fb; }
.status-blocked,
.status-not_implemented { color: #b13d4a; background: #feecef; }

.ledger-bottom-grid {
  margin-top: 13px;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 13px;
}

.boundary-row,
.audit-row {
  padding: 9px 0;
  display: flex;
  align-items: flex-start;
  gap: 9px;
  border-top: 1px solid #edf1f4;
  color: #667582;
}

.boundary-row div,
.audit-row div {
  min-width: 0;
  flex: 1;
}

.boundary-row strong,
.audit-row strong,
.boundary-row p,
.audit-row span,
.audit-row small {
  display: block;
}

.boundary-row strong,
.audit-row strong {
  color: #344652;
  font-size: 10px;
}

.boundary-row p,
.audit-row span {
  margin: 3px 0 0;
  color: #71808b;
  font-size: 9px;
  line-height: 1.55;
}

.audit-row small {
  max-width: 150px;
  color: #929da6;
  font-size: 8px;
  text-align: right;
}

.ledger-state {
  margin: 14px 0;
  padding: 24px;
  color: #7c8994;
  background: #fafcfd;
  text-align: center;
  font-size: 11px;
}

.ledger-state.danger {
  color: #b13c49;
  background: #fff2f3;
}

.ledger-state span,
.ledger-state button {
  margin: 8px auto 0;
  display: block;
}

.spinning {
  animation: spin .8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

@media (max-width: 1100px) {
  .ledger-counts { grid-template-columns: repeat(4, 1fr); }
  .ledger-grid { grid-template-columns: repeat(2, 1fr); }
}
</style>
