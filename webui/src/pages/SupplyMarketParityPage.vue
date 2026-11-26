<script setup>
import { onMounted, ref } from "vue";
import { call } from "../api";

const stores = ref([]);
const selectedStore = ref("");
const activeView = ref("search");
const logVisible = ref(true);
const note = ref("");
const views = [
  ["search", "选品搜索"],
  ["hot", "热销货品"],
  ["potential", "潜力货品"],
  ["store", "整店搬运"],
];

onMounted(async () => {
  try {
    const data = await call("stores.list", {});
    stores.value = data.stores || [];
    selectedStore.value = stores.value[0]?.id || "";
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
  }
});
</script>

<template>
  <section class="supply-page">
    <header class="supply-head">
      <h1>货源市场</h1>
      <div class="supply-head-actions">
        <span>查询店铺</span>
        <select v-model="selectedStore" class="select store-select">
          <option value="">全部店铺</option>
          <option v-for="store in stores" :key="store.id" :value="store.id">{{ store.name }}</option>
        </select>
        <button class="btn purple" type="button" disabled title="货源市场数据接口尚未接入">定时执行</button>
        <button class="btn green" type="button" disabled title="货源市场数据接口尚未接入">货源市场上架记录</button>
        <button class="btn blue" type="button" @click="logVisible = !logVisible">{{ logVisible ? "隐藏日志" : "显示日志" }}</button>
      </div>
    </header>

    <div class="supply-layout" :class="{ 'without-log': !logVisible }">
      <main class="supply-main">
        <nav class="supply-tabs">
          <button
            v-for="[key, label] in views"
            :key="key"
            type="button"
            :class="{ active: activeView === key }"
            @click="activeView = key"
          >{{ label }}</button>
        </nav>

        <div class="supply-filter card">
          <div class="filter-row">
            <select class="select category-select" disabled title="货源市场数据接口尚未接入"><option>全部一级类目</option></select>
            <select class="select category-select" disabled title="货源市场数据接口尚未接入"><option>全部二级类目</option></select>
            <input class="input supply-search" disabled placeholder="货源市场数据接口尚未接入" />
            <button class="btn blue search-button" type="button" disabled title="货源市场数据接口尚未接入">搜索</button>
            <button class="btn" type="button" disabled title="货源市场数据接口尚未接入">重置</button>
          </div>
          <div class="filter-row">
            <select class="select wide-select" disabled><option>综合排序</option></select>
            <select class="select wide-select" disabled><option>好评率不限</option></select>
            <select class="select wide-select" disabled><option>品退率不限</option></select>
            <select class="select wide-select" disabled><option>准时发货率不限</option></select>
          </div>
          <div class="filter-row">
            <select class="select wide-select" disabled><option>服务保障不限</option></select>
            <input class="input price" type="number" placeholder="最低价" disabled />
            <span>至</span>
            <input class="input price" type="number" placeholder="最高价" disabled />
            <label class="qualified"><input type="checkbox" disabled /> 仅看有资质类目</label>
          </div>
        </div>

        <div class="login-alert">货源市场数据接口尚未接入。店铺筛选可读取本地店铺，但不会伪造货品、上架记录或日志。</div>

        <div class="supply-table-card">
          <div class="supply-toolbar">
            <span>第 1 / 1 页 已选 0 件</span>
            <div>
              <button class="btn" type="button" disabled title="货源市场数据接口尚未接入">全选本页</button>
              <button class="btn blue" type="button" disabled title="货源市场数据接口尚未接入">加入选品</button>
            </div>
          </div>
          <div class="supply-empty">没有符合条件的货品</div>
          <footer class="supply-pagination">
            <button class="btn" disabled type="button" title="货源市场数据接口尚未接入">上一页</button>
            <span>第</span><input class="input page-input" value="1" /><span>页</span>
            <button class="btn" disabled type="button" title="货源市场数据接口尚未接入">下一页</button>
          </footer>
        </div>
      </main>

      <aside v-if="logVisible" class="supply-log">
        <header>
          <strong>上架日志</strong>
          <div>
            <button class="btn" type="button" disabled title="货源市场上架日志尚未接入">刷新</button>
            <button class="btn" type="button" disabled title="货源市场上架日志尚未接入">清空日志</button>
          </div>
        </header>
        <div class="log-empty">暂无日志</div>
      </aside>
    </div>

    <div v-if="note" class="toast">{{ note }}</div>
  </section>
</template>

<style scoped>
.supply-page {
  height: 100%;
  min-height: 0;
  padding: 18px 12px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: #f2f4f7;
}

.supply-head {
  flex: 0 0 58px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.supply-head h1 {
  margin: 0;
  color: #1c2732;
  font-size: 23px;
  font-weight: 600;
}

.supply-head-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #566575;
  font-size: 13px;
}

.store-select {
  width: 245px;
}

.supply-layout {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: 14px;
}

.supply-layout.without-log {
  grid-template-columns: minmax(0, 1fr);
}

.supply-main {
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.supply-tabs {
  flex: 0 0 50px;
  padding: 0 12px;
  display: flex;
  align-items: center;
  gap: 12px;
  border: 1px solid #dce2e8;
  border-bottom: 0;
  background: #fff;
}

.supply-tabs button {
  height: 31px;
  padding: 0 14px;
  border: 1px solid transparent;
  border-radius: 3px;
  color: #536271;
  background: transparent;
}

.supply-tabs button.active {
  border-color: #9bbbea;
  color: #276fe4;
  background: #f4f8ff;
}

.supply-filter {
  padding: 12px 14px;
  border-radius: 0;
}

.supply-filter .filter-row + .filter-row {
  margin-top: 10px;
}

.category-select {
  width: 210px;
}

.supply-search {
  flex: 1;
}

.search-button {
  width: 72px;
}

.wide-select {
  width: 300px;
}

.price {
  width: 110px;
}

.qualified {
  margin-left: auto;
  color: #526171;
  font-size: 13px;
}

.login-alert {
  min-height: 50px;
  padding: 15px 13px;
  border: 1px solid #f1cfd2;
  color: #dc5f61;
  background: #fff0f1;
  font-size: 13px;
}

.supply-table-card {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid #dce2e8;
  background: #fff;
}

.supply-toolbar {
  min-height: 51px;
  padding: 0 13px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid #d4e0ea;
}

.supply-toolbar > span {
  color: #596876;
  font-size: 12px;
}

.supply-toolbar div {
  display: flex;
  gap: 6px;
}

.supply-empty {
  flex: 1;
  min-height: 240px;
  display: grid;
  place-items: center;
  color: #8c98a4;
}

.supply-pagination {
  min-height: 53px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}

.page-input {
  width: 47px;
  height: 30px;
}

.supply-log {
  min-height: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid #dce2e8;
  background: #fff;
}

.supply-log header {
  min-height: 51px;
  padding: 0 13px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid #dce2e8;
}

.supply-log header div {
  display: flex;
  gap: 6px;
}

.log-empty {
  flex: 1;
  display: grid;
  place-items: center;
  color: #98a3ad;
}

.purple {
  border-color: #b263dc;
  color: #fff;
  background: #b860e1;
}

.green {
  border-color: #0e8d73;
  color: #fff;
  background: #108671;
}
</style>
