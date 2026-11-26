<script setup>
import { computed, onMounted, ref } from "vue";
import { call } from "../api";
import Icon from "../components/Icon.vue";

const activeTab = ref("inbox");
const stores = ref([]);
const storeId = ref("");
const loading = ref(false);
const note = ref("");
const tabs = [
  ["inbox", "待回复"],
  ["processing", "处理中"],
  ["closed", "已结束"],
  ["settings", "客服设置"],
];

const currentStore = computed(() => stores.value.find((store) => String(store.id) === String(storeId.value)) || null);

async function loadStores() {
  const result = await call("stores.list", {});
  stores.value = result.stores || [];
  if (!storeId.value && stores.value[0]) storeId.value = stores.value[0].id;
}

async function openOfficialConsole() {
  if (!storeId.value || loading.value) return;
  loading.value = true;
  note.value = "正在打开微信小店官方客服控制台…";
  try {
    const result = await call("customer-service.open", { store_id: storeId.value });
    note.value = `已提交打开请求：${result.store_name}。请在官方浏览器窗口完成客服操作，关闭窗口后会话状态会回写本地。`;
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  try {
    await loadStores();
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
  }
});
</script>

<template>
  <section class="page">
    <header class="page-header">
      <div><h1>AI客服</h1></div>
      <div class="toolbar">
        <select v-model="storeId" class="select service-store-select" :disabled="loading">
          <option v-for="store in stores" :key="store.id" :value="store.id">{{ store.name }}</option>
        </select>
        <button class="btn primary" type="button" :disabled="loading || !storeId" @click="openOfficialConsole">
          {{ loading ? "正在打开…" : "打开官方客服控制台" }}
        </button>
        <button class="btn muted" type="button" disabled title="旧版与新版的真实数据源只提供官方客服控制台，尚未提供会话列表 API">
          <Icon name="refresh" :size="15" /> 刷新
        </button>
        <button class="btn" type="button" :disabled="loading || !storeId" @click="openOfficialConsole">客服设置</button>
      </div>
    </header>
    <div class="service-boundary">
      {{ currentStore?.name || "请选择店铺" }}：旧版已具备的是“打开官方客服控制台”真实能力；官方尚未提供会话列表、首响和未回复轮次 API，因此下方 AI 会话区保持空态，不执行虚假发送或自动回复。
    </div>
    <div class="service-layout card">
      <aside class="service-list">
        <div class="service-tabs">
          <button
            v-for="[key, label] in tabs"
            :key="key"
            type="button"
            :class="{ active: activeTab === key }"
            @click="activeTab = key"
          >{{ label }}</button>
        </div>
        <div class="empty-state">暂无会话</div>
      </aside>
      <main class="service-chat">
        <div class="empty-state">
          选择左侧会话后查看消息
        </div>
        <div class="service-composer">
          <textarea disabled placeholder="官方未提供会话消息发送 API；请使用上方官方客服控制台"></textarea>
          <button class="btn primary" type="button" disabled title="客服消息接口尚未接入">发送</button>
        </div>
      </main>
    </div>
    <div v-if="note" class="toast">{{ note }}</div>
  </section>
</template>

<style scoped>
.service-layout {
  min-height: 620px;
  display: grid;
  grid-template-columns: 290px minmax(0, 1fr);
}

.service-boundary {
  margin-bottom: 10px;
  padding: 10px 12px;
  border: 1px solid #efd4aa;
  color: #875f18;
  background: #fff8e9;
  font-size: 11px;
}

.service-store-select {
  width: 240px;
}

.service-list {
  border-right: 1px solid var(--line);
}

.service-tabs {
  min-height: 48px;
  display: flex;
  border-bottom: 1px solid var(--line);
}

.service-tabs button {
  flex: 1;
  border: 0;
  color: #667482;
  background: transparent;
  font-size: 12px;
}

.service-tabs button.active {
  color: #12b567;
  box-shadow: inset 0 -2px #20c778;
}

.service-list .empty-state {
  min-height: 530px;
}

.service-chat {
  display: flex;
  flex-direction: column;
}

.service-chat .empty-state {
  flex: 1;
}

.service-composer {
  min-height: 130px;
  padding: 12px;
  display: flex;
  align-items: flex-end;
  gap: 10px;
  border-top: 1px solid var(--line);
}

.service-composer textarea {
  flex: 1;
  height: 94px;
  padding: 10px;
  resize: none;
  border: 1px solid #d7dee5;
  border-radius: 4px;
}
</style>
